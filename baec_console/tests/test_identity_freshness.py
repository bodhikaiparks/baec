"""V0.4 Phase 2: durable attribution, principal freshness, demo isolation, identity kinds, and deployment mode.

All data is synthetic. These are software tests of the product authorization boundary, not evidence for the theory
and not a production security assessment.
"""

from __future__ import annotations

import ast
import inspect
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from baec_app.domain.enums import AccountState
from baec_app.engine3.enums import HumanReviewDecision
from baec_console import console_store, demo_seed, deployment, identity, storage
from baec_console.console_service import (
    IDENTITY_FILE,
    UNGUARDED_METHODS,
    AccessDenied,
    Console,
    ConsoleError,
    ConsoleService,
    PrincipalInvalid,
)
from baec_console.identity import UserKind

from .conftest import ACTOR, ATTEST, StepClock, make_console
from .test_tenancy import AT, _db, _downgrade_to_v03, build_world, tenant

CONSOLE = Path(__file__).resolve().parents[1]
HUMAN = UserKind.HUMAN


def _set_active(root: Path, user_id: str, active: bool) -> None:
    with identity.identity_store(root / IDENTITY_FILE) as store:
        store.set_active(user_id, active, at=AT)


def _rename(root: Path, user_id: str, name: str) -> None:
    with identity.identity_store(root / IDENTITY_FILE) as store:
        store.rename_user(user_id, name, at=AT)


# --- 2A / 2C: durable attribution and rename ------------------------------------------------------------------------


def test_rename_keeps_the_user_id_and_each_records_historical_name(tmp_path):
    root, clock = tmp_path / "data", StepClock()
    alice = tenant(root, clock, "org-a", "user-alice", "Alice Smith")
    account_id = alice.local.create_account("Rename account (synthetic)")
    interaction_id = alice.local.record_interaction(
        account_id, buyer_role=demo_seed.BUYER_ROLE, occurred_at=AT, buyer_statement=demo_seed.BUYER_STATEMENT,
        seller_notes=demo_seed.SELLER_NOTES)
    draft_id = alice.local.capture_condition(account_id, interaction_id, demo_seed.condition_form())

    alice.set_actor("Alice Jones")
    assert (alice.principal.user_id, alice.principal.organization_id) == ("user-alice", "org-a")

    alice.local.discard_draft(draft_id)
    rows = {(a["record_type"], a["record_id"], a["action"]): a for a in alice.local.attributions(account_id)}
    captured = rows[(console_store.ATTR_DRAFT, draft_id, "CAPTURED")]
    discarded = rows[(console_store.ATTR_DRAFT_RESOLUTION, draft_id, "DISCARDED")]
    # The historical action still resolves to the same immutable user, under the name it was recorded with.
    assert (captured["user_id"], captured["organization_id"], captured["display_name_snapshot"]) == (
        "user-alice", "org-a", "Alice Smith")
    # The new action records the same user under the new name.
    assert (discarded["user_id"], discarded["display_name_snapshot"]) == ("user-alice", "Alice Jones")
    assert {a["user_id"] for a in rows.values()} == {"user-alice"}

    timeline = {i.title: i for i in alice.local.timeline(account_id)}
    assert (timeline["Condition captured (not yet confirmed)"].actor,
            timeline["Condition captured (not yet confirmed)"].actor_user_id) == ("Alice Smith", "user-alice")
    assert (timeline["Captured condition discarded"].actor,
            timeline["Captured condition discarded"].actor_user_id) == ("Alice Jones", "user-alice")
    with identity.identity_store(root / IDENTITY_FILE) as store:
        assert [(e.event_type, e.detail) for e in store.audit("user-alice")] == [
            ("USER_CREATED", "HUMAN user created as 'Alice Smith'"),
            ("USER_RENAMED", "'Alice Smith' -> 'Alice Jones'")]


def test_a_rename_made_elsewhere_reaches_a_running_console_without_invalidating_it(tmp_path):
    root, clock = tmp_path / "data", StepClock()
    alice = tenant(root, clock, "org-a", "user-alice", "Alice Smith")
    account_id = alice.local.create_account("Rename elsewhere (synthetic)")
    _rename(root, "user-alice", "Alice Jones")  # e.g. another session or an administrator
    contact_id = alice.local.crm_add_contact(account_id, name="Pat Lee (synthetic)")
    assert alice.actor == "Alice Jones"
    rows = {(a["record_type"], a["record_id"]): a for a in alice.local.attributions(account_id)}
    assert rows[(console_store.ATTR_ACCOUNT, account_id)]["display_name_snapshot"] == "Alice Smith"
    assert rows[(console_store.ATTR_CRM_CONTACT, contact_id)]["display_name_snapshot"] == "Alice Jones"
    assert {r["user_id"] for r in rows.values()} == {"user-alice"}


