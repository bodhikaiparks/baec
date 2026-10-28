"""The fixed synthetic Engine 2 demonstration context, recorded only through the locked Stage D writers.

Everything here is invented for demonstration: the account, the supplier, the buyer statement, and every source
item. It is not taken from the research corpus and carries no expected review answer: the human reviewer decides
every finding, and the locked classifier derives the result. Timestamps are fixed, so the seed is deterministic.

The demo store is an in-memory SQLite database created per operator session. Nothing is written to disk, and no
other database (Engine 1, research, or AI) is ever opened.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from baec_app.domain.enums import ProvenanceCategory, StalenessStatus, ThresholdComparator

from .. import persistence as store
from ..ai import BaecConditionInput
from ..domain import (AuthorizedSource, CorrespondenceCandidate, CorrespondenceDimension, Engine1CurrentnessReading,
                      MonitoringPlan, SignalCandidate, SourceItem, Supersession, SupersessionBasis, ThresholdSpec,
                      activate_monitoring_plan, capture_observation, content_integrity_sha256, reject_source_item)
from ..measurement import MeasurementInput, Transformation, compute_derived_measurement

ACTOR = "DEMO-SEED (synthetic operator)"
SELECTED_BY = "DEMO-ANALYST (synthetic operator)"
AUTHORIZED_AT = datetime(2027, 3, 1, 9, 0, tzinfo=timezone.utc)
RECORDED_AT = datetime(2027, 6, 1, 9, 0, tzinfo=timezone.utc)

BAEC_ID = "BAEC-DEMO-001"
PLAN_ID = "MP-DEMO-001"
PLAN_VERSION = 1
ACTIVATION_ID = "ACT-DEMO-001"
CANDIDATE_ID = "CC-DEMO-001"
BUYER_STATEMENT = ("If Cobalt raises the price of our imaging service contract by more than 8% at the July renewal, "
                   "we would put the contract out for review.")
NOTICES = "SRC-DEMO-SUPPLIER-NOTICES"
NEWS = "SRC-DEMO-SUPPLIER-NEWS"


@dataclass(frozen=True)
class DemoSelection:
    """The one explicit, fixed demo selection. Nothing is chosen behind the operator's back."""

    account_name: str
    supplier_name: str
    monitoring_plan_id: str
    plan_version: int
    baec_id: str
    activation_id: str
    candidate_id: str


SELECTION = DemoSelection("Alder Ridge Clinics (synthetic)", "Cobalt Imaging Supply (synthetic)", PLAN_ID, PLAN_VERSION,
                          BAEC_ID, ACTIVATION_ID, CANDIDATE_ID)

# The confirmed BAEC's condition as Engine 1 would supply it. Used only to check an advisory artifact's exact input.
CONDITION = BaecConditionInput(baec_id=BAEC_ID, condition_reference=BUYER_STATEMENT, buyer_exact_statement=BUYER_STATEMENT)

SOURCES = (
    AuthorizedSource(NOTICES, "supplier renewal notice", "Cobalt Imaging Supply (synthetic)",
                     "forwarded by the buyer under the account agreement", "buyer forwards notices by email",
                     "notice text and dates", "DEMO-ACCOUNT-MANAGER (synthetic)", AUTHORIZED_AT),
    AuthorizedSource(NEWS, "supplier public announcement", "Cobalt Imaging Supply (synthetic)",
                     "public announcements on the supplier's news page", "manual capture of the public page",
                     "announcement text and dates", "DEMO-ACCOUNT-MANAGER (synthetic)", AUTHORIZED_AT,
                     known_limitations="announcements describe all customers, not this contract"),
)

