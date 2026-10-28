"""Synthetic demo data for BAEC Console: Riverside Outpatient Surgery Center and the Halvern Surgical Supply scenario.

Every name, statement, notice, and figure here is fictional. The seed writes only to the synthetic workspace,
and only through the same ConsoleService methods a salesperson uses, so every engine rule applies: Engine 1
classifies and its gate records the confirmation, Engine 2 activates the plan and derives the correspondence
outcome, and Engine 3 validates every prepared draft. Seeded human actions are performed by the organization's
synthetic-seed user (Console.seed_service), whose display name SEED_ACTOR names the seed, not a real person.
Seeded accounts belong to the organization of the principal who loaded the demo.

Three modes:
    "call_notes" the account and its contact only; the user records CALL_NOTES through CRM intake (optionally
                 analyzed with their own Claude API key) and walks the whole workflow;
    "beginning"  the account, its contact, and the recorded buyer interaction; the user walks the workflow from
                 Engine 1 without needing a Claude API key;
    "completed"  the whole workflow, through human acceptance of the prepared drafts.
"""

from __future__ import annotations

from datetime import datetime, timezone

from baec_app.domain.enums import (
    AccountState,
    ArticulationOrigin,
    BaecCriterion,
    CriterionFinding,
    ElicitationMode,
    ReviewAnswer,
    ThresholdComparator,
)
from baec_app.engine2.domain import CorrespondenceDimension, DimensionFinding, SufficiencyFinding
from baec_app.engine2.measurement import Transformation
from baec_app.engine3.enums import HumanReviewDecision, PreparationChannel, PreparationGoal

from .console_service import ATTESTATIONS, Console
from .deterministic_preparation import G_STATEMENT, G_THRESHOLD, G_TIMING, g_evidence, g_measurement
from .engine1_adapter import ConditionForm
from .engine2_adapter import ObservationForm, SourceForm
from .identity import SEED_DISPLAY_NAME

MODES = ("call_notes", "beginning", "completed")
SEED_ACTOR = SEED_DISPLAY_NAME
ACCOUNT = "Riverside Outpatient Surgery Center"
INDUSTRY = "Ambulatory surgery (synthetic)"
SUPPLIER = "Halvern Surgical Supply"
CONTACT_NAME = "Morgan Lee"
BUYER_ROLE = "Materials Manager"
NON_EVALUATION = "We're not evaluating other suppliers today"
BUYER_STATEMENT = ("If our contracted pricing increases by at least 7% at the April renewal, "
                   "we would compare other options.")
SELLER_NOTES = ("Morgan said the team is not evaluating other surgical supply vendors today. The current Halvern "
                "supply agreement renews in April.")
CALL_NOTES = (
    f"Quarterly check-in call with {CONTACT_NAME}, {BUYER_ROLE} (synthetic). Morgan said: \"{NON_EVALUATION}. "
    f"{BUYER_STATEMENT}\" The current {SUPPLIER} supply agreement renews in April. No next meeting was scheduled."
)
INITIAL_NOTICE = (
    "Renewal notice for supply agreement RSC-4410: from 1 April 2027 the annual contracted price changes from "
    "USD 120,000 to USD 130,800."
)
CORRECTED_NOTICE = (
    "Correction to renewal notice for supply agreement RSC-4410: the previously reported amount was incorrect. "
    "From 1 April 2027 the annual contracted price changes from USD 120,000 to USD 128,400."
)
PARAPHRASE = "a contracted pricing increase of at least 7% at the April renewal could make it worth comparing options"


class ScriptedClock:
    """A fixed clock the seed moves forward step by step, so the seeded history is deterministic."""

    def __init__(self, at: datetime) -> None:
        self.at = at

    def now(self) -> datetime:
        return self.at


def _at(month: int, day: int, hour: int = 9, minute: int = 0) -> datetime:
    return datetime(2026, month, day, hour, minute, tzinfo=timezone.utc)


