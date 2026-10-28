"""V0.4 Phase 1: organization ownership, principal identity, and the cross-tenant authorization boundary.

Two organizations share one data root (the same workspace SQLite files), which is the case tenancy must survive.
All data is synthetic. These are software tests of the product boundary, not evidence for the theory.
"""

from __future__ import annotations

import hashlib
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from baec_app.data.records import SourceInteraction
from baec_app.domain.enums import AccountState, ReviewAnswer
from baec_app.domain.models import Account
from baec_app.engine3.enums import HumanReviewDecision
from baec_console import demo_seed, identity
from baec_console.console_service import IDENTITY_FILE, NOT_ACCESSIBLE, AccessDenied, Console, ConsoleError
from baec_console.engine2_adapter import ObservationForm

from .conftest import ATTEST, REQUIRED, StepClock, make_console, permitted_request, verified

CONSOLE = Path(__file__).resolve().parents[1]
AT = datetime(2026, 9, 5, 9, 0, tzinfo=timezone.utc)


def tenant(root: Path, clock, organization_id: str, user_id: str, display_name: str) -> Console:
    principal = identity.provision(root / IDENTITY_FILE, organization_id=organization_id,
                                   organization_name=f"Organization {organization_id} (synthetic)", user_id=user_id,
                                   display_name=display_name, kind=identity.UserKind.HUMAN, now=AT)
    return Console(root, clock, principal=principal)


@dataclass
class World:
    """One organization's records, reached through the full Remember -> Prepare workflow plus CRM records."""

    console: Console
    account_id: str
    interaction_id: str
    draft_id: str
    baec_id: str
    judgment_id: int
    source_id: str
    observation_id: str
    candidate_id: str
    review_id: str
    request_id: str
    preparation_id: str
    artifact_id: str
    contact_id: str
    task_id: str
    activity_id: str


def build_world(console: Console) -> World:
    s = console.local
    account_id, baec_id, result, review_id = verified(console)
    request_id = s.request_preparation(review_id, permitted_request(result))
    preparation_id = s.generate_preparation(request_id)
    view = s.account_view(account_id)
    interaction_id = view.interactions[0][0].interaction_id
    draft_id = s.capture_condition(account_id, interaction_id, demo_seed.condition_form())  # left pending
    judgment_id = s.record_dormancy_judgment(baec_id, plausibility=ReviewAnswer.YES, addressability=ReviewAnswer.YES,
                                             notes="")
    contact_id = s.crm_add_contact(account_id, name="Pat Lee (synthetic)")
    task_id = s.crm_add_task(account_id, contact_id=contact_id, title="Follow up", due_date=None, priority="NORMAL")
    activity_id = s.crm_add_activity(account_id, contact_id=contact_id, interaction_type="CALL", occurred_at=AT,
                                     raw_notes="Synthetic call notes")
    artifact_id = next(o["artifact"]["artifact_id"] for o in s.preparation_view(preparation_id)["preparation"]["artifacts"]
                       if o["artifact"] and o["report"]["allowed"])
    return World(console, account_id, interaction_id, draft_id, baec_id, judgment_id, next(iter(view.sources)),
                 result.observation_id, result.candidate_id, review_id, request_id, preparation_id, artifact_id,
                 contact_id, task_id, activity_id)


@pytest.fixture(scope="module")
def worlds(tmp_path_factory) -> dict[str, World]:
    root = tmp_path_factory.mktemp("shared") / "data"
    clock = StepClock()
    a = tenant(root, clock, "org-a", "user-a", "Avery (Org A, synthetic)")
    b = tenant(root, clock, "org-b", "user-b", "Blake (Org B, synthetic)")
    return {"A": build_world(a), "B": build_world(b)}


def _observation(source_id: str, corrects: str = "") -> ObservationForm:
    return ObservationForm(source_id=source_id, observed_at=datetime(2026, 10, 13, 10, 0, tzinfo=timezone.utc),
                           content="Synthetic renewal notice text.", corrects_observation_id=corrects,
                           flag_for_review=True)


