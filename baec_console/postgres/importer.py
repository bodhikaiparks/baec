"""Explicit SQLite -> PostgreSQL import of product and engine data, for development and testing. Never automatic.

    python -m baec_console.postgres.importer SOURCE_ROOT [--dry-run]

    SOURCE_ROOT                   a console data directory (identity.sqlite3, local/, synthetic/, synthetic-organizations/)
    BAEC_DATABASE_URL             the application role: every row is written under row-level security, acting for the
                                  organization that owns it, so the import cannot write a row its owner could not
    BAEC_MIGRATION_DATABASE_URL   the migration role (real import only): advances id sequences past the imported ids
                                  and records the import in the operator log

What it copies: organizations, users, identity audit, account ownership, account and interaction metadata, condition
drafts and resolutions, preparation records, block decisions, console events, attributions, and CRM contacts, tasks,
interaction links, and activities; and, per workspace, the Engine 1 records (accounts, interactions, evidence,
authorizations, BAEC records and their assessments, evidence and thresholds, dormancy judgments, state evidence and
state transitions) and every Engine 2 record (engine_import.py). Ids, timestamps (product tables: as instants; engine
tables: as stored text), organization ownership, attribution, and actor labels are preserved; JSON, buyer text, and
evidence are copied as stored, never re-serialized, regenerated, or reclassified. Not copied: the legacy "Your name"
preference, store metadata rows, the SQLite backfill log (recorded verbatim in the operator log instead), and Engine 1
AI provenance and proposal-bridge history (a source holding any is refused; see engine_import.py).

Safety:
  * the source is opened read-only and must be at the current schema with no pending backfill;
  * every row must resolve to an owning organization; synthetic workspaces must belong to the directory's owner;
  * a row already present with identical content is skipped; a row present with different content is a conflict,
    and any conflict refuses the whole import. Nothing is ever updated or overwritten;
  * the import is one transaction: all of it, or none. --dry-run performs it, verifies it, and rolls it back;
  * after a real import, each organization's data is re-read through the PostgreSQL stores and compared with the
    SQLite stores (verification report). Credentials are never printed.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from .. import console_store, crm_store, identity
from ..console_service import IDENTITY_FILE, LOCAL, SYNTHETIC, SYNTHETIC_ORGANIZATIONS, synthetic_directory
from . import PostgresError, require_driver
from . import engine_import
from .database import act_for, connect, validate_database_url
from .stores import ProductSession

BOOLEAN = {"synthetic", "model_used", "active", "ai_assisted"}
_SEQUENCES = (("identity_audit", "seq"), ("events", "event_id"), ("preparation_blocks", "decision_id"))


_MIGRATION_URL = "BAEC_MIGRATION_DATABASE_URL"


class ImportRefused(PostgresError):
    """The source or target cannot be imported safely. Nothing was written."""


@dataclass(frozen=True)
class Table:
    name: str           # PostgreSQL table (schema baec)
    source: str         # identity | console | crm
    sql: str            # SQLite select, in insertion order; column names are the PostgreSQL column names
    key: tuple          # identifying columns, besides organization_id and workspace for workspace tables
    via: str = "account_id"  # how a workspace row reaches its account: account_id, draft_id, request_id, preparation_id


IDENTITY_TABLES = (
    Table("organizations", "identity", "SELECT organization_id, name, created_at FROM organizations ORDER BY rowid",
          ("organization_id",)),
    Table("users", "identity", "SELECT user_id, organization_id, display_name, created_at, active, kind FROM users "
          "ORDER BY rowid", ("user_id",)),
    Table("identity_audit", "identity", "SELECT seq, occurred_at, event_type, user_id, detail FROM identity_audit "
          "ORDER BY seq", ("seq",)),
)
WORKSPACE_TABLES = (
    Table("account_ownership", "console", "SELECT account_id, organization_id, recorded_at, recorded_by_user_id, basis "
          "FROM account_ownership ORDER BY rowid", ("account_id",)),
    Table("account_meta", "console", "SELECT account_id, display_name, industry, owner_note, synthetic, created_at, "
          "created_by FROM account_meta ORDER BY rowid", ("account_id",)),
    Table("interaction_meta", "console", "SELECT interaction_id, account_id, buyer_role, buyer_statement, "
          "seller_notes, recorded_at, recorded_by FROM interaction_meta ORDER BY rowid", ("interaction_id",)),
    Table("condition_drafts", "console", "SELECT draft_id, account_id, interaction_id, form_json, captured_at, "
          "captured_by FROM condition_drafts ORDER BY rowid", ("draft_id",)),
    Table("draft_resolutions", "console", "SELECT draft_id, resolution, baec_id, resolved_at, resolved_by "
          "FROM draft_resolutions ORDER BY rowid", ("draft_id",), via="draft_id"),
    Table("preparation_requests", "console", "SELECT request_id, account_id, baec_id, review_id, candidate_id, "
          "request_json, snapshot_json, context_digest, requested_at, requested_by FROM preparation_requests "
          "ORDER BY rowid", ("request_id",)),
    Table("preparations", "console", "SELECT preparation_id, request_id, generator, artifacts_json, generated_at, "
          "generated_by FROM preparations ORDER BY rowid", ("preparation_id",), via="request_id"),
    Table("preparation_provenance", "console", "SELECT preparation_id, bound_review_id, bound_candidate_id, "
          "console_generator_origin, engine3_validator_origin, origin_compatibility_note, model_used "
          "FROM preparation_provenance ORDER BY rowid", ("preparation_id",), via="preparation_id"),
    Table("preparation_edits", "console", "SELECT edit_id, preparation_id, parent_artifact_id, artifact_json, "
          "edited_at, edited_by FROM preparation_edits ORDER BY rowid", ("edit_id",), via="preparation_id"),
    Table("preparation_reviews", "console", "SELECT preparation_id, artifact_id, decision, edit_id, attestations_json, "
          "reason, reviewed_at, reviewed_by FROM preparation_reviews ORDER BY rowid", ("preparation_id", "artifact_id"),
          via="preparation_id"),
    Table("preparation_blocks", "console", "SELECT decision_id, account_id, review_id, action, reason, decided_at, "
          "decided_by FROM preparation_blocks ORDER BY decision_id", ("decision_id",)),
    Table("events", "console", "SELECT event_id, account_id, event_type, actor_kind, actor, occurred_at, summary, "
          "ref_id FROM events ORDER BY event_id", ("event_id",)),
    Table("attributions", "console", "SELECT record_type, record_id, action, account_id, user_id, organization_id, "
          "display_name_snapshot, recorded_at, basis FROM attributions ORDER BY rowid",
          ("record_type", "record_id", "action")),
    Table("crm_contacts", "crm", "SELECT contact_id, account_id, name, title, email, phone, notes, active, created_at, "
          "created_by FROM contacts ORDER BY rowid", ("contact_id",)),
    Table("crm_tasks", "crm", "SELECT task_id, account_id, contact_id, title, due_date, priority, status, notes, "
          "created_at, created_by, completed_at, completed_by FROM tasks ORDER BY rowid", ("task_id",)),
    Table("crm_interaction_links", "crm", "SELECT interaction_id, account_id, contact_id, interaction_type, linked_at "
          "FROM interaction_links ORDER BY rowid", ("interaction_id",)),
    Table("crm_activities", "crm", "SELECT activity_id, account_id, contact_id, interaction_type, occurred_at, "
          "raw_notes, summary, buyer_role, buyer_statement, seller_notes, engine1_interaction_id, ai_assisted, "
          "created_at, created_by FROM activities ORDER BY rowid", ("activity_id",)),
)


def _value(column: str, value):
    """A SQLite value as the PostgreSQL column takes it. Text (including JSON and buyer language) is unchanged."""
    if value is None:
        return None
    if column in BOOLEAN:
        return bool(value)
    if column.endswith("_at"):
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ImportRefused(f"a stored timestamp in column {column} has no time zone")
        return parsed
    if column == "due_date":
        return date.fromisoformat(value)
    return value


def _read_only(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def _rows(db: sqlite3.Connection, table: Table) -> list[dict]:
    cursor = db.execute(table.sql)
    names = [d[0] for d in cursor.description]
    return [{n: _value(n, v) for n, v in zip(names, r)} for r in cursor.fetchall()]


# --- source -----------------------------------------------------------------------------------------------------------


@dataclass
class Workspace:
    workspace: str               # local | synthetic
    directory: Path
    console: sqlite3.Connection
    crm: sqlite3.Connection | None
    owner: dict[str, str] = field(default_factory=dict)   # account_id -> organization_id
    migration_log: list[dict] = field(default_factory=list)
    engines: engine_import.EngineSource | None = None


def _meta(db: sqlite3.Connection, table: str) -> dict:
    try:
        return dict(db.execute(f"SELECT key, value FROM {table}").fetchall())
    except sqlite3.Error:
        return {}


def open_source(root: Path) -> tuple[sqlite3.Connection, list[Workspace]]:
    """The identity store and every workspace under `root`, opened read-only and checked. Refuses rather than
    migrating: a store must first be opened once by the development console."""
    root = Path(root)
    if not (root / IDENTITY_FILE).is_file():
        raise ImportRefused(f"no {IDENTITY_FILE} under the source directory")
    ident = _read_only(root / IDENTITY_FILE)
    workspaces = []
    try:
        _open_workspaces(root, ident, workspaces)
    except BaseException:
        _close(ident, workspaces)
        raise
    return ident, workspaces


def _close(ident: sqlite3.Connection, workspaces: list[Workspace]) -> None:
    ident.close()
    for ws in workspaces:
        ws.console.close()
        if ws.crm is not None:
            ws.crm.close()
        if ws.engines is not None:
            ws.engines.close()


def _open_workspaces(root: Path, ident: sqlite3.Connection, workspaces: list[Workspace]) -> None:
    """Open and check every workspace, appending each as soon as it is open (so a refusal can close it)."""
    if _meta(ident, "identity_meta") != {"engine": "BAEC_IDENTITY", "schema_version": str(identity.SCHEMA_VERSION)}:
        raise ImportRefused("the identity store is not at the current schema; open it once in development first")
    directories = [(LOCAL, root / LOCAL), (SYNTHETIC, root / SYNTHETIC)]
    directories += [(SYNTHETIC, d) for d in sorted((root / SYNTHETIC_ORGANIZATIONS).glob("*")) if d.is_dir()]
    for name, directory in directories:
        if not (directory / "console.sqlite3").is_file():
            continue
        console = _read_only(directory / "console.sqlite3")
        ws = Workspace(name, directory, console, None)
        workspaces.append(ws)
        meta = _meta(console, "console_meta")
        if meta != {"engine": "BAEC_CONSOLE", "schema_version": str(console_store.SCHEMA_VERSION),
                    console_store.OWNERSHIP_BACKFILL: console_store.BACKFILL_DONE,
                    console_store.ATTRIBUTION_BACKFILL: console_store.BACKFILL_DONE}:
            raise ImportRefused(f"the console store in {directory.name}/ is not current or has a pending backfill; "
                                "open it once in development first")
        if (directory / "crm.sqlite3").is_file():
            ws.crm = _read_only(directory / "crm.sqlite3")
            if _meta(ws.crm, "crm_meta") != {"engine": "BAEC_CRM", "schema_version": str(crm_store.SCHEMA_VERSION)}:
                raise ImportRefused(f"the CRM store in {directory.name}/ is not at the current schema")
        ws.owner = dict(console.execute("SELECT account_id, organization_id FROM account_ownership").fetchall())
        try:
            ws.engines = engine_import.open_engines(name, directory, ws.owner)
        except engine_import.EngineImportRefused as error:
            raise ImportRefused(str(error)) from None
        ws.migration_log = [{"seq": r[0], "migration": r[1], "completed_at": r[2], "detail": json.loads(r[3])}
                            for r in console.execute("SELECT seq, migration, completed_at, detail_json "
                                                     "FROM migration_log ORDER BY seq")]
        if name == SYNTHETIC:
            for organization_id in set(ws.owner.values()):
                if synthetic_directory(root, organization_id) != directory:
                    raise ImportRefused(f"synthetic workspace {directory.name}/ holds an account of an organization "
                                        "it does not belong to")


@dataclass
class PlannedRow:
    table: Table
    organization_id: str
    workspace: str | None
    values: dict


def plan(ident: sqlite3.Connection, workspaces: list[Workspace]) -> list[PlannedRow]:
    """Every row to import, with its owning organization, in dependency and insertion order. Refuses any row that
    does not resolve to an owner."""
    planned, unowned = [], []
    users = {}
    for table in IDENTITY_TABLES:
        for row in _rows(ident, table):
            if table.name == "organizations":
                organization_id = row["organization_id"]
            elif table.name == "users":
                organization_id = users[row["user_id"]] = row["organization_id"]
            else:
                organization_id = users.get(row["user_id"])
            if organization_id is None:
                unowned.append(f"{table.name} {row}")
                continue
            planned.append(PlannedRow(table, organization_id, None, row))
    for ws in workspaces:
        account_of = {"draft_id": {}, "request_id": {}, "preparation_id": {}}
        for table in WORKSPACE_TABLES:
            db = ws.console if table.source == "console" else ws.crm
            if db is None:
                continue
            for row in _rows(db, table):
                if table.via == "account_id":
                    account_id = row["account_id"]
                elif table.via == "draft_id":
                    account_id = account_of["draft_id"].get(row["draft_id"])
                elif table.via == "request_id":
                    account_id = account_of["request_id"].get(row["request_id"])
                else:
                    account_id = account_of["preparation_id"].get(row["preparation_id"])
                if table.name == "condition_drafts":
                    account_of["draft_id"][row["draft_id"]] = row["account_id"]
                elif table.name == "preparation_requests":
                    account_of["request_id"][row["request_id"]] = row["account_id"]
                elif table.name == "preparations":
                    account_of["preparation_id"][row["preparation_id"]] = account_id
                organization_id = ws.owner.get(account_id)
                if organization_id is None or row.get("organization_id", organization_id) != organization_id:
                    unowned.append(f"{ws.directory.name}/{table.name} {[row.get(k) for k in table.key]}")
                    continue
                values = {k: v for k, v in row.items() if k != "organization_id"}
                planned.append(PlannedRow(table, organization_id, ws.workspace, values))
    if unowned:
        raise ImportRefused(f"{len(unowned)} rows have no owning organization (or disagree with it): "
                            + "; ".join(unowned[:10]))
    return planned


# --- target -----------------------------------------------------------------------------------------------------------


def _scope(row: PlannedRow) -> dict:
    scope = {"organization_id": row.organization_id}
    if row.workspace is not None:
        scope["workspace"] = row.workspace
    return scope


def _existing(connection, row: PlannedRow) -> dict | None:
    """The target row with the same identity, if the current organization can see one."""
    key = {**_scope(row), **{k: row.values[k] for k in row.table.key}}
    columns = list(dict.fromkeys([*_scope(row), *row.values]))
    found = connection.execute(
        f"SELECT {', '.join(columns)} FROM baec.{row.table.name} WHERE "
        + " AND ".join(f"{k} = %s" for k in key), tuple(key.values())).fetchone()
    return None if found is None else dict(zip(columns, found))


def _insert(connection, row: PlannedRow) -> None:
    values = {**_scope(row), **row.values}
    connection.execute(f"INSERT INTO baec.{row.table.name} ({', '.join(values)}) "
                       f"VALUES ({', '.join(['%s'] * len(values))})", tuple(values.values()))


@dataclass
class ImportReport:
    dry_run: bool
    source_counts: dict = field(default_factory=dict)
    target_counts_before: dict = field(default_factory=dict)
    inserted: dict = field(default_factory=dict)
    already_present: dict = field(default_factory=dict)
    target_counts_after: dict = field(default_factory=dict)
    conflicts: list = field(default_factory=list)
    verification: dict = field(default_factory=dict)
    not_imported: tuple = ("the legacy actor preference (prefs)",
                           "store metadata rows (identity_meta, console_meta, crm_meta, engine2_meta, Engine 1 "
                           "schema_meta)", "Engine 1 AI provenance and proposal-bridge history (refused if present)")

    def as_dict(self) -> dict:
        return {k: getattr(self, k) for k in ("dry_run", "source_counts", "target_counts_before", "inserted",
                                              "already_present", "target_counts_after", "conflicts", "verification",
                                              "not_imported")}


def _bump(counts: dict, organization_id: str, table: str, by: int = 1) -> None:
    counts.setdefault(organization_id, {}).setdefault(table, 0)
    counts[organization_id][table] += by


def _target_counts(connection, organizations: set[str]) -> dict:
    counts = {}
    for organization_id in sorted(organizations):
        act_for(connection, organization_id)
        for table in IDENTITY_TABLES + WORKSPACE_TABLES:
            counts.setdefault(organization_id, {})[table.name] = connection.execute(
                f"SELECT count(*) FROM baec.{table.name}").fetchone()[0]  # RLS: this organization's rows only
        counts[organization_id].update(engine_import.target_counts(connection, organization_id, {LOCAL, SYNTHETIC}))
    return counts


def _differs(existing: dict, wanted: dict) -> bool:
    return any(existing[k] != v for k, v in wanted.items())


def _advance_sequences(migration_url: str, maxima: dict[str, int]) -> None:
    """Move each id sequence past the largest imported id, so ids the application assigns later never collide with
    imported ones. Runs as the migration role, which owns the sequences; only ever moves a sequence forward."""
    db = connect(validate_database_url(migration_url, name=_MIGRATION_URL), name=_MIGRATION_URL)
    try:
        for (table, column), maximum in maxima.items():
            if maximum:
                db.execute("SELECT setval(q.s, GREATEST(COALESCE(pg_sequence_last_value(q.s), 0), %s)) "
                           "FROM (SELECT pg_get_serial_sequence(%s, %s)::regclass AS s) AS q",
                           (maximum, f"baec.{table}", column))
    finally:
        db.close()


def _record(migration_url: str, report: ImportReport, workspaces: list[Workspace]) -> None:
    db = connect(validate_database_url(migration_url, name=_MIGRATION_URL), name=_MIGRATION_URL)
    try:
        detail = {"report": report.as_dict(), "source_migration_logs": {
            f"{ws.workspace}:{ws.directory.name}": ws.migration_log for ws in workspaces}}
        db.execute("INSERT INTO baec.operator_log (kind, detail_json) VALUES ('SQLITE_IMPORT', %s)",
                   (json.dumps(detail, sort_keys=True, default=str),))
    finally:
        db.close()


def run_import(source_root: str | Path, database_url: str | None, *, dry_run: bool,
               migration_url: str | None = None) -> ImportReport:
    """Import (or, with dry_run, rehearse) one source directory. Raises ImportRefused on any conflict or refusal;
    nothing is committed unless the whole import and its verification succeed."""
    require_driver()
    ident, workspaces = open_source(Path(source_root))
    report = None
    try:
        rows = plan(ident, workspaces)
        sources = [ws.engines for ws in workspaces if ws.engines is not None]
        try:
            engine_rows = engine_import.plan_engines(sources)
        except engine_import.EngineImportRefused as error:
            raise ImportRefused(str(error)) from None
        report = ImportReport(dry_run)
        organizations = {r.organization_id for r in rows}
        unknown = {r.organization_id for r in engine_rows} - organizations
        if unknown:
            raise ImportRefused(f"engine records belong to organizations with no identity records: {sorted(unknown)}")
        for row in rows:
            _bump(report.source_counts, row.organization_id, row.table.name)
        for row in engine_rows:
            _bump(report.source_counts, row.organization_id, row.label)
        maxima = {(t, c): max((r.values[c] for r in rows if r.table.name == t), default=0) for t, c in _SEQUENCES}
        if not dry_run:
            if not migration_url:
                raise ImportRefused("a real import needs BAEC_MIGRATION_DATABASE_URL (the migration role)")
            _advance_sequences(migration_url, maxima)
        connection = connect(validate_database_url(database_url), autocommit=True, name="BAEC_DATABASE_URL")
        try:
            psycopg = require_driver()
            connection.execute("BEGIN")
            try:
                report.target_counts_before = _target_counts(connection, organizations)
                current = None
                for row in rows:
                    if row.organization_id != current:
                        act_for(connection, row.organization_id)
                        current = row.organization_id
                    existing = _existing(connection, row)
                    if existing is None:
                        try:
                            connection.execute("SAVEPOINT row_insert")
                            _insert(connection, row)
                            connection.execute("RELEASE SAVEPOINT row_insert")
                        except psycopg.Error as error:
                            connection.execute("ROLLBACK TO SAVEPOINT row_insert")
                            report.conflicts.append({"table": row.table.name, "organization_id": row.organization_id,
                                                     "key": [str(row.values[k]) for k in row.table.key],
                                                     "refused_by_database": type(error).__name__})
                            continue
                        _bump(report.inserted, row.organization_id, row.table.name)
                    elif _differs(existing, {**_scope(row), **row.values}):
                        report.conflicts.append({"table": row.table.name, "organization_id": row.organization_id,
                                                 "key": [str(row.values[k]) for k in row.table.key],
                                                 "existing_record_differs": True})
                    else:
                        _bump(report.already_present, row.organization_id, row.table.name)
                for row in engine_rows:
                    act_for(connection, row.organization_id, row.workspace)
                    current = None
                    found = engine_import.existing(connection, row)
                    if found is None:
                        try:
                            connection.execute("SAVEPOINT row_insert")
                            engine_import.insert(connection, row)
                            connection.execute("RELEASE SAVEPOINT row_insert")
                        except psycopg.Error as error:
                            connection.execute("ROLLBACK TO SAVEPOINT row_insert")
                            report.conflicts.append({"table": row.label, "organization_id": row.organization_id,
                                                     "key": [str(row.values[k]) for k in engine_import.primary_key(
                                                         row.schema, row.table)],
                                                     "refused_by_database": type(error).__name__})
                            continue
                        _bump(report.inserted, row.organization_id, row.label)
                    elif found != row.values:
                        report.conflicts.append({"table": row.label, "organization_id": row.organization_id,
                                                 "key": [str(row.values[k]) for k in engine_import.primary_key(
                                                     row.schema, row.table)], "existing_record_differs": True})
                    else:
                        _bump(report.already_present, row.organization_id, row.label)
                report.target_counts_after = _target_counts(connection, organizations)
                if report.conflicts:
                    raise ImportRefused(f"{len(report.conflicts)} conflicting records; nothing was imported")
                report.verification = verify(connection, ident, workspaces, organizations)
                engine_import.verify_engines(connection, sources, engine_rows, report.verification)
                report.verification["matches"] = not report.verification["mismatches"]
                if not report.verification["matches"]:
                    raise ImportRefused("the imported records do not match the source; nothing was imported")
            except BaseException:
                connection.execute("ROLLBACK")
                raise
            connection.execute("ROLLBACK" if dry_run else "COMMIT")
        finally:
            connection.close()
        if not dry_run:
            _record(migration_url, report, workspaces)
        return report
    except ImportRefused as error:
        error.report = report
        raise
    finally:
        _close(ident, workspaces)


# --- verification -----------------------------------------------------------------------------------------------------


def _without_seq(items: list[dict]) -> list[dict]:
    return [{k: v for k, v in i.items() if k != "seq"} for i in items]


def _compare(results: dict, label: str, source, target) -> None:
    results["checked"] += 1
    if source != target:
        results["mismatches"].append(label)


def verify(connection, ident: sqlite3.Connection, workspaces: list[Workspace], organizations: set[str]) -> dict:
    """Re-read every imported record through the PostgreSQL stores, acting for its organization, and compare it with
    the same read through the SQLite stores. Timestamps compare as instants."""
    results = {"checked": 0, "mismatches": []}
    source_identity = identity.IdentityStore(ident)
    for organization_id in sorted(organizations):
        act_for(connection, organization_id)
        target = ProductSession(connection, organization_id, LOCAL).identity
        _compare(results, f"organization {organization_id}", source_identity.organization(organization_id),
                 target.organization(organization_id))
        users = source_identity.users(organization_id)
        _compare(results, f"users of {organization_id}", users, target.users(organization_id))
        for user in users:
            _compare(results, f"identity audit of {user.user_id}", source_identity.audit(user.user_id),
                     target.audit(user.user_id))
    for ws in workspaces:
        source_console = console_store.ConsoleStore(ws.console)
        source_crm = crm_store.CRMStore(ws.crm) if ws.crm is not None else None
        for organization_id in sorted(set(ws.owner.values())):
            act_for(connection, organization_id)
            session = ProductSession(connection, organization_id, ws.workspace)
            owned = {a for a, o in ws.owner.items() if o == organization_id}
            where = f"{ws.directory.name}/{organization_id}"
            _compare(results, f"{where} owned accounts", owned, session.console.owned_account_ids(organization_id))
            requests = [r for r in source_console.preparation_requests() if r["account_id"] in owned]
            _compare(results, f"{where} preparation requests", _without_seq(requests),
                     _without_seq(session.console.preparation_requests()))
            _compare(results, f"{where} drafts", [d for d in source_console.drafts() if d["account_id"] in owned],
                     session.console.drafts())
            request_ids = {r["request_id"] for r in requests}
            preparations = [p for p in source_console.preparations() if p["request_id"] in request_ids]
            _compare(results, f"{where} preparations", preparations, session.console.preparations())
            for p in preparations:
                pid = p["preparation_id"]
                _compare(results, f"{where} provenance {pid}", source_console.provenance(pid),
                         session.console.provenance(pid))
                _compare(results, f"{where} edits {pid}", source_console.edits(pid), session.console.edits(pid))
                _compare(results, f"{where} reviews {pid}", source_console.reviews(pid), session.console.reviews(pid))
            for review_id in {r["review_id"] for r in requests} | {
                    r[0] for r in ws.console.execute("SELECT review_id FROM preparation_blocks")}:
                blocks = [b for b in source_console.block_decisions(review_id) if b["account_id"] in owned]
                _compare(results, f"{where} blocks {review_id}", blocks, session.console.block_decisions(review_id))
            for account_id in sorted(owned):
                _compare(results, f"{where} account {account_id}", source_console.account_meta(account_id),
                         session.console.account_meta(account_id))
                _compare(results, f"{where} events {account_id}", source_console.events(account_id),
                         session.console.events(account_id))
                _compare(results, f"{where} attributions {account_id}", source_console.attributions(account_id),
                         session.console.attributions(account_id))
                for (interaction_id,) in ws.console.execute("SELECT interaction_id FROM interaction_meta "
                                                            "WHERE account_id = ?", (account_id,)):
                    _compare(results, f"{where} interaction {interaction_id}",
                             source_console.interaction_meta(interaction_id),
                             session.console.interaction_meta(interaction_id))
                if source_crm is None:
                    continue
                by_id = lambda items, key: sorted(items, key=lambda i: i[key])  # noqa: E731 - order is query-defined
                _compare(results, f"{where} contacts {account_id}", by_id(source_crm.contacts(account_id), "contact_id"),
                         by_id(session.crm.contacts(account_id), "contact_id"))
                _compare(results, f"{where} tasks {account_id}",
                         by_id(source_crm.tasks(account_id=account_id), "task_id"),
                         by_id(session.crm.tasks(account_id=account_id), "task_id"))
                _compare(results, f"{where} activities {account_id}",
                         by_id(source_crm.activities(account_id), "activity_id"),
                         by_id(session.crm.activities(account_id), "activity_id"))
                for (interaction_id,) in ws.crm.execute("SELECT interaction_id FROM interaction_links "
                                                        "WHERE account_id = ?", (account_id,)):
                    _compare(results, f"{where} interaction link {interaction_id}",
                             source_crm.interaction_link(interaction_id), session.crm.interaction_link(interaction_id))
    results["matches"] = not results["mismatches"]
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Import BAEC product data from SQLite into PostgreSQL.")
    parser.add_argument("source_root")
    parser.add_argument("--dry-run", action="store_true", help="perform and verify the import, then roll it back")
    args = parser.parse_args(argv)
    try:
        report = run_import(args.source_root, os.environ.get("BAEC_DATABASE_URL"), dry_run=args.dry_run,
                            migration_url=os.environ.get("BAEC_MIGRATION_DATABASE_URL"))
    except PostgresError as error:
        print(f"import refused: {error}", file=sys.stderr)
        partial = getattr(error, "report", None)
        if partial is not None:
            print(json.dumps(partial.as_dict(), indent=2, sort_keys=True, default=str))
        return 1
    print(json.dumps(report.as_dict(), indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