def condition_form() -> ConditionForm:
    met = CriterionFinding.MET.value
    return ConditionForm(
        criteria={
            BaecCriterion.PRESENT_NON_EVALUATION.value: {
                "finding": met, "excerpt": "the team is not evaluating other surgical supply vendors today",
                "rationale": "Seller notes record that the buyer is not evaluating alternatives."},
            BaecCriterion.PROSPECTIVE_CONDITION.value: {
                "finding": met,
                "excerpt": "If our contracted pricing increases by at least 7% at the April renewal",
                "rationale": "The renewal has not happened yet."},
            BaecCriterion.BUYER_ARTICULATION.value: {"finding": met, "excerpt": BUYER_STATEMENT, "rationale": ""},
            BaecCriterion.EVALUATION_LINKAGE.value: {
                "finding": met, "excerpt": "we would compare other options", "rationale": ""},
        },
        origin=ArticulationOrigin.BUYER_GENERATED.value,
        elicitation=ElicitationMode.CEE_ELICITED.value,
        threshold_text="by at least 7%", comparator=ThresholdComparator.AT_LEAST.value, threshold_value="7",
        threshold_unit="%", timing_text="at the April renewal",
    )


def load(console: Console, mode: str) -> str:
    """Reset the synthetic workspace, then seed it. Returns the synthetic account id. Local data is untouched."""
    if mode not in MODES:
        raise ValueError(f"unknown seed mode {mode!r}")
    console.reset_synthetic()
    service = console.seed_service()
    original_clock = service.clock
    clock = ScriptedClock(_at(9, 4, 15, 0))
    service.clock = clock
    try:
        account_id = service.create_account(ACCOUNT, industry=INDUSTRY, owner_note="Synthetic example account",
                                            synthetic=True)
        clock.at = _at(9, 4, 15, 10)
        contact_id = service.crm_add_contact(account_id, name=CONTACT_NAME, title=BUYER_ROLE,
                                             notes="Synthetic contact")
        if mode == "call_notes":
            return account_id
        clock.at = _at(9, 4, 15, 30)
        interaction_id = _record_call(service, account_id, contact_id)
        if mode == "beginning":
            return account_id
        _complete(service, clock, account_id, interaction_id)
        return account_id
    finally:
        service.clock = original_clock


def _record_call(service, account_id: str, contact_id: str) -> str:
    """The CRM activity and its Engine 1 interaction, linked to the contact, as the CRM intake's manual path would
    record them. No AI is involved and no condition is captured."""
    occurred_at = _at(9, 4, 14, 0)
    activity_id = service.crm_add_activity(
        account_id, contact_id=contact_id, interaction_type="Call", occurred_at=occurred_at, raw_notes=CALL_NOTES,
        summary="Not evaluating other suppliers; stated a pricing condition for the April renewal.",
        buyer_role=BUYER_ROLE, buyer_statement=BUYER_STATEMENT, seller_notes=SELLER_NOTES, ai_assisted=False)
    interaction_id = service.record_interaction(account_id, buyer_role=BUYER_ROLE, occurred_at=occurred_at,
                                                buyer_statement=BUYER_STATEMENT, seller_notes=SELLER_NOTES)
    service.crm_attach_engine1_interaction(activity_id, interaction_id)
    service.crm_link_interaction(interaction_id, account_id, contact_id=contact_id, interaction_type="Call")
    return interaction_id


