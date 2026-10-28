"""V0.4 Phase 3C: Engine 1's schema invariants behave identically on SQLite and PostgreSQL.

One set of direct-SQL attacks runs against two databases holding the same synthetic seed:

    SQLite      baec_app.data.database.open_database(":memory:"): the released Engine 1 schema, version 7
    PostgreSQL  schema baec_engine1 (migration 0004), in one tenant transaction for a fresh synthetic organization,
                as the application role (baec_app) or, where a statement needs UPDATE or DELETE, as the probe role
                (every DML privilege, still under row-level security), so the triggers themselves are what refuses

The statements bypass ConsoleService and the repository. Each case states which SQLite trigger(s) it exercises and
the outcome expected on both. Outcomes are compared exactly:

    ("ok", rows)                 the statement was accepted (rows: what a follow-up query returned, if any)
    ("trigger", message)         a trigger refused it, with exactly this message (SQLite RAISE(ABORT, ...) text)
    ("constraint", kind)         a CHECK, FOREIGN KEY, UNIQUE, or NOT NULL constraint refused it. Constraint messages
                                 are each engine's own wording, so only the kind is compared.

Every one of the 92 SQLite triggers has at least one case (test_every_sqlite_trigger_has_an_attack_case), and every
trigger is shown to be load-bearing on both backends: dropping it changes the outcome of at least one of its cases
(test_dropping_any_trigger_changes_an_outcome_on_both_backends). Clause-level mutants are in the same style.

Corruption-only states (rows no guarded write can produce) are written with every trigger and foreign key off: on
SQLite by dropping and recreating the triggers with foreign keys off, on PostgreSQL with session_replication_role =
replica. CHECK constraints stay on in both (PostgreSQL cannot switch them off), so no case relies on breaking one.

All data is synthetic. These are software tests of the storage layer, not evidence for the BAEC theory.
"""

from __future__ import annotations

import re
import sqlite3
from contextlib import closing, contextmanager
from dataclasses import dataclass, field

import pytest

from baec_app.data import database as engine1
from baec_console.postgres import gate
from baec_console.postgres.database import act_for
from baec_console.postgres.engine1 import _translate

from .conftest import fresh, psycopg

# --- the two sides ---------------------------------------------------------------------------------------------------

_SQLITE_KINDS = {"SQLITE_CONSTRAINT_CHECK": "check", "SQLITE_CONSTRAINT_FOREIGNKEY": "foreign key",
                 "SQLITE_CONSTRAINT_NOTNULL": "not null", "SQLITE_CONSTRAINT_UNIQUE": "unique",
                 "SQLITE_CONSTRAINT_PRIMARYKEY": "unique"}
_PG_KINDS = {"23514": "check", "23503": "foreign key", "23502": "not null", "23505": "unique"}
PG_NAMES = gate.engine1_trigger_names()
TABLE_OF = {name: table for name, _, _, _, table in gate.engine1_sqlite_triggers()}


def _sqlite_outcome(error: sqlite3.IntegrityError) -> tuple:
    if error.sqlite_errorname == "SQLITE_CONSTRAINT_TRIGGER":
        return ("trigger", str(error))
    return ("constraint", _SQLITE_KINDS.get(error.sqlite_errorname, error.sqlite_errorname))


def _pg_outcome(error) -> tuple:
    state = error.diag.sqlstate or ""
    if state == "23502" or (state in _PG_KINDS and error.diag.constraint_name):
        return ("constraint", _PG_KINDS[state])
    if state == "23503":
        return ("constraint", "foreign key")
    if state.startswith("23"):
        return ("trigger", error.diag.message_primary)
    return ("error", state, error.diag.message_primary)


class SQLiteSide:
    name = "sqlite"

    def __init__(self) -> None:
        self.db = engine1.open_database(":memory:")

    def run(self, sql: str, params: tuple = (), *, writer: bool = False, pg_sql: str | None = None,
            check: str | None = None) -> tuple:
        try:
            self.db.execute(sql, params).fetchall()
        except sqlite3.IntegrityError as error:
            return _sqlite_outcome(error)
        return ("ok", [tuple(r) for r in self.db.execute(check).fetchall()] if check else [])

    def query(self, sql: str, params: tuple = ()) -> list[tuple]:
        return [tuple(r) for r in self.db.execute(sql, params).fetchall()]

    def bypass(self, statements) -> None:
        triggers = self.db.execute("SELECT sql FROM sqlite_master WHERE type = 'trigger'").fetchall()
        for (name,) in self.db.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'").fetchall():
            self.db.execute(f"DROP TRIGGER {name}")
        self.db.execute("PRAGMA foreign_keys = OFF")
        try:
            for sql, params in statements:
                self.db.execute(sql, params)
        finally:
            self.db.execute("PRAGMA foreign_keys = ON")
            for (sql,) in triggers:
                self.db.execute(sql)

    def drop_trigger(self, trigger: str) -> None:
        self.db.execute(f"DROP TRIGGER {trigger}")

    def replace_clause(self, trigger: str, old: str, new: str) -> None:
        (sql,) = self.db.execute("SELECT sql FROM sqlite_master WHERE type = 'trigger' AND name = ?",
                                 (trigger,)).fetchone()
        assert sql.count(old) == 1, (trigger, old)
        self.db.execute(f"DROP TRIGGER {trigger}")
        self.db.execute(sql.replace(old, new))

    def close(self) -> None:
        self.db.close()


class PostgresSide:
    name = "postgres"

    def __init__(self, admin_url: str, probe: str, organization_id: str | None = None) -> None:
        self.raw = psycopg.connect(admin_url, autocommit=True)
        self.probe = probe
        self.organization_id = organization_id or fresh("org-e1")
        self.raw.execute("BEGIN")
        self.raw.execute("SET LOCAL ROLE baec_app")
        self.raw.execute("SET LOCAL lock_timeout = '20s'")  # a lock wait is a test bug: fail, never hang
        act_for(self.raw, self.organization_id, "local")
        self.raw.execute("INSERT INTO baec.organizations (organization_id, name, created_at) VALUES (%s, %s, now())",
                         (self.organization_id, f"Organization {self.organization_id} (synthetic)"))
        self.raw.execute("SELECT set_config('search_path', 'pg_catalog, baec_engine1, pg_temp', true)")
        self._count = 0

    def run(self, sql: str, params: tuple = (), *, writer: bool = False, pg_sql: str | None = None,
            check: str | None = None) -> tuple:
        self._count += 1
        name = f"case_{self._count}"
        self.raw.execute(f"SAVEPOINT {name}")
        try:
            if writer:
                self.raw.execute(f"SET LOCAL ROLE {self.probe}")
            self.raw.execute(_translate(pg_sql or sql), params)
            if writer:
                self.raw.execute("SET LOCAL ROLE baec_app")
        except psycopg.Error as error:
            self.raw.execute(f"ROLLBACK TO SAVEPOINT {name}")
            self.raw.execute(f"RELEASE SAVEPOINT {name}")
            self.raw.execute("SET LOCAL ROLE baec_app")
            return _pg_outcome(error)
        self.raw.execute(f"RELEASE SAVEPOINT {name}")
        return ("ok", self.query(check) if check else [])

    def query(self, sql: str, params: tuple = ()) -> list[tuple]:
        return [tuple(r) for r in self.raw.execute(_translate(sql), params).fetchall()]

    def bypass(self, statements) -> None:
        self.raw.execute("RESET ROLE")
        self.raw.execute("SET LOCAL session_replication_role = replica")
        try:
            for sql, params in statements:
                self.raw.execute(_translate(sql), params)
        finally:
            self.raw.execute("SET LOCAL session_replication_role = origin")
            self.raw.execute("SET LOCAL ROLE baec_app")

    def drop_trigger(self, trigger: str) -> None:
        self.raw.execute("RESET ROLE")
        self.raw.execute(f"DROP TRIGGER {PG_NAMES[trigger]} ON baec_engine1.{TABLE_OF[trigger]}")
        self.raw.execute("SET LOCAL ROLE baec_app")

    def replace_clause(self, function: str, old: str, new: str) -> None:
        self.raw.execute("RESET ROLE")
        (definition,) = self.raw.execute("SELECT pg_get_functiondef(%s::regprocedure)",
                                         (f"baec_engine1.{function}()",)).fetchone()
        assert definition.count(old) == 1, (function, old)
        self.raw.execute(definition.replace(old, new))
        self.raw.execute("SET LOCAL ROLE baec_app")

    def close(self) -> None:
        self.raw.execute("ROLLBACK")
        self.raw.close()


@pytest.fixture
def sides(migrated, engine1_roles):
    """A fresh seeded SQLite database and a fresh seeded PostgreSQL tenant transaction."""
    opened = [SQLiteSide(), PostgresSide(migrated["admin"], engine1_roles["probe"])]
    try:
        for side in opened:
            seed(side)
        yield opened
    finally:
        for side in opened:
            side.close()


# --- the seed: every Engine 1 table, three AI lineages ---------------------------------------------------------------

T0 = "2026-09-01T09:00:00.000000+00:00"
ISSUED = "2026-09-01T10:00:00.000000+00:00"
EXPIRES = "2026-09-01T10:15:00.000000+00:00"
CONSUMED = "2026-09-01T10:05:00.000000+00:00"
ACTOR = "Avery Synthetic"
MODEL = "FIXTURE-synthetic-model-not-a-real-invocation"
QUOTE = "If the renewal price rises more than 8% we would reopen the evaluation."
NOTE = "the team is not evaluating alternatives now"
REVIEW = "We have opened a formal supplier review."
TEXT1 = f"Buyer: {QUOTE}\nRep note: {NOTE}."
TEXT2 = f"Buyer: {REVIEW}"
V2 = "baec-extraction-validation/v2"
CRITERIA = ("PRESENT_NON_EVALUATION", "PROSPECTIVE_CONDITION", "BUYER_ARTICULATION", "EVALUATION_LINKAGE")


def h(n: int) -> str:
    """A synthetic lowercase-hex digest (64 characters)."""
    return format(n, "x").rjust(64, "0")


def grant_id(letter: str) -> str:
    return "grant_" + letter * 64


def insert(table: str, **values) -> tuple[str, tuple]:
    return (f"INSERT INTO {table} ({', '.join(values)}) VALUES ({', '.join('?' for _ in values)})",
            tuple(values.values()))


def run_row(run: str, **overrides) -> dict:
    return {**dict(ai_run_id=run, provider="anthropic", task_type="baec-extraction", task_version="v1",
                   account_id="ACC-1", interaction_id="INT-1", requested_model=MODEL, sdk_name="anthropic-sdk",
                   sdk_version="1.0", prompt_version="p1", prompt_digest=h(1), input_version="i1",
                   input_digest=h(2), output_schema_version="o1", output_schema_digest=h(3),
                   canonicalization_version="c1", request_spec_version="r1", request_digest=h(4),
                   validation_version=V2, requested_at=T0), **overrides}


