"""Engine 2 persistence and audit history (Stage D): a separate, append-only SQLite store.

This store is isolated from Engine 1. It is a different database file with
its own schema (engine BAEC_ENGINE_2, schema version 1); it never opens,
reads, or writes the Engine 1 database, and Engine 1 identifiers (BAEC,
confirmation, currentness record) are stored as opaque references only.

Persistence adapts to the locked Stage C domain, never the reverse:

* every write takes validated domain objects, and every read rebuilds them
  through the domain constructors, so Stage C validation runs again on load
  (content hashes, Derived Measurement recomputation, review consistency);
* authoritative rows are append-only: triggers refuse UPDATE, DELETE, and any
  insert that would collide with an existing key, and there is no delete API;
* a duplicate identifier is refused (DuplicateRecordError), never replaced;
* the correspondence outcome is stored only as a snapshot computed here by the
  Stage C classifier, recomputed on every load, and refused on any mismatch.
  No function accepts an outcome;
* each write is one transaction with its audit event: all of it, or none;
* every timestamp is supplied by the caller; nothing reads the clock;
* a Monitoring Plan is identified by (monitoring_plan_id, plan_version). Each
  version is immutable; activation, source authorization, evidence, candidates,
  and reviews bind to one exact version, and composite keys forbid mixing them.

It interprets no source text, performs no AI reasoning, accesses no source,
and takes no seller action.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Iterator

from baec_app.domain.enums import ProvenanceCategory, StalenessStatus, ThresholdComparator

from .classification import (
    CrossCuttingCheck,
    DimensionAssessment,
    EvidenceLedger,
    HumanCorrespondenceReview,
    classify_correspondence,
)
from .domain import (
    ActiveMonitoringPlan,
    AuthorizedSource,
    CorrespondenceCandidate,
    CorrespondenceDimension,
    CorrespondenceOutcome,
    CrossCuttingCheckName,
    DimensionFinding,
    Engine1CurrentnessReading,
    MonitoringPlan,
    Observation,
    RejectedSourceItem,
    SignalCandidate,
    SufficiencyFinding,
    Supersession,
    SupersessionBasis,
    ThresholdSpec,
    activate_monitoring_plan,
    plan_key,
)
from .errors import Engine2Error, MonitoringPlanActivationError
from .measurement import DerivedMeasurement, MeasurementInput, Transformation

ENGINE = "BAEC_ENGINE_2"
SCHEMA_VERSION = 1
MINIMUM_SQLITE_VERSION = (3, 38, 0)


class Engine2PersistenceError(Exception):
    """Base class for Engine 2 storage failures."""


class Engine2SchemaError(Engine2PersistenceError):
    """Not an Engine 2 schema-version-1 database (or SQLite too old). Fail closed."""


class Engine2IntegrityError(Engine2PersistenceError):
    """Stored data is tampered, orphaned, or inconsistent with the locked domain. Nothing is returned."""


class DuplicateRecordError(Engine2PersistenceError):
    """An authoritative identifier already exists. Stored history is never replaced."""


class RecordNotFoundError(Engine2PersistenceError):
    """A referenced Engine 2 record does not exist."""


class Engine2WriteRefused(Engine2PersistenceError):
    """A write disagrees with records already stored (wrong plan, unstored ledger member, mismatch)."""


AUDIT_EVENT_TYPES = (
    "AUTHORIZED_SOURCE_RECORDED",
    "MONITORING_PLAN_RECORDED",
    "PLAN_ACTIVATION_RECORDED",
    "PLAN_ACTIVATION_REFUSED",
    "SOURCE_ITEM_REFUSED",
    "OBSERVATION_RECORDED",
    "SUPERSESSION_RECORDED",
    "DERIVED_MEASUREMENT_RECORDED",
    "SIGNAL_CANDIDATE_RECORDED",
    "CORRESPONDENCE_CANDIDATE_RECORDED",
    "HUMAN_REVIEW_RECORDED",
)

# --- schema -------------------------------------------------------------------


def _in(column: str, values) -> str:
    quoted = ", ".join("'" + (v.value if hasattr(v, "value") else v) + "'" for v in values)
    return f"{column} IN ({quoted})"


_DIMS = _in("dimension", CorrespondenceDimension)
_OBSERVATION_PROVENANCE = (ProvenanceCategory.BUYER_FACT, ProvenanceCategory.SELLER_OBSERVATION,
                           ProvenanceCategory.EXTERNAL_EVIDENCE)
_FLAG = "IN (0, 1)"

# Monitoring Plan identity is (monitoring_plan_id, plan_version): the id names the
# lineage, the version an immutable historical plan (Stage A Section 5). Every
# plan-scoped row carries both, and composite foreign keys keep evidence,
# candidates, and reviews inside the exact version they were recorded under.
_PV = "plan_version INTEGER NOT NULL CHECK (plan_version >= 1)"
_PLAN_FK = "FOREIGN KEY (monitoring_plan_id, plan_version) REFERENCES monitoring_plans (monitoring_plan_id, plan_version) ON DELETE RESTRICT"


def _obs_fk(column: str) -> str:
    return (f"FOREIGN KEY ({column}, monitoring_plan_id, plan_version) "
            "REFERENCES observations (observation_id, monitoring_plan_id, plan_version) ON DELETE RESTRICT")


_TABLES = {
    "engine2_meta": """(
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL)""",
    "authorized_sources": """(
        source_id TEXT PRIMARY KEY,
        source_type TEXT NOT NULL, owner_or_publisher TEXT NOT NULL, legitimate_basis TEXT NOT NULL,
        access_method TEXT NOT NULL, permitted_capture TEXT NOT NULL, authorized_by TEXT NOT NULL,
        authorized_at TEXT NOT NULL, known_limitations TEXT,
        recorded_at TEXT NOT NULL, recorded_by TEXT NOT NULL)""",
    "monitoring_plans": f"""(
        monitoring_plan_id TEXT NOT NULL,
        {_PV},
        baec_id TEXT NOT NULL, condition_reference TEXT NOT NULL,
        engine1_currentness_reference TEXT NOT NULL,
        authorized_by TEXT NOT NULL, authorization_reference TEXT NOT NULL, authorized_at TEXT NOT NULL,
        threshold_comparator TEXT CHECK (threshold_comparator IS NULL OR {_in("threshold_comparator", ThresholdComparator)}),
        threshold_value TEXT, threshold_unit TEXT,
        timing_context TEXT,
        recorded_at TEXT NOT NULL, recorded_by TEXT NOT NULL,
        PRIMARY KEY (monitoring_plan_id, plan_version),
        CHECK ((threshold_comparator IS NULL) = (threshold_value IS NULL)
               AND (threshold_value IS NULL) = (threshold_unit IS NULL)))""",
    "plan_target_entities": f"""(
        monitoring_plan_id TEXT NOT NULL, {_PV},
        position INTEGER NOT NULL CHECK (position >= 0),
        entity TEXT NOT NULL,
        PRIMARY KEY (monitoring_plan_id, plan_version, position),
        UNIQUE (monitoring_plan_id, plan_version, entity),
        {_PLAN_FK})""",
    "plan_dimensions": f"""(
        monitoring_plan_id TEXT NOT NULL, {_PV},
        dimension TEXT NOT NULL CHECK ({_DIMS}),
        requirement TEXT NOT NULL CHECK (requirement IN ('REQUIRED', 'NOT_APPLICABLE')),
        PRIMARY KEY (monitoring_plan_id, plan_version, dimension),
        {_PLAN_FK})""",
    "plan_references": f"""(
        monitoring_plan_id TEXT NOT NULL, {_PV},
        reference_id TEXT NOT NULL,
        kind TEXT NOT NULL CHECK (kind IN ('FACT', 'RULE')),
        PRIMARY KEY (monitoring_plan_id, plan_version, reference_id),
        {_PLAN_FK})""",
    "plan_authorized_sources": f"""(
        monitoring_plan_id TEXT NOT NULL, {_PV},
        source_id TEXT NOT NULL REFERENCES authorized_sources (source_id) ON DELETE RESTRICT,
        PRIMARY KEY (monitoring_plan_id, plan_version, source_id),
        {_PLAN_FK})""",
    "plan_activations": f"""(
        activation_id TEXT PRIMARY KEY,
        monitoring_plan_id TEXT NOT NULL, {_PV},
        result TEXT NOT NULL CHECK (result IN ('ACTIVATED', 'REFUSED')),
        reading_present INTEGER NOT NULL CHECK (reading_present {_FLAG}),
        reading_baec_id TEXT,
        reading_staleness_status TEXT CHECK (reading_staleness_status IS NULL OR {_in("reading_staleness_status", StalenessStatus)}),
        reading_record_reference TEXT,
        refusal_reason TEXT,
        recorded_at TEXT NOT NULL, recorded_by TEXT NOT NULL,
        CHECK ((result = 'ACTIVATED') = (refusal_reason IS NULL)),
        CHECK (reading_present = 1 OR (reading_baec_id IS NULL AND reading_staleness_status IS NULL
                                       AND reading_record_reference IS NULL)),
        UNIQUE (activation_id, monitoring_plan_id, plan_version),
        {_PLAN_FK})""",
    "observations": f"""(
        observation_id TEXT PRIMARY KEY,
        monitoring_plan_id TEXT NOT NULL, {_PV},
        activation_id TEXT NOT NULL,
        source_id TEXT NOT NULL,
        source_type TEXT NOT NULL,
        observed_at TEXT NOT NULL, published_at TEXT, effective_at TEXT,
        exact_evidence_content TEXT NOT NULL,
        source_locator TEXT NOT NULL,
        provenance TEXT NOT NULL CHECK ({_in("provenance", _OBSERVATION_PROVENANCE)}),
        content_sha256 TEXT NOT NULL CHECK (length(content_sha256) = 64),
        acquisition_method TEXT NOT NULL,
        authorization_reference TEXT NOT NULL,
        evidence_event_id TEXT NOT NULL,
        recorded_at TEXT NOT NULL, recorded_by TEXT NOT NULL,
        UNIQUE (observation_id, monitoring_plan_id, plan_version),
        FOREIGN KEY (activation_id, monitoring_plan_id, plan_version)
            REFERENCES plan_activations (activation_id, monitoring_plan_id, plan_version) ON DELETE RESTRICT,
        FOREIGN KEY (monitoring_plan_id, plan_version, source_id)
            REFERENCES plan_authorized_sources (monitoring_plan_id, plan_version, source_id) ON DELETE RESTRICT)""",
    "source_refusals": f"""(
        monitoring_plan_id TEXT NOT NULL, {_PV},
        item_id TEXT NOT NULL,
        activation_id TEXT NOT NULL,
        source_reference TEXT,
        reason TEXT NOT NULL,
        recorded_at TEXT NOT NULL, recorded_by TEXT NOT NULL,
        PRIMARY KEY (monitoring_plan_id, plan_version, item_id),
        FOREIGN KEY (activation_id, monitoring_plan_id, plan_version)
            REFERENCES plan_activations (activation_id, monitoring_plan_id, plan_version) ON DELETE RESTRICT)""",
    "supersessions": f"""(
        superseded_observation_id TEXT PRIMARY KEY,
        superseding_observation_id TEXT NOT NULL,
        monitoring_plan_id TEXT NOT NULL, {_PV},
        basis TEXT NOT NULL CHECK ({_in("basis", SupersessionBasis)}),
        retraction INTEGER NOT NULL CHECK (retraction {_FLAG}),
        recorded_at TEXT NOT NULL, recorded_by TEXT NOT NULL,
        CHECK (superseded_observation_id <> superseding_observation_id),
        UNIQUE (superseded_observation_id, monitoring_plan_id, plan_version),
        {_obs_fk("superseded_observation_id")},
        {_obs_fk("superseding_observation_id")})""",
    "derived_measurements": f"""(
        measurement_id TEXT PRIMARY KEY,
        monitoring_plan_id TEXT NOT NULL, {_PV},
        transformation TEXT NOT NULL CHECK ({_in("transformation", Transformation)}),
        transformation_version TEXT NOT NULL,
        formula TEXT NOT NULL,
        rounding_rule TEXT NOT NULL,
        output_value TEXT NOT NULL,
        output_unit TEXT NOT NULL,
        calculated_at TEXT NOT NULL,
        recorded_at TEXT NOT NULL, recorded_by TEXT NOT NULL,
        UNIQUE (measurement_id, monitoring_plan_id, plan_version),
        {_PLAN_FK})""",
    "measurement_inputs": f"""(
        measurement_id TEXT NOT NULL,
        monitoring_plan_id TEXT NOT NULL, {_PV},
        position INTEGER NOT NULL CHECK (position >= 0),
        observation_id TEXT NOT NULL,
        quantity TEXT NOT NULL, value TEXT NOT NULL, unit TEXT NOT NULL,
        PRIMARY KEY (measurement_id, position),
        FOREIGN KEY (measurement_id, monitoring_plan_id, plan_version)
            REFERENCES derived_measurements (measurement_id, monitoring_plan_id, plan_version) ON DELETE RESTRICT,
        {_obs_fk("observation_id")})""",
    "signal_candidates": f"""(
        signal_candidate_id TEXT PRIMARY KEY,
        monitoring_plan_id TEXT NOT NULL, {_PV},
        selected_by TEXT NOT NULL,
        recorded_at TEXT NOT NULL, recorded_by TEXT NOT NULL,
        UNIQUE (signal_candidate_id, monitoring_plan_id, plan_version),
        {_PLAN_FK})""",
    "signal_candidate_observations": f"""(
        signal_candidate_id TEXT NOT NULL,
        monitoring_plan_id TEXT NOT NULL, {_PV},
        position INTEGER NOT NULL CHECK (position >= 0),
        observation_id TEXT NOT NULL,
        PRIMARY KEY (signal_candidate_id, position),
        UNIQUE (signal_candidate_id, observation_id),
        FOREIGN KEY (signal_candidate_id, monitoring_plan_id, plan_version)
            REFERENCES signal_candidates (signal_candidate_id, monitoring_plan_id, plan_version) ON DELETE RESTRICT,
        {_obs_fk("observation_id")})""",
    "signal_candidate_dimensions": f"""(
        signal_candidate_id TEXT NOT NULL REFERENCES signal_candidates (signal_candidate_id) ON DELETE RESTRICT,
        dimension TEXT NOT NULL CHECK ({_DIMS}),
        PRIMARY KEY (signal_candidate_id, dimension))""",
    "correspondence_candidates": f"""(
        candidate_id TEXT PRIMARY KEY,
        baec_id TEXT NOT NULL,
        monitoring_plan_id TEXT NOT NULL, {_PV},
        recorded_at TEXT NOT NULL, recorded_by TEXT NOT NULL,
        UNIQUE (candidate_id, monitoring_plan_id, plan_version),
        {_PLAN_FK})""",
    "candidate_signals": f"""(
        candidate_id TEXT NOT NULL,
        monitoring_plan_id TEXT NOT NULL, {_PV},
        position INTEGER NOT NULL CHECK (position >= 0),
        signal_candidate_id TEXT NOT NULL,
        PRIMARY KEY (candidate_id, position),
        UNIQUE (candidate_id, signal_candidate_id),
        FOREIGN KEY (candidate_id, monitoring_plan_id, plan_version)
            REFERENCES correspondence_candidates (candidate_id, monitoring_plan_id, plan_version) ON DELETE RESTRICT,
        FOREIGN KEY (signal_candidate_id, monitoring_plan_id, plan_version)
            REFERENCES signal_candidates (signal_candidate_id, monitoring_plan_id, plan_version) ON DELETE RESTRICT)""",
    "reviews": f"""(
        review_id TEXT PRIMARY KEY,
        candidate_id TEXT NOT NULL,
        monitoring_plan_id TEXT NOT NULL, {_PV},
        activation_id TEXT NOT NULL,
        sufficiency TEXT NOT NULL CHECK ({_in("sufficiency", SufficiencyFinding)}),
        reviewer TEXT NOT NULL,
        reviewed_at TEXT NOT NULL,
        baec_revalidation_required INTEGER NOT NULL CHECK (baec_revalidation_required {_FLAG}),
        outcome_snapshot TEXT NOT NULL CHECK ({_in("outcome_snapshot", CorrespondenceOutcome)}),
        recorded_at TEXT NOT NULL, recorded_by TEXT NOT NULL,
        UNIQUE (review_id, monitoring_plan_id, plan_version),
        FOREIGN KEY (candidate_id, monitoring_plan_id, plan_version)
            REFERENCES correspondence_candidates (candidate_id, monitoring_plan_id, plan_version) ON DELETE RESTRICT,
        FOREIGN KEY (activation_id, monitoring_plan_id, plan_version)
            REFERENCES plan_activations (activation_id, monitoring_plan_id, plan_version) ON DELETE RESTRICT)""",
    "review_ledger_observations": f"""(
        review_id TEXT NOT NULL, monitoring_plan_id TEXT NOT NULL, {_PV},
        position INTEGER NOT NULL CHECK (position >= 0), observation_id TEXT NOT NULL,
        PRIMARY KEY (review_id, position), UNIQUE (review_id, observation_id),
        FOREIGN KEY (review_id, monitoring_plan_id, plan_version)
            REFERENCES reviews (review_id, monitoring_plan_id, plan_version) ON DELETE RESTRICT,
        {_obs_fk("observation_id")})""",
    "review_ledger_supersessions": f"""(
        review_id TEXT NOT NULL, monitoring_plan_id TEXT NOT NULL, {_PV},
        position INTEGER NOT NULL CHECK (position >= 0),
        superseded_observation_id TEXT NOT NULL,
        PRIMARY KEY (review_id, position), UNIQUE (review_id, superseded_observation_id),
        FOREIGN KEY (review_id, monitoring_plan_id, plan_version)
            REFERENCES reviews (review_id, monitoring_plan_id, plan_version) ON DELETE RESTRICT,
        FOREIGN KEY (superseded_observation_id, monitoring_plan_id, plan_version)
            REFERENCES supersessions (superseded_observation_id, monitoring_plan_id, plan_version) ON DELETE RESTRICT)""",
    "review_ledger_measurements": f"""(
        review_id TEXT NOT NULL, monitoring_plan_id TEXT NOT NULL, {_PV},
        position INTEGER NOT NULL CHECK (position >= 0), measurement_id TEXT NOT NULL,
        PRIMARY KEY (review_id, position), UNIQUE (review_id, measurement_id),
        FOREIGN KEY (review_id, monitoring_plan_id, plan_version)
            REFERENCES reviews (review_id, monitoring_plan_id, plan_version) ON DELETE RESTRICT,
        FOREIGN KEY (measurement_id, monitoring_plan_id, plan_version)
            REFERENCES derived_measurements (measurement_id, monitoring_plan_id, plan_version) ON DELETE RESTRICT)""",
    "review_ledger_refusals": f"""(
        review_id TEXT NOT NULL, monitoring_plan_id TEXT NOT NULL, {_PV},
        position INTEGER NOT NULL CHECK (position >= 0), item_id TEXT NOT NULL,
        PRIMARY KEY (review_id, position), UNIQUE (review_id, item_id),
        FOREIGN KEY (review_id, monitoring_plan_id, plan_version)
            REFERENCES reviews (review_id, monitoring_plan_id, plan_version) ON DELETE RESTRICT,
        FOREIGN KEY (monitoring_plan_id, plan_version, item_id)
            REFERENCES source_refusals (monitoring_plan_id, plan_version, item_id) ON DELETE RESTRICT)""",
    "review_findings": f"""(
        review_id TEXT NOT NULL REFERENCES reviews (review_id) ON DELETE RESTRICT,
        position INTEGER NOT NULL CHECK (position >= 0),
        dimension TEXT NOT NULL CHECK ({_DIMS}),
        finding TEXT NOT NULL CHECK ({_in("finding", DimensionFinding)}),
        reason TEXT NOT NULL,
        PRIMARY KEY (review_id, dimension), UNIQUE (review_id, position))""",
    "review_finding_evidence": """(
        review_id TEXT NOT NULL, dimension TEXT NOT NULL,
        position INTEGER NOT NULL CHECK (position >= 0), evidence_ref TEXT NOT NULL,
        PRIMARY KEY (review_id, dimension, position), UNIQUE (review_id, dimension, evidence_ref),
        FOREIGN KEY (review_id, dimension) REFERENCES review_findings (review_id, dimension) ON DELETE RESTRICT)""",
    "review_checks": f"""(
        review_id TEXT NOT NULL REFERENCES reviews (review_id) ON DELETE RESTRICT,
        position INTEGER NOT NULL CHECK (position >= 0),
        check_name TEXT NOT NULL CHECK ({_in("check_name", CrossCuttingCheckName)}),
        note TEXT NOT NULL,
        PRIMARY KEY (review_id, check_name), UNIQUE (review_id, position))""",
    "review_check_evidence": """(
        review_id TEXT NOT NULL, check_name TEXT NOT NULL,
        position INTEGER NOT NULL CHECK (position >= 0), evidence_ref TEXT NOT NULL,
        PRIMARY KEY (review_id, check_name, position), UNIQUE (review_id, check_name, evidence_ref),
        FOREIGN KEY (review_id, check_name) REFERENCES review_checks (review_id, check_name) ON DELETE RESTRICT)""",
    "review_check_dimensions": f"""(
        review_id TEXT NOT NULL, check_name TEXT NOT NULL,
        dimension TEXT NOT NULL CHECK ({_DIMS}),
        PRIMARY KEY (review_id, check_name, dimension),
        FOREIGN KEY (review_id, check_name) REFERENCES review_checks (review_id, check_name) ON DELETE RESTRICT)""",
    "review_revalidation_triggers": """(
        review_id TEXT NOT NULL REFERENCES reviews (review_id) ON DELETE RESTRICT,
        position INTEGER NOT NULL CHECK (position >= 0),
        trigger_text TEXT NOT NULL,
        PRIMARY KEY (review_id, position), UNIQUE (review_id, trigger_text))""",
    "audit_events": f"""(
        event_id INTEGER PRIMARY KEY,
        event_type TEXT NOT NULL CHECK ({_in("event_type", AUDIT_EVENT_TYPES)}),
        entity_type TEXT NOT NULL,
        entity_id TEXT NOT NULL,
        recorded_at TEXT NOT NULL,
        actor TEXT NOT NULL,
        details_json TEXT NOT NULL CHECK (json_valid(details_json)))""",
}

# Every key that identifies a stored row. An insert colliding on any of them is
# refused by trigger, so INSERT OR REPLACE can never overwrite history.
_KEYS = {
    "engine2_meta": ("key",),
    "authorized_sources": ("source_id",),
    "monitoring_plans": ("monitoring_plan_id", "plan_version"),
    "plan_target_entities": ("monitoring_plan_id", "plan_version", "position"),
    "plan_dimensions": ("monitoring_plan_id", "plan_version", "dimension"),
    "plan_references": ("monitoring_plan_id", "plan_version", "reference_id"),
    "plan_authorized_sources": ("monitoring_plan_id", "plan_version", "source_id"),
    "plan_activations": ("activation_id",),
    "observations": ("observation_id",),
    "source_refusals": ("monitoring_plan_id", "plan_version", "item_id"),
    "supersessions": ("superseded_observation_id",),
    "derived_measurements": ("measurement_id",),
    "measurement_inputs": ("measurement_id", "position"),
    "signal_candidates": ("signal_candidate_id",),
    "signal_candidate_observations": ("signal_candidate_id", "position"),
    "signal_candidate_dimensions": ("signal_candidate_id", "dimension"),
    "correspondence_candidates": ("candidate_id",),
    "candidate_signals": ("candidate_id", "position"),
    "reviews": ("review_id",),
    "review_ledger_observations": ("review_id", "position"),
    "review_ledger_supersessions": ("review_id", "position"),
    "review_ledger_measurements": ("review_id", "position"),
    "review_ledger_refusals": ("review_id", "position"),
    "review_findings": ("review_id", "dimension"),
    "review_finding_evidence": ("review_id", "dimension", "position"),
    "review_checks": ("review_id", "check_name"),
    "review_check_evidence": ("review_id", "check_name", "position"),
    "review_check_dimensions": ("review_id", "check_name", "dimension"),
    "review_revalidation_triggers": ("review_id", "position"),
    "audit_events": ("event_id",),
}
TABLES = tuple(_TABLES)


def _schema_statements() -> list[str]:
    statements = [f"CREATE TABLE {name} {body} STRICT" for name, body in _TABLES.items()]
    for table, key in _KEYS.items():
        for operation in ("UPDATE", "DELETE"):
            statements.append(
                f"CREATE TRIGGER {table}_no_{operation.lower()} BEFORE {operation} ON {table} "
                f"BEGIN SELECT RAISE(ABORT, '{table} is append-only'); END"
            )
        match = " AND ".join(f"{column} = NEW.{column}" for column in key)
        statements.append(
            f"CREATE TRIGGER {table}_no_replace BEFORE INSERT ON {table} "
            f"WHEN EXISTS (SELECT 1 FROM {table} WHERE {match}) "
            f"BEGIN SELECT RAISE(ABORT, '{table} rows cannot be replaced'); END"
        )
    for table in ("observations", "source_refusals"):
        statements.append(
            f"CREATE TRIGGER {table}_needs_activation BEFORE INSERT ON {table} "
            "WHEN COALESCE((SELECT result FROM plan_activations WHERE activation_id = NEW.activation_id), '') "
            "<> 'ACTIVATED' "
            f"BEGIN SELECT RAISE(ABORT, '{table} requires an ACTIVATED Monitoring Plan'); END"
        )
    return statements


# --- connection, schema version, transactions --------------------------------


def _connect(path: str) -> sqlite3.Connection:
    if sqlite3.sqlite_version_info < MINIMUM_SQLITE_VERSION:
        raise Engine2SchemaError("SQLite 3.38.0 or newer is required")
    connection = sqlite3.connect(str(path), isolation_level=None)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
            raise Engine2SchemaError("foreign key enforcement could not be enabled")
    except BaseException:
        connection.close()
        raise
    return connection


@contextmanager
def _transaction(connection: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    connection.execute("BEGIN IMMEDIATE")
    try:
        yield connection
        connection.execute("COMMIT")
    except BaseException as error:
        if connection.in_transaction:
            try:
                connection.execute("ROLLBACK")
            except sqlite3.Error as rollback_error:
                error.add_note(f"rolling back also failed: {rollback_error}")
        raise


def _is_empty(connection: sqlite3.Connection) -> bool:
    count = connection.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'").fetchone()[0]
    return count == 0 and connection.execute("PRAGMA user_version").fetchone()[0] == 0


def require_engine2_schema(connection: sqlite3.Connection) -> None:
    """Fail closed unless this is an Engine 2 schema-version-1 database."""
    try:
        meta = dict(connection.execute("SELECT key, value FROM engine2_meta").fetchall())
    except sqlite3.Error as error:
        raise Engine2SchemaError("not an Engine 2 database (no engine2_meta table)") from error
    version = connection.execute("PRAGMA user_version").fetchone()[0]
    if meta != {"engine": ENGINE, "schema_version": str(SCHEMA_VERSION)} or version != SCHEMA_VERSION:
        raise Engine2SchemaError(f"unsupported Engine 2 database: meta={meta}, user_version={version}")


def open_engine2_database(path: str = ":memory:") -> sqlite3.Connection:
    """Open (creating if new and empty) an isolated Engine 2 database. Never an Engine 1 file."""
    connection = _connect(path)
    try:
        if _is_empty(connection):
            with _transaction(connection):
                for statement in _schema_statements():
                    connection.execute(statement)
                connection.execute("INSERT INTO engine2_meta (key, value) VALUES ('engine', ?), ('schema_version', ?)",
                                   (ENGINE, str(SCHEMA_VERSION)))
                connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        require_engine2_schema(connection)
    except BaseException:
        connection.close()
        raise
    return connection


# --- value helpers ----------------------------------------------------------------


def _ts(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def _dt(value: str | None) -> datetime | None:
    return None if value is None else datetime.fromisoformat(value)


def _actor(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise Engine2WriteRefused("an audit actor (alias or reference) is required")
    return value


def _when(value: object) -> str:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise Engine2WriteRefused("recorded_at must be a timezone-aware datetime supplied by the caller")
    return value.isoformat()


def _exists(connection, table: str, **key) -> bool:
    where = " AND ".join(f"{column} = ?" for column in key)
    return connection.execute(f"SELECT 1 FROM {table} WHERE {where}", tuple(key.values())).fetchone() is not None


def _refuse_duplicate(connection, table: str, label: str, **key) -> None:
    if _exists(connection, table, **key):
        raise DuplicateRecordError(f"{label} {'/'.join(map(str, key.values()))} already exists; history is never replaced")


def _insert(connection, table: str, **values) -> None:
    columns = ", ".join(values)
    marks = ", ".join("?" for _ in values)
    connection.execute(f"INSERT INTO {table} ({columns}) VALUES ({marks})", tuple(values.values()))


def _audit(connection, event_type: str, entity_type: str, entity_id: str, recorded_at: str, actor: str, **details) -> None:
    _insert_audit_event(connection, event_type, entity_type, entity_id, recorded_at, actor,
                        json.dumps(details, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")))


def _insert_audit_event(connection, event_type, entity_type, entity_id, recorded_at, actor, details_json) -> None:
    _insert(connection, "audit_events", event_type=event_type, entity_type=entity_type, entity_id=entity_id,
            recorded_at=recorded_at, actor=actor, details_json=details_json)


@contextmanager
def _reading(label: str) -> Iterator[None]:
    """Rebuild domain objects; any validation failure is stored-data corruption."""
    try:
        yield
    except (Engine2Error, ValueError, TypeError, KeyError, ArithmeticError) as error:
        raise Engine2IntegrityError(f"{label}: stored data fails domain validation: {error}") from error


def _one(connection, sql: str, args: tuple, label: str):
    row = connection.execute(sql, args).fetchone()
    if row is None:
        raise RecordNotFoundError(f"{label} not found")
    return row


# --- Authorized Sources and Monitoring Plans --------------------------------------


def record_authorized_source(connection, source: AuthorizedSource, *, recorded_at: datetime, recorded_by: str) -> None:
    if not isinstance(source, AuthorizedSource):
        raise Engine2WriteRefused("record_authorized_source requires an AuthorizedSource")
    when, actor = _when(recorded_at), _actor(recorded_by)
    with _transaction(connection):
        _refuse_duplicate(connection, "authorized_sources", "Authorized Source", source_id=source.source_id)
        _insert(connection, "authorized_sources", source_id=source.source_id, source_type=source.source_type,
                owner_or_publisher=source.owner_or_publisher, legitimate_basis=source.legitimate_basis,
                access_method=source.access_method, permitted_capture=source.permitted_capture,
                authorized_by=source.authorized_by, authorized_at=_ts(source.authorized_at),
                known_limitations=source.known_limitations, recorded_at=when, recorded_by=actor)
        _audit(connection, "AUTHORIZED_SOURCE_RECORDED", "AuthorizedSource", source.source_id, when, actor)


def load_authorized_source(connection, source_id: str) -> AuthorizedSource:
    row = _one(connection, "SELECT source_id, source_type, owner_or_publisher, legitimate_basis, access_method, "
               "permitted_capture, authorized_by, authorized_at, known_limitations FROM authorized_sources "
               "WHERE source_id = ?", (source_id,), f"Authorized Source {source_id}")
    with _reading(f"Authorized Source {source_id}"):
        return AuthorizedSource(*row[:7], _dt(row[7]), row[8])


def load_authorized_sources(connection) -> dict[str, AuthorizedSource]:
    ids = [r[0] for r in connection.execute("SELECT source_id FROM authorized_sources ORDER BY rowid")]
    return {i: load_authorized_source(connection, i) for i in ids}


def record_monitoring_plan(connection, plan: MonitoringPlan, *, recorded_at: datetime, recorded_by: str) -> None:
    """Record one immutable plan version. Its sources must already be recorded.

    A new version is a new (monitoring_plan_id, plan_version) row; earlier
    versions are never changed. Activation is a separate, version-specific record.
    """
    if not isinstance(plan, MonitoringPlan):
        raise Engine2WriteRefused("record_monitoring_plan requires a MonitoringPlan")
    when, actor = _when(recorded_at), _actor(recorded_by)
    pid, ver = plan.monitoring_plan_id, plan.plan_version
    key = {"monitoring_plan_id": pid, "plan_version": ver}
    with _transaction(connection):
        _refuse_duplicate(connection, "monitoring_plans", "Monitoring Plan version", **key)
        t = plan.threshold
        _insert(connection, "monitoring_plans", **key, baec_id=plan.baec_id, condition_reference=plan.condition_reference,
                engine1_currentness_reference=plan.engine1_currentness_reference, authorized_by=plan.authorized_by,
                authorization_reference=plan.authorization_reference, authorized_at=_ts(plan.authorized_at),
                threshold_comparator=t.comparator.value if t else None, threshold_value=str(t.value) if t else None,
                threshold_unit=t.unit if t else None, timing_context=plan.timing_context,
                recorded_at=when, recorded_by=actor)
        for position, entity in enumerate(plan.target_entities):
            _insert(connection, "plan_target_entities", **key, position=position, entity=entity)
        for d in sorted(plan.required_dimensions | plan.not_applicable_dimensions, key=lambda x: x.value):
            _insert(connection, "plan_dimensions", **key, dimension=d.value,
                    requirement="REQUIRED" if d in plan.required_dimensions else "NOT_APPLICABLE")
        for ref in sorted(plan.plan_fact_ids):
            _insert(connection, "plan_references", **key, reference_id=ref, kind="FACT")
        for ref in sorted(plan.plan_rule_ids):
            _insert(connection, "plan_references", **key, reference_id=ref, kind="RULE")
        for source_id in sorted(plan.authorized_source_ids):
            _insert(connection, "plan_authorized_sources", **key, source_id=source_id)
        _audit(connection, "MONITORING_PLAN_RECORDED", "MonitoringPlan", _plan_ref(pid, ver), when, actor,
               monitoring_plan_id=pid, plan_version=ver, baec_id=plan.baec_id)


def _plan_ref(monitoring_plan_id: str, plan_version: int) -> str:
    return f"{monitoring_plan_id}@v{plan_version}"


def load_monitoring_plan(connection, monitoring_plan_id: str, plan_version: int) -> MonitoringPlan:
    """Load exactly one historical plan version."""
    pid, ver = monitoring_plan_id, plan_version
    row = _one(connection, "SELECT baec_id, condition_reference, engine1_currentness_reference, "
               "authorized_by, authorization_reference, authorized_at, threshold_comparator, threshold_value, "
               "threshold_unit, timing_context FROM monitoring_plans WHERE monitoring_plan_id = ? AND plan_version = ?",
               (pid, ver), f"Monitoring Plan {_plan_ref(pid, ver)}")
    args = (pid, ver)
    entities = [r[0] for r in connection.execute(
        "SELECT entity FROM plan_target_entities WHERE monitoring_plan_id = ? AND plan_version = ? ORDER BY position", args)]
    dims = connection.execute("SELECT dimension, requirement FROM plan_dimensions "
                              "WHERE monitoring_plan_id = ? AND plan_version = ?", args).fetchall()
    refs = connection.execute("SELECT reference_id, kind FROM plan_references "
                              "WHERE monitoring_plan_id = ? AND plan_version = ?", args).fetchall()
    sources = [r[0] for r in connection.execute(
        "SELECT source_id FROM plan_authorized_sources WHERE monitoring_plan_id = ? AND plan_version = ?", args)]
    with _reading(f"Monitoring Plan {_plan_ref(pid, ver)}"):
        threshold = None if row[6] is None else ThresholdSpec(ThresholdComparator(row[6]), Decimal(row[7]), row[8])
        return MonitoringPlan(
            monitoring_plan_id=pid, plan_version=ver, baec_id=row[0], condition_reference=row[1],
            target_entities=tuple(entities),
            required_dimensions=frozenset(CorrespondenceDimension(d) for d, req in dims if req == "REQUIRED"),
            not_applicable_dimensions=frozenset(CorrespondenceDimension(d) for d, req in dims if req == "NOT_APPLICABLE"),
            authorized_source_ids=frozenset(sources), engine1_currentness_reference=row[2], authorized_by=row[3],
            authorization_reference=row[4], authorized_at=_dt(row[5]), threshold=threshold, timing_context=row[9],
            plan_fact_ids=frozenset(r for r, k in refs if k == "FACT"),
            plan_rule_ids=frozenset(r for r, k in refs if k == "RULE"),
        )


def plan_versions(connection, monitoring_plan_id: str) -> list[int]:
    """Every stored version of a plan lineage, ascending."""
    return [r[0] for r in connection.execute("SELECT plan_version FROM monitoring_plans WHERE monitoring_plan_id = ? "
                                             "ORDER BY plan_version", (monitoring_plan_id,))]


def record_plan_activation(
    connection, monitoring_plan_id: str, plan_version: int, reading: Engine1CurrentnessReading | None, *,
    activation_id: str, recorded_at: datetime, recorded_by: str,
) -> ActiveMonitoringPlan:
    """Attempt to activate one exact plan version with the supplied Engine 1 reading; record the result either way.

    The Stage C rule decides; persistence never judges currentness and no
    activation carries over to another version. A refusal is recorded, then
    MonitoringPlanActivationError is raised.
    """
    when, actor = _when(recorded_at), _actor(recorded_by)
    plan = load_monitoring_plan(connection, monitoring_plan_id, plan_version)
    if reading is not None and not isinstance(reading, Engine1CurrentnessReading):
        raise Engine2WriteRefused("reading must be an Engine1CurrentnessReading or None")
    refusal: MonitoringPlanActivationError | None = None
    active = None
    try:
        active = activate_monitoring_plan(plan, reading)
    except MonitoringPlanActivationError as error:
        refusal = error
    with _transaction(connection):
        _refuse_duplicate(connection, "plan_activations", "Plan activation", activation_id=activation_id)
        _insert(connection, "plan_activations", activation_id=activation_id, monitoring_plan_id=monitoring_plan_id,
                plan_version=plan_version, result="REFUSED" if refusal else "ACTIVATED",
                reading_present=int(reading is not None), reading_baec_id=reading.baec_id if reading else None,
                reading_staleness_status=reading.staleness_status.value if reading and reading.staleness_status else None,
                reading_record_reference=reading.record_reference if reading else None,
                refusal_reason=str(refusal) if refusal else None, recorded_at=when, recorded_by=actor)
        _audit(connection, "PLAN_ACTIVATION_REFUSED" if refusal else "PLAN_ACTIVATION_RECORDED", "PlanActivation",
               activation_id, when, actor, monitoring_plan_id=monitoring_plan_id, plan_version=plan_version,
               **({"refusal_reason": str(refusal)} if refusal else {}))
    if refusal:
        raise refusal
    return active


def load_active_plan(connection, activation_id: str) -> ActiveMonitoringPlan:
    """Rebuild an activation by re-running the Stage C rule on the stored reading and exact plan version.

    A stored REFUSED activation raises MonitoringPlanActivationError. A stored
    result that disagrees with the rule is integrity failure.
    """
    row = _one(connection, "SELECT monitoring_plan_id, plan_version, result, reading_present, reading_baec_id, "
               "reading_staleness_status, reading_record_reference FROM plan_activations WHERE activation_id = ?",
               (activation_id,), f"Plan activation {activation_id}")
    plan = load_monitoring_plan(connection, row[0], row[1])
    with _reading(f"Plan activation {activation_id}"):
        reading = None if not row[3] else Engine1CurrentnessReading(
            row[4], None if row[5] is None else StalenessStatus(row[5]), row[6])
    try:
        active = activate_monitoring_plan(plan, reading)
    except MonitoringPlanActivationError:
        if row[2] != "REFUSED":
            raise Engine2IntegrityError(f"activation {activation_id} is stored ACTIVATED but the rule refuses it")
        raise
    if row[2] != "ACTIVATED":
        raise Engine2IntegrityError(f"activation {activation_id} is stored REFUSED but the rule activates it")
    return active


def _activated_plan(connection, activation_id: str) -> tuple[str, int]:
    row = _one(connection, "SELECT monitoring_plan_id, plan_version, result FROM plan_activations WHERE activation_id = ?",
               (activation_id,), f"Plan activation {activation_id}")
    if row[2] != "ACTIVATED":
        raise Engine2WriteRefused(f"activation {activation_id} was refused; nothing may be captured under it")
    load_active_plan(connection, activation_id)  # re-verify before relying on it
    return row[0], row[1]


def _plan_key_of(connection, table: str, id_column: str, record_id: str, label: str) -> tuple[str, int]:
    row = _one(connection, f"SELECT monitoring_plan_id, plan_version FROM {table} WHERE {id_column} = ?",
               (record_id,), f"{label} {record_id}")
    return row[0], row[1]


def _require_same_version(connection, key: tuple[str, int], table: str, id_column: str, ids, label: str) -> None:
    for record_id in ids:
        found = _plan_key_of(connection, table, id_column, record_id, label)
        if found != key:
            raise Engine2WriteRefused(
                f"{label} {record_id} belongs to {_plan_ref(*found)}, not {_plan_ref(*key)}; plan versions never mix")


# --- Observations, refusals, supersessions ---------------------------------------


def record_observation(connection, observation: Observation, *, activation_id: str, recorded_at: datetime, recorded_by: str) -> None:
    """Record a raw Observation captured under an ACTIVATED plan version from one of that version's sources."""
    if not isinstance(observation, Observation):
        raise Engine2WriteRefused("record_observation requires an Observation")
    when, actor = _when(recorded_at), _actor(recorded_by)
    plan_id, ver = _activated_plan(connection, activation_id)
    if plan_key(observation) != (plan_id, ver):
        raise Engine2WriteRefused(
            f"the Observation belongs to {_plan_ref(*plan_key(observation))}, but activation {activation_id} "
            f"is for {_plan_ref(plan_id, ver)}")
    if not _exists(connection, "plan_authorized_sources", monitoring_plan_id=plan_id, plan_version=ver,
                   source_id=observation.source_id):
        raise Engine2WriteRefused(f"source {observation.source_id} is not authorized for {_plan_ref(plan_id, ver)}")
    o = observation
    with _transaction(connection):
        _refuse_duplicate(connection, "observations", "Observation", observation_id=o.observation_id)
        _insert(connection, "observations", observation_id=o.observation_id, monitoring_plan_id=o.monitoring_plan_id,
                plan_version=o.plan_version,
                activation_id=activation_id, source_id=o.source_id, source_type=o.source_type,
                observed_at=_ts(o.observed_at), published_at=_ts(o.published_at), effective_at=_ts(o.effective_at),
                exact_evidence_content=o.exact_evidence_content, source_locator=o.source_locator,
                provenance=o.provenance.value, content_sha256=o.content_sha256, acquisition_method=o.acquisition_method,
                authorization_reference=o.authorization_reference, evidence_event_id=o.evidence_event_id,
                recorded_at=when, recorded_by=actor)
        _audit(connection, "OBSERVATION_RECORDED", "Observation", o.observation_id, when, actor,
               monitoring_plan_id=plan_id, plan_version=ver, source_id=o.source_id, content_sha256=o.content_sha256)