def _complete(service, clock: ScriptedClock, account_id: str, interaction_id: str) -> None:
    D = CorrespondenceDimension
    clock.at = _at(9, 4, 15, 40)
    draft_id = service.capture_condition(account_id, interaction_id, condition_form())
    clock.at = _at(9, 4, 15, 45)
    preview = service.preview_condition(draft_id)
    baec_id = service.confirm_condition(draft_id, displayed_fingerprint=preview["fingerprint"])

    clock.at = _at(9, 4, 15, 50)
    judgment_id = service.record_dormancy_judgment(
        baec_id,
        plausibility=ReviewAnswer.YES,
        addressability=ReviewAnswer.YES,
        notes="Synthetic demo: human judged the buyer-defined condition plausible and addressable.",
    )

    clock.at = _at(9, 4, 15, 55)
    service.change_account_state(
        account_id,
        AccountState.CONDITIONALLY_DORMANT,
        {"baec_id": baec_id, "judgment_id": judgment_id},
    )

    clock.at = _at(9, 4, 16, 0)
    service.start_monitoring(
        account_id, baec_id, target_entities=(f"{SUPPLIER} (synthetic)", f"{ACCOUNT} (synthetic)"),
        required=frozenset({D.ENTITY_MATCH, D.SOURCE_RELEVANCE, D.EVENT_OR_CONDITION_TYPE, D.THRESHOLD_MATCH,
                            D.COMPARATOR_MATCH, D.NUMERIC_MAGNITUDE, D.UNIT_MATCH, D.RENEWAL_OR_EFFECTIVE_DATE_MATCH}),
        sources=(SourceForm("Supplier renewal notice", f"{SUPPLIER} (synthetic)",
                            "Forwarded by the buyer under the account agreement", "Buyer forwards notices by email",
                            "Notice text and dates"),))

    source_id = next(iter(service.account_view(account_id).sources))

    clock.at = _at(10, 12, 10, 45)
    initial = service.add_observation(baec_id, ObservationForm(
        source_id=source_id,
        observed_at=_at(10, 12, 10, 30),
        content=INITIAL_NOTICE,
        effective_at=datetime(2027, 4, 1, tzinfo=timezone.utc),
        source_locator="Forwarded renewal notice, 12 October 2026",
        transformation=Transformation.PERCENT_CHANGE_FROM_TWO_PRICES.value,
        baseline_value="120000",
        new_value="130800",
        value_unit="USD per year",
        flag_for_review=False,
    ))

    clock.at = _at(10, 12, 11, 0)
    result = service.add_observation(baec_id, ObservationForm(
        source_id=source_id,
        observed_at=_at(10, 12, 10, 50),
        content=CORRECTED_NOTICE,
        effective_at=datetime(2027, 4, 1, tzinfo=timezone.utc),
        source_locator="Forwarded correction, 12 October 2026",
        corrects_observation_id=initial.observation_id,
        transformation=Transformation.PERCENT_CHANGE_FROM_TWO_PRICES.value,
        baseline_value="120000",
        new_value="128400",
        value_unit="USD per year",
    ))

    clock.at = _at(10, 15, 9, 30)
    packet = service.review_packet(result.candidate_id)
    supported = DimensionFinding.SUPPORTED.value
    review = service.submit_correspondence_review(
        result.candidate_id, displayed_context_digest=packet.context_digest,
        findings={d: supported for d in packet.plan.required_dimensions}, notes={},
        sufficiency=SufficiencyFinding.YES.value)

    clock.at = _at(10, 15, 9, 45)
    request_id = service.request_preparation(review.review.review_id, {
        "channel": PreparationChannel.EMAIL_AND_CALL.value,
        "goal": PreparationGoal.ASK_WHETHER_TO_REVISIT_PRIOR_DISCUSSION.value,
        "statement_use": "PARAPHRASE", "paraphrase": PARAPHRASE,
        # The Engine 3 Stage A section 14 example disclosure choices.
        "dispositions": {G_STATEMENT: "PARAPHRASE_ALLOWED", G_THRESHOLD: "DIRECT_REFERENCE_ALLOWED",
                         G_TIMING: "DIRECT_REFERENCE_ALLOWED", g_evidence(result.observation_id): "INTERNAL_ONLY",
                         g_measurement(result.measurement_id): "PARAPHRASE_ALLOWED"},
        "source_identity": {}, "adversity": {},
    })
    clock.at = _at(10, 15, 9, 46)
    preparation_id = service.generate_preparation(request_id)

    clock.at = _at(10, 15, 10, 0)
    attest = {key: True for key, _ in ATTESTATIONS}
    for output in service.preparation_view(preparation_id)["preparation"]["artifacts"]:
        if output["artifact"] and output["report"]["allowed"]:
            service.review_artifact(preparation_id, output["artifact"]["artifact_id"],
                                    HumanReviewDecision.ACCEPT_AS_DRAFTED, attestations=attest)