def result_row(run: str, digest: str, **overrides) -> dict:
    return {**dict(ai_run_id=run, status="success", remote_outcome="response_received",
                   provider_message_id=f"msg-{run}", response_model=MODEL, stop_reason="end_turn",
                   input_tokens=10, output_tokens=20, output_digest=digest, completed_at=T0), **overrides}


def grant_row(letter: str, number: int, **overrides) -> dict:
    return {**dict(grant_id=grant_id(letter), grant_format="baec-human-grant/v1", action="CONFIRM_BAEC",
                   issuing_surface="streamlit-review/v1", account_id="ACC-1", interaction_id="INT-1",
                   artifact_id=f"ART-{letter}", proposal_id=f"aiprop_{letter}", proposal_digest=h(500 + number),
                   review_revision_id=f"aireview_{letter}1", review_content_digest=h(600 + number),
                   decision_id=f"aidecision_{letter}1", issue_sequence=1, supersedes_grant_id=None,
                   baec_id=f"BAEC-{letter}", actor_label=ACTOR, issued_at=ISSUED, ttl_seconds=900,
                   expires_at=EXPIRES, grant_digest=h(700 + number)), **overrides}


def revision_row(letter: str, number: int, revision: int, **overrides) -> dict:
    return {**dict(review_revision_id=f"aireview_{letter}{revision}", proposal_id=f"aiprop_{letter}",
                   proposal_digest=h(500 + number), account_id="ACC-1", interaction_id="INT-1",
                   artifact_id=f"ART-{letter}", revision_number=revision,
                   previous_revision_id=None if revision == 1 else f"aireview_{letter}{revision - 1}",
                   review_content_version="baec-ai-review-content/v1",
                   normalization_validation_version="baec-human-normalization-validation/v1", content='{"r": 1}',
                   review_content_digest=h(600 + number) if revision == 1 else h(650 + revision),
                   actor_label=ACTOR, created_at=T0), **overrides}


def decision_row(letter: str, decision_id: str, revision: str | None, decision: str, **overrides) -> dict:
    return {**dict(decision_id=decision_id, proposal_id=f"aiprop_{letter}", review_revision_id=revision,
                   account_id="ACC-1", decision=decision, actor_label=ACTOR, decided_at=T0), **overrides}


def authorization_row(action: str, subject: str, at: str = T0, target: str | None = None, by: str = ACTOR) -> dict:
    return dict(authorized_by=by, authorized_at=at, action=action, subject_id=subject, target_state=target)


def record_row(baec_id: str, authorization: int | None, **overrides) -> dict:
    return {**dict(baec_id=baec_id, account_id="ACC-1", source_interaction_id="INT-1", source_excerpt_id=1,
                   captured_at=T0, buyer_role="Materials Manager", buyer_exact_statement=QUOTE,
                   articulation_origin="BUYER_GENERATED", elicitation_mode="CEE_ELICITED",
                   classification="CONFIRMED_BAEC", classification_reason=None, staleness_status="CURRENT",
                   confirmation_authorization_id=authorization), **overrides}


def link_row(letter: str, authorization: int, **overrides) -> dict:
    return {**dict(baec_id=f"BAEC-{letter}", artifact_id=f"ART-{letter}", proposal_id=f"aiprop_{letter}",
                   review_revision_id=f"aireview_{letter}1", grant_id=grant_id(letter),
                   authorization_id=authorization, account_id="ACC-1", interaction_id="INT-1"), **overrides}


# a: confirmed and consumed; b: open, one unretired grant; c: grant superseded by revision 2; d: grant re-issued (e)
LINEAGES = {"a": 1, "b": 2, "c": 3, "d": 4}


def seed_statements() -> list[tuple[str, tuple]]:
    s = [insert("schema_meta", key="engine", value="BAEC_ENGINE_1 (synthetic seed)"),
         insert("accounts", account_id="ACC-1", name="Harbor Surgical (synthetic)", state=None),
         insert("accounts", account_id="ACC-2", name="Other Synthetic Account", state=None),
         insert("interactions", interaction_id="INT-1", account_id="ACC-1", occurred_at=T0, text=TEXT1),
         insert("interactions", interaction_id="INT-2", account_id="ACC-1", occurred_at=T0, text=TEXT2),
         insert("interactions", interaction_id="INT-9", account_id="ACC-2", occurred_at=T0, text=TEXT1),
         insert("interaction_evidence", interaction_id="INT-1", provenance="BUYER_FACT", text=QUOTE),  # 1
         insert("interaction_evidence", interaction_id="INT-2", provenance="BUYER_FACT", text=REVIEW),  # 2
         insert("interaction_evidence", interaction_id="INT-1", provenance="SELLER_OBSERVATION", text=NOTE),  # 3
         insert("human_authorizations", **authorization_row("CONFIRM_BAEC", "B-1")),  # 1
         insert("human_authorizations", **authorization_row("RECORD_DORMANCY_JUDGMENT", "B-1")),  # 2
         insert("human_authorizations", **authorization_row("CHANGE_ACCOUNT_STATE", "ACC-1",
                                                             target="CONDITIONALLY_DORMANT")),  # 3
         insert("baec_records", **record_row("B-1", 1)),
         insert("baec_records", **record_row("B-2", None, articulation_origin="SELLER_SEEDED",
                                             elicitation_mode="SPONTANEOUS", classification="NOT_BAEC",
                                             classification_reason="synthetic reason", staleness_status=None))]
    for position, criterion in enumerate(CRITERIA):
        s.append(insert("criterion_assessments", baec_id="B-1", criterion=criterion, position=position,
                        finding="MET", rationale=None))
    s += [insert("criterion_evidence", baec_id="B-1", criterion="PRESENT_NON_EVALUATION", position=0, evidence_id=3,
                 source_interaction_id="INT-1"),
          insert("criterion_evidence", baec_id="B-1", criterion="PROSPECTIVE_CONDITION", position=0, evidence_id=1,
                 source_interaction_id="INT-1"),
          insert("stringency_expressions", baec_id="B-1", verbatim_text="more than 8%", comparator="GREATER_THAN",
                 numeric_value="8", unit="%", timing_text=None),
          insert("dormancy_judgments", baec_id="B-1", plausibility="YES", addressability="YES", notes=None,
                 authorization_id=2),  # 1
          insert("evaluation_evidence", account_id="ACC-1", interaction_id="INT-2", evidence_id=2, observed_at=T0),
          insert("non_evaluation_evidence", account_id="ACC-1", interaction_id="INT-1", evidence_id=3,
                 observed_at=T0),
          insert("account_state_transitions", account_id="ACC-1", from_state=None, to_state="CONDITIONALLY_DORMANT",
                 authorization_id=3, baec_id="B-1", judgment_id=1, unresolved="[]", recorded_at=T0)]
    for letter, number in LINEAGES.items():
        run = f"RUN-{letter}"
        s += [insert("ai_runs", **run_row(run)),
              insert("ai_run_results", **result_row(run, h(100 + number))),
              insert("ai_run_outputs", ai_run_id=run, raw_output_text=f"raw output {letter}",
                     output_digest=h(100 + number)),
              insert("ai_artifacts", artifact_id=f"ART-{letter}", ai_run_id=run, task_type="baec-extraction",
                     task_version="v1", output_schema_version="o1", account_id="ACC-1", interaction_id="INT-1",
                     canonical_result='{"x": 1}', artifact_digest=h(400 + number), created_at=T0),
              insert("ai_artifact_excerpts", artifact_id=f"ART-{letter}", excerpt_id="E1", interaction_id="INT-1",
                     text=QUOTE, attributed_speaker="buyer"),
              insert("ai_proposals", proposal_id=f"aiprop_{letter}", origin="AI_DRAFT",
                     mapping_version="baec-ai-proposal-mapping/v1", content_version="baec-ai-proposal-content/v1",
                     artifact_id=f"ART-{letter}", artifact_digest=h(400 + number), account_id="ACC-1",
                     interaction_id="INT-1", content='{"p": 1}', proposal_digest=h(500 + number),
                     created_by=ACTOR, created_at=T0),
              insert("ai_proposal_review_revisions", **revision_row(letter, number, 1)),
              insert("ai_proposal_review_decisions", **decision_row(letter, f"aidecision_{letter}1",
                                                                     f"aireview_{letter}1", "ACCEPTED")),
              insert("human_authorization_grants", **grant_row(letter, number))]
    s += [insert("human_authorizations", **authorization_row("CONFIRM_BAEC", "BAEC-a", ISSUED)),  # 4
          insert("baec_records", **record_row("BAEC-a", 4, captured_at=ISSUED)),
          insert("ai_proposal_confirmations", **link_row("a", 4)),
          insert("human_authorization_grant_consumptions", grant_id=grant_id("a"), baec_id="BAEC-a",
                 executor_surface="mcp-write/confirm_baec/v1", consumed_at=CONSUMED),
          insert("human_authorizations", **authorization_row("CONFIRM_BAEC", "BAEC-b", ISSUED)),  # 5
          insert("baec_records", **record_row("BAEC-b", 5, captured_at=ISSUED)),
          insert("ai_proposal_review_revisions", **revision_row("c", 3, 2)),  # supersedes grant c (AFTER trigger)
          insert("human_authorization_grants", **grant_row("d", 4, grant_id=grant_id("e"), issue_sequence=2,
                                                            supersedes_grant_id=grant_id("d"), baec_id="BAEC-e",
                                                            grant_digest=h(799)))]
    return s


def seed(side) -> None:
    for sql, params in seed_statements():
        outcome = side.run(sql, params)
        assert outcome == ("ok", []), (side.name, sql, outcome)


def test_the_seed_is_identical_on_both_backends_including_database_assigned_ids(sides):
    """Every table holds the same rows on both, compared column by column (PostgreSQL's tenancy columns and seq aside),
    so the six rowid ids (1, 2, 3, ... per workspace) and the AFTER-insert supersession are the same."""
    sqlite, postgres = sides
    for table in engine1.ALL_TABLES:
        assert rows(sqlite, table) == rows(postgres, table), table
    assert sqlite.query("SELECT count(*) FROM human_authorization_grant_supersessions") == [(1,)]
    assert sqlite.query("SELECT max(evidence_id), max(authorization_id) FROM interaction_evidence, "
                        "human_authorizations") == [(3, 5)]


def columns(table: str) -> list[str]:
    """The SQLite table's columns, in order (the PostgreSQL table has the same, plus organization_id, workspace, seq)."""
    with closing(engine1.open_database(":memory:")) as db:
        return [r[1] for r in db.execute(f"PRAGMA table_info({table})").fetchall()]


def rows(side, table: str) -> list[tuple]:
    """Every row of `table` in this side's workspace, sorted in Python (no collation is involved)."""
    return sorted(side.query(f"SELECT {', '.join(columns(table))} FROM {table}"), key=repr)


# --- attack cases ----------------------------------------------------------------------------------------------------