# Every operation a principal could aim at another organization's records. `t` is the target (other organization),
# `own` is the acting principal's own world (for mixed attempts that pair an owned account with a foreign record).
OPERATIONS = {
    # reads
    "read account": lambda s, t, own: s.account_view(t.account_id),
    "read account timeline": lambda s, t, own: s.timeline(t.account_id),
    "read contacts": lambda s, t, own: s.crm_contacts(t.account_id),
    "read tasks of account": lambda s, t, own: s.crm_tasks(account_id=t.account_id),
    "read activities": lambda s, t, own: s.crm_activities(t.account_id),
    "read monitoring handoff": lambda s, t, own: s.handoff(t.baec_id),
    "preview condition": lambda s, t, own: s.preview_condition(t.draft_id),
    "preview account state change": lambda s, t, own: s.preview_state_change(
        t.account_id, AccountState.NO_PLAUSIBLE_PATH, {}),
    "read correspondence review packet": lambda s, t, own: s.review_packet(t.candidate_id),
    "read preparation snapshot": lambda s, t, own: s.preparation_snapshot(t.review_id),
    "read preparation eligibility": lambda s, t, own: s.preparation_eligibility(t.review_id),
    "read preparation": lambda s, t, own: s.preparation_view(t.preparation_id),
    "read attributions": lambda s, t, own: s.attributions(t.account_id),
    # account and CRM writes
    "add buyer interaction": lambda s, t, own: s.record_interaction(
        t.account_id, buyer_role="Buyer", occurred_at=AT, buyer_statement="Synthetic statement."),
    "add contact": lambda s, t, own: s.crm_add_contact(t.account_id, name="Intruder (synthetic)"),
    "add task": lambda s, t, own: s.crm_add_task(t.account_id, contact_id=None, title="x", due_date=None,
                                                 priority="NORMAL"),
    "add task to own account with foreign contact": lambda s, t, own: s.crm_add_task(
        own.account_id, contact_id=t.contact_id, title="x", due_date=None, priority="NORMAL"),
    "complete task": lambda s, t, own: s.crm_complete_task(t.task_id),
    "log activity": lambda s, t, own: s.crm_add_activity(
        t.account_id, contact_id=None, interaction_type="CALL", occurred_at=AT, raw_notes="x"),
    "log activity on own account with foreign contact": lambda s, t, own: s.crm_add_activity(
        own.account_id, contact_id=t.contact_id, interaction_type="CALL", occurred_at=AT, raw_notes="x"),
    "link interaction": lambda s, t, own: s.crm_link_interaction(
        t.interaction_id, t.account_id, contact_id=None, interaction_type="CALL"),
    "link foreign interaction to own account": lambda s, t, own: s.crm_link_interaction(
        t.interaction_id, own.account_id, contact_id=None, interaction_type="CALL"),
    "attach Engine 1 interaction to activity": lambda s, t, own: s.crm_attach_engine1_interaction(
        t.activity_id, t.interaction_id),
    "attach foreign interaction to own activity": lambda s, t, own: s.crm_attach_engine1_interaction(
        own.activity_id, t.interaction_id),
    # Engine 1: conditions and account state
    "capture condition": lambda s, t, own: s.capture_condition(t.account_id, t.interaction_id,
                                                               demo_seed.condition_form()),
    "capture condition on own account from foreign interaction": lambda s, t, own: s.capture_condition(
        own.account_id, t.interaction_id, demo_seed.condition_form()),
    "confirm condition": lambda s, t, own: s.confirm_condition(t.draft_id, displayed_fingerprint="x"),
    "record condition as not confirmable": lambda s, t, own: s.record_not_confirmable(
        t.draft_id, displayed_fingerprint="x"),
    "discard condition draft": lambda s, t, own: s.discard_draft(t.draft_id),
    "record dormancy judgment": lambda s, t, own: s.record_dormancy_judgment(
        t.baec_id, plausibility=ReviewAnswer.YES, addressability=ReviewAnswer.YES, notes=""),
    "change account state": lambda s, t, own: s.change_account_state(
        t.account_id, AccountState.CONDITIONALLY_DORMANT, {"baec_id": t.baec_id, "judgment_id": t.judgment_id}),
    "change own account state with foreign condition": lambda s, t, own: s.change_account_state(
        own.account_id, AccountState.CONDITIONALLY_DORMANT, {"baec_id": t.baec_id, "judgment_id": t.judgment_id}),
    "change own account state with foreign evidence": lambda s, t, own: s.change_account_state(
        own.account_id, AccountState.ACTIVE_OPPORTUNITY,
        {"eval_interaction": t.interaction_id, "eval_excerpt": demo_seed.BUYER_STATEMENT}),
    # Engine 2: monitoring, observations, correspondence
    "configure monitoring": lambda s, t, own: s.start_monitoring(
        t.account_id, t.baec_id, target_entities=("x",), required=REQUIRED, sources=()),
    "configure monitoring of foreign condition on own account": lambda s, t, own: s.start_monitoring(
        own.account_id, t.baec_id, target_entities=("x",), required=REQUIRED, sources=()),
    "add observation": lambda s, t, own: s.add_observation(t.baec_id, _observation(t.source_id)),
    "add observation to own condition from a foreign source": lambda s, t, own: s.add_observation(
        own.baec_id, _observation(t.source_id)),
    "add observation correcting a foreign observation": lambda s, t, own: s.add_observation(
        own.baec_id, _observation(own.source_id, corrects=t.observation_id)),
    "queue observation for review": lambda s, t, own: s.queue_observation_for_review(t.baec_id, t.observation_id),
    "queue foreign observation under own condition": lambda s, t, own: s.queue_observation_for_review(
        own.baec_id, t.observation_id),
    "review correspondence": lambda s, t, own: s.submit_correspondence_review(
        t.candidate_id, displayed_context_digest="x", findings={}, notes={}, sufficiency="YES"),
    # Engine 3: preparation and human preparation review
    "block preparation": lambda s, t, own: s.block_preparation(t.review_id, reason="x"),
    "unblock preparation": lambda s, t, own: s.unblock_preparation(t.review_id, reason="x"),
    "request preparation": lambda s, t, own: s.request_preparation(t.review_id, {}),
    "generate preparation": lambda s, t, own: s.generate_preparation(t.request_id),
    "edit artifact": lambda s, t, own: s.edit_artifact(t.preparation_id, t.artifact_id, []),
    "accept artifact": lambda s, t, own: s.review_artifact(
        t.preparation_id, t.artifact_id, HumanReviewDecision.ACCEPT_AS_DRAFTED, attestations=ATTEST),
    "accept edited artifact": lambda s, t, own: s.review_artifact(
        t.preparation_id, t.artifact_id, HumanReviewDecision.EDIT, attestations=ATTEST, edit_id="x"),
    "reject artifact": lambda s, t, own: s.review_artifact(
        t.preparation_id, t.artifact_id, HumanReviewDecision.REJECT, attestations={}),
}

