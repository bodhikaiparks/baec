"""Stage F: the AI correspondence proposal boundary. Offline only; no model, network, or provider call."""

import copy
import hashlib
import inspect
import json
import re
from dataclasses import FrozenInstanceError, fields, replace
from datetime import datetime

import pytest

from baec_app.domain.enums import ProvenanceCategory
from baec_app.engine2 import ai
from baec_app.engine2.ai import (
    OUTPUT_SCHEMA,
    PROMPT_VERSION,
    SYSTEM_PROMPT,
    CorrespondenceAIInput,
    CorrespondenceAIProposal,
    ModelResponse,
    ProposalRejected,
    ProposedFinding,
    build_correspondence_ai_input,
    canonical_json,
    propose_correspondence,
    validate_proposal_output,
)
from baec_app.engine2.classification import CrossCuttingCheck, DimensionAssessment, HumanCorrespondenceReview, classify_correspondence
from baec_app.engine2.domain import CorrespondenceCandidate, CorrespondenceDimension, DimensionFinding, SignalCandidate
from baec_app.engine2.errors import Engine2ValidationError
from tests.engine2 import builders as b
from tests.engine2.ai_replay import ReplayModel, ai_case, ai_input_for, condition_input, faithful_output, overreaching_output
from tests.engine2.corpus_adapter import REPO, load_corpus, run_case

CORPUS = load_corpus()
CASES = {c["case_id"]: c for c in CORPUS["cases"]}
WHEN = datetime.fromisoformat("2027-06-02T00:00:00+00:00")


def _run(case_id):
    return run_case(CORPUS, CASES[case_id])


def _input(case_id):
    return ai_input_for(CORPUS, _run(case_id))


def _validate(ai_input, raw):
    return validate_proposal_output(ModelResponse(raw, "replay", "fixture-replay/v1"), ai_input,
                                    artifact_id="AIP-T", created_at=WHEN)


def _good(case_id="HBR-CORR-003"):
    ai_input, to_neutral = ai_case(CORPUS, _run(case_id))
    return ai_input, json.loads(faithful_output(ai_input, CASES[case_id], to_neutral))


def _ids(case_id):
    return ai_case(CORPUS, _run(case_id))[1]


# --- input snapshot --------------------------------------------------------------------------


def test_input_snapshot_is_deterministic_and_digest_is_sha256_of_canonical_utf8():
    a, b_ = _input("HBR-CORR-020"), _input("HBR-CORR-020")
    assert a == b_ and a.input_digest == b_.input_digest
    assert a.input_digest == hashlib.sha256(a.canonical_json.encode("utf-8")).hexdigest()
    assert a.canonical_json == json.dumps(json.loads(a.canonical_json), sort_keys=True, separators=(",", ":"),
                                          ensure_ascii=False, allow_nan=False)
    assert (a.monitoring_plan_id, a.plan_version) == ("MP-HBR-001", 1)


def _keys(value, out):
    if isinstance(value, dict):
        for k, v in value.items():
            out.add(k)
            _keys(v, out)
    elif isinstance(value, list):
        for v in value:
            _keys(v, out)
    return out


def test_input_snapshot_carries_no_oracle_or_human_decision():
    for case_id in ("HBR-CORR-003", "HBR-CORR-055", "HBR-CORR-024"):
        ai_input = _input(case_id)
        keys = _keys(ai_input.payload, set())
        assert not keys & {"expected_correspondence_outcome", "expected_dimension_findings", "findings", "finding",
                           "sufficiency", "outcome", "baec_revalidation_required", "case_id", "category", "reason"}
        text = ai_input.canonical_json
        for leaked in ("HUMAN_VERIFIED_CORRESPONDENCE", "NO_CORRESPONDENCE", "POSSIBLE_CORRESPONDENCE", "HBR-CORR"):
            assert leaked not in text
        assert ai_input.payload["monitoring_plan"]["plan_version"] == 1


def test_input_snapshot_is_tamper_evident_and_immutable():
    ai_input = _input("HBR-CORR-003")
    with pytest.raises(FrozenInstanceError):
        ai_input.input_digest = "0" * 64
    with pytest.raises(Engine2ValidationError):
        replace(ai_input, canonical_json=ai_input.canonical_json.replace("12%", "13%"))
    with pytest.raises(Engine2ValidationError):
        replace(ai_input, plan_version=2)