def test_every_human_action_in_the_workflow_is_attributed_to_the_acting_user(tmp_path):
    root = tmp_path / "data"
    console = make_console(root, StepClock())
    world = build_world(console)
    s = console.local
    s.crm_complete_task(world.task_id)
    s.crm_link_interaction(world.interaction_id, world.account_id, contact_id=None, interaction_type="CALL")
    s.crm_attach_engine1_interaction(world.activity_id, world.interaction_id)
    s.review_artifact(world.preparation_id, world.artifact_id, HumanReviewDecision.ACCEPT_AS_DRAFTED,
                      attestations=ATTEST)
    principal = console.principal
    rows = s.attributions(world.account_id)
    assert {(r["user_id"], r["organization_id"], r["display_name_snapshot"], r["basis"]) for r in rows} == {
        (principal.user_id, principal.organization_id, ACTOR, "RECORDED")}
    E2 = console_store.ATTR_ENGINE2
    assert {r["record_type"] for r in rows} == {
        console_store.ATTR_ACCOUNT, console_store.ATTR_INTERACTION, console_store.ATTR_DRAFT,
        console_store.ATTR_DRAFT_RESOLUTION, console_store.ATTR_BAEC_CONFIRMATION,
        console_store.ATTR_DORMANCY_JUDGMENT, console_store.ATTR_PREPARATION_REQUEST, console_store.ATTR_PREPARATION,
        console_store.ATTR_PREPARATION_REVIEW, console_store.ATTR_EVENT, console_store.ATTR_CRM_CONTACT,
        console_store.ATTR_CRM_TASK, console_store.ATTR_CRM_ACTIVITY, console_store.ATTR_CRM_INTERACTION_LINK,
        console_store.ATTR_CRM_ACTIVITY_ENGINE1_LINK, E2 + "AuthorizedSource", E2 + "MonitoringPlan",
        E2 + "Observation", E2 + "CorrespondenceCandidate", E2 + "HumanCorrespondenceReview"}
    for item in s.timeline(world.account_id):
        if item.actor_kind == "Human":
            assert item.actor_user_id == principal.user_id, item
        else:
            assert item.actor_user_id is None, item  # engines and the template are never attributed to a person


def test_an_account_state_change_is_attributed(tmp_path):
    console = make_console(tmp_path / "data", StepClock())
    world = build_world(console)
    console.local.change_account_state(world.account_id, AccountState.CONDITIONALLY_DORMANT,
                                       {"baec_id": world.baec_id, "judgment_id": world.judgment_id})
    item = next(i for i in console.local.timeline(world.account_id) if i.event_type == "STATE_CHANGE")
    assert (item.actor, item.actor_user_id) == (ACTOR, console.principal.user_id)


def test_attributions_are_append_only(tmp_path):
    console = make_console(tmp_path / "data", StepClock())
    console.local.create_account("Append only (synthetic)")
    with _db(tmp_path / "data" / "local" / "console.sqlite3") as db:
        for statement in ("UPDATE attributions SET user_id = 'someone-else'", "DELETE FROM attributions"):
            with pytest.raises(sqlite3.IntegrityError, match="append-only"):
                db.execute(statement)


# --- migration / backfill -----------------------------------------------------------------------------------------


def _live(console: Console, account_id: str) -> set:
    # A V0.3 store never recorded links, attachments, or who asked for a generation, so those have nothing to backfill.
    unrecorded = {console_store.ATTR_CRM_INTERACTION_LINK, console_store.ATTR_CRM_ACTIVITY_ENGINE1_LINK,
                  console_store.ATTR_PREPARATION}
    return {(r["record_type"], r["record_id"], r["action"], r["user_id"], r["display_name_snapshot"])
            for r in console.local.attributions(account_id) if r["record_type"] not in unrecorded}