DIRECTIONS = (("A", "B"), ("B", "A"))


def _snapshot(world: World) -> tuple:
    """Everything the target organization can see about its own account, read as that organization."""
    s = world.console.local
    view = s.account_view(world.account_id)
    return ([(i.at, i.title, i.actor, i.detail) for i in s.timeline(world.account_id)],
            view.account.state, len(view.drafts), [d["resolution"] for d in view.drafts],
            len(view.observations), len(view.candidates), len(view.requests),
            [(p["preparation_id"], sorted(p["reviews"]), len(p["edits"])) for p in view.preparations],
            s.crm_contacts(world.account_id), s.crm_tasks(account_id=world.account_id),
            s.crm_activities(world.account_id), s.crm_interaction_link(world.interaction_id))


@pytest.mark.parametrize("operation", sorted(OPERATIONS))
@pytest.mark.parametrize("actor,target", DIRECTIONS, ids=("A-to-B", "B-to-A"))
def test_cross_tenant_action_is_refused(worlds, actor, target, operation):
    t, own = worlds[target], worlds[actor]
    before = _snapshot(t)
    with pytest.raises(AccessDenied) as refused:
        OPERATIONS[operation](own.console.local, t, own)
    assert str(refused.value) == NOT_ACCESSIBLE
    assert _snapshot(t) == before  # nothing was written to, or about, the other organization