def test_input_fails_before_any_model_call_on_cross_version_or_foreign_evidence():
    run = _run("HBR-CORR-003")
    obs = run.observations[0]
    v2_obs = replace(obs, plan_version=2)
    v2_candidate = CorrespondenceCandidate("CC-V2", "BAEC-HBR-001", "MP-HBR-001", 2,
                                           (SignalCandidate("SC-V2", "MP-HBR-001", 2, (v2_obs,), "R"),))
    with pytest.raises(Engine2ValidationError, match="never mix"):
        build_correspondence_ai_input(condition_input(CORPUS), v2_candidate, run.ledger)
    with pytest.raises(Engine2ValidationError):
        build_correspondence_ai_input(replace(condition_input(CORPUS), baec_id="BAEC-OTHER"), run.candidate, run.ledger)
    with pytest.raises(Engine2ValidationError):
        build_correspondence_ai_input(replace(condition_input(CORPUS), condition_reference="EXC-OTHER"), run.candidate, run.ledger)


def test_blocked_cases_never_produce_an_ai_input():
    assert _run("HBR-CORR-040").candidate is None and _run("HBR-CORR-041").candidate is None
    run_057 = _run("HBR-CORR-057")
    assert run_057.activation_error is not None and run_057.ledger is None


# --- proposal artifact and vocabulary -------------------------------------------------------------


def test_proposal_is_a_distinct_ai_inference_type():
    ai_input, raw = _good()
    proposal = _validate(ai_input, json.dumps(raw))
    assert proposal.origin is ProvenanceCategory.AI_INFERENCE
    assert proposal.prompt_version == PROMPT_VERSION and proposal.input_digest == ai_input.input_digest
    for authoritative in (DimensionAssessment, CrossCuttingCheck, HumanCorrespondenceReview):
        assert not isinstance(proposal, authoritative)
        assert not any(isinstance(p, authoritative) for p in proposal.dimension_proposals)
    assert {p.value for p in ProposedFinding}.isdisjoint({f.value for f in DimensionFinding})
    assert ProposedFinding is not DimensionFinding
    names = {f.name for f in fields(CorrespondenceAIProposal)}
    assert not names & {"outcome", "correspondence_outcome", "sufficiency", "findings", "baec_revalidation_required"}
    with pytest.raises(Engine2ValidationError):
        classify_correspondence(proposal)
    with pytest.raises(Engine2ValidationError):
        replace(proposal, origin=ProvenanceCategory.BUYER_FACT)


def test_propose_correspondence_uses_the_model_protocol_offline():
    ai_input, raw = _good()
    model = ReplayModel({ai_input.input_digest: json.dumps(raw)})
    proposal = propose_correspondence(model, ai_input, artifact_id="AIP-1", created_at=WHEN)
    assert model.calls == [ai_input.input_digest]
    assert [p.dimension for p in proposal.dimension_proposals] == list(CorrespondenceDimension)


# --- failure modes: every defect fails closed --------------------------------------------------------

def _mutate(fn, case_id="HBR-CORR-003"):
    ai_input, raw = _good(case_id)
    data = copy.deepcopy(raw)
    result = fn(data)
    return ai_input, json.dumps(data) if result is None else result


def _dim(data, label):
    return next(p for p in data["dimension_proposals"] if p["dimension"] == label)


