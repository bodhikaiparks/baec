"""V0.4 Phase 3C: the Engine 1 repository on PostgreSQL, compared with SQLite; tenancy, privileges, and the adapter.

    lifecycle     the same scripted sequence of Repository calls (accounts, interactions, every classification,
                  confirmed BAECs with and without thresholds, several conditions per account, every dormancy
                  judgment combination, every transition between the three states and the unclassified state with
                  valid and invalid prerequisites) on a SQLite database and on a PostgreSQL tenant transaction:
                  every return value and refusal (type and message), every read, and afterwards every row of all
                  25 tables, database-assigned ids included, must be identical
    structure     the PostgreSQL tables have SQLite's columns, primary keys, UNIQUE keys, and foreign keys
                  (prefixed with organization_id and workspace), read from both catalogs
    tenancy       another organization or workspace sees nothing, cannot reference another tenant's rows, cannot
                  forge tenancy columns, and the guards treat another tenant's rows as nonexistent
    privileges    the application role can read and append, and update only accounts.state
    adapter       the closed translation set; everything else is refused; the read-only block is enforced by
                  PostgreSQL; a refused statement leaves the write block usable, as SQLite's statement rollback does
    concurrency   writers of one workspace are serialized as BEGIN IMMEDIATE serializes one SQLite file

All data is synthetic. These are software tests, not evidence for the BAEC theory.
"""

from __future__ import annotations

import random
import sqlite3
import threading
from contextlib import closing, contextmanager
from datetime import datetime, timedelta, timezone

import pytest

from baec_app.data import database as engine1
from baec_app.data.database import StorageRefusal
from baec_app.data.records import SourceInteraction
from baec_app.data.repository import Repository
from baec_app.domain.enums import NoPlausiblePathGround, ReviewAnswer, StalenessStatus
from baec_app.domain.models import Account
from baec_console.postgres.database import ENGINE_SEARCH_PATH
from baec_console.postgres.engine1 import Engine1Connection, Engine1StatementRefused
from tests.builders import (
    AO,
    CD,
    HARBOR_QUOTE,
    NOW,
    NP,
    candidate,
    confirmed_record,
    evaluation_evidence,
    judgment,
    non_evaluation_evidence,
    state_auth,
)
from tests.persistence_builders import INTERACTION_TEXTS, LATER

from .conftest import AT, fresh, provision, psycopg

# --- the two repositories --------------------------------------------------------------------------------------------


@pytest.fixture
def organization(storage):
    organization_id = fresh("org-e1repo")
    provision(storage, organization_id, fresh("user-e1repo"), "Avery Repository (synthetic)")
    return organization_id


@contextmanager
def postgres_engine1(storage, organization_id: str, workspace: str = "local"):
    with storage.database.tenant(organization_id, workspace) as connection:
        yield Engine1Connection(connection)


def outcome(fn):
    try:
        value = fn()
    except Exception as error:  # noqa: BLE001 - the refusal itself is compared
        return ("refused", type(error).__qualname__, str(error))
    return ("ok", repr(value))