def test_v03_history_under_the_local_users_name_is_backfilled_to_the_local_user(tmp_path):
    root = tmp_path / "data"
    before = make_console(root, StepClock())
    world = build_world(before)
    recorded = _live(before, world.account_id)
    _downgrade_to_v03(root, ACTOR)  # the V0.3 "Your name" preference was the name on every recorded action

    after = Console.local_development(root, StepClock())
    rows = after.local.attributions(world.account_id)
    assert {r["basis"] for r in rows} == {"BACKFILL"}
    assert _live(after, world.account_id) == recorded  # exactly what live recording would have attributed
    for item in after.local.timeline(world.account_id):
        assert item.actor_user_id == (identity.LOCAL_USER_ID if item.actor_kind == "Human" else None), item
    with _db(root / "local" / "console.sqlite3") as db:
        log = console_store.ConsoleStore(db).migration_log()
    assert [entry["migration"] for entry in log] == ["attribution_backfill"]
    assert log[0]["detail"]["attributed"] == len(rows)
    assert all(c["unattributed"] == 0 for c in log[0]["detail"]["by_record_type"].values())

    # Idempotent: reopening adds no attribution and no second log entry.
    again = Console.local_development(root, StepClock())
    assert again.local.attributions(world.account_id) == rows
    with _db(root / "local" / "console.sqlite3") as db:
        assert len(console_store.ConsoleStore(db).migration_log()) == 1


def test_backfill_never_attributes_a_label_it_cannot_prove(tmp_path):
    root = tmp_path / "data"
    world = build_world(make_console(root, StepClock()))
    history = [(i.at, i.title, i.actor) for i in make_console(root, StepClock()).local.timeline(world.account_id)]
    # The V0.3 preference names someone else: the recorded "Test salesperson" label is not proven to be the local user.
    _downgrade_to_v03(root, "Jamie Rivera (synthetic)")
    after = Console.local_development(root, StepClock())
    assert after.local.attributions(world.account_id) == []
    timeline = after.local.timeline(world.account_id)
    assert [(i.at, i.title, i.actor) for i in timeline] == history  # every old actor label is preserved
    assert {i.actor_user_id for i in timeline} == {None}
    with _db(root / "local" / "console.sqlite3") as db:
        detail = console_store.ConsoleStore(db).migration_log()[0]["detail"]
    assert detail["attributed"] == 0 and sum(c["unattributed"] for c in detail["by_record_type"].values()) > 0


def test_backfill_leaves_an_organization_with_two_possible_actors_unattributed(tmp_path):
    root, clock = tmp_path / "data", StepClock()
    first = tenant(root, clock, "org-a", "user-a1", "Avery (synthetic)")
    tenant(root, clock, "org-a", "user-a2", "Jordan (synthetic)")
    account_id = first.local.create_account("Two actors (synthetic)")
    with _db(root / "local" / "console.sqlite3") as db:  # the store as Phase 1 left it: V3, no attributions
        db.execute("DROP TABLE attributions")
        db.execute("DROP TABLE migration_log")
        db.execute("DELETE FROM console_meta WHERE key = 'attribution_backfill'")
        db.execute("UPDATE console_meta SET value = '3' WHERE key = 'schema_version'")
    reopened = tenant(root, clock, "org-a", "user-a1", "Avery (synthetic)")
    assert reopened.local.attributions(account_id) == []  # "Avery" names one user today, but names can be reused


def test_v1_identity_store_classifies_only_known_phase1_identities(tmp_path):
    path = tmp_path / IDENTITY_FILE
    v1 = sqlite3.connect(path, isolation_level=None)
    v1.executescript("""
        CREATE TABLE identity_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE organizations (organization_id TEXT PRIMARY KEY, name TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE users (user_id TEXT PRIMARY KEY, organization_id TEXT NOT NULL REFERENCES organizations
            (organization_id), display_name TEXT NOT NULL, created_at TEXT NOT NULL,
            active INTEGER NOT NULL CHECK (active IN (0, 1)), UNIQUE (organization_id, display_name));
        INSERT INTO identity_meta VALUES ('engine', 'BAEC_IDENTITY'), ('schema_version', '1');
        INSERT INTO organizations VALUES ('local', 'Local development', '2026-09-01T00:00:00+00:00'),
                                         ('org-a', 'Org A', '2026-09-01T00:00:00+00:00');
        INSERT INTO users VALUES ('local-user', 'local', 'Jamie Rivera (synthetic)', '2026-09-01T00:00:00+00:00', 1),
                                 ('synthetic-seed@local', 'local', 'Synthetic demo seed (not a real person)',
                                  '2026-09-01T00:00:00+00:00', 1),
                                 ('user-a', 'org-a', 'Local user', '2026-09-01T00:00:00+00:00', 1);
    """)
    v1.close()
    for _ in range(2):  # idempotent
        with identity.identity_store(path) as store:
            kinds = {u: store.user(u).kind for u in ("local-user", "synthetic-seed@local", "user-a")}
            names = {u: store.user(u).display_name for u in kinds}
            audit = store.audit()
    assert kinds == {"local-user": UserKind.LOCAL_DEVELOPMENT, "synthetic-seed@local": UserKind.SYSTEM_SEED,
                     # Named "Local user", but classification is by stored id, never by name. Unknown: never eligible.
                     "user-a": UserKind.LEGACY_UNCLASSIFIED}
    assert names["local-user"] == "Jamie Rivera (synthetic)" and names["user-a"] == "Local user"
    assert [(e.event_type, e.user_id) for e in audit] == [("KIND_CLASSIFIED_BY_MIGRATION", u) for u in kinds]


