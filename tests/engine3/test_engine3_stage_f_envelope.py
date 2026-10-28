"""Stage F experimental envelope: strict structure, harness-bound provenance, fail closed."""

import hashlib
import json

import pytest

from baec_app.engine3.errors import Engine3ValidationError
from baec_app.engine3.proposal_contract import PRERECORDED_PROVENANCE, load_prerecorded_preparation_proposal
from experiments.engine3_stage_f.contexts import apply_context_delta
from experiments.engine3_stage_f.envelope import (
    OUTPUT_SCHEMA_VERSION,
    ExperimentalProposal,
    ExperimentArm,
    ExperimentProvenance,
    FixedProseArtifact,
    RawRunRecord,
    RunBinding,
    RunKind,
    StructuralRejection,
    StructuralRejectionError,
    capture_run,
    parse_experimental_output,
)
from tests.engine3 import stage_e_adapter
from tests.engine3 import stage_f_adapter as ad

DIGEST = "a" * 64
SEGMENT = {
    "segment_id": "S1", "text": "Has anything changed on your side since we last spoke?",
    "claim_type": "QUESTION", "semantic_class": "NEUTRAL_QUESTION", "grounding_refs": [],
    "reference_use_mode": "DIRECT", "source_identity_refs": [], "claim_field": "NONE", "declared_value": None,
    "specificity": 0, "question_has_premise": False, "source_instruction_ref": None, "prescribes_buyer_choice": False,
}


def _output(**changes):
    d = {
        "schema_version": OUTPUT_SCHEMA_VERSION, "requested_action": "NONE", "requests_score": False,
        "artifacts": [{"artifact_id": "A-1", "artifact_type": "FOLLOW_UP_EMAIL", "rendered_text": SEGMENT["text"],
                       "segments": [dict(SEGMENT)]}],
    }
    d.update(changes)
    return d


def _bytes(value) -> bytes:
    return json.dumps(value).encode("utf-8")


def _binding(arm=ExperimentArm.G, kind=RunKind.DRY_RUN, repeat=1, scenario="SCN-1"):
    return RunBinding("CONFIG-1", arm, scenario, repeat, kind, DIGEST)


def _rejection(raw: bytes, binding=None, fixed=None) -> StructuralRejection:
    with pytest.raises(StructuralRejectionError) as caught:
        parse_experimental_output(binding or _binding(), raw, fixed)
    return caught.value.rejection


def test_valid_output_parses_and_binds_to_the_harness_supplied_run():
    proposal = parse_experimental_output(_binding(), _bytes(_output()))
    assert proposal.binding.scenario_id == "SCN-1" and proposal.binding.bound_context_digest == DIGEST
    assert proposal.experiment_provenance is ExperimentProvenance.OFFLINE_HANDWRITTEN_DRY_RUN
    assert proposal.artifacts[0].audience.value == "BUYER_FACING"  # derived, never supplied


@pytest.mark.parametrize("kind,provenance", [
    (RunKind.DRY_RUN, ExperimentProvenance.OFFLINE_HANDWRITTEN_DRY_RUN),
    (RunKind.PILOT, ExperimentProvenance.LIVE_SYNTHETIC_EXPERIMENT),
    (RunKind.FULL_RUN, ExperimentProvenance.LIVE_SYNTHETIC_EXPERIMENT),
])
def test_provenance_is_derived_from_run_kind_and_is_never_prerecorded(kind, provenance):
    assert _binding(kind=kind).experiment_provenance is provenance
    assert PRERECORDED_PROVENANCE not in {p.value for p in ExperimentProvenance}


def test_live_output_cannot_be_relabelled_and_dry_run_input_cannot_claim_to_be_live():
    proposal = parse_experimental_output(_binding(kind=RunKind.FULL_RUN), _bytes(_output()))
    for provenance in ExperimentProvenance:
        if provenance is not ExperimentProvenance.LIVE_SYNTHETIC_EXPERIMENT:
            with pytest.raises(Engine3ValidationError):
                ExperimentalProposal(proposal.binding, provenance, proposal.artifacts, proposal.requested_action,
                                     proposal.requests_score)
    dry = parse_experimental_output(_binding(), _bytes(_output()))
    with pytest.raises(Engine3ValidationError):
        ExperimentalProposal(dry.binding, ExperimentProvenance.LIVE_SYNTHETIC_EXPERIMENT, dry.artifacts,
                             dry.requested_action, dry.requests_score)


