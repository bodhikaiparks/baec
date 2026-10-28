"""Engine 1 adapter: the console's structured capture form, turned into the real Engine 1 objects.

Nothing here classifies a BAEC or decides an account state. The adapter builds the locked domain objects
(SourceInteraction, BaecCandidate, EvidenceExcerpt, StringencyExpression, ...) from what the salesperson typed,
and every authoritative write goes through the Phase 4 HumanCommandFacade: request, then the gate's approval
in response to the salesperson's click, then the approval-only command. The locked classifier and state
machine decide; the console only shows what they decided.

Form rules that are stricter than the domain (implementation choices of this console, not research rules):

* every evidence excerpt must be copied exactly from the interaction. Text inside the buyer's exact statement
  is recorded as BUYER_FACT; text inside the seller notes is recorded as SELLER_OBSERVATION;
* threshold wording and timing wording must appear verbatim in the buyer's exact statement (RC-18).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation

from baec_app.application import HumanCommandFacade, InteractionSession, RequestNotCoherent, build_command_facade
from baec_app.application.account_state import require_coherent_preview
from baec_app.application.canonical import canonical_json
from baec_app.application.context import Clock, UuidIdFactory
from baec_app.data.records import SourceInteraction
from baec_app.data.repository import Repository
from baec_app.domain.enums import (
    ArticulationOrigin,
    BaecCriterion,
    CriterionFinding,
    ElicitationMode,
    NoPlausiblePathGround,
    ProvenanceCategory,
    ReviewAnswer,
    ThresholdComparator,
    TransitionRejectionKind,
)
from baec_app.domain.models import (
    BaecCandidate,
    CriterionAssessment,
    EvaluationEvidence,
    EvidenceExcerpt,
    NonEvaluationEvidence,
    StringencyExpression,
)


class CaptureError(ValueError):
    """The capture form cannot be turned into Engine 1 objects. Nothing was written."""


CRITERION_QUESTIONS = {
    BaecCriterion.PRESENT_NON_EVALUATION: "Is the buyer presently outside an active evaluation of alternatives?",
    BaecCriterion.PROSPECTIVE_CONDITION: "Is the condition prospective (it has not happened yet)?",
    BaecCriterion.BUYER_ARTICULATION: "Did the buyer articulate the condition, rather than the seller supplying it?",
    BaecCriterion.EVALUATION_LINKAGE: "Did the buyer link the condition to opening or reopening evaluation?",
}
CRITERION_LABELS = {
    BaecCriterion.PRESENT_NON_EVALUATION: "Present non-evaluation",
    BaecCriterion.PROSPECTIVE_CONDITION: "Prospective condition",
    BaecCriterion.BUYER_ARTICULATION: "Buyer articulation",
    BaecCriterion.EVALUATION_LINKAGE: "Evaluation linkage",
}
FINDING_LABELS = {CriterionFinding.MET: "Met", CriterionFinding.NOT_MET: "Not met", CriterionFinding.UNKNOWN: "Unknown"}
ORIGIN_LABELS = {
    ArticulationOrigin.BUYER_GENERATED: "The buyer supplied the condition",
    ArticulationOrigin.SELLER_SEEDED: "The seller suggested it; the buyer agreed or added detail",
    ArticulationOrigin.UNCERTAIN: "Not clear who supplied it",
}
ELICITATION_LABELS = {
    ElicitationMode.SPONTANEOUS: "Volunteered by the buyer",
    ElicitationMode.CEE_ELICITED: "In answer to an open question about what would change",
    ElicitationMode.UNKNOWN: "Not recorded",
}
COMPARATOR_LABELS = {
    ThresholdComparator.GREATER_THAN: "More than",
    ThresholdComparator.AT_LEAST: "At least",
    ThresholdComparator.LESS_THAN: "Less than",
    ThresholdComparator.AT_MOST: "At most",
    ThresholdComparator.EXACTLY: "Exactly",
    ThresholdComparator.APPROXIMATELY: "Approximately",
    ThresholdComparator.QUALITATIVE_ONLY: "A word, no number (e.g. 'significant')",
    ThresholdComparator.NONE_STATED: "No threshold stated",
}
REVIEW_ANSWER_LABELS = {ReviewAnswer.YES: "Yes", ReviewAnswer.NO: "No", ReviewAnswer.UNKNOWN: "Unknown",
                        ReviewAnswer.NOT_YET: "Not yet considered"}
GROUND_LABELS = {
    NoPlausiblePathGround.NO_PLAUSIBLE_BAEC: "No plausible buyer condition",
    NoPlausiblePathGround.CONDITION_TOO_IMPLAUSIBLE: "Condition too implausible",
    NoPlausiblePathGround.CLEARLY_SELLER_UNADDRESSABLE: "Clearly not something we can address",
    NoPlausiblePathGround.OTHER: "Other",
}

_K = TransitionRejectionKind
REJECTION_LABELS = {
    _K.SAME_STATE: "The account is already in that state.",
    _K.NON_EVALUATION_EVIDENCE_MISSING: "Leaving Active Opportunity needs evidence that the buyer stopped evaluating.",
    _K.NON_EVALUATION_EVIDENCE_WRONG_ACCOUNT: "The non-evaluation evidence is from another account.",
    _K.BAEC_MISSING: "Choose a confirmed buyer condition.",
    _K.BAEC_NOT_CONFIRMED: "The buyer condition is not confirmed.",
    _K.BAEC_WRONG_ACCOUNT: "The buyer condition belongs to another account.",
    _K.BAEC_NOT_CURRENT: "The buyer condition is not current; reassess it first.",
    _K.JUDGMENT_MISSING: "Record a dormancy judgment first.",
    _K.JUDGMENT_WRONG_BAEC: "The judgment is for a different condition.",
    _K.PLAUSIBILITY_NOT_YES: "Plausibility must be judged Yes.",
    _K.ADDRESSABILITY_NO: "Addressability was judged No.",
    _K.ADDRESSABILITY_NOT_YET: "Addressability has not been considered yet.",
    _K.EVALUATION_EVIDENCE_MISSING: "Active Opportunity needs evidence from an interaction that the buyer is evaluating.",
    _K.EVALUATION_EVIDENCE_WRONG_ACCOUNT: "The evaluation evidence is from another account.",
    _K.GROUND_MISSING: "Choose a ground.",
    _K.REASON_MISSING: "Write a reason.",
}


def preview_blockers(preview) -> list[str]:
    """Plain-language reasons the locked state machine would refuse, beyond the authorization a preview never has.

    Uses the application layer's own registration rule (require_coherent_preview) to decide.
    """
    try:
        require_coherent_preview(preview)
        return []
    except RequestNotCoherent:
        return [REJECTION_LABELS.get(k, k.value) for k in preview.rejections if k is not _K.AUTHORIZATION_MISSING]


def compose_interaction_text(buyer_role: str | None, buyer_statement: str, seller_notes: str | None) -> str:
    """The Engine 1 interaction text the console records. The buyer's statement appears in it verbatim."""
    speaker = buyer_role.strip() if buyer_role and buyer_role.strip() else "Buyer"
    text = f'{speaker} said: "{buyer_statement}"'
    if seller_notes and seller_notes.strip():
        text += f"\n\nSeller notes: {seller_notes.strip()}"
    return text