def observation_plan_version(connection, observation_id: str) -> tuple[str, int]:
    """The exact (monitoring_plan_id, plan_version) an Observation was captured under."""
    return _plan_key_of(connection, "observations", "observation_id", observation_id, "Observation")


def load_observation(connection, observation_id: str) -> Observation:
    row = _one(connection, "SELECT monitoring_plan_id, source_id, source_type, observed_at, exact_evidence_content, "
               "source_locator, provenance, content_sha256, acquisition_method, authorization_reference, "
               "evidence_event_id, published_at, effective_at, plan_version FROM observations WHERE observation_id = ?",
               (observation_id,), f"Observation {observation_id}")
    with _reading(f"Observation {observation_id}"):
        return Observation(
            observation_id=observation_id, monitoring_plan_id=row[0], plan_version=row[13], source_id=row[1],
            source_type=row[2],
            observed_at=_dt(row[3]), exact_evidence_content=row[4], source_locator=row[5],
            provenance=ProvenanceCategory(row[6]), content_sha256=row[7], acquisition_method=row[8],
            authorization_reference=row[9], evidence_event_id=row[10], published_at=_dt(row[11]),
            effective_at=_dt(row[12]),
        )


def record_source_refusal(
    connection, refusal: RejectedSourceItem, *, activation_id: str, source_reference: str | None,
    recorded_at: datetime, recorded_by: str,
) -> None:
    """Audit an item refused during capture. The refusal's own plan identity is authoritative and must
    equal the ACTIVATED activation's exact plan version. Not an Observation, not an outcome."""
    if not isinstance(refusal, RejectedSourceItem):
        raise Engine2WriteRefused("record_source_refusal requires a RejectedSourceItem")
    when, actor = _when(recorded_at), _actor(recorded_by)
    plan_id, ver = _activated_plan(connection, activation_id)
    if plan_key(refusal) != (plan_id, ver):
        raise Engine2WriteRefused(
            f"the refusal belongs to {_plan_ref(*plan_key(refusal))}, but activation {activation_id} "
            f"is for {_plan_ref(plan_id, ver)}")
    plan_id, ver = plan_key(refusal)
    with _transaction(connection):
        _refuse_duplicate(connection, "source_refusals", "Source refusal", monitoring_plan_id=plan_id, plan_version=ver,
                          item_id=refusal.item_id)
        _insert(connection, "source_refusals", monitoring_plan_id=plan_id, plan_version=ver, item_id=refusal.item_id,
                activation_id=activation_id, source_reference=source_reference, reason=refusal.reason,
                recorded_at=when, recorded_by=actor)
        _audit(connection, "SOURCE_ITEM_REFUSED", "SourceItem", refusal.item_id, when, actor,
               monitoring_plan_id=plan_id, plan_version=ver, source_reference=source_reference)


