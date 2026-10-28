"""V0.4 Phase 4: the Streamlit app in production mode, end to end on PostgreSQL.

The app runs as deployed: BAEC_DEPLOYMENT_MODE=production, the real startup checks (production.process_runtime), the
real pools as baec_app and baec_auth_lookup. Only the two Streamlit readers are replaced, in this test process:
auth.session_claims (what st.user would hold after Streamlit verified an ID token; test_oidc_flow.py exercises that
part with Streamlit's own routes) and auth.streamlit_auth_section (Streamlit's [auth] secrets). Each AppTest is one
browser session. Synthetic data only; software tests, not a security assessment.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from baec_console import auth, production

from .conftest import TEST_ISSUER, auth_section, fresh, production_environ
from .test_production_auth import World, provisioner  # noqa: F401

APP = str(Path(production.__file__).parent / "app.py")
KEY = "_baec_production_console"


class Browser:
    """The claims Streamlit would hold for one browser, and the sign-in and sign-out calls the app made."""

    def __init__(self) -> None:
        self.claims: dict = {}
        self.sign_ins = self.sign_outs = 0

    def sign_in_as(self, subject: str, **overrides) -> None:
        self.claims = {"is_logged_in": True, "provider": "default", "iss": TEST_ISSUER, "sub": subject,
                       "iat": int(time.time()) - 60, "email": f"{subject}@example.invalid", "email_verified": True,
                       "name": "Name from the provider (synthetic)", **overrides}


@pytest.fixture
def browser(migrated, monkeypatch, tmp_path):
    production.close_process_runtime()
    for name, value in production_environ(migrated).items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("BAEC_CONSOLE_DATA_DIR", str(tmp_path / "data"))

    def no_sqlite(*args, **kwargs):
        raise AssertionError("production opened SQLite")
    monkeypatch.setattr(sqlite3, "connect", no_sqlite)
    opened = Browser()
    monkeypatch.setattr(auth, "streamlit_auth_section", lambda: auth_section())
    monkeypatch.setattr(auth, "session_claims", lambda: dict(opened.claims))

    def sign_in():
        opened.sign_ins += 1

    def sign_out():
        opened.sign_outs += 1
        opened.claims = {}  # Streamlit clears its cookie and starts a new session
    monkeypatch.setattr(auth, "begin_sign_in", sign_in)
    monkeypatch.setattr(auth, "sign_out", sign_out)
    yield opened
    production.close_process_runtime()
    assert not (tmp_path / "data").exists()  # production never creates a data directory
    assert not production.PRODUCTION_ROOT.exists()


@pytest.fixture
def world(provisioner) -> World:  # noqa: F811
    return World(provisioner)


def run(at: AppTest | None = None) -> AppTest:
    at = at or AppTest.from_file(APP, default_timeout=60)
    at.run()
    assert not at.exception, at.exception
    return at


def text(at: AppTest) -> str:
    parts = ([m.value for m in at.markdown] + [c.value for c in at.caption] + [e.value for e in at.error]
             + [b.label for b in at.button] + [d.value.to_csv() for d in at.dataframe])
    return "\n".join(str(p) for p in parts)


def buttons(at: AppTest) -> set[str]:
    return {b.label for b in at.button}


def accounts_page(at: AppTest) -> AppTest:
    at.button(key="nav-Accounts").click()
    return run(at)


def test_without_a_sign_in_only_the_sign_in_page_is_shown(browser, world):
    at = run()
    assert buttons(at) == {"Sign in"} and not at.text_input and not at.error
    assert KEY not in at.session_state
    at.button(key="sign-in").click()
    run(at)
    assert browser.sign_ins == 1 and KEY not in at.session_state


def test_a_signed_in_user_gets_their_own_console_with_their_baec_identity(browser, world, storage):
    a_name, b_name = fresh("Account A (synthetic)"), fresh("Account B (synthetic)")
    runtime = production.process_runtime(auth_section())
    for subject, name in ((world.subject_b, b_name), (world.subject_a, a_name)):
        signed_in = auth.identity_from_claims({**Browser().claims, **_claims(subject)}, issuer=TEST_ISSUER,
                                              now=runtime.clock.now())
        runtime.console_for(signed_in).local.create_account(name)
    browser.sign_in_as(world.subject_a)
    at = accounts_page(run())
    shown = text(at)
    assert a_name in shown and b_name not in shown
    assert "Avery (synthetic)" in shown and "Name from the provider" not in shown  # BAEC's name, not the claim
    assert f"Signed in as {world.subject_a}@example.invalid" in shown
    assert {"Sign out", "Today", "Accounts"} <= buttons(at)
    assert not {"Load demo: start from call notes", "Load demo: start from beginning", "Load demo: completed example",
                "Reset synthetic data"} & buttons(at)
    assert [t.label for t in at.text_input if t.key == "actor-name"] == []  # no self-rename in production
    signed_in, console = at.session_state[KEY]
    assert signed_in.key == (TEST_ISSUER, world.subject_a)
    assert (console.principal.user_id, console.principal.organization_id) == (world.user_a, world.org_a)


def _claims(subject: str) -> dict:
    browser = Browser()
    browser.sign_in_as(subject)
    return browser.claims


def test_query_parameters_cannot_choose_the_organization_user_or_a_foreign_account(browser, world):
    runtime = production.process_runtime(auth_section())
    b_console = runtime.console_for(auth.identity_from_claims(_claims(world.subject_b), issuer=TEST_ISSUER,
                                                              now=runtime.clock.now()))
    b_name = fresh("Account B (synthetic)")
    b_account = b_console.local.create_account(b_name)
    browser.sign_in_as(world.subject_a, organization_id=world.org_b, user_id=world.user_b)
    at = AppTest.from_file(APP, default_timeout=60)
    at.query_params["account"] = b_account
    at.query_params["organization"] = world.org_b
    at.query_params["user"] = world.user_b
    at = run(at)
    console = at.session_state[KEY][1]
    assert (console.principal.user_id, console.principal.organization_id) == (world.user_a, world.org_a)
    assert b_account not in console.local.account_ids()
    assert b_name not in text(at)  # the bookmarked foreign workspace is not opened; the dashboard is shown instead
    assert b_name not in text(accounts_page(at))


def test_two_browser_sessions_never_share_a_console(browser, world):
    browser.sign_in_as(world.subject_a)
    at_a = run()
    browser.sign_in_as(world.subject_b)
    at_b = run()
    console_a, console_b = at_a.session_state[KEY][1], at_b.session_state[KEY][1]
    assert console_a is not console_b
    assert console_a.principal.user_id == world.user_a and console_b.principal.user_id == world.user_b


def test_a_session_whose_sign_in_changes_never_reuses_the_previous_console(browser, world):
    """Session swapping: if the verified identity behind one browser session changes (another cookie), the console
    kept for the previous identity is dropped, never reused."""
    browser.sign_in_as(world.subject_a)
    at = run()
    console_a = at.session_state[KEY][1]
    browser.sign_in_as(world.subject_b)
    at = run(at)
    signed_in, console = at.session_state[KEY]
    assert signed_in.subject == world.subject_b and console is not console_a
    assert (console.principal.user_id, console.principal.organization_id) == (world.user_b, world.org_b)
    browser.claims = {}
    at = run(at)
    assert KEY not in at.session_state and buttons(at) == {"Sign in"}


@pytest.mark.parametrize("change", ["revoke", "deactivate"])
def test_revocation_or_deactivation_closes_a_signed_in_browser_on_its_next_request(browser, world, change):
    browser.sign_in_as(world.subject_a)
    at = run()
    assert "Accounts" in buttons(at)
    if change == "revoke":
        world.provisioner.revoke(world.org_a, TEST_ISSUER, world.subject_a, "operator (synthetic)")
    else:
        world.provisioner.set_active(world.org_a, world.user_a, False)
    at = run(at)  # the same browser session, still holding its cookie and its kept console
    assert len(at.error) == 1 and buttons(at) == {"Sign out"}  # no navigation, no data
    assert KEY not in at.session_state
    at = run(at)
    assert [e.value for e in at.error] == [auth.NO_ACCESS] and buttons(at) == {"Sign out"}


def test_an_unlinked_or_foreign_issuer_sign_in_sees_only_a_refusal(browser, world):
    for claims in ({"sub": fresh("never-linked")}, {"sub": world.subject_a, "iss": "https://other.invalid"},
                   {"sub": world.subject_a, "provider": "other"}):
        browser.sign_in_as(world.subject_a, **{k: v for k, v in claims.items() if k != "sub"})
        browser.claims["sub"] = claims["sub"]
        at = run()
        assert [e.value for e in at.error] == [auth.NO_ACCESS]
        assert buttons(at) == {"Sign out"} and KEY not in at.session_state


def test_an_expired_session_is_refused(browser, world):
    browser.sign_in_as(world.subject_a, iat=int(time.time() - auth.SESSION_MAX_AGE.total_seconds()) - 60)
    at = run()
    assert [e.value for e in at.error] == [auth.SESSION_EXPIRED] and buttons(at) == {"Sign out"}


def test_sign_out_clears_the_session_including_the_claude_key(browser, world):
    browser.sign_in_as(world.subject_a)
    at = run()
    at.session_state["baec-ai-token"] = "synthetic-test-key-not-a-real-key"
    at = run(at)
    at.button(key="sign-out").click()
    at = run(at)  # the run that signs out stops after clearing the session
    assert browser.sign_outs == 1
    assert "baec-ai-token" not in at.session_state and KEY not in at.session_state
    at = run(at)  # what the browser shows next: Streamlit has cleared its sign-in cookie
    assert buttons(at) == {"Sign in"} and KEY not in at.session_state


def test_a_failed_startup_shows_only_the_refusal(browser, world, monkeypatch, migrated):
    monkeypatch.setenv("BAEC_AUTH_DATABASE_URL", migrated["app"])  # the wrong role for the sign-in lookup
    browser.sign_in_as(world.subject_a)
    at = run()
    assert len(at.error) == 1 and "sign-in" in at.error[0].value.lower()
    assert not at.button and KEY not in at.session_state
