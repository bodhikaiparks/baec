"""V0.4 Phase 3B/3C: ConsoleService behaves the same on SQLite and on PostgreSQL storage.

The same scenario runs through the same Console / ConsoleService methods twice, with the same organization and user
ids, the same step clock, and the same generated record ids:

    SQLite      SQLiteBackend: identity, console, CRM, Engine 1, and Engine 2 SQLite files (development);
    PostgreSQL  PostgresBackend: identity, console, CRM, Engine 1, and Engine 2 in PostgreSQL (no SQLite file).

Compared exactly: every return value, every refusal (type and message), and afterwards every read the console offers
(account views, timelines, attributions, CRM lists, review queue, preparations, dashboard, activity). Database-
assigned integers of product tables (console event and block-decision ids, insertion sequence numbers) are compared
by their order within one workspace, which is all the console relies on: SQLite numbers each workspace file from 1,
PostgreSQL numbers one sequence for the whole database. Engine 1 and Engine 2 ids, including Engine 2 audit event ids,
are compared as they are.

Failure atomicity is tested separately: on PostgreSQL an action whose transaction fails leaves no product, Engine 1,
or Engine 2 record; on SQLite the earlier writes of the same action stay (separate files), as documented.
All data is synthetic.
"""

from __future__ import annotations

import dataclasses
import itertools
from collections import defaultdict
from datetime import date, datetime, timezone

import pytest

from baec_app.domain.enums import AccountState, ReviewAnswer
from baec_app.engine3.enums import HumanReviewDecision
from baec_console import console_service, demo_seed, engine1_adapter, identity
from baec_console.backends import RESET_UNAVAILABLE, PostgresBackend
from baec_console.console_service import Console, ConsoleError
from baec_console.console_store import ATTR_EVENT, ATTR_PREPARATION_BLOCK
from baec_console.identity import UserKind
from baec_console.postgres.engine2 import Engine2Connection
from baec_console.postgres.gate import ENGINE1_TABLES, ENGINE2_TABLES, TENANT_TABLES
from baec_console.postgres.stores import PgConsoleStore, PgCRMStore
from baec_console.tests.conftest import (
    ATTEST,
    StepClock,
    add_interaction,
    monitored,
    observe,
    permitted_request,
    review,
)

from .conftest import AT, fresh, psycopg

ACTOR = "Avery Parity (synthetic)"
OTHER = "Blake Parity (synthetic)"


# --- the two consoles ------------------------------------------------------------------------------------------------


@pytest.fixture
def ids(monkeypatch):
    """Deterministic record ids (the console's and Engine 1's), so one scenario generates the same ids on both
    backends. Without this, every run on either backend differs, because both generate random ids."""
    counter = {"next": itertools.count(1)}

    def token() -> str:
        return f"T{next(counter['next']):06d}"

    class Ids:
        def new_baec_id(self) -> str:
            return f"BAEC-{token()}"

        def new_token(self) -> str:
            return token()

    def reset() -> None:
        counter["next"] = itertools.count(1)
    monkeypatch.setattr(console_service, "_token", token)
    monkeypatch.setattr(engine1_adapter, "UuidIdFactory", Ids)
    return reset


@pytest.fixture
def tenants() -> dict:
    """Fresh organization and user ids, shared by both backends."""
    return {"org": fresh("org-svc"), "user": fresh("user-svc"), "other_org": fresh("org-svc-b"),
            "other_user": fresh("user-svc-b"), "teammate": fresh("user-svc-t")}


def _sqlite_console(root, t: dict) -> Console:
    for o, u, n in ((t["org"], t["user"], ACTOR), (t["org"], t["teammate"], "Casey Parity (synthetic)"),
                    (t["other_org"], t["other_user"], OTHER)):
        identity.provision(root / "identity.sqlite3", organization_id=o, organization_name=f"Org {o} (synthetic)",
                           user_id=u, display_name=n, kind=UserKind.HUMAN, now=AT)
    with identity.identity_store(root / "identity.sqlite3") as store:
        principal = store.principal(t["user"])
    return Console(root, StepClock(), principal=principal)


def _postgres_console(root, storage, t: dict, *, user="user") -> Console:
    for o, u, n in ((t["org"], t["user"], ACTOR), (t["org"], t["teammate"], "Casey Parity (synthetic)"),
                    (t["other_org"], t["other_user"], OTHER)):
        with storage.session(o, "local") as s:
            s.identity.ensure_organization(o, f"Org {o} (synthetic)", AT)
            s.identity.ensure_user(u, o, n, AT, kind=UserKind.HUMAN)
    backend = PostgresBackend(storage)
    org = t["org"] if user in ("user", "teammate") else t["other_org"]
    with backend.identity(org) as store:
        principal = store.principal(t[user])
    return Console(root, StepClock(), principal=principal, backend=backend)


