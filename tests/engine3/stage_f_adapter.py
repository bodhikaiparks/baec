"""Test-only adapter for Stage F dry-run inputs.

Builds contexts from the locked Stage E fixture contexts plus the locked Stage F
spec deltas, run bindings, blinded packets, and reference records. Expected
results are read only from the separate oracle file and only by tests.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from baec_app.engine3.enums import PreparationChannel, PreparationGoal
from experiments.engine3_stage_f.annotation import (
    AdjudicatedSegment,
    Adjudication,
    AnnotatorRole,
    PrimaryAnnotation,
    attestation_from_dict,
    build_annotation_packet,
    finalize_reference,
    record_provenance,
    segment_annotation_from_dict,
)
from experiments.engine3_stage_f.contexts import apply_context_delta
from experiments.engine3_stage_f.envelope import ExperimentArm, FixedProseArtifact, RunBinding, RunKind, capture_run
from tests.engine3 import stage_e_adapter

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
INPUTS_PATH = HERE / "fixtures" / "ENGINE3_STAGE_F_DRY_RUN_INPUTS.json"
ORACLE_PATH = HERE / "fixtures" / "ENGINE3_STAGE_F_DRY_RUN_ORACLE.json"
SPEC_PATH = REPO / "docs" / "engine3" / "ENGINE3_STAGE_F_EXPERIMENT_SPEC.json"
PROTOCOL_PATH = REPO / "docs" / "engine3" / "ENGINE3_STAGE_F_HUMAN_REFERENCE_ANNOTATION_PROTOCOL.md"
GUIDE_PATH = REPO / "docs" / "engine3" / "ENGINE3_STAGE_F_HUMAN_REFERENCE_OPERATIONAL_GUIDE.md"


def load_inputs():
    return json.loads(INPUTS_PATH.read_text(encoding="utf-8"))


def load_oracle():
    return json.loads(ORACLE_PATH.read_text(encoding="utf-8"))


def load_spec():
    return json.loads(SPEC_PATH.read_text(encoding="utf-8"))


def protocol_sha256() -> str:
    return hashlib.sha256(PROTOCOL_PATH.read_bytes()).hexdigest()


def guide_sha256() -> str:
    return hashlib.sha256(GUIDE_PATH.read_bytes()).hexdigest()


def contexts():
    """Stage E fixture contexts (read-only, unchanged) plus the Stage F spec variants."""
    built = {c["context_id"]: stage_e_adapter.build_context(c) for c in stage_e_adapter.load_inputs()["contexts"]}
    for variant in load_spec()["contexts"]["stage_f_variants"]:
        built[variant["context_id"]] = apply_context_delta(built[variant["base"]], variant["delta"])
    return built


def scenario(scenario_id):
    matches = [s for s in load_spec()["scenarios"] if s["id"] == scenario_id]
    return matches[0] if matches else None


def binding(case, ctx) -> RunBinding:
    return RunBinding(
        experiment_configuration_id=load_inputs()["experiment_configuration_id"],
        arm=ExperimentArm(case["arm"]),
        scenario_id=case["scenario_id"],
        repeat_index=case["repeat_index"],
        run_kind=RunKind.DRY_RUN,
        bound_context_digest=ctx[case["generation_context_id"]].digest,
    )


def fixed_prose(case):
    if "fixed_prose" not in case:
        return None
    return tuple(
        FixedProseArtifact(a["artifact_id"], a["artifact_type"], tuple(tuple(s) for s in a["segments"]))
        for a in case["fixed_prose"]
    )


def run(case, ctx):
    return capture_run(binding(case, ctx), case["raw_model_output"].encode("utf-8"), fixed_prose(case))


def packet(case, artifact, ctx):
    s = scenario(case["scenario_id"]) if case["arm"] == "G" else None
    goal = None if s is None else PreparationGoal(s["goal"])
    channel = None if s is None else PreparationChannel(s["channel"])
    return build_annotation_packet(artifact, ctx[case["evaluation_context_id"]], goal, channel)


def _primary(record, role, alias, pkt):
    return PrimaryAnnotation(
        alias=alias,
        role=role,
        packet_id=pkt.packet_id,
        **record_provenance(pkt),
        recorded_at=record["recorded_at"],
        attestation=attestation_from_dict(record["attestation"]),
        segments=tuple(segment_annotation_from_dict(sid, s) for sid, s in zip(pkt.segment_ids, record["segments"])),
    )


def primaries(case, artifact_id, pkt):
    aliases = load_inputs()["annotator_aliases"]
    records = case["human_reference"][artifact_id]
    return (_primary(records["primary_a"], AnnotatorRole.PRIMARY_A, aliases["primary_a"], pkt),
            _primary(records["primary_b"], AnnotatorRole.PRIMARY_B, aliases["primary_b"], pkt))


def adjudication(case, artifact_id, pkt):
    record = case["human_reference"][artifact_id]["adjudication"]
    if record is None:
        return None
    return Adjudication(
        alias=load_inputs()["annotator_aliases"]["adjudicator"],
        packet_id=pkt.packet_id,
        **record_provenance(pkt),
        recorded_at=record["recorded_at"],
        segments=tuple(
            AdjudicatedSegment(
                segment_annotation_from_dict(pkt.segment_ids[s["ordinal"] - 1], s["decision"]), s["reason"]
            )
            for s in record["segments"]
        ),
        attestation=None if record["attestation"] is None else attestation_from_dict(record["attestation"]),
    )


def reference(case, artifact_id, pkt):
    a, b = primaries(case, artifact_id, pkt)
    return finalize_reference(pkt, a, b, adjudication(case, artifact_id, pkt))