def lifecycle(repo: Repository) -> list:
    """Every Engine 1 repository operation the console relies on, valid and invalid, in one fixed order."""
    step = []

    def run(label, fn):
        step.append((label, outcome(fn)))

    run("account", lambda: repo.add_account(Account("ACC-1", "Harbor Surgical (synthetic)")))
    run("second account", lambda: repo.add_account(Account("ACC-2", "Other Synthetic Account")))
    run("duplicate account", lambda: repo.add_account(Account("ACC-1", "again")))
    run("account with a state", lambda: repo.add_account(Account("ACC-3", "x", AO)))
    for interaction_id, text in INTERACTION_TEXTS.items():
        run(f"interaction {interaction_id}", lambda i=interaction_id, t=text: repo.add_interaction(
            SourceInteraction(i, "ACC-1", NOW, t)))
    run("interaction of the other account", lambda: repo.add_interaction(
        SourceInteraction("INT-9", "ACC-2", NOW, "Buyer: " + HARBOR_QUOTE)))
    run("duplicate interaction", lambda: repo.add_interaction(SourceInteraction("INT-1", "ACC-1", NOW, "x")))
    run("interaction of a missing account", lambda: repo.add_interaction(
        SourceInteraction("INT-7", "ACC-404", NOW, "x")))
    from dataclasses import replace

    from baec_app.domain.baec_rules import create_nonconfirmed_classification_record
    from baec_app.domain.enums import ArticulationOrigin, BaecCriterion, CriterionFinding
    from tests.builders import seller_seeded_record
    unknown = candidate(findings={BaecCriterion.EVALUATION_LINKAGE: CriterionFinding.UNKNOWN})
    run("not confirmable B-N1 (seller seeded)", lambda: repo.save_classification_record(
        create_nonconfirmed_classification_record(candidate(ArticulationOrigin.SELLER_SEEDED), baec_id="B-N1",
                                                  captured_at=NOW)))
    run("not confirmable with a non-canonical reason", lambda: repo.save_classification_record(
        replace(seller_seeded_record(), baec_id="B-N3")))
    run("not confirmable B-N2", lambda: repo.save_classification_record(
        create_nonconfirmed_classification_record(unknown, baec_id="B-N2", captured_at=NOW)))
    run("confirmed B-1", lambda: repo.save_confirmed_baec(confirmed_record()))
    run("second condition B-2", lambda: repo.save_confirmed_baec(confirmed_record(baec_id="B-2")))
    run("duplicate B-1", lambda: repo.save_confirmed_baec(confirmed_record()))
    run("confirmed for an interaction of another account", lambda: repo.save_confirmed_baec(
        confirmed_record(account_id="ACC-2", baec_id="B-9")))
    run("confirmed that is not CURRENT", lambda: repo.save_confirmed_baec(
        confirmed_record(baec_id="B-3", staleness=StalenessStatus.STALE)))
    answers = list(ReviewAnswer)
    for plausibility in answers:
        for addressability in answers:
            run(f"judgment {plausibility.value}/{addressability.value}", lambda p=plausibility, a=addressability:
                repo.record_dormancy_judgment(judgment(p, a)))
    run("judgment of a missing BAEC", lambda: repo.record_dormancy_judgment(judgment(baec_id="B-404")))
    judgments = {(j.judgment.plausibility, j.judgment.addressability): j.judgment_id
                 for j in repo.list_dormancy_judgments("B-1")}

    def dormant(key, **extra):
        return lambda: repo.persist_transition_to_conditionally_dormant(
            "ACC-1", baec_id=extra.pop("baec_id", "B-1"), judgment_id=judgments.get(key),
            authorization=state_auth(CD), recorded_at=LATER, **extra)

    def active(**extra):
        return lambda: repo.persist_transition_to_active_opportunity(
            "ACC-1", evaluation_evidence=extra.pop("evidence", evaluation_evidence()),
            authorization=extra.pop("authorization", state_auth(AO)), recorded_at=LATER, **extra)

    def no_path(**extra):
        values = dict(ground=NoPlausiblePathGround.NO_PLAUSIBLE_BAEC, reason="No foreseeable condition.",
                      authorization=state_auth(NP), recorded_at=LATER)
        values.update(extra)
        return lambda: repo.persist_transition_to_no_plausible_path("ACC-1", **values)

    Y, N, U, NY = ReviewAnswer.YES, ReviewAnswer.NO, ReviewAnswer.UNKNOWN, ReviewAnswer.NOT_YET
    for label, fn in (
            ("active from unclassified without evidence", active(evidence=None)),
            ("active without authorization", active(authorization=None)),
            ("dormant with plausibility NO", dormant((N, Y))),
            ("dormant with addressability NO", dormant((Y, N))),
            ("dormant with addressability NOT_YET", dormant((Y, NY))),
            ("dormant with a not-confirmable record", dormant((Y, Y), baec_id="B-N1")),
            ("dormant with another BAEC's judgment", dormant((Y, Y), baec_id="B-2")),
            ("dormant with no judgment", dormant(None)),
            ("dormant YES/UNKNOWN (addressability unresolved)", dormant((Y, U))),
            ("dormant again (same state)", dormant((Y, Y))),
            ("active with evidence", active()),
            ("active again", active()),
            ("no plausible path without non-evaluation evidence", no_path()),
            ("no plausible path without a ground", no_path(ground=None,
                                                         non_evaluation_evidence=non_evaluation_evidence())),
            ("no plausible path without a reason", no_path(reason=None,
                                                         non_evaluation_evidence=non_evaluation_evidence())),
            ("no plausible path, leaving active", no_path(non_evaluation_evidence=non_evaluation_evidence(),
                                                          basis_interaction_id="INT-3")),
            ("no plausible path with another account's basis", no_path(basis_interaction_id="INT-9")),
            ("dormant from no plausible path", dormant((Y, Y))),
            ("active from dormant, with another account's evidence", active(
                evidence=evaluation_evidence(account_id="ACC-2"))),
            ("active from dormant", active())):
        run(label, fn)
    for label, fn in (("account", lambda: repo.get_account("ACC-1")), ("accounts", repo.list_accounts),
                      ("missing account", lambda: repo.get_account("ACC-404")),
                      ("interactions", lambda: repo.list_interactions("ACC-1")),
                      ("missing interaction", lambda: repo.get_interaction("INT-404")),
                      ("records", lambda: repo.list_baec_records("ACC-1")),
                      ("missing record", lambda: repo.get_baec_record("B-404")),
                      ("judgments", lambda: repo.list_dormancy_judgments("B-1")),
                      ("history", lambda: repo.get_transition_history("ACC-1")),
                      ("other account history", lambda: repo.get_transition_history("ACC-2"))):
        run(f"read {label}", fn)
    return step