@pytest.mark.parametrize("extra", [
    {"provenance": PRERECORDED_PROVENANCE}, {"experiment_provenance": "LIVE_SYNTHETIC_EXPERIMENT"},
    {"origin": "HUMAN_EDITED"}, {"scenario_id": "SCN-2"}, {"arm": "A"}, {"repeat_index": 3},
    {"bound_context_digest": "b" * 64}, {"experiment_configuration_id": "OTHER"}, {"proposal_id": "P"},
    {"disclosure_override": {}}, {"send_email": True}, {"account_state": "ACTIVE_OPPORTUNITY"}, {"score": 0.9},
])
def test_model_output_cannot_supply_binding_provenance_authority_or_actions(extra):
    assert _rejection(_bytes(_output(**extra))) is StructuralRejection.INVALID_STRUCTURE


@pytest.mark.parametrize("mutate", [
    lambda d: d.pop("requests_score"),
    lambda d: d.update(schema_version="engine3.ai_preparation_proposal.v1"),
    lambda d: d.update(requested_action="SEND"),
    lambda d: d.update(requested_action=0),
    lambda d: d.update(requests_score=0),
    lambda d: d.update(artifacts=[]),
    lambda d: d.update(artifacts={}),
    lambda d: d["artifacts"][0].update(rendered_text="Extra free text. " + SEGMENT["text"]),
    lambda d: d["artifacts"][0].update(audience="INTERNAL"),
    lambda d: d["artifacts"][0]["segments"][0].update(origin="AI_INFERENCE"),
    lambda d: d["artifacts"][0]["segments"][0].update(specificity=True),
    lambda d: d["artifacts"][0]["segments"][0].update(question_has_premise=0),
    lambda d: d["artifacts"][0]["segments"][0].update(semantic_class="SAFE"),
    lambda d: d["artifacts"].append(dict(d["artifacts"][0])),
])
def test_structural_deviations_fail_closed(mutate):
    value = _output()
    mutate(value)
    assert _rejection(_bytes(value)) is StructuralRejection.INVALID_STRUCTURE


@pytest.mark.parametrize("raw,rejection", [
    (b"\xff\xfe not utf-8", StructuralRejection.NOT_UTF8),
    (b"Here is the proposal: {", StructuralRejection.NOT_JSON),
    (b'{"a": 1, "a": 2}', StructuralRejection.NOT_JSON),
    (b'{"specificity": NaN}', StructuralRejection.NOT_JSON),
    (b"[]", StructuralRejection.INVALID_STRUCTURE),
])
def test_unparseable_output_is_classified(raw, rejection):
    assert _rejection(raw) is rejection


def test_capture_records_exact_raw_bytes_and_never_repairs_a_rejection():
    raw = b'{"not": "a proposal"}'
    record, proposal = capture_run(_binding(), raw)
    assert proposal is None and record.raw_output == raw
    assert record.raw_output_sha256 == hashlib.sha256(raw).hexdigest()
    assert record.outcome.rejection is StructuralRejection.INVALID_STRUCTURE and record.outcome.proposal_digest is None
    raw = _bytes(_output())
    record, proposal = capture_run(_binding(), raw)
    assert record.outcome.rejection is None and record.outcome.proposal_digest == proposal.digest
    assert record.raw_output == raw


def test_digest_is_deterministic_and_binds_the_run():
    raw = _bytes(_output())
    first = parse_experimental_output(_binding(), raw).digest
    assert first == parse_experimental_output(_binding(), raw).digest
    assert first != parse_experimental_output(_binding(repeat=2), raw).digest
    assert first != parse_experimental_output(_binding(scenario="SCN-2"), raw).digest


@pytest.mark.parametrize("arm,kind,repeat", [
    (ExperimentArm.G, RunKind.FULL_RUN, 11), (ExperimentArm.G, RunKind.PILOT, 3),
    (ExperimentArm.A, RunKind.FULL_RUN, 6), (ExperimentArm.A, RunKind.PILOT, 1),
    (ExperimentArm.G, RunKind.FULL_RUN, 0), (ExperimentArm.G, RunKind.FULL_RUN, True),
])
def test_binding_enforces_the_locked_repeat_counts_and_pilot_scope(arm, kind, repeat):
    with pytest.raises(Engine3ValidationError):
        _binding(arm, kind, repeat)


