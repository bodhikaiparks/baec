"""Public-demo copy and presentation helpers for BAEC Engine 2. Deterministic, and free of Streamlit.

The demo simplifies presentation, never authority. The visitor answers every judgment the locked
HumanCorrespondenceReview requires: a finding for each of the 11 dimensions and each of the 4 cross-cutting checks,
sufficiency, and BAEC revalidation. Dimensions are shown in three groups; the visitor writes one reason and chooses
the cited evidence once per group, and that reason and evidence are recorded on each dimension of the group (none on
a dimension the visitor marks not applicable). The locked domain then validates the review, and the locked
classifier derives the result. Nothing here decides a finding, usability, or an outcome.
"""

from __future__ import annotations

from dataclasses import dataclass

from .. import review
from ..classification import CrossCuttingCheck, DimensionAssessment
from ..domain import CorrespondenceDimension, CorrespondenceOutcome, CrossCuttingCheckName, DimensionFinding, SufficiencyFinding

D = CorrespondenceDimension
C = CrossCuttingCheckName

PAGE_TITLE = "BAEC Engine 2: Monitoring + Correspondence"
TITLE = "BAEC Engine 2"
SUBTITLE = "Monitoring + Correspondence"
ENGINE1_LINE = "Engine 1 remembers what the buyer said would make evaluation worthwhile."
ENGINE2_LINE = ("Engine 2 watches authorized evidence for possible correspondence with that buyer-defined condition "
                "and sends the result to human review.")
BOUNDARY = ("Correspondence narrows human attention. It does not establish that the buyer is currently evaluating, "
            "intends to purchase, or that an opportunity exists.")
NOTICE = ("Research prototype", "Synthetic data only", "No real customer information", "No live AI call",
          "Not empirically validated", "Not production software")
AI_NOTE = ("Engine 2 also contains an optional advisory-AI architecture. It is intentionally not used in this public "
           "demonstration and is not authoritative.")
FOOTER = "BAEC Trigger Intelligence | Research prototype | Bodhi Parks"
SYNTHETIC_DATES = "Dates shown are part of the synthetic scenario."
SESSION_NOTE = "Your demo session is temporary, isolated to you, and discarded on restart or when the session ends."
STAGES = ("What the buyer said", "Monitoring", "New evidence", "Human review", "Result")

CONDITION_NOT_INTENT = ("This is not purchase intent. It is a condition the buyer previously said would make "
                        "evaluation worthwhile.")
ENGINE2_STARTS_HERE = ("Engine 1 has already verified and confirmed this condition with a human reviewer. Engine 2 "
                       "starts from that confirmed record and does not change it.")
SOURCES_MATTER = ("Engine 2 watches only sources a person approved for this plan, each with a recorded basis. It does "
                  "not browse arbitrary information about the buyer. Items from any other source are refused and "
                  "never become evidence.")
HISTORY_LINE = "Engine 2 preserves the history instead of silently replacing evidence."
CURRENT_HEADING = "Currently usable evidence"
HISTORY_HEADING = "Full evidence history"
REVIEW_HEADLINE = "You decide. The rule derives the result."
REVIEW_INTRO = ("Answer every question below. Nothing is pre-filled, and there is no AI suggestion. When you submit, "
                "your review is recorded and the locked Engine 2 rule derives the result from your answers.")
GROUP_NOTE = ("Your reason and cited evidence apply to every question in this group, except any you mark "
              "Not applicable.")
ALIAS_NOTE = "Use initials or an alias. Do not enter personal or customer information."
GUIDANCE = (
    "The strongest evidence is the corrected renewal notice for contract AR-2207. It is addressed to this buyer, comes "
    "from the supplier, and concerns the price change the buyer named.",
    "The buyer's threshold was more than 8%. The current usable derived measurement, computed exactly from the "
    "corrected notice, is 9.00%.",
    "The first notice reported 12.00%. The supplier corrected it, so that value stays in the history but can no "
    "longer support a finding.",
    "The corrected notice takes effect at the July renewal, the time the buyer named.",
    "The general announcement covers all customers rather than this contract. The price-freeze statement was "
    "retracted. The forum post came from an unapproved source and was refused.",
    "The Monitoring Plan did not require three questions: relationship match, timing match, and causal or contextual "
    "relevance. Mark those Not applicable.",
    "This guidance explains the evidence. It never answers a question for you, and nothing is submitted until you "
    "choose Submit Human Review.",
)
NOT_MEAN = ("The buyer is not proven to be currently evaluating.", "Purchase intent has not been established.",
            "No opportunity has been created.", "No outreach has been authorized.", "No account state has changed.")
FLOW = ("Buyer-defined condition", "Authorized monitoring", "New evidence", "Possible correspondence", "Human review",
        "Verified correspondence")