def dump(connection) -> dict:
    out = {}
    for table in engine1.ALL_TABLES:
        with closing(engine1.open_database(":memory:")) as db:
            names = [r[1] for r in db.execute(f"PRAGMA table_info({table})").fetchall()]
        out[table] = sorted(map(tuple, connection.execute(f"SELECT {', '.join(names)} FROM {table}").fetchall()),
                            key=repr)
    return out


def test_the_engine1_lifecycle_is_identical_on_both_backends(storage, organization):
    with closing(engine1.open_database(":memory:")) as db:
        on_sqlite, sqlite_rows = lifecycle(Repository(db)), dump(db)
    with postgres_engine1(storage, organization) as connection:
        on_postgres, postgres_rows = lifecycle(Repository(connection)), dump(connection)
    assert on_postgres == on_sqlite
    assert postgres_rows == sqlite_rows
    refused = [label for label, result in on_sqlite if result[0] == "refused" or "allowed=False" in result[1]]
    allowed = [label for label, result in on_sqlite if "allowed=True" in result[1]]
    assert len(refused) >= 25 and len(allowed) == 5 and len(on_sqlite) == 65  # both paths, not only success
    states = [r for label, r in on_sqlite if label == "read account"]
    assert "ACTIVE_OPPORTUNITY" in states[0][1]
    assert sqlite_rows["account_state_transitions"] and sqlite_rows["human_authorizations"]


def test_a_signal_alone_never_moves_an_account_to_active_opportunity_on_postgresql(storage, organization):
    """RC-34 at the storage layer: external (signal) evidence cannot be stored as interaction evidence, and Active
    Opportunity needs evaluation evidence from an interaction."""
    from tests.builders import external_excerpt
    from baec_app.domain.models import EvaluationEvidence
    with postgres_engine1(storage, organization) as connection:
        repo = Repository(connection)
        repo.add_account(Account("ACC-1", "Harbor (synthetic)"))
        repo.add_interaction(SourceInteraction("INT-1", "ACC-1", NOW, INTERACTION_TEXTS["INT-1"]))
        assert outcome(lambda: EvaluationEvidence("ACC-1", external_excerpt(), NOW))[:2] == (
            "refused", "DomainValidationError")  # a signal cannot even be expressed as evaluation evidence
        result = repo.persist_transition_to_active_opportunity("ACC-1", evaluation_evidence=None,
                                                               authorization=state_auth(AO), recorded_at=LATER)
        assert not result.allowed and repo.get_account("ACC-1").state is None
        refused = outcome(lambda: connection.execute(
            "INSERT INTO interaction_evidence (interaction_id, provenance, text) VALUES (?, 'EXTERNAL', 'Buyer')",
            ("INT-1",)))
        assert refused[:2] == ("refused", "StorageRefusal")


