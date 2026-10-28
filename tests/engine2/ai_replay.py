"""Test-only replay models and offline evaluation for the Stage F AI proposal boundary.

Nothing here calls a model. Replay models return predetermined JSON keyed by the
input digest; they never see case IDs, categories, or oracle fields.

The replay outputs are built from the locked Stage B corpus so the boundary can be
exercised end to end:
* faithful_output: pipeline validation using predetermined outputs that mirror the
  corpus's human findings. It shows valid proposals pass validation. It is not a
  measure of AI accuracy, model accuracy, or model performance;
* overreaching_output: a deliberately bad model that proposes everything SUPPORTED.
  It tests that the evaluation metrics expose false decisive proposals.
Oracle fields are read here only to construct replays and to score them afterwards;
they never enter the AI input.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace

from baec_app.engine2.ai import (
    OUTPUT_SCHEMA_VERSION,
    PROHIBITED_INFERENCE_ACKNOWLEDGEMENT,
    BaecConditionInput,
    CorrespondenceAIInput,
    ModelResponse,
    ProposedFinding,
    build_correspondence_ai_input,
)
from baec_app.engine2.classification import EvidenceLedger
from baec_app.engine2.domain import (CorrespondenceCandidate, CorrespondenceDimension, RejectedSourceItem,
                                     SignalCandidate, Supersession)
from baec_app.engine2.measurement import DerivedMeasurement, MeasurementInput

PROPOSAL_FOR = {"SUPPORTED": "PROPOSED_SUPPORTED", "CONTRADICTED": "PROPOSED_CONTRADICTED",
                "UNRESOLVED": "PROPOSED_UNRESOLVED", "NOT_APPLICABLE": "PROPOSED_NOT_APPLICABLE"}
DECISIVE = {"PROPOSED_SUPPORTED", "PROPOSED_CONTRADICTED"}


class ReplayModel:
    """Returns the recorded output for an input digest. Unknown digests are an error, never a guess."""

    def __init__(self, outputs: dict[str, str], provider: str = "replay", model: str = "fixture-replay/v1"):
        self.outputs, self.provider, self.model = outputs, provider, model
        self.calls: list[str] = []

    def propose(self, ai_input: CorrespondenceAIInput, *, system_prompt: str, output_schema: dict) -> ModelResponse:
        self.calls.append(ai_input.input_digest)
        return ModelResponse(self.outputs[ai_input.input_digest], self.provider, self.model)


def condition_input(corpus: dict) -> BaecConditionInput:
    fixture = corpus["engine1_fixture"]
    return BaecConditionInput(
        baec_id=fixture["baec_id"],
        condition_reference=corpus["monitoring_plan"]["authoritative_condition_reference"]["buyer_exact_statement"],
        buyer_exact_statement=fixture["buyer_exact_statement"]["text"],
        normalized_condition=fixture["human_reviewed_normalized_condition"]["text"],
    )


@dataclass(frozen=True)
class NeutralCase:
    """A case's evidence re-expressed with neutral, model-visible identifiers (fixture hygiene only).

    to_neutral maps corpus identifiers to neutral ones, outside the AI snapshot, so results
    can still be compared with the corpus. Production AI code never sees this mapping.
    """

    ledger: EvidenceLedger
    candidate: CorrespondenceCandidate
    to_neutral: dict


def neutralize(run) -> NeutralCase:
    led = run.ledger
    obs_map = {o.observation_id: f"OBS-{n}" for n, o in enumerate(led.observations, 1)}
    event_map: dict[str, str] = {}
    for o in led.observations:
        event_map.setdefault(o.evidence_event_id, f"EVENT-{len(event_map) + 1}")
    observations = {o.observation_id: replace(o, observation_id=obs_map[o.observation_id],
                                              evidence_event_id=event_map[o.evidence_event_id],
                                              source_locator=f"{obs_map[o.observation_id]}: full text")
                    for o in led.observations}
    meas_map = {m.measurement_id: f"MEAS-{n}" for n, m in enumerate(led.measurements, 1)}
    measurements = tuple(
        DerivedMeasurement(measurement_id=meas_map[m.measurement_id], transformation=m.transformation,
                           inputs=tuple(MeasurementInput(observations[i.observation_id], i.quantity, i.value, i.unit)
                                        for i in m.inputs),
                           output_value=m.output_value, calculated_at=m.calculated_at)
        for m in led.measurements)
    item_map = {r.item_id: f"ITEM-{n}" for n, r in enumerate(led.rejected_items, 1)}

    def scrub(text: str) -> str:
        for old, new in item_map.items():
            text = text.replace(old, new)
        return text

    refusals = tuple(RejectedSourceItem(item_map[r.item_id], r.monitoring_plan_id, r.plan_version, scrub(r.reason))
                     for r in led.rejected_items)
    supersessions = tuple(Supersession(obs_map[s.superseded_observation_id], obs_map[s.superseding_observation_id],
                                       s.basis, s.retraction) for s in led.supersessions)
    ledger = EvidenceLedger(led.active_plan, tuple(observations[o.observation_id] for o in led.observations),
                            supersessions, measurements, refusals)
    cand = run.candidate
    signals = tuple(SignalCandidate(f"SIG-{n}", s.monitoring_plan_id, s.plan_version,
                                    tuple(observations[o.observation_id] for o in s.observations), "REVIEWER",
                                    s.possibly_relevant_dimensions)
                    for n, s in enumerate(cand.signal_candidates, 1))
    candidate = CorrespondenceCandidate("CANDIDATE-1", cand.baec_id, cand.monitoring_plan_id, cand.plan_version, signals)
    return NeutralCase(ledger, candidate, {**obs_map, **meas_map, **item_map})


def ai_case(corpus: dict, run) -> tuple[CorrespondenceAIInput, dict]:
    neutral = neutralize(run)
    return build_correspondence_ai_input(condition_input(corpus), neutral.candidate, neutral.ledger), neutral.to_neutral


def ai_input_for(corpus: dict, run) -> CorrespondenceAIInput:
    return ai_case(corpus, run)[0]


def _excerpt(content: str) -> str:
    return content[:80]


def _citations(refs, ai_input: CorrespondenceAIInput, to_neutral: dict, *, allow_refusal: bool) -> list[dict]:
    payload = ai_input.payload
    content = {o["observation_id"]: o["content"] for o in payload["observations"]}
    measures = {m["measurement_id"]: [i["observation_id"] for i in m["inputs"]] for m in payload["derived_measurements"]}
    plan_refs = set(payload["monitoring_plan"]["plan_fact_ids"]) | set(payload["monitoring_plan"]["plan_rule_ids"])
    refusals = {r["item_id"] for r in payload["source_refusals"]}
    out = []
    for ref in (to_neutral.get(r, r) for r in refs):
        if ref in content:
            out.append({"kind": "observation", "observation_id": ref, "excerpt": _excerpt(content[ref])})
        elif ref in measures:
            out.append({"kind": "measurement", "measurement_id": ref, "observation_ids": measures[ref]})
        elif ref in plan_refs:
            out.append({"kind": "plan_reference", "reference_id": ref})
        elif allow_refusal and ref in refusals:
            out.append({"kind": "refusal", "item_id": ref})
    return out


def _observed(citations: list[dict]) -> list[dict]:
    return [c for c in citations if c["kind"] in ("observation", "measurement")]


def _unresolved(label: str, finding: dict, case: dict, ai_input: CorrespondenceAIInput, to_neutral: dict):
    """Fixture rule for the basis and citations of an unresolved replay proposal (pipeline test only)."""
    checks = case["cross_cutting_checks"]
    own = _observed(_citations(finding["evidence"], ai_input, to_neutral, allow_refusal=False))
    if own:
        return "AMBIGUOUS_EVIDENCE", own
    for check, basis in (("Contradictory evidence", "CONFLICTING_EVIDENCE"), ("Provenance quality", "INSUFFICIENT_PROVENANCE")):
        if label in checks[check]["affected_dimensions"]:
            cited = _observed(_citations(checks[check]["evidence"], ai_input, to_neutral, allow_refusal=False))
            if cited:
                return basis, cited
    if {"imprecise_value", "semantic_ambiguity"} & set(case["coverage_tags"]):
        usable = [o for o in ai_input.payload["observations"] if o["usable_for_current_support"]]
        if usable:
            return "AMBIGUOUS_EVIDENCE", [{"kind": "observation", "observation_id": o["observation_id"],
                                          "excerpt": _excerpt(o["content"])} for o in usable]
    return "MISSING_EVIDENCE", []


def _envelope(ai_input: CorrespondenceAIInput, proposals, checks, questions) -> str:
    return json.dumps({
        "schema_version": OUTPUT_SCHEMA_VERSION, "monitoring_plan_id": ai_input.monitoring_plan_id,
        "plan_version": ai_input.plan_version, "baec_id": ai_input.baec_id, "candidate_id": ai_input.candidate_id,
        "input_digest": ai_input.input_digest, "dimension_proposals": proposals,
        "cross_cutting_observations": checks, "unresolved_questions": questions,
        "prohibited_inference_acknowledgement": PROHIBITED_INFERENCE_ACKNOWLEDGEMENT,
    })


def faithful_output(ai_input: CorrespondenceAIInput, case: dict, to_neutral: dict) -> str:
    proposals = []
    for label, f in case["expected_dimension_findings"].items():
        proposal = PROPOSAL_FOR[f["finding"]]
        if proposal == "PROPOSED_UNRESOLVED":
            basis, evidence = _unresolved(label, f, case, ai_input, to_neutral)
        elif proposal == "PROPOSED_NOT_APPLICABLE":
            basis, evidence = None, [{"kind": "plan_dimension", "dimension": label}]
        else:
            basis, evidence = None, _citations(f["evidence"], ai_input, to_neutral, allow_refusal=False)
        proposals.append({"dimension": label, "proposal": proposal, "unresolved_basis": basis,
                          "evidence": evidence, "rationale": f["reason"][:600]})
    checks = []
    for label, c in case["cross_cutting_checks"].items():
        if c["affected_dimensions"] or c["evidence"]:
            checks.append({"check": label, "concern": c["note"][:600],
                           "evidence": _citations(c["evidence"], ai_input, to_neutral, allow_refusal=True),
                           "affected_dimensions": c["affected_dimensions"]})
    missing = case["cross_cutting_checks"]["Missing evidence"]["note"]
    return _envelope(ai_input, proposals, checks, [] if missing == "None." else [missing[:600]])


def overreaching_output(ai_input: CorrespondenceAIInput) -> str:
    payload = ai_input.payload
    usable = next(o for o in payload["observations"] if o["usable_for_current_support"])
    cite = [{"kind": "observation", "observation_id": usable["observation_id"], "excerpt": _excerpt(usable["content"])}]
    proposals = [{"dimension": d.label, "proposal": "PROPOSED_SUPPORTED", "unresolved_basis": None, "evidence": cite,
                  "rationale": "The cited notice appears to satisfy this dimension."} for d in CorrespondenceDimension]
    return _envelope(ai_input, proposals, [], [])


@dataclass
class CaseMetrics:
    """Deterministic per-case metrics. No combined number; no purchase metric."""

    case_id: str
    category: str
    accepted: bool
    rejection: str | None = None
    agreement: int = 0
    false_decisive: int = 0
    unresolved_total: int = 0
    unresolved_preserved: int = 0
    evidence_refs_valid: bool = False
    excerpts_valid: bool = False
    plan_version_violations: int = 0
    checks_expected: set = field(default_factory=set)
    checks_identified: set = field(default_factory=set)


def evaluate(proposal, case: dict) -> CaseMetrics:
    m = CaseMetrics(case["case_id"], case["category"], accepted=True, evidence_refs_valid=True, excerpts_valid=True)
    expected = {CorrespondenceDimension.from_label(k): PROPOSAL_FOR[v["finding"]]
                for k, v in case["expected_dimension_findings"].items()}
    for p in proposal.dimension_proposals:
        want, got = expected[p.dimension], p.proposal.value
        m.agreement += want == got
        if got in DECISIVE and want != got:
            m.false_decisive += 1
        if want == ProposedFinding.PROPOSED_UNRESOLVED.value:
            m.unresolved_total += 1
            m.unresolved_preserved += got == want
    m.plan_version_violations = int((proposal.monitoring_plan_id, proposal.plan_version)
                                    != ("MP-HBR-001", case["monitoring_plan_context"]["plan_version"]))
    m.checks_expected = {k for k, c in case["cross_cutting_checks"].items()
                         if c["affected_dimensions"] and k != "Missing evidence"}
    m.checks_identified = {c.check.label for c in proposal.cross_cutting_observations} & m.checks_expected
    return m