# --- 2B: principal freshness ----------------------------------------------------------------------------------------

READS = {
    "account view": lambda c, w: c.local.account_view(w.account_id),
    "timeline": lambda c, w: c.local.timeline(w.account_id),
    "contacts": lambda c, w: c.local.crm_contacts(w.account_id),
    "preparation": lambda c, w: c.local.preparation_view(w.preparation_id),
    "attributions": lambda c, w: c.local.attributions(w.account_id),
    "has account": lambda c, w: c.local.has_account(w.account_id),
}
LISTS = {
    "account ids": lambda c, w: c.local.account_ids(),
    "synthetic account ids": lambda c, w: c.synthetic.account_ids(),
    "account views": lambda c, w: c.account_views(),
    "review queue": lambda c, w: c.review_queue(),
    "activity": lambda c, w: c.activity(),
    "preparations": lambda c, w: c.preparations(),
    "dashboard": lambda c, w: c.dashboard(),
    "tasks": lambda c, w: c.local.crm_tasks(),
    "owner": lambda c, w: c.owner(w.account_id),
}
WRITES = {
    "create account": lambda c, w: c.local.create_account("Blocked (synthetic)"),
    "add contact": lambda c, w: c.local.crm_add_contact(w.account_id, name="Blocked (synthetic)"),
    "complete task": lambda c, w: c.local.crm_complete_task(w.task_id),
    "record interaction": lambda c, w: c.local.record_interaction(
        w.account_id, buyer_role="Buyer", occurred_at=AT, buyer_statement="Synthetic statement."),
    "discard draft": lambda c, w: c.local.discard_draft(w.draft_id),
    "block preparation": lambda c, w: c.local.block_preparation(w.review_id, reason="x"),
    "rename self": lambda c, w: c.set_actor("Someone new (synthetic)"),
    "load demo": lambda c, w: demo_seed.load(c, "beginning"),
    "reset demo": lambda c, w: c.reset_synthetic(),
    "seed service": lambda c, w: c.seed_service(),
}


@pytest.fixture
def running(tmp_path):
    root, clock = tmp_path / "data", StepClock()
    world = build_world(tenant(root, clock, "org-a", "user-a", "Avery (synthetic)"))
    return root, world


def _state(world) -> tuple:
    s = world.console.local
    return ([(i.at, i.title, i.actor) for i in s.timeline(world.account_id)], s.crm_contacts(world.account_id),
            s.crm_tasks(), s.attributions(world.account_id), world.console.synthetic.account_ids())


def test_a_deactivated_user_loses_every_access_without_a_restart(running):
    root, world = running
    console = world.console
    for operation in {**READS, **LISTS}.values():  # normal access first
        operation(console, world)
    before = _state(world)
    _set_active(root, "user-a", False)
    for name, operation in {**READS, **LISTS, **WRITES}.items():
        with pytest.raises(PrincipalInvalid):  # the same Console object: never rebuilt
            operation(console, world)
    # Reactivation is an explicit identity-store decision; the same running console then works again on its next
    # operation. Nothing was read or written while the user was inactive.
    _set_active(root, "user-a", True)
    assert _state(world) == before
    console.local.crm_add_contact(world.account_id, name="After reactivation (synthetic)")