# --- structure ---------------------------------------------------------------------------------------------------------

TENANCY = ("organization_id", "workspace")


def _sqlite_structure() -> dict:
    with closing(engine1.open_database(":memory:")) as db:
        out = {}
        for table in engine1.ALL_TABLES:
            info = db.execute(f"PRAGMA table_info({table})").fetchall()
            columns = [(r[1], {"TEXT": "text", "INTEGER": "bigint"}[r[2]], bool(r[3]) or r[5] > 0) for r in info]
            primary = tuple(r[1] for r in sorted((r for r in info if r[5]), key=lambda r: r[5]))
            unique = set()
            for index in db.execute(f"PRAGMA index_list({table})").fetchall():
                if index[2] and not index[4]:
                    unique.add(tuple(r[2] for r in db.execute(f"PRAGMA index_info({index[1]})")))
            foreign = {}
            for row in db.execute(f"PRAGMA foreign_key_list({table})").fetchall():
                foreign.setdefault(row[0], [row[2], [], [], row[6]])
                foreign[row[0]][1].append(row[3])
                foreign[row[0]][2].append(row[4] or "")
            fks = {(t, tuple(f), tuple(c), d) for t, f, c, d in foreign.values()}
            partial = {tuple(r[2] for r in db.execute(f"PRAGMA index_info({i[1]})"))
                       for i in db.execute(f"PRAGMA index_list({table})").fetchall() if i[4]}
            out[table] = (columns, primary, unique | {primary} - {()}, fks, partial)
        return out


def _postgres_structure(db) -> dict:
    out = {}
    for table in engine1.ALL_TABLES:
        info = db.execute("SELECT attname, format_type(atttypid, atttypmod), attnotnull FROM pg_attribute "
                          "WHERE attrelid = %s::regclass AND attnum > 0 AND NOT attisdropped ORDER BY attnum",
                          (f"baec_engine1.{table}",)).fetchall()
        columns = [(n, t, nn) for n, t, nn in info if n not in TENANCY + ("seq",)]
        keys = {}
        for kind, cols, ref, ref_cols, on_delete in db.execute(
                "SELECT k.contype::text, ARRAY(SELECT a.attname FROM unnest(k.conkey) WITH ORDINALITY u(n, i) "
                "JOIN pg_attribute a ON a.attrelid = k.conrelid AND a.attnum = u.n ORDER BY u.i), "
                "(SELECT relname FROM pg_class WHERE oid = k.confrelid), "
                "ARRAY(SELECT a.attname FROM unnest(k.confkey) WITH ORDINALITY u(n, i) "
                "JOIN pg_attribute a ON a.attrelid = k.confrelid AND a.attnum = u.n ORDER BY u.i), "
                "k.confdeltype::text FROM pg_constraint k WHERE k.conrelid = %s::regclass AND k.contype IN "
                "('p', 'u', 'f')", (f"baec_engine1.{table}",)).fetchall():
            keys.setdefault(kind, []).append((list(cols), ref, list(ref_cols), on_delete))
        for (definition,) in db.execute("SELECT pg_get_indexdef(indexrelid) FROM pg_index WHERE indrelid = "
                                        "%s::regclass AND indisunique AND NOT EXISTS (SELECT 1 FROM pg_constraint "
                                        "WHERE conindid = indexrelid)", (f"baec_engine1.{table}",)).fetchall():
            inner = definition[definition.index("(") + 1:definition.rindex(")")]
            if " WHERE " in definition:
                keys.setdefault("partial", []).append([c.strip() for c in definition.split(" WHERE ")[0]
                                                       .split("(", 1)[1].rstrip(")").split(",")])
            else:
                keys.setdefault("u", []).append(([c.strip().replace("baec_engine1.text_key(text)", "text")
                                                  for c in inner.split(",")], None, None, None))

        def strip(cols):
            assert cols[:2] == list(TENANCY), (table, cols)
            return tuple(cols[2:])
        primary = strip(keys["p"][0][0])
        unique = {strip(c) for c, *_ in keys.get("u", []) if c != ["seq"]} | {primary}
        fks = {(ref, strip(c), strip(rc), "RESTRICT" if d == "r" else d) for c, ref, rc, d in keys.get("f", [])
               if ref != "organizations"}
        partial = {strip(c) for c in keys.get("partial", [])}
        out[table] = (columns, primary, unique, fks, partial)
    return out