def excerpt_provenance(excerpt: str, buyer_statement: str, interaction_text: str) -> ProvenanceCategory:
    """BUYER_FACT inside the buyer's exact words; SELLER_OBSERVATION elsewhere in the interaction."""
    if not excerpt or not excerpt.strip():
        raise CaptureError("an evidence excerpt cannot be empty")
    if excerpt in buyer_statement:
        return ProvenanceCategory.BUYER_FACT
    if excerpt in interaction_text:
        return ProvenanceCategory.SELLER_OBSERVATION
    raise CaptureError(f"evidence must be copied exactly from the interaction; not found: {excerpt!r}")


@dataclass(frozen=True)
class CriterionInput:
    finding: str  # CriterionFinding value
    excerpt: str = ""
    rationale: str = ""


@dataclass(frozen=True)
class ConditionForm:
    """What the salesperson entered. Plain strings, so the draft can be stored exactly as typed."""

    criteria: dict  # BaecCriterion value -> CriterionInput fields (dict)
    origin: str  # ArticulationOrigin value
    elicitation: str  # ElicitationMode value
    threshold_text: str = ""
    comparator: str = ""  # ThresholdComparator value, or "" when the wording cannot be normalized
    threshold_value: str = ""
    threshold_unit: str = ""
    qualitative_term: str = ""
    recurrence_text: str = ""
    timing_text: str = ""

    def to_dict(self) -> dict:
        return {"criteria": self.criteria, "origin": self.origin, "elicitation": self.elicitation,
                "threshold_text": self.threshold_text, "comparator": self.comparator,
                "threshold_value": self.threshold_value, "threshold_unit": self.threshold_unit,
                "qualitative_term": self.qualitative_term, "recurrence_text": self.recurrence_text,
                "timing_text": self.timing_text}

    @classmethod
    def from_dict(cls, data: dict) -> ConditionForm:
        return cls(**data)


def _opt(text: str) -> str | None:
    return text.strip() if text and text.strip() else None


