"""Stage F-AR blinding, provenance, consensus, and adjudication (adversarial, offline).

Software tests over handwritten synthetic reviewer output. They say nothing about any model's reliability,
and the autonomous reference they build is not a human reference or ground truth.
"""

from __future__ import annotations

import dataclasses
import enum
import hashlib
import inspect
import json
from pathlib import Path

import pytest

from baec_app.engine3.errors import Engine3ValidationError
from experiments.engine3_stage_f.annotation import (
    LOCKED_PROTOCOL_SHA256,
    OPERATIONAL_GUIDE_SHA256,
    AnnotationPacket,
    annotation_packet_sha256,
    build_annotation_packet,
)
from experiments.engine3_stage_f.comparison import compare_artifact
from experiments.engine3_stage_f.envelope import ExperimentProvenance, RunBinding, capture_run
from experiments.engine3_stage_f_ar import analysis, panel, review
from experiments.engine3_stage_f_ar.panel import ReviewerRole
from experiments.engine3_stage_f_ar.review import (
    AutonomousBatch,
    AutonomousProvenance,
    AutonomousReference,
    AutonomousReferenceKind,
    ReviewRejection,
    ab_disagreements,
    build_adjudication_request,
    build_review_request,
    finalize_autonomous_reference,
    record_review,
)
from tests.engine3 import stage_f_adapter as ad
from tests.engine3 import stage_f_ar_adapter as ar

REPO = Path(__file__).resolve().parents[2]
CTX = ad.contexts()
CASES = {c["case_id"]: c for c in ad.load_inputs()["cases"]}
CALIBRATION = json.loads((REPO / "docs/engine3/ENGINE3_STAGE_F_HUMAN_CALIBRATION_CORPUS.json").read_text("utf-8"))
PROVENANCE_FIELDS = {
    "reviewer_role", "reviewer_family", "reviewer_model_id", "reviewer_configuration_sha256", "prompt_bundle_sha256",
    "locked_protocol_sha256", "operational_guide_sha256", "annotation_packet_sha256", "raw_output_sha256",
    "parsed_annotation_sha256", "run_slot_id", "attempt_number", "provenance",
}


def _artifact(case_id, index=0):
    case = CASES[case_id]
    _, proposal = ad.run(case, CTX)
    return case, proposal, proposal.artifacts[index]


def _setup(case_id="F-DRY-04"):
    case, proposal, artifact = _artifact(case_id)
    packet, record_a, record_b = ar.records(case, artifact, CTX)
    return case, proposal, artifact, packet, record_a, record_b


def _decided(record):
    """A record's raw output as a list of segment decisions (packet ids stripped)."""
    return [{k: v for k, v in s.items() if k != "packet_segment_id"} for s in json.loads(record.raw_output)["segments"]]


def _disagreeing_b(packet, record_a, field="specificity", value=90, ordinal=2):
    """Reviewer B output equal to A except one field on one segment."""
    changed = _decided(record_a)
    changed[ordinal - 1]["fields"] = {**changed[ordinal - 1]["fields"], field: value}
    return ar.review(ar.CONFIG_B, packet, ar.raw_output(packet, changed))


def _adjudicated(case_id="F-DRY-04"):
    case, proposal, artifact, packet, record_a, _ = _setup(case_id)
    record_b = _disagreeing_b(packet, record_a)
    adjudicator = ar.adjudicate(packet, record_a, record_b, ar.raw_output(packet, _decided(record_a)))
    return packet, record_a, record_b, adjudicator


# --- blinding ----------------------------------------------------------------------------------------


def test_review_request_builder_takes_only_the_packet_guide_and_prompt():
    assert list(inspect.signature(build_review_request).parameters) == ["packet", "operational_guide", "prompt_bundle"]


def test_reviewer_a_and_reviewer_b_receive_identical_requests_with_no_role_or_other_output():
    _, _, _, packet, record_a, record_b = _setup()
    before = build_review_request(packet, ar.GUIDE, ar.REVIEW_PROMPT)
    assert record_a.request_sha256 == record_b.request_sha256 == before.sha256
    body = json.loads(before.body)
    assert set(body) == {"request_schema_version", "task", "instructions", "operational_guide", "packet",
                         "output_schema_version"}
    assert body["task"] == "INDEPENDENT_REVIEW"
    for text in ("REVIEWER_A", "REVIEWER_B", "recorded_reviews", "Synthetic Family", record_a.raw_output_sha256,
                 record_a.parsed_annotation_sha256, record_a.record_sha256):
        assert text not in before.body