def test_the_postgresql_tables_have_sqlites_columns_keys_and_foreign_keys(migrated):
    """Read from both catalogs. PostgreSQL adds organization_id and workspace (in front of every key) and seq; the two
    free-text UNIQUE keys are unique indexes on the text's digest (text_key), shown here as the text column."""
    with psycopg.connect(migrated["admin"]) as db:
        live = _postgres_structure(db)
    expected = _sqlite_structure()
    for table in engine1.ALL_TABLES:
        assert live[table] == expected[table], table


def test_engine1_and_engine2_table_names_are_disjoint():
    """One transaction-local search_path names both engine schemas (ENGINE_SEARCH_PATH)."""
    from baec_app.engine2 import persistence
    assert not set(engine1.ALL_TABLES) & set(persistence.TABLES)
    assert ENGINE_SEARCH_PATH.split(", ").index("pg_temp") == 3


# --- tenancy -----------------------------------------------------------------------------------------------------------


def _seed_account(storage, organization_id: str, workspace: str = "local") -> None:
    with postgres_engine1(storage, organization_id, workspace) as connection:
        repo = Repository(connection)
        repo.add_account(Account("ACC-1", "Harbor (synthetic)"))
        repo.add_interaction(SourceInteraction("INT-1", "ACC-1", NOW, INTERACTION_TEXTS["INT-1"]))
        repo.save_confirmed_baec(confirmed_record())


@pytest.fixture
def two_tenants(storage):
    a, b = fresh("org-e1a"), fresh("org-e1b")
    for org in (a, b):
        provision(storage, org, fresh("user"), f"User {org} (synthetic)")
    _seed_account(storage, a)
    return a, b


@pytest.mark.parametrize("role", ["app", "probe"])
def test_another_organization_sees_no_engine1_row(storage, two_tenants, migrated, engine1_roles, role):
    a, b = two_tenants
    with psycopg.connect(migrated["admin"], autocommit=True) as raw:
        raw.execute("BEGIN")
        raw.execute(f"SET LOCAL ROLE {'baec_app' if role == 'app' else engine1_roles['probe']}")
        raw.execute("SELECT set_config('baec.organization_id', %s, true), set_config('baec.workspace', 'local', true)",
                    (b,))
        for table in engine1.ALL_TABLES:
            assert raw.execute(f"SELECT count(*) FROM baec_engine1.{table}").fetchone()[0] == 0, table
        if role == "probe":  # even with UPDATE and DELETE privileges, another tenant's rows are unreachable
            assert raw.execute("UPDATE baec_engine1.accounts SET name = 'x'").rowcount == 0
            assert raw.execute("DELETE FROM baec_engine1.interactions").rowcount == 0
        raw.execute("ROLLBACK")
    with postgres_engine1(storage, a) as connection:
        assert connection.execute("SELECT count(*) FROM baec_records").fetchone()[0] == 1


def test_another_workspace_of_the_same_organization_sees_nothing(storage, two_tenants):
    a, _ = two_tenants
    with postgres_engine1(storage, a, "synthetic") as connection:
        assert Repository(connection).list_accounts() == ()


def test_no_tenant_context_sees_and_writes_nothing(migrated, two_tenants):
    with psycopg.connect(migrated["app"], autocommit=True) as raw:
        raw.execute("BEGIN")
        assert raw.execute("SELECT count(*) FROM baec_engine1.accounts").fetchone()[0] == 0
        with pytest.raises(psycopg.errors.InsufficientPrivilege):  # no organization: the policy admits no row
            raw.execute("INSERT INTO baec_engine1.accounts (account_id, name) VALUES ('ACC-X', 'x')")
        raw.execute("ROLLBACK")