GUESSED = World(console=None, account_id="ACC-0000DEADBEEF", interaction_id="INT-0000DEADBEEF",
                draft_id="DRAFT-0000DEADBEEF", baec_id="BAEC-0000DEADBEEF", judgment_id=999999,
                source_id="SRC-0000DEADBEEF", observation_id="OBS-0000DEADBEEF", candidate_id="CC-0000DEADBEEF",
                review_id="HCR-0000DEADBEEF", request_id="PREQ-0000DEADBEEF", preparation_id="PREP-0000DEADBEEF",
                artifact_id="EMAIL", contact_id="CON-0000DEADBEEF", task_id="TASK-0000DEADBEEF",
                activity_id="ACT-0000DEADBEEF")


@pytest.mark.parametrize("operation", sorted(OPERATIONS))
def test_a_guessed_id_is_refused_exactly_like_a_foreign_one(worlds, operation):
    """Unknown and inaccessible records are indistinguishable: same error type, same message."""
    own = worlds["A"]
    with pytest.raises(AccessDenied) as refused:
        OPERATIONS[operation](own.console.local, GUESSED, own)
    assert str(refused.value) == NOT_ACCESSIBLE


@pytest.mark.parametrize("actor,target", DIRECTIONS, ids=("A-to-B", "B-to-A"))
def test_other_organization_is_absent_from_every_list(worlds, actor, target):
    c, t = worlds[actor].console, worlds[target]
    assert t.account_id not in c.local.account_ids() and not c.local.has_account(t.account_id)
    assert {v.account.account_id for v in c.account_views()} == {worlds[actor].account_id}
    queue = c.review_queue()
    assert {r["account_id"] for r in queue["pending"] + queue["completed"]} == {worlds[actor].account_id}
    assert {i.account_id for i in c.activity()} == {worlds[actor].account_id}
    assert t.preparation_id not in c.local.preparation_ids()
    assert {p["request"]["account_id"] for p in c.preparations()} == {worlds[actor].account_id}
    assert {task["task_id"] for task in c.local.crm_tasks()} == {worlds[actor].task_id}
    assert c.dashboard()["accounts"] == 1
    assert c.local.crm_interaction_link(t.interaction_id) is None
    with pytest.raises(AccessDenied):
        c.owner(t.account_id)
    with pytest.raises(AccessDenied):
        c.owner_of_preparation(t.preparation_id)


def test_same_organization_operations_still_succeed(worlds):
    a = worlds["A"]
    s = a.console.local
    assert s.account_view(a.account_id).account.account_id == a.account_id
    assert a.console.owner(a.account_id) is s
    contact_id = s.crm_add_contact(a.account_id, name="Sam Ortiz (synthetic)")
    task_id = s.crm_add_task(a.account_id, contact_id=contact_id, title="Call back", due_date=None, priority="HIGH")
    s.crm_complete_task(task_id)
    assert {t["task_id"]: t["status"] for t in s.crm_tasks(account_id=a.account_id)}[task_id] == "DONE"
    s.preview_condition(a.draft_id)