FAILURES = {
    "malformed_json": lambda d: "{not json",
    "trailing_prose": lambda d: json.dumps(d) + " Here is my reasoning.",
    "nan_value": lambda d: json.dumps(d).replace('"plan_version": 1', '"plan_version": NaN'),
    "duplicate_key": lambda d: json.dumps(d).replace('"plan_version": 1', '"plan_version": 1, "plan_version": 1'),
    "unknown_dimension": lambda d: _dim(d, "Entity match").update(dimension="Price match"),
    "duplicate_dimension": lambda d: _dim(d, "Unit match").update(dimension="Entity match"),
    "missing_dimension": lambda d: d["dimension_proposals"].pop(),
    "authoritative_vocabulary": lambda d: _dim(d, "Entity match").update(proposal="SUPPORTED"),
    "unknown_evidence_id": lambda d: _dim(d, "Entity match")["evidence"][0].update(observation_id="OBS-UNKNOWN"),
    "wrong_plan_version": lambda d: d.update(plan_version=2),
    "bool_plan_version": lambda d: d.update(plan_version=True),
    "wrong_input_digest": lambda d: d.update(input_digest="0" * 64),
    "fabricated_excerpt": lambda d: _dim(d, "Entity match")["evidence"][0].update(excerpt="NorthStar guarantees Harbor will switch."),
    "paraphrased_excerpt": lambda d: _dim(d, "Entity match")["evidence"][0].update(excerpt="pricing will go up 12 percent"),
    "required_dimension_not_applicable": lambda d: _dim(d, "Timing match").update(proposal="PROPOSED_NOT_APPLICABLE", evidence=[]),
    "authoritative_outcome_field": lambda d: d.update(correspondence_outcome="HUMAN_VERIFIED_CORRESPONDENCE"),
    "purchase_probability_field": lambda d: d.update(purchase_probability=0.82),
    "lead_score_field": lambda d: d.update(lead_score=91),
    "nested_extra_field": lambda d: _dim(d, "Entity match").update(certainty="high"),
    "intent_claim": lambda d: _dim(d, "Entity match").update(rationale="Harbor intends to purchase from us after this."),
    "evaluation_claim": lambda d: _dim(d, "Threshold match").update(rationale="The buyer is evaluating alternatives now."),
    "contact_claim": lambda d: d["unresolved_questions"].append("Should the seller reach out today?"),
    "opportunity_claim": lambda d: _dim(d, "Timing match").update(rationale="This is now an active opportunity."),
    "decisive_without_evidence": lambda d: _dim(d, "Entity match").update(evidence=[]),
    "altered_acknowledgement": lambda d: d.update(prohibited_inference_acknowledgement="ok"),
    "rationale_too_long": lambda d: _dim(d, "Entity match").update(rationale="x" * 601),
    "unsupported_citation_kind": lambda d: _dim(d, "Entity match")["evidence"].append({"kind": "web", "url": "x"}),
    "plan_reference_only_support": lambda d: _dim(d, "Relationship match").update(
        evidence=[{"kind": "plan_reference", "reference_id": "PLAN-FACT-PRODUCT-SCOPE"}]),
    "basis_on_decisive": lambda d: _dim(d, "Entity match").update(unresolved_basis="AMBIGUOUS_EVIDENCE"),
    "unresolved_without_basis": lambda d: _dim(d, "Entity match").update(proposal="PROPOSED_UNRESOLVED"),
    "unknown_basis": lambda d: _dim(d, "Entity match").update(proposal="PROPOSED_UNRESOLVED", unresolved_basis="HUNCH"),
    "missing_basis_with_citation": lambda d: _dim(d, "Entity match").update(
        proposal="PROPOSED_UNRESOLVED", unresolved_basis="MISSING_EVIDENCE"),
    "ambiguous_basis_without_citation": lambda d: _dim(d, "Entity match").update(
        proposal="PROPOSED_UNRESOLVED", unresolved_basis="AMBIGUOUS_EVIDENCE", evidence=[]),
    "basis_field_missing": lambda d: _dim(d, "Entity match").pop("unresolved_basis"),
    "plan_dimension_on_decisive": lambda d: _dim(d, "Entity match")["evidence"].append(
        {"kind": "plan_dimension", "dimension": "Entity match"}),
}


@pytest.mark.parametrize("name", sorted(FAILURES))
def test_defective_output_is_rejected_and_creates_nothing(name):
    ai_input, raw = _mutate(FAILURES[name])
    with pytest.raises(ProposalRejected):
        _validate(ai_input, raw)


def test_superseded_evidence_cannot_be_current_support():
    ai_input, raw = _good("HBR-CORR-008")  # the 12% notice is superseded by the 7% correction
    ids = _ids("HBR-CORR-008")
    old, new = ids["OBS-008-01"], ids["OBS-008-02"]
    content = {o["observation_id"]: o["content"] for o in ai_input.payload["observations"]}
    _dim(raw, "Threshold match").update(proposal="PROPOSED_SUPPORTED", evidence=[
        {"kind": "observation", "observation_id": old, "excerpt": content[old][:60]},
        {"kind": "observation", "observation_id": new, "excerpt": content[new][:60]}])
    with pytest.raises(ProposalRejected, match="cannot support a decisive"):
        _validate(ai_input, json.dumps(raw))
    _dim(raw, "Threshold match").update(proposal="PROPOSED_UNRESOLVED", unresolved_basis="CONFLICTING_EVIDENCE",
                                        rationale="Two notices differ; the earlier is superseded.")
    assert _validate(ai_input, json.dumps(raw)) is not None  # an unresolved proposal may expose both sides