def test_freshness_refuses_before_reading_and_says_nothing_about_records(running):
    root, world = running
    _set_active(root, "user-a", False)
    with pytest.raises(PrincipalInvalid) as refused:
        world.console.local.account_view("ACC-0000DEADBEEF")
    assert "not active" in str(refused.value)  # the same refusal for any record id, real or not
    assert isinstance(refused.value, ConsoleError) and not isinstance(refused.value, AccessDenied)


def test_a_principal_whose_organization_no_longer_matches_is_refused(running):
    root, world = running
    with _db(root / IDENTITY_FILE) as db, pytest.raises(sqlite3.IntegrityError, match="cannot change organization"):
        db.execute("UPDATE users SET organization_id = 'org-b' WHERE user_id = 'user-a'")
    tenant(root, StepClock(), "org-b", "user-b", "Blake (synthetic)")
    tampered = world.console.local
    tampered.principal = replace(tampered.principal, organization_id="org-b")  # a principal that is not the user
    with pytest.raises(PrincipalInvalid):
        tampered.account_ids()


def test_a_display_name_change_never_invalidates_the_principal(running):
    root, world = running
    _rename(root, "user-a", "Avery Renamed (synthetic)")
    assert world.console.local.account_ids() == [world.account_id]
    assert world.console.actor == "Avery Renamed (synthetic)"


@pytest.mark.parametrize("cls", (ConsoleService, Console))
def test_every_public_operation_passes_through_the_freshness_guard(cls):
    """A method added later is guarded automatically; this proves no public method escaped the wrapper."""
    public = {n for n, v in vars(cls).items() if not n.startswith("_") and inspect.isfunction(v)}
    assert public, cls
    unguarded = {n for n in public if not getattr(vars(cls)[n], "principal_guarded", False)}
    assert unguarded == (UNGUARDED_METHODS & public)
    assert UNGUARDED_METHODS == {"ids", "now"}  # neither touches stored data


# --- cross-user cache ------------------------------------------------------------------------------------------------


def test_a_console_for_one_principal_never_serves_another(tmp_path):
    root, clock = tmp_path / "data", StepClock()
    a = tenant(root, clock, "org-a", "user-a", "Avery (synthetic)")
    b = tenant(root, clock, "org-b", "user-b", "Blake (synthetic)")
    a_account, b_account = a.local.create_account("A (synthetic)"), b.local.create_account("B (synthetic)")
    assert a is not b and a.local is not b.local
    assert (a.local.account_ids(), b.local.account_ids()) == ([a_account], [b_account])
    for console, foreign in ((a, b_account), (b, a_account)):
        with pytest.raises(AccessDenied):
            console.local.account_view(foreign)
    # Deactivating B has no effect on A's console, and A's console never becomes B's.
    _set_active(root, "user-b", False)
    assert a.local.account_ids() == [a_account]
    with pytest.raises(PrincipalInvalid):
        b.local.account_ids()


def test_the_app_caches_consoles_by_immutable_identity_only():
    """Phase 4: the only process-wide cached console is the development one, keyed by path and mode (one principal,
    the local-development user). Production consoles are never put in a process-wide cache: each is kept in its own
    browser session with the exact signed-in identity it was opened for (test_production_auth.py)."""
    tree = ast.parse((CONSOLE / "app.py").read_text(encoding="utf-8"))
    cached = [f for f in ast.walk(tree) if isinstance(f, ast.FunctionDef)
              and any(getattr(d, "attr", None) == "cache_resource" for d in f.decorator_list)]
    assert [f.name for f in cached] == ["_console"]
    assert [a.arg for a in cached[0].args.args] == ["path", "mode"]  # no display name, no user claim


# --- 2D: synthetic demo isolation -----------------------------------------------------------------------------------


