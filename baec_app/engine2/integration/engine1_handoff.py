"""Stage K: the read-only handoff of one confirmed Engine 1 BAEC into Engine 2.

Engine 1 stays authoritative for BAEC creation, confirmation, and currentness; Engine 2 for monitoring and
correspondence after the handoff. This module only reads Engine 1 and builds Engine 2 inputs:

* read_confirmed_baec reads one BAEC through a query-only Engine 1 connection and the locked Engine 1 Repository,
  which rebuilds the record through the Engine 1 domain constructors and re-verifies it against the locked
  classification rules (fail closed on corruption). Eligibility is exactly Engine 1's own: classification
  CONFIRMED_BAEC, which the Engine 1 domain allows only with a CONFIRM_BAEC human authorization for that BAEC.
  Anything else (no record, NOT_BAEC, INSUFFICIENT_EVIDENCE) is refused. Nothing is written, no grant is touched,
  and no confirmation is rerun;
* the ConfirmedBaecHandoff carries only authoritative confirmed facts, exactly as stored: identity, the source
  excerpt, the buyer's exact statement, the four criterion assessments with their evidence, the stringency
  expression (verbatim text, comparator, number, unit, qualitative term, recurrence, timing), the confirmation
  (who, when, and its authorization row), and the staleness status as Engine 1 stores it. The AI-derived
  normalized condition never crosses. No text is normalized;
* monitoring_plan_from_handoff combines those facts with separately supplied Engine 2 planning choices
  (PlanChoices). The BAEC, condition reference, threshold, timing, and Engine 1 reference come only from the
  handoff; PlanChoices has no field for them, so a caller cannot override them;
* currentness_reading reports Engine 1's stored staleness status as an Engine1CurrentnessReading, after checking
  that the plan agrees with the handoff. Engine 2's locked activation rule then decides; nothing here declares a
  BAEC current;
* condition_input builds the existing Stage F BaecConditionInput from the handoff.

The handoff is a snapshot of what was read. It never creates an opportunity, changes account state, authorizes
contact, or calls a model.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from baec_app.data.database import SCHEMA_VERSION as ENGINE1_SCHEMA_VERSION
from baec_app.data.database import RepositoryNotFoundError, require_current_schema
from baec_app.data.repository import Repository
from baec_app.domain.enums import AuthorizationAction, BaecClassification, StalenessStatus
from baec_app.domain.models import CriterionAssessment, EvidenceExcerpt, StringencyExpression

from ..ai import BaecConditionInput
from ..domain import CorrespondenceDimension, Engine1CurrentnessReading, MonitoringPlan, ThresholdSpec
from ..errors import Engine2Error

HANDOFF_VERSION = "baec-e1-e2-confirmed-handoff/v1"
_READER = object()  # only read_confirmed_baec builds a handoff


class HandoffRefused(Engine2Error):
    """Engine 1 does not hold a confirmed BAEC that can cross, or a plan disagrees with the handoff."""


@dataclass(frozen=True)
class ConfirmedBaecHandoff:
    """One confirmed Engine 1 BAEC, exactly as Engine 1 stores it. Built only by read_confirmed_baec."""

    baec_id: str
    account_id: str
    source_interaction_id: str
    captured_at: datetime
    source_excerpt: EvidenceExcerpt
    buyer_exact_statement: str | None
    buyer_role: str | None
    articulation_origin: str
    elicitation_mode: str
    criteria: tuple[CriterionAssessment, ...]
    stringency: StringencyExpression | None
    confirmed_by: str
    confirmed_at: datetime
    confirmation_authorization_id: int
    staleness_status: StalenessStatus
    engine1_schema_version: int
    digest: str
    _source: object = None

    def __post_init__(self) -> None:
        if self._source is not _READER:
            raise HandoffRefused("a ConfirmedBaecHandoff is built only by reading Engine 1 (read_confirmed_baec)")
        if _digest(self) != self.digest:
            raise HandoffRefused("the handoff digest does not match its content")

    @property
    def engine1_reference(self) -> str:
        """The plan's Engine 1 reference: the BAEC, the confirmation row, and the exact handed-off content."""
        return (f"engine1:baec/{self.baec_id}/confirmation/{self.confirmation_authorization_id}"
                f"/handoff/{HANDOFF_VERSION}/{self.digest}")