def test_measurement_with_unusable_input_cannot_be_current_support():
    ai_input, raw = _good("HBR-CORR-024")  # the first measurement rests on a corrected baseline
    ids = _ids("HBR-CORR-024")
    _dim(raw, "Threshold match").update(proposal="PROPOSED_SUPPORTED", evidence=[
        {"kind": "measurement", "measurement_id": ids["DM-024-01"], "observation_ids": [ids["OBS-024-01"], ids["OBS-024-03"]]}])
    with pytest.raises(ProposalRejected, match="decisive"):
        _validate(ai_input, json.dumps(raw))
    _dim(raw, "Threshold match").update(evidence=[
        {"kind": "measurement", "measurement_id": ids["DM-024-02"], "observation_ids": [ids["OBS-024-03"]]}])
    with pytest.raises(ProposalRejected, match="exactly the input observations"):
        _validate(ai_input, json.dumps(raw))


def test_conflicting_sources_can_be_exposed_as_unresolved_with_both_sides():
    ai_input, raw = _good("HBR-CORR-048")
    proposal = _validate(ai_input, json.dumps(raw))
    threshold = proposal.dimension_proposals[list(CorrespondenceDimension).index(CorrespondenceDimension.THRESHOLD_MATCH)]
    assert threshold.proposal is ProposedFinding.PROPOSED_UNRESOLVED
    conflict = next(c for c in proposal.cross_cutting_observations if c.check.label == "Contradictory evidence")
    ids = _ids("HBR-CORR-048")
    assert {c.reference_id for c in conflict.evidence} == {ids["OBS-048-01"], ids["OBS-048-02"]}
    assert threshold.unresolved_basis is ai.UnresolvedBasis.CONFLICTING_EVIDENCE
    assert {c.reference_id for c in threshold.evidence} == {ids["OBS-048-01"], ids["OBS-048-02"]}


def test_not_applicable_only_where_the_plan_already_says_so():
    from baec_app.engine2.domain import ALL_DIMENSIONS
    from tests.engine2.ai_replay import _envelope
    plan = b.plan(required_dimensions=ALL_DIMENSIONS - {CorrespondenceDimension.UNIT_MATCH},
                  not_applicable_dimensions=frozenset({CorrespondenceDimension.UNIT_MATCH}))
    led = b.ledger(b.active(plan))
    review = b.review(led, findings=b.findings(UNIT_MATCH=(DimensionFinding.NOT_APPLICABLE, ())))
    cond = ai.BaecConditionInput(b.BAEC_ID, plan.condition_reference, "If prices rise more than 10% at renewal, we would look.")
    ai_input = build_correspondence_ai_input(cond, review.candidate, led)
    content = ai_input.payload["observations"][0]["content"]
    cite = [{"kind": "observation", "observation_id": "OBS-T-01", "excerpt": content[:20]}]

    na_basis = [{"kind": "plan_dimension", "dimension": "Unit match"}]

    def output(unit_proposal, unit_evidence=na_basis, unit_basis=None):
        return _envelope(ai_input, [
            {"dimension": d.label,
             "proposal": unit_proposal if d is CorrespondenceDimension.UNIT_MATCH else "PROPOSED_UNRESOLVED",
             "unresolved_basis": unit_basis if d is CorrespondenceDimension.UNIT_MATCH else "AMBIGUOUS_EVIDENCE",
             "evidence": unit_evidence if d is CorrespondenceDimension.UNIT_MATCH else cite, "rationale": "Concise rationale."}
            for d in CorrespondenceDimension], [], [])
    assert _validate(ai_input, output("PROPOSED_NOT_APPLICABLE")) is not None
    with pytest.raises(ProposalRejected, match="not applicable under the Monitoring Plan"):
        _validate(ai_input, output("PROPOSED_UNRESOLVED", [], "MISSING_EVIDENCE"))
    with pytest.raises(ProposalRejected, match="exactly its own plan_dimension"):  # no plan basis cited
        _validate(ai_input, output("PROPOSED_NOT_APPLICABLE", []))
    with pytest.raises(ProposalRejected, match="requires it"):  # citing a required dimension as not applicable
        _validate(ai_input, output("PROPOSED_NOT_APPLICABLE", [{"kind": "plan_dimension", "dimension": "Timing match"}]))


# --- prompt contract and static authority audit ---------------------------------------------------