_RAISE = re.compile(r"CREATE TRIGGER (\w+).*?RAISE\(ABORT, '([^']*)'\)", re.S)
MESSAGE = {m.group(1): m.group(2) for m in (_RAISE.search(s) for s in engine1._schema_statements()) if m}
SQLITE_TRIGGERS = tuple(name for name, *_ in gate.engine1_sqlite_triggers())


@dataclass(frozen=True)
class Case:
    id: str
    triggers: tuple[str, ...]  # the SQLite triggers whose protection this case exercises
    sql: str
    params: tuple = ()
    expect: tuple = ("ok", [])
    setup: tuple = ()  # guarded writes made first (all must be accepted)
    bypass: tuple = ()  # corruption-only rows, written with every trigger and foreign key off
    without: tuple = ()  # SQLite triggers dropped before the attack (to isolate a guard behind another)
    writer: bool = False  # needs UPDATE or DELETE: PostgreSQL runs it as the probe role
    pg_sql: str | None = None  # PostgreSQL spelling, only where SQLite syntax has none (OR REPLACE / OR IGNORE)
    check: str | None = None  # a query whose rows are part of the outcome


def refused_by(trigger: str) -> tuple:
    return ("trigger", MESSAGE[trigger])


def _seed_rows() -> dict[str, list[dict]]:
    """Every seeded row of every table, as column -> value, in insertion order (rowid ids are 1, 2, ...)."""
    found: dict[str, list[dict]] = {}
    for sql, params in seed_statements():
        table, names = re.match(r"INSERT INTO (\w+) \(([^)]*)\)", sql).groups()
        found.setdefault(table, []).append(dict(zip(names.split(", "), params)))
    for table, column in gate.ENGINE1_ROWID_KEYS.items():
        found[table] = [{column: i + 1, **row} for i, row in enumerate(found[table])]
    found["human_authorization_grant_supersessions"] = [dict(
        grant_id=grant_id("c"), reason="REVISION_SUPERSEDED", superseding_revision_id="aireview_c2",
        rejection_decision_id=None, actor_label=ACTOR, superseded_at=T0)]
    return found


SEED_ROWS = _seed_rows()
REPLACE_KEYS = engine1.REPLACE_GUARDED_KEYS | engine1.AI_REPLACE_GUARDED_KEYS | engine1.BRIDGE_REPLACE_GUARDED_KEYS


def _fresh_value(value):
    return value + 99999 if isinstance(value, int) else f"{value}-other"


def _colliding_row(table: str, key: tuple[str, ...]) -> dict:
    """A seeded row of the table with `key` set, changed so that it collides with it on `key` and on no other UNIQUE
    key."""
    row = dict(next(r for r in SEED_ROWS[table] if all(r.get(c) is not None for c in key)))
    rowid = gate.ENGINE1_ROWID_KEYS.get(table)
    for other in REPLACE_KEYS[table]:
        for column in other:
            if column not in key and row.get(column) is not None:
                row[column] = _fresh_value(row[column])
    if rowid and rowid not in key:
        row.pop(rowid)  # assigned by the database: no collision on it
    return row


def _append_only_cases() -> list[Case]:
    cases = []
    tables = engine1.APPEND_ONLY_TABLES + engine1.AI_APPEND_ONLY_TABLES + engine1.BRIDGE_APPEND_ONLY_TABLES
    for table in tables:
        column = columns(table)[0]
        cases.append(Case(f"G1 update {table}", (f"{table}_no_update",), f"UPDATE {table} SET {column} = {column}",
                          expect=refused_by(f"{table}_no_update"), writer=True))
        cases.append(Case(f"G1 delete {table}", (f"{table}_no_delete",), f"DELETE FROM {table}",
                          expect=refused_by(f"{table}_no_delete"), writer=True))
    cases.append(Case("G1 delete baec_records", ("baec_records_no_delete",),
                      "DELETE FROM baec_records WHERE baec_id = 'B-2'", expect=refused_by("baec_records_no_delete"),
                      writer=True))
    return cases


def guards_before(trigger: str) -> tuple[str, ...]:
    """The BEFORE INSERT triggers SQLite fires before `trigger` on its table (newest first; derived from Engine 1's
    schema statements). A no_replace case switches these off, so that it tests the replace guard alone."""
    table = TABLE_OF[trigger]
    same = [n for n, timing, event, _, t in gate.engine1_sqlite_triggers() if (timing, event, t) == ("BEFORE", "INSERT",
                                                                                                    table)]
    order = list(reversed(same))
    return tuple(order[:order.index(trigger)])


def _replace_cases() -> list[Case]:
    cases = []
    for table, keys in REPLACE_KEYS.items():
        trigger = f"{table}_no_replace"
        alone = guards_before(trigger)
        for key in keys:
            sql, params = insert(table, **_colliding_row(table, key))
            cases.append(Case(f"G2 {table} collides on ({', '.join(key)})", (trigger,), sql, params,
                              expect=refused_by(trigger), without=alone))
        sql, params = insert(table, **SEED_ROWS[table][0])
        cases.append(Case(f"G2 {table} INSERT OR REPLACE", (trigger,), sql.replace("INSERT", "INSERT OR REPLACE", 1),
                          params, expect=refused_by(trigger), pg_sql=sql + " ON CONFLICT DO NOTHING", without=alone))
        cases.append(Case(f"G2 {table} INSERT OR IGNORE", (trigger,), sql.replace("INSERT", "INSERT OR IGNORE", 1),
                          params, expect=refused_by(trigger), pg_sql=sql + " ON CONFLICT DO NOTHING", without=alone))
    rejection = decision_row("b", "aidecision_b8", None, "REJECTED")
    second = decision_row("b", "aidecision_b9", None, "REJECTED")
    cases.append(Case("G2 a second rejection cannot replace the first", ("ai_proposal_review_decisions_no_replace",),
                      *insert("ai_proposal_review_decisions", **second),
                      expect=refused_by("ai_proposal_review_decisions_no_replace"),
                      setup=(insert("ai_proposal_review_decisions", **rejection),),
                      without=("ai_proposal_review_decisions_terminal",)))
    return cases


def _protected_column_cases() -> list[Case]:
    cases = [Case(f"G3 update {column}", ("baec_records_protected_no_update",),
                  f"UPDATE baec_records SET {column} = {column} WHERE baec_id = 'B-1'",
                  expect=refused_by("baec_records_protected_no_update"), writer=True)
             for column in engine1.BAEC_RECORD_PROTECTED_COLUMNS]
    cases.append(Case("G3 staleness_status stays writable", ("baec_records_protected_no_update",),
                      "UPDATE baec_records SET staleness_status = 'STALE' WHERE baec_id = 'B-1'", writer=True,
                      check="SELECT staleness_status FROM baec_records WHERE baec_id = 'B-1'",
                      expect=("ok", [("STALE",)])))
    return cases


EVIDENCE = "interaction_evidence_verbatim"
EXCERPT = "ai_artifact_excerpts_verbatim"


def _evidence(text: str, interaction: str = "INT-2", provenance: str = "SELLER_OBSERVATION", **extra):
    return insert("interaction_evidence", **extra, interaction_id=interaction, provenance=provenance, text=text)


def _excerpt(text: str, interaction: str = "INT-1", excerpt_id: str = "E2", artifact: str = "ART-a"):
    return insert("ai_artifact_excerpts", artifact_id=artifact, excerpt_id=excerpt_id, interaction_id=interaction,
                  text=text, attributed_speaker="seller")


def _verbatim_cases() -> list[Case]:
    cases = [
        Case("G4 evidence: blank", (EVIDENCE,), *_evidence(""), expect=refused_by(EVIDENCE)),
        Case("G4 evidence: not in the interaction", (EVIDENCE,), *_evidence("Something the buyer never said."),
             expect=refused_by(EVIDENCE)),
        Case("G4 evidence: case changed", (EVIDENCE,), *_evidence(REVIEW.upper()), expect=refused_by(EVIDENCE)),
        Case("G4 evidence: missing interaction", (EVIDENCE,), *_evidence("Buyer", interaction="INT-404"),
             expect=refused_by(EVIDENCE)),
        Case("G4 evidence: exact substring is accepted", (EVIDENCE,), *_evidence("formal supplier review"),
             check="SELECT max(evidence_id) FROM interaction_evidence", expect=("ok", [(4,)])),
        Case("G4 evidence: verbatim fires before no_replace", (EVIDENCE, "interaction_evidence_no_replace"),
             *_evidence("not said", evidence_id=1), expect=refused_by(EVIDENCE)),
        Case("G4 excerpt: blank", (EXCERPT,), *_excerpt(""), expect=refused_by(EXCERPT)),
        Case("G4 excerpt: not in the interaction", (EXCERPT,), *_excerpt("never said"), expect=refused_by(EXCERPT)),
        Case("G4 excerpt: case changed", (EXCERPT,), *_excerpt(QUOTE.lower()), expect=refused_by(EXCERPT)),
        Case("G4 excerpt: missing interaction", (EXCERPT,), *_excerpt("Buyer", interaction="INT-404"),
             expect=refused_by(EXCERPT)),
        Case("G4 excerpt: no-break space is not trimmed and not in the text", (EXCERPT,), *_excerpt(" "),
             expect=refused_by(EXCERPT)),
        Case("G4 excerpt: exact substring is accepted", (EXCERPT,), *_excerpt("renewal price")),
        Case("G4 excerpt: verbatim fires before no_replace", (EXCERPT, "ai_artifact_excerpts_no_replace"),
             *_excerpt("never said", excerpt_id="E1"), expect=refused_by(EXCERPT)),
    ]
    for label, space in (("space", " "), ("tab", "\t"), ("newline", "\n"), ("vertical tab", "\x0b"),
                         ("form feed", "\x0c"), ("carriage return", "\r"), ("mixed", " \t\r\n")):
        cases.append(Case(f"G4 excerpt: whitespace only ({label})", (EXCERPT,), *_excerpt(space),
                          expect=refused_by(EXCERPT)))
    return cases


def lineage(letter: str, number: int, *, validation: str = V2, accepted: bool = True, granted: bool = False
            ) -> tuple:
    """Guarded writes creating one more AI lineage: run, result, output, artifact, proposal, revision 1, and
    optionally its acceptance and a grant."""
    run = f"RUN-{letter}"
    rows = [insert("ai_runs", **run_row(run, validation_version=validation)),
            insert("ai_run_results", **result_row(run, h(100 + number))),
            insert("ai_run_outputs", ai_run_id=run, raw_output_text="raw", output_digest=h(100 + number)),
            insert("ai_artifacts", artifact_id=f"ART-{letter}", ai_run_id=run, task_type="baec-extraction",
                   task_version="v1", output_schema_version="o1", account_id="ACC-1", interaction_id="INT-1",
                   canonical_result='{"x": 1}', artifact_digest=h(400 + number), created_at=T0),
            insert("ai_proposals", proposal_id=f"aiprop_{letter}", origin="AI_DRAFT",
                   mapping_version="baec-ai-proposal-mapping/v1", content_version="baec-ai-proposal-content/v1",
                   artifact_id=f"ART-{letter}", artifact_digest=h(400 + number), account_id="ACC-1",
                   interaction_id="INT-1", content='{"p": 1}', proposal_digest=h(500 + number), created_by=ACTOR,
                   created_at=T0),
            insert("ai_proposal_review_revisions", **revision_row(letter, number, 1))]
    if accepted:
        rows.append(insert("ai_proposal_review_decisions",
                           **decision_row(letter, f"aidecision_{letter}1", f"aireview_{letter}1", "ACCEPTED")))
    if granted:
        rows.append(insert("human_authorization_grants", **grant_row(letter, number)))
    return tuple(rows)


