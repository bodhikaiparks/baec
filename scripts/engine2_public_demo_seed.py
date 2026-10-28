"""Per-visitor seed for the BAEC Engine 2 public demo: a confirmed Engine 1 BAEC and the Engine 2 state up to review.

This is the Stage L release demonstration (scripts/engine2_release_demo.py), stopped before the human review, so a
visitor can perform that review. It reuses the release demonstration's Engine 1 confirmation, planning choices, and
fixed identifiers, and the Stage J scenario's sources and evidence; it does not define a second scenario. The test
suite checks that the review packet built here equals the release demonstration's pre-review packet exactly.

Both stores are in-memory and returned as serialized images: nothing is written to disk, no AI or network is used,
and no Engine 1 account state changes. The Engine 1 confirmation is the release demonstration's scripted synthetic
approval; it stands for a confirmation that happened before the visitor arrives.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT))

import scripts.engine2_release_demo as release  # noqa: E402
from baec_app.data.database import make_read_only, open_database  # noqa: E402
from baec_app.engine2 import persistence as e2  # noqa: E402
from baec_app.engine2.domain import (CorrespondenceCandidate, CorrespondenceDimension, SignalCandidate,  # noqa: E402
                                     SourceItem, Supersession, SupersessionBasis, capture_observation,
                                     content_integrity_sha256, reject_source_item)
from baec_app.engine2.integration import engine1_handoff as handoff_boundary  # noqa: E402
from baec_app.engine2.measurement import MeasurementInput, Transformation, compute_derived_measurement  # noqa: E402

D = CorrespondenceDimension
stage_j = release.stage_j


@dataclass(frozen=True)
class VisitorSeed:
    """One visitor's fresh synthetic state. Images only: every read and write reopens them."""

    engine1_image: bytes
    engine2_image: bytes
    handoff: handoff_boundary.ConfirmedBaecHandoff
    account_name: str
    supplier_name: str
    monitoring_plan_id: str
    plan_version: int
    activation_id: str
    candidate_id: str
    review_at: datetime  # the scenario's fixed synthetic review time, not the visitor's clock


def seed_engine2(connection, handoff: handoff_boundary.ConfirmedBaecHandoff) -> None:
    """The release demonstration's Engine 2 steps, through the Correspondence Candidate. No review is recorded."""
    plan = handoff_boundary.monitoring_plan_from_handoff(handoff, release.CHOICES)
    for source in stage_j.SOURCES:
        e2.record_authorized_source(connection, source, recorded_at=release.PLANNED_AT, recorded_by=release.ACTOR)
    e2.record_monitoring_plan(connection, plan, recorded_at=release.PLANNED_AT, recorded_by=release.ACTOR)
    active = e2.record_plan_activation(connection, release.PLAN_ID, 1, handoff_boundary.currentness_reading(plan, handoff),
                                       activation_id=release.ACTIVATION_ID, recorded_at=release.PLANNED_AT,
                                       recorded_by=release.ACTOR)
    sources = {s.source_id: s for s in stage_j.SOURCES}
    observations = {}
    for observation_id, source_id, provenance, received, content, effective in stage_j._ITEMS:
        item = SourceItem(f"ITEM-{observation_id}", source_id, sources[source_id].source_type, "manual capture",
                          received, content, published_at=received, effective_at=effective)
        observation = capture_observation(active, sources, item, observation_id=observation_id, provenance=provenance,
                                          content_sha256=content_integrity_sha256(content),
                                          source_locator=f"{source_id}/{item.item_id}",
                                          evidence_event_id=f"EVENT-{observation_id}")
        e2.record_observation(connection, observation, activation_id=release.ACTIVATION_ID,
                              recorded_at=release.RECORDED_AT, recorded_by=release.ACTOR)
        observations[observation_id] = observation
    refused = stage_j._REFUSED
    e2.record_source_refusal(connection, reject_source_item(active, refused, f"source {refused.source_id} is not "
                                                                              "authorized for this plan"),
                             activation_id=release.ACTIVATION_ID, source_reference=refused.source_id,
                             recorded_at=release.RECORDED_AT, recorded_by=release.ACTOR)
    correction = SupersessionBasis("explicit correction or retraction language")
    for old, new, retraction in (("OBS-DEMO-01", "OBS-DEMO-02", False), ("OBS-DEMO-04", "OBS-DEMO-05", True)):
        e2.record_supersession(connection, Supersession(old, new, correction, retraction),
                               recorded_at=release.RECORDED_AT, recorded_by=release.ACTOR)
    for measurement_id, observation_id, new_price in (("DM-DEMO-01", "OBS-DEMO-01", "44800"),
                                                      ("DM-DEMO-02", "OBS-DEMO-02", "43600")):
        source = observations[observation_id]
        e2.record_derived_measurement(connection, compute_derived_measurement(
            measurement_id, Transformation.PERCENT_CHANGE_FROM_TWO_PRICES,
            (MeasurementInput(source, "baseline_price", Decimal("40000"), "USD per year"),
             MeasurementInput(source, "new_price", Decimal(new_price), "USD per year")), release.RECORDED_AT),
            recorded_at=release.RECORDED_AT, recorded_by=release.ACTOR)
    signal = SignalCandidate("SC-RC-001", release.PLAN_ID, 1, (observations["OBS-DEMO-02"], observations["OBS-DEMO-03"]),
                             release.ACTOR, frozenset({D.NUMERIC_MAGNITUDE, D.RENEWAL_OR_EFFECTIVE_DATE_MATCH}))
    e2.record_signal_candidate(connection, signal, recorded_at=release.RECORDED_AT, recorded_by=release.ACTOR)
    e2.record_correspondence_candidate(connection, CorrespondenceCandidate(release.CANDIDATE_ID, handoff.baec_id,
                                                                           release.PLAN_ID, 1, (signal,)),
                                       recorded_at=release.RECORDED_AT, recorded_by=release.ACTOR)


def new_visitor_seed() -> VisitorSeed:
    """A fresh, isolated, deterministic visitor state, built in memory."""
    engine1 = open_database(":memory:")
    reader = open_database(":memory:")
    engine2 = e2.open_engine2_database(":memory:")
    try:
        baec_id = release.confirm_in_engine1(engine1)
        engine1_image = engine1.serialize()
        reader.deserialize(engine1_image)
        make_read_only(reader)
        handoff = handoff_boundary.read_confirmed_baec(reader, baec_id)
        if reader.serialize() != engine1_image:
            raise RuntimeError("the Engine 1 store changed during the handoff")
        seed_engine2(engine2, handoff)
        e2.verify_database(engine2)
        return VisitorSeed(engine1_image, engine2.serialize(), handoff, stage_j.SELECTION.account_name,
                           stage_j.SELECTION.supplier_name, release.PLAN_ID, 1, release.ACTIVATION_ID,
                           release.CANDIDATE_ID, release.REVIEWED_AT)
    finally:
        engine1.close()
        reader.close()
        engine2.close()