def load_source_refusal(connection, monitoring_plan_id: str, plan_version: int, item_id: str) -> RejectedSourceItem:
    row = _one(connection, "SELECT reason FROM source_refusals WHERE monitoring_plan_id = ? AND plan_version = ? "
               "AND item_id = ?", (monitoring_plan_id, plan_version, item_id), f"Source refusal {item_id}")
    with _reading(f"Source refusal {item_id}"):
        return RejectedSourceItem(item_id, monitoring_plan_id, plan_version, row[0])


def record_supersession(connection, supersession: Supersession, *, recorded_at: datetime, recorded_by: str) -> None:
    """Record that one stored Observation supersedes or retracts another of the same plan version. Neither is edited."""
    if not isinstance(supersession, Supersession):
        raise Engine2WriteRefused("record_supersession requires a Supersession")
    when, actor = _when(recorded_at), _actor(recorded_by)
    old = load_observation(connection, supersession.superseded_observation_id)
    new = load_observation(connection, supersession.superseding_observation_id)
    key = plan_key(old)
    if plan_key(new) != key:
        raise Engine2WriteRefused("a Supersession must relate Observations of the same plan version")
    with _transaction(connection):
        _refuse_duplicate(connection, "supersessions", "Supersession",
                          superseded_observation_id=supersession.superseded_observation_id)
        _insert(connection, "supersessions", superseded_observation_id=old.observation_id,
                superseding_observation_id=new.observation_id, monitoring_plan_id=key[0], plan_version=key[1],
                basis=supersession.basis.value, retraction=int(supersession.retraction), recorded_at=when, recorded_by=actor)
        _audit(connection, "SUPERSESSION_RECORDED", "Supersession", old.observation_id, when, actor,
               superseding_observation_id=new.observation_id, basis=supersession.basis.value,
               retraction=supersession.retraction, plan_version=key[1])


