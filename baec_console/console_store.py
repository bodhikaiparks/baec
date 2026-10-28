"""The console-owned SQLite store: product metadata and Engine 3 preparation records.

What lives here, and why it is not a second copy of an engine record:

* account metadata (industry, owner note, synthetic flag, creation time). Engine 1 accounts carry only an id,
  a name, and a state;
* interaction metadata (buyer role, exact statement, seller notes) for the structured form a salesperson filled
  in. The authoritative interaction text is Engine 1's; this is the breakdown of how the console composed it;
* condition drafts: what a salesperson captured before human confirmation. Engine 1 has no draft concept; a
  confirmed draft points at the Engine 1 BAEC that became authoritative;
* Engine 3 preparation records. Engine 3 has no persistence of its own (it is a pure validator), so the
  Preparation Request, the generated outputs with their validation reports, human edits, and human review
  decisions are kept here, append-only;
* console events for console-only actions (account created, preparation requested, and so on). Engine 1 and
  Engine 2 history is read from those engines' own records, never copied here;
* account ownership (V3): which organization owns each account. Engine records carry no organization, so this
  product-side mapping is the tenancy boundary every console operation checks before touching an engine record;
* attributions (V4): which user performed each recorded human action. Every product, CRM, and engine record keeps
  its own actor label (a display name) unchanged; this product-side mapping adds the durable user_id and
  organization_id, with the display name the user had at that moment as an immutable snapshot. Engine records are
  keyed by their own identities, so no engine schema changes.

Preparation tables and events are append-only: triggers refuse UPDATE and DELETE.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterator

SCHEMA_VERSION = 4

# Fixed, versioned provenance values for console-generated preparations (see engine3_adapter and README_CONSOLE.md).
# The CHECK constraints below make it impossible to store a console preparation that claims any other origin.
CONSOLE_GENERATOR_ORIGIN = "DETERMINISTIC_CONSOLE_TEMPLATE_V1"
ENGINE3_VALIDATOR_ORIGIN = "AI_INFERENCE"
ORIGIN_COMPATIBILITY_NOTE = "E3-ORIGIN-COMPAT-V1"
ORIGIN_COMPATIBILITY_TEXT = (
    "AI_INFERENCE is the existing Engine 3 validator-compatible origin value for machine-produced drafts. "
    "No model was used to generate this preparation.")

_TABLES = {
    "console_meta": "(key TEXT PRIMARY KEY, value TEXT NOT NULL)",
    "prefs": "(key TEXT PRIMARY KEY, value TEXT NOT NULL)",
    "account_meta": (
        "(account_id TEXT PRIMARY KEY, display_name TEXT NOT NULL, industry TEXT, owner_note TEXT, "
        "synthetic INTEGER NOT NULL CHECK (synthetic IN (0, 1)), created_at TEXT NOT NULL, created_by TEXT NOT NULL)"
    ),
    "interaction_meta": (
        "(interaction_id TEXT PRIMARY KEY, account_id TEXT NOT NULL, buyer_role TEXT, buyer_statement TEXT NOT NULL, "
        "seller_notes TEXT, recorded_at TEXT NOT NULL, recorded_by TEXT NOT NULL)"
    ),
    "condition_drafts": (
        "(draft_id TEXT PRIMARY KEY, account_id TEXT NOT NULL, interaction_id TEXT NOT NULL, form_json TEXT NOT NULL, "
        "captured_at TEXT NOT NULL, captured_by TEXT NOT NULL)"
    ),
    # One row per resolved draft: the draft became an Engine 1 record (confirmed or not confirmable), or was discarded.
    "draft_resolutions": (
        "(draft_id TEXT PRIMARY KEY REFERENCES condition_drafts (draft_id), resolution TEXT NOT NULL "
        "CHECK (resolution IN ('CONFIRMED', 'RECORDED_NOT_CONFIRMABLE', 'DISCARDED')), baec_id TEXT, "
        "resolved_at TEXT NOT NULL, resolved_by TEXT NOT NULL)"
    ),
    "preparation_requests": (
        "(request_id TEXT PRIMARY KEY, account_id TEXT NOT NULL, baec_id TEXT NOT NULL, review_id TEXT NOT NULL, "
        "candidate_id TEXT NOT NULL, request_json TEXT NOT NULL, snapshot_json TEXT NOT NULL, "
        "context_digest TEXT NOT NULL, requested_at TEXT NOT NULL, requested_by TEXT NOT NULL)"
    ),
    "preparations": (
        "(preparation_id TEXT PRIMARY KEY, request_id TEXT NOT NULL REFERENCES preparation_requests (request_id), "
        "generator TEXT NOT NULL, artifacts_json TEXT NOT NULL, generated_at TEXT NOT NULL, generated_by TEXT NOT NULL)"
    ),
    "preparation_edits": (
        "(edit_id TEXT PRIMARY KEY, preparation_id TEXT NOT NULL REFERENCES preparations (preparation_id), "
        "parent_artifact_id TEXT NOT NULL, artifact_json TEXT NOT NULL, edited_at TEXT NOT NULL, edited_by TEXT NOT NULL)"
    ),
    "preparation_reviews": (
        "(preparation_id TEXT NOT NULL REFERENCES preparations (preparation_id), artifact_id TEXT NOT NULL, "
        "decision TEXT NOT NULL CHECK (decision IN ('ACCEPT_AS_DRAFTED', 'EDIT', 'REJECT')), "
        "edit_id TEXT REFERENCES preparation_edits (edit_id), attestations_json TEXT NOT NULL, reason TEXT, "
        "reviewed_at TEXT NOT NULL, reviewed_by TEXT NOT NULL, PRIMARY KEY (preparation_id, artifact_id))"
    ),
    # V2. One row per generated preparation, written in the same transaction as the preparation.
    "preparation_provenance": (
        "(preparation_id TEXT PRIMARY KEY REFERENCES preparations (preparation_id), "
        "bound_review_id TEXT NOT NULL, bound_candidate_id TEXT NOT NULL, "
        f"console_generator_origin TEXT NOT NULL CHECK (console_generator_origin = '{CONSOLE_GENERATOR_ORIGIN}'), "
        f"engine3_validator_origin TEXT NOT NULL CHECK (engine3_validator_origin = '{ENGINE3_VALIDATOR_ORIGIN}'), "
        f"origin_compatibility_note TEXT NOT NULL CHECK (origin_compatibility_note = '{ORIGIN_COMPATIBILITY_NOTE}'), "
        "model_used INTEGER NOT NULL CHECK (model_used = 0))"
    ),
    # V2. Human decisions to block or unblock preparation from one verified correspondence (Engine 2 review).
    # Append-only: the current state is the latest decision; earlier decisions are never changed.
    "preparation_blocks": (
        "(decision_id INTEGER PRIMARY KEY AUTOINCREMENT, account_id TEXT NOT NULL, review_id TEXT NOT NULL, "
        "action TEXT NOT NULL CHECK (action IN ('BLOCK', 'UNBLOCK')), reason TEXT, decided_at TEXT NOT NULL, "
        "decided_by TEXT NOT NULL)"
    ),
    # V3. One row per account, never changed: the organization that owns it. basis records how ownership arose.
    "account_ownership": (
        "(account_id TEXT PRIMARY KEY, organization_id TEXT NOT NULL, recorded_at TEXT NOT NULL, "
        "recorded_by_user_id TEXT NOT NULL, basis TEXT NOT NULL CHECK (basis IN ('CREATED', 'V03_MIGRATION')))"
    ),
    # V4. One row per attributed action, never changed. record_type names the record family (ATTR_* below),
    # record_id that record's own identity, action what the user did to it. basis RECORDED is written with the action;
    # BACKFILL is the one-time V3 -> V4 backfill (see migration_log).
    "attributions": (
        "(record_type TEXT NOT NULL, record_id TEXT NOT NULL, action TEXT NOT NULL, account_id TEXT NOT NULL, "
        "user_id TEXT NOT NULL, organization_id TEXT NOT NULL, display_name_snapshot TEXT NOT NULL, "
        "recorded_at TEXT NOT NULL, basis TEXT NOT NULL CHECK (basis IN ('RECORDED', 'BACKFILL')), "
        "PRIMARY KEY (record_type, record_id, action))"
    ),
    # V4. What each data migration did, so a backfill can be audited after the fact.
    "migration_log": "(seq INTEGER PRIMARY KEY AUTOINCREMENT, migration TEXT NOT NULL, completed_at TEXT NOT NULL, "
                     "detail_json TEXT NOT NULL)",
    "events": (
        "(event_id INTEGER PRIMARY KEY AUTOINCREMENT, account_id TEXT NOT NULL, event_type TEXT NOT NULL, "
        "actor_kind TEXT NOT NULL, actor TEXT NOT NULL, occurred_at TEXT NOT NULL, summary TEXT NOT NULL, "
        "ref_id TEXT)"
    ),
}

_APPEND_ONLY = ("interaction_meta", "condition_drafts", "draft_resolutions", "preparation_requests", "preparations",
                "preparation_edits", "preparation_reviews", "events", "preparation_provenance", "preparation_blocks",
                "account_ownership", "attributions", "migration_log")
_V2_TABLES = ("preparation_provenance", "preparation_blocks")
_V3_TABLES = ("account_ownership",)
_V4_TABLES = ("attributions", "migration_log")

# Accounts that existed before V3 have no ownership row. The V2 -> V3 migration marks the store PENDING; the service
# then assigns every existing Engine 1 account to the local-development organization once and marks it DONE. After
# that, an account without an ownership row is visible to no one (fail closed); it is never adopted silently.
OWNERSHIP_BACKFILL = "ownership_backfill"
BACKFILL_PENDING, BACKFILL_DONE = "PENDING", "DONE"
# The V3 -> V4 migration marks the attribution backfill PENDING the same way; the service completes it once.
ATTRIBUTION_BACKFILL = "attribution_backfill"

# Attribution record types. Console and CRM records use their own ids; Engine 1 records use the BAEC id, judgment id,
# or transition id; Engine 2 records use the audit entity type and id ("engine2." + entity_type), with the audit
# event type as the action, so each Engine 2 audit event a human caused maps to exactly one attribution.
ATTR_ACCOUNT = "console.account"
ATTR_INTERACTION = "console.interaction"
ATTR_DRAFT = "console.condition_draft"
ATTR_DRAFT_RESOLUTION = "console.draft_resolution"
ATTR_PREPARATION_REQUEST = "console.preparation_request"
ATTR_PREPARATION = "console.preparation"
ATTR_PREPARATION_EDIT = "console.preparation_edit"
ATTR_PREPARATION_REVIEW = "console.preparation_review"  # record_id is "<preparation_id>/<artifact_id>"
ATTR_PREPARATION_BLOCK = "console.preparation_block"
ATTR_EVENT = "console.event"
ATTR_CRM_CONTACT = "crm.contact"
ATTR_CRM_TASK = "crm.task"
ATTR_CRM_ACTIVITY = "crm.activity"
ATTR_CRM_INTERACTION_LINK = "crm.interaction_link"
ATTR_CRM_ACTIVITY_ENGINE1_LINK = "crm.activity_engine1_link"
ATTR_BAEC_CONFIRMATION = "engine1.baec_confirmation"
ATTR_DORMANCY_JUDGMENT = "engine1.dormancy_judgment"
ATTR_STATE_TRANSITION = "engine1.state_transition"
ATTR_ENGINE2 = "engine2."


class ConsoleStoreError(Exception):
    """The console store refused an operation or holds data it cannot read."""


def _schema(tables=None) -> list[str]:
    names = tuple(_TABLES) if tables is None else tables
    statements = [f"CREATE TABLE {name} {_TABLES[name]}" for name in names]
    for table in _APPEND_ONLY:
        if table not in names:
            continue
        for verb in ("UPDATE", "DELETE"):
            statements.append(
                f"CREATE TRIGGER {table}_no_{verb.lower()} BEFORE {verb} ON {table} "
                f"BEGIN SELECT RAISE(ABORT, '{table} is append-only'); END"
            )
    return statements


def _migrate_v1_to_v2(connection: sqlite3.Connection) -> None:
    """Additive: create the two V2 tables and record provenance for V1 preparations. Nothing existing changes.

    Every V1 preparation was produced by the same deterministic template and validated under AI_INFERENCE, and
    its Preparation Request already stores the exact Engine 2 review it was requested from, so the backfilled
    provenance is a statement of fact, not an inference.
    """
    with transaction(connection):
        for statement in _schema(_V2_TABLES):
            connection.execute(statement)
        rows = connection.execute(
            "SELECT p.preparation_id, p.generator, r.review_id, r.candidate_id FROM preparations p "
            "JOIN preparation_requests r ON r.request_id = p.request_id ORDER BY p.rowid").fetchall()
        for preparation_id, generator, review_id, candidate_id in rows:
            if generator != CONSOLE_GENERATOR_ORIGIN:
                raise ConsoleStoreError(f"preparation {preparation_id} has unknown generator {generator!r}")
            connection.execute("INSERT INTO preparation_provenance VALUES (?, ?, ?, ?, ?, ?, 0)",
                               (preparation_id, review_id, candidate_id, CONSOLE_GENERATOR_ORIGIN,
                                ENGINE3_VALIDATOR_ORIGIN, ORIGIN_COMPATIBILITY_NOTE))
        connection.execute("UPDATE console_meta SET value = '2' WHERE key = 'schema_version'")


def _migrate_v2_to_v3(connection: sqlite3.Connection) -> None:
    """Additive: create the ownership table and mark the ownership backfill pending. Nothing existing changes."""
    with transaction(connection):
        for statement in _schema(_V3_TABLES):
            connection.execute(statement)
        connection.execute("INSERT INTO console_meta VALUES (?, ?)", (OWNERSHIP_BACKFILL, BACKFILL_PENDING))
        connection.execute("UPDATE console_meta SET value = '3' WHERE key = 'schema_version'")


def _migrate_v3_to_v4(connection: sqlite3.Connection) -> None:
    """Additive: create the attribution tables and mark the attribution backfill pending. Nothing existing changes."""
    with transaction(connection):
        for statement in _schema(_V4_TABLES):
            connection.execute(statement)
        connection.execute("INSERT INTO console_meta VALUES (?, ?)", (ATTRIBUTION_BACKFILL, BACKFILL_PENDING))
        connection.execute("UPDATE console_meta SET value = '4' WHERE key = 'schema_version'")


def _supported(meta: dict) -> bool:
    return (meta.get("engine") == "BAEC_CONSOLE" and meta.get("schema_version") == str(SCHEMA_VERSION)
            and meta.get(OWNERSHIP_BACKFILL) in (BACKFILL_PENDING, BACKFILL_DONE)
            and meta.get(ATTRIBUTION_BACKFILL) in (BACKFILL_PENDING, BACKFILL_DONE) and len(meta) == 4)


def open_console_store(path: str) -> sqlite3.Connection:
    """Open (creating if new) a console store. The caller closes it."""
    connection = sqlite3.connect(str(path), isolation_level=None)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        empty = connection.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()[0] == 0
        if empty:
            with transaction(connection):
                for statement in _schema():
                    connection.execute(statement)
                connection.execute("INSERT INTO console_meta VALUES ('engine', 'BAEC_CONSOLE'), ('schema_version', ?), "
                                   "(?, ?), (?, ?)", (str(SCHEMA_VERSION), OWNERSHIP_BACKFILL, BACKFILL_DONE,
                                                      ATTRIBUTION_BACKFILL, BACKFILL_DONE))
        meta = dict(connection.execute("SELECT key, value FROM console_meta").fetchall())
        if meta == {"engine": "BAEC_CONSOLE", "schema_version": "1"}:
            _migrate_v1_to_v2(connection)
            meta = dict(connection.execute("SELECT key, value FROM console_meta").fetchall())
        if meta == {"engine": "BAEC_CONSOLE", "schema_version": "2"}:
            _migrate_v2_to_v3(connection)
            meta = dict(connection.execute("SELECT key, value FROM console_meta").fetchall())
        if meta.get("schema_version") == "3" and len(meta) == 3 and OWNERSHIP_BACKFILL in meta:
            _migrate_v3_to_v4(connection)
            meta = dict(connection.execute("SELECT key, value FROM console_meta").fetchall())
        if not _supported(meta):
            raise ConsoleStoreError(f"unsupported console store: {meta}")
    except BaseException:
        connection.close()
        raise
    return connection


def legacy_actor_name(path: str | Path) -> str | None:
    """The V0.3 "Your name" preference of an existing console store, read without opening it for writing."""
    if not Path(path).is_file():
        return None
    connection = sqlite3.connect(f"file:{Path(path)}?mode=ro", uri=True)
    try:
        if connection.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'prefs'").fetchone() is None:
            return None
        row = connection.execute("SELECT value FROM prefs WHERE key = 'actor'").fetchone()
        return row[0].strip() if row and row[0].strip() else None
    finally:
        connection.close()


@contextmanager
def transaction(connection: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    connection.execute("BEGIN IMMEDIATE")
    try:
        yield connection
        connection.execute("COMMIT")
    except BaseException:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise


def ts(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ConsoleStoreError("timestamps must be timezone-aware")
    return value.isoformat()


def dt(text: str) -> datetime:
    return datetime.fromisoformat(text)


def display_time(value: datetime) -> datetime:
    """The one conversion from a stored timestamp to the console's display zone (the local zone). Display only:
    every user-visible date, in the UI and in generated copy, goes through here so the same instant never shows
    two calendar dates. Stored timestamps are never changed."""
    return value.astimezone()


@dataclass(frozen=True)
class ConsoleEvent:
    event_id: int
    account_id: str
    event_type: str
    actor_kind: str
    actor: str
    occurred_at: datetime
    summary: str
    ref_id: str | None


class ConsoleStore:
    """Thin reads and writes over one open console-store connection."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.db = connection

    # --- events ------------------------------------------------------------------

    def add_event(self, account_id: str, event_type: str, actor_kind: str, actor: str, occurred_at: datetime,
                  summary: str, ref_id: str | None = None) -> int:
        return self.db.execute("INSERT INTO events (account_id, event_type, actor_kind, actor, occurred_at, summary, "
                               "ref_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
                               (account_id, event_type, actor_kind, actor, ts(occurred_at), summary, ref_id)).lastrowid

    def events(self, account_id: str) -> list[ConsoleEvent]:
        rows = self.db.execute("SELECT event_id, account_id, event_type, actor_kind, actor, occurred_at, summary, ref_id "
                               "FROM events WHERE account_id = ? ORDER BY event_id", (account_id,)).fetchall()
        return [ConsoleEvent(r[0], r[1], r[2], r[3], r[4], dt(r[5]), r[6], r[7]) for r in rows]

    # --- accounts and interactions -------------------------------------------------

    def add_account_meta(self, account_id: str, display_name: str, industry: str | None, owner_note: str | None,
                         synthetic: bool, created_at: datetime, created_by: str) -> None:
        self.db.execute("INSERT INTO account_meta VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (account_id, display_name, industry, owner_note, int(synthetic), ts(created_at), created_by))

    def account_meta(self, account_id: str) -> dict | None:
        row = self.db.execute("SELECT account_id, display_name, industry, owner_note, synthetic, created_at, created_by "
                              "FROM account_meta WHERE account_id = ?", (account_id,)).fetchone()
        if row is None:
            return None
        return {"account_id": row[0], "display_name": row[1], "industry": row[2], "owner_note": row[3],
                "synthetic": bool(row[4]), "created_at": dt(row[5]), "created_by": row[6]}

    def add_interaction_meta(self, interaction_id: str, account_id: str, buyer_role: str | None, buyer_statement: str,
                             seller_notes: str | None, recorded_at: datetime, recorded_by: str) -> None:
        self.db.execute("INSERT INTO interaction_meta VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (interaction_id, account_id, buyer_role, buyer_statement, seller_notes, ts(recorded_at),
                         recorded_by))

    def interaction_meta(self, interaction_id: str) -> dict | None:
        row = self.db.execute("SELECT interaction_id, account_id, buyer_role, buyer_statement, seller_notes, recorded_at, "
                              "recorded_by FROM interaction_meta WHERE interaction_id = ?", (interaction_id,)).fetchone()
        if row is None:
            return None
        return {"interaction_id": row[0], "account_id": row[1], "buyer_role": row[2], "buyer_statement": row[3],
                "seller_notes": row[4], "recorded_at": dt(row[5]), "recorded_by": row[6]}

    # --- condition drafts ------------------------------------------------------------

    def add_draft(self, draft_id: str, account_id: str, interaction_id: str, form: dict, captured_at: datetime,
                  captured_by: str) -> None:
        self.db.execute("INSERT INTO condition_drafts VALUES (?, ?, ?, ?, ?, ?)",
                        (draft_id, account_id, interaction_id, json.dumps(form, sort_keys=True), ts(captured_at),
                         captured_by))

    def resolve_draft(self, draft_id: str, resolution: str, baec_id: str | None, resolved_at: datetime,
                      resolved_by: str) -> None:
        self.db.execute("INSERT INTO draft_resolutions VALUES (?, ?, ?, ?, ?)",
                        (draft_id, resolution, baec_id, ts(resolved_at), resolved_by))

    def drafts(self, account_id: str | None = None) -> list[dict]:
        sql = ("SELECT d.draft_id, d.account_id, d.interaction_id, d.form_json, d.captured_at, d.captured_by, "
               "r.resolution, r.baec_id, r.resolved_at, r.resolved_by FROM condition_drafts d "
               "LEFT JOIN draft_resolutions r ON r.draft_id = d.draft_id")
        args: tuple = ()
        if account_id is not None:
            sql += " WHERE d.account_id = ?"
            args = (account_id,)
        rows = self.db.execute(sql + " ORDER BY d.rowid", args).fetchall()
        return [{"draft_id": r[0], "account_id": r[1], "interaction_id": r[2], "form": json.loads(r[3]),
                 "captured_at": dt(r[4]), "captured_by": r[5], "resolution": r[6], "baec_id": r[7],
                 "resolved_at": dt(r[8]) if r[8] else None, "resolved_by": r[9]} for r in rows]

    def draft(self, draft_id: str) -> dict:
        for item in self.drafts():
            if item["draft_id"] == draft_id:
                return item
        raise ConsoleStoreError(f"no condition draft {draft_id}")

    # --- preparation -----------------------------------------------------------------

    def add_preparation_request(self, request_id: str, account_id: str, baec_id: str, review_id: str,
                                candidate_id: str, request: dict, snapshot: dict, context_digest: str,
                                requested_at: datetime, requested_by: str) -> None:
        self.db.execute("INSERT INTO preparation_requests VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (request_id, account_id, baec_id, review_id, candidate_id, json.dumps(request, sort_keys=True),
                         json.dumps(snapshot, sort_keys=True), context_digest, ts(requested_at), requested_by))

    def preparation_requests(self, account_id: str | None = None) -> list[dict]:
        # rowid is the insertion order: the table is append-only (UPDATE and DELETE are refused by trigger).
        sql = ("SELECT request_id, account_id, baec_id, review_id, candidate_id, request_json, snapshot_json, "
               "context_digest, requested_at, requested_by, rowid FROM preparation_requests")
        args: tuple = ()
        if account_id is not None:
            sql += " WHERE account_id = ?"
            args = (account_id,)
        rows = self.db.execute(sql + " ORDER BY rowid", args).fetchall()
        return [{"request_id": r[0], "account_id": r[1], "baec_id": r[2], "review_id": r[3], "candidate_id": r[4],
                 "request": json.loads(r[5]), "snapshot": json.loads(r[6]), "context_digest": r[7],
                 "requested_at": dt(r[8]), "requested_by": r[9], "seq": r[10]} for r in rows]

    def preparation_request(self, request_id: str) -> dict:
        for item in self.preparation_requests():
            if item["request_id"] == request_id:
                return item
        raise ConsoleStoreError(f"no preparation request {request_id}")

    def add_preparation(self, preparation_id: str, request_id: str, generator: str, artifacts: list[dict],
                        generated_at: datetime, generated_by: str) -> None:
        """Store a console-generated preparation with its fixed provenance and correspondence binding.

        Call inside a transaction: the preparation and its provenance row are written together.
        """
        request = self.preparation_request(request_id)
        self.db.execute("INSERT INTO preparations VALUES (?, ?, ?, ?, ?, ?)",
                        (preparation_id, request_id, generator, json.dumps(artifacts, sort_keys=True), ts(generated_at),
                         generated_by))
        self.db.execute("INSERT INTO preparation_provenance VALUES (?, ?, ?, ?, ?, ?, 0)",
                        (preparation_id, request["review_id"], request["candidate_id"], generator,
                         ENGINE3_VALIDATOR_ORIGIN, ORIGIN_COMPATIBILITY_NOTE))

    def provenance(self, preparation_id: str) -> dict:
        row = self.db.execute("SELECT preparation_id, bound_review_id, bound_candidate_id, console_generator_origin, "
                              "engine3_validator_origin, origin_compatibility_note, model_used "
                              "FROM preparation_provenance WHERE preparation_id = ?", (preparation_id,)).fetchone()
        if row is None:
            raise ConsoleStoreError(f"preparation {preparation_id} has no provenance record")
        return {"preparation_id": row[0], "bound_review_id": row[1], "bound_candidate_id": row[2],
                "console_generator_origin": row[3], "engine3_validator_origin": row[4],
                "origin_compatibility_note": row[5], "model_used": bool(row[6])}

    # --- human preparation blocks ------------------------------------------------------

    def add_block_decision(self, account_id: str, review_id: str, action: str, reason: str | None,
                           decided_at: datetime, decided_by: str) -> int:
        return self.db.execute(
            "INSERT INTO preparation_blocks (account_id, review_id, action, reason, decided_at, decided_by) "
            "VALUES (?, ?, ?, ?, ?, ?)", (account_id, review_id, action, reason, ts(decided_at), decided_by)).lastrowid

    def block_decisions(self, review_id: str) -> list[dict]:
        rows = self.db.execute("SELECT decision_id, account_id, review_id, action, reason, decided_at, decided_by "
                               "FROM preparation_blocks WHERE review_id = ? ORDER BY decision_id", (review_id,)).fetchall()
        return [{"decision_id": r[0], "account_id": r[1], "review_id": r[2], "action": r[3], "reason": r[4],
                 "decided_at": dt(r[5]), "decided_by": r[6]} for r in rows]

    def is_blocked(self, review_id: str) -> bool:
        decisions = self.block_decisions(review_id)
        return bool(decisions) and decisions[-1]["action"] == "BLOCK"

    def preparations(self) -> list[dict]:
        rows = self.db.execute("SELECT preparation_id, request_id, generator, artifacts_json, generated_at, generated_by "
                               "FROM preparations ORDER BY rowid").fetchall()
        return [{"preparation_id": r[0], "request_id": r[1], "generator": r[2], "artifacts": json.loads(r[3]),
                 "generated_at": dt(r[4]), "generated_by": r[5]} for r in rows]

    def preparation(self, preparation_id: str) -> dict:
        for item in self.preparations():
            if item["preparation_id"] == preparation_id:
                return item
        raise ConsoleStoreError(f"no preparation {preparation_id}")

    def add_edit(self, edit_id: str, preparation_id: str, parent_artifact_id: str, artifact: dict,
                 edited_at: datetime, edited_by: str) -> None:
        self.db.execute("INSERT INTO preparation_edits VALUES (?, ?, ?, ?, ?, ?)",
                        (edit_id, preparation_id, parent_artifact_id, json.dumps(artifact, sort_keys=True),
                         ts(edited_at), edited_by))

    def edits(self, preparation_id: str) -> list[dict]:
        rows = self.db.execute("SELECT edit_id, preparation_id, parent_artifact_id, artifact_json, edited_at, edited_by "
                               "FROM preparation_edits WHERE preparation_id = ? ORDER BY rowid",
                               (preparation_id,)).fetchall()
        return [{"edit_id": r[0], "preparation_id": r[1], "parent_artifact_id": r[2], "artifact": json.loads(r[3]),
                 "edited_at": dt(r[4]), "edited_by": r[5]} for r in rows]

    def add_review(self, preparation_id: str, artifact_id: str, decision: str, edit_id: str | None,
                   attestations: dict, reason: str | None, reviewed_at: datetime, reviewed_by: str) -> None:
        self.db.execute("INSERT INTO preparation_reviews VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (preparation_id, artifact_id, decision, edit_id, json.dumps(attestations, sort_keys=True),
                         reason, ts(reviewed_at), reviewed_by))

    def reviews(self, preparation_id: str) -> dict[str, dict]:
        rows = self.db.execute("SELECT artifact_id, decision, edit_id, attestations_json, reason, reviewed_at, reviewed_by "
                               "FROM preparation_reviews WHERE preparation_id = ? ORDER BY rowid",
                               (preparation_id,)).fetchall()
        return {r[0]: {"artifact_id": r[0], "decision": r[1], "edit_id": r[2], "attestations": json.loads(r[3]),
                       "reason": r[4], "reviewed_at": dt(r[5]), "reviewed_by": r[6]} for r in rows}

    def account_ids(self) -> list[str]:
        return [r[0] for r in self.db.execute("SELECT account_id FROM account_meta ORDER BY rowid").fetchall()]

    # --- account ownership (V3) --------------------------------------------------------------------------------

    def add_ownership(self, account_id: str, organization_id: str, recorded_at: datetime, recorded_by_user_id: str,
                      basis: str = "CREATED") -> None:
        self.db.execute("INSERT INTO account_ownership VALUES (?, ?, ?, ?, ?)",
                        (account_id, organization_id, ts(recorded_at), recorded_by_user_id, basis))

    def organization_of(self, account_id: str) -> str | None:
        row = self.db.execute("SELECT organization_id FROM account_ownership WHERE account_id = ?",
                              (account_id,)).fetchone()
        return None if row is None else row[0]

    def owned_account_ids(self, organization_id: str) -> set[str]:
        return {r[0] for r in self.db.execute("SELECT account_id FROM account_ownership WHERE organization_id = ?",
                                              (organization_id,)).fetchall()}

    def owning_organizations(self) -> set[str]:
        return {r[0] for r in self.db.execute("SELECT DISTINCT organization_id FROM account_ownership").fetchall()}

    def ownership_backfill(self) -> str:
        return self.db.execute("SELECT value FROM console_meta WHERE key = ?", (OWNERSHIP_BACKFILL,)).fetchone()[0]

    def complete_ownership_backfill(self, account_ids: list[str], organization_id: str, recorded_at: datetime,
                                    recorded_by_user_id: str) -> int:
        """Assign every listed account that has no owner yet to `organization_id`, once. Call inside a transaction."""
        if self.ownership_backfill() == BACKFILL_DONE:
            return 0
        added = 0
        for account_id in account_ids:
            if self.organization_of(account_id) is None:
                self.add_ownership(account_id, organization_id, recorded_at, recorded_by_user_id, "V03_MIGRATION")
                added += 1
        self.db.execute("UPDATE console_meta SET value = ? WHERE key = ?", (BACKFILL_DONE, OWNERSHIP_BACKFILL))
        return added

    # --- attribution (V4) ------------------------------------------------------------------------------------------

    def add_attribution(self, record_type: str, record_id: str, action: str, account_id: str, user_id: str,
                        organization_id: str, display_name_snapshot: str, recorded_at: datetime,
                        basis: str = "RECORDED") -> None:
        self.db.execute("INSERT INTO attributions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (record_type, str(record_id), action, account_id, user_id, organization_id,
                         display_name_snapshot, ts(recorded_at), basis))

    def attributions(self, account_id: str | None = None) -> list[dict]:
        sql = ("SELECT record_type, record_id, action, account_id, user_id, organization_id, display_name_snapshot, "
               "recorded_at, basis FROM attributions")
        args: tuple = ()
        if account_id is not None:
            sql, args = sql + " WHERE account_id = ?", (account_id,)
        return [{"record_type": r[0], "record_id": r[1], "action": r[2], "account_id": r[3], "user_id": r[4],
                 "organization_id": r[5], "display_name_snapshot": r[6], "recorded_at": dt(r[7]), "basis": r[8]}
                for r in self.db.execute(sql + " ORDER BY rowid", args).fetchall()]

    def attribution_backfill(self) -> str:
        return self.db.execute("SELECT value FROM console_meta WHERE key = ?", (ATTRIBUTION_BACKFILL,)).fetchone()[0]

    def complete_attribution_backfill(self, rows: list[dict], detail: dict, completed_at: datetime) -> int:
        """Write the backfilled attributions and the audit log entry, and mark the backfill done, once. Call inside
        a transaction. A record that already has an attribution keeps it."""
        if self.attribution_backfill() == BACKFILL_DONE:
            return 0
        added = 0
        for row in rows:
            added += self.db.execute(
                "INSERT OR IGNORE INTO attributions VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'BACKFILL')",
                (row["record_type"], str(row["record_id"]), row["action"], row["account_id"], row["user_id"],
                 row["organization_id"], row["display_name_snapshot"], ts(row["recorded_at"]))).rowcount
        self.db.execute("INSERT INTO migration_log (migration, completed_at, detail_json) VALUES (?, ?, ?)",
                        (ATTRIBUTION_BACKFILL, ts(completed_at), json.dumps({**detail, "attributed": added},
                                                                           sort_keys=True)))
        self.db.execute("UPDATE console_meta SET value = ? WHERE key = ?", (BACKFILL_DONE, ATTRIBUTION_BACKFILL))
        return added

    def migration_log(self) -> list[dict]:
        return [{"seq": r[0], "migration": r[1], "completed_at": dt(r[2]), "detail": json.loads(r[3])}
                for r in self.db.execute("SELECT seq, migration, completed_at, detail_json FROM migration_log "
                                         "ORDER BY seq").fetchall()]

    # Every row a V0.3 / Phase 1 store recorded with an actor label, for the attribution backfill.

    def labelled_records(self) -> list[tuple]:
        """(record_type, record_id, action, account_id, actor label, recorded_at) for every console row with a human
        actor label. Engine 3 validator and deterministic-preparation rows carry no human and are not listed."""
        q = self.db.execute
        out = [(ATTR_ACCOUNT, r[0], "CREATED", r[0], r[1], dt(r[2]))
               for r in q("SELECT account_id, created_by, created_at FROM account_meta ORDER BY rowid")]
        out += [(ATTR_INTERACTION, r[0], "RECORDED", r[1], r[2], dt(r[3]))
                for r in q("SELECT interaction_id, account_id, recorded_by, recorded_at FROM interaction_meta "
                           "ORDER BY rowid")]
        out += [(ATTR_DRAFT, r[0], "CAPTURED", r[1], r[2], dt(r[3]))
                for r in q("SELECT draft_id, account_id, captured_by, captured_at FROM condition_drafts ORDER BY rowid")]
        out += [(ATTR_DRAFT_RESOLUTION, r[0], r[1], r[2], r[3], dt(r[4]))
                for r in q("SELECT r.draft_id, r.resolution, d.account_id, r.resolved_by, r.resolved_at "
                           "FROM draft_resolutions r JOIN condition_drafts d ON d.draft_id = r.draft_id "
                           "ORDER BY r.rowid")]
        out += [(ATTR_PREPARATION_REQUEST, r[0], "REQUESTED", r[1], r[2], dt(r[3]))
                for r in q("SELECT request_id, account_id, requested_by, requested_at FROM preparation_requests "
                           "ORDER BY rowid")]
        out += [(ATTR_PREPARATION_EDIT, r[0], "EDITED", r[1], r[2], dt(r[3]))
                for r in q("SELECT e.edit_id, r.account_id, e.edited_by, e.edited_at FROM preparation_edits e "
                           "JOIN preparations p ON p.preparation_id = e.preparation_id "
                           "JOIN preparation_requests r ON r.request_id = p.request_id ORDER BY e.rowid")]
        out += [(ATTR_PREPARATION_REVIEW, f"{r[0]}/{r[1]}", r[2], r[3], r[4], dt(r[5]))
                for r in q("SELECT v.preparation_id, v.artifact_id, v.decision, r.account_id, v.reviewed_by, "
                           "v.reviewed_at FROM preparation_reviews v "
                           "JOIN preparations p ON p.preparation_id = v.preparation_id "
                           "JOIN preparation_requests r ON r.request_id = p.request_id ORDER BY v.rowid")]
        out += [(ATTR_PREPARATION_BLOCK, r[0], r[1], r[2], r[3], dt(r[4]))
                for r in q("SELECT decision_id, action, account_id, decided_by, decided_at FROM preparation_blocks "
                           "ORDER BY decision_id")]
        out += [(ATTR_EVENT, r[0], "RECORDED", r[1], r[2], dt(r[3]))
                for r in q("SELECT event_id, account_id, actor, occurred_at FROM events "
                           "WHERE actor_kind IN ('Human', 'System') ORDER BY event_id")]
        return out