def test_a_cannot_see_b_and_b_cannot_see_a_even_after_the_other_is_recorded():
    _, _, _, packet, record_a, _ = _setup()
    after_a = build_review_request(packet, ar.GUIDE, ar.REVIEW_PROMPT)
    record_b = _disagreeing_b(packet, record_a)
    after_b = build_review_request(packet, ar.GUIDE, ar.REVIEW_PROMPT)
    assert after_a.body == after_b.body and not after_a.is_adjudication
    for record in (record_a, record_b):
        assert record.raw_output.decode("utf-8") not in after_b.body
        assert record.record_sha256 not in after_b.body


def test_generator_labels_never_reach_a_reviewer_request():
    """Rewriting every generator annotation leaves the packet and request byte-identical."""
    case, proposal, artifact, packet, _, _ = _setup()
    raw = json.loads(case["raw_model_output"])
    for segment in raw["artifacts"][0]["segments"]:
        segment.update(claim_type="FACTUAL_ASSERTION", semantic_class="BUYER_PURCHASE_OR_SWITCH_INTENT",
                       claim_field="OTHER", specificity=90, prescribes_buyer_choice=True)
    relabelled = dict(case, raw_model_output=json.dumps(raw))
    _, other = ad.run(relabelled, CTX)
    assert other.artifacts[0].segments != artifact.segments  # the generator labels really differ
    other_packet = ad.packet(relabelled, other.artifacts[0], CTX)
    assert other_packet == packet
    assert annotation_packet_sha256(other_packet) == annotation_packet_sha256(packet)
    assert (build_review_request(other_packet, ar.GUIDE, ar.REVIEW_PROMPT).sha256
            == build_review_request(packet, ar.GUIDE, ar.REVIEW_PROMPT).sha256)


def test_hidden_run_metadata_never_changes_the_packet_hash():
    """Arm, scenario, repeat index, run kind, and configuration id are not packet inputs."""
    case, proposal, artifact, packet, _, _ = _setup()
    binding = proposal.binding
    for changes in ({"scenario_id": "F-G99"}, {"repeat_index": 2}, {"experiment_configuration_id": "OTHER"}):
        rebound = dataclasses.replace(binding, **changes)
        _, other = capture_run(rebound, case["raw_model_output"].encode("utf-8"))
        other_packet = ad.packet(case, other.artifacts[0], CTX)
        assert annotation_packet_sha256(other_packet) == annotation_packet_sha256(packet)
    assert list(inspect.signature(build_annotation_packet).parameters) == [
        "artifact", "context", "preparation_goal", "channel_scope"]


def test_visible_packet_content_changes_the_packet_hash():
    _, _, artifact, packet, _, _ = _setup()
    digest = annotation_packet_sha256(packet)
    changed_text = dataclasses.replace(packet, segments=(dataclasses.replace(packet.segments[0], text="Changed."),)
                                       + packet.segments[1:])
    changed_item = dataclasses.replace(packet, grounding_items=(
        dataclasses.replace(packet.grounding_items[0], exact_value="changed"),) + packet.grounding_items[1:])
    changed_goal = dataclasses.replace(packet, preparation_goal="OTHER_GOAL")
    for changed in (changed_text, changed_item, changed_goal):
        assert annotation_packet_sha256(changed) != digest