D = CorrespondenceDimension
PLAN = MonitoringPlan(
    monitoring_plan_id=PLAN_ID, plan_version=PLAN_VERSION, baec_id=BAEC_ID, condition_reference=BUYER_STATEMENT,
    target_entities=("Cobalt Imaging Supply (synthetic)", "Alder Ridge Clinics (synthetic)"),
    required_dimensions=frozenset({D.ENTITY_MATCH, D.SOURCE_RELEVANCE, D.EVENT_OR_CONDITION_TYPE, D.THRESHOLD_MATCH,
                                   D.COMPARATOR_MATCH, D.NUMERIC_MAGNITUDE, D.UNIT_MATCH,
                                   D.RENEWAL_OR_EFFECTIVE_DATE_MATCH}),
    not_applicable_dimensions=frozenset({D.RELATIONSHIP_MATCH, D.TIMING_MATCH, D.CAUSAL_OR_CONTEXTUAL_RELEVANCE}),
    authorized_source_ids=frozenset({NOTICES, NEWS}), engine1_currentness_reference="E1-CURRENTNESS-DEMO-001",
    authorized_by="DEMO-ACCOUNT-MANAGER (synthetic)", authorization_reference="DEMO-PLAN-AUTHORIZATION-001",
    authorized_at=AUTHORIZED_AT, threshold=ThresholdSpec(ThresholdComparator.GREATER_THAN, Decimal("8"), "%"),
    timing_context="At the contract renewal on 1 July 2027",
    plan_fact_ids=frozenset({"FACT-DEMO-RENEWAL-DATE-2027-07-01"}),
    plan_rule_ids=frozenset({"RULE-DEMO-CONTRACT-NOTICE-OVER-ANNOUNCEMENT"}),
)
READING = Engine1CurrentnessReading(BAEC_ID, StalenessStatus.CURRENT, "E1-CURRENTNESS-DEMO-001")

# (observation id, source id, provenance, received, content, effective)
_ITEMS = (
    ("OBS-DEMO-01", NOTICES, ProvenanceCategory.BUYER_FACT, datetime(2027, 5, 3, 10, 0, tzinfo=timezone.utc),
     "Renewal notice for contract AR-2207: from 1 July 2027 the annual service fee changes from USD 40,000 to "
     "USD 44,800.", datetime(2027, 7, 1, tzinfo=timezone.utc)),
    ("OBS-DEMO-02", NOTICES, ProvenanceCategory.BUYER_FACT, datetime(2027, 5, 10, 10, 0, tzinfo=timezone.utc),
     "Correction to our renewal notice of 3 May for contract AR-2207: from 1 July 2027 the annual service fee "
     "changes from USD 40,000 to USD 43,600. The 3 May figure was issued in error.",
     datetime(2027, 7, 1, tzinfo=timezone.utc)),
    ("OBS-DEMO-03", NEWS, ProvenanceCategory.EXTERNAL_EVIDENCE, datetime(2027, 4, 20, 8, 0, tzinfo=timezone.utc),
     "Cobalt Imaging Supply announces service price adjustments of up to 10% for contracts renewing in the second "
     "half of 2027.", None),
    ("OBS-DEMO-04", NEWS, ProvenanceCategory.EXTERNAL_EVIDENCE, datetime(2027, 4, 12, 8, 0, tzinfo=timezone.utc),
     "Cobalt Imaging Supply will hold service prices flat for existing customers through 2027.", None),
    ("OBS-DEMO-05", NEWS, ProvenanceCategory.EXTERNAL_EVIDENCE, datetime(2027, 4, 14, 8, 0, tzinfo=timezone.utc),
     "Cobalt Imaging Supply retracts its 12 April statement about holding service prices flat; that statement was "
     "published in error.", None),
)
_REFUSED = SourceItem("ITEM-DEMO-FORUM-01", "SRC-DEMO-INDUSTRY-FORUM", "industry forum post", "manual capture",
                      datetime(2027, 5, 12, 8, 0, tzinfo=timezone.utc),
                      "Forum post: heard Cobalt is raising service prices 15% for everyone this year.")