def run_only(run: str, validation: str = V2) -> tuple:
    return (insert("ai_runs", **run_row(run, validation_version=validation)),)


MODEL_BINDING = "ai_run_results_model_binding"
CODES = "ai_run_results_failure_codes_closed"
REQUIRE_RESULT = "ai_run_outputs_require_result"
REQUIRE_SUCCESS = "ai_artifacts_require_success"


def _parse_failure(codes: str, run: str = "RUN-x") -> tuple:
    return insert("ai_run_results", **result_row(run, None, status="parse_failure", failure_codes=codes))


def _semantic_failure(codes: str, run: str = "RUN-x") -> tuple:
    return insert("ai_run_results", **result_row(run, h(900), status="semantic_validation_failure",
                                                  failure_codes=codes))


def _artifact(run: str = "RUN-x", artifact: str = "ART-x") -> tuple:
    return insert("ai_artifacts", artifact_id=artifact, ai_run_id=run, task_type="baec-extraction", task_version="v1",
                  output_schema_version="o1", account_id="ACC-1", interaction_id="INT-1", canonical_result='{"x": 1}',
                  artifact_digest=h(410), created_at=T0)


def _provenance_cases() -> list[Case]:
    run = run_only("RUN-x")
    v1 = run_only("RUN-x", "baec-extraction-validation/v1")
    mismatch = dict(status="model_mismatch", response_model="OTHER-MODEL")
    cases = [
        Case("G5 success must name the requested model", (MODEL_BINDING,),
             *insert("ai_run_results", **result_row("RUN-x", h(900), response_model="OTHER-MODEL")), setup=run,
             expect=refused_by(MODEL_BINDING)),
        Case("G5 model_mismatch must name another model", (MODEL_BINDING,),
             *insert("ai_run_results", **result_row("RUN-x", h(900), status="model_mismatch")), setup=run,
             expect=refused_by(MODEL_BINDING)),
        Case("G5 model_mismatch with another model is accepted", (MODEL_BINDING,),
             *insert("ai_run_results", **result_row("RUN-x", h(900), **mismatch)), setup=run),
        Case("G5 success for a missing run", (MODEL_BINDING,),
             *insert("ai_run_results", **result_row("RUN-404", h(900))), expect=refused_by(MODEL_BINDING)),
        Case("G5 codes on a status without codes", (CODES,),
             *insert("ai_run_results", **result_row("RUN-x", h(900), status="refusal", stop_reason="refusal",
                                                     failure_codes='["invalid_json"]')), setup=run,
             expect=refused_by(CODES)),
        Case("G5 semantic codes for a missing run", (CODES,), *_semantic_failure('["excerpt_blank"]', "RUN-404"),
             expect=refused_by(CODES)),
    ]
    for label, codes, ok in (("one parse code", '["invalid_json"]', True),
                             ("whitespace around the array", ' ["invalid_json"] ', True),
                             ("a unicode escape", '["\\u0069nvalid_json"]', True),
                             ("not JSON", "invalid_json", False), ("an object", '{"code": "invalid_json"}', False),
                             ("two codes", '["invalid_json", "missing_text_block"]', False),
                             ("a number", "[1]", False), ("an unknown code", '["made_up"]', False),
                             ("a semantic code", '["excerpt_blank"]', False), ("JSON5", '["invalid_json",]', False),
                             ("empty", "[]", False), ("a NUL escape", '["invalid_json\\u0000"]', False)):
        cases.append(Case(f"G5 parse_failure: {label}", (CODES,), *_parse_failure(codes), setup=run,
                          expect=("ok", []) if ok else refused_by(CODES)))
    for label, codes, setup, ok in (
            ("one v1 code under v1", '["excerpt_blank"]', v1, True),
            ("a v2-only code under v1", '["normalization_number_unsupported"]', v1, False),
            ("a v2-only code under v2", '["normalization_number_unsupported"]', run, True),
            ("sorted codes", '["duplicate_excerpt_id", "excerpt_blank"]', run, True),
            ("unsorted codes", '["excerpt_blank", "duplicate_excerpt_id"]', run, False),
            ("a repeated code", '["excerpt_blank", "excerpt_blank"]', run, False),
            ("binary order of an underscore", '["too_many_excerpt_references", "too_many_excerpts"]', run, True),
            ("binary order reversed", '["too_many_excerpts", "too_many_excerpt_references"]', run, False),
            ("empty", "[]", run, False), ("a number", '["excerpt_blank", 2]', run, False),
            ("not JSON", "excerpt_blank", run, False), ("a parse code", '["invalid_json"]', run, False),
            ("a NUL escape", '["\\u0000excerpt_blank"]', run, False)):
        cases.append(Case(f"G5 semantic_validation_failure: {label}", (CODES,), *_semantic_failure(codes),
                          setup=setup, expect=("ok", []) if ok else refused_by(CODES)))
    success = insert("ai_run_results", **result_row("RUN-x", h(900)))
    output = insert("ai_run_outputs", ai_run_id="RUN-x", raw_output_text="raw", output_digest=h(900))
    cases += [
        Case("G5 output without a result", (REQUIRE_RESULT,),
             *insert("ai_run_outputs", ai_run_id="RUN-x", raw_output_text="raw", output_digest=h(900)), setup=run,
             expect=refused_by(REQUIRE_RESULT)),
        Case("G5 output with another digest", (REQUIRE_RESULT,),
             *insert("ai_run_outputs", ai_run_id="RUN-x", raw_output_text="raw", output_digest=h(901)),
             setup=run + (success,), expect=refused_by(REQUIRE_RESULT)),
        Case("G5 output with the result's digest", (REQUIRE_RESULT,), *output, setup=run + (success,)),
        Case("G5 artifact without the raw output", (REQUIRE_SUCCESS,), *_artifact(), setup=run + (success,),
             expect=refused_by(REQUIRE_SUCCESS)),
        Case("G5 artifact for a semantic failure", (REQUIRE_SUCCESS,), *_artifact(),
             setup=run + (_semantic_failure('["excerpt_blank"]'), output), expect=refused_by(REQUIRE_SUCCESS)),
        Case("G5 artifact whose output digest differs from its result's", (REQUIRE_SUCCESS,), *_artifact(),
             setup=run + (success,),
             bypass=(insert("ai_run_outputs", ai_run_id="RUN-x", raw_output_text="raw", output_digest=h(901)),),
             expect=refused_by(REQUIRE_SUCCESS)),
        Case("G5 artifact for a successful run with its output", (REQUIRE_SUCCESS,), *_artifact(),
             setup=run + (success, output)),
    ]
    return cases


BINDING = "ai_proposals_artifact_binding"
SEQUENCE = "ai_proposal_review_revisions_sequence"
OPEN = "ai_proposal_review_revisions_open"
SUPERSEDE = "ai_proposal_review_revisions_supersede_grants"
DECISION_BINDING = "ai_proposal_review_decisions_binding"
LATEST = "ai_proposal_review_decisions_latest"
TERMINAL = "ai_proposal_review_decisions_terminal"
REJECT_SUPERSEDES = "ai_proposal_review_decisions_reject_supersedes"
SUPERSESSIONS = "SELECT grant_id, reason, superseding_revision_id, rejection_decision_id FROM " \
                "human_authorization_grant_supersessions WHERE grant_id = '" + grant_id("b") + "'"


def _proposal(letter: str = "x", number: int = 9, **overrides) -> tuple:
    return insert("ai_proposals", **{**dict(
        proposal_id=f"aiprop_{letter}", origin="AI_DRAFT", mapping_version="baec-ai-proposal-mapping/v1",
        content_version="baec-ai-proposal-content/v1", artifact_id=f"ART-{letter}", artifact_digest=h(400 + number),
        account_id="ACC-1", interaction_id="INT-1", content='{"p": 1}', proposal_digest=h(500 + number),
        created_by=ACTOR, created_at=T0), **overrides})