def test_two_organizations_load_reset_and_inspect_their_demos_independently(tmp_path):
    root, clock = tmp_path / "data", StepClock()
    a = tenant(root, clock, "org-a", "user-a", "Avery (synthetic)")
    b = tenant(root, clock, "org-b", "user-b", "Blake (synthetic)")
    assert a.synthetic.directory != b.synthetic.directory

    a_demo = demo_seed.load(a, "completed")
    b_demo = demo_seed.load(b, "beginning")  # not blocked by A's demo
    assert (a.synthetic.account_ids(), b.synthetic.account_ids()) == ([a_demo], [b_demo])
    a_view, b_view = a.synthetic.account_view(a_demo), b.synthetic.account_view(b_demo)
    assert a_view.preparations and not b_view.baecs  # each organization sees its own mode
    for console, foreign in ((a, b_demo), (b, a_demo)):
        with pytest.raises(AccessDenied):
            console.synthetic.account_view(foreign)
    a_history = [(i.at, i.title) for i in a.synthetic.timeline(a_demo)]

    b.reset_synthetic()  # B's reset leaves A's demo exactly as it was
    assert b.synthetic.account_ids() == [] and a.synthetic.account_ids() == [a_demo]
    assert [(i.at, i.title) for i in a.synthetic.timeline(a_demo)] == a_history
    b_again = demo_seed.load(b, "completed")
    a.reset_synthetic()  # and A's reset leaves B's
    assert a.synthetic.account_ids() == [] and b.synthetic.account_ids() == [b_again]
    a_again = demo_seed.load(a, "beginning")
    assert (a.synthetic.account_ids(), b.synthetic.account_ids()) == ([a_again], [b_again])
    # Seeded actions are each organization's own seed user, never another organization's.
    for console, account_id in ((a, a_again), (b, b_again)):
        assert {r["user_id"] for r in console.synthetic.attributions(account_id)} == {
            identity.seed_user_id(console.principal.organization_id)}
    # Local (non-demo) accounts are untouched by any of it.
    assert a.local.account_ids() == [] and b.local.account_ids() == []


def test_the_local_organization_keeps_the_v03_synthetic_location(tmp_path):
    console = make_console(tmp_path / "data", StepClock())
    assert console.synthetic.directory == tmp_path / "data" / "synthetic"


# --- 2E: identity kinds and authentication eligibility --------------------------------------------------------------


def test_only_active_human_users_are_eligible_for_authentication(tmp_path):
    root = tmp_path / "data"
    local = make_console(root, StepClock())
    local.seed_service()
    human = tenant(root, StepClock(), "org-a", "user-a", "Avery (synthetic)")
    with identity.identity_store(root / IDENTITY_FILE) as store:
        users = {u: store.user(u) for u in (identity.LOCAL_USER_ID, identity.seed_user_id("local"), "user-a")}
        assert {u: (x.kind, x.authentication_eligible) for u, x in users.items()} == {
            identity.LOCAL_USER_ID: (UserKind.LOCAL_DEVELOPMENT, False),
            identity.seed_user_id("local"): (UserKind.SYSTEM_SEED, False),
            "user-a": (UserKind.HUMAN, True)}
        assert store.authenticated_principal("user-a") == human.principal
        for user_id in (identity.LOCAL_USER_ID, identity.seed_user_id("local"), "no-such-user"):
            with pytest.raises(identity.IdentityError, match="not eligible"):
                store.authenticated_principal(user_id)
        store.set_active("user-a", False, at=AT)
        assert not store.user("user-a").authentication_eligible
        with pytest.raises(identity.IdentityError):
            store.authenticated_principal("user-a")


def test_a_system_identity_can_never_be_made_eligible(tmp_path):
    root = tmp_path / "data"
    make_console(root, StepClock()).seed_service()
    with _db(root / IDENTITY_FILE) as db:
        for user_id in (identity.LOCAL_USER_ID, identity.seed_user_id("local")):
            with pytest.raises(sqlite3.IntegrityError, match="kind never changes"):
                db.execute("UPDATE users SET kind = 'HUMAN' WHERE user_id = ?", (user_id,))
    with identity.identity_store(root / IDENTITY_FILE) as store:
        with pytest.raises(identity.IdentityError, match="LOCAL_DEVELOPMENT"):  # re-provisioning cannot relabel it
            store.ensure_user(identity.LOCAL_USER_ID, identity.LOCAL_ORGANIZATION_ID, "x", AT, kind=HUMAN)
        with pytest.raises(identity.IdentityError, match="not eligible"):
            store.authenticated_principal(identity.seed_user_id("local"))
    with pytest.raises(deployment.ProductionAccessRefused):  # Phase 4: production never takes a user id at all
        deployment.runtime_console(root, mode=deployment.DeploymentMode.PRODUCTION)


def test_a_user_needs_an_explicit_kind(tmp_path):
    with identity.identity_store(tmp_path / IDENTITY_FILE) as store:
        store.ensure_organization("org-a", "Org A", AT)
        with pytest.raises(identity.IdentityError, match="explicit kind"):
            store.ensure_user("user-a", "org-a", "Avery", AT, kind="HUMAN")


# --- 2F: deployment mode --------------------------------------------------------------------------------------------