def _excerpt(e: EvidenceExcerpt) -> dict:
    return {"text": e.text, "provenance": e.provenance.value, "source_id": e.source_id}


def _digest(h: ConfirmedBaecHandoff) -> str:
    s = h.stringency
    material = {
        "version": HANDOFF_VERSION, "baec_id": h.baec_id, "account_id": h.account_id,
        "source_interaction_id": h.source_interaction_id, "captured_at": h.captured_at.isoformat(),
        "source_excerpt": _excerpt(h.source_excerpt), "buyer_exact_statement": h.buyer_exact_statement,
        "buyer_role": h.buyer_role, "articulation_origin": h.articulation_origin, "elicitation_mode": h.elicitation_mode,
        "criteria": [{"criterion": a.criterion.value, "finding": a.finding.value, "rationale": a.rationale,
                      "evidence": [_excerpt(e) for e in a.evidence]} for a in h.criteria],
        "stringency": None if s is None else {
            "verbatim_text": s.verbatim_text, "comparator": s.comparator.value if s.comparator else None,
            "numeric_value": None if s.numeric_value is None else str(s.numeric_value), "unit": s.unit,
            "qualitative_term": s.qualitative_term, "recurrence_text": s.recurrence_text, "timing_text": s.timing_text},
        "confirmed_by": h.confirmed_by, "confirmed_at": h.confirmed_at.isoformat(),
        "confirmation_authorization_id": h.confirmation_authorization_id, "staleness_status": h.staleness_status.value,
        "engine1_schema_version": h.engine1_schema_version,
    }
    text = json.dumps(material, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def read_confirmed_baec(read_connection: sqlite3.Connection, baec_id: str) -> ConfirmedBaecHandoff:
    """Read one confirmed BAEC from Engine 1, or raise. Requires a query-only connection; writes nothing."""
    if read_connection.execute("PRAGMA query_only").fetchone()[0] != 1:
        raise HandoffRefused("the Engine 1 handoff reads only through a query-only connection")
    require_current_schema(read_connection)
    try:
        record = Repository(read_connection).get_baec_record(baec_id)
    except RepositoryNotFoundError as error:
        raise HandoffRefused(f"Engine 1 holds no BAEC record {baec_id!r}") from error
    if record.classification is not BaecClassification.CONFIRMED_BAEC:
        raise HandoffRefused(f"Engine 1 BAEC {baec_id!r} is {record.classification.value}, not CONFIRMED_BAEC")
    confirmation = record.confirmation
    if confirmation is None or confirmation.action is not AuthorizationAction.CONFIRM_BAEC \
            or confirmation.subject_id != baec_id or record.staleness_status is None:
        raise HandoffRefused(f"Engine 1 BAEC {baec_id!r} carries no confirmation for itself")
    (authorization_id,) = read_connection.execute(
        "SELECT confirmation_authorization_id FROM baec_records WHERE baec_id = ?", (baec_id,)).fetchone()
    c = record.candidate
    fields = dict(
        baec_id=record.baec_id, account_id=c.account_id, source_interaction_id=c.source_interaction_id,
        captured_at=record.captured_at, source_excerpt=c.source_excerpt, buyer_exact_statement=c.buyer_exact_statement,
        buyer_role=c.buyer_role, articulation_origin=c.articulation_origin.value, elicitation_mode=c.elicitation_mode.value,
        criteria=c.assessments, stringency=c.stringency, confirmed_by=confirmation.authorized_by,
        confirmed_at=confirmation.authorized_at, confirmation_authorization_id=authorization_id,
        staleness_status=record.staleness_status, engine1_schema_version=ENGINE1_SCHEMA_VERSION,
    )
    unsealed = object.__new__(ConfirmedBaecHandoff)
    for name, value in fields.items():
        object.__setattr__(unsealed, name, value)
    return ConfirmedBaecHandoff(**fields, digest=_digest(unsealed), _source=_READER)


# --- Engine 2 side ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class PlanChoices:
    """The new Engine 2 monitoring-plan choices a human makes. Never a BAEC fact."""

    monitoring_plan_id: str
    plan_version: int
    target_entities: tuple[str, ...]
    required_dimensions: frozenset[CorrespondenceDimension]
    not_applicable_dimensions: frozenset[CorrespondenceDimension]
    authorized_source_ids: frozenset[str]
    authorized_by: str
    authorization_reference: str
    authorized_at: datetime
    plan_fact_ids: frozenset[str] = frozenset()
    plan_rule_ids: frozenset[str] = frozenset()


def _require_handoff(handoff: object) -> ConfirmedBaecHandoff:
    if type(handoff) is not ConfirmedBaecHandoff:
        raise HandoffRefused("a ConfirmedBaecHandoff read from Engine 1 is required")
    return handoff


def _condition_reference(handoff: ConfirmedBaecHandoff) -> str:
    if handoff.buyer_exact_statement is None:
        raise HandoffRefused(f"Engine 1 BAEC {handoff.baec_id!r} has no exact buyer statement; Engine 2 monitors the "
                             "buyer's own words and never substitutes a paraphrase")
    return handoff.buyer_exact_statement


def _threshold(handoff: ConfirmedBaecHandoff) -> ThresholdSpec | None:
    s = handoff.stringency
    if s is None or s.numeric_value is None:
        return None  # no number was stated: there is no numeric threshold to hand off
    if s.unit is None:
        raise HandoffRefused(f"Engine 1 BAEC {handoff.baec_id!r} states a number without a unit; Engine 2 cannot "
                             "represent that threshold exactly and does not invent a unit")
    return ThresholdSpec(s.comparator, Decimal(s.numeric_value), s.unit)


def monitoring_plan_from_handoff(handoff: ConfirmedBaecHandoff, choices: PlanChoices) -> MonitoringPlan:
    """An Engine 2 MonitoringPlan: BAEC facts from the handoff, monitoring choices from the human."""
    h = _require_handoff(handoff)
    if type(choices) is not PlanChoices:
        raise HandoffRefused("the Engine 2 planning choices must be a PlanChoices")
    return MonitoringPlan(
        monitoring_plan_id=choices.monitoring_plan_id, plan_version=choices.plan_version, baec_id=h.baec_id,
        condition_reference=_condition_reference(h), target_entities=choices.target_entities,
        required_dimensions=choices.required_dimensions, not_applicable_dimensions=choices.not_applicable_dimensions,
        authorized_source_ids=choices.authorized_source_ids, engine1_currentness_reference=h.engine1_reference,
        authorized_by=choices.authorized_by, authorization_reference=choices.authorization_reference,
        authorized_at=choices.authorized_at, threshold=_threshold(h),
        timing_context=h.stringency.timing_text if h.stringency else None,
        plan_fact_ids=choices.plan_fact_ids, plan_rule_ids=choices.plan_rule_ids,
    )


def require_plan_matches(plan: MonitoringPlan, handoff: ConfirmedBaecHandoff) -> None:
    """Refuse a plan whose BAEC facts differ in any way from the handoff."""
    h = _require_handoff(handoff)
    if not isinstance(plan, MonitoringPlan):
        raise HandoffRefused("a MonitoringPlan is required")
    expected = (h.baec_id, _condition_reference(h), _threshold(h), h.stringency.timing_text if h.stringency else None,
                h.engine1_reference)
    found = (plan.baec_id, plan.condition_reference, plan.threshold, plan.timing_context,
             plan.engine1_currentness_reference)
    if found != expected:
        raise HandoffRefused(f"Monitoring Plan {plan.monitoring_plan_id} v{plan.plan_version} does not carry exactly "
                             f"the BAEC facts of Engine 1 BAEC {h.baec_id!r}")


def currentness_reading(plan: MonitoringPlan, handoff: ConfirmedBaecHandoff) -> Engine1CurrentnessReading:
    """Engine 1's stored staleness status, for Engine 2's locked activation rule to judge."""
    require_plan_matches(plan, handoff)
    return Engine1CurrentnessReading(handoff.baec_id, handoff.staleness_status, handoff.engine1_reference)


def condition_input(handoff: ConfirmedBaecHandoff) -> BaecConditionInput:
    """The existing Stage F BaecConditionInput, from the handoff. The AI-derived normalized condition never crosses."""
    h = _require_handoff(handoff)
    statement = _condition_reference(h)
    return BaecConditionInput(baec_id=h.baec_id, condition_reference=statement, buyer_exact_statement=statement,
                              normalized_condition=None)