def test_cross_tenant_references_and_forged_tenancy_are_refused(storage, two_tenants, migrated):
    a, b = two_tenants
    with postgres_engine1(storage, b) as connection:
        repo = Repository(connection)
        # B cannot attach a child to A's account or interaction: to B they do not exist
        assert outcome(lambda: repo.add_interaction(SourceInteraction("INT-2", "ACC-1", NOW, "x")))[1] == \
            "RepositoryNotFoundError"
        refused = outcome(lambda: connection.execute(
            "INSERT INTO interactions (interaction_id, account_id, occurred_at, text) VALUES ('INT-2', 'ACC-1', ?, 'x')",
            (NOW.isoformat(),)))
        assert refused[:2] == ("refused", "StorageRefusal")  # the composite foreign key includes the organization
        # the verbatim guard reads only B's interactions: A's INT-1 is "missing"
        refused = outcome(lambda: connection.execute(
            "INSERT INTO interaction_evidence (interaction_id, provenance, text) VALUES ('INT-1', 'BUYER_FACT', 'Buyer')"))
        assert refused == ("refused", "StorageRefusal", "interaction_evidence text must occur verbatim in its "
                                                        "interaction")
    for column, value in (("organization_id", a), ("workspace", "synthetic")):
        with storage.database.tenant(b, "local") as raw:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):  # row-level security WITH CHECK
                raw.execute(f"INSERT INTO baec_engine1.accounts ({column}, account_id, name) VALUES (%s, 'F', 'f')",
                            (value,))


def test_rowid_ids_are_numbered_per_organization_and_workspace(storage, two_tenants):
    a, b = two_tenants
    _seed_account(storage, b)
    _seed_account(storage, a, "synthetic")
    for org, workspace in ((a, "local"), (b, "local"), (a, "synthetic")):
        with postgres_engine1(storage, org, workspace) as connection:
            assert connection.execute("SELECT authorization_id, evidence_id FROM human_authorizations, "
                                      "interaction_evidence").fetchall() == [(1, 1)]


def test_tenant_context_does_not_outlive_its_transaction_in_the_pool(storage, two_tenants):
    a, _ = two_tenants
    with postgres_engine1(storage, a):
        pass
    for _ in range(storage.database.pool.max_size + 1):
        with storage.database.pool.connection() as connection:
            assert connection.execute("SELECT current_setting('baec.organization_id', true)").fetchone()[0] in (None,
                                                                                                             "")
            assert connection.execute("SELECT count(*) FROM baec_engine1.accounts").fetchone()[0] == 0


# --- privileges --------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("table", engine1.ALL_TABLES)
def test_the_application_role_can_only_read_and_append(storage, two_tenants, table):
    a, _ = two_tenants
    first = engine1.ALL_TABLES and (table, )
    for sql in (f"UPDATE baec_engine1.{table} SET seq = DEFAULT", f"DELETE FROM baec_engine1.{table}",
                f"TRUNCATE baec_engine1.{table}"):
        if table == "accounts" and sql.startswith("UPDATE"):
            sql = "UPDATE baec_engine1.accounts SET name = 'renamed'"
        with storage.database.tenant(a, "local") as raw:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                raw.execute(sql)
    assert first


def test_accounts_state_is_the_only_column_the_application_role_updates(storage, two_tenants):
    a, _ = two_tenants
    with storage.database.tenant(a, "local") as raw:
        assert raw.execute("UPDATE baec_engine1.accounts SET state = state").rowcount == 1
    with storage.database.tenant(a, "local") as raw:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):  # deliberately stricter than SQLite (no phase yet)
            raw.execute("UPDATE baec_engine1.baec_records SET staleness_status = 'STALE'")


def test_truncate_is_refused_even_for_the_owner(migrated, two_tenants):
    with psycopg.connect(migrated["migrator"], autocommit=True) as owner:
        for table in engine1.ALL_TABLES:
            with pytest.raises(psycopg.errors.RestrictViolation, match=f"{table} is append-only"):
                owner.execute(f"TRUNCATE baec_engine1.{table} CASCADE")


# --- the adapter -------------------------------------------------------------------------------------------------------