@pytest.fixture
def both(tmp_path, storage, tenants, ids):
    """Run `scenario(console)` on a fresh SQLite console and a fresh PostgreSQL console."""
    def run(scenario):
        ids()
        on_sqlite = scenario(_sqlite_console(tmp_path / "sqlite", tenants), tenants)
        ids()
        on_postgres = scenario(_postgres_console(tmp_path / "postgres-engines", storage, tenants), tenants)
        return on_sqlite, on_postgres
    return run


# --- transcripts -----------------------------------------------------------------------------------------------------


def outcome(fn):
    try:
        return ("ok", fn())
    except (psycopg.Error,) as error:  # a database error is never an accepted refusal; it fails the comparison
        return ("database error", type(error).__name__, str(error).splitlines()[0])
    except Exception as error:  # noqa: BLE001 - the refusal itself is what is compared
        return ("refused", type(error).__qualname__, str(error))


class Transcript(list):
    def __call__(self, label: str, fn):
        result = outcome(fn)
        self.append((label, result))
        return result[1] if result[0] == "ok" else None


def _plain(value):
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {"__type__": type(value).__name__, **{f.name: _plain(getattr(value, f.name))
                                                     for f in dataclasses.fields(value)}}
    if isinstance(value, dict):
        return {(_plain(k) if not isinstance(k, str) else k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, (set, frozenset)):
        return sorted((_plain(v) for v in value), key=repr)
    return value


def _rank(document):
    """Replace database-assigned product integers with their order (see the module docstring)."""
    found = defaultdict(set)

    def collect(x):
        if isinstance(x, dict):
            for key in ("seq", "decision_id"):
                if isinstance(x.get(key), int):
                    found[key].add(x[key])
            if x.get("record_type") in (ATTR_EVENT, ATTR_PREPARATION_BLOCK):
                found[x["record_type"]].add(int(x["record_id"]))
            for v in x.values():
                collect(v)
        elif isinstance(x, list):
            for v in x:
                collect(v)
    collect(document)
    order = {space: {v: i for i, v in enumerate(sorted(values))} for space, values in found.items()}

    def rewrite(x):
        if isinstance(x, dict):
            out = {k: rewrite(v) for k, v in x.items()}
            for key in ("seq", "decision_id"):
                if isinstance(x.get(key), int):
                    out[key] = ("rank", order[key][x[key]])
            if x.get("record_type") in (ATTR_EVENT, ATTR_PREPARATION_BLOCK):
                out["record_id"] = ("rank", order[x["record_type"]][int(x["record_id"])])
            return out
        if isinstance(x, list):
            return [rewrite(v) for v in x]
        return x
    return rewrite(document)


def reads(console: Console) -> dict:
    """Everything the console shows, per workspace."""
    out = {"dashboard": console.dashboard(), "queue": _plain(console.review_queue()),
           "activity": _plain(console.activity()), "actor": console.actor}
    for service in console.services:
        accounts = service.account_ids()
        out[service.workspace] = _rank(_plain({
            "accounts": accounts,
            "views": [service.account_view(a) for a in accounts],
            "timelines": [service.timeline(a) for a in accounts],
            "attributions": [service.attributions(a) for a in accounts],
            "contacts": [service.crm_contacts(a) for a in accounts],
            "activities": [service.crm_activities(a) for a in accounts],
            "tasks": service.crm_tasks(), "open_tasks": service.crm_tasks(status="OPEN"),
            "preparations": [service.preparation_view(p) for p in service.preparation_ids()],
        }))
    return out


def _same(both, scenario):
    on_sqlite, on_postgres = both(scenario)
    assert [s for s in on_postgres["steps"] if s[1][0] == "database error"] == []
    assert on_postgres["steps"] == on_sqlite["steps"]
    for key in on_sqlite["reads"]:
        assert on_postgres["reads"][key] == on_sqlite["reads"][key], key
    return on_sqlite


# --- scenarios -------------------------------------------------------------------------------------------------------


def _crm_and_identity(console: Console, t: dict) -> dict:
    """Organizations/users, renames, account ownership, attribution, contacts, tasks, activities, links."""
    step, s = Transcript(), console.local
    step("blank account", lambda: s.create_account("  "))
    a = step("account A", lambda: s.create_account("Harbor Point Labs", industry="Diagnostics (synthetic)",
                                                     owner_note="Synthetic"))
    b = step("account B", lambda: s.create_account("Northwind Clinics", industry="Clinics (synthetic)"))
    step("synthetic flag in local", lambda: s.create_account("X", synthetic=True))
    step("ids", lambda: s.account_ids())
    step("has", lambda: (s.has_account(a), s.has_account("ACC-NONE")))
    alex = step("contact", lambda: s.crm_add_contact(a, name="  alex Rivera ", title="Imaging Lead",
                                                      email="alex@example.invalid", phone="", notes="synthetic"))
    step("contact 2", lambda: s.crm_add_contact(a, name="Bea Chen", title="", email="", phone="555-0100", notes=""))
    other = step("contact on B", lambda: s.crm_add_contact(b, name="Dana Ortiz"))
    step("blank contact", lambda: s.crm_add_contact(a, name=" "))
    step("contact on unknown account", lambda: s.crm_add_contact("ACC-NONE", name="Eve"))
    t1 = step("task", lambda: s.crm_add_task(a, contact_id=alex, title="Call back", due_date=date(2026, 10, 1),
                                              priority="HIGH", notes="synthetic"))
    step("task 2", lambda: s.crm_add_task(a, contact_id=None, title="Send summary", due_date=None, priority="LOW"))
    step("task 3", lambda: s.crm_add_task(b, contact_id=other, title="Intro", due_date=date(2026, 9, 30),
                                          priority="NORMAL"))
    step("task with another account's contact", lambda: s.crm_add_task(a, contact_id=other, title="x", due_date=None,
                                                                       priority="LOW"))
    step("task unknown priority", lambda: s.crm_add_task(a, contact_id=None, title="x", due_date=None,
                                                         priority="URGENT"))
    step("blank task", lambda: s.crm_add_task(a, contact_id=None, title=" ", due_date=None, priority="LOW"))
    step("complete", lambda: s.crm_complete_task(t1))
    step("complete again", lambda: s.crm_complete_task(t1))
    step("complete unknown", lambda: s.crm_complete_task("TASK-NONE"))
    interaction = step("interaction", lambda: add_interaction(console, a))
    step("blank statement", lambda: s.record_interaction(a, buyer_role="CFO", occurred_at=AT, buyer_statement=" "))
    step("link", lambda: s.crm_link_interaction(interaction, a, contact_id=alex, interaction_type="CALL"))
    step("link to other account", lambda: s.crm_link_interaction(interaction, b, contact_id=None,
                                                                 interaction_type="CALL"))
    step("read link", lambda: s.crm_interaction_link(interaction))
    act = step("activity", lambda: s.crm_add_activity(
        a, contact_id=alex, interaction_type="MEETING", occurred_at=datetime(2026, 9, 3, 15, 0, tzinfo=timezone.utc),
        raw_notes=" Met with the team. ", summary="Met", buyer_role="Imaging Lead",
        buyer_statement="We'd revisit if the price rises above 8%.", seller_notes="", ai_assisted=True))
    step("blank activity", lambda: s.crm_add_activity(a, contact_id=None, interaction_type="CALL", occurred_at=AT,
                                                      raw_notes=" "))
    step("attach", lambda: s.crm_attach_engine1_interaction(act, interaction))
    step("attach again", lambda: s.crm_attach_engine1_interaction(act, interaction))
    step("rename", lambda: console.set_actor("Avery P. (synthetic)"))
    step("rename to a teammate's name", lambda: console.set_actor("Casey Parity (synthetic)"))
    step("blank rename", lambda: console.set_actor(" "))
    step("after rename", lambda: s.crm_add_task(a, contact_id=None, title="After rename", due_date=None,
                                                priority="NORMAL"))
    return {"steps": step, "reads": reads(console)}


def _engines_and_preparation(console: Console, t: dict) -> dict:
    """Engine 1 drafts, confirmation, dormancy, state; Engine 2 monitoring, correction, review; Engine 3
    preparation request, block/unblock, generation, edit, review decisions, replacement; and their refusals."""
    step, s = Transcript(), console.local
    account_id, baec_id = monitored(console)
    step("handoff", lambda: s.handoff(baec_id))
    step("monitor twice", lambda: s.start_monitoring(account_id, baec_id, target_entities=("X",), required=frozenset(),
                                                     sources=()))
    interaction = s.account_view(account_id).interactions[0][0].interaction_id
    second = step("second draft", lambda: s.capture_condition(account_id, interaction, demo_seed.condition_form()))
    step("stale fingerprint", lambda: s.confirm_condition(second, displayed_fingerprint="0" * 64))
    step("discard", lambda: s.discard_draft(second))
    step("discard again", lambda: s.discard_draft(second))
    judgment = step("dormancy judgment", lambda: s.record_dormancy_judgment(
        baec_id, plausibility=ReviewAnswer.YES, addressability=ReviewAnswer.YES, notes="synthetic"))
    step("preview dormant", lambda: s.preview_state_change(account_id, AccountState.CONDITIONALLY_DORMANT,
                                                           {"baec_id": baec_id, "judgment_id": judgment}))
    step("dormant", lambda: s.change_account_state(account_id, AccountState.CONDITIONALLY_DORMANT,
                                                   {"baec_id": baec_id, "judgment_id": judgment}))
    step("active without evidence", lambda: s.change_account_state(account_id, AccountState.ACTIVE_OPPORTUNITY, {}))
    first = step("observation", lambda: observe(console, account_id, baec_id, new_price="44800", flag=False))
    corrected = step("correction", lambda: observe(console, account_id, baec_id, corrects=first.observation_id,
                                                   content="CORRECTION: the new fee is USD 43,600."))
    step("queue superseded", lambda: s.queue_observation_for_review(baec_id, first.observation_id))
    step("unknown source", lambda: s.add_observation(baec_id, dataclasses.replace(
        _form(console, account_id), source_id="SRC-NONE")))
    step("packet", lambda: s.review_packet(corrected.candidate_id))
    step("stale digest", lambda: s.submit_correspondence_review(
        corrected.candidate_id, displayed_context_digest="0" * 64, findings={}, notes={}, sufficiency="YES"))
    recorded = step("review", lambda: review(console, corrected.candidate_id))
    review_id = recorded.review.review_id
    step("snapshot", lambda: s.preparation_snapshot(review_id))
    step("block", lambda: s.block_preparation(review_id, reason="Waiting for the buyer's procurement window."))
    step("block twice", lambda: s.block_preparation(review_id, reason="again"))
    step("request while blocked", lambda: s.request_preparation(review_id, permitted_request(corrected)))
    step("unblock", lambda: s.unblock_preparation(review_id, reason="Window opened."))
    step("bad statement use", lambda: s.request_preparation(review_id, permitted_request(corrected,
                                                                                         statement_use="VERBATIM")))
    request_id = step("request", lambda: s.request_preparation(review_id, permitted_request(corrected)))
    preparation_id = step("generate", lambda: s.generate_preparation(request_id))
    step("generate twice", lambda: s.generate_preparation(request_id))
    view = s.preparation_view(preparation_id)
    drafts = [o["artifact"]["artifact_id"] for o in view["preparation"]["artifacts"]
              if o["artifact"] and o["report"]["allowed"]]
    parent = next(o for o in view["preparation"]["artifacts"] if o["artifact"] and o["report"]["allowed"])
    segments = [dict(seg) for seg in parent["artifact"]["segments"]]
    edit = step("edit", lambda: s.edit_artifact(preparation_id, drafts[0], segments))
    step("accept without attestations", lambda: s.review_artifact(preparation_id, drafts[0],
                                                                  HumanReviewDecision.ACCEPT_AS_DRAFTED,
                                                                  attestations={}))
    step("accept edit", lambda: s.review_artifact(preparation_id, drafts[0], HumanReviewDecision.EDIT,
                                                  attestations=ATTEST, edit_id=edit["edit_id"]))
    step("review twice", lambda: s.review_artifact(preparation_id, drafts[0], HumanReviewDecision.REJECT,
                                                   attestations={}))
    for aid in drafts[1:]:
        step(f"reject {aid}", lambda aid=aid: s.review_artifact(preparation_id, aid, HumanReviewDecision.REJECT,
                                                                attestations={}, reason="Not needed."))
    newer = step("newer request", lambda: s.request_preparation(review_id, permitted_request(corrected)))
    step("generate newer", lambda: s.generate_preparation(newer))
    step("edit replaced", lambda: s.edit_artifact(preparation_id, drafts[0], segments))
    step("eligibility", lambda: s.preparation_eligibility(review_id))
    return {"steps": step, "reads": reads(console)}


def _form(console, account_id):
    from baec_console.engine2_adapter import ObservationForm
    source_id = next(iter(console.local.account_view(account_id).sources))
    return ObservationForm(source_id=source_id, observed_at=AT, content="Synthetic notice.")


def _synthetic_demo(console: Console, t: dict) -> dict:
    """The seeded synthetic demo, through the seed user, on the synthetic workspace (no reset: see Part 5)."""
    step = Transcript()
    service = console.seed_service()
    clock = demo_seed.ScriptedClock(datetime(2026, 9, 4, 15, 0, tzinfo=timezone.utc))
    service.clock = clock
    account_id = step("seed account", lambda: service.create_account(demo_seed.ACCOUNT, industry=demo_seed.INDUSTRY,
                                                                      owner_note="Synthetic example account",
                                                                      synthetic=True))
    interaction = step("seed interaction", lambda: service.record_interaction(
        account_id, buyer_role=demo_seed.BUYER_ROLE, occurred_at=datetime(2026, 9, 4, 14, 0, tzinfo=timezone.utc),
        buyer_statement=demo_seed.BUYER_STATEMENT, seller_notes=demo_seed.SELLER_NOTES))
    step("complete demo", lambda: demo_seed._complete(service, clock, account_id, interaction))
    step("seed user is not the loader", lambda: [a["user_id"] == t["user"]
                                                 for a in console.synthetic.attributions(account_id)])
    return {"steps": step, "reads": reads(console)}


def _cross_tenant(console: Console, t: dict) -> dict:
    """Another organization's console never reaches this organization's records."""
    step, s = Transcript(), console.local
    account_id, baec_id = monitored(console)
    contact = s.crm_add_contact(account_id, name="Alex Rivera")
    task = s.crm_add_task(account_id, contact_id=contact, title="Call", due_date=None, priority="LOW")
    result = observe(console, account_id, baec_id)
    root = console.root
    if isinstance(console._backend, PostgresBackend):
        intruder = _postgres_console(root, console._backend.storage, t, user="other_user")
    else:
        with identity.identity_store(root / "identity.sqlite3") as store:
            intruder = Console(root, StepClock(), principal=store.principal(t["other_user"]))
    x = intruder.local
    step("intruder sees", lambda: (x.account_ids(), intruder.dashboard(), x.crm_tasks()))
    for label, fn in {
        "view": lambda: x.account_view(account_id), "timeline": lambda: x.timeline(account_id),
        "contacts": lambda: x.crm_contacts(account_id), "complete": lambda: x.crm_complete_task(task),
        "contact": lambda: x.crm_add_contact(account_id, name="Mallory"),
        "observation": lambda: x.add_observation(baec_id, _form(console, account_id)),
        "packet": lambda: x.review_packet(result.candidate_id),
        "review": lambda: review(intruder, result.candidate_id),
        "handoff": lambda: x.handoff(baec_id),
        "link": lambda: x.crm_interaction_link(s.account_view(account_id).interactions[0][0].interaction_id),
        "attributions": lambda: x.attributions(account_id),
    }.items():
        step(f"intruder {label}", fn)
    step("owner still sees", lambda: (s.account_ids() == [account_id], s.crm_tasks()))
    return {"steps": step, "reads": reads(console)}


@pytest.mark.parametrize("scenario", [_crm_and_identity, _engines_and_preparation, _synthetic_demo, _cross_tenant],
                         ids=lambda f: f.__name__.strip("_"))
def test_the_same_service_workflow_is_identical_on_both_backends(both, scenario):
    result = _same(both, scenario)
    assert any(o[0] == "ok" for _, o in result["steps"])


def test_refusals_are_the_services_own_and_reset_is_refused_on_postgresql(both):
    on_sqlite, on_postgres = both(_crm_and_identity)
    sq, pg = dict(on_sqlite["steps"]), dict(on_postgres["steps"])
    assert sq["complete again"] == ("refused", "CRMStoreError", "Task is already complete.")
    assert sq["contact on unknown account"] == ("refused", "AccessDenied", console_service.NOT_ACCESSIBLE)
    assert sq["rename to a teammate's name"] == ("refused", "ConsoleError",
                                                 "That name is already used by someone else in this organization.")
    assert pg == sq


def test_synthetic_reset_works_on_sqlite_and_is_refused_on_postgresql_without_reading_anything(both, storage,
                                                                                                 tenants):
    """The one deliberate difference (Part 5): PostgreSQL rows are append-only, so a demo reset is not deleting."""
    def scenario(console, t):
        console.seed_service().create_account("Demo Labs", synthetic=True)
        return {"reset": outcome(console.reset_synthetic), "synthetic": console.synthetic.account_ids()}
    on_sqlite, on_postgres = both(scenario)
    assert on_sqlite == {"reset": ("ok", None), "synthetic": []}
    assert on_postgres["reset"] == ("refused", "ConsoleError", RESET_UNAVAILABLE)
    assert len(on_postgres["synthetic"]) == 1  # nothing was deleted


def test_a_signal_alone_never_moves_an_account_to_active_opportunity_on_either_backend(both):
    for result in both(_engines_and_preparation):
        assert dict(result["steps"])["active without evidence"][0] == "refused"
        assert [v["account"]["state"] for v in result["reads"]["local"]["views"]] == [
            AccountState.CONDITIONALLY_DORMANT]


# --- PostgreSQL transaction boundary ---------------------------------------------------------------------------------


def _counts(storage, organization_id: str) -> dict:
    """Rows this organization has in every product, Engine 1, and Engine 2 table, both workspaces."""
    out = {}
    for workspace in ("local", "synthetic"):
        with storage.database.tenant(organization_id, workspace) as connection:
            for table in TENANT_TABLES:
                where = "" if table in ("organizations", "users", "identity_audit") else " WHERE workspace = %s"
                args = () if not where else (workspace,)
                out[(workspace, table)] = connection.execute(f"SELECT count(*) FROM baec.{table}{where}",
                                                             args).fetchone()[0]
            for table in ENGINE2_TABLES:
                out[(workspace, table)] = connection.execute(f"SELECT count(*) FROM baec_engine2.{table}"
                                                             ).fetchone()[0]
            for table in ENGINE1_TABLES:
                out[(workspace, "engine1", table)] = connection.execute(
                    f"SELECT count(*) FROM baec_engine1.{table}").fetchone()[0]
    return out


class Injected(Exception):
    pass


def _fail_attribution(monkeypatch, *, database: bool):
    """Make the action's last product write (its attribution) fail: as a database error that aborts the PostgreSQL
    transaction, or as an application exception."""
    original = PgConsoleStore.add_attribution

    def failing(self, *args, **kwargs):
        original(self, *args, **kwargs)
        if database:
            self.db.execute("SELECT 1 / 0")
        raise Injected("attribution failed after the record was written")
    monkeypatch.setattr(PgConsoleStore, "add_attribution", failing)


@pytest.fixture
def pg_console(tmp_path, storage, tenants, ids):
    ids()
    return _postgres_console(tmp_path / "pg", storage, tenants)


ACTIONS = {
    "create account": lambda c, ctx: c.local.create_account("Failing Labs"),
    "add contact": lambda c, ctx: c.local.crm_add_contact(ctx["account"], name="Alex"),
    "add task": lambda c, ctx: c.local.crm_add_task(ctx["account"], contact_id=None, title="Call", due_date=None,
                                                     priority="LOW"),
    "complete task": lambda c, ctx: c.local.crm_complete_task(ctx["task"]),
    "add activity": lambda c, ctx: c.local.crm_add_activity(ctx["account"], contact_id=None, interaction_type="CALL",
                                                             occurred_at=AT, raw_notes="Notes"),
    "capture condition": lambda c, ctx: c.local.capture_condition(ctx["account"], ctx["interaction"],
                                                                   demo_seed.condition_form()),
    "start monitoring": lambda c, ctx: monitored(c),
    "add observation": lambda c, ctx: observe(c, ctx["monitored"][0], ctx["monitored"][1]),
    "review correspondence": lambda c, ctx: review(c, ctx["candidate"]),
    "block preparation": lambda c, ctx: c.local.block_preparation(ctx["review"], reason="Hold"),
    "request preparation": lambda c, ctx: c.local.request_preparation(ctx["review"], permitted_request(ctx["result"])),
    # Engine 1 writes, then the product write that fails: all one transaction on PostgreSQL (Phase 3C).
    "record interaction": lambda c, ctx: add_interaction(c, ctx["account"]),
    "confirm condition": lambda c, ctx: c.local.confirm_condition(ctx["draft"], displayed_fingerprint=ctx["fp"]),
    "record not confirmable": lambda c, ctx: c.local.record_not_confirmable(ctx["weak_draft"],
                                                                            displayed_fingerprint=ctx["weak_fp"]),
    "dormancy judgment": lambda c, ctx: c.local.record_dormancy_judgment(
        ctx["monitored"][1], plausibility=ReviewAnswer.YES, addressability=ReviewAnswer.YES, notes="synthetic"),
    "state change": lambda c, ctx: c.local.change_account_state(
        ctx["monitored"][0], AccountState.CONDITIONALLY_DORMANT,
        {"baec_id": ctx["monitored"][1], "judgment_id": ctx["judgment"]}),
}


def weak_form():
    """The demo form with evaluation linkage unknown: the locked classifier records it as not confirmable."""
    form = demo_seed.condition_form()
    criteria = dict(form.criteria)
    criteria["EVALUATION_LINKAGE"] = {"finding": "UNKNOWN", "excerpt": "", "rationale": ""}
    return dataclasses.replace(form, criteria=criteria)


def _context(console) -> dict:
    s = console.local
    account = s.create_account("Context Labs")
    ctx = {"account": account, "interaction": add_interaction(console, account),
           "task": s.crm_add_task(account, contact_id=None, title="Open task", due_date=None, priority="LOW")}
    ctx["monitored"] = monitored(console)
    ctx["result"] = observe(console, *ctx["monitored"])
    ctx["candidate"] = ctx["result"].candidate_id
    other = observe(console, *ctx["monitored"], new_price="44000", content="Second synthetic notice.")
    ctx["review"] = review(console, other.candidate_id).review.review_id
    ctx["result"] = other
    ctx["draft"] = s.capture_condition(account, ctx["interaction"], demo_seed.condition_form())
    ctx["fp"] = s.preview_condition(ctx["draft"])["fingerprint"]
    ctx["weak_draft"] = s.capture_condition(account, ctx["interaction"], weak_form())
    ctx["weak_fp"] = s.preview_condition(ctx["weak_draft"])["fingerprint"]
    ctx["judgment"] = s.record_dormancy_judgment(ctx["monitored"][1], plausibility=ReviewAnswer.YES,
                                                 addressability=ReviewAnswer.YES, notes="context")
    return ctx


@pytest.mark.parametrize("database_error", [True, False], ids=["database-error", "application-error"])
@pytest.mark.parametrize("action", sorted(ACTIONS))
def test_a_failed_postgresql_action_leaves_no_partial_product_or_engine2_record(pg_console, storage, tenants,
                                                                                monkeypatch, action, database_error):
    """Also Engine 1 (Phase 3C): the Engine 1 write of the action is rolled back with the failed product write."""
    ctx = _context(pg_console)
    before = _counts(storage, tenants["org"])
    task_before = pg_console.local.crm_tasks(account_id=ctx["account"])
    _fail_attribution(monkeypatch, database=database_error)
    with pytest.raises((Injected, psycopg.errors.DivisionByZero)):
        ACTIONS[action](pg_console, ctx)
    monkeypatch.undo()
    assert _counts(storage, tenants["org"]) == before
    assert pg_console.local.crm_tasks(account_id=ctx["account"]) == task_before  # task completion rolled back too
    pg_console.local.crm_add_contact(ctx["account"], name="Still works")  # the console and pool remain usable


def test_on_sqlite_the_same_failure_keeps_the_earlier_write_of_the_action(tmp_path, tenants, ids, monkeypatch):
    """The documented SQLite limitation the PostgreSQL unit removes: separate files cannot share a transaction."""
    from baec_console.console_store import ConsoleStore
    ids()
    console = _sqlite_console(tmp_path / "sqlite", tenants)
    account = console.local.create_account("Context Labs")
    original = ConsoleStore.add_attribution

    def failing(self, *args, **kwargs):
        original(self, *args, **kwargs)
        raise Injected("attribution failed")
    monkeypatch.setattr(ConsoleStore, "add_attribution", failing)
    with pytest.raises(Injected):
        console.local.crm_add_contact(account, name="Alex")
    monkeypatch.undo()
    assert [c["name"] for c in console.local.crm_contacts(account)] == ["Alex"]  # the CRM row stayed
    assert not [a for a in console.local.attributions(account) if a["record_type"] == "crm.contact"]


def test_an_engine1_write_is_inside_the_postgresql_transaction(pg_console, storage, tenants, monkeypatch):
    """Phase 3C regression: Engine 1 is PostgreSQL now. A failed product write after the Engine 1 account leaves no
    Engine 1 account either (Phase 3B kept it, unowned and invisible). The identical scenario on SQLite keeps the
    Engine 1 account, which is unchanged V0.3 behavior (test_on_sqlite_the_same_failure_keeps_the_earlier_write_...)."""
    before = _counts(storage, tenants["org"])
    _fail_attribution(monkeypatch, database=True)
    with pytest.raises(psycopg.errors.DivisionByZero):
        pg_console.local.create_account("Orphan Labs")
    monkeypatch.undo()
    assert _counts(storage, tenants["org"]) == before
    with storage.database.tenant(tenants["org"], "local") as connection:
        assert connection.execute("SELECT count(*) FROM baec_engine1.accounts WHERE name = 'Orphan Labs'"
                                  ).fetchone()[0] == 0
    assert pg_console.local.account_ids() == []


def test_on_sqlite_a_failed_create_account_keeps_the_unowned_engine1_account(tmp_path, tenants, ids, monkeypatch):
    """The deliberate SQLite/PostgreSQL atomicity difference, kept as V0.3 behavior: separate files. The orphan is
    owned by no organization, so it is invisible and inaccessible (fail closed)."""
    from baec_app.data.database import open_database
    from baec_app.data.repository import Repository
    from baec_console.console_store import ConsoleStore
    ids()
    console = _sqlite_console(tmp_path / "sqlite", tenants)
    original = ConsoleStore.add_attribution

    def failing(self, *args, **kwargs):
        original(self, *args, **kwargs)
        raise Injected("attribution failed")
    monkeypatch.setattr(ConsoleStore, "add_attribution", failing)
    with pytest.raises(Injected):
        console.local.create_account("Orphan Labs")
    monkeypatch.undo()
    connection = open_database(console.local.engine1_path)
    try:
        orphans = [a.account_id for a in Repository(connection).list_accounts() if a.name == "Orphan Labs"]
    finally:
        connection.close()
    assert len(orphans) == 1 and console.local.account_ids() == []
    with pytest.raises(console_service.AccessDenied):
        console.local.account_view(orphans[0])


def test_a_recorded_engine3_refusal_is_kept_while_the_action_reports_it(pg_console, storage, tenants):
    """generate_preparation records PREPARATION_REFUSED and then raises; the unit keeps exactly that record."""
    ctx = _context(pg_console)
    s = pg_console.local
    request_id = s.request_preparation(ctx["review"], permitted_request(ctx["result"]))
    s.block_preparation(ctx["review"], reason="Hold")
    before = _counts(storage, tenants["org"])
    with pytest.raises(console_service.PreparationRefused):
        s.generate_preparation(request_id)
    after = _counts(storage, tenants["org"])
    assert {k: after[k] - before[k] for k in after if after[k] != before[k]} == {("local", "events"): 1}
    assert s.timeline(ctx["monitored"][0])[-1].event_type == "PREPARATION_REFUSED"


def test_a_refused_correction_differs_only_in_what_the_failed_action_leaves_behind(both):
    """Correcting an observation that is already corrected: Engine 2 refuses the second supersession on both
    backends with the same error. On SQLite the new observation was already committed in its own transaction and stays
    (without a supersession); on PostgreSQL the action is one transaction and nothing of it remains. This is the
    documented cross-backend difference, and the only one this suite found outside synthetic reset."""
    def scenario(console, t):
        account_id, baec_id = monitored(console)
        first = observe(console, account_id, baec_id, flag=False)
        observe(console, account_id, baec_id, corrects=first.observation_id, content="CORRECTION one.")
        before = len(console.local.account_view(account_id).observations)
        again = outcome(lambda: observe(console, account_id, baec_id, corrects=first.observation_id,
                                        content="CORRECTION two."))
        return again, len(console.local.account_view(account_id).observations) - before
    (sq_refusal, sq_left), (pg_refusal, pg_left) = both(scenario)
    assert sq_refusal == pg_refusal and sq_refusal[:2] == ("refused", "DuplicateRecordError")
    assert (sq_left, pg_left) == (1, 0)


def test_a_postgresql_service_action_is_one_transaction_on_one_connection(pg_console, monkeypatch):
    """Product and Engine 2 stores opened during one action share one tenant transaction."""
    seen = []
    original = PgCRMStore.add_contact

    def spy(self, *args, **kwargs):
        seen.append(("crm", id(self.db)))
        return original(self, *args, **kwargs)
    monkeypatch.setattr(PgCRMStore, "add_contact", spy)
    original_attr = PgConsoleStore.add_attribution

    def spy_attr(self, *args, **kwargs):
        seen.append(("console", id(self.db)))
        return original_attr(self, *args, **kwargs)
    monkeypatch.setattr(PgConsoleStore, "add_attribution", spy_attr)
    account = pg_console.local.create_account("One Transaction Labs")
    seen.clear()
    pg_console.local.crm_add_contact(account, name="Alex")
    assert [k for k, _ in seen] == ["crm", "console"] and len({c for _, c in seen}) == 1


def test_a_postgresql_console_uses_no_sqlite_file(pg_console, tmp_path):
    account = pg_console.local.create_account("Files Labs")
    add_interaction(pg_console, account)
    monitored(pg_console)
    assert sorted(p.name for p in tmp_path.rglob("*.sqlite3")) == []  # identity, console, CRM, Engines 1 and 2


def test_engine2_connections_of_a_console_see_only_their_workspace(pg_console, storage, tenants):
    monitored(pg_console)
    with storage.database.tenant(tenants["org"], "synthetic") as connection:
        assert Engine2Connection(connection).execute("SELECT count(*) FROM monitoring_plans").fetchone()[0] == 0
    with storage.database.tenant(tenants["org"], "local") as connection:
        assert Engine2Connection(connection).execute("SELECT count(*) FROM monitoring_plans").fetchone()[0] == 1