def _lineage_cases() -> list[Case]:
    artifact_x = lineage("x", 9)[:4]  # run, result, output, artifact of a lineage with no proposal yet
    v1 = lineage("x", 9, validation="baec-extraction-validation/v1")[:4]
    reject_b = insert("ai_proposal_review_decisions", **decision_row("b", "aidecision_b2", None, "REJECTED"))
    return [
        Case("G6 a proposal of an eligible artifact", (BINDING,), *_proposal(), setup=artifact_x),
        Case("G6 proposal: missing artifact", (BINDING,), *_proposal(artifact_id="ART-404"), setup=artifact_x,
             expect=refused_by(BINDING)),
        Case("G6 proposal: artifact digest differs", (BINDING,), *_proposal(artifact_digest=h(1)), setup=artifact_x,
             expect=refused_by(BINDING)),
        Case("G6 proposal: another account", (BINDING,), *_proposal(account_id="ACC-2"), setup=artifact_x,
             expect=refused_by(BINDING)),
        Case("G6 proposal: another interaction", (BINDING,), *_proposal(interaction_id="INT-2"), setup=artifact_x,
             expect=refused_by(BINDING)),
        Case("G6 proposal: validated under v1", (BINDING,), *_proposal(), setup=v1, expect=refused_by(BINDING)),
        Case("G6 proposal: the run's result is not success", (BINDING,), *_proposal(),
             setup=run_only("RUN-x"),
             bypass=(insert("ai_run_results", **result_row("RUN-x", h(109), status="refusal", stop_reason="refusal")),
                     insert("ai_run_outputs", ai_run_id="RUN-x", raw_output_text="raw", output_digest=h(109)),
                     lineage("x", 9)[3]),
             expect=refused_by(BINDING)),
        Case("G6 revision 2 extends revision 1 and supersedes its grant", (SEQUENCE, SUPERSEDE),
             *insert("ai_proposal_review_revisions", **revision_row("b", 2, 2)), check=SUPERSESSIONS,
             expect=("ok", [(grant_id("b"), "REVISION_SUPERSEDED", "aireview_b2", None)])),
        Case("G6 revision: a skipped number", (SEQUENCE,),
             *insert("ai_proposal_review_revisions", **revision_row("b", 2, 3, previous_revision_id="aireview_b1")),
             expect=refused_by(SEQUENCE)),
        Case("G6 revision: no previous revision", (SEQUENCE,),
             *insert("ai_proposal_review_revisions", **revision_row("b", 2, 2, previous_revision_id=None)),
             expect=refused_by(SEQUENCE)),
        Case("G6 revision: another proposal's revision as previous", (SEQUENCE,),
             *insert("ai_proposal_review_revisions", **revision_row("b", 2, 2, previous_revision_id="aireview_c2")),
             expect=refused_by(SEQUENCE)),
        Case("G6 revision: a first revision numbered 2", (SEQUENCE,),
             *insert("ai_proposal_review_revisions", **revision_row("x", 9, 2, previous_revision_id=None)),
             setup=lineage("x", 9)[:5], expect=refused_by(SEQUENCE)),
        Case("G6 revision: a confirmed proposal", (OPEN,),
             *insert("ai_proposal_review_revisions", **revision_row("a", 1, 2)), expect=refused_by(OPEN)),
        Case("G6 revision: a rejected proposal", (OPEN,),
             *insert("ai_proposal_review_revisions", **revision_row("b", 2, 2)), setup=(reject_b,),
             expect=refused_by(OPEN)),
        Case("G6 decision: another account", (DECISION_BINDING,),
             *insert("ai_proposal_review_decisions", **decision_row("b", "aidecision_b2", None, "REJECTED",
                                                                    account_id="ACC-2")),
             expect=refused_by(DECISION_BINDING)),
        Case("G6 decision: a missing proposal", (DECISION_BINDING,),
             *insert("ai_proposal_review_decisions", **decision_row("b", "aidecision_b2", None, "REJECTED",
                                                                    proposal_id="aiprop_404")),
             expect=refused_by(DECISION_BINDING)),
        Case("G6 decision: another proposal's revision", (DECISION_BINDING,),
             *insert("ai_proposal_review_decisions", **decision_row("b", "aidecision_b2", "aireview_c2", "REJECTED")),
             expect=refused_by(DECISION_BINDING)),
        Case("G6 decision: a revision recorded for another account", (DECISION_BINDING,),
             *insert("ai_proposal_review_decisions", **decision_row("b", "aidecision_b2", "aireview_b9", "REJECTED")),
             bypass=(insert("ai_proposal_review_revisions", **revision_row("b", 2, 9, account_id="ACC-2",
                                                                           previous_revision_id="aireview_b1")),),
             expect=refused_by(DECISION_BINDING)),
        Case("G6 decision: accept a revision that is not the latest", (LATEST,),
             *insert("ai_proposal_review_decisions", **decision_row("c", "aidecision_c9", "aireview_c1", "ACCEPTED")),
             expect=refused_by(LATEST)),
        Case("G6 decision: accept the latest revision", (LATEST,),
             *insert("ai_proposal_review_decisions", **decision_row("c", "aidecision_c2", "aireview_c2", "ACCEPTED"))),
        Case("G6 decision: after a confirmation", (TERMINAL,),
             *insert("ai_proposal_review_decisions", **decision_row("a", "aidecision_a2", None, "REJECTED")),
             expect=refused_by(TERMINAL)),
        Case("G6 decision: after a rejection", (TERMINAL,),
             *insert("ai_proposal_review_decisions", **decision_row("b", "aidecision_b3", None, "REJECTED")),
             setup=(reject_b,), expect=refused_by(TERMINAL)),
        Case("G7 a rejection supersedes the proposal's unretired grant", (REJECT_SUPERSEDES,), *reject_b,
             check=SUPERSESSIONS, expect=("ok", [(grant_id("b"), "PROPOSAL_REJECTED", None, "aidecision_b2")])),
    ]


ACCEPTED = "human_authorization_grants_accepted"
DIGEST = "human_authorization_grants_proposal_digest"
ONE_UNRETIRED = "human_authorization_grants_one_unretired"
SUPERSESSION_BINDING = "human_authorization_grant_supersessions_binding"


def _grant(letter: str, number: int, new: str, **overrides) -> tuple:
    return insert("human_authorization_grants", **grant_row(letter, number, grant_id=grant_id(new),
                                                            baec_id=f"BAEC-{new}", grant_digest=h(800), **overrides))


def _supersession(grant: str, reason: str = "REVISION_SUPERSEDED", revision: str | None = None,
                  decision: str | None = None) -> tuple:
    return insert("human_authorization_grant_supersessions", grant_id=grant_id(grant), reason=reason,
                  superseding_revision_id=revision, rejection_decision_id=decision, actor_label=ACTOR,
                  superseded_at=T0)


def _grant_cases() -> list[Case]:
    x = lineage("x", 9)
    reject_x = insert("ai_proposal_review_decisions", **decision_row("x", "aidecision_x2", None, "REJECTED"))
    return [
        Case("G7 a grant for an accepted latest revision", (ACCEPTED,), *_grant("x", 9, "f"), setup=x),
        Case("G7 grant: no acceptance", (ACCEPTED,), *_grant("x", 9, "f"), setup=lineage("x", 9, accepted=False),
             expect=refused_by(ACCEPTED)),
        Case("G7 grant: a revision that is no longer the latest", (ACCEPTED,), *_grant("c", 3, "f"),
             expect=refused_by(ACCEPTED)),
        Case("G7 grant: accepted, then the proposal was rejected", (ACCEPTED,), *_grant("x", 9, "f"),
             setup=x + (reject_x,), expect=refused_by(ACCEPTED)),
        Case("G7 grant: a confirmed proposal", (ACCEPTED,), *_grant("a", 1, "f"), expect=refused_by(ACCEPTED)),
        Case("G7 grant: proposal digest differs", (DIGEST,), *_grant("x", 9, "f", proposal_digest=h(1)), setup=x,
             expect=refused_by(DIGEST)),
        Case("G7 grant: a second unretired grant for the revision", (ONE_UNRETIRED,), *_grant("b", 2, "f"),
             expect=refused_by(ONE_UNRETIRED)),
        Case("G7 grant: a re-issue naming another revision's grant", (ONE_UNRETIRED,),
             *_grant("x", 9, "f", issue_sequence=2, supersedes_grant_id=grant_id("b")), setup=x,
             expect=refused_by(ONE_UNRETIRED)),
        Case("G7 grant: a re-issue skipping a sequence number", (ONE_UNRETIRED,),
             *_grant("b", 2, "f", issue_sequence=3, supersedes_grant_id=grant_id("b")),
             expect=refused_by(ONE_UNRETIRED)),
        Case("G7 grant: a re-issue of a consumed grant", (ONE_UNRETIRED,),
             *_grant("a", 1, "f", issue_sequence=2, supersedes_grant_id=grant_id("a")),
             expect=refused_by(ONE_UNRETIRED)),
        Case("G7 grant: a re-issue of a retired grant", (ONE_UNRETIRED,),
             *_grant("d", 4, "f", issue_sequence=2, supersedes_grant_id=grant_id("d")),
             expect=refused_by(ONE_UNRETIRED)),
        Case("G7 a re-issue retires its predecessor", (ONE_UNRETIRED,),
             *_grant("b", 2, "f", issue_sequence=2, supersedes_grant_id=grant_id("b")),
             check="SELECT grant_id FROM human_authorization_grants WHERE supersedes_grant_id = '" + grant_id("b") + "'",
             expect=("ok", [(grant_id("f"),)])),
        Case("G7 supersession: a grant already superseded", (SUPERSESSION_BINDING,),
             *_supersession("c", revision="aireview_c2"), expect=refused_by(SUPERSESSION_BINDING)),
        Case("G7 supersession: by another proposal's revision", (SUPERSESSION_BINDING,),
             *_supersession("b", revision="aireview_c2"), expect=refused_by(SUPERSESSION_BINDING)),
        Case("G7 supersession: by the grant's own revision", (SUPERSESSION_BINDING,),
             *_supersession("b", revision="aireview_b1"), expect=refused_by(SUPERSESSION_BINDING)),
        Case("G7 supersession: by a rejection of another proposal", (SUPERSESSION_BINDING,),
             *_supersession("b", "PROPOSAL_REJECTED", decision="aidecision_x2"), setup=x + (reject_x,),
             expect=refused_by(SUPERSESSION_BINDING)),
        Case("G7 supersession: a consumed grant, even by a later revision", (SUPERSESSION_BINDING,),
             *_supersession("a", revision="aireview_a2"),
             bypass=(insert("ai_proposal_review_revisions", **revision_row("a", 1, 2)),),
             expect=refused_by(SUPERSESSION_BINDING)),
        Case("G7 supersession: a grant retired by a re-issue", (SUPERSESSION_BINDING,),
             *_supersession("d", revision="aireview_d2"),
             bypass=(insert("ai_proposal_review_revisions", **revision_row("d", 4, 2)),),
             expect=refused_by(SUPERSESSION_BINDING)),
        Case("G7 supersession: an unretired grant by a later revision", (SUPERSESSION_BINDING,),
             *_supersession("b", revision="aireview_b2"),
             bypass=(insert("ai_proposal_review_revisions", **revision_row("b", 2, 2)),)),
    ]


AUTH_BINDING = "ai_proposal_confirmations_authorization_binding"
UNRETIRED = "ai_proposal_confirmations_unretired"
CLASSIFICATION = "ai_proposal_confirmations_classification"
WINDOW = "human_authorization_grant_consumptions_window"
PAIRING = "human_authorization_grant_consumptions_pairing"
LINK_B = insert("ai_proposal_confirmations", **link_row("b", 5))


def _consumption(at: str, grant: str = "b", baec: str = "BAEC-b") -> tuple:
    return insert("human_authorization_grant_consumptions", grant_id=grant_id(grant), baec_id=baec,
                  executor_surface="mcp-write/confirm_baec/v1", consumed_at=at)