def test_only_the_closed_translation_set_is_accepted(storage, two_tenants):
    a, _ = two_tenants
    with postgres_engine1(storage, a) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (engine1.SCHEMA_VERSION,)
        assert connection.execute("PRAGMA foreign_keys").fetchone() == (1,)
        assert connection.execute("PRAGMA query_only").fetchone() == (0,)
        for sql in ("PRAGMA journal_mode", "DELETE FROM accounts", "DROP TABLE accounts", "VACUUM",
                    "ATTACH DATABASE 'x' AS y", "CREATE TABLE z (a int)", "", "REPLACE INTO accounts VALUES (1)"):
            with pytest.raises(Engine1StatementRefused):
                connection.execute(sql)
        with pytest.raises(Engine1StatementRefused):
            connection.execute("COMMIT")  # no write block is open
        assert connection.execute("SELECT state IS ? FROM accounts", (None,)).fetchone() == (True,)
        assert connection.execute("SELECT account_id FROM accounts WHERE state IS NOT ?", (None,)).fetchall() == []
        assert connection.execute("SELECT account_id FROM accounts ORDER BY rowid").fetchall() == [("ACC-1",)]


def test_text_with_u0000_is_refused_before_it_reaches_postgresql(storage, two_tenants):
    a, _ = two_tenants
    with postgres_engine1(storage, a) as connection:
        result = outcome(lambda: Repository(connection).add_account(Account("ACC-NUL", "nul\x00name")))
        assert result == ("refused", "RepositoryConflictError",
                          "the database refused the write: PostgreSQL text cannot contain U+0000")


def test_a_refused_statement_leaves_the_write_block_usable_as_sqlite_does(storage, two_tenants):
    a, _ = two_tenants
    with postgres_engine1(storage, a) as connection:
        with engine1.transaction(connection):
            with pytest.raises(StorageRefusal, match="interactions is append-only|rows cannot be replaced"):
                connection.execute("INSERT INTO interactions (interaction_id, account_id, occurred_at, text) "
                                   "VALUES ('INT-1', 'ACC-1', 'x', 'x')")
            connection.execute("INSERT INTO accounts (account_id, name) VALUES ('ACC-OK', 'still usable')")
        assert connection.execute("SELECT count(*) FROM accounts").fetchone() == (2,)


def test_the_read_only_block_is_enforced_by_postgresql(storage, two_tenants):
    a, _ = two_tenants
    with postgres_engine1(storage, a) as connection:
        with connection.read_only() as reader:
            assert reader.execute("PRAGMA query_only").fetchone() == (1,)
            with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
                reader.execute("INSERT INTO accounts (account_id, name) VALUES ('ACC-RO', 'x')")
            with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):  # even bypassing the adapter
                reader.db.execute("INSERT INTO baec_engine1.accounts (account_id, name) VALUES ('ACC-RO', 'x')")
        assert connection.execute("PRAGMA query_only").fetchone() == (0,)
        connection.execute("INSERT INTO accounts (account_id, name) VALUES ('ACC-RW', 'writable again')")
        with engine1.transaction(connection):
            with pytest.raises(Engine1StatementRefused):
                with connection.read_only():
                    pass


def test_the_handoff_reader_requires_and_gets_a_query_only_connection(storage, two_tenants):
    from baec_app.engine2.integration.engine1_handoff import read_confirmed_baec
    a, _ = two_tenants
    with postgres_engine1(storage, a) as connection:
        with connection.read_only() as reader:
            assert read_confirmed_baec(reader, "B-1").baec_id == "B-1"
        with pytest.raises(Exception, match="query"):
            read_confirmed_baec(connection, "B-1")


# --- concurrency -------------------------------------------------------------------------------------------------------