def test_binding_accepts_the_locked_maxima():
    _binding(ExperimentArm.G, RunKind.FULL_RUN, 10)
    _binding(ExperimentArm.G, RunKind.PILOT, 2)
    _binding(ExperimentArm.A, RunKind.FULL_RUN, 5)
    with pytest.raises(Engine3ValidationError):
        RunBinding("CONFIG-1", ExperimentArm.G, "SCN-1", 1, RunKind.DRY_RUN, "A" * 64)


def test_arm_a_output_may_not_alter_the_fixed_prose():
    fixed = (FixedProseArtifact("A-1", "FOLLOW_UP_EMAIL", (("S1", SEGMENT["text"]),)),)
    binding = _binding(arm=ExperimentArm.A)
    assert parse_experimental_output(binding, _bytes(_output()), fixed).artifacts[0].segments[0].text == SEGMENT["text"]
    altered = (FixedProseArtifact("A-1", "FOLLOW_UP_EMAIL", (("S1", "Has anything changed?"),)),)
    assert _rejection(_bytes(_output()), binding, altered) is StructuralRejection.FIXED_PROSE_MISMATCH
    with pytest.raises(Engine3ValidationError):
        parse_experimental_output(binding, _bytes(_output()))
    with pytest.raises(Engine3ValidationError):
        parse_experimental_output(_binding(), _bytes(_output()), fixed)


def test_locked_stage_e_loader_still_refuses_any_non_prerecorded_provenance():
    recording = stage_e_adapter.load_inputs()["recordings"][0]["proposal"]
    for provenance in ExperimentProvenance:
        with pytest.raises(Engine3ValidationError):
            load_prerecorded_preparation_proposal(dict(recording, provenance=provenance.value))


def test_envelope_records_have_no_action_or_send_surface():
    for cls in (RunBinding, ExperimentalProposal, RawRunRecord):
        public = {n for n in dir(cls) if not n.startswith("_")}
        callables = {n for n in public if callable(getattr(cls, n)) and not isinstance(getattr(cls, n), property)}
        assert callables == set(), (cls.__name__, callables)


# --- Stage F context variants -------------------------------------------------------------------------


def test_every_spec_variant_is_one_exact_delta_that_preserves_locked_values():
    ctx = ad.contexts()
    spec = ad.load_spec()["contexts"]["stage_f_variants"]
    assert {v["context_id"] for v in spec} <= set(ctx)
    for variant in spec:
        base, built = ctx[variant["base"]], ctx[variant["context_id"]]
        assert built.digest != base.digest and built.upstream == base.upstream
        before = {g.grounding_item_id: g for g in base.grounding_items}
        after = {g.grounding_item_id: g for g in built.grounding_items}
        for item_id in before:  # buyer statement, threshold, renewal, measurement values never change
            assert after[item_id].exact_value == before[item_id].exact_value
        changed = {i for i in after if i not in before or after[i] != before[i]}
        assert len(changed) == 1, variant["context_id"]


def test_the_source_attribution_pair_differs_only_in_source_identity_permission():
    ctx = ad.contexts()
    a = {g.grounding_item_id: g for g in ctx["CTX-F-SOURCE-ALLOWED"].grounding_items}
    b = {g.grounding_item_id: g for g in ctx["CTX-F-SOURCE-NOT-ALLOWED"].grounding_items}
    differing = [i for i in a if a[i] != b[i]]
    assert len(differing) == 1 and a[differing[0]].exact_value == b[differing[0]].exact_value


@pytest.mark.parametrize("delta", [
    {}, {"add_grounding_item": {}, "set_disposition": {}}, {"remove_grounding_item": "G-AR-THRESHOLD"},
    {"set_disposition": {"grounding_item_id": "G-MISSING", "disposition": "UNKNOWN"}},
    {"set_disposition": {"grounding_item_id": "G-AR-THRESHOLD", "disposition": "PUBLIC"}},
])
def test_invalid_context_deltas_fail_closed(delta):
    with pytest.raises(Engine3ValidationError):
        apply_context_delta(ad.contexts()["CTX-AR-CURRENT"], delta)


def test_a_delta_cannot_replace_an_existing_grounding_item():
    base = ad.contexts()["CTX-AR-CURRENT"]
    item = next(v for v in ad.load_spec()["contexts"]["stage_f_variants"] if "add_grounding_item" in v["delta"])
    replacement = dict(item["delta"]["add_grounding_item"], grounding_item_id="G-AR-THRESHOLD")
    with pytest.raises(Engine3ValidationError):
        apply_context_delta(base, {"add_grounding_item": replacement})