OUTCOME_TEXT = {
    CorrespondenceOutcome.HUMAN_VERIFIED_CORRESPONDENCE:
        "The currently usable evidence corresponds to the buyer-defined condition under the locked Engine 2 rule and "
        "the human review you just completed.",
    CorrespondenceOutcome.POSSIBLE_CORRESPONDENCE:
        "Correspondence is not verified. Under the locked Engine 2 rule and your review, at least one required "
        "question remains unresolved, the evidence was not judged sufficient, or the buyer's condition was flagged "
        "for revalidation.",
    CorrespondenceOutcome.NO_CORRESPONDENCE:
        "Under the locked Engine 2 rule and your review, at least one required question was found contradicted, so "
        "the evidence does not correspond to the buyer-defined condition.",
}

FINDING_LABELS = {"SUPPORTED": "Supported", "CONTRADICTED": "Contradicted", "UNRESOLVED": "Unresolved",
                  "NOT_APPLICABLE": "Not applicable"}
FINDINGS = tuple(f.value for f in DimensionFinding)
SUFFICIENCY_LABELS = {"YES": "Yes, sufficient", "NO": "No, not sufficient", "UNKNOWN": "Cannot tell"}
SUFFICIENCY = tuple(s.value for s in SufficiencyFinding)
REVALIDATION_LABELS = {"NOT_REQUIRED": "No", "REQUIRED": "Yes, the buyer's condition should be revalidated"}
CHECK_LABELS = {"NO_CONCERN": "No concern", "CONCERN": "Concern to record"}
NO_CONCERN_NOTE = "The reviewer recorded no concern for this check."

# The locked Stage A questions (contract section 8.2), stated without the contract's worked example.
QUESTIONS = {
    D.ENTITY_MATCH: "Is the evidence about the entity the condition concerns?",
    D.RELATIONSHIP_MATCH: "Does it concern the buyer's own relationship or agreement, rather than the entity's "
                          "dealings generally?",
    D.SOURCE_RELEVANCE: "Is the source capable of establishing this, for example a notice addressed to the buyer "
                        "rather than a general announcement?",
    D.EVENT_OR_CONDITION_TYPE: "Is the reported event the kind of change the buyer named?",
    D.THRESHOLD_MATCH: "Does the reported magnitude meet the buyer's threshold under the buyer's comparator?",
    D.COMPARATOR_MATCH: "Is the comparison applied exactly as stated? \"More than\" is strict.",
    D.NUMERIC_MAGNITUDE: "Is the magnitude the evidence states established exactly as stated?",
    D.UNIT_MATCH: "Is the evidence in the buyer's unit?",
    D.TIMING_MATCH: "Does the evidence satisfy the buyer's timing context?",
    D.RENEWAL_OR_EFFECTIVE_DATE_MATCH: "Does the effective date align with the renewal or other stated time?",
    D.CAUSAL_OR_CONTEXTUAL_RELEVANCE: "Is the evidence about the condition the buyer described, rather than a "
                                      "coincidental match on words or numbers?",
}
CHECK_QUESTIONS = {
    C.FRESHNESS: "Is the evidence current, and not superseded, retracted, or outdated?",
    C.CONTRADICTORY_EVIDENCE: "Does any accepted evidence contradict it?",
    C.MISSING_EVIDENCE: "Is anything needed to resolve an unresolved question missing?",
    C.PROVENANCE_QUALITY: "Does the evidence come from an authorized source with complete provenance?",
}


@dataclass(frozen=True)
class Group:
    key: str
    title: str
    dimensions: tuple[CorrespondenceDimension, ...]


GROUPS = (
    Group("subject", "Supplier and event",
          (D.ENTITY_MATCH, D.RELATIONSHIP_MATCH, D.SOURCE_RELEVANCE, D.EVENT_OR_CONDITION_TYPE)),
    Group("threshold", "Buyer's threshold",
          (D.THRESHOLD_MATCH, D.COMPARATOR_MATCH, D.NUMERIC_MAGNITUDE, D.UNIT_MATCH)),
    Group("timing", "Timing and context",
          (D.TIMING_MATCH, D.RENEWAL_OR_EFFECTIVE_DATE_MATCH, D.CAUSAL_OR_CONTEXTUAL_RELEVANCE)),
)
assert sorted(d.name for g in GROUPS for d in g.dimensions) == sorted(d.name for d in D)  # every dimension, once


SUBSTEPS = ("4A  Supplier and event", "4B  Buyer's threshold", "4C  Timing and context",
            "4D  Evidence quality and conclusion")
SUBSTEP_NAV = ("4A Supplier", "4B Threshold", "4C Timing", "4D Conclusion")
OUTCOME_HEADLINES = {
    CorrespondenceOutcome.HUMAN_VERIFIED_CORRESPONDENCE: "Verified correspondence",
    CorrespondenceOutcome.POSSIBLE_CORRESPONDENCE: "Possible correspondence",
    CorrespondenceOutcome.NO_CORRESPONDENCE: "No correspondence",
}
WHY_MATCHED = ("The currently usable evidence crossed the buyer-defined threshold in the relevant renewal context, and "
               "the human review found the required correspondence dimensions supported.")


def flow_line() -> str:
    """The result flow on one line. Each step keeps its words together, so lines break only between steps."""
    return " → ".join(step.replace(" ", "\u00a0") for step in FLOW)