def _confirmation_cases() -> list[Case]:
    auth = "UPDATE human_authorizations SET {} WHERE authorization_id = 5"
    cases = [
        Case("G8 a link for the grant's own confirmed BAEC", (AUTH_BINDING, UNRETIRED, CLASSIFICATION), *LINK_B),
        Case("G8 link: another authorization than the BAEC's confirmation", (AUTH_BINDING,),
             *insert("ai_proposal_confirmations", **link_row("b", 6)),
             setup=(insert("human_authorizations", **authorization_row("CONFIRM_BAEC", "BAEC-b", ISSUED)),),
             expect=refused_by(AUTH_BINDING)),
        Case("G8 link: another BAEC's confirmation", (AUTH_BINDING,),
             *insert("ai_proposal_confirmations", **link_row("b", 6)),
             setup=(insert("human_authorizations", **authorization_row("CONFIRM_BAEC", "BAEC-n", ISSUED)),
                    insert("baec_records", **record_row("BAEC-n", 6))),
             expect=refused_by(AUTH_BINDING)),
        Case("G8 link: authorization for another action", (AUTH_BINDING,), *LINK_B,
             bypass=((auth.format("action = 'RECORD_DORMANCY_JUDGMENT'"), ()),), expect=refused_by(AUTH_BINDING)),
        Case("G8 link: authorization for another subject", (AUTH_BINDING,), *LINK_B,
             bypass=((auth.format("subject_id = 'BAEC-elsewhere'"), ()),), expect=refused_by(AUTH_BINDING)),
        Case("G8 link: authorization by another actor", (AUTH_BINDING,), *LINK_B,
             bypass=((auth.format("authorized_by = 'Someone Else'"), ()),), expect=refused_by(AUTH_BINDING)),
        Case("G8 link: the same instant spelled differently", (AUTH_BINDING,), *LINK_B,
             bypass=((auth.format("authorized_at = '2026-09-01T10:00:00+00:00'"), ()),),
             expect=refused_by(AUTH_BINDING)),
        Case("G8 link: a consumed grant", (UNRETIRED,), *LINK_B, bypass=(_consumption(CONSUMED),),
             expect=refused_by(UNRETIRED)),
        Case("G8 link: a superseded grant", (UNRETIRED,), *LINK_B,
             bypass=(_supersession("b", revision="aireview_c2"),), expect=refused_by(UNRETIRED)),
        Case("G8 link: a grant replaced by a re-issue", (UNRETIRED,), *LINK_B,
             setup=(_grant("b", 2, "f", issue_sequence=2, supersedes_grant_id=grant_id("b")),),
             expect=refused_by(UNRETIRED)),
        Case("G8 link: a revision that is no longer the latest", (UNRETIRED,), *LINK_B,
             bypass=(insert("ai_proposal_review_revisions", **revision_row("b", 2, 2)),),
             expect=refused_by(UNRETIRED)),
        Case("G8 link: a rejected proposal", (UNRETIRED,), *LINK_B,
             bypass=(insert("ai_proposal_review_decisions", **decision_row("b", "aidecision_b2", None, "REJECTED")),),
             expect=refused_by(UNRETIRED)),
        Case("G8 link: a BAEC that is no longer CURRENT", (CLASSIFICATION,), *LINK_B,
             setup=(("UPDATE baec_records SET staleness_status = 'STALE' WHERE baec_id = 'BAEC-b'", ()),),
             expect=refused_by(CLASSIFICATION)),
        Case("G8 link: a record that is not a confirmed BAEC", (CLASSIFICATION,), *LINK_B,
             bypass=(("UPDATE baec_records SET classification = 'NOT_BAEC' WHERE baec_id = 'BAEC-b'", ()),),
             expect=refused_by(CLASSIFICATION)),
        Case("G8 link: a missing BAEC", (CLASSIFICATION,),
             *insert("ai_proposal_confirmations", **link_row("b", 5, baec_id="BAEC-404")),
             expect=refused_by(CLASSIFICATION)),
        Case("G8 consumption: pairs with another link", (PAIRING,), *_consumption(CONSUMED, baec="BAEC-a"),
             setup=(LINK_B,), expect=refused_by(PAIRING)),
        Case("G8 consumption: no link", (PAIRING,), *_consumption(CONSUMED), expect=refused_by(PAIRING)),
    ]
    for label, at, ok in (("at issuance", ISSUED, True), ("inside the window", CONSUMED, True),
                          ("at issuance spelled with Z", "2026-09-01T10:00:00Z", True),
                          ("the last microsecond (SQLite: .999)", "2026-09-01T10:14:59.999999+00:00", True),
                          ("at expiry", EXPIRES, False), ("after expiry", "2026-09-01T10:15:00.001000+00:00", False),
                          ("half a millisecond before issuance", "2026-09-01T09:59:59.999500+00:00", False),
                          ("before issuance", "2026-09-01T09:00:00.000000+00:00", False),
                          ("not a timestamp", "soon", False), ("SQLite-only 'now'", "now", False),
                          # Exact to the microsecond on both backends (migration 0006): julianday's millisecond
                          # rounding once refused .9997 s before expiry on SQLite 3.46.
                          ("0.3 ms before expiry", "2026-09-01T10:14:59.999700+00:00", True),
                          ("the last microsecond at another offset", "2026-09-01T12:14:59.999999+02:00", True),
                          ("expiry at another offset", "2026-09-01T05:15:00-05:00", False),
                          ("a space instead of T", "2026-09-01 10:05:00+00:00", False),
                          ("seven fractional digits", "2026-09-01T10:05:00.0000001+00:00", False),
                          ("hour 24", "2026-09-01T24:00:00+00:00", False),
                          ("no offset", "2026-09-01T10:05:00", False),
                          ("an offset beyond 14 hours", "2026-09-01T10:05:00+15:00", False),
                          ("a day that does not exist", "2026-02-30T10:05:00+00:00", False)):
        cases.append(Case(f"G8 consumption: {label}", (WINDOW,), *_consumption(at), setup=(LINK_B,),
                          expect=("ok", []) if ok else refused_by(WINDOW)))
    return cases


CASES = (_append_only_cases() + _replace_cases() + _protected_column_cases() + _verbatim_cases() + _provenance_cases()
         + _lineage_cases() + _grant_cases() + _confirmation_cases())
BY_ID = {case.id: case for case in CASES}
assert len(BY_ID) == len(CASES), "case ids must be unique"


def attack(side, case: Case, *, drop: tuple[str, ...] = ()) -> tuple:
    for sql, params in case.setup:
        outcome = side.run(sql, params, writer=sql.startswith("UPDATE"))
        assert outcome[0] == "ok", (side.name, case.id, sql, outcome)
    if case.bypass:
        side.bypass(case.bypass)
    for trigger in case.without + drop:
        side.drop_trigger(trigger)
    return side.run(case.sql, case.params, writer=case.writer, pg_sql=case.pg_sql, check=case.check)


@pytest.mark.parametrize("case", CASES, ids=[c.id for c in CASES])
def test_each_attack_has_the_same_outcome_on_both_backends(sides, case):
    outcomes = [attack(side, case) for side in sides]
    assert outcomes[0] == outcomes[1] == case.expect, outcomes


def test_every_sqlite_trigger_has_an_attack_case():
    covered = {t for case in CASES for t in case.triggers}
    assert len(SQLITE_TRIGGERS) == 92 and set(SQLITE_TRIGGERS) <= covered, sorted(set(SQLITE_TRIGGERS) - covered)


@contextmanager
def seeded(kind: str, migrated: dict, probe: str):
    """A newly seeded side, closed (its transaction rolled back, so any dropped trigger is restored) on exit."""
    side = SQLiteSide() if kind == "sqlite" else PostgresSide(migrated["admin"], probe)
    try:
        seed(side)
        yield side
    finally:
        side.close()


@pytest.mark.parametrize("trigger", SQLITE_TRIGGERS)
def test_dropping_any_trigger_changes_an_outcome_on_both_backends(migrated, engine1_roles, trigger):
    """Mutation, per trigger: with only this trigger removed (SQLite: DROP TRIGGER; PostgreSQL: DROP TRIGGER on its
    one equivalent), at least one of its cases no longer has the expected outcome, on each backend separately. Every
    case runs on a freshly seeded database."""
    cases = [c for c in CASES if trigger in c.triggers and trigger not in c.without]
    for kind in ("sqlite", "postgres"):
        changed = []
        for case in cases:
            with seeded(kind, migrated, engine1_roles["probe"]) as side:
                if attack(side, case, drop=(trigger,)) != case.expect:
                    changed.append(case.id)
        assert changed, (kind, trigger)


# --- clause-level mutants --------------------------------------------------------------------------------------------
#
# Each mutant makes exactly one clause of one guard a tautology (or removes one key or protected column), on SQLite by
# recreating the trigger from its stored SQL with that text replaced, and on PostgreSQL by recreating the function (or
# trigger) the same way. The named case must then change outcome on each backend: the clause is load-bearing on both.


@dataclass(frozen=True)
class Mutant:
    id: str
    case: str
    sqlite: tuple[str, str, str]  # (trigger, old text, new text)
    postgres: tuple  # ("function", name, old, new) | ("trigger", sqlite trigger name, old, new)


def _empty(table: str, alias: str = "") -> tuple[str, str]:
    """old -> new text that makes one (NOT) EXISTS over `table` see no rows."""
    if alias:
        return (f"FROM baec_engine1.{table} {alias}", f"FROM (SELECT * FROM baec_engine1.{table} WHERE false) {alias}")
    return (f"FROM {table} WHERE", f"FROM (SELECT * FROM {table} WHERE 0) WHERE")


def _fn(name: str, old: str, new: str) -> tuple:
    return ("function", name, old, new)


_REJ = "EXISTS (SELECT 1 FROM ai_proposal_review_decisions WHERE proposal_id = NEW.proposal_id AND decision = 'REJECTED')"
_CONF = "EXISTS (SELECT 1 FROM ai_proposal_confirmations WHERE proposal_id = NEW.proposal_id)"
_PG_REJ = "EXISTS (SELECT 1 FROM baec_engine1.ai_proposal_review_decisions d"
_PG_CONF = "EXISTS (SELECT 1 FROM baec_engine1.ai_proposal_confirmations c"
_GRANTS, _CONSUMED, _SUPERSEDED = ("human_authorization_grants", "human_authorization_grant_consumptions",
                                   "human_authorization_grant_supersessions")


_us = engine1._exact_instant_us  # the SQLite window guard's exact-instant expression