def load_supersession(connection, superseded_observation_id: str) -> Supersession:
    row = _one(connection, "SELECT superseding_observation_id, basis, retraction FROM supersessions "
               "WHERE superseded_observation_id = ?", (superseded_observation_id,), f"Supersession {superseded_observation_id}")
    with _reading(f"Supersession {superseded_observation_id}"):
        if row[2] not in (0, 1):
            raise ValueError("retraction flag must be 0 or 1")
        return Supersession(superseded_observation_id, row[0], SupersessionBasis(row[1]), bool(row[2]))


# --- Derived Measurements -----------------------------------------------------------


def record_derived_measurement(connection, measurement: DerivedMeasurement, *, recorded_at: datetime,
                               recorded_by: str) -> None:
    """Record a Derived Measurement. Its plan version is its inputs' version; every input must be stored unchanged."""
    if not isinstance(measurement, DerivedMeasurement):
        raise Engine2WriteRefused("record_derived_measurement requires a DerivedMeasurement")
    when, actor = _when(recorded_at), _actor(recorded_by)
    m, key = measurement, plan_key(measurement)
    load_monitoring_plan(connection, *key)
    for i in m.inputs:
        if load_observation(connection, i.observation_id) != i.observation:
            raise Engine2WriteRefused(f"input Observation {i.observation_id} differs from the stored record")
    _require_same_version(connection, key, "observations", "observation_id", sorted(m.source_observation_ids), "Observation")
    if _exists(connection, "observations", observation_id=m.measurement_id):
        raise Engine2WriteRefused("a measurement id may not equal an observation id")
    with _transaction(connection):
        _refuse_duplicate(connection, "derived_measurements", "Derived Measurement", measurement_id=m.measurement_id)
        _insert(connection, "derived_measurements", measurement_id=m.measurement_id, monitoring_plan_id=key[0],
                plan_version=key[1], transformation=m.transformation.value, transformation_version=m.transformation_version,
                formula=m.formula, rounding_rule=m.rounding_rule, output_value=str(m.output_value),
                output_unit=m.output_unit, calculated_at=_ts(m.calculated_at), recorded_at=when, recorded_by=actor)
        for position, i in enumerate(m.inputs):
            _insert(connection, "measurement_inputs", measurement_id=m.measurement_id, monitoring_plan_id=key[0],
                    plan_version=key[1], position=position, observation_id=i.observation_id, quantity=i.quantity,
                    value=str(i.value), unit=i.unit)
        _audit(connection, "DERIVED_MEASUREMENT_RECORDED", "DerivedMeasurement", m.measurement_id, when, actor,
               transformation=m.transformation.value, output_value=str(m.output_value), plan_version=key[1],
               source_observation_ids=sorted(m.source_observation_ids))


