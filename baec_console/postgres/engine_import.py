"""The Engine 1 and Engine 2 part of the explicit SQLite -> PostgreSQL import (importer.py). Never run automatically.

Each workspace directory's engine1.sqlite3 and engine2.sqlite3 are opened read-only and must be at their current
schema. Every row is copied as stored, in its table's insertion order, tables in dependency order:

    ids          kept: caller-supplied text ids and the database-assigned integer ids (evidence, authorization,
                 judgment, evaluation-evidence, non-evaluation-evidence, transition, Engine 2 audit event) alike.
                 The PostgreSQL guards assign an id only when none is given, so later ids continue after them
    text         byte for byte: buyer statements, evidence excerpts, thresholds, reasons, JSON, and every
                 timestamp (engine timestamps are stored as text in both databases)
    nothing      reclassified, regenerated, renumbered, or repaired; accounts keep their stored state and history

Ownership. A workspace directory can hold several organizations' accounts (local/), so every engine row is resolved to
the account it belongs to, and the account to its owner (account_ownership): Engine 1 rows through their account,
interaction, BAEC, run, artifact, or grant; Engine 2 rows through their Monitoring Plan's BAEC (or the review, signal
candidate, or audited entity that leads to it). A row that resolves to no owner, or to two, refuses the import.

Every row is written as the application role, acting for its organization and workspace, so row-level security and
every Engine 1 and Engine 2 trigger apply to it exactly as to a row the application writes: the import cannot store a
row the guards would refuse. That is also why the Engine 1 AI provenance and proposal-bridge history is not imported:
its review state machine (latest revision, grant supersession written by AFTER INSERT triggers) cannot be replayed one
table at a time without switching the guards off. The console never writes those tables; if a source holds any such
row, the whole import is refused. Engine 1's schema_meta rows are store metadata and are not copied.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from baec_app.data import database as engine1
from baec_app.data.repository import Repository
from baec_app.engine2 import persistence as engine2

from . import PostgresError
from .database import act_for
from .engine1 import Engine1Connection
from .engine2 import Engine2Connection

ENGINE1_IMPORTED = engine1.DATA_TABLES[:12]  # accounts ... account_state_transitions
ENGINE1_REFUSED = engine1.AI_PROVENANCE_TABLES + engine1.BRIDGE_TABLES
ENGINE2_IMPORTED = tuple(t for t in engine2.TABLES if t != "engine2_meta")


class EngineImportRefused(PostgresError):
    """The engine stores of the source cannot be imported safely. Nothing was written."""


@dataclass
class EngineRow:
    schema: str            # baec_engine1 | baec_engine2
    table: str
    organization_id: str
    workspace: str
    values: dict
    directory: Path | None = None  # the source workspace directory the row came from

    @property
    def label(self) -> str:
        return f"{'engine1' if self.schema == 'baec_engine1' else 'engine2'}.{self.table}"


@dataclass
class EngineSource:
    workspace: str
    directory: Path
    engine1: sqlite3.Connection | None = None
    engine2: sqlite3.Connection | None = None
    owner: dict = field(default_factory=dict)

    def close(self) -> None:
        for db in (self.engine1, self.engine2):
            if db is not None:
                db.close()


def _read_only(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def open_engines(workspace: str, directory: Path, owner: dict) -> EngineSource:
    """The workspace's engine stores, read-only, checked to be at the current schema (never migrated here)."""
    source = EngineSource(workspace, directory, owner=owner)
    try:
        if (directory / "engine1.sqlite3").is_file():
            source.engine1 = _read_only(directory / "engine1.sqlite3")
            if source.engine1.execute("PRAGMA user_version").fetchone()[0] != engine1.SCHEMA_VERSION:
                raise EngineImportRefused(f"the Engine 1 store in {directory.name}/ is not at schema "
                                          f"{engine1.SCHEMA_VERSION}")
        if (directory / "engine2.sqlite3").is_file():
            source.engine2 = _read_only(directory / "engine2.sqlite3")
            meta = dict(source.engine2.execute("SELECT key, value FROM engine2_meta").fetchall())
            if meta != {"engine": engine2.ENGINE, "schema_version": str(engine2.SCHEMA_VERSION)}:
                raise EngineImportRefused(f"the Engine 2 store in {directory.name}/ is not at the current schema")
    except sqlite3.Error:
        source.close()
        raise EngineImportRefused(f"an engine store in {directory.name}/ could not be read") from None
    except BaseException:
        source.close()
        raise
    return source


def _rows(db: sqlite3.Connection, table: str) -> list[dict]:
    cursor = db.execute(f"SELECT * FROM {table} ORDER BY rowid")
    names = [d[0] for d in cursor.description]
    return [dict(zip(names, r)) for r in cursor.fetchall()]