NOT_CONFIGURED = {"unset": None, "empty": "", "unknown": "staging", "abbreviated": "dev", "capitalised": "Production",
                  "padded": " development", "trailing newline": "development\n"}


def test_deployment_mode_must_be_exactly_development_or_production():
    assert deployment.deployment_mode({"BAEC_DEPLOYMENT_MODE": "development"}) is deployment.DeploymentMode.DEVELOPMENT
    assert deployment.deployment_mode({"BAEC_DEPLOYMENT_MODE": "production"}) is deployment.DeploymentMode.PRODUCTION
    for name, value in NOT_CONFIGURED.items():
        environ = {} if value is None else {"BAEC_DEPLOYMENT_MODE": value}
        with pytest.raises(deployment.DeploymentModeNotConfigured, match="not configured"):
            deployment.deployment_mode(environ)


@pytest.mark.parametrize("case", sorted(NOT_CONFIGURED))
def test_an_unconfigured_mode_fails_closed_and_creates_nothing(tmp_path, monkeypatch, case):
    root = tmp_path / "data"
    if NOT_CONFIGURED[case] is None:
        monkeypatch.delenv("BAEC_DEPLOYMENT_MODE", raising=False)
    else:
        monkeypatch.setenv("BAEC_DEPLOYMENT_MODE", NOT_CONFIGURED[case])
    with pytest.raises(deployment.DeploymentModeNotConfigured):
        Console.local_development(root, StepClock())  # never falls back to development
    with pytest.raises(deployment.DeploymentModeNotConfigured):
        deployment.runtime_console(root, mode=deployment.deployment_mode())
    assert not root.exists()  # no identity store, no local principal, no workspace or CRM files


def test_explicit_development_mode_permits_the_local_development_principal(tmp_path, monkeypatch):
    monkeypatch.setenv("BAEC_DEPLOYMENT_MODE", "development")
    console = deployment.runtime_console(tmp_path / "data", mode=deployment.deployment_mode(), clock=StepClock())
    assert (console.principal.user_id, console.principal.organization_id) == (identity.LOCAL_USER_ID,
                                                                             identity.LOCAL_ORGANIZATION_ID)
    console.local.create_account("Development account (synthetic)")


@pytest.mark.parametrize("case", sorted(NOT_CONFIGURED))
def test_the_app_with_an_unconfigured_mode_shows_a_configuration_error_and_no_data(tmp_path, monkeypatch, case):
    from streamlit.testing.v1 import AppTest
    root = tmp_path / "data"
    monkeypatch.setenv("BAEC_CONSOLE_DATA_DIR", str(root))
    if NOT_CONFIGURED[case] is None:
        monkeypatch.delenv("BAEC_DEPLOYMENT_MODE", raising=False)
    else:
        monkeypatch.setenv("BAEC_DEPLOYMENT_MODE", NOT_CONFIGURED[case])
    at = AppTest.from_file(str(CONSOLE / "app.py"), default_timeout=30)
    at.run()
    assert not at.exception
    assert len(at.error) == 1 and "BAEC_DEPLOYMENT_MODE" in at.error[0].value
    assert not at.button and not at.text_input  # no navigation, no workflows, no demo controls
    assert not root.exists()


def test_production_without_an_authenticated_human_refuses_and_never_creates_the_local_user(tmp_path, monkeypatch):
    root = tmp_path / "data"
    monkeypatch.setenv("BAEC_DEPLOYMENT_MODE", "production")
    with pytest.raises(deployment.ProductionAccessRefused):
        deployment.runtime_console(root, mode=deployment.DeploymentMode.PRODUCTION)
    with pytest.raises(deployment.DeploymentError):
        Console.local_development(root, StepClock())  # no fallback, even when called directly
    with pytest.raises(deployment.DeploymentError):
        deployment.runtime_console(root, mode=deployment.DeploymentMode.DEVELOPMENT)
    assert not root.exists()  # nothing was provisioned or opened, so nothing can be impersonated


def test_production_refuses_the_local_development_user_even_once_it_exists(tmp_path, monkeypatch):
    root = tmp_path / "data"
    make_console(root, StepClock())  # a development run created local-user earlier
    monkeypatch.setenv("BAEC_DEPLOYMENT_MODE", "production")
    with pytest.raises(deployment.ProductionAccessRefused):  # no production run on the SQLite identity store at all
        deployment.runtime_console(root, mode=deployment.DeploymentMode.PRODUCTION)
    with pytest.raises(identity.IdentityError, match="not eligible"):  # nor by constructing a Console directly
        Console(root, StepClock(), principal=identity.Principal(identity.LOCAL_USER_ID, identity.LOCAL_ORGANIZATION_ID,
                                                                ACTOR), authentication_required=True)