def load_derived_measurement(connection, measurement_id: str) -> DerivedMeasurement:
    """Rebuild and recompute. A stored output that differs from the deterministic result fails closed."""
    row = _one(connection, "SELECT transformation, transformation_version, formula, rounding_rule, output_value, "
               "output_unit, calculated_at FROM derived_measurements WHERE measurement_id = ?",
               (measurement_id,), f"Derived Measurement {measurement_id}")
    inputs = connection.execute("SELECT observation_id, quantity, value, unit FROM measurement_inputs "
                                "WHERE measurement_id = ? ORDER BY position", (measurement_id,)).fetchall()
    with _reading(f"Derived Measurement {measurement_id}"):
        m = DerivedMeasurement(
            measurement_id=measurement_id, transformation=Transformation(row[0]),
            inputs=tuple(MeasurementInput(load_observation(connection, o), q, Decimal(v), u) for o, q, v, u in inputs),
            output_value=Decimal(row[4]), calculated_at=_dt(row[6]), rounding_rule=row[3], output_unit=row[5],
        )
        if (row[1], row[2]) != (m.transformation_version, m.formula):
            raise ValueError("stored transformation version or formula differs from the locked transformation")
        return m


# --- Signal and Correspondence Candidates ---------------------------------------------


def record_signal_candidate(connection, signal: SignalCandidate, *, recorded_at: datetime, recorded_by: str) -> None:
    """Record a Signal Candidate under its own exact plan version. It asserts no finding."""
    if not isinstance(signal, SignalCandidate):
        raise Engine2WriteRefused("record_signal_candidate requires a SignalCandidate")
    when, actor = _when(recorded_at), _actor(recorded_by)
    s, key = signal, plan_key(signal)
    load_monitoring_plan(connection, *key)
    for o in s.observations:
        if load_observation(connection, o.observation_id) != o:
            raise Engine2WriteRefused(f"Observation {o.observation_id} differs from the stored record")
    _require_same_version(connection, key, "observations", "observation_id", s.observation_ids, "Observation")
    with _transaction(connection):
        _refuse_duplicate(connection, "signal_candidates", "Signal Candidate", signal_candidate_id=s.signal_candidate_id)
        _insert(connection, "signal_candidates", signal_candidate_id=s.signal_candidate_id, monitoring_plan_id=key[0],
                plan_version=key[1], selected_by=s.selected_by, recorded_at=when, recorded_by=actor)
        for position, oid in enumerate(s.observation_ids):
            _insert(connection, "signal_candidate_observations", signal_candidate_id=s.signal_candidate_id,
                    monitoring_plan_id=key[0], plan_version=key[1], position=position, observation_id=oid)
        for d in sorted(s.possibly_relevant_dimensions, key=lambda x: x.value):
            _insert(connection, "signal_candidate_dimensions", signal_candidate_id=s.signal_candidate_id, dimension=d.value)
        _audit(connection, "SIGNAL_CANDIDATE_RECORDED", "SignalCandidate", s.signal_candidate_id, when, actor,
               monitoring_plan_id=key[0], plan_version=key[1], observation_ids=list(s.observation_ids))


