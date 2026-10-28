"""V0.4 Phase 3: the database itself isolates organizations. ConsoleService is never used here.

Every test acts directly on PostgreSQL, as tenant A against tenant B and the reverse, with raw SQL that applies no
organization filter. Three layers are shown separately:
  * row-level security: the `probe` role holds every DML privilege and fires no triggers or foreign keys
    (session_replication_role = replica), so only RLS stands between it and the other organization's rows;
  * privileges: the real application role has no DELETE, TRUNCATE, or organization_id UPDATE privilege;
  * composite foreign keys: a row can only reference rows of its own organization.
All data is synthetic. These are software tests of the storage boundary, not a production security assessment.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from baec_console.console_store import CONSOLE_GENERATOR_ORIGIN
from baec_console.crm_store import CRMStoreError
from baec_console.identity import IdentityError, Principal
from baec_console.postgres.gate import TENANT_TABLES

from .conftest import AT, fresh, provision, psycopg

STATEMENT = "We would revisit this only if the renewal price rises above 8% (synthetic)."


@dataclass(frozen=True)
class Tenant:
    organization_id: str
    user_id: str
    account_id: str
    interaction_id: str
    contact_id: str
    task_id: str
    activity_id: str
    request_id: str
    preparation_id: str


def populate(storage, label: str) -> Tenant:
    """One row in every tenant table, written through the stores as the organization itself."""
    org, user, name = fresh(f"org-{label}"), fresh(f"user-{label}"), f"Seller {label} (synthetic)"
    provision(storage, org, user, name)
    t = Tenant(org, user, fresh("ACC"), fresh("INT"), fresh("CON"), fresh("TASK"), fresh("ACT"), fresh("REQ"),
               fresh("PREP"))
    with storage.session(org, "local") as s:
        c, crm = s.console, s.crm
        s.identity.rename_user(user, name + " renamed", at=AT)
        c.add_ownership(t.account_id, org, AT, user)
        c.add_account_meta(t.account_id, f"Account {label} (synthetic)", None, None, False, AT, name)
        c.add_interaction_meta(t.interaction_id, t.account_id, "Buyer", STATEMENT, None, AT, name)
        draft = fresh("DRAFT")
        c.add_draft(draft, t.account_id, t.interaction_id, {"statement": STATEMENT}, AT, name)
        c.resolve_draft(draft, "DISCARDED", None, AT, name)
        c.add_preparation_request(t.request_id, t.account_id, "BAEC-1", "REV-1", "CAND-1", {"goal": "x"}, {"s": 1},
                                  "0" * 64, AT, name)
        c.add_preparation(t.preparation_id, t.request_id, CONSOLE_GENERATOR_ORIGIN, [{"artifact_id": "ART-1"}], AT,
                          "Deterministic preparation")
        edit = fresh("EDIT")
        c.add_edit(edit, t.preparation_id, "ART-1", {"artifact_id": "ART-2"}, AT, name)
        c.add_review(t.preparation_id, "ART-1", "EDIT", edit, {"supported": True}, None, AT, name)
        c.add_block_decision(t.account_id, "REV-1", "BLOCK", "synthetic", AT, name)
        c.add_event(t.account_id, "ACCOUNT_CREATED", "Human", name, AT, "Account created")
        c.add_attribution("console.account", t.account_id, "CREATED", t.account_id, user, org, name, AT)
        crm.add_contact(t.contact_id, t.account_id, name="Pat Lee (synthetic)", title="", email="", phone="",
                        notes="", created_at=AT, created_by=name)
        crm.add_task(t.task_id, t.account_id, contact_id=t.contact_id, title="Follow up", due_date=None,
                     priority="NORMAL", notes="", created_at=AT, created_by=name)
        crm.add_activity(t.activity_id, t.account_id, contact_id=t.contact_id, interaction_type="CALL",
                         occurred_at=AT, raw_notes="Synthetic call notes", summary="", buyer_role="",
                         buyer_statement=STATEMENT, seller_notes="", ai_assisted=False, created_at=AT,
                         created_by=name)
        crm.link_interaction(t.interaction_id, t.account_id, contact_id=t.contact_id, interaction_type="CALL",
                             linked_at=AT)
    return t


@pytest.fixture(scope="module")
def tenants(database) -> dict[str, Tenant]:
    from baec_console.postgres.stores import PostgresProductStorage
    storage = PostgresProductStorage(database)
    return {"A": populate(storage, "a"), "B": populate(storage, "b")}


DIRECTIONS = [("A", "B"), ("B", "A")]


@pytest.fixture(scope="module")
def probe_role(migrated):
    """A role with every DML privilege on every tenant table that is still subject to RLS (NOBYPASSRLS, not an
    owner). The test superuser switches to it with SET ROLE; it cannot log in."""
    with psycopg.connect(migrated["admin"], autocommit=True) as admin:
        admin.execute("DROP ROLE IF EXISTS baec_rls_probe")
        admin.execute("CREATE ROLE baec_rls_probe NOLOGIN NOSUPERUSER NOBYPASSRLS")
        admin.execute("GRANT USAGE ON SCHEMA baec TO baec_rls_probe")
        admin.execute("GRANT EXECUTE ON FUNCTION baec.current_organization() TO baec_rls_probe")
        for table in TENANT_TABLES:
            admin.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON baec.{table} TO baec_rls_probe")
    yield "baec_rls_probe"
    with psycopg.connect(migrated["admin"], autocommit=True) as admin:
        admin.execute("DROP OWNED BY baec_rls_probe")
        admin.execute("DROP ROLE baec_rls_probe")


@pytest.fixture
def probe(migrated, probe_role):
    """A probe connection with triggers and foreign keys off, so row-level security is the only check left."""
    with psycopg.connect(migrated["admin"], autocommit=True) as connection:
        connection.execute("SET session_replication_role = replica")  # set as superuser, before dropping privileges
        connection.execute(f"SET ROLE {probe_role}")
        assert connection.execute("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = current_user"
                                  ).fetchone() == (False,)
        yield connection


def _as(connection, organization_id: str | None):
    """Open a transaction on a raw connection and act for `organization_id` (None: act for no one)."""
    connection.execute("BEGIN")
    if organization_id is not None:
        connection.execute("SELECT set_config('baec.organization_id', %s, true)", (organization_id,))


def _rollback(connection) -> None:
    connection.execute("ROLLBACK")


# --- SELECT -----------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("me,other", DIRECTIONS)
def test_an_unfiltered_select_sees_only_the_current_organization(app, tenants, me, other):
    mine, theirs = tenants[me], tenants[other]
    _as(app, mine.organization_id)
    try:
        for table in TENANT_TABLES:
            orgs = {r[0] for r in app.execute(f"SELECT organization_id FROM baec.{table}").fetchall()}
            assert orgs == {mine.organization_id}, table  # this organization's rows exist, and nothing else
            assert app.execute(f"SELECT count(*) FROM baec.{table} WHERE organization_id = %s",
                               (theirs.organization_id,)).fetchone() == (0,), table
        assert app.execute("SELECT count(*) FROM baec.interaction_meta WHERE buyer_statement = %s",
                           (STATEMENT,)).fetchone() == (1,)  # identical text, other tenant's row still invisible
        assert app.execute("SELECT * FROM baec.users WHERE user_id = %s", (theirs.user_id,)).fetchall() == []
        assert app.execute("SELECT * FROM baec.crm_tasks WHERE task_id = %s", (theirs.task_id,)).fetchall() == []
    finally:
        _rollback(app)


@pytest.mark.parametrize("organization_id", [None, "", "x' OR '1'='1", "% OR true"])
def test_without_a_valid_organization_nothing_is_visible_or_writable(app, tenants, organization_id):
    _as(app, organization_id)
    try:
        for table in TENANT_TABLES:
            assert app.execute(f"SELECT count(*) FROM baec.{table}").fetchone() == (0,), table
        app.execute("SAVEPOINT s")
        with pytest.raises(psycopg.errors.InsufficientPrivilege, match="row-level security"):
            app.execute("INSERT INTO baec.organizations (organization_id, name, created_at) VALUES (%s, 'x', now())",
                        (fresh("org-not-current"),))
        app.execute("ROLLBACK TO SAVEPOINT s")
    finally:
        _rollback(app)


# --- INSERT -----------------------------------------------------------------------------------------------------------


def _copy_columns(connection, table: str) -> list[str]:
    """Every column of `table` an INSERT may supply (GENERATED ALWAYS identity columns excluded)."""
    return [r[0] for r in connection.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_schema = 'baec' AND table_name = %s "
        "AND NOT (is_identity = 'YES' AND identity_generation = 'ALWAYS') ORDER BY ordinal_position",
        (table,)).fetchall()]


@pytest.mark.parametrize("me,other", DIRECTIONS)
def test_no_row_can_be_inserted_for_another_organization(app, tenants, me, other):
    mine, theirs = tenants[me], tenants[other]
    _as(app, mine.organization_id)
    try:
        for table in TENANT_TABLES:
            columns = _copy_columns(app, table)
            selected = ", ".join("%s" if c == "organization_id" else c for c in columns)
            app.execute("SAVEPOINT s")
            with pytest.raises(psycopg.errors.InsufficientPrivilege, match="row-level security"):
                app.execute(f"INSERT INTO baec.{table} ({', '.join(columns)}) SELECT {selected} FROM baec.{table} "
                            f"LIMIT 1", (theirs.organization_id,))
            app.execute("ROLLBACK TO SAVEPOINT s")
    finally:
        _rollback(app)


@pytest.mark.parametrize("me,other", DIRECTIONS)
def test_no_relationship_can_point_into_another_organization(app, tenants, me, other):
    mine, theirs = tenants[me], tenants[other]
    org = mine.organization_id
    attempts = {
        "contact on their account": ("INSERT INTO baec.crm_contacts (organization_id, workspace, contact_id, "
                                     "account_id, name, created_at, created_by) VALUES (%s, 'local', 'C-X', %s, 'x', "
                                     "now(), 'x')", (org, theirs.account_id)),
        "task with their contact": ("INSERT INTO baec.crm_tasks (organization_id, workspace, task_id, account_id, "
                                    "contact_id, title, created_at, created_by) VALUES (%s, 'local', 'T-X', %s, %s, "
                                    "'x', now(), 'x')", (org, mine.account_id, theirs.contact_id)),
        "activity with their contact": ("INSERT INTO baec.crm_activities (organization_id, workspace, activity_id, "
                                        "account_id, contact_id, interaction_type, occurred_at, raw_notes, created_at, "
                                        "created_by) VALUES (%s, 'local', 'A-X', %s, %s, 'CALL', now(), 'x', now(), "
                                        "'x')", (org, mine.account_id, theirs.contact_id)),
        "attribution to their user": ("INSERT INTO baec.attributions (organization_id, workspace, record_type, "
                                      "record_id, action, account_id, user_id, display_name_snapshot, recorded_at, "
                                      "basis) VALUES (%s, 'local', 'x', 'x', 'x', %s, %s, 'x', now(), 'RECORDED')",
                                      (org, mine.account_id, theirs.user_id)),
        "ownership recorded by their user": ("INSERT INTO baec.account_ownership (organization_id, workspace, "
                                             "account_id, recorded_at, recorded_by_user_id, basis) VALUES (%s, "
                                             "'local', 'ACC-X', now(), %s, 'CREATED')", (org, theirs.user_id)),
        "audit of their user": ("INSERT INTO baec.identity_audit (organization_id, occurred_at, event_type, user_id, "
                                "detail) VALUES (%s, now(), 'USER_RENAMED', %s, 'x')", (org, theirs.user_id)),
        "preparation on their request": ("INSERT INTO baec.preparations (organization_id, workspace, preparation_id, "
                                         "request_id, generator, artifacts_json, generated_at, generated_by) VALUES "
                                         "(%s, 'local', 'P-X', %s, 'x', '[]', now(), 'x')", (org, theirs.request_id)),
        "event on their account": ("INSERT INTO baec.events (organization_id, workspace, account_id, event_type, "
                                   "actor_kind, actor, occurred_at, summary) VALUES (%s, 'local', %s, 'x', 'Human', "
                                   "'x', now(), 'x')", (org, theirs.account_id)),
    }
    _as(app, org)
    try:
        for label, (sql, args) in attempts.items():
            app.execute("SAVEPOINT s")
            with pytest.raises(psycopg.errors.ForeignKeyViolation):
                app.execute(sql, args)
            app.execute("ROLLBACK TO SAVEPOINT s")
            # The same reference to an id that exists nowhere fails identically: the error reveals nothing.
            app.execute("SAVEPOINT s")
            with pytest.raises(psycopg.errors.ForeignKeyViolation):
                app.execute(sql, tuple("NO-SUCH-ID" if a not in (org,) else a for a in args))
            app.execute("ROLLBACK TO SAVEPOINT s")
    finally:
        _rollback(app)


# --- UPDATE -----------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("me,other", DIRECTIONS)
def test_the_application_role_cannot_update_another_organizations_rows(app, storage, tenants, me, other):
    mine, theirs = tenants[me], tenants[other]
    _as(app, mine.organization_id)
    try:
        for sql, args, touched in (
                ("UPDATE baec.crm_tasks SET status = 'DONE', completed_at = now(), completed_by = 'intruder' "
                 "WHERE task_id = %s", (theirs.task_id,), 0),
                ("UPDATE baec.crm_activities SET engine1_interaction_id = 'INT-X' WHERE activity_id = %s",
                 (theirs.activity_id,), 0),
                ("UPDATE baec.users SET display_name = 'Intruder', active = false WHERE user_id = %s",
                 (theirs.user_id,), 0),
                # No WHERE clause at all: only this organization's own task is reached (rolled back below).
                ("UPDATE baec.crm_tasks SET status = 'DONE', completed_at = now(), completed_by = 'intruder'", (), 1),
        ):
            app.execute("SAVEPOINT s")
            assert app.execute(sql, args).rowcount == touched, sql
            app.execute("ROLLBACK TO SAVEPOINT s")
    finally:
        _rollback(app)
    with storage.session(theirs.organization_id, "local") as s:
        assert s.crm.tasks(account_id=theirs.account_id)[0]["status"] == "OPEN"
        assert s.crm.activities(theirs.account_id)[0]["engine1_interaction_id"] is None
        assert s.identity.user(theirs.user_id).active


@pytest.mark.parametrize("me,other", DIRECTIONS)
def test_rls_alone_stops_updates_to_other_rows_and_moves_into_another_organization(probe, tenants, me, other):
    mine, theirs = tenants[me], tenants[other]
    _as(probe, mine.organization_id)
    try:
        for table in TENANT_TABLES:
            cursor = probe.execute(f"UPDATE baec.{table} SET organization_id = organization_id "
                                   f"WHERE organization_id = %s", (theirs.organization_id,))
            assert cursor.rowcount == 0, table
            probe.execute("SAVEPOINT s")
            with pytest.raises(psycopg.errors.InsufficientPrivilege, match="row-level security"):
                probe.execute(f"UPDATE baec.{table} SET organization_id = %s", (theirs.organization_id,))
            probe.execute("ROLLBACK TO SAVEPOINT s")
    finally:
        _rollback(probe)


# --- DELETE and TRUNCATE ----------------------------------------------------------------------------------------------


@pytest.mark.parametrize("me,other", DIRECTIONS)
def test_rls_alone_stops_deletes_of_another_organizations_rows(probe, tenants, me, other):
    mine, theirs = tenants[me], tenants[other]
    _as(probe, mine.organization_id)
    try:
        for table in TENANT_TABLES:
            assert probe.execute(f"DELETE FROM baec.{table} WHERE organization_id = %s",
                                 (theirs.organization_id,)).rowcount == 0, table
        # With the same privileges, the probe's own rows are deletable: RLS, not a missing grant, stopped it above.
        # (Rolled back below; the application role has no DELETE privilege at all.)
        assert probe.execute("DELETE FROM baec.crm_interaction_links").rowcount == 1
    finally:
        _rollback(probe)


@pytest.mark.parametrize("statement", ["DELETE FROM baec.{t}", "TRUNCATE baec.{t}",
                                       "UPDATE baec.{t} SET organization_id = organization_id"])
def test_the_application_role_has_no_delete_truncate_or_organization_update_privilege(app, tenants, statement):
    _as(app, tenants["A"].organization_id)
    try:
        for table in TENANT_TABLES:
            app.execute("SAVEPOINT s")
            with pytest.raises(psycopg.errors.InsufficientPrivilege, match="permission denied"):
                app.execute(statement.format(t=table))
            app.execute("ROLLBACK TO SAVEPOINT s")
    finally:
        _rollback(app)


def test_append_only_triggers_refuse_changes_to_the_organizations_own_rows(migrated, probe_role, tenants):
    """With triggers on, even the probe's own rows refuse UPDATE and DELETE where a store never changes them."""
    with psycopg.connect(migrated["admin"], autocommit=True) as connection:
        connection.execute(f"SET ROLE {probe_role}")
        _as(connection, tenants["A"].organization_id)
        try:
            for table in ("events", "attributions", "interaction_meta", "preparations", "crm_contacts",
                          "account_ownership", "identity_audit", "organizations"):
                for sql in (f"UPDATE baec.{table} SET organization_id = organization_id", f"DELETE FROM baec.{table}"):
                    connection.execute("SAVEPOINT s")
                    with pytest.raises(psycopg.errors.RestrictViolation, match="append-only"):
                        connection.execute(sql)
                    connection.execute("ROLLBACK TO SAVEPOINT s")
        finally:
            _rollback(connection)