def _clause_mutants() -> list[Mutant]:
    m = [
        Mutant("evidence blank", "G4 evidence: blank", (EVIDENCE, "NEW.text = ''", "0"),
               _fn(EVIDENCE, "NEW.text = ''", "false")),
        Mutant("evidence substring", "G4 evidence: not in the interaction",
               (EVIDENCE, "instr(", "1 OR instr("), _fn(EVIDENCE, "pg_catalog.strpos(", "1 OR pg_catalog.strpos(")),
        Mutant("excerpt blank", "G4 excerpt: whitespace only (newline)",
               (EXCERPT, "trim(NEW.text, ' ' || char(9, 10, 11, 12, 13)) = ''", "0"),
               _fn(EXCERPT, "pg_catalog.btrim(NEW.text, E' \\t\\n\\x0b\\f\\r') = ''", "false")),
        Mutant("model binding success", "G5 success must name the requested model",
               (MODEL_BINDING, "NEW.status = 'success'", "0"), _fn(MODEL_BINDING, "NEW.status = 'success'", "false")),
        Mutant("model binding mismatch", "G5 model_mismatch must name another model",
               (MODEL_BINDING, "NEW.status = 'model_mismatch'", "0"),
               _fn(MODEL_BINDING, "NEW.status = 'model_mismatch'", "false")),
        Mutant("codes other statuses", "G5 codes on a status without codes",
               (CODES, "ELSE NEW.failure_codes IS NULL", "ELSE 1"),
               _fn(CODES, "ok := NEW.failure_codes IS NULL;", "ok := true;")),
        Mutant("parse code count", "G5 parse_failure: two codes",
               (CODES, "json_array_length(NEW.failure_codes) = 1", "1"),
               _fn(CODES, "pg_catalog.json_array_length(codes) = 1", "true")),
        Mutant("parse vocabulary", "G5 parse_failure: an unknown code",
               (CODES, "AND json_extract(NEW.failure_codes, '$[0]') IN (",
                "AND 1 OR json_extract(NEW.failure_codes, '$[0]') IN ("),
               _fn(CODES, "AND (codes ->> 0) IN (", "AND true OR (codes ->> 0) IN (")),
        Mutant("semantic non-empty", "G5 semantic_validation_failure: empty",
               (CODES, "json_array_length(NEW.failure_codes) >= 1", "1"),
               _fn(CODES, "pg_catalog.json_array_length(codes) >= 1", "true")),
        Mutant("semantic vocabulary of the run's validator", "G5 semantic_validation_failure: a v2-only code under v1",
               (CODES, "OR NOT COALESCE((", "OR 0 AND NOT COALESCE(("),
               _fn(CODES, "<> 'string' OR NOT COALESCE(", "<> 'string' OR false AND NOT COALESCE(")),
        Mutant("semantic strictly increasing", "G5 semantic_validation_failure: unsorted codes",
               (CODES, "WHERE NOT COALESCE(earlier.value < later.value, 0)", "WHERE 0"),
               _fn(CODES, "WHERE NOT COALESCE((earlier.value", "WHERE false AND NOT COALESCE((earlier.value")),
        Mutant("output digest", "G5 output with another digest",
               (REQUIRE_RESULT, "AND output_digest = NEW.output_digest", ""),
               _fn(REQUIRE_RESULT, "AND r.output_digest = NEW.output_digest", "")),
        Mutant("artifact success", "G5 artifact for a semantic failure",
               (REQUIRE_SUCCESS, "AND result.status = 'success'", ""),
               _fn(REQUIRE_SUCCESS, "AND result.status = 'success'", "")),
        Mutant("artifact output digest", "G5 artifact whose output digest differs from its result's",
               (REQUIRE_SUCCESS, "AND output.output_digest = result.output_digest", ""),
               _fn(REQUIRE_SUCCESS, "AND output.output_digest = result.output_digest", "")),
    ]
    for column, case in (("artifact_digest", "artifact digest differs"), ("account_id", "another account"),
                         ("interaction_id", "another interaction")):
        text = f"AND artifact.{column} = NEW.{column}"
        m.append(Mutant(f"proposal {column}", f"G6 proposal: {case}", (BINDING, text, ""), _fn(BINDING, text, "")))
    m += [
        Mutant("proposal result success", "G6 proposal: the run's result is not success",
               (BINDING, "AND result.status = 'success'", ""), _fn(BINDING, "AND result.status = 'success'", "")),
        Mutant("proposal validation version", "G6 proposal: validated under v1",
               (BINDING, "AND run.validation_version = 'baec-extraction-validation/v2'", ""),
               _fn(BINDING, "AND run.validation_version = 'baec-extraction-validation/v2'", "")),
        Mutant("revision number", "G6 revision: a skipped number",
               (SEQUENCE, "NEW.revision_number IS NOT 1", "0 AND NEW.revision_number IS NOT 1"),
               _fn(SEQUENCE, "IF NEW.revision_number", "IF false AND NEW.revision_number")),
        Mutant("revision previous", "G6 revision: another proposal's revision as previous",
               (SEQUENCE, "OR NEW.previous_revision_id IS NOT", "OR 0 AND NEW.previous_revision_id IS NOT"),
               _fn(SEQUENCE, "OR NEW.previous_revision_id IS DISTINCT FROM",
                   "OR false AND NEW.previous_revision_id IS DISTINCT FROM")),
        Mutant("revision after rejection", "G6 revision: a rejected proposal", (OPEN, _REJ, "0"),
               _fn(OPEN, f"IF {_PG_REJ}", f"IF false AND {_PG_REJ}")),
        Mutant("revision after confirmation", "G6 revision: a confirmed proposal", (OPEN, _CONF, "0"),
               _fn(OPEN, f"OR {_PG_CONF}", f"OR false AND {_PG_CONF}")),
        Mutant("decision proposal account", "G6 decision: another account",
               (DECISION_BINDING, "FROM ai_proposals WHERE proposal_id = NEW.proposal_id AND account_id = "
                                  "NEW.account_id", "FROM ai_proposals WHERE proposal_id = NEW.proposal_id"),
               _fn(DECISION_BINDING, "AND p.proposal_id = NEW.proposal_id AND p.account_id = NEW.account_id",
                   "AND p.proposal_id = NEW.proposal_id")),
        Mutant("decision revision proposal", "G6 decision: another proposal's revision",
               (DECISION_BINDING, "AND proposal_id = NEW.proposal_id AND account_id = NEW.account_id))",
                "AND account_id = NEW.account_id))"),
               _fn(DECISION_BINDING, "AND r.proposal_id = NEW.proposal_id AND r.account_id = NEW.account_id",
                   "AND r.account_id = NEW.account_id")),
        Mutant("decision revision account", "G6 decision: a revision recorded for another account",
               (DECISION_BINDING, "AND proposal_id = NEW.proposal_id AND account_id = NEW.account_id))",
                "AND proposal_id = NEW.proposal_id))"),
               _fn(DECISION_BINDING, "AND r.proposal_id = NEW.proposal_id AND r.account_id = NEW.account_id",
                   "AND r.proposal_id = NEW.proposal_id")),
        Mutant("accept only the latest", "G6 decision: accept a revision that is not the latest",
               (LATEST, "NEW.decision = 'ACCEPTED' AND", "0 AND"), _fn(LATEST, "IF NEW.decision = 'ACCEPTED' AND",
                                                                       "IF false AND")),
        Mutant("nothing after a rejection", "G6 decision: after a rejection", (TERMINAL, _REJ, "0"),
               _fn(TERMINAL, f"IF {_PG_REJ}", f"IF false AND {_PG_REJ}")),
        Mutant("nothing after a confirmation", "G6 decision: after a confirmation", (TERMINAL, _CONF, "0"),
               _fn(TERMINAL, f"OR {_PG_CONF}", f"OR false AND {_PG_CONF}")),
        Mutant("grant needs an acceptance", "G7 grant: no acceptance", (ACCEPTED, "NOT EXISTS (", "0 AND EXISTS ("),
               _fn(ACCEPTED, "IF NOT EXISTS (", "IF false AND EXISTS (")),
        Mutant("grant on the latest revision", "G7 grant: a revision that is no longer the latest",
               (ACCEPTED, "OR NEW.review_revision_id IS NOT", "OR 0 AND NEW.review_revision_id IS NOT"),
               _fn(ACCEPTED, "OR NEW.review_revision_id IS DISTINCT FROM",
                   "OR false AND NEW.review_revision_id IS DISTINCT FROM")),
        Mutant("grant after a rejection", "G7 grant: accepted, then the proposal was rejected", (ACCEPTED, _REJ, "0"),
               _fn(ACCEPTED, f"OR {_PG_REJ}", f"OR false AND {_PG_REJ}")),
        Mutant("grant after a confirmation", "G7 grant: a confirmed proposal", (ACCEPTED, _CONF, "0"),
               _fn(ACCEPTED, f"OR {_PG_CONF}", f"OR false AND {_PG_CONF}")),
        Mutant("one unretired grant", "G7 grant: a second unretired grant for the revision",
               (ONE_UNRETIRED, "AND grant_row.grant_id IS NOT NEW.supersedes_grant_id", "AND 0"),
               _fn(ONE_UNRETIRED, "AND grant_row.grant_id IS DISTINCT FROM NEW.supersedes_grant_id", "AND false")),
        Mutant("re-issue names its predecessor", "G7 grant: a re-issue naming another revision's grant",
               (ONE_UNRETIRED, "OR (NEW.supersedes_grant_id IS NOT NULL AND", "OR (0 AND"),
               _fn(ONE_UNRETIRED, "OR (NEW.supersedes_grant_id IS NOT NULL AND", "OR (false AND")),
        Mutant("re-issue sequence", "G7 grant: a re-issue skipping a sequence number",
               (ONE_UNRETIRED, "AND grant_row.issue_sequence = NEW.issue_sequence - 1", ""),
               _fn(ONE_UNRETIRED, "AND grant_row.issue_sequence = NEW.issue_sequence - 1", "")),
        Mutant("supersede only unconsumed", "G7 supersession: a consumed grant, even by a later revision",
               (SUPERSESSION_BINDING, *_empty(_CONSUMED)), _fn(SUPERSESSION_BINDING, *_empty(_CONSUMED, "c"))),
        Mutant("supersede only once", "G7 supersession: a grant already superseded",
               (SUPERSESSION_BINDING, *_empty(_SUPERSEDED)), _fn(SUPERSESSION_BINDING, *_empty(_SUPERSEDED, "s"))),
        Mutant("supersede only unreplaced", "G7 supersession: a grant retired by a re-issue",
               (SUPERSESSION_BINDING, f"FROM {_GRANTS} WHERE supersedes_grant_id",
                f"FROM (SELECT * FROM {_GRANTS} WHERE 0) WHERE supersedes_grant_id"),
               _fn(SUPERSESSION_BINDING, *_empty(_GRANTS, "n"))),
        Mutant("supersede by the same proposal", "G7 supersession: by another proposal's revision",
               (SUPERSESSION_BINDING, "AND later.proposal_id = grant_row.proposal_id", ""),
               _fn(SUPERSESSION_BINDING, "AND later.proposal_id = grant_row.proposal_id", "")),
        Mutant("supersede by a later revision", "G7 supersession: by the grant's own revision",
               (SUPERSESSION_BINDING, "AND later.revision_number > granted.revision_number", ""),
               _fn(SUPERSESSION_BINDING, "AND later.revision_number > granted.revision_number", "")),
        Mutant("supersede by this proposal's rejection", "G7 supersession: by a rejection of another proposal",
               (SUPERSESSION_BINDING, "AND proposal_id = grant_row.proposal_id AND decision = 'REJECTED'",
                "AND decision = 'REJECTED'"),
               _fn(SUPERSESSION_BINDING, "AND d.proposal_id = grant_row.proposal_id AND d.decision = 'REJECTED'",
                   "AND d.decision = 'REJECTED'")),
    ]
    for column, sqlite_text, case in (
            ("authorization id", "AND authorization.authorization_id = NEW.authorization_id",
             "G8 link: another authorization than the BAEC's confirmation"),
            ("action", "AND authorization.action = 'CONFIRM_BAEC'", "G8 link: authorization for another action"),
            ("subject", "AND authorization.subject_id = NEW.baec_id", "G8 link: authorization for another subject"),
            ("actor", "AND authorization.authorized_by = grant_row.actor_label",
             "G8 link: authorization by another actor"),
            ("issuance time", "AND authorization.authorized_at = grant_row.issued_at",
             "G8 link: the same instant spelled differently")):
        m.append(Mutant(f"link authorization {column}", case, (AUTH_BINDING, sqlite_text, ""),
                        _fn(AUTH_BINDING, sqlite_text.replace("authorization.", "auth_row."), "")))
    m += [
        Mutant("link unconsumed", "G8 link: a consumed grant", (UNRETIRED, *_empty(_CONSUMED)),
               _fn(UNRETIRED, *_empty(_CONSUMED, "c"))),
        Mutant("link unsuperseded", "G8 link: a superseded grant", (UNRETIRED, *_empty(_SUPERSEDED)),
               _fn(UNRETIRED, *_empty(_SUPERSEDED, "s"))),
        Mutant("link unreplaced", "G8 link: a grant replaced by a re-issue",
               (UNRETIRED, f"FROM {_GRANTS} WHERE supersedes_grant_id",
                f"FROM (SELECT * FROM {_GRANTS} WHERE 0) WHERE supersedes_grant_id"),
               _fn(UNRETIRED, *_empty(_GRANTS, "n"))),
        Mutant("link on the latest revision", "G8 link: a revision that is no longer the latest",
               (UNRETIRED, "OR NEW.review_revision_id IS NOT", "OR 0 AND NEW.review_revision_id IS NOT"),
               _fn(UNRETIRED, "OR NEW.review_revision_id IS DISTINCT FROM",
                   "OR false AND NEW.review_revision_id IS DISTINCT FROM")),
        Mutant("link not rejected", "G8 link: a rejected proposal", (UNRETIRED, f"OR {_REJ}", "OR 0"),
               _fn(UNRETIRED, f"OR {_PG_REJ}", f"OR false AND {_PG_REJ}")),
        Mutant("link confirmed classification", "G8 link: a record that is not a confirmed BAEC",
               (CLASSIFICATION, "AND classification = 'CONFIRMED_BAEC'", ""),
               _fn(CLASSIFICATION, "AND b.classification = 'CONFIRMED_BAEC'", "")),
        Mutant("link CURRENT", "G8 link: a BAEC that is no longer CURRENT",
               (CLASSIFICATION, "AND staleness_status = 'CURRENT'", ""),
               _fn(CLASSIFICATION, "AND b.staleness_status = 'CURRENT'", "")),
        Mutant("window lower bound", "G8 consumption: before issuance",
               (WINDOW, f"{_us('grant_row.issued_at')} <= {_us('NEW.consumed_at')}", "1"),
               _fn(WINDOW, "baec_engine1.exact_instant_us(grant_row.issued_at) <= "
                   "baec_engine1.exact_instant_us(NEW.consumed_at)", "true")),
        Mutant("window upper bound", "G8 consumption: at expiry",
               (WINDOW, f"{_us('NEW.consumed_at')} < {_us('grant_row.expires_at')}", "1"),
               _fn(WINDOW, "baec_engine1.exact_instant_us(NEW.consumed_at) < "
                   "baec_engine1.exact_instant_us(grant_row.expires_at)", "true")),
        Mutant("consumption pairs with its link", "G8 consumption: pairs with another link",
               (PAIRING, "AND grant_id = NEW.grant_id", ""), _fn(PAIRING, "AND c.grant_id = NEW.grant_id", "")),
    ]
    for table, keys in REPLACE_KEYS.items():  # every UNIQUE key of a table that has more than one
        trigger = f"{table}_no_replace"
        for key in keys if len(keys) > 1 else ():
            sqlite_text = f"EXISTS (SELECT 1 FROM {table} WHERE " + " AND ".join(f"{c} = NEW.{c}" for c in key) + ")"
            if table == "ai_proposal_review_decisions":
                pg = _fn(trigger, f"AND d.{key[0]} = NEW.{key[0]})", "AND false)")
            else:
                rowid = gate.ENGINE1_ROWID_KEYS.get(table)
                argument = ",".join(("#" + c) if c == rowid else c for c in key)
                pg = ("trigger", trigger, f"'{argument}'", "'organization_id'")  # a key every row shares... see below
            m.append(Mutant(f"replace key {table} ({', '.join(key)})", f"G2 {table} collides on ({', '.join(key)})",
                            (trigger, sqlite_text, "0"), pg))
    m.append(Mutant("replace a second rejection", "G2 a second rejection cannot replace the first",
                    ("ai_proposal_review_decisions_no_replace",
                     "(NEW.decision = 'REJECTED' AND EXISTS", "(0 AND EXISTS"),
                    _fn("ai_proposal_review_decisions_no_replace", "OR (NEW.decision = 'REJECTED' AND",
                        "OR (false AND")))
    for column in ("buyer_exact_statement", "classification", "confirmation_authorization_id"):
        m.append(Mutant(f"protected column {column}", f"G3 update {column}",
                        ("baec_records_protected_no_update", f" {column}, ", " "),
                        ("trigger", "baec_records_protected_no_update", f" {column}, ", " ")))
    return m