def load_signal_candidate(connection, signal_candidate_id: str) -> SignalCandidate:
    row = _one(connection, "SELECT monitoring_plan_id, selected_by, plan_version FROM signal_candidates "
               "WHERE signal_candidate_id = ?", (signal_candidate_id,), f"Signal Candidate {signal_candidate_id}")
    obs = [r[0] for r in connection.execute("SELECT observation_id FROM signal_candidate_observations "
                                            "WHERE signal_candidate_id = ? ORDER BY position", (signal_candidate_id,))]
    dims = [r[0] for r in connection.execute("SELECT dimension FROM signal_candidate_dimensions "
                                             "WHERE signal_candidate_id = ?", (signal_candidate_id,))]
    with _reading(f"Signal Candidate {signal_candidate_id}"):
        return SignalCandidate(signal_candidate_id, row[0], row[2], tuple(load_observation(connection, o) for o in obs),
                               row[1], frozenset(CorrespondenceDimension(d) for d in dims))


def record_correspondence_candidate(connection, candidate: CorrespondenceCandidate, *, recorded_at: datetime,
                                    recorded_by: str) -> None:
    """Record a candidate under its own exact plan version; its Signal Candidates must be stored unchanged. No outcome here."""
    if not isinstance(candidate, CorrespondenceCandidate):
        raise Engine2WriteRefused("record_correspondence_candidate requires a CorrespondenceCandidate")
    when, actor = _when(recorded_at), _actor(recorded_by)
    c, key = candidate, plan_key(candidate)
    if load_monitoring_plan(connection, *key).baec_id != c.baec_id:
        raise Engine2WriteRefused("the candidate's BAEC is not the plan's BAEC")
    for s in c.signal_candidates:
        if load_signal_candidate(connection, s.signal_candidate_id) != s:
            raise Engine2WriteRefused(f"Signal Candidate {s.signal_candidate_id} differs from the stored record")
    _require_same_version(connection, key, "signal_candidates", "signal_candidate_id",
                          [s.signal_candidate_id for s in c.signal_candidates], "Signal Candidate")
    with _transaction(connection):
        _refuse_duplicate(connection, "correspondence_candidates", "Correspondence Candidate", candidate_id=c.candidate_id)
        _insert(connection, "correspondence_candidates", candidate_id=c.candidate_id, baec_id=c.baec_id,
                monitoring_plan_id=key[0], plan_version=key[1], recorded_at=when, recorded_by=actor)
        for position, s in enumerate(c.signal_candidates):
            _insert(connection, "candidate_signals", candidate_id=c.candidate_id, monitoring_plan_id=key[0],
                    plan_version=key[1], position=position, signal_candidate_id=s.signal_candidate_id)
        _audit(connection, "CORRESPONDENCE_CANDIDATE_RECORDED", "CorrespondenceCandidate", c.candidate_id, when, actor,
               baec_id=c.baec_id, monitoring_plan_id=key[0], plan_version=key[1],
               signal_candidate_ids=[s.signal_candidate_id for s in c.signal_candidates])