def _engine1_rows(source: EngineSource, unowned: list) -> list[EngineRow]:
    db = source.engine1
    for table in ENGINE1_REFUSED:
        if db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]:
            raise EngineImportRefused(f"the Engine 1 store in {source.directory.name}/ holds {table} rows; AI "
                                      "provenance and proposal-bridge history is not imported (its guarded state "
                                      "machine cannot be replayed table by table). Nothing was imported")
    interaction = dict(db.execute("SELECT interaction_id, account_id FROM interactions").fetchall())
    baec = dict(db.execute("SELECT baec_id, account_id FROM baec_records").fetchall())
    out = []
    for table in ENGINE1_IMPORTED:
        for row in _rows(db, table):
            if table == "interaction_evidence":
                account_id = interaction.get(row["interaction_id"])
            elif table == "human_authorizations":
                account_id = (row["subject_id"] if row["action"] == "CHANGE_ACCOUNT_STATE"
                              else baec.get(row["subject_id"]))
            elif "account_id" in row:
                account_id = row["account_id"]
            else:
                account_id = baec.get(row["baec_id"])
            organization_id = source.owner.get(account_id)
            if organization_id is None:
                unowned.append(f"{source.directory.name}/engine1.{table}")
                continue
            out.append(EngineRow("baec_engine1", table, organization_id, source.workspace, row, source.directory))
    return out


_ENGINE2_ENTITY_KEYS = ("source_id", "monitoring_plan_id", "activation_id", "observation_id", "item_id",
                        "measurement_id", "signal_candidate_id", "candidate_id", "review_id")


def _engine2_rows(source: EngineSource, baec_owner: dict, unowned: list) -> list[EngineRow]:
    db = source.engine2
    plan_org = {}
    for plan_id, baec_id in db.execute("SELECT monitoring_plan_id, baec_id FROM monitoring_plans").fetchall():
        organization_id = baec_owner.get(baec_id)
        if plan_org.setdefault(plan_id, organization_id) != organization_id:
            unowned.append(f"{source.directory.name}/engine2.monitoring_plans {plan_id}")
    entity_org: dict[str, set] = {}
    rows_by_table = {table: _rows(db, table) for table in ENGINE2_IMPORTED}
    for table in ENGINE2_IMPORTED:
        for row in rows_by_table[table]:
            organization_id = plan_org.get(row.get("monitoring_plan_id"))
            if organization_id is None and "signal_candidate_id" in row:
                organization_id = next(iter(entity_org.get(row["signal_candidate_id"], ())), None)
            if organization_id is None and "review_id" in row:
                organization_id = next(iter(entity_org.get(row["review_id"], ())), None)
            if organization_id is not None:
                for key in _ENGINE2_ENTITY_KEYS:
                    if row.get(key) is not None:
                        entity_org.setdefault(row[key], set()).add(organization_id)
            row["_organization_id"] = organization_id
    out = []
    for table in ENGINE2_IMPORTED:
        for row in rows_by_table[table]:
            organization_id = row.pop("_organization_id")
            if table == "authorized_sources":
                owners = entity_org.get(row["source_id"], set())
                organization_id = next(iter(owners)) if len(owners) == 1 else None
            elif table == "audit_events":
                if row["entity_type"] == "MonitoringPlan":  # entity id "<plan id>@v<version>" (persistence._plan_ref)
                    owners = {plan_org.get(row["entity_id"].rsplit("@v", 1)[0])} - {None}
                else:
                    owners = entity_org.get(row["entity_id"], set())
                organization_id = next(iter(owners)) if len(owners) == 1 else None
            if organization_id is None:
                unowned.append(f"{source.directory.name}/engine2.{table}")
                continue
            out.append(EngineRow("baec_engine2", table, organization_id, source.workspace, row, source.directory))
    return out


def plan_engines(sources: list[EngineSource]) -> list[EngineRow]:
    """Every engine row to import, with its owning organization and workspace. Refuses any row without one owner."""
    planned, unowned = [], []
    for source in sources:
        if source.engine1 is not None:
            engine1_rows = _engine1_rows(source, unowned)
            planned += engine1_rows
            baec_owner = {r.values["baec_id"]: r.organization_id for r in engine1_rows if r.table == "baec_records"}
        else:
            baec_owner = {}
        if source.engine2 is not None:
            planned += _engine2_rows(source, baec_owner, unowned)
    if unowned:
        raise EngineImportRefused(f"{len(unowned)} engine rows have no single owning organization: "
                                  + "; ".join(sorted(set(unowned))[:10]))
    return planned


# --- target -----------------------------------------------------------------------------------------------------------

_PRIMARY_KEYS: dict[tuple[str, str], tuple] = {}