def test_prompt_contract():
    for required in (ai.DOCTRINE_NO_PREDICTION, ai.DOCTRINE_NARROWS, "You propose, you do not decide",
                     "Use only the supplied evidence", "Do not predict purchase", "Do not recommend contact or outreach",
                     "Do not calculate", "PROPOSED_UNRESOLVED", "Do not output hidden reasoning or chain-of-thought",
                     "Never express certainty or likelihood as a percentage", ai.PROHIBITED_INFERENCE_ACKNOWLEDGEMENT,
                     "not even to deny them", "unresolved_basis", "only MISSING_EVIDENCE may cite no",
                     "Exact quotations of source text inside evidence excerpts are not your words",
                     "plan_dimension citation",
                     PROMPT_VERSION):
        assert required in SYSTEM_PROMPT, required
    for forbidden in ("step by step", "think aloud", "show your reasoning", "explain your thinking"):
        assert forbidden not in SYSTEM_PROMPT.lower()
    assert PROMPT_VERSION == "baec-e2-correspondence-proposal-prompt/v1"
    props = OUTPUT_SCHEMA["properties"]
    assert OUTPUT_SCHEMA["additionalProperties"] is False and "correspondence_outcome" not in props
    assert props["dimension_proposals"]["minItems"] == props["dimension_proposals"]["maxItems"] == 11


def test_ai_module_has_no_authority_path():
    source = (REPO / "baec_app/engine2/ai.py").read_text(encoding="utf-8")
    code = re.sub(r'"""[\s\S]*?"""', "", source)
    for forbidden in ("HumanCorrespondenceReview", "DimensionAssessment(", "CrossCuttingCheck(", "classify_correspondence",
                      "persistence", "record_", "AccountState", "import anthropic", "requests", "urllib", "socket",
                      "datetime.now", "baec_app.ai", "baec_app.data"):
        assert forbidden not in code, forbidden
    names = [n for n, _ in inspect.getmembers(ai, inspect.isfunction)]
    assert not [n for n in names if n.startswith(("accept", "to_review", "convert", "apply"))]


# --- generated-text authority boundary ---------------------------------------------------------------

ACCEPTABLE_RATIONALE = [
    "The renewal notice states a 12% increase.",
    "The effective date in the notice is six months after the recorded renewal date.",
    "The two current notices state different pricing changes.",
    "No accepted evidence establishes the renewal date.",
]
REJECTED_GENERATED = [
    # adversarial constructions that defeated the earlier negation heuristic
    "Not only does the notice apply, the buyer is evaluating.",
    "No issue exists with provenance; the buyer is evaluating alternatives.",
    "The evidence is not stale, and the buyer intends to switch.",
    "I cannot confirm timing, but the seller should contact the buyer.",
    "The source is not ambiguous; this account is now an opportunity.",
    # disclaimers: owned by the fixed acknowledgement, so refused in generated text
    "The buyer is not evaluating.",
    "No purchase intent can be inferred from this evidence.",
    "This proposal does not provide permission to contact the buyer.",
    "This is not yet an opportunity.",
    # affirmative claims
    "The buyer is evaluating alternatives.",
    "The buyer intends to purchase.",
    "The seller should contact the buyer.",
    "Purchase probability is 80%.",
    "Lead score: 91.",
    "Harbor is in market for procedure packs.",
    "Move the account state to active.",
    "We recommend a call next week.",
]


@pytest.mark.parametrize("text", ACCEPTABLE_RATIONALE)
def test_evidence_scoped_generated_text_is_accepted(text):
    assert ai.generated_text_violation(text) is None
    ai_input, raw = _good()
    _dim(raw, "Entity match").update(rationale=text)
    raw["unresolved_questions"] = [text]
    assert _validate(ai_input, json.dumps(raw)) is not None


@pytest.mark.parametrize("text", REJECTED_GENERATED)
def test_buyer_state_or_action_topics_are_refused_in_generated_text(text):
    assert ai.generated_text_violation(text) is not None
    for place in ("rationale", "question", "concern"):
        ai_input, raw = _good("HBR-CORR-048")
        if place == "rationale":
            _dim(raw, "Entity match").update(rationale=text)
        elif place == "question":
            raw["unresolved_questions"] = [text]
        else:
            raw["cross_cutting_observations"][0]["concern"] = text
        with pytest.raises(ProposalRejected, match="AI-generated text"):
            _validate(ai_input, json.dumps(raw))