def test_users_in_one_organization_share_its_data_and_each_action_names_its_user(tmp_path):
    root, clock = tmp_path / "data", StepClock()
    first = tenant(root, clock, "org-a", "user-a1", "Avery (synthetic)")
    second = tenant(root, clock, "org-a", "user-a2", "Jordan (synthetic)")
    account_id = first.local.create_account("Shared account (synthetic)")
    assert second.local.account_ids() == [account_id]
    contact_id = second.local.crm_add_contact(account_id, name="Pat Lee (synthetic)")
    assert {c["contact_id"]: c["created_by"] for c in first.local.crm_contacts(account_id)} == {
        contact_id: "Jordan (synthetic)"}
    interaction_id = second.local.record_interaction(
        account_id, buyer_role=demo_seed.BUYER_ROLE, occurred_at=AT, buyer_statement=demo_seed.BUYER_STATEMENT,
        seller_notes=demo_seed.SELLER_NOTES)
    draft_id = first.local.capture_condition(account_id, interaction_id, demo_seed.condition_form())
    baec_id = second.local.confirm_condition(draft_id, displayed_fingerprint=second.local.preview_condition(draft_id)[
        "fingerprint"])
    view = first.local.account_view(account_id)
    assert view.current_baec.baec_id == baec_id
    assert view.current_baec.confirmation.authorized_by == "Jordan (synthetic)"  # the confirming user, not the creator
    actors = {i.title: i.actor for i in first.local.timeline(account_id) if i.actor_kind == "Human"}
    assert actors["Account created"] == "Avery (synthetic)"
    assert actors["Buyer interaction recorded"] == "Jordan (synthetic)"
    with _db(root / "local" / "console.sqlite3") as db:
        assert db.execute("SELECT organization_id, recorded_by_user_id, basis FROM account_ownership").fetchall() == [
            ("org-a", "user-a1", "CREATED")]


# --- identity -------------------------------------------------------------------------------------------------


def test_console_refuses_a_principal_that_is_not_an_active_stored_user(tmp_path):
    root, clock = tmp_path / "data", StepClock()
    a = tenant(root, clock, "org-a", "user-a", "Avery (synthetic)")
    tenant(root, clock, "org-b", "user-b", "Blake (synthetic)")
    for forged in (identity.Principal("user-x", "org-a", "Nobody"),  # no such user
                   replace(a.principal, organization_id="org-b")):  # a real user claiming another organization
        with pytest.raises(identity.IdentityError):
            Console(root, clock, principal=forged)
    # The display name is a label, not identity (Phase 2): a principal carrying a stale or wrong label is still that
    # user, and the console records the user's stored name, never the label the principal carried.
    relabelled = Console(root, clock, principal=replace(a.principal, display_name="Someone else"))
    assert relabelled.actor == "Avery (synthetic)"
    with identity.identity_store(root / IDENTITY_FILE) as store:
        store.set_active("user-a", False, at=AT)
    with pytest.raises(identity.IdentityError):
        Console(root, clock, principal=a.principal)
    with pytest.raises(identity.IdentityError):
        identity.provision(root / IDENTITY_FILE, organization_id="org-b", organization_name="x", user_id="user-a",
                           display_name="Avery (synthetic)", kind=identity.UserKind.HUMAN,
                           now=AT)  # a user id never moves organization


def test_display_names_are_unique_within_an_organization_only(tmp_path):
    root, clock = tmp_path / "data", StepClock()
    tenant(root, clock, "org-a", "user-a1", "Avery (synthetic)")
    second = tenant(root, clock, "org-a", "user-a2", "Jordan (synthetic)")
    tenant(root, clock, "org-b", "user-b", "Avery (synthetic)")  # the same name in another organization is fine
    with pytest.raises(ConsoleError, match="already used"):
        second.set_actor("Avery (synthetic)")
    second.seed_service()  # provisions the organization's synthetic-seed user
    with pytest.raises(ConsoleError, match="already used"):
        second.set_actor(demo_seed.SEED_ACTOR)  # no human can take the seed's label