def test_request_exposes_no_stage_d_result_arm_scenario_or_evaluation_metadata():
    case, proposal, artifact, packet, _, _ = _setup()
    comparison = compare_artifact(proposal, artifact.artifact_id, CTX[case["evaluation_context_id"]],
                                  ad.reference(case, artifact.artifact_id, packet))
    assert comparison.false_pass  # F-DRY-04 is a false pass in the human dry run
    body = build_review_request(packet, ar.GUIDE, ar.REVIEW_PROMPT).body
    reason_codes = {i.reason_code.value for i in comparison.d_ref.issues} | {i.reason_code.value
                                                                             for i in comparison.d_model.issues}
    for text in reason_codes | {case["scenario_id"], case["case_id"], case["generation_context_id"], "F-G", "F-DRY",
                                "FALSE_PASS", "FALSE_REFUSAL", "CONCORDANT", "LIVE_SYNTHETIC_EXPERIMENT",
                                "OFFLINE_HANDWRITTEN_DRY_RUN", proposal.digest, case["purpose"], "human_safe"}:
        assert text not in body, text

    def keys(value):
        if isinstance(value, dict):
            for k, v in value.items():
                yield k
                yield from keys(v)
        elif isinstance(value, list):
            for v in value:
                yield from keys(v)

    forbidden = {"arm", "scenario_id", "scenario_family", "repeat_index", "run_id", "binding", "d_model", "d_ref",
                 "reason_code", "primary_reason_code", "allowed", "category", "attestation", "training_key",
                 "explanation", "model_identifier", "raw_output", "reasoning", "thinking"}
    assert not forbidden & set(keys(json.loads(body)))


def test_request_never_carries_the_calibration_answer_key():
    _, _, _, packet, _, _ = _setup()
    body = build_review_request(packet, ar.GUIDE, ar.REVIEW_PROMPT).body
    assert "training_key" not in body and "CAL-" not in body
    for item in CALIBRATION["items"]:
        assert item["training_key"]["attestation"]["explanation"] not in body
        for segment in item["training_key"]["segments"]:
            assert segment["explanation"] not in body


def test_request_never_carries_generator_raw_output():
    case, proposal, _, packet, _, _ = _setup()
    raw_record, _ = ad.run(case, CTX)
    body = build_review_request(packet, ar.GUIDE, ar.REVIEW_PROMPT).body
    assert raw_record.raw_output_sha256 not in body and case["raw_model_output"] not in body
    assert '"segment_id"' not in body  # generator segment ids are replaced by opaque packet ids


def test_request_requires_the_exact_frozen_operational_guide():
    _, _, _, packet, _, _ = _setup()
    assert hashlib.sha256(ar.GUIDE).hexdigest() == OPERATIONAL_GUIDE_SHA256
    with pytest.raises(Engine3ValidationError, match="frozen operational guide"):
        build_review_request(packet, ar.GUIDE + b"\n", ar.REVIEW_PROMPT)
    with pytest.raises(Engine3ValidationError):
        build_review_request(packet, ar.GUIDE.decode("utf-8"), ar.REVIEW_PROMPT)


# --- adjudicator access --------------------------------------------------------------------------------


def test_adjudicator_receives_a_and_b_only_after_both_records_exist():
    _, _, _, packet, record_a, record_b = _setup()
    record_b = _disagreeing_b(packet, record_a)
    for a, b in ((record_a, None), (None, record_b), (None, None)):
        with pytest.raises(Engine3ValidationError, match="both required"):
            build_adjudication_request(packet, ar.GUIDE, ar.ADJUDICATION_PROMPT, a, b)
    rejected = ar.review(ar.CONFIG_B, packet, b"not json")
    with pytest.raises(Engine3ValidationError, match="rejected"):
        build_adjudication_request(packet, ar.GUIDE, ar.ADJUDICATION_PROMPT, record_a, rejected)
    request = build_adjudication_request(packet, ar.GUIDE, ar.ADJUDICATION_PROMPT, record_a, record_b)
    # The exact A and B records are bound outside the visible body.
    assert request.reviewed_record_sha256s == (record_a.record_sha256, record_b.record_sha256)
    assert json.loads(request.body)["task"] == "AUTONOMOUS_ADJUDICATION"


