"""DETERMINISTIC_CONSOLE_TEMPLATE_V1: a fixed, offline preparation template for BAEC Console V1.

This is a local product convenience, not a research result and not an AI generator. It fills fixed sentence
patterns (modelled on the Engine 3 Stage A section 14 allowed patterns) with recorded values: the buyer's exact
statement, the human-written paraphrase from the Preparation Request, the recorded evidence, and the derived
measurement. Each sentence is a DraftSegment with its claim type, semantic class, grounding references, and
reference-use mode declared, so the released Engine 3 validator can check it.

The template never checks or repairs its own output. Whatever it builds is submitted unchanged to
baec_app.engine3.validate_case; a refusal is shown as a refusal. Grounding references are declared
conservatively: a sentence that describes the buyer's condition cites the statement, the threshold, and the
timing, so a missing disclosure permission for any of them refuses the sentence rather than leaking it.
"""

from __future__ import annotations

import re

from baec_app.engine3.enums import (
    ArtifactType,
    ClaimSemanticClass,
    ClaimType,
    PreparationChannel,
    PreparationGoal,
    ReferenceUseMode,
)
from baec_app.engine3.models import ClaimField, DraftSegment
from baec_app.engine3.proposal_contract import (
    INTERNAL_SUMMARY_BOUNDARY_STATEMENT,
    SEGMENT_SEPARATOR,
    ProposedArtifact,
)

from .console_store import CONSOLE_GENERATOR_ORIGIN as GENERATOR  # noqa: E402  (one source for the constant)

G_STATEMENT = "G-BUYER-STATEMENT"
G_THRESHOLD = "G-THRESHOLD"
G_TIMING = "G-TIMING"
G_CORRESPONDENCE = "G-CORRESPONDENCE"


def g_evidence(observation_id: str) -> str:
    return f"G-EVIDENCE-{observation_id}"


def g_measurement(measurement_id: str) -> str:
    return f"G-MEASUREMENT-{measurement_id}"


CLOSING_QUESTIONS = {
    PreparationGoal.CHECK_CONDITION_STILL_RELEVANT: "Is that still relevant on your side?",
    PreparationGoal.ASK_WHETHER_TO_REVISIT_PRIOR_DISCUSSION: "Would it be useful to revisit the conversation?",
    PreparationGoal.ASK_WHAT_HAS_CHANGED: "Has anything changed on your side?",
    PreparationGoal.UNDERSTAND_WHETHER_EVALUATION_REOPENED:
        "Has your team started looking at options again, or is it too early to say?",
}


def _seg(segment_id: str, text: str, claim_type: ClaimType, semantic: ClaimSemanticClass, refs=(),
         mode: ReferenceUseMode = ReferenceUseMode.DIRECT, field: ClaimField = ClaimField.NONE,
         declared: str | None = None, specificity: int = 0, premise: bool = False) -> DraftSegment:
    return DraftSegment(segment_id=segment_id, text=text, claim_type=claim_type, semantic_class=semantic,
                        grounding_refs=tuple(refs), reference_use_mode=mode, claim_field=field,
                        declared_value=declared, specificity=specificity, question_has_premise=premise)


def _artifact(artifact_id: str, artifact_type: ArtifactType, segments: list[DraftSegment]) -> ProposedArtifact:
    return ProposedArtifact(artifact_id, artifact_type, tuple(segments),
                            SEGMENT_SEPARATOR.join(s.text for s in segments))


def _condition_refs(snapshot: dict) -> list[str]:
    refs = [G_STATEMENT]
    if snapshot.get("threshold_text"):
        refs.append(G_THRESHOLD)
    if snapshot.get("timing_text"):
        refs.append(G_TIMING)
    return refs


# The template already supplies this lead-in, so a paraphrase that repeats it would read "you mentioned that you
# mentioned that ...". Only the lead-in is dropped; the rest of the human-written paraphrase is used unchanged.
_LEAD_IN = re.compile(r"^(?:when we (?:last )?spoke,?\s*)?you mentioned(?:\s+that\b)?[\s,:]*", re.IGNORECASE)


def paraphrase_body(paraphrase: str) -> str:
    body = paraphrase.strip()
    stripped = _LEAD_IN.sub("", body, count=1)
    return (stripped or body).rstrip(".")


def _prior_reference(segment_id: str, snapshot: dict, request: dict) -> DraftSegment:
    if request["statement_use"] == "QUOTE":
        return _seg(segment_id, f'When we last spoke, you said: "{snapshot["buyer_statement"]}"',
                    ClaimType.PRIOR_BUYER_STATEMENT_REFERENCE, ClaimSemanticClass.CONDITION_REFERENCE,
                    _condition_refs(snapshot), ReferenceUseMode.DIRECT)
    paraphrase = paraphrase_body(request["paraphrase"])
    return _seg(segment_id, f"When we last spoke, you mentioned that {paraphrase}.",
                ClaimType.PRIOR_BUYER_STATEMENT_REFERENCE, ClaimSemanticClass.CONDITION_REFERENCE,
                _condition_refs(snapshot), ReferenceUseMode.PARAPHRASE)