def test_concurrent_transitions_of_one_account_serialize_like_one_sqlite_file(storage, two_tenants):
    """Two writers race the same transition. As with BEGIN IMMEDIATE, the second waits, then sees the first's state:
    exactly one transition is stored, and the other gets the state machine's own answer (same state)."""
    a, _ = two_tenants
    with postgres_engine1(storage, a) as connection:
        judgment_id = Repository(connection).record_dormancy_judgment(judgment())
    barrier, results = threading.Barrier(2), []

    def race():
        with postgres_engine1(storage, a) as connection:
            barrier.wait()
            results.append(Repository(connection).persist_transition_to_conditionally_dormant(
                "ACC-1", baec_id="B-1", judgment_id=judgment_id, authorization=state_auth(CD), recorded_at=LATER))
    threads = [threading.Thread(target=race) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert sorted(r.allowed for r in results) == [False, True]
    with postgres_engine1(storage, a) as connection:
        assert len(Repository(connection).get_transition_history("ACC-1")) == 1


def test_concurrent_rowid_assignment_gives_distinct_consecutive_ids(storage, two_tenants):
    a, _ = two_tenants
    barrier = threading.Barrier(4)

    def write(n):
        with postgres_engine1(storage, a) as connection:
            barrier.wait()
            with engine1.transaction(connection):
                connection.execute("INSERT INTO human_authorizations (authorized_by, authorized_at, action, subject_id) "
                                   "VALUES (?, ?, 'CONFIRM_BAEC', ?)", (f"writer {n}", NOW.isoformat(), f"B-{n}"))
    threads = [threading.Thread(target=write, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    with postgres_engine1(storage, a) as connection:
        ids = [r[0] for r in connection.execute("SELECT authorization_id FROM human_authorizations ORDER BY 1")]
    assert ids == [1, 2, 3, 4, 5]


# --- julianday ---------------------------------------------------------------------------------------------------------


def test_julianday_is_sqlites_for_every_iso_timestamp(migrated):
    """baec_engine1.julianday returns exactly the double SQLite's julianday() returns (the TTL CHECK and the grant
    window use it). Edge forms, invalid dates SQLite accepts, sub-millisecond rounding, the .999 clamp, and 2,000
    random timestamps; text outside the ISO form is NULL on both."""
    rng = random.Random(3)
    cases = ["2026-09-01T09:00:00.000499+00:00", "2026-09-01T09:00:00.000500+00:00", "2026-09-01T09:00:59.999999Z",
             "2026-02-30T00:00:00+00:00", "2026-09-01 09:00", "2026-09-01", "2026-09-01T", "2026-09-0109:00",
             "2026-09-01T24:59", "2026-09-01T09:00:00-14:59", "2026-09-01T09:00:00+15:00", "2026-13-01",
             "2026-09-32", "2026-09-01T09:60", "2026-09-01Z", "garbage", "", "2026-09-01T09:00:00.",
             "2026-09-01T09:00:00.12345678901234567890+05:30"]
    for _ in range(2000):
        cases.append(f"{rng.randint(1, 9999):04d}-{rng.randint(1, 12):02d}-{rng.randint(1, 31):02d}T"
                     f"{rng.randint(0, 23):02d}:{rng.randint(0, 59):02d}:{rng.randint(0, 59):02d}"
                     f"{rng.choice(['', '.' + str(rng.randint(0, 999999)).zfill(6), '.5'])}"
                     f"{rng.choice(['', 'Z', '+00:00', '-05:00', '+05:30'])}")
    with closing(sqlite3.connect(":memory:")) as sqlite, psycopg.connect(migrated["admin"]) as pg:
        for text in cases:
            assert sqlite.execute("SELECT julianday(?)", (text,)).fetchone()[0] == pg.execute(
                "SELECT baec_engine1.julianday(%s)", (text,)).fetchone()[0], text


def test_grant_ttl_check_uses_sqlites_arithmetic(migrated):
    issued = datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc)
    with psycopg.connect(migrated["admin"]) as pg, closing(sqlite3.connect(":memory:")) as sqlite:
        for delta in (timedelta(seconds=900), timedelta(seconds=900, microseconds=400),
                      timedelta(seconds=900, milliseconds=1), timedelta(seconds=899)):
            expires = (issued + delta).isoformat(timespec="microseconds")
            sql = "SELECT COALESCE(abs((julianday({e}) - julianday({i})) * 86400.0 - 900.0) < 0.001, 0)"
            expected = bool(sqlite.execute(sql.format(e="?", i="?"), (expires, issued.isoformat())).fetchone()[0])
            got = pg.execute(sql.replace("julianday", "baec_engine1.julianday").replace(", 0)", ", false)").format(
                e="%s", i="%s"), (expires, issued.isoformat())).fetchone()[0]
            assert got is expected, delta