def test_adjudicator_visible_request_holds_only_packet_guide_review_x_y_and_disagreements():
    packet, record_a, record_b, _ = _adjudicated()
    request = build_adjudication_request(packet, ar.GUIDE, ar.ADJUDICATION_PROMPT, record_a, record_b)
    body = json.loads(request.body)
    assert set(body) == {"request_schema_version", "task", "instructions", "operational_guide", "packet",
                         "output_schema_version", "review_x", "review_y", "disagreements"}
    assert body["disagreements"] == [{"packet_segment_id": "PS-2", "item": "specificity"}]
    for payload in (body["review_x"], body["review_y"]):
        assert [set(s) for s in payload] == [{"packet_segment_id", "status", "fields", "formatting_variant",
                                              "mixed_reference_modes"}] * len(packet.segments)
    hidden = {"REVIEWER_A", "REVIEWER_B", "AUTONOMOUS_ADJUDICATOR", "reviewer_a", "reviewer_b", "record_sha256",
              "Synthetic Family", "Synthetic Developer", "Claude Sonnet", "Anthropic", "claude-sonnet",
              record_a.record_sha256, record_b.record_sha256, record_a.raw_output_sha256, record_b.raw_output_sha256}
    for config in ar.PANEL:
        hidden |= {config.model_id, config.model_family, config.model_developer, config.configuration_sha256}
    for text in hidden:
        assert text not in request.body, text


def test_reviewer_identity_metadata_never_changes_the_adjudicator_visible_request():
    packet, record_a, record_b, _ = _adjudicated()
    base = build_adjudication_request(packet, ar.GUIDE, ar.ADJUDICATION_PROMPT, record_a, record_b)
    other_a = record_review(
        ar.configuration(ReviewerRole.REVIEWER_A, "Other Family Delta", "Other Developer", model_id="other-model-9",
                         model_version="v9", sampling_configuration=(("top_p", "OMITTED"),)),
        build_review_request(packet, ar.GUIDE, ar.REVIEW_PROMPT), packet, record_a.raw_output, "AR-SLOT-OTHER", 3,
        AutonomousBatch.OFFLINE_DRY_RUN)
    other_b = record_review(
        ar.configuration(ReviewerRole.REVIEWER_B, "Other Family Epsilon", "Another Developer", model_id="m-2"),
        build_review_request(packet, ar.GUIDE, ar.REVIEW_PROMPT), packet, record_b.raw_output, "AR-SLOT-X", 2,
        AutonomousBatch.OFFLINE_DRY_RUN)
    assert other_a.record_sha256 != record_a.record_sha256 and other_b.record_sha256 != record_b.record_sha256
    changed = build_adjudication_request(packet, ar.GUIDE, ar.ADJUDICATION_PROMPT, other_a, other_b)
    assert changed.body == base.body and changed.sha256 == base.sha256
    assert changed.reviewed_record_sha256s != base.reviewed_record_sha256s  # bound outside the visible body


def test_adjudicator_cannot_tell_which_review_came_from_reviewer_a():
    """Swapping which reviewer produced which annotation leaves the visible request byte-identical."""
    packet, record_a, record_b, _ = _adjudicated()
    swapped_a = ar.review(ar.CONFIG_A, packet, record_b.raw_output)
    swapped_b = ar.review(ar.CONFIG_B, packet, record_a.raw_output)
    base = build_adjudication_request(packet, ar.GUIDE, ar.ADJUDICATION_PROMPT, record_a, record_b)
    swapped = build_adjudication_request(packet, ar.GUIDE, ar.ADJUDICATION_PROMPT, swapped_a, swapped_b)
    assert swapped.body == base.body


def test_reviewer_notes_never_reach_the_adjudicator():
    packet, record_a, record_b, _ = _adjudicated()
    noted = _decided(record_a)
    noted[0] = {**noted[0], "note": "I am model Other Family Delta from Other Developer"}
    with_note = ar.review(ar.CONFIG_A, packet, ar.raw_output(packet, noted))
    request = build_adjudication_request(packet, ar.GUIDE, ar.ADJUDICATION_PROMPT, with_note, record_b)
    assert "Other Family Delta" not in request.body
    assert request.body == build_adjudication_request(packet, ar.GUIDE, ar.ADJUDICATION_PROMPT, record_a,
                                                      record_b).body