def test_no_negation_exception_remains():
    assert not hasattr(ai, "prohibited_assertion")
    source = (REPO / "baec_app/engine2/ai.py").read_text(encoding="utf-8")
    assert "_NEGATION" not in source and "negation exception" in source  # documented as absent


# --- exact source quotes are never treated as AI assertions ------------------------------------------------

BUYER_EMAIL = ("Buyer email (synthetic): We'd evaluate other options. We may switch suppliers. "
               "We aren't looking at alternatives right now.")


def _quote_input():
    from tests.engine2.ai_replay import _envelope
    active = b.active()
    obs = b.observe(active, "OBS-Q", b.item("PKT-Q", content=BUYER_EMAIL))
    review = b.review(b.ledger(active, [obs]), findings=b.findings(ev=("OBS-Q",)))
    cond = ai.BaecConditionInput(b.BAEC_ID, b.plan().condition_reference, "If pricing rises more than 10%, we'd evaluate other options.")
    return build_correspondence_ai_input(cond, review.candidate, review.ledger), _envelope


@pytest.mark.parametrize("quote", ["We'd evaluate other options.", "We may switch suppliers.",
                                   "We aren't looking at alternatives right now."])
def test_exact_source_quotes_with_topic_words_are_accepted(quote):
    ai_input, envelope = _quote_input()
    assert "evaluate other options" in ai_input.payload["baec"]["buyer_exact_statement"]  # condition text kept verbatim
    cite = [{"kind": "observation", "observation_id": "OBS-Q", "excerpt": quote}]
    proposals = [{"dimension": d.label, "proposal": "PROPOSED_UNRESOLVED", "unresolved_basis": "AMBIGUOUS_EVIDENCE",
                  "evidence": cite, "rationale": "The cited email does not state a pricing change."}
                 for d in CorrespondenceDimension]
    proposal = _validate(ai_input, envelope(ai_input, proposals, [], []))
    assert proposal.dimension_proposals[0].evidence[0].excerpt == quote
    altered = [dict(p, evidence=[dict(cite[0], excerpt=quote.replace("We", "They"))]) for p in proposals]
    with pytest.raises(ProposalRejected, match="exact quotation"):
        _validate(ai_input, envelope(ai_input, altered, [], []))


# --- the fixed acknowledgement --------------------------------------------------------------------------------


def test_acknowledgement_must_be_exact_and_present():
    ai_input, raw = _good()
    assert _validate(ai_input, json.dumps(raw)).prohibited_inference_acknowledgement == ai.PROHIBITED_INFERENCE_ACKNOWLEDGEMENT
    for altered in (ai.PROHIBITED_INFERENCE_ACKNOWLEDGEMENT.lower(), ai.PROHIBITED_INFERENCE_ACKNOWLEDGEMENT + " ", ""):
        bad = dict(raw, prohibited_inference_acknowledgement=altered)
        with pytest.raises(ProposalRejected):
            _validate(ai_input, json.dumps(bad))
    missing = {k: v for k, v in raw.items() if k != "prohibited_inference_acknowledgement"}
    with pytest.raises(ProposalRejected):
        _validate(ai_input, json.dumps(missing))


def test_generated_rationale_cannot_stand_in_for_the_acknowledgement():
    ai_input, raw = _good()
    _dim(raw, "Entity match").update(rationale=ai.PROHIBITED_INFERENCE_ACKNOWLEDGEMENT[:600])
    with pytest.raises(ProposalRejected, match="AI-generated text"):
        _validate(ai_input, json.dumps(raw))
    moved = dict(raw, prohibited_inference_acknowledgement="See rationale.")
    with pytest.raises(ProposalRejected):
        _validate(ai_input, json.dumps(moved))


def test_unresolved_basis_is_a_closed_reason_not_a_rating():
    assert [b_.value for b_ in ai.UnresolvedBasis] == ["MISSING_EVIDENCE", "AMBIGUOUS_EVIDENCE", "CONFLICTING_EVIDENCE",
                                                       "INSUFFICIENT_PROVENANCE", "OTHER_EVIDENCE_LIMITATION"]
    ai_input, raw = _good("HBR-CORR-002")  # relationship unresolved because Harbor-specific evidence is absent
    proposal = _validate(ai_input, json.dumps(raw))
    rel = proposal.dimension_proposals[1]
    assert rel.unresolved_basis is ai.UnresolvedBasis.MISSING_EVIDENCE and rel.evidence == ()