def primary_key(schema: str, table: str) -> tuple:
    if (schema, table) not in _PRIMARY_KEYS:
        opener = engine1.open_database if schema == "baec_engine1" else engine2.open_engine2_database
        db = opener(":memory:")
        try:
            info = db.execute(f"PRAGMA table_info({table})").fetchall()
        finally:
            db.close()
        key = tuple(r[1] for r in sorted((r for r in info if r[5]), key=lambda r: r[5]))
        _PRIMARY_KEYS[(schema, table)] = key or tuple(r[1] for r in info)
    return _PRIMARY_KEYS[(schema, table)]


def existing(connection, row: EngineRow) -> dict | None:
    """The target row with the same key in the row's organization and workspace (row-level security applies)."""
    key = primary_key(row.schema, row.table)
    columns = list(row.values)
    found = connection.execute(
        f"SELECT {', '.join(columns)} FROM {row.schema}.{row.table} WHERE organization_id = %s AND workspace = %s AND "
        + " AND ".join(f"{k} = %s" for k in key), (row.organization_id, row.workspace,
                                                    *(row.values[k] for k in key))).fetchone()
    return None if found is None else dict(zip(columns, found))


def insert(connection, row: EngineRow) -> None:
    values = row.values
    connection.execute(f"INSERT INTO {row.schema}.{row.table} ({', '.join(values)}) "
                       f"VALUES ({', '.join(['%s'] * len(values))})", tuple(values.values()))


def target_counts(connection, organization_id: str, workspaces: set[str]) -> dict:
    """This organization's rows in every imported engine table (all of its workspaces)."""
    counts = {}
    for schema, tables in (("baec_engine1", ENGINE1_IMPORTED), ("baec_engine2", ENGINE2_IMPORTED)):
        for table in tables:
            label = f"{'engine1' if schema == 'baec_engine1' else 'engine2'}.{table}"
            counts[label] = 0
            for workspace in sorted(workspaces):
                act_for(connection, organization_id, workspace)
                counts[label] += connection.execute(f"SELECT count(*) FROM {schema}.{table}").fetchone()[0]
    return counts


# --- verification -----------------------------------------------------------------------------------------------------


def _repository_view(repo: Repository, accounts: list[str]) -> list:
    view = []
    for account_id in accounts:
        records = repo.list_baec_records(account_id)
        view.append((repo.get_account(account_id), repo.list_interactions(account_id), records,
                     [repo.list_dormancy_judgments(r.baec_id) for r in records],
                     repo.get_transition_history(account_id)))
    return view


def verify_engines(connection, sources: list[EngineSource], planned: list[EngineRow], results: dict) -> None:
    """Re-read the imported engine records through PostgreSQL and compare them with the SQLite source: every row of
    every table (column by column, as stored), every owned account through the Engine 1 repository (which re-verifies
    each record against the locked rules and the transition history), and the Engine 2 audit history and reviews
    through Engine 2's loaders. Timestamps are text in both, so they compare byte for byte."""
    def compare(label, source, target):
        results["checked"] += 1
        if source != target:
            results["mismatches"].append(label)

    for source in sources:
        organizations = {r.organization_id for r in planned if r.directory == source.directory}
        for organization_id in sorted(organizations):
            mine = [r for r in planned if r.organization_id == organization_id and r.directory == source.directory]
            act_for(connection, organization_id, source.workspace)
            where = f"{source.directory.name}/{organization_id}"
            for schema, tables in (("baec_engine1", ENGINE1_IMPORTED), ("baec_engine2", ENGINE2_IMPORTED)):
                for table in tables:
                    wanted = [r.values for r in mine if r.schema == schema and r.table == table]
                    if not wanted:
                        continue
                    columns = list(wanted[0])
                    stored = [dict(zip(columns, r)) for r in connection.execute(
                        f"SELECT {', '.join(columns)} FROM {schema}.{table}").fetchall()]
                    compare(f"{where} {schema}.{table}", sorted(map(repr, wanted)), sorted(map(repr, stored)))
            accounts = [r.values["account_id"] for r in mine if r.table == "accounts"]
            if source.engine1 is not None and accounts:
                compare(f"{where} Engine 1 repository", _repository_view(Repository(source.engine1), accounts),
                        _repository_view(Repository(Engine1Connection(connection)), accounts))
            reviews = [r.values["review_id"] for r in mine if r.table == "reviews"]
            if source.engine2 is not None and any(r.schema == "baec_engine2" for r in mine):
                target = Engine2Connection(connection)
                events = {r.values["event_id"] for r in mine if r.table == "audit_events"}
                compare(f"{where} Engine 2 audit history",
                        [e for e in engine2.audit_events(source.engine2) if e.event_id in events],
                        engine2.audit_events(target))
                for review_id in reviews:
                    compare(f"{where} Engine 2 review {review_id}", engine2.load_review(source.engine2, review_id),
                            engine2.load_review(target, review_id))