def test_review_x_y_annotation_content_changes_the_adjudicator_visible_request():
    packet, record_a, record_b, _ = _adjudicated()
    base = build_adjudication_request(packet, ar.GUIDE, ar.ADJUDICATION_PROMPT, record_a, record_b)
    other_b = _disagreeing_b(packet, record_a, "specificity", 10)
    changed = build_adjudication_request(packet, ar.GUIDE, ar.ADJUDICATION_PROMPT, record_a, other_b)
    assert changed.body != base.body and changed.sha256 != base.sha256
    more = _decided(other_b)
    more[0] = {**more[0], "fields": {**more[0]["fields"], "prescribes_buyer_choice": True}}
    wider = build_adjudication_request(packet, ar.GUIDE, ar.ADJUDICATION_PROMPT, record_a,
                                       ar.review(ar.CONFIG_B, packet, ar.raw_output(packet, more)))
    assert json.loads(wider.body)["disagreements"] == [
        {"packet_segment_id": "PS-1", "item": "prescribes_buyer_choice"},
        {"packet_segment_id": "PS-2", "item": "specificity"}]


def test_adjudicator_is_never_invoked_on_exact_agreement():
    _, _, _, packet, record_a, record_b = _setup()
    assert ab_disagreements(packet, record_a, record_b) == ()
    with pytest.raises(Engine3ValidationError, match="only when Reviewer A and Reviewer B disagree"):
        build_adjudication_request(packet, ar.GUIDE, ar.ADJUDICATION_PROMPT, record_a, record_b)


def test_adjudication_must_bind_the_exact_recorded_a_and_b_reviews():
    packet, record_a, record_b, adjudicator = _adjudicated()
    retried_a = ar.review(ar.CONFIG_A, packet, record_a.raw_output, attempt=2)
    assert retried_a.record_sha256 != record_a.record_sha256
    with pytest.raises(Engine3ValidationError, match="not bound to these recorded A and B reviews"):
        finalize_autonomous_reference(packet, retried_a, record_b, adjudicator)


def test_only_the_adjudicator_receives_an_adjudication_request_and_vice_versa():
    packet, record_a, record_b, _ = _adjudicated()
    request = build_adjudication_request(packet, ar.GUIDE, ar.ADJUDICATION_PROMPT, record_a, record_b)
    with pytest.raises(Engine3ValidationError):
        record_review(ar.CONFIG_A, request, packet, record_a.raw_output, "S", 1, AutonomousBatch.OFFLINE_DRY_RUN)
    plain = build_review_request(packet, ar.GUIDE, ar.ADJUDICATION_PROMPT)
    with pytest.raises(Engine3ValidationError, match="only the Autonomous Adjudicator"):
        record_review(ar.CONFIG_ADJ, plain, packet, record_a.raw_output, "S", 1, AutonomousBatch.OFFLINE_DRY_RUN)


# --- consensus logic ---------------------------------------------------------------------------------------


def test_exact_agreement_is_consensus_without_adjudication():
    _, _, _, packet, record_a, record_b = _setup()
    reference = finalize_autonomous_reference(packet, record_a, record_b)
    assert reference.kind is AutonomousReferenceKind.AUTONOMOUS_CONSENSUS_REFERENCE
    assert reference.adjudicator is None and reference.disagreements == () and reference.adjudicated_items == ()
    assert reference.segments == record_a.segments
    _, _, _, adjudicator = _adjudicated()
    with pytest.raises(Engine3ValidationError, match="only when Reviewer A and Reviewer B disagree"):
        finalize_autonomous_reference(packet, record_a, record_b, adjudicator)


def test_any_governed_disagreement_requires_the_adjudicator():
    _, _, _, packet, record_a, _ = _setup()
    record_b = _disagreeing_b(packet, record_a)
    with pytest.raises(Engine3ValidationError, match="requires the Autonomous Adjudicator"):
        finalize_autonomous_reference(packet, record_a, record_b)


@pytest.mark.parametrize("field,value", [
    ("specificity", 10), ("claim_type", "PRIOR_BUYER_STATEMENT_REFERENCE"), ("semantic_class", "OTHER_FACT"),
    ("reference_use_mode", "PARAPHRASE"), ("prescribes_buyer_choice", True), ("grounding_refs", "FIRST_PACKET_ID"),
])
def test_every_field_disagreement_is_detected(field, value):
    _, _, _, packet, record_a, _ = _setup()
    if value == "FIRST_PACKET_ID":
        value = [packet.grounding_items[0].grounding_item_id]
    record_b = _disagreeing_b(packet, record_a, field, value, ordinal=1)
    assert record_b.parsed, record_b.rejection_detail
    assert [(d.packet_segment_id, d.item) for d in ab_disagreements(packet, record_a, record_b)] == [("PS-1", field)]


