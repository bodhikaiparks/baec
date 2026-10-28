"""Public-demo isolation and hard read-only checks."""

import pytest

from baec_app.domain.enums import AccountState
from baec_console.console_service import ConsoleError
from baec_console.public_runtime import PUBLIC_ACTOR, new_public_runtime


def test_public_runtimes_are_isolated():
    first = new_public_runtime()
    second = new_public_runtime()

    try:
        assert first.directory != second.directory
        assert first.account_id != second.account_id
        assert first.console.actor == PUBLIC_ACTOR
        assert second.console.actor == PUBLIC_ACTOR
    finally:
        first.cleanup()
        second.cleanup()


def test_public_runtime_starts_with_completed_synthetic_example():
    runtime = new_public_runtime()

    try:
        view = runtime.console.synthetic.account_view(runtime.account_id)

        assert view.synthetic
        assert view.display_name == "Riverside Outpatient Surgery Center"
        assert view.account.state is AccountState.CONDITIONALLY_DORMANT
        assert view.current_baec is not None
        assert view.plan is not None

        assert len(view.observations) == 2
        assert len(view.superseded) == 1
        assert len(view.measurements) == 2
        assert {str(m.output_value) for m in view.measurements} == {"9.00", "7.00"}

        assert view.verified_correspondences()
        assert view.preparations

        request = view.requests[0]
        current_evidence = request["snapshot"]["evidence"]
        assert len(current_evidence) == 1
        assert "128,400" in current_evidence[0]["text"]
        assert "130,800" not in current_evidence[0]["text"]
    finally:
        runtime.cleanup()


def test_public_runtime_refuses_persistent_writes():
    runtime = new_public_runtime()

    try:
        with pytest.raises(ConsoleError, match="read-only"):
            runtime.console.local.create_account(
                "Blocked account",
                industry="",
                owner_note="",
            )
    finally:
        runtime.cleanup()


def test_public_runtime_does_not_hand_out_a_writable_seed_service():
    runtime = new_public_runtime()

    try:
        with pytest.raises(ConsoleError, match="read-only"):
            runtime.console.seed_service()
    finally:
        runtime.cleanup()


# --- V0.4 Phase 2: the public capability boundary is an allowlist ---------------------------------------------------

import inspect  # noqa: E402

from baec_console import identity  # noqa: E402
from baec_console.console_service import Console, ConsoleService  # noqa: E402
from baec_console.public_runtime import (  # noqa: E402
    LOCKED,
    PUBLIC_CONSOLE_READS,
    PUBLIC_CONSOLE_VALUES,
    PUBLIC_SERVICE_READS,
    PUBLIC_SERVICE_VALUES,
)

# Reviewed by hand: each of these reads stored records and writes nothing. Adding a name to a public allowlist
# without adding it here fails test_public_allowlists_hold_only_reviewed_reads.
REVIEWED_SERVICE_READS = frozenset({
    "account_ids", "has_account", "account_view", "timeline", "attributions", "review_queue", "preparation_ids",
    "preparation_view", "preview_condition", "preview_state_change", "handoff", "review_packet",
    "preparation_snapshot", "preparation_eligibility", "crm_contacts", "crm_tasks", "crm_activities",
    "crm_interaction_link",
})
REVIEWED_CONSOLE_READS = frozenset({"account_views", "review_queue", "activity", "preparations", "dashboard"})
# ReadOnlyConsole implements these itself, returning only wrapped read-only services.
WRAPPED_CONSOLE_METHODS = frozenset({"owner", "owner_of_preparation"})


def _public_methods(cls) -> set[str]:
    return {name for name, value in inspect.getmembers(cls)
            if not name.startswith("_") and (inspect.isfunction(value) or inspect.ismethod(value))}


SERVICE_MUTATORS = sorted(_public_methods(ConsoleService) - PUBLIC_SERVICE_READS)
CONSOLE_MUTATORS = sorted(_public_methods(Console) - PUBLIC_CONSOLE_READS - WRAPPED_CONSOLE_METHODS)


def test_public_allowlists_hold_only_reviewed_reads():
    assert PUBLIC_SERVICE_READS <= REVIEWED_SERVICE_READS
    assert PUBLIC_CONSOLE_READS <= REVIEWED_CONSOLE_READS
    assert PUBLIC_SERVICE_READS <= _public_methods(ConsoleService)  # no stale names
    assert PUBLIC_CONSOLE_READS <= _public_methods(Console)
    assert not (PUBLIC_SERVICE_VALUES | PUBLIC_CONSOLE_VALUES) & (_public_methods(ConsoleService)
                                                                  | _public_methods(Console))


def test_every_known_mutator_is_in_the_refused_set():
    """The enumeration below is computed, so a new method is refused automatically; this pins the current list."""
    assert {"create_account", "record_interaction", "capture_condition", "confirm_condition",
            "record_not_confirmable", "discard_draft", "record_dormancy_judgment", "change_account_state",
            "start_monitoring", "add_observation", "queue_observation_for_review", "submit_correspondence_review",
            "block_preparation", "unblock_preparation", "request_preparation", "generate_preparation",
            "edit_artifact", "review_artifact", "crm_add_contact", "crm_add_task", "crm_complete_task",
            "crm_link_interaction", "crm_add_activity", "crm_attach_engine1_interaction", "destroy",
            "initialize"} <= set(SERVICE_MUTATORS)
    assert {"reset_synthetic", "set_actor", "seed_service", "local_development"} <= set(CONSOLE_MUTATORS)


@pytest.fixture(scope="module")
def runtime():
    runtime = new_public_runtime()
    yield runtime
    runtime.cleanup()


def _public_state(runtime) -> tuple:
    c = runtime.console
    return ([(i.at, i.title, i.actor) for i in c.activity()], c.synthetic.attributions(runtime.account_id),
            c.local.account_ids(), c.synthetic.account_ids(), c.actor, c.dashboard())


@pytest.mark.parametrize("workspace", ("local", "synthetic"))
@pytest.mark.parametrize("name", SERVICE_MUTATORS)
def test_public_runtime_refuses_every_service_mutator(runtime, workspace, name):
    before = _public_state(runtime)
    with pytest.raises(ConsoleError, match="read-only") as refused:
        getattr(getattr(runtime.console, workspace), name)()
    assert str(refused.value) == LOCKED
    assert _public_state(runtime) == before


@pytest.mark.parametrize("name", CONSOLE_MUTATORS)
def test_public_runtime_refuses_every_console_mutator(runtime, name):
    before = _public_state(runtime)
    with pytest.raises(ConsoleError, match="read-only"):
        getattr(runtime.console, name)()
    assert _public_state(runtime) == before


def test_public_runtime_hands_out_no_internal_handles(runtime):
    for name in ("directory", "clock", "engine1_path", "console_path", "crm_path", "identity_path"):
        with pytest.raises(ConsoleError, match="read-only"):
            getattr(runtime.console.synthetic, name)
    with pytest.raises(ConsoleError, match="read-only"):
        runtime.console.identity_path
    assert runtime.console.owner(runtime.account_id) is runtime.console.synthetic  # the wrapped service, not the real one


def test_public_visitor_is_a_public_demo_identity_never_eligible_for_sign_in(runtime):
    with identity.identity_store(runtime.directory / "identity.sqlite3") as store:
        user = store.user(runtime.console.principal.user_id)
    assert user.kind is identity.UserKind.PUBLIC_DEMO_SESSION and not user.authentication_eligible