def seed_demo(connection) -> None:
    """Record the demo context into an empty Engine 2 store, append-only, through Stage D only."""
    for source in SOURCES:
        store.record_authorized_source(connection, source, recorded_at=RECORDED_AT, recorded_by=ACTOR)
    store.record_monitoring_plan(connection, PLAN, recorded_at=RECORDED_AT, recorded_by=ACTOR)
    store.record_plan_activation(connection, PLAN_ID, PLAN_VERSION, READING, activation_id=ACTIVATION_ID,
                                 recorded_at=RECORDED_AT, recorded_by=ACTOR)
    active = activate_monitoring_plan(PLAN, READING)
    sources = {s.source_id: s for s in SOURCES}
    observations = {}
    for observation_id, source_id, provenance, received, content, effective in _ITEMS:
        item = SourceItem(f"ITEM-{observation_id}", source_id, sources[source_id].source_type, "manual capture",
                          received, content, published_at=received, effective_at=effective)
        observation = capture_observation(active, sources, item, observation_id=observation_id, provenance=provenance,
                                          content_sha256=content_integrity_sha256(content),
                                          source_locator=f"{source_id}/{item.item_id}",
                                          evidence_event_id=f"EVENT-{observation_id}")
        store.record_observation(connection, observation, activation_id=ACTIVATION_ID, recorded_at=RECORDED_AT,
                                 recorded_by=ACTOR)
        observations[observation_id] = observation
    refusal = reject_source_item(active, _REFUSED, "source SRC-DEMO-INDUSTRY-FORUM is not authorized for this plan")
    store.record_source_refusal(connection, refusal, activation_id=ACTIVATION_ID, source_reference=_REFUSED.source_id,
                                recorded_at=RECORDED_AT, recorded_by=ACTOR)
    correction = SupersessionBasis("explicit correction or retraction language")
    for old, new, retraction in (("OBS-DEMO-01", "OBS-DEMO-02", False), ("OBS-DEMO-04", "OBS-DEMO-05", True)):
        store.record_supersession(connection, Supersession(old, new, correction, retraction), recorded_at=RECORDED_AT,
                                  recorded_by=ACTOR)
    for measurement_id, observation_id, new_price in (("DM-DEMO-01", "OBS-DEMO-01", "44800"),
                                                      ("DM-DEMO-02", "OBS-DEMO-02", "43600")):
        source = observations[observation_id]
        measurement = compute_derived_measurement(
            measurement_id, Transformation.PERCENT_CHANGE_FROM_TWO_PRICES,
            (MeasurementInput(source, "baseline_price", Decimal("40000"), "USD per year"),
             MeasurementInput(source, "new_price", Decimal(new_price), "USD per year")), RECORDED_AT)
        store.record_derived_measurement(connection, measurement, recorded_at=RECORDED_AT, recorded_by=ACTOR)
    signal = SignalCandidate("SC-DEMO-001", PLAN_ID, PLAN_VERSION,
                             (observations["OBS-DEMO-02"], observations["OBS-DEMO-03"]), SELECTED_BY,
                             frozenset({D.NUMERIC_MAGNITUDE, D.RENEWAL_OR_EFFECTIVE_DATE_MATCH}))
    store.record_signal_candidate(connection, signal, recorded_at=RECORDED_AT, recorded_by=ACTOR)
    store.record_correspondence_candidate(connection, CorrespondenceCandidate(CANDIDATE_ID, BAEC_ID, PLAN_ID,
                                                                              PLAN_VERSION, (signal,)),
                                          recorded_at=RECORDED_AT, recorded_by=ACTOR)


def new_demo_image() -> bytes:
    """A freshly seeded in-memory demo store, as a serialized SQLite image."""
    connection = store.open_engine2_database(":memory:")
    try:
        seed_demo(connection)
        store.verify_database(connection)
        return connection.serialize()
    finally:
        connection.close()


def open_image(image: bytes):
    """Open a serialized Engine 2 image in memory and verify its schema. The caller closes it."""
    connection = store.open_engine2_database(":memory:")
    try:
        connection.deserialize(image)
        store.require_engine2_schema(connection)
        if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
            raise store.Engine2SchemaError("foreign key enforcement is off after restoring the image")
    except BaseException:
        connection.close()
        raise
    return connection