def test_status_flag_and_requested_adjudication_disagreements_are_governed():
    _, _, _, packet, record_a, _ = _setup()
    decided = _decided(record_a)
    flagged = list(decided)
    flagged[0] = {"status": "ADJUDICATION_REQUIRED", "fields": None, "formatting_variant": False,
                  "mixed_reference_modes": False, "note": "unsure"}
    b_flagged = ar.review(ar.CONFIG_B, packet, ar.raw_output(packet, flagged))
    assert {d.item for d in ab_disagreements(packet, record_a, b_flagged)} == {"status", "reviewer_requested_adjudication"}
    a_flagged = ar.review(ar.CONFIG_A, packet, ar.raw_output(packet, flagged))
    both = ab_disagreements(packet, a_flagged, b_flagged)
    assert [d.item for d in both] == ["reviewer_requested_adjudication"]  # both unsure is not consensus


def test_adjudicated_reference_is_complete_explicit_and_preserves_disagreement_history():
    packet, record_a, record_b, adjudicator = _adjudicated()
    reference = finalize_autonomous_reference(packet, record_a, record_b, adjudicator)
    assert reference.kind is AutonomousReferenceKind.AUTONOMOUS_ADJUDICATED_REFERENCE
    assert reference.label == "AUTONOMOUS_ADJUDICATED_REFERENCE"
    assert reference.segments == adjudicator.segments and len(reference.segments) == len(packet.segments)
    assert reference.adjudicated_items == ("specificity",)
    assert [(d.packet_segment_id, d.item, d.reviewer_a_value, d.reviewer_b_value) for d in reference.disagreements] == [
        ("PS-2", "specificity", record_a.segments[1].fields.specificity, 90)]
    # Raw and parsed A and B outputs, and the adjudicator output, are all kept.
    assert reference.reviewer_a is record_a and reference.reviewer_b is record_b and reference.adjudicator is adjudicator
    assert reference.reviewer_b.segments[1].fields.specificity == 90 != reference.segments[1].fields.specificity
    # History cannot be erased: a reference with an adjudicator and no disagreement is invalid.
    with pytest.raises(Engine3ValidationError):
        dataclasses.replace(reference, disagreements=())
    with pytest.raises(Engine3ValidationError):
        dataclasses.replace(reference, kind=AutonomousReferenceKind.AUTONOMOUS_CONSENSUS_REFERENCE)
    with pytest.raises(dataclasses.FrozenInstanceError):
        reference.disagreements = ()


def test_adjudicator_departure_from_agreed_segments_is_recorded():
    packet, record_a, record_b, _ = _adjudicated()
    decided = _decided(record_a)
    decided[0] = {**decided[0], "note": "different note only"}
    decided[1] = {**decided[1], "fields": {**decided[1]["fields"], "specificity": 90}}
    decided[0]["fields"] = {**decided[0]["fields"], "prescribes_buyer_choice": True}
    adjudicator = ar.adjudicate(packet, record_a, record_b, ar.raw_output(packet, decided))
    reference = finalize_autonomous_reference(packet, record_a, record_b, adjudicator)
    assert reference.adjudicator_departures == ("PS-1",)


def test_reviewer_a_and_b_must_be_different_model_families():
    _, _, _, packet, record_a, _ = _setup()
    same_family = ar.configuration(ReviewerRole.REVIEWER_B, " synthetic family ALPHA ", "Synthetic Developer Two")
    record_b = ar.review(same_family, packet, record_a.raw_output)
    with pytest.raises(Engine3ValidationError, match="different model families"):
        finalize_autonomous_reference(packet, record_a, record_b)


# --- hashes and provenance ---------------------------------------------------------------------------------