def build_stringency(form: ConditionForm, buyer_statement: str) -> StringencyExpression | None:
    """The buyer's threshold exactly as stated (RC-18), or None when nothing about it was said."""
    fields = (form.threshold_text, form.comparator, form.threshold_value, form.threshold_unit, form.qualitative_term,
              form.recurrence_text, form.timing_text)
    if not any(f and f.strip() for f in fields):
        return None
    for label, text in (("threshold wording", form.threshold_text), ("timing wording", form.timing_text),
                        ("recurrence wording", form.recurrence_text), ("qualitative term", form.qualitative_term)):
        if _opt(text) is not None and text.strip() not in buyer_statement:
            raise CaptureError(f"the {label} must be copied exactly from the buyer's statement")
    verbatim = _opt(form.threshold_text) or _opt(form.timing_text) or _opt(form.recurrence_text)
    if verbatim is None:
        raise CaptureError("record the buyer's exact threshold or timing wording")
    comparator = ThresholdComparator(form.comparator) if form.comparator else None
    number = None
    if _opt(form.threshold_value) is not None:
        try:
            number = Decimal(form.threshold_value.strip())
        except InvalidOperation as error:
            raise CaptureError(f"{form.threshold_value!r} is not a number") from error
    return StringencyExpression(
        verbatim_text=verbatim.strip(), comparator=comparator, numeric_value=number,
        unit=_opt(form.threshold_unit), qualitative_term=_opt(form.qualitative_term),
        recurrence_text=_opt(form.recurrence_text), timing_text=_opt(form.timing_text),
    )


def build_candidate(account_id: str, interaction: SourceInteraction, buyer_statement: str, buyer_role: str | None,
                    form: ConditionForm) -> BaecCandidate:
    """The Engine 1 BaecCandidate for a draft. The domain constructors validate it; errors propagate."""
    if buyer_statement not in interaction.text:
        raise CaptureError("the buyer's exact statement is not in the recorded interaction")
    assessments = []
    for criterion in BaecCriterion:
        entry = CriterionInput(**form.criteria[criterion.value])
        finding = CriterionFinding(entry.finding)
        evidence: tuple[EvidenceExcerpt, ...] = ()
        if _opt(entry.excerpt) is not None:
            provenance = excerpt_provenance(entry.excerpt, buyer_statement, interaction.text)
            evidence = (EvidenceExcerpt(entry.excerpt, provenance, interaction.interaction_id),)
        elif finding is not CriterionFinding.UNKNOWN:
            raise CaptureError(f"{CRITERION_LABELS[criterion]}: a finding of {FINDING_LABELS[finding]} needs an "
                               "evidence excerpt copied from the interaction")
        assessments.append(CriterionAssessment(criterion, finding, evidence, _opt(entry.rationale)))
    return BaecCandidate(
        account_id=account_id,
        source_interaction_id=interaction.interaction_id,
        source_excerpt=EvidenceExcerpt(buyer_statement, ProvenanceCategory.BUYER_FACT, interaction.interaction_id),
        assessments=tuple(assessments),
        articulation_origin=ArticulationOrigin(form.origin),
        elicitation_mode=ElicitationMode(form.elicitation),
        buyer_exact_statement=buyer_statement,
        buyer_role=_opt(buyer_role or ""),
        stringency=build_stringency(form, buyer_statement),
    )


def fingerprint(candidate: BaecCandidate) -> str:
    """Digest of exactly what the salesperson was shown in the preview. Binds the confirm click to it."""
    return hashlib.sha256(canonical_json(candidate).encode("utf-8")).hexdigest()


def build_evidence(kind: type, account_id: str, interaction: SourceInteraction, buyer_statement: str, excerpt: str,
                   observed_at: datetime):
    """EvaluationEvidence or NonEvaluationEvidence from an exact excerpt of one interaction."""
    provenance = excerpt_provenance(excerpt, buyer_statement, interaction.text)
    if kind not in (EvaluationEvidence, NonEvaluationEvidence):
        raise CaptureError("unsupported evidence kind")
    return kind(account_id, EvidenceExcerpt(excerpt, provenance, interaction.interaction_id), observed_at)


class Engine1Session:
    """The command facade over one writable Engine 1 connection, per console operation. The connection belongs to the
    caller (the storage backend), which opens and closes it."""

    def __init__(self, connection, clock: Clock) -> None:
        self.connection = connection
        self.repository = Repository(connection)
        self.facade: HumanCommandFacade = build_command_facade(self.repository, clock=clock, ids=UuidIdFactory())

    def human_session(self, actor: str) -> InteractionSession:
        return self.facade.gate.open_session(actor)

    def approve_and_run(self, session: InteractionSession, request, command):
        """The trusted-adapter step: the salesperson clicked, so the gate issues the one approval and it is redeemed.

        The digest approved is the digest of the request the console just built from the content it displayed.
        """
        approval = self.facade.gate.approve(session, request.request_id, displayed_digest=request.digest)
        try:
            return command(approval)
        finally:
            self.facade.gate.close_session(session)