# --- through the stores, with their filters --------------------------------------------------------------------------


@pytest.mark.parametrize("me,other", DIRECTIONS)
def test_the_stores_cannot_reach_another_organizations_records(storage, tenants, me, other):
    mine, theirs = tenants[me], tenants[other]
    with storage.session(mine.organization_id, "local") as s:
        assert s.console.account_meta(theirs.account_id) is None
        assert s.console.interaction_meta(theirs.interaction_id) is None
        assert s.console.organization_of(theirs.account_id) is None
        assert s.console.owning_organizations() == {mine.organization_id}
        assert s.console.events(theirs.account_id) == []
        assert s.console.attributions(theirs.account_id) == []
        assert s.crm.contacts(theirs.account_id) == []
        assert s.crm.account_of("tasks", theirs.task_id) is None
        assert s.crm.interaction_link(theirs.interaction_id) is None
        with pytest.raises(CRMStoreError, match="does not exist"):
            s.crm.complete_task(theirs.task_id, completed_at=AT, completed_by="intruder")
        with pytest.raises(CRMStoreError, match="does not exist"):
            s.crm.attach_engine1_interaction(theirs.activity_id, "INT-X")
        assert s.identity.user(theirs.user_id) is None
        with pytest.raises(IdentityError):
            s.identity.verify(Principal(theirs.user_id, theirs.organization_id, "x"))
        with pytest.raises(IdentityError):
            s.identity.rename_user(theirs.user_id, "Intruder", at=AT)
        with pytest.raises(psycopg.errors.InsufficientPrivilege, match="row-level security"):
            with s.connection.transaction():
                s.console.add_ownership("ACC-STOLEN", theirs.organization_id, AT, theirs.user_id)