def test_local_development_principal_is_deterministic(tmp_path):
    first = Console.local_development(tmp_path / "data", StepClock())
    second = Console.local_development(tmp_path / "data", StepClock())
    assert first.principal == second.principal == identity.Principal(
        identity.LOCAL_USER_ID, identity.LOCAL_ORGANIZATION_ID, identity.LOCAL_USER_DEFAULT_NAME)


def test_identity_store_holds_no_credentials():
    schema = " ".join(identity._SCHEMA).lower()
    for word in ("password", "secret", "token", "credential", "api"):
        assert word not in schema


def test_local_principal_literals_live_only_in_the_identity_module():
    for path in CONSOLE.glob("*.py"):
        if path.name == "identity.py":
            continue
        text = path.read_text(encoding="utf-8")
        for literal in ('"Local user"', '"local-user"', "'Local user'", "'local-user'"):
            assert literal not in text, (path.name, literal)


def test_sidebar_shows_the_current_principal(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest
    root = tmp_path / "data"
    Console.local_development(root, StepClock()).set_actor("Jamie Rivera (synthetic)")
    monkeypatch.setenv("BAEC_CONSOLE_DATA_DIR", str(root))
    at = AppTest.from_file(str(CONSOLE / "app.py"), default_timeout=30)
    at.run()
    assert not at.exception
    assert at.text_input(key="actor-name").value == "Jamie Rivera (synthetic)"
    assert any(f"{identity.LOCAL_ORGANIZATION_ID} organization" in c.value for c in at.caption)


# --- synthetic workspace -------------------------------------------------------------------------------------


def test_seeded_demo_belongs_to_the_loading_organization_only(tmp_path):
    root, clock = tmp_path / "data", StepClock()
    a = tenant(root, clock, "org-a", "user-a", "Avery (synthetic)")
    b = tenant(root, clock, "org-b", "user-b", "Blake (synthetic)")
    account_id = demo_seed.load(a, "completed")
    assert a.synthetic.account_ids() == [account_id] and b.synthetic.account_ids() == []
    with pytest.raises(AccessDenied):
        b.synthetic.account_view(account_id)
    # Phase 2: each organization has its own synthetic workspace, so another organization's reset or reseed neither
    # touches nor is blocked by this one (test_identity_freshness covers the full two-organization cycle).
    assert a.synthetic.directory != b.synthetic.directory
    b.reset_synthetic()
    demo_seed.load(b, "beginning")
    assert a.synthetic.account_ids() == [account_id]
    a.reset_synthetic()
    assert a.synthetic.account_ids() == []


# --- V0.3 -> V0.4 migration -------------------------------------------------------------------------------------


def _downgrade_to_v03(root: Path, actor_pref: str) -> None:
    """Rewrite a V0.4 root into exactly the V0.3 shape: console store schema 2, no ownership, no identity store,
    and the V0.3 "Your name" preference in the local console store. Engine and CRM files are untouched."""
    (root / IDENTITY_FILE).unlink()
    for workspace in ("local", "synthetic"):
        with _db(root / workspace / "console.sqlite3") as db:
            db.execute("DROP TABLE account_ownership")
            db.execute("DROP TABLE attributions")
            db.execute("DROP TABLE migration_log")
            db.execute("DELETE FROM console_meta WHERE key IN ('ownership_backfill', 'attribution_backfill')")
            db.execute("UPDATE console_meta SET value = '2' WHERE key = 'schema_version'")
            if workspace == "local":
                db.execute("INSERT INTO prefs VALUES ('actor', ?)", (actor_pref,))


@contextmanager
def _db(path: Path):
    db = sqlite3.connect(path, isolation_level=None)
    try:
        yield db
    finally:
        db.close()


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _history(console: Console) -> dict:
    return {a: [(i.at, i.title, i.actor_kind, i.actor, i.detail) for i in s.timeline(a)]
            for s in console.services for a in s.account_ids()}


def test_v03_data_migrates_to_the_local_organization_without_changing_any_record(tmp_path):
    root = tmp_path / "data"
    before = make_console(root, StepClock())
    world = build_world(before)
    synthetic_id = demo_seed.load(before, "completed")
    history = _history(before)
    crm = (before.local.crm_contacts(world.account_id), before.local.crm_tasks(), before.local.crm_activities(
        world.account_id))
    _downgrade_to_v03(root, "Jamie Rivera (synthetic)")
    untouched = {p: _digest(p) for p in sorted(root.glob("*/*.sqlite3")) if p.name != "console.sqlite3"}

    after = Console.local_development(root, StepClock())
    assert after.principal == identity.Principal(identity.LOCAL_USER_ID, identity.LOCAL_ORGANIZATION_ID,
                                                 "Jamie Rivera (synthetic)")  # the V0.3 name carries over
    assert after.local.account_ids() == [world.account_id] and after.synthetic.account_ids() == [synthetic_id]
    assert _history(after) == history  # same ids, same BAEC lineage, same timeline and audit history
    assert (after.local.crm_contacts(world.account_id), after.local.crm_tasks(),
            after.local.crm_activities(world.account_id)) == crm
    assert after.local.preparation_view(world.preparation_id)["status"] == "Needs review"
    assert {p: _digest(p) for p in untouched} == untouched  # Engine 1, Engine 2, and CRM files never written
    for workspace, account_id in (("local", world.account_id), ("synthetic", synthetic_id)):
        with _db(root / workspace / "console.sqlite3") as db:
            assert db.execute("SELECT account_id, organization_id, recorded_by_user_id, basis FROM account_ownership"
                              ).fetchall() == [(account_id, "local", "local-user", "V03_MIGRATION")]
            assert db.execute("SELECT value FROM console_meta WHERE key = 'ownership_backfill'").fetchone() == ("DONE",)

    # Idempotent: opening again adds nothing; V0.3 data stays usable for the full workflow.
    again = Console.local_development(root, StepClock())
    assert _history(again) == history
    with _db(root / "local" / "console.sqlite3") as db:
        assert db.execute("SELECT COUNT(*) FROM account_ownership").fetchone() == (1,)
    again.local.discard_draft(world.draft_id)
    again.local.review_artifact(world.preparation_id, world.artifact_id, HumanReviewDecision.REJECT, attestations={})


def test_migration_assigns_v03_data_to_local_even_if_another_organization_opens_it_first(tmp_path):
    root = tmp_path / "data"
    world = build_world(make_console(root, StepClock()))
    _downgrade_to_v03(root, "Jamie Rivera (synthetic)")
    other = tenant(root, StepClock(), "org-b", "user-b", "Blake (synthetic)")
    assert other.local.account_ids() == []
    with pytest.raises(AccessDenied):
        other.local.account_view(world.account_id)
    assert Console.local_development(root, StepClock()).local.account_ids() == [world.account_id]


def test_an_account_without_an_owner_is_visible_to_no_one(tmp_path):
    root = tmp_path / "data"
    console = make_console(root, StepClock())
    owned = console.local.create_account("Owned account (synthetic)")
    # An Engine 1 account written outside the console after migration has no ownership row. It is never adopted.
    with console.local._e1_repo() as repo:
        repo.add_account(Account("ACC-ORPHAN", "Orphan (synthetic)"))
        repo.add_interaction(SourceInteraction("INT-ORPHAN", "ACC-ORPHAN", AT, "Synthetic text"))
    reopened = Console.local_development(root, StepClock())
    assert reopened.local.account_ids() == [owned]
    with pytest.raises(AccessDenied):
        reopened.local.account_view("ACC-ORPHAN")
    with pytest.raises(AccessDenied):
        reopened.local.capture_condition("ACC-ORPHAN", "INT-ORPHAN", demo_seed.condition_form())