def test_production_never_opens_a_console_for_a_user_id_even_an_eligible_humans(tmp_path, monkeypatch):
    """Phase 4: production opens a console only for a verified sign-in linked to a PostgreSQL user (production.py);
    there is no way to name a user id, so an eligible HUMAN on the SQLite store never gets a production console."""
    from baec_console import production
    root = tmp_path / "data"
    tenant(root, StepClock(), "org-a", "user-a", "Avery (synthetic)")
    before = sorted(p.relative_to(root) for p in root.rglob("*"))
    monkeypatch.setenv("BAEC_DEPLOYMENT_MODE", "production")
    with pytest.raises(deployment.ProductionAccessRefused):
        deployment.runtime_console(root, mode=deployment.DeploymentMode.PRODUCTION)
    with pytest.raises(storage.StorageRefused, match="BAEC_DATABASE_URL is not set"):
        production.open_runtime(None)
    assert sorted(p.relative_to(root) for p in root.rglob("*")) == before  # no SQLite store created or opened


def test_an_authentication_required_console_accepts_an_eligible_human_and_keeps_checking_it(tmp_path):
    """The authentication gate production will use once its storage exists: an eligible HUMAN principal is accepted
    and re-checked on every operation."""
    root = tmp_path / "data"
    human = tenant(root, StepClock(), "org-a", "user-a", "Avery (synthetic)")
    console = Console(root, StepClock(), principal=human.principal, authentication_required=True)
    account_id = console.local.create_account("Authenticated account (synthetic)")
    _set_active(root, "user-a", False)
    with pytest.raises(PrincipalInvalid):
        console.local.account_view(account_id)


def test_the_app_in_production_mode_shows_a_refusal_and_no_data(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest
    root = tmp_path / "data"
    monkeypatch.setenv("BAEC_CONSOLE_DATA_DIR", str(root))
    monkeypatch.setenv("BAEC_DEPLOYMENT_MODE", "production")
    monkeypatch.delenv("BAEC_DATABASE_URL", raising=False)
    at = AppTest.from_file(str(CONSOLE / "app.py"), default_timeout=30)
    at.run()
    assert not at.exception
    assert [e.value for e in at.error] == [f"Production storage refused: BAEC_DATABASE_URL is not set. Nothing was "
                                           f"read or changed."]  # the startup checks, before any sign-in page
    assert not at.button and not at.text_input  # no sign-in, navigation, name field, or demo controls
    assert not root.exists()


@pytest.mark.parametrize("kind", [k for k in UserKind if k is not HUMAN])
def test_no_non_human_identity_satisfies_production_authentication(tmp_path, monkeypatch, kind):
    root = tmp_path / "data"
    identity.provision(root / IDENTITY_FILE, organization_id="org-a", organization_name="Org A (synthetic)",
                       user_id=f"user-{kind.value.lower()}", display_name=f"{kind.value} (synthetic)", kind=kind,
                       now=AT)
    monkeypatch.setenv("BAEC_DEPLOYMENT_MODE", "production")
    with pytest.raises(deployment.ProductionAccessRefused):  # no user id is ever accepted in production
        deployment.runtime_console(root, mode=deployment.DeploymentMode.PRODUCTION)
    with identity.identity_store(root / IDENTITY_FILE) as store:
        assert not store.user(f"user-{kind.value.lower()}").authentication_eligible
        with pytest.raises(identity.IdentityError, match="not eligible"):
            store.authenticated_principal(f"user-{kind.value.lower()}")
    assert not (root / "local").exists()  # refused before any workspace or CRM store is opened


def test_the_app_shows_a_refusal_once_its_user_is_deactivated(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest
    root = tmp_path / "data"
    make_console(root, StepClock())
    monkeypatch.setenv("BAEC_CONSOLE_DATA_DIR", str(root))
    at = AppTest.from_file(str(CONSOLE / "app.py"), default_timeout=30)
    at.run()
    assert not at.exception and not at.error
    _set_active(root, identity.LOCAL_USER_ID, False)
    at.run()
    assert not at.exception
    assert any("not active" in e.value for e in at.error)
