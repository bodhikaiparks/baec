"""V0.4 Phase 3: the PostgreSQL stores behave like the SQLite stores they mirror.

One scenario runs against each backend through the same store methods: identity, console, and CRM creates, reads,
lists, the updates the stores allow, and the writes they refuse. The outcomes are compared after replacing database-
assigned integers (event and block-decision ids, insertion sequence numbers) with their order, which is what the
console relies on. Refusals compare by kind: a store error compares by its type and message; a database refusal
compares as "refused by the database" (SQLite raises it from a trigger or constraint, PostgreSQL from a trigger,
constraint, or missing privilege). Differences that are deliberate are tested separately at the end.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta

import pytest

from baec_console import console_store, crm_store, identity
from baec_console.console_store import CONSOLE_GENERATOR_ORIGIN, ConsoleStoreError
from baec_console.crm_store import CRMStoreError
from baec_console.identity import IdentityError, Principal, UserKind

from .conftest import AT, fresh, psycopg

NAIVE = datetime(2026, 9, 5, 9, 0)
STATEMENT = "We would revisit this only if the renewal price rises above 8% — before 1 March (synthetic). ✓"


class SQLiteBackend:
    def __init__(self, directory) -> None:
        self.directory = directory

    @contextmanager
    def session(self, organization_id: str):
        connections = [identity.open_identity_store(self.directory / "identity.sqlite3"),
                       console_store.open_console_store(str(self.directory / "console.sqlite3")),
                       crm_store.open_crm_store(self.directory / "crm.sqlite3")]
        try:
            yield (identity.IdentityStore(connections[0]), console_store.ConsoleStore(connections[1]),
                   crm_store.CRMStore(connections[2]), connections)
        finally:
            for c in connections:
                c.close()

    def raw(self, stores, which: str, sql: str) -> None:
        db = {"identity": stores[3][0], "console": stores[3][1], "crm": stores[3][2]}[which]
        db.execute(sql.replace("baec.", "").replace("crm_contacts", "contacts").replace("crm_tasks", "tasks"))

    @contextmanager
    def guarded(self, stores):
        """Each action in a transaction on every store, as the console service runs them (the stores' multi-row
        writes, such as a preparation with its provenance, expect one)."""
        connections = stores[3]
        for c in connections:
            c.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            for c in connections:
                c.execute("ROLLBACK")
            raise
        for c in connections:
            c.execute("COMMIT")


class PostgresBackend:
    def __init__(self, storage) -> None:
        self.storage = storage

    @contextmanager
    def session(self, organization_id: str):
        with self.storage.session(organization_id, "local") as s:
            yield (s.identity, s.console, s.crm, s.connection)

    def raw(self, stores, which: str, sql: str) -> None:
        stores[3].execute(sql)

    @contextmanager
    def guarded(self, stores):
        """A savepoint, so a refused statement does not abort the rest of the scenario (SQLite needs none)."""
        with stores[3].transaction():
            yield


def outcome(backend, stores, action):
    try:
        with backend.guarded(stores):
            return ("ok", action())
    except (IdentityError, ConsoleStoreError, CRMStoreError) as error:
        return (type(error).__name__, str(error))
    except (sqlite3.IntegrityError, psycopg.errors.IntegrityError, psycopg.errors.InsufficientPrivilege):
        return ("refused by the database",)


def ordinal(items: list[dict], *keys: str) -> list[dict]:
    """Replace database-assigned integers with their rank, keeping everything else."""
    out = [dict(i) for i in items]
    for key in keys:
        ranks = {v: n for n, v in enumerate(sorted({i[key] for i in out}))}
        for i in out:
            i[key] = ranks[i[key]]
    return out


def scenario(backend, organization_id: str) -> list:
    log = []
    user, other = f"user-{organization_id}", f"user-2-{organization_id}"  # user ids are unique across organizations
    t = [AT + timedelta(minutes=n) for n in range(40)]
    with backend.session(organization_id) as stores:
        ident, cs, crm = stores[:3]
        run = lambda action: log.append(outcome(backend, stores, action))  # noqa: E731

        # identity
        run(lambda: ident.ensure_organization(organization_id, " Parity org (synthetic) ", t[0]))
        run(lambda: ident.ensure_organization(organization_id, "Another name", t[1]))  # idempotent, unchanged
        run(lambda: ident.ensure_user(user, organization_id, " Avery (synthetic) ", t[2], kind=UserKind.HUMAN))
        run(lambda: ident.ensure_user(other, organization_id, "Local (synthetic)", t[3],
                                      kind=UserKind.LOCAL_DEVELOPMENT))
        run(lambda: ident.ensure_user("user-dup", organization_id, "Avery (synthetic)", t[4], kind=UserKind.HUMAN))
        run(lambda: ident.ensure_user(user, organization_id, "x", t[4], kind=UserKind.SYSTEM_SEED))
        run(lambda: ident.ensure_user("user-x", organization_id, "x", t[4], kind="HUMAN"))
        run(lambda: ident.ensure_user("user-x", "no-such-org", "x", t[4], kind=UserKind.HUMAN))
        run(lambda: ident.ensure_user("user-naive", organization_id, "Naive", NAIVE, kind=UserKind.HUMAN))
        run(lambda: ident.rename_user(user, "Avery Jones (synthetic)", at=t[5]))
        run(lambda: ident.rename_user(user, "Local (synthetic)", at=t[6]))  # taken by the other user
        run(lambda: ident.rename_user(user, "   ", at=t[6]))
        run(lambda: ident.set_active(other, False, at=t[7]))
        run(lambda: ident.set_active(other, False, at=t[7]))  # no change, no audit
        run(lambda: ident.authenticated_principal(user))
        run(lambda: ident.authenticated_principal(other))
        run(lambda: ident.principal(other))  # inactive
        run(lambda: ident.set_active(other, True, at=t[8]))
        run(lambda: ident.verify(Principal(user, organization_id, "stale label")))
        run(lambda: ident.verify(Principal(user, "another-org", "x")))
        run(lambda: ident.verify(Principal(other, organization_id, "x"), require_authentication_eligible=True))
        run(lambda: backend.raw(stores, "identity", f"UPDATE baec.users SET kind = 'HUMAN' WHERE user_id = '{other}'"))
        run(lambda: backend.raw(stores, "identity", f"DELETE FROM baec.users WHERE user_id = '{other}'"))
        log.append(("users", ident.users(organization_id)))
        log.append(("organization", ident.organization(organization_id)))
        log.append(("audit", [(e.event_type, e.user_id, e.detail, e.occurred_at) for e in ident.audit()]))

        # console store
        acc, acc2 = "ACC-PARITY-1", "ACC-PARITY-2"
        run(lambda: cs.add_ownership(acc, organization_id, t[9], user))
        run(lambda: cs.add_ownership(acc2, organization_id, t[9], user, "V03_MIGRATION"))
        run(lambda: cs.add_ownership(acc, organization_id, t[9], user))  # duplicate
        run(lambda: cs.add_ownership("ACC-BAD", organization_id, t[9], user, "ADOPTED"))
        run(lambda: cs.add_account_meta(acc, "Harbor Point (synthetic)", "Diagnostics", None, False, t[10], "Avery"))
        run(lambda: cs.add_account_meta(acc2, "Second (synthetic)", None, "note", True, t[10], "Avery"))
        run(lambda: cs.add_account_meta(acc2, "Second again", None, None, True, t[10], "Avery"))
        run(lambda: cs.add_account_meta("ACC-N", "Naive", None, None, False, NAIVE, "Avery"))
        run(lambda: cs.add_interaction_meta("INT-1", acc, "VP Operations", STATEMENT, "notes", t[11], "Avery"))
        run(lambda: cs.add_draft("DRAFT-1", acc, "INT-1", {"threshold": "8%", "statement": STATEMENT, "n": 1.5},
                                 t[12], "Avery"))
        run(lambda: cs.add_draft("DRAFT-2", acc, "INT-1", {"b": [1, 2]}, t[13], "Avery"))
        run(lambda: cs.resolve_draft("DRAFT-1", "CONFIRMED", "BAEC-1", t[14], "Avery"))
        run(lambda: cs.resolve_draft("DRAFT-1", "DISCARDED", None, t[15], "Avery"))  # resolved once only
        run(lambda: cs.resolve_draft("DRAFT-2", "SHELVED", None, t[15], "Avery"))
        run(lambda: cs.draft("DRAFT-404"))
        for n in (1, 2):
            run(lambda n=n: cs.add_preparation_request(f"REQ-{n}", acc, "BAEC-1", f"REV-{n}", f"CAND-{n}",
                                                       {"goal": "ASK", "n": n}, {"snapshot": STATEMENT}, "a" * 64,
                                                       t[15 + n], "Avery"))
        run(lambda: cs.add_preparation("PREP-1", "REQ-1", CONSOLE_GENERATOR_ORIGIN,
                                       [{"artifact_id": "ART-1", "text": STATEMENT}], t[18], "Deterministic preparation"))
        run(lambda: cs.add_preparation("PREP-X", "REQ-2", "SOME_MODEL", [], t[18], "x"))  # provenance CHECK
        run(lambda: cs.add_preparation("PREP-Y", "REQ-404", CONSOLE_GENERATOR_ORIGIN, [], t[18], "x"))
        run(lambda: cs.add_edit("EDIT-1", "PREP-1", "ART-1", {"artifact_id": "ART-2"}, t[19], "Avery"))
        run(lambda: cs.add_review("PREP-1", "ART-1", "EDIT", "EDIT-1", {"supported": True}, None, t[20], "Avery"))
        run(lambda: cs.add_review("PREP-1", "ART-1", "REJECT", None, {}, "again", t[21], "Avery"))  # once per artifact
        run(lambda: cs.add_review("PREP-1", "ART-9", "MAYBE", None, {}, None, t[21], "Avery"))
        run(lambda: type(cs.add_block_decision(acc, "REV-1", "BLOCK", "wait", t[22], "Avery")))
        run(lambda: type(cs.add_block_decision(acc, "REV-1", "UNBLOCK", None, t[23], "Avery")))
        run(lambda: type(cs.add_block_decision(acc, "REV-2", "BLOCK", "hold", t[24], "Avery")))
        run(lambda: cs.add_block_decision(acc, "REV-2", "PAUSE", None, t[24], "Avery"))
        for n in range(3):
            run(lambda n=n: type(cs.add_event(acc, "NOTE", "Human", "Avery", t[25 + n], f"event {n}", f"ref-{n}")))
        run(lambda: cs.add_attribution("console.account", acc, "CREATED", acc, user, organization_id, "Avery", t[28]))
        run(lambda: cs.add_attribution("console.event", 1, "RECORDED", acc, user, organization_id, "Avery", t[28]))
        run(lambda: cs.add_attribution("console.account", acc, "CREATED", acc, user, organization_id, "Avery", t[29]))
        run(lambda: cs.add_attribution("x", "y", "z", acc, user, organization_id, "Avery", t[29], "GUESSED"))
        run(lambda: backend.raw(stores, "console", "UPDATE baec.events SET summary = 'changed'"))
        run(lambda: backend.raw(stores, "console", "DELETE FROM baec.preparations"))
        run(lambda: backend.raw(stores, "console", "UPDATE baec.attributions SET user_id = 'someone'"))
        log.append(("account_ids", cs.account_ids()))
        log.append(("account_meta", [cs.account_meta(a) for a in (acc, acc2, "ACC-404")]))
        log.append(("interaction_meta", [cs.interaction_meta(i) for i in ("INT-1", "INT-404")]))
        log.append(("drafts", cs.drafts(), cs.drafts(acc2)))
        log.append(("draft", cs.draft("DRAFT-1")))
        log.append(("requests", ordinal(cs.preparation_requests(), "seq"), [r["request_id"] for r in
                                                                           cs.preparation_requests(acc)]))
        log.append(("preparations", cs.preparations(), cs.preparation("PREP-1"), cs.provenance("PREP-1")))
        log.append(("edits", cs.edits("PREP-1"), cs.reviews("PREP-1"), cs.reviews("PREP-404")))
        log.append(("blocks", [ordinal(cs.block_decisions(r), "decision_id") for r in ("REV-1", "REV-2", "REV-3")],
                    [cs.is_blocked(r) for r in ("REV-1", "REV-2", "REV-3")]))
        log.append(("events", ordinal([e.__dict__ for e in cs.events(acc)], "event_id"), cs.events(acc2)))
        log.append(("ownership", cs.organization_of(acc), cs.organization_of("ACC-404"),
                    cs.owned_account_ids(organization_id), cs.owning_organizations()))
        log.append(("attributions", cs.attributions(), cs.attributions(acc2)))
        log.append(("backfills", cs.ownership_backfill(), cs.attribution_backfill()))

        # CRM
        for n, name in enumerate(("beta Contact", "Alpha Contact", "gamma Contact")):
            run(lambda n=n, name=name: crm.add_contact(f"CON-{n}", acc, name=f" {name} ", title="", email=" a@x.test ",
                                                       phone="", notes="", created_at=t[30 + n], created_by="Avery"))
        run(lambda: crm.add_contact("CON-0", acc, name="dup", title="", email="", phone="", notes="",
                                    created_at=t[33], created_by="Avery"))
        run(lambda: crm.add_contact("CON-N", acc, name="naive", title="", email="", phone="", notes="",
                                    created_at=NAIVE, created_by="Avery"))
        tasks = (("TASK-1", "LOW", None), ("TASK-2", "HIGH", date(2026, 10, 1)), ("TASK-3", "HIGH", date(2026, 9, 1)),
                 ("TASK-4", "NORMAL", None))
        for n, (task_id, priority, due) in enumerate(tasks):
            run(lambda n=n, task_id=task_id, priority=priority, due=due: crm.add_task(
                task_id, acc, contact_id="CON-1" if n % 2 else None, title=f" Task {n} ", due_date=due,
                priority=priority, notes="", created_at=t[33 + n], created_by="Avery"))
        run(lambda: crm.add_task("TASK-5", acc, contact_id=None, title="x", due_date=None, priority="URGENT",
                                 notes="", created_at=t[37], created_by="Avery"))
        run(lambda: crm.complete_task("TASK-2", completed_at=t[38], completed_by="Avery"))
        run(lambda: crm.complete_task("TASK-2", completed_at=t[38], completed_by="Avery"))
        run(lambda: crm.complete_task("TASK-404", completed_at=t[38], completed_by="Avery"))
        run(lambda: crm.link_interaction("INT-1", acc, contact_id="CON-0", interaction_type="MEETING",
                                         linked_at=t[38]))
        run(lambda: crm.link_interaction("INT-1", acc, contact_id=None, interaction_type="CALL", linked_at=t[38]))
        for n in range(2):
            run(lambda n=n: crm.add_activity(f"ACT-{n}", acc, contact_id="CON-0", interaction_type="CALL",
                                             occurred_at=t[20 + n], raw_notes=f" {STATEMENT} ", summary="",
                                             buyer_role=" Buyer ", buyer_statement=STATEMENT, seller_notes="",
                                             ai_assisted=bool(n), created_at=t[39], created_by="Avery"))
        run(lambda: crm.attach_engine1_interaction("ACT-0", "INT-1"))
        run(lambda: crm.attach_engine1_interaction("ACT-0", "INT-2"))
        run(lambda: crm.attach_engine1_interaction("ACT-404", "INT-1"))
        log.append(("contacts", crm.contacts(acc), crm.contacts(acc2)))
        log.append(("tasks", crm.tasks(), crm.tasks(account_id=acc, status="OPEN"), crm.tasks(status="DONE")))
        log.append(("links", crm.interaction_link("INT-1"), crm.interaction_link("INT-404")))
        log.append(("activities", crm.activities(acc)))
        log.append(("account_of", [crm.account_of(table, i) for table, i in (("contacts", "CON-1"), ("tasks", "TASK-1"),
                                                                             ("activities", "ACT-1"),
                                                                             ("tasks", "TASK-404"))]))
    return log


def test_the_postgres_stores_match_the_sqlite_stores(tmp_path, storage):
    sqlite_log = scenario(SQLiteBackend(tmp_path), "org-parity")
    organization_id = fresh("org-parity")
    postgres_log = scenario(PostgresBackend(storage), organization_id)

    def same_org(value):  # the only intended difference is the organization id the scenario ran under
        return repr(value).replace(organization_id, "org-parity")

    assert len(sqlite_log) == len(postgres_log)
    for n, (a, b) in enumerate(zip(sqlite_log, postgres_log)):
        assert repr(a) == same_org(b), f"step {n}"
    refusals = sum(1 for entry in sqlite_log if entry[0] != "ok" and len(entry) <= 2 and entry[0].endswith(
        ("Error", "database")))
    assert refusals >= 30  # the scenario really exercises the refusal paths


# --- deliberate differences ------------------------------------------------------------------------------------------


def test_postgres_refuses_records_sqlite_would_accept_without_an_owner_or_with_a_foreign_contact(storage):
    """Not parity, by design: in PostgreSQL every product record must belong to an owned account, a CRM contact must
    belong to the same account, and rows change only as the stores change them. The console enforces the first two
    in Python and never makes the third kind of change; the database now refuses all three."""
    org = fresh("org-strict")
    with storage.session(org, "local") as s:
        s.identity.ensure_organization(org, "Strict (synthetic)", AT)
        s.identity.ensure_user("user-strict-" + org, org, "Strict", AT, kind=UserKind.HUMAN)
        for action in (
                lambda: s.console.add_account_meta("ACC-UNOWNED", "x", None, None, False, AT, "x"),
                lambda: s.console.add_event("ACC-UNOWNED", "NOTE", "Human", "x", AT, "x"),
                lambda: s.crm.add_contact("CON-U", "ACC-UNOWNED", name="x", title="", email="", phone="", notes="",
                                          created_at=AT, created_by="x"),
        ):
            with pytest.raises(psycopg.errors.ForeignKeyViolation):
                with s.connection.transaction():
                    action()
        s.console.add_ownership("ACC-1", org, AT, "user-strict-" + org)
        s.crm.add_task("TASK-1", "ACC-1", contact_id=None, title="Follow up", due_date=None, priority="NORMAL",
                       notes="", created_at=AT, created_by="x")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):  # a task's title never changes (SQLite: no guard)
            with s.connection.transaction():
                s.connection.execute("UPDATE baec.crm_tasks SET title = 'renamed'")
        s.console.add_ownership("ACC-2", org, AT, "user-strict-" + org)
        s.crm.add_contact("CON-2", "ACC-2", name="x", title="", email="", phone="", notes="", created_at=AT,
                          created_by="x")
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            with s.connection.transaction():
                s.crm.add_task("TASK-X", "ACC-1", contact_id="CON-2", title="x", due_date=None, priority="NORMAL",
                               notes="", created_at=AT, created_by="x")


def test_one_session_commits_identity_console_and_crm_writes_together(storage):
    """Not parity, by design: the three SQLite stores are separate files; in PostgreSQL one session is one
    transaction, so a failure after the first write leaves none of them."""
    org = fresh("org-atomic")
    with pytest.raises(RuntimeError):
        with storage.session(org, "local") as s:
            s.identity.ensure_organization(org, "Atomic (synthetic)", AT)
            raise RuntimeError("failure after the first write")
    with storage.session(org, "local") as s:
        assert s.identity.organization(org) is None