def load_correspondence_candidate(connection, candidate_id: str) -> CorrespondenceCandidate:
    row = _one(connection, "SELECT baec_id, monitoring_plan_id, plan_version FROM correspondence_candidates "
               "WHERE candidate_id = ?", (candidate_id,), f"Correspondence Candidate {candidate_id}")
    ids = [r[0] for r in connection.execute("SELECT signal_candidate_id FROM candidate_signals WHERE candidate_id = ? "
                                            "ORDER BY position", (candidate_id,))]
    signals = tuple(load_signal_candidate(connection, i) for i in ids)
    with _reading(f"Correspondence Candidate {candidate_id}"):
        return CorrespondenceCandidate(candidate_id, row[0], row[1], row[2], signals)


# --- Human Correspondence Reviews --------------------------------------------------------


def record_review(connection, review: HumanCorrespondenceReview, *, activation_id: str, recorded_at: datetime,
                  recorded_by: str) -> CorrespondenceOutcome:
    """Record one review, append-only, with the exact evidence ledger it saw, its findings, checks, and triggers.

    The activation fixes the exact plan version. The candidate and every ledger
    member must already be stored, unchanged, under that same version. The
    outcome snapshot is computed here by the Stage C classifier; no caller supplies it.
    """
    if not isinstance(review, HumanCorrespondenceReview):
        raise Engine2WriteRefused("record_review requires a HumanCorrespondenceReview")
    when, actor = _when(recorded_at), _actor(recorded_by)
    led = review.ledger
    key = _activated_plan(connection, activation_id)
    plan_id, ver = key
    if load_active_plan(connection, activation_id) != led.active_plan:
        raise Engine2WriteRefused("the review's active plan differs from the stored activation")
    if load_correspondence_candidate(connection, review.candidate.candidate_id) != review.candidate:
        raise Engine2WriteRefused("the review's candidate differs from the stored record")
    _require_same_version(connection, key, "correspondence_candidates", "candidate_id", [review.candidate.candidate_id],
                          "Correspondence Candidate")
    for o in led.observations:
        if load_observation(connection, o.observation_id) != o:
            raise Engine2WriteRefused(f"ledger Observation {o.observation_id} differs from the stored record")
    _require_same_version(connection, key, "observations", "observation_id", [o.observation_id for o in led.observations],
                          "Observation")
    for s in led.supersessions:
        if load_supersession(connection, s.superseded_observation_id) != s:
            raise Engine2WriteRefused(f"ledger Supersession of {s.superseded_observation_id} differs from the stored record")
    _require_same_version(connection, key, "supersessions", "superseded_observation_id",
                          [s.superseded_observation_id for s in led.supersessions], "Supersession")
    for m in led.measurements:
        if load_derived_measurement(connection, m.measurement_id) != m:
            raise Engine2WriteRefused(f"ledger Derived Measurement {m.measurement_id} differs from the stored record")
    _require_same_version(connection, key, "derived_measurements", "measurement_id",
                          [m.measurement_id for m in led.measurements], "Derived Measurement")
    for r in led.rejected_items:
        if load_source_refusal(connection, plan_id, ver, r.item_id) != r:
            raise Engine2WriteRefused(f"ledger refusal {r.item_id} differs from the stored record")
    outcome = classify_correspondence(review)
    rid = review.review_id
    plan_cols = {"monitoring_plan_id": plan_id, "plan_version": ver}
    with _transaction(connection):
        _refuse_duplicate(connection, "reviews", "Human Correspondence Review", review_id=rid)
        _insert(connection, "reviews", review_id=rid, candidate_id=review.candidate.candidate_id, **plan_cols,
                activation_id=activation_id, sufficiency=review.sufficiency.value,
                reviewer=review.reviewer, reviewed_at=_ts(review.reviewed_at),
                baec_revalidation_required=int(review.baec_revalidation_required),
                outcome_snapshot=outcome.value, recorded_at=when, recorded_by=actor)
        for position, o in enumerate(led.observations):
            _insert(connection, "review_ledger_observations", review_id=rid, **plan_cols, position=position,
                    observation_id=o.observation_id)
        for position, s in enumerate(led.supersessions):
            _insert(connection, "review_ledger_supersessions", review_id=rid, **plan_cols, position=position,
                    superseded_observation_id=s.superseded_observation_id)
        for position, m in enumerate(led.measurements):
            _insert(connection, "review_ledger_measurements", review_id=rid, **plan_cols, position=position,
                    measurement_id=m.measurement_id)
        for position, r in enumerate(led.rejected_items):
            _insert(connection, "review_ledger_refusals", review_id=rid, **plan_cols, position=position, item_id=r.item_id)
        for position, f in enumerate(review.findings):
            _insert(connection, "review_findings", review_id=rid, position=position, dimension=f.dimension.value,
                    finding=f.finding.value, reason=f.reason)
            for p, ref in enumerate(f.evidence_refs):
                _insert(connection, "review_finding_evidence", review_id=rid, dimension=f.dimension.value,
                        position=p, evidence_ref=ref)
        for position, c in enumerate(review.checks):
            _insert(connection, "review_checks", review_id=rid, position=position, check_name=c.check.value, note=c.note)
            for p, ref in enumerate(c.evidence_refs):
                _insert(connection, "review_check_evidence", review_id=rid, check_name=c.check.value,
                        position=p, evidence_ref=ref)
            for d in sorted(c.affected_dimensions, key=lambda x: x.value):
                _insert(connection, "review_check_dimensions", review_id=rid, check_name=c.check.value, dimension=d.value)
        for position, trigger in enumerate(review.revalidation_triggers):
            _insert(connection, "review_revalidation_triggers", review_id=rid, position=position, trigger_text=trigger)
        _audit(connection, "HUMAN_REVIEW_RECORDED", "HumanCorrespondenceReview", rid, when, actor,
               candidate_id=review.candidate.candidate_id, reviewer=review.reviewer, outcome=outcome.value,
               baec_revalidation_required=review.baec_revalidation_required, plan_version=ver)
    return outcome


