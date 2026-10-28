"""Test-only adapter for Stage F-AR offline tests.

Reuses the locked Stage F dry-run inputs unchanged. The handwritten Primary A and
Primary B segment records stand in for prerecorded Reviewer A and Reviewer B
output, and the handwritten adjudications stand in for adjudicator output. No
label is invented here, and none of it is model output. Reviewer configurations
are synthetic placeholders, never frozen and never selected.
"""

from __future__ import annotations

import hashlib
import json

from experiments.engine3_stage_f.annotation import AnnotationStatus
from experiments.engine3_stage_f_ar.panel import REVIEWER_OUTPUT_SCHEMA_VERSION, ReviewerConfiguration, ReviewerRole
from experiments.engine3_stage_f_ar.review import (
    AutonomousBatch,
    ab_disagreements,
    build_adjudication_request,
    build_review_request,
    finalize_autonomous_reference,
    record_review,
)
from tests.engine3 import stage_f_adapter as ad

GUIDE = ad.GUIDE_PATH.read_bytes()
REVIEW_PROMPT = "SYNTHETIC TEST REVIEW PROMPT. Not a frozen Stage F-AR prompt."
ADJUDICATION_PROMPT = "SYNTHETIC TEST ADJUDICATION PROMPT. Not a frozen Stage F-AR prompt."


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def configuration(role: ReviewerRole, family: str, developer: str, frozen: bool = False, **changes):
    values = dict(
        role=role,
        model_family=family,
        model_developer=developer,
        model_id=f"synthetic-{family.lower().replace(' ', '-')}",
        model_version="synthetic-v0",
        sampling_configuration=(("temperature", "OMITTED"),),
        prompt_bundle_sha256=_sha(ADJUDICATION_PROMPT if role is ReviewerRole.AUTONOMOUS_ADJUDICATOR else REVIEW_PROMPT),
        output_schema_version=REVIEWER_OUTPUT_SCHEMA_VERSION,
        frozen=frozen,
    )
    values.update(changes)
    return ReviewerConfiguration(**values)


CONFIG_A = configuration(ReviewerRole.REVIEWER_A, "Synthetic Family Alpha", "Synthetic Developer One")
CONFIG_B = configuration(ReviewerRole.REVIEWER_B, "Synthetic Family Beta", "Synthetic Developer Two")
CONFIG_ADJ = configuration(ReviewerRole.AUTONOMOUS_ADJUDICATOR, "Synthetic Family Gamma", "Synthetic Developer Three")
PANEL = (CONFIG_A, CONFIG_B, CONFIG_ADJ)


def raw_output(packet, segments: list[dict]) -> bytes:
    items = [{"packet_segment_id": sid, **s} for sid, s in zip(packet.segment_ids, segments)]
    return json.dumps({"schema_version": REVIEWER_OUTPUT_SCHEMA_VERSION, "segments": items}).encode("utf-8")


def review(config, packet, raw: bytes, slot="AR-SLOT-TEST", attempt=1, batch=AutonomousBatch.OFFLINE_DRY_RUN):
    return record_review(config, build_review_request(packet, GUIDE, REVIEW_PROMPT), packet, raw, slot, attempt, batch)


def adjudicate(packet, record_a, record_b, raw: bytes, config=CONFIG_ADJ, batch=AutonomousBatch.OFFLINE_DRY_RUN):
    request = build_adjudication_request(packet, GUIDE, ADJUDICATION_PROMPT, record_a, record_b)
    return record_review(config, request, packet, raw, "AR-SLOT-TEST", 1, batch)


def _decision(record: dict) -> dict:
    return {k: record[k] for k in ("status", "fields", "formatting_variant", "mixed_reference_modes", "note")}


def adjudicator_segments(case, artifact_id) -> list[dict]:
    """Complete adjudicator annotation: the handwritten adjudication where given, the agreed A/B decision where
    A and B agree, and ADJUDICATION_REQUIRED where neither exists."""
    records = case["human_reference"][artifact_id]
    adjudicated = {s["ordinal"]: s["decision"] for s in (records["adjudication"] or {"segments": []})["segments"]}
    out = []
    for n, (a, b) in enumerate(zip(records["primary_a"]["segments"], records["primary_b"]["segments"]), start=1):
        if n in adjudicated:
            out.append(_decision(adjudicated[n]))
        elif _decision(a) == _decision(b) and a["status"] != AnnotationStatus.ADJUDICATION_REQUIRED.value:
            out.append(_decision(a))
        else:
            out.append({"status": "ADJUDICATION_REQUIRED", "fields": None, "formatting_variant": False,
                        "mixed_reference_modes": False, "note": "not adjudicated"})
    return out


def records(case, artifact, ctx):
    packet = ad.packet(case, artifact, ctx)
    human = case["human_reference"][artifact.artifact_id]
    record_a = review(CONFIG_A, packet, raw_output(packet, [_decision(s) for s in human["primary_a"]["segments"]]))
    record_b = review(CONFIG_B, packet, raw_output(packet, [_decision(s) for s in human["primary_b"]["segments"]]))
    return packet, record_a, record_b


def reference(case, artifact, ctx):
    packet, record_a, record_b = records(case, artifact, ctx)
    adjudicator = None
    if ab_disagreements(packet, record_a, record_b):
        adjudicator = adjudicate(packet, record_a, record_b,
                                 raw_output(packet, adjudicator_segments(case, artifact.artifact_id)))
    return finalize_autonomous_reference(packet, record_a, record_b, adjudicator)
