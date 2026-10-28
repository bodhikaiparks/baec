"""Human review of one persisted AI_DRAFT proposal (Phase 7E).

docs/PHASE7_HUMAN_AUTHORIZED_AI_PROPOSAL_BRIDGE_DESIGN.md §7-§10. This module holds every authoritative
review rule and the persistence orchestration; the Streamlit page only renders ProposalReview and submits
ReviewDecisions. It never imports Streamlit.

What a human review is, and is not.
* Review Accepted ≠ BAEC Confirmed. ACCEPTED means only that a human finalized this immutable review revision. It does not confirm a BAEC,
  establish purchase intent, activate an account, issue an authorization grant, or authorize any execution.
* REJECTED is terminal review history for the proposal. It creates no BAEC, no grant, no state change, and
  contacts nobody.
* Nothing here issues or consumes grants, writes a BAEC or a HumanAuthorization, or changes account state.

Authority. Every authoritative value is an explicit human input: the verbatim evidence selections and their
provenance, the source selection, the buyer's exact statement, the four criterion findings, articulation
origin, elicitation mode, the stringency decision, and the final normalizations. AI material (suggested
excerpts, attributed speaker, normalizations, hypotheses, uncertainties) is AI_INFERENCE, shown beside the
controls and never copied into them. buyer_role is always None in Phase 7.

Source binding. Evidence may be any exact, contiguous, non-blank substring of the proposal's stored source
interaction; AI excerpts are suggestions only. Every selection is re-checked against the stored interaction
here, whatever the client claims, before the human-normalization contract runs over the selected texts only.

Identity. The actor label is self-asserted in this prototype; there is no authentication.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from decimal import Decimal

from baec_app.application.ai_proposal_mapping import (
    ArtifactNotEligible,
    canonical_text,
    map_artifact_to_proposal_content,
    verify_artifact,
)
from baec_app.application.context import Clock
from baec_app.application.errors import ApplicationError
from baec_app.application.human_normalization import (
    HUMAN_NORMALIZATION_VALIDATION_VERSION,
    validate_final_normalization,
)
from baec_app.data.ai_provenance import AiProvenanceStore
from baec_app.data.database import (
    BRIDGE_REVIEW_CONTENT_VERSION,
    PersistenceError,
    PersistenceIntegrityError,
    RepositoryConflictError,
    RepositoryNotFoundError,
    connect,
    require_current_schema,
)
from baec_app.data.proposal_bridge import (
    AiProposalRecord,
    ProposalBridgeStore,
    ReviewDecision,
    ReviewDecisionRecord,
    ReviewRevisionRecord,
    sha256_text,
)
from baec_app.data.repository import Repository, encode_datetime
from baec_app.domain.baec_rules import classify_candidate
from baec_app.domain.enums import (
    ArticulationOrigin,
    BaecClassification,
    BaecCriterion,
    CriterionFinding,
    ElicitationMode,
    ProvenanceCategory,
    ThresholdComparator,
)
from baec_app.domain.models import (
    BaecCandidate,
    CriterionAssessment,
    DomainValidationError,
    EvidenceExcerpt,
    StringencyExpression,
)

AI_INFERENCE = "AI_INFERENCE"
# The authoritative provenance a human may assert for evidence drawn from an interaction: the locked
# interaction-evidence vocabulary (RC-32). AI_INFERENCE and UNKNOWN can never be evidence.
EVIDENCE_PROVENANCE = (ProvenanceCategory.BUYER_FACT, ProvenanceCategory.SELLER_OBSERVATION)
CRITERIA = tuple(BaecCriterion)  # C1-C4, in the domain's order
REVIEW_STATES = ("OPEN", "REVIEW_ACCEPTED", "REVIEW_REJECTED")
NORMALIZATION_DISPOSITIONS = ("KEEP_AI", "EDITED", "NONE")

REVIEW_FAILURE_CODES = (
    "actor_blank",
    "proposal_not_found",
    "proposal_integrity_failure",
    "proposal_closed",
    "selection_none",
    "selection_blank",
    "selection_duplicate",
    "selection_not_verbatim",
    "selection_suggestion_mismatch",
    "provenance_invalid",
    "source_selection_unknown",
    "buyer_statement_not_selected",
    "buyer_statement_requires_buyer_fact",
    "criteria_incomplete",
    "finding_evidence_unknown",
    "stringency_unset",
    "stringency_not_verbatim",
    "normalization_invalid",
    "candidate_invalid",
    "conflict",
)


class ReviewNotSaved(ApplicationError):
    """A review submission was refused and nothing was written. Carries closed codes and no source text."""

    def __init__(self, code: str, normalization_codes: tuple[str, ...] = ()) -> None:
        if code not in REVIEW_FAILURE_CODES:
            raise ValueError("unknown review failure code")
        super().__init__(code if not normalization_codes else f"{code}: {', '.join(normalization_codes)}")
        self.code = code
        self.normalization_codes = normalization_codes


# --- the read model ------------------------------------------------------------------------------


@dataclass(frozen=True)
class AiSuggestedExcerpt:
    excerpt_id: str
    text: str
    ai_attributed_speaker: str  # buyer | seller | unclear: an AI inference, never evidence provenance
    status: str = AI_INFERENCE


@dataclass(frozen=True)
class AiCriterionHypothesis:
    criterion: str  # a BaecCriterion name, for display alignment only
    ai_status: str  # supported | not_supported | unclear: never a criterion finding
    excerpt_refs: tuple[str, ...]
    explanation: str
    status: str = AI_INFERENCE


@dataclass(frozen=True)
class ProposalSummary:
    proposal_id: str
    account_id: str
    interaction_id: str
    review_state: str


@dataclass(frozen=True)
class ProposalReview:
    """Everything a reviewer sees for one AI_DRAFT. AI fields carry AI_INFERENCE; nothing here is a decision."""

    proposal_id: str
    proposal_digest: str
    mapping_version: str
    account_id: str
    interaction_id: str
    interaction_text: str  # SOURCE: untrusted text, to be rendered inert
    interaction_occurred_at: str
    # Provenance, reconstructed through proposal -> artifact_id -> the Phase 6 run (never copied in the snapshot).
    artifact_id: str
    artifact_digest: str
    ai_run_id: str
    provider: str
    requested_model: str
    response_model: str
    prompt_version: str
    prompt_digest: str
    validation_version: str
    request_digest: str
    # AI suggestions (AI_INFERENCE).
    ai_suggested_excerpts: tuple[AiSuggestedExcerpt, ...]
    ai_normalized_condition: str | None
    ai_normalized_evaluation_link: str | None
    ai_criterion_hypotheses: tuple[AiCriterionHypothesis, ...]
    ai_uncertainties: tuple[str, ...]
    # Human review history.
    review_state: str
    revisions: tuple[ReviewRevisionRecord, ...]
    decisions: tuple[ReviewDecisionRecord, ...]


# --- the human decisions --------------------------------------------------------------------------


@dataclass(frozen=True)
class EvidenceSelection:
    """One exact verbatim span of the source interaction, with the provenance a human asserted for it."""

    selection_id: str
    text: str
    provenance: ProvenanceCategory
    suggested_excerpt_id: str | None  # set only when the human took an AI suggestion unchanged


@dataclass(frozen=True)
class CriterionDecision:
    criterion: BaecCriterion
    finding: CriterionFinding
    evidence_selection_ids: tuple[str, ...]


class _NoStringencyStated:
    """The explicit human decision that the buyer stated no stringency. Distinct from an unset choice."""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "NO_STRINGENCY_STATED"


NO_STRINGENCY_STATED = _NoStringencyStated()


@dataclass(frozen=True)
class ReviewDecisions:
    """Every authoritative review value, each an explicit human choice. No field has a default."""

    evidence_selections: tuple[EvidenceSelection, ...]
    source_selection_id: str
    buyer_exact_statement: str | None
    findings: tuple[CriterionDecision, ...]
    articulation_origin: ArticulationOrigin
    elicitation_mode: ElicitationMode
    stringency: StringencyExpression | _NoStringencyStated
    final_normalized_condition: str | None
    final_normalized_evaluation_link: str | None


@dataclass(frozen=True)
class AcceptedReview:
    """The immutable revision and its ACCEPTED decision.

    Review Accepted ≠ BAEC Confirmed. classification is the locked classifier's display preview only: it is not
    an authorization and is not persisted as a confirmation.
    """

    revision: ReviewRevisionRecord
    decision: ReviewDecisionRecord
    classification: BaecClassification


# --- composition ----------------------------------------------------------------------------------------


def open_review_connection(database_path: str) -> sqlite3.Connection:
    """A writable connection to an existing current-schema database file. Never creates one."""
    from pathlib import Path
    if type(database_path) is not str or not database_path.strip() or database_path == ":memory:" \
            or database_path.strip().lower().startswith("file:") or not Path(database_path).is_file():
        raise PersistenceError("a path to an existing database file is required")
    connection = connect(database_path)
    try:
        require_current_schema(connection)
    except BaseException:
        connection.close()
        raise
    return connection


def _new_id(prefix: str) -> str:
    return prefix + uuid.uuid4().hex


class ProposalReviewService:
    """Loads AI_DRAFT proposals for review and persists human review decisions. No grant, BAEC, or state path."""

    def __init__(self, connection: sqlite3.Connection, *, clock: Clock, new_id=_new_id) -> None:
        self._bridge = ProposalBridgeStore(connection)  # each store verifies the schema and foreign keys
        self._provenance = AiProvenanceStore(connection)
        self._repository = Repository(connection)
        self._clock = clock
        self._new_id = new_id

    # --- reads ----------------------------------------------------------------------------------

    def list_reviewable(self) -> tuple[ProposalSummary, ...]:
        return tuple(ProposalSummary(p.proposal_id, p.account_id, p.interaction_id, self._state(p.proposal_id))
                     for p in self._bridge.list_proposals())

    def load_review(self, proposal_id: str) -> ProposalReview:
        proposal = self._verified_proposal(proposal_id)
        content = json.loads(proposal.content)
        artifact = self._provenance.get_artifact(proposal.artifact_id)
        run, result = self._provenance.get_run(artifact.ai_run_id), self._provenance.get_result(artifact.ai_run_id)
        interaction = self._repository.get_interaction(proposal.interaction_id)
        return ProposalReview(
            proposal_id=proposal.proposal_id, proposal_digest=proposal.proposal_digest,
            mapping_version=proposal.mapping_version, account_id=proposal.account_id,
            interaction_id=proposal.interaction_id, interaction_text=interaction.text,
            interaction_occurred_at=encode_datetime(interaction.occurred_at),
            artifact_id=artifact.artifact_id, artifact_digest=artifact.artifact_digest, ai_run_id=run.ai_run_id,
            provider=run.provider, requested_model=run.requested_model, response_model=result.response_model,
            prompt_version=run.prompt_version, prompt_digest=run.prompt_digest,
            validation_version=run.validation_version, request_digest=run.request_digest,
            ai_suggested_excerpts=tuple(
                AiSuggestedExcerpt(e["excerpt_id"], e["text"], e["ai_attributed_speaker"]["value"])
                for e in content["ai_suggested_excerpts"]),
            ai_normalized_condition=content["ai_normalized_condition"]["value"],
            ai_normalized_evaluation_link=content["ai_normalized_evaluation_link"]["value"],
            ai_criterion_hypotheses=tuple(
                AiCriterionHypothesis(h["criterion"], h["ai_status"], tuple(h["excerpt_refs"]), h["explanation"])
                for h in content["ai_criterion_hypotheses"]),
            ai_uncertainties=tuple(content["ai_uncertainties"]["values"]),
            review_state=self._state(proposal_id),
            revisions=self._bridge.list_revisions(proposal_id),
            decisions=self._bridge.list_decisions(proposal_id),
        )

    # --- writes ---------------------------------------------------------------------------------

    def accept_review(self, proposal_id: str, decisions: ReviewDecisions, *, actor_label: str) -> AcceptedReview:
        """Validate every human decision, then persist the next immutable revision and its ACCEPTED decision."""
        _require_actor(actor_label)
        if type(decisions) is not ReviewDecisions:
            raise TypeError("accept_review requires ReviewDecisions")
        proposal = self._verified_proposal(proposal_id)  # the proposal, and through it the artifact provenance
        if self._state(proposal_id) == "REVIEW_REJECTED":
            raise ReviewNotSaved("proposal_closed")
        interaction = self._repository.get_interaction(proposal.interaction_id)
        selections = _verified_selections(decisions, interaction.text, json.loads(proposal.content))
        candidate = _candidate(decisions, selections, proposal)
        codes = validate_final_normalization(
            normalized_condition=decisions.final_normalized_condition,
            normalized_evaluation_link=decisions.final_normalized_evaluation_link,
            evidence_texts=tuple(s.text for s in selections),  # the selected, source-bound texts only
        )
        if codes:
            raise ReviewNotSaved("normalization_invalid", codes)

        previous = self._bridge.list_revisions(proposal_id)
        number = len(previous) + 1
        previous_id = previous[-1].review_revision_id if previous else None
        content = canonical_text(_review_content(proposal, number, previous_id, decisions,
                                                 encode_datetime(interaction.occurred_at)))
        now = self._clock.now()
        revision = ReviewRevisionRecord(
            review_revision_id=self._new_id("aireview_"), proposal_id=proposal.proposal_id,
            proposal_digest=proposal.proposal_digest, account_id=proposal.account_id,
            interaction_id=proposal.interaction_id, artifact_id=proposal.artifact_id, revision_number=number,
            previous_revision_id=previous_id, content=content, review_content_digest=sha256_text(content),
            actor_label=actor_label, created_at=now,
        )
        decision = ReviewDecisionRecord(
            decision_id=self._new_id("aidecision_"), proposal_id=proposal.proposal_id,
            review_revision_id=revision.review_revision_id, account_id=proposal.account_id,
            decision=ReviewDecision.ACCEPTED, actor_label=actor_label, decided_at=now,
        )
        try:
            self._bridge.add_accepted_revision(revision, decision)  # one transaction: both, or neither
        except RepositoryConflictError:
            raise ReviewNotSaved("conflict") from None
        return AcceptedReview(revision, decision, classify_candidate(candidate).classification)

    def reject_proposal(self, proposal_id: str, *, actor_label: str) -> ReviewDecisionRecord:
        """Persist the terminal REJECTED decision for the proposal. Authorizes and changes nothing else."""
        _require_actor(actor_label)
        proposal = self._verified_proposal(proposal_id)
        if self._state(proposal_id) == "REVIEW_REJECTED":
            raise ReviewNotSaved("proposal_closed")
        decision = ReviewDecisionRecord(
            decision_id=self._new_id("aidecision_"), proposal_id=proposal.proposal_id, review_revision_id=None,
            account_id=proposal.account_id, decision=ReviewDecision.REJECTED, actor_label=actor_label,
            decided_at=self._clock.now(),
        )
        try:
            self._bridge.add_decision(decision)  # a single insert: atomic by itself
        except RepositoryConflictError:
            raise ReviewNotSaved("conflict") from None
        return decision

    # --- helpers --------------------------------------------------------------------------------

    def _verified_proposal(self, proposal_id: str) -> AiProposalRecord:
        """The stored proposal, re-verified, and its snapshot re-derived from the re-verified artifact."""
        try:
            proposal = self._bridge.get_proposal(proposal_id)
        except RepositoryNotFoundError:
            raise ReviewNotSaved("proposal_not_found") from None
        except PersistenceIntegrityError:
            raise ReviewNotSaved("proposal_integrity_failure") from None
        try:
            verified = verify_artifact(proposal.artifact_id, provenance=self._provenance, repository=self._repository)
        except ArtifactNotEligible:
            raise ReviewNotSaved("proposal_integrity_failure") from None
        if map_artifact_to_proposal_content(verified) != proposal.content:
            raise ReviewNotSaved("proposal_integrity_failure")
        if self._bridge.confirmed_baec_for_artifact(proposal.artifact_id) is not None:
            raise ReviewNotSaved("proposal_closed")
        return proposal

    def _state(self, proposal_id: str) -> str:
        decisions = self._bridge.list_decisions(proposal_id)
        if any(d.decision is ReviewDecision.REJECTED for d in decisions):
            return "REVIEW_REJECTED"
        revisions = self._bridge.list_revisions(proposal_id)
        latest = revisions[-1].review_revision_id if revisions else None
        if latest is not None and any(d.decision is ReviewDecision.ACCEPTED and d.review_revision_id == latest
                                      for d in decisions):
            return "REVIEW_ACCEPTED"
        return "OPEN"


def _require_actor(actor_label: object) -> None:
    if type(actor_label) is not str or not actor_label.strip():
        raise ReviewNotSaved("actor_blank")


def _verified_selections(decisions: ReviewDecisions, source_text: str, content: dict) -> tuple[EvidenceSelection, ...]:
    """Every selection is an exact, non-blank, contiguous substring of the stored source, with human provenance."""
    selections = decisions.evidence_selections
    if type(selections) is not tuple or not selections:
        raise ReviewNotSaved("selection_none")
    suggestions = {e["excerpt_id"]: e["text"] for e in content["ai_suggested_excerpts"]}
    seen_ids, seen_texts = set(), set()
    for selection in selections:
        if type(selection) is not EvidenceSelection or type(selection.text) is not str \
                or type(selection.selection_id) is not str or not selection.selection_id.strip():
            raise ReviewNotSaved("selection_blank")
        if not selection.text.strip():
            raise ReviewNotSaved("selection_blank")
        if selection.selection_id in seen_ids or selection.text in seen_texts:
            raise ReviewNotSaved("selection_duplicate")
        seen_ids.add(selection.selection_id)
        seen_texts.add(selection.text)
        if selection.text not in source_text:  # re-checked here, whatever the client claims
            raise ReviewNotSaved("selection_not_verbatim")
        if selection.suggested_excerpt_id is not None and suggestions.get(selection.suggested_excerpt_id) != selection.text:
            raise ReviewNotSaved("selection_suggestion_mismatch")
        if type(selection.provenance) is not ProvenanceCategory or selection.provenance not in EVIDENCE_PROVENANCE:
            raise ReviewNotSaved("provenance_invalid")
    return selections


def _candidate(decisions: ReviewDecisions, selections: tuple[EvidenceSelection, ...],
               proposal: AiProposalRecord) -> BaecCandidate:
    by_id = {s.selection_id: s for s in selections}
    source = by_id.get(decisions.source_selection_id)
    if source is None:
        raise ReviewNotSaved("source_selection_unknown")
    # Lineage: stored source -> exact selection -> explicit human BUYER_FACT -> exact text equality. A substring of
    # a larger selection does not qualify; a smaller span must first become its own selection with its own provenance.
    statement = decisions.buyer_exact_statement
    if statement is not None:
        selected = [s for s in selections if s.text == statement]
        if type(statement) is not str or len(selected) != 1:
            raise ReviewNotSaved("buyer_statement_not_selected")
        if selected[0].provenance is not ProvenanceCategory.BUYER_FACT:
            raise ReviewNotSaved("buyer_statement_requires_buyer_fact")
    findings = decisions.findings
    if type(findings) is not tuple or len(findings) != len(CRITERIA) or any(
            type(f) is not CriterionDecision or type(f.criterion) is not BaecCriterion
            or type(f.finding) is not CriterionFinding for f in findings) \
            or {f.criterion for f in findings} != set(CRITERIA):
        raise ReviewNotSaved("criteria_incomplete")
    for finding in findings:
        if type(finding.evidence_selection_ids) is not tuple or any(i not in by_id for i in finding.evidence_selection_ids):
            raise ReviewNotSaved("finding_evidence_unknown")
    stringency = decisions.stringency
    if stringency is not NO_STRINGENCY_STATED:
        if type(stringency) is not StringencyExpression:  # unset: neither "none stated" nor an expression
            raise ReviewNotSaved("stringency_unset")
        if not any(stringency.verbatim_text in s.text for s in selections) or any(
                part is not None and part not in stringency.verbatim_text
                for part in (stringency.qualitative_term, stringency.recurrence_text, stringency.timing_text)):
            raise ReviewNotSaved("stringency_not_verbatim")

    def excerpt(selection: EvidenceSelection) -> EvidenceExcerpt:
        return EvidenceExcerpt(selection.text, selection.provenance, proposal.interaction_id)

    ordered = sorted(findings, key=lambda f: CRITERIA.index(f.criterion))
    try:
        return BaecCandidate(
            account_id=proposal.account_id, source_interaction_id=proposal.interaction_id,
            source_excerpt=excerpt(source),
            assessments=tuple(CriterionAssessment(f.criterion, f.finding,
                                                  tuple(excerpt(by_id[i]) for i in f.evidence_selection_ids))
                              for f in ordered),
            articulation_origin=decisions.articulation_origin, elicitation_mode=decisions.elicitation_mode,
            buyer_exact_statement=statement, buyer_role=None,
            stringency=None if stringency is NO_STRINGENCY_STATED else stringency,
        )
    except (DomainValidationError, TypeError, ValueError):
        raise ReviewNotSaved("candidate_invalid") from None


def _disposition(ai_value: str | None, final_value: str | None) -> str:
    if final_value is None:
        return "NONE"
    return "KEEP_AI" if final_value == ai_value else "EDITED"


def _stringency_content(stringency) -> dict | None:
    if stringency is NO_STRINGENCY_STATED:
        return None
    return {
        "verbatim_text": stringency.verbatim_text,
        "comparator": None if stringency.comparator is None else stringency.comparator.value,
        "numeric_value": None if stringency.numeric_value is None else str(stringency.numeric_value),
        "unit": stringency.unit, "qualitative_term": stringency.qualitative_term,
        "recurrence_text": stringency.recurrence_text, "timing_text": stringency.timing_text,
    }


def _review_content(proposal: AiProposalRecord, number: int, previous_id: str | None, decisions: ReviewDecisions,
                    captured_at: str) -> dict:
    """baec-ai-review-content/v1 (design §13.2): the authoritative reviewed content the digest covers."""
    snapshot = json.loads(proposal.content)
    ai_condition = snapshot["ai_normalized_condition"]["value"]
    ai_link = snapshot["ai_normalized_evaluation_link"]["value"]
    findings = sorted(decisions.findings, key=lambda f: CRITERIA.index(f.criterion))
    return {
        "review_content_version": BRIDGE_REVIEW_CONTENT_VERSION,
        "normalization_validation_version": HUMAN_NORMALIZATION_VALIDATION_VERSION,
        "proposal_id": proposal.proposal_id,
        "proposal_digest": proposal.proposal_digest,
        "account_id": proposal.account_id,
        "interaction_id": proposal.interaction_id,
        "artifact_id": proposal.artifact_id,
        "revision_number": number,
        "previous_revision_id": previous_id,
        "evidence_selections": [
            {"selection_id": s.selection_id, "text": s.text, "provenance": s.provenance.value,
             "suggested_excerpt_id": s.suggested_excerpt_id} for s in decisions.evidence_selections],
        "source_selection_id": decisions.source_selection_id,
        "buyer_exact_statement": decisions.buyer_exact_statement,
        "buyer_role": None,  # Phase 7: representational authority is deferred (RC-19)
        "findings": [{"criterion": f.criterion.value, "finding": f.finding.value,
                      "evidence_selection_ids": list(f.evidence_selection_ids)} for f in findings],
        "articulation_origin": decisions.articulation_origin.value,
        "elicitation_mode": decisions.elicitation_mode.value,
        "stringency": _stringency_content(decisions.stringency),
        "normalization": {
            "condition": {"disposition": _disposition(ai_condition, decisions.final_normalized_condition),
                          "ai_value": ai_condition, "final_value": decisions.final_normalized_condition},
            "evaluation_link": {"disposition": _disposition(ai_link, decisions.final_normalized_evaluation_link),
                                "ai_value": ai_link, "final_value": decisions.final_normalized_evaluation_link},
        },
        "captured_at": captured_at,
    }


def stringency_from_fields(*, verbatim_text: str, comparator, numeric_value: str | None, unit: str | None,
                           qualitative_term: str | None, recurrence_text: str | None,
                           timing_text: str | None) -> StringencyExpression:
    """Build a StringencyExpression from human-entered fields through the domain constructor (fail closed)."""
    try:
        value = None if numeric_value in (None, "") else Decimal(numeric_value)
        return StringencyExpression(verbatim_text, comparator, value, unit or None, qualitative_term or None,
                                    recurrence_text or None, timing_text or None)
    except (DomainValidationError, ArithmeticError, TypeError, ValueError):
        raise ReviewNotSaved("candidate_invalid") from None


def _decisions_from_content(content: dict) -> ReviewDecisions:
    """The ReviewDecisions a stored baec-ai-review-content/v1 revision records, rebuilt from its fields only."""
    stringency = content["stringency"]
    return ReviewDecisions(
        evidence_selections=tuple(EvidenceSelection(e["selection_id"], e["text"], ProvenanceCategory(e["provenance"]),
                                                    e["suggested_excerpt_id"]) for e in content["evidence_selections"]),
        source_selection_id=content["source_selection_id"],
        buyer_exact_statement=content["buyer_exact_statement"],
        findings=tuple(CriterionDecision(BaecCriterion(f["criterion"]), CriterionFinding(f["finding"]),
                                         tuple(f["evidence_selection_ids"])) for f in content["findings"]),
        articulation_origin=ArticulationOrigin(content["articulation_origin"]),
        elicitation_mode=ElicitationMode(content["elicitation_mode"]),
        stringency=NO_STRINGENCY_STATED if stringency is None else stringency_from_fields(
            verbatim_text=stringency["verbatim_text"],
            comparator=None if stringency["comparator"] is None else ThresholdComparator(stringency["comparator"]),
            numeric_value=stringency["numeric_value"], unit=stringency["unit"],
            qualitative_term=stringency["qualitative_term"], recurrence_text=stringency["recurrence_text"],
            timing_text=stringency["timing_text"]),
        final_normalized_condition=content["normalization"]["condition"]["final_value"],
        final_normalized_evaluation_link=content["normalization"]["evaluation_link"]["final_value"],
    )


def rebuild_reviewed_candidate(proposal: AiProposalRecord, revision: ReviewRevisionRecord,
                               interaction_text: str) -> BaecCandidate:
    """Re-derive the confirmation candidate from one immutable reviewed revision (Phase 7F-B).

    Authority comes only from the stored human review: its selections are re-checked against the stored source,
    its provenance and buyer-exact-statement lineage are re-checked, the human-normalization contract is re-run
    over the selected texts, and the revision's content must re-serialize byte for byte from what was rebuilt.
    Nothing earlier (a preview, a UI state, an AI value) is trusted. Raises ReviewNotSaved with a closed code.
    """
    try:
        content = json.loads(revision.content)
        decisions = _decisions_from_content(content)
    except (KeyError, TypeError, ValueError):
        raise ReviewNotSaved("proposal_integrity_failure") from None
    selections = _verified_selections(decisions, interaction_text, json.loads(proposal.content))
    candidate = _candidate(decisions, selections, proposal)
    codes = validate_final_normalization(
        normalized_condition=decisions.final_normalized_condition,
        normalized_evaluation_link=decisions.final_normalized_evaluation_link,
        evidence_texts=tuple(s.text for s in selections),
    )
    if codes:
        raise ReviewNotSaved("normalization_invalid", codes)
    rebuilt = canonical_text(_review_content(proposal, revision.revision_number, revision.previous_revision_id,
                                             decisions, content["captured_at"]))
    if rebuilt != revision.content:
        raise ReviewNotSaved("proposal_integrity_failure")
    return candidate