MUTANTS = _clause_mutants()


def _pg_recreate_trigger(side: PostgresSide, trigger: str, old: str, new: str) -> None:
    definition = gate.expected_engine1_triggers()[PG_NAMES[trigger]]
    assert definition.count(old) == 1, (trigger, old)
    if new == "'organization_id'":  # remove this key from the replace guard's key list
        definition = definition.replace(f", {old}", "").replace(f"{old}, ", "")
    else:
        definition = definition.replace(old, new)
    side.raw.execute("RESET ROLE")
    side.raw.execute(f"DROP TRIGGER {PG_NAMES[trigger]} ON baec_engine1.{TABLE_OF[trigger]}")
    side.raw.execute(definition)
    side.raw.execute("SET LOCAL ROLE baec_app")


def _apply(side, mutant: Mutant) -> None:
    if side.name == "sqlite":
        side.replace_clause(*mutant.sqlite)
    elif mutant.postgres[0] == "function":
        side.replace_clause(*mutant.postgres[1:])
    else:
        _pg_recreate_trigger(side, *mutant.postgres[1:])


@pytest.mark.parametrize("mutant", MUTANTS, ids=[m.id for m in MUTANTS])
def test_each_clause_is_load_bearing_on_both_backends(migrated, engine1_roles, mutant):
    case = BY_ID[mutant.case]
    for kind in ("sqlite", "postgres"):
        with seeded(kind, migrated, engine1_roles["probe"]) as side:
            for sql, params in case.setup:
                assert side.run(sql, params, writer=sql.startswith("UPDATE"))[0] == "ok"
            if case.bypass:
                side.bypass(case.bypass)
            for trigger in case.without:
                side.drop_trigger(trigger)
            _apply(side, mutant)
            outcome = side.run(case.sql, case.params, writer=case.writer, pg_sql=case.pg_sql, check=case.check)
        assert outcome != case.expect, (kind, mutant.id, outcome)


# --- CHECK constraints -------------------------------------------------------------------------------------------------
# The corruptions the existing suites simulate with PRAGMA ignore_check_constraints cannot be stored on PostgreSQL at
# all (CHECKs cannot be switched off). Here every such value is refused at write time, identically, on both.

CHECK_VIOLATIONS = {
    "unknown account state": insert("accounts", account_id="ACC-7", name="x", state="HOT_LEAD"),
    "external provenance on interaction evidence": _evidence("formal supplier review", provenance="EXTERNAL_EVIDENCE"),
    "AI inference as interaction evidence": _evidence("formal supplier review", provenance="AI_INFERENCE"),
    "confirmation carrying a target state": insert("human_authorizations", **authorization_row(
        "CONFIRM_BAEC", "B-1", target="ACTIVE_OPPORTUNITY")),
    "state change without a target state": insert("human_authorizations", **authorization_row(
        "CHANGE_ACCOUNT_STATE", "ACC-1")),
    "unknown classification": insert("baec_records", **record_row("B-7", None, classification="HOT_LEAD")),
    "unknown articulation origin": insert("baec_records", **record_row("B-7", None, articulation_origin="X")),
    "unknown elicitation mode": insert("baec_records", **record_row("B-7", None, elicitation_mode="X")),
    "unknown staleness status": insert("baec_records", **record_row("B-7", None, staleness_status="FRESH")),
    "normalized model without normalized text": insert("baec_records", **record_row("B-7", None,
                                                                                    normalized_model="m")),
    "normalized text without a timestamp": insert("baec_records", **record_row("B-7", None, normalized_text="t")),
    "normalized timestamp without text": insert("baec_records", **record_row("B-7", None,
                                                                             normalized_generated_at=T0)),
    "unknown criterion": insert("criterion_assessments", baec_id="B-2", criterion="VIBES", position=0, finding="MET"),
    "unknown finding": insert("criterion_assessments", baec_id="B-2", criterion="PRESENT_NON_EVALUATION", position=0,
                              finding="MAYBE"),
    "negative position": insert("criterion_assessments", baec_id="B-2", criterion="PRESENT_NON_EVALUATION",
                                position=-1, finding="MET"),
    "unknown comparator": insert("stringency_expressions", baec_id="B-2", verbatim_text="x", comparator="ROUGHLY"),
    "unknown plausibility": insert("dormancy_judgments", baec_id="B-1", plausibility="MAYBE", addressability="YES",
                                   authorization_id=1),
    "unknown addressability": insert("dormancy_judgments", baec_id="B-1", plausibility="YES",
                                     addressability="SORT_OF", authorization_id=1),
    "unknown destination state": insert("account_state_transitions", account_id="ACC-2", to_state="HOT_LEAD",
                                        authorization_id=1, unresolved="[]", recorded_at=T0),
    "unknown ground": insert("account_state_transitions", account_id="ACC-2", to_state="NO_PLAUSIBLE_PATH",
                             authorization_id=1, ground="BORED", unresolved="[]", recorded_at=T0),
    "run with another provider": insert("ai_runs", **run_row("RUN-z", provider="other")),
    "run with a blank validation version": insert("ai_runs", **run_row("RUN-z", validation_version=" \t")),
    "run digest that is not lowercase hex": insert("ai_runs", **run_row("RUN-z", prompt_digest="A" * 64)),
    "grant with another TTL": insert("human_authorization_grants", **grant_row("x", 9, grant_id=grant_id("9"),
                                                                              ttl_seconds=60)),
    "proposal content that is not a JSON object": insert("ai_proposals", proposal_id="aiprop_z", origin="AI_DRAFT",
                                                         mapping_version="baec-ai-proposal-mapping/v1",
                                                         content_version="baec-ai-proposal-content/v1",
                                                         artifact_id="ART-a", artifact_digest=h(401),
                                                         account_id="ACC-1", interaction_id="INT-1", content="[1]",
                                                         proposal_digest=h(1), created_by=ACTOR, created_at=T0),
}


@pytest.mark.parametrize("label", sorted(CHECK_VIOLATIONS))
def test_check_constraints_refuse_the_same_values_on_both_backends(sides, label):
    sql, params = CHECK_VIOLATIONS[label]
    trigger_guards = tuple(n for n, timing, event, _, t in gate.engine1_sqlite_triggers()
                           if (timing, event) == ("BEFORE", "INSERT") and t == re.match(r"INSERT INTO (\w+)", sql)[1])
    outcomes = []
    for side in sides:
        for trigger in trigger_guards:  # the CHECK alone: no guard may refuse first
            side.drop_trigger(trigger)
        outcomes.append(side.run(sql, params))
    assert outcomes[0] == outcomes[1] == ("constraint", "check"), outcomes