def _relevance_refs(snapshot: dict) -> list[str]:
    if snapshot["measurements"]:
        return [g_measurement(m["id"]) for m in snapshot["measurements"]]
    return [g_evidence(e["id"]) for e in snapshot["evidence"]]


def build_artifacts(snapshot: dict, request: dict) -> list[ProposedArtifact]:
    """The fixed template outputs for one Preparation Request. Structure errors propagate (Engine3ValidationError)."""
    channel = PreparationChannel(request["channel"])
    goal = PreparationGoal(request["goal"])
    artifacts = []

    summary = [
        _seg("S1", f'The buyer previously said: "{snapshot["buyer_statement"]}"',
             ClaimType.PRIOR_BUYER_STATEMENT_REFERENCE, ClaimSemanticClass.CONDITION_REFERENCE, [G_STATEMENT],
             ReferenceUseMode.INTERNAL),
        _seg("S2", f"A human reviewer recorded human-verified correspondence on {snapshot['reviewed_on']} using the "
                   "recorded evidence below.",
             ClaimType.FACTUAL_ASSERTION, ClaimSemanticClass.OTHER_FACT,
             [G_CORRESPONDENCE] + [g_evidence(e["id"]) for e in snapshot["evidence"]], ReferenceUseMode.INTERNAL,
             ClaimField.OTHER),
    ]
    for n, evidence in enumerate(snapshot["evidence"], start=1):
        summary.append(_seg(f"S2-E{n}", f'Evidence ({evidence["source_type"]}, {evidence["observed_on"]}): '
                                        f'"{evidence["text"]}"',
                            ClaimType.FACTUAL_ASSERTION, ClaimSemanticClass.EXTERNAL_EVENT_FACT,
                            [g_evidence(evidence["id"])], ReferenceUseMode.INTERNAL, ClaimField.OTHER))
    for n, measurement in enumerate(snapshot["measurements"], start=1):
        value = measurement["exact_value"]
        summary.append(_seg(f"S2-M{n}", f"The current usable derived measurement is {value}.",
                            ClaimType.FACTUAL_ASSERTION, ClaimSemanticClass.EXTERNAL_EVENT_FACT,
                            [g_measurement(measurement["id"])], ReferenceUseMode.INTERNAL, ClaimField.EVENT_MAGNITUDE,
                            declared=value))
    summary += [
        _seg("S3", INTERNAL_SUMMARY_BOUNDARY_STATEMENT, ClaimType.FACTUAL_ASSERTION, ClaimSemanticClass.OTHER_FACT,
             [G_CORRESPONDENCE], ReferenceUseMode.INTERNAL, ClaimField.OTHER),
        _seg("S4", "The next conversation should test whether the earlier condition is still relevant and whether "
                   "evaluation has actually reopened.", ClaimType.COURTESY_OR_TRANSITION, ClaimSemanticClass.COURTESY),
    ]
    artifacts.append(_artifact("WHY-NOW", ArtifactType.INTERNAL_WHY_NOW_SUMMARY, summary))

    relevance = _seg("S2", "I wanted to reconnect because that condition may now be relevant.",
                     ClaimType.FACTUAL_ASSERTION, ClaimSemanticClass.EXTERNAL_EVENT_FACT, _relevance_refs(snapshot),
                     ReferenceUseMode.PARAPHRASE, ClaimField.OTHER, specificity=10)
    if channel in (PreparationChannel.EMAIL, PreparationChannel.EMAIL_AND_CALL):
        artifacts.append(_artifact("EMAIL", ArtifactType.FOLLOW_UP_EMAIL, [
            _prior_reference("S1", snapshot, request),
            relevance,
            _seg("S3", CLOSING_QUESTIONS[goal], ClaimType.QUESTION, ClaimSemanticClass.NEUTRAL_QUESTION),
        ]))
    if channel in (PreparationChannel.CALL, PreparationChannel.EMAIL_AND_CALL):
        artifacts.append(_artifact("CALL", ArtifactType.CALL_OPENING, [
            _seg("S0", "Thanks for taking a few minutes.", ClaimType.COURTESY_OR_TRANSITION,
                 ClaimSemanticClass.COURTESY),
            _prior_reference("S1", snapshot, request),
            _seg("S2", CLOSING_QUESTIONS[goal], ClaimType.QUESTION, ClaimSemanticClass.NEUTRAL_QUESTION),
        ]))
    artifacts.append(_artifact("QUESTIONS", ArtifactType.DISCOVERY_QUESTION_SET, [
        _seg("Q1", "Is the condition you described when we last spoke still relevant?", ClaimType.QUESTION,
             ClaimSemanticClass.CONDITION_REFERENCE, [G_STATEMENT], ReferenceUseMode.PARAPHRASE, premise=True),
        _seg("Q2", "What, if anything, has changed since we last spoke?", ClaimType.QUESTION,
             ClaimSemanticClass.NEUTRAL_QUESTION),
        _seg("Q3", "Has any review of alternatives actually started, or is it still only a possibility?",
             ClaimType.QUESTION, ClaimSemanticClass.NEUTRAL_QUESTION),
        _seg("Q4", "Would it be useful to look at alternatives, or is that premature?", ClaimType.QUESTION,
             ClaimSemanticClass.NEUTRAL_QUESTION),
    ]))
    return artifacts