def test_every_record_carries_the_required_provenance():
    packet, record_a, record_b, adjudicator = _adjudicated()
    for record, config in ((record_a, ar.CONFIG_A), (record_b, ar.CONFIG_B), (adjudicator, ar.CONFIG_ADJ)):
        fields = record.provenance_record()
        assert PROVENANCE_FIELDS <= set(fields)
        assert fields["reviewer_role"] == config.role.value
        assert fields["reviewer_family"] == config.model_family and fields["reviewer_model_id"] == config.model_id
        assert fields["reviewer_configuration_sha256"] == config.configuration_sha256
        assert fields["prompt_bundle_sha256"] == config.prompt_bundle_sha256
        assert fields["locked_protocol_sha256"] == LOCKED_PROTOCOL_SHA256
        assert fields["operational_guide_sha256"] == OPERATIONAL_GUIDE_SHA256
        assert fields["annotation_packet_sha256"] == annotation_packet_sha256(packet)
        assert fields["raw_output_sha256"] == hashlib.sha256(record.raw_output).hexdigest()
        assert fields["provenance"] == "OFFLINE_HANDWRITTEN_AUTONOMOUS_DRY_RUN"
    assert adjudicator.provenance_record()["reviewed_record_sha256s"] == (record_a.record_sha256, record_b.record_sha256)


def test_reviewer_provenance_is_distinct_from_generator_provenance():
    assert {p.value for p in AutonomousProvenance}.isdisjoint({p.value for p in ExperimentProvenance})
    assert (review._PROVENANCE[AutonomousBatch.AR_44_INITIAL_AUTONOMOUS_EXECUTION_BATCH]
            is AutonomousProvenance.AUTONOMOUS_SYNTHETIC_REFERENCE)
    assert review._PROVENANCE[AutonomousBatch.AR_FULL_RUN] is AutonomousProvenance.AUTONOMOUS_SYNTHETIC_REFERENCE
    _, _, _, packet, record_a, _ = _setup()
    with pytest.raises(Engine3ValidationError, match="provenance must match"):
        dataclasses.replace(record_a, provenance=AutonomousProvenance.AUTONOMOUS_SYNTHETIC_REFERENCE)


def test_hashes_bind_the_exact_blinded_packet():
    case, _, _, packet, record_a, record_b = _setup()
    other_case, _, other_artifact = _artifact("F-DRY-01")
    other_packet = ad.packet(other_case, other_artifact, CTX)
    request = build_review_request(other_packet, ar.GUIDE, ar.REVIEW_PROMPT)
    with pytest.raises(Engine3ValidationError, match="exact packet"):
        record_review(ar.CONFIG_A, request, packet, record_a.raw_output, "S", 1, AutonomousBatch.OFFLINE_DRY_RUN)
    _, other_a, other_b = ar.records(other_case, other_artifact, CTX)
    with pytest.raises(Engine3ValidationError, match="exact packet"):
        finalize_autonomous_reference(packet, other_a, record_b)
    reference = finalize_autonomous_reference(packet, record_a, record_b)
    assert reference.annotation_packet_sha256 == annotation_packet_sha256(packet)


def test_record_hashes_detect_tampering():
    _, _, _, packet, record_a, _ = _setup()
    with pytest.raises(Engine3ValidationError, match="raw_output_sha256"):
        dataclasses.replace(record_a, raw_output=record_a.raw_output + b" ")
    with pytest.raises(Engine3ValidationError, match="parsed_annotation_sha256"):
        dataclasses.replace(record_a, segments=record_a.segments[:-1])
    with pytest.raises(Engine3ValidationError, match="operational guide"):
        dataclasses.replace(record_a, operational_guide_sha256="0" * 64)


def test_prompt_bundle_must_match_the_configuration():
    _, _, _, packet, record_a, _ = _setup()
    request = build_review_request(packet, ar.GUIDE, "a different prompt")
    with pytest.raises(Engine3ValidationError, match="configured prompt bundle"):
        record_review(ar.CONFIG_A, request, packet, record_a.raw_output, "S", 1, AutonomousBatch.OFFLINE_DRY_RUN)


def test_ar_44_or_full_run_review_needs_a_frozen_configuration():
    _, _, _, packet, record_a, _ = _setup()
    request = build_review_request(packet, ar.GUIDE, ar.REVIEW_PROMPT)
    for kind in (AutonomousBatch.AR_44_INITIAL_AUTONOMOUS_EXECUTION_BATCH, AutonomousBatch.AR_FULL_RUN):
        with pytest.raises(Engine3ValidationError, match="frozen reviewer configuration"):
            record_review(ar.CONFIG_A, request, packet, record_a.raw_output, "S", 1, kind)