def substep_complete(values: dict, index: int) -> bool:
    """Whether a review substep is fully answered. Display only; submission is validated as before."""
    if index < len(GROUPS):
        group = GROUPS[index]
        findings = [values.get(f"finding:{d.name}") for d in group.dimensions]
        if any(_blank(f) for f in findings) or _blank(values.get(f"reason:{group.key}")):
            return False
        decided = any(f in ("SUPPORTED", "CONTRADICTED") for f in findings)
        return not decided or bool(values.get(f"evidence:{group.key}"))
    for c in CrossCuttingCheckName:
        status = values.get(f"check:{c.name}")
        if _blank(status) or (status == "CONCERN" and _blank(values.get(f"check_note:{c.name}"))):
            return False
    if _blank(values.get("sufficiency")) or _blank(values.get("revalidation")) or _blank(values.get("alias")):
        return False
    return values["revalidation"] != "REQUIRED" or bool(triggers(values))


def percent(value, unit: str) -> str:
    """Presentation only: '9.00 %' reads as '9.00%'. Stored values and exact evidence text are never changed."""
    return f"{value}%" if unit.strip() == "%" else f"{value} {unit}"


def threshold_phrase(comparator: str, value, unit: str) -> str:
    phrase = comparator_phrase(comparator)
    return f"{phrase[:1].upper()}{phrase[1:]} {percent(value, unit)}"


def watching_for(comparator: str, value, unit: str, timing: str | None) -> str:
    """A plain sentence for the Monitoring screen, from the plan's own threshold and timing."""
    when = f" {timing}" if timing else ""
    if comparator == "GREATER_THAN":
        return f"A price increase above the buyer's {percent(value, unit)} threshold{when}."
    return f"A price change {comparator_phrase(comparator)} {percent(value, unit)}{when}."


def timing_label(timing: str | None) -> str:
    """'at the July renewal' reads as 'July renewal'. Display only."""
    if not timing:
        return "none stated"
    return timing.removeprefix("at the ").removeprefix("at ")


def comparator_phrase(comparator: str) -> str:
    return {"GREATER_THAN": "more than", "AT_LEAST": "at least", "LESS_THAN": "less than", "AT_MOST": "at most",
            "EXACTLY": "exactly", "APPROXIMATELY": "approximately"}.get(comparator, comparator)


def timeline(packet: review.ReviewPacket, rows) -> list:
    """Observations in the order they were observed. Labels come from the ledger; this only orders them."""
    by_id = {o.observation_id: o for o in packet.ledger.observations}
    return sorted(rows, key=lambda row: (by_id[row.observation_id].observed_at, row.observation_id))


def _blank(value) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def missing_answers(values: dict) -> list[str]:
    """Every question still unanswered. Display only; the locked domain validates the review itself."""
    missing = []
    for group in GROUPS:
        for d in group.dimensions:
            if _blank(values.get(f"finding:{d.name}")):
                missing.append(d.label)
        if _blank(values.get(f"reason:{group.key}")):
            missing.append(f"Reason for \"{group.title}\"")
    for c in CrossCuttingCheckName:
        status = values.get(f"check:{c.name}")
        if _blank(status):
            missing.append(f"{c.label} check")
        elif status == "CONCERN" and _blank(values.get(f"check_note:{c.name}")):
            missing.append(f"{c.label} concern note")
    if _blank(values.get("sufficiency")):
        missing.append("Evidence sufficiency")
    if _blank(values.get("revalidation")):
        missing.append("BAEC revalidation")
    elif values["revalidation"] == "REQUIRED" and not triggers(values):
        missing.append("Revalidation reason")
    if _blank(values.get("alias")):
        missing.append("Reviewer alias")
    return missing


def triggers(values: dict) -> tuple[str, ...]:
    return tuple(line for line in (values.get("triggers") or "").splitlines() if line.strip())


def review_arguments(values: dict) -> dict:
    """The complete human judgments for Stage I submit_human_review, exactly as the visitor gave them."""
    findings = {}
    for group in GROUPS:
        evidence = tuple(values.get(f"evidence:{group.key}") or ())
        for d in group.dimensions:
            finding = DimensionFinding(values[f"finding:{d.name}"])
            cited = () if finding is DimensionFinding.NOT_APPLICABLE else evidence
            findings[d] = DimensionAssessment(d, finding, cited, values[f"reason:{group.key}"])
    checks = []
    for c in CrossCuttingCheckName:
        if values[f"check:{c.name}"] == "CONCERN":
            checks.append(CrossCuttingCheck(
                c, values[f"check_note:{c.name}"], tuple(values.get(f"check_evidence:{c.name}") or ()),
                frozenset(CorrespondenceDimension[n] for n in values.get(f"check_dimensions:{c.name}") or ())))
        else:
            checks.append(CrossCuttingCheck(c, NO_CONCERN_NOTE))
    required = values["revalidation"] == "REQUIRED"
    return dict(findings=tuple(findings[d] for d in D), checks=tuple(checks),
                sufficiency=SufficiencyFinding(values["sufficiency"]), reviewer=values["alias"],
                baec_revalidation_required=required, revalidation_triggers=triggers(values) if required else ())