def load_review(connection, review_id: str) -> HumanCorrespondenceReview:
    """Rebuild a review against its exact plan version and evidence snapshot; verify the outcome snapshot."""
    row = _one(connection, "SELECT candidate_id, monitoring_plan_id, plan_version, activation_id, sufficiency, reviewer, "
               "reviewed_at, baec_revalidation_required, outcome_snapshot FROM reviews WHERE review_id = ?",
               (review_id,), f"Human Correspondence Review {review_id}")
    candidate_id, plan_id, ver, activation_id = row[0], row[1], row[2], row[3]
    try:
        active = load_active_plan(connection, activation_id)
    except MonitoringPlanActivationError as error:
        raise Engine2IntegrityError(f"review {review_id} rests on a refused activation") from error
    if (active.plan.monitoring_plan_id, active.plan.plan_version) != (plan_id, ver):
        raise Engine2IntegrityError(f"review {review_id} plan version disagrees with its activation")

    def ids(sql: str) -> list[str]:
        return [r[0] for r in connection.execute(sql, (review_id,))]

    try:
        observations = tuple(load_observation(connection, i) for i in ids(
            "SELECT observation_id FROM review_ledger_observations WHERE review_id = ? ORDER BY position"))
        supersessions = tuple(load_supersession(connection, i) for i in ids(
            "SELECT superseded_observation_id FROM review_ledger_supersessions WHERE review_id = ? ORDER BY position"))
        measurements = tuple(load_derived_measurement(connection, i) for i in ids(
            "SELECT measurement_id FROM review_ledger_measurements WHERE review_id = ? ORDER BY position"))
        refusals = tuple(load_source_refusal(connection, plan_id, ver, i) for i in ids(
            "SELECT item_id FROM review_ledger_refusals WHERE review_id = ? ORDER BY position"))
        candidate = load_correspondence_candidate(connection, candidate_id)
        for o in observations:
            if observation_plan_version(connection, o.observation_id) != (plan_id, ver):
                raise Engine2IntegrityError(f"review {review_id} ledger mixes plan versions")
    except RecordNotFoundError as error:
        raise Engine2IntegrityError(f"review {review_id} references a missing record: {error}") from error
    finding_rows = connection.execute("SELECT dimension, finding, reason FROM review_findings WHERE review_id = ? "
                                      "ORDER BY position", (review_id,)).fetchall()
    check_rows = connection.execute("SELECT check_name, note FROM review_checks WHERE review_id = ? ORDER BY position",
                                    (review_id,)).fetchall()
    triggers = ids("SELECT trigger_text FROM review_revalidation_triggers WHERE review_id = ? ORDER BY position")
    with _reading(f"Human Correspondence Review {review_id}"):
        findings = tuple(
            DimensionAssessment(CorrespondenceDimension(d), DimensionFinding(f), tuple(r[0] for r in connection.execute(
                "SELECT evidence_ref FROM review_finding_evidence WHERE review_id = ? AND dimension = ? ORDER BY position",
                (review_id, d))), reason)
            for d, f, reason in finding_rows)
        checks = tuple(
            CrossCuttingCheck(
                CrossCuttingCheckName(name), note,
                tuple(r[0] for r in connection.execute("SELECT evidence_ref FROM review_check_evidence WHERE review_id = ? "
                                                       "AND check_name = ? ORDER BY position", (review_id, name))),
                frozenset(CorrespondenceDimension(r[0]) for r in connection.execute(
                    "SELECT dimension FROM review_check_dimensions WHERE review_id = ? AND check_name = ?", (review_id, name))))
            for name, note in check_rows)
        if row[7] not in (0, 1):
            raise ValueError("baec_revalidation_required must be 0 or 1")
        review = HumanCorrespondenceReview(
            review_id=review_id, candidate=candidate,
            ledger=EvidenceLedger(active, observations, supersessions, measurements, refusals),
            findings=findings, checks=checks, sufficiency=SufficiencyFinding(row[4]), reviewer=row[5],
            reviewed_at=_dt(row[6]), baec_revalidation_required=bool(row[7]), revalidation_triggers=tuple(triggers),
        )
        snapshot = CorrespondenceOutcome(row[8])
    if classify_correspondence(review) is not snapshot:
        raise Engine2IntegrityError(f"review {review_id}: stored outcome snapshot disagrees with the classifier")
    return review


def review_ids_for_candidate(connection, candidate_id: str) -> list[str]:
    """Every review of a candidate, in recording order. Earlier reviews are never replaced."""
    return [r[0] for r in connection.execute("SELECT review_id FROM reviews WHERE candidate_id = ? ORDER BY rowid",
                                             (candidate_id,))]


# --- audit history and whole-database verification ----------------------------------------


@dataclass(frozen=True)
class AuditEvent:
    """One append-only audit record. History, not a domain state."""

    event_id: int
    event_type: str
    entity_type: str
    entity_id: str
    recorded_at: datetime
    actor: str
    details: dict


def audit_events(connection) -> list[AuditEvent]:
    rows = connection.execute("SELECT event_id, event_type, entity_type, entity_id, recorded_at, actor, details_json "
                              "FROM audit_events ORDER BY event_id").fetchall()
    with _reading("audit history"):
        out = []
        for event_id, event_type, entity_type, entity_id, recorded_at, actor, details in rows:
            if event_type not in AUDIT_EVENT_TYPES:
                raise ValueError(f"unknown audit event type {event_type}")
            out.append(AuditEvent(event_id, event_type, entity_type, entity_id, _dt(recorded_at), actor, json.loads(details)))
        return out


def verify_database(connection) -> dict[str, int]:
    """Re-validate every stored record through the domain. Raises on the first problem; returns table counts."""
    require_engine2_schema(connection)
    orphans = connection.execute("PRAGMA foreign_key_check").fetchall()
    if orphans:
        raise Engine2IntegrityError(f"orphaned references: {orphans[:5]}")
    load_authorized_sources(connection)
    for (pid, ver) in connection.execute("SELECT monitoring_plan_id, plan_version FROM monitoring_plans").fetchall():
        load_monitoring_plan(connection, pid, ver)
    for (aid, result) in connection.execute("SELECT activation_id, result FROM plan_activations").fetchall():
        try:
            load_active_plan(connection, aid)
        except MonitoringPlanActivationError:
            if result != "REFUSED":
                raise
    for (oid,) in connection.execute("SELECT observation_id FROM observations").fetchall():
        load_observation(connection, oid)
    for (sid,) in connection.execute("SELECT superseded_observation_id FROM supersessions").fetchall():
        load_supersession(connection, sid)
    for (mid,) in connection.execute("SELECT measurement_id FROM derived_measurements").fetchall():
        load_derived_measurement(connection, mid)
    for (cid,) in connection.execute("SELECT candidate_id FROM correspondence_candidates").fetchall():
        load_correspondence_candidate(connection, cid)
    for (rid,) in connection.execute("SELECT review_id FROM reviews").fetchall():
        load_review(connection, rid)
    audit_events(connection)
    return {t: connection.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in TABLES}