# --- fail-closed parsing -------------------------------------------------------------------------------------


@pytest.mark.parametrize("raw,rejection", [
    (b"\xff", ReviewRejection.NOT_UTF8),
    (b"not json", ReviewRejection.NOT_JSON),
    (b'{"schema_version": "x", "schema_version": "y"}', ReviewRejection.NOT_JSON),
    (b'{"schema_version": "engine3.stage_f_ar.reviewer_output.v1", "segments": [], "extra": 1}',
     ReviewRejection.INVALID_STRUCTURE),
    (b'{"schema_version": "engine3.stage_f_ar.reviewer_output.v1", "segments": []}', ReviewRejection.INVALID_STRUCTURE),
])
def test_invalid_reviewer_output_is_recorded_as_a_rejection_and_yields_no_reference(raw, rejection):
    _, _, _, packet, record_a, _ = _setup()
    record_b = ar.review(ar.CONFIG_B, packet, raw)
    assert record_b.rejection is rejection and record_b.segments is None and record_b.raw_output == raw
    with pytest.raises(Engine3ValidationError, match="rejected review"):
        finalize_autonomous_reference(packet, record_a, record_b)


def test_citing_an_id_not_shown_in_the_packet_is_rejected():
    _, _, _, packet, record_a, _ = _setup()
    decided = _decided(record_a)
    decided[0]["fields"] = {**decided[0]["fields"], "grounding_refs": ["G-NOT-IN-PACKET"]}
    record = ar.review(ar.CONFIG_B, packet, ar.raw_output(packet, decided))
    assert record.rejection is ReviewRejection.INVALID_STRUCTURE and "not shown in the packet" in record.rejection_detail


def test_rejected_adjudication_yields_no_reference():
    packet, record_a, record_b, _ = _adjudicated()
    adjudicator = ar.adjudicate(packet, record_a, record_b, b"{}")
    assert not adjudicator.parsed
    with pytest.raises(Engine3ValidationError, match="rejected adjudication"):
        finalize_autonomous_reference(packet, record_a, record_b, adjudicator)


# --- labels ------------------------------------------------------------------------------------------------------


def test_autonomous_results_can_never_be_labelled_human_reference_or_ground_truth():
    assert {k.value for k in AutonomousReferenceKind} == {"AUTONOMOUS_CONSENSUS_REFERENCE",
                                                          "AUTONOMOUS_ADJUDICATED_REFERENCE"}
    packet, record_a, record_b, adjudicator = _adjudicated()
    reference = finalize_autonomous_reference(packet, record_a, record_b, adjudicator)
    for label in ("HUMAN_REFERENCE", "GROUND_TRUTH", "HUMAN_GOLD_STANDARD", None):
        with pytest.raises(Engine3ValidationError, match="labelled only"):
            dataclasses.replace(reference, kind=label)
    for module in (panel, review, analysis):
        for value in vars(module).values():
            if isinstance(value, type) and issubclass(value, enum.Enum):
                for member in value:
                    assert "HUMAN" not in str(member.value) and "GROUND_TRUTH" not in str(member.value)
                    assert "GOLD" not in str(member.value)
    with pytest.raises(Engine3ValidationError, match="Stage F-AR namespace"):
        dataclasses.replace(reference, result_namespace="engine3_stage_f_human_reference")


def test_an_autonomous_reference_cannot_enter_the_human_comparison_path():
    case, proposal, artifact, packet, record_a, record_b = _setup()
    reference = finalize_autonomous_reference(packet, record_a, record_b)
    assert isinstance(reference, AutonomousReference)
    with pytest.raises(Engine3ValidationError, match="reference has wrong type"):
        compare_artifact(proposal, artifact.artifact_id, CTX[case["evaluation_context_id"]], reference)


def test_packet_type_is_the_locked_harness_packet():
    _, _, _, packet, _, _ = _setup()
    assert type(packet) is AnnotationPacket
    with pytest.raises(Engine3ValidationError, match="packet has wrong type"):
        build_review_request(dataclasses.asdict(packet), ar.GUIDE, ar.REVIEW_PROMPT)
    assert RunBinding.__module__ == "experiments.engine3_stage_f.envelope"
