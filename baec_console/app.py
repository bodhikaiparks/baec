"""BAEC Console: Trigger Intelligence. A local, offline product shell over BAEC Engines 1 to 3.

Run from the baec_console directory (see README_CONSOLE.md):
    BAEC_DEPLOYMENT_MODE=development ../.venv/bin/python -m streamlit run app.py

No email, CRM write, outreach, or other external action exists here. AI Assist runs only with the person's own
Claude API key, kept in this browser session.

BAEC_DEPLOYMENT_MODE (deployment.py) must be set explicitly and decides who the session runs as:
    development  the local-development principal, on local SQLite files under baec_console/.data/ (or
                 BAEC_CONSOLE_DATA_DIR), created on first run;
    production   the production startup checks (production.open_runtime), then a sign-in page, then, only for a
                 signed-in user whose sign-in an operator linked to an active HUMAN BAEC user, the console on
                 PostgreSQL (production.py, auth.py). No SQLite file and no data directory is used.
Unset, empty, or any other value shows a configuration error and opens nothing.

Operations (production; V0.4 Phase 5). Every log record this process writes is redacted (log_redaction.py). If the
database cannot serve a request in time (pool exhausted, a timeout, a lost connection), the page shows one fixed
message and nothing else; PostgreSQL has rolled that request's transaction back.

Session isolation (production). The console is kept in this browser session's state only, together with the exact
signed-in identity it was opened for, and is reused only for that identity; it is never shared between sessions.
Every operation re-checks the sign-in link and the user (console_service guard), so revocation or deactivation
takes effect on the next request whatever the browser holds. Sign-out clears this session's state, including the
Claude API key, and Streamlit's sign-in cookie.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from baec_console import (  # noqa: E402
    auth, demo_seed, deployment, log_redaction, production, ui_crm, ui_pages, ui_workspace)
from baec_console.console_service import Console, ConsoleError, PrincipalInvalid  # noqa: E402
from baec_console.identity import IdentityError  # noqa: E402
from baec_console.styles import CSS  # noqa: E402
from baec_console.ui_common import PAGES, attempt, go, html  # noqa: E402

DATA_DIR = Path(os.environ.get("BAEC_CONSOLE_DATA_DIR") or Path(__file__).resolve().parent / ".data")

PRODUCT_PAGES = (
    "Today",
    "Accounts",
    "Review Queue",
    "Preparation",
    "Activity",
    "Dashboard",
)


PRODUCTION_CONSOLE_KEY = "_baec_production_console"


@st.cache_resource
def _console(path: str, mode: str) -> Console:
    # Development only. Holds only file paths, a clock, and the local-development principal; every operation opens
    # and closes its own connections and re-validates the principal first. A refusal raises, and Streamlit does not
    # cache it.
    return deployment.runtime_console(path, mode=deployment.DeploymentMode(mode))


def _disconnect_claude() -> None:
    st.session_state["baec-ai-token"] = ""


def _sign_out() -> None:
    st.session_state.clear()  # this session's console, page, and Claude API key
    auth.sign_out()  # clears Streamlit's sign-in cookie and starts a new session
    st.stop()


def _sign_in_page() -> None:
    html('<div class="bc-brand"><div class="bc-brand-mark">BAEC</div>'
         '<div class="bc-brand-sub">Trigger Intelligence</div></div>')
    st.info("Sign in to open BAEC Console. Access is given by your BAEC administrator; signing in does not create "
            "an account.")
    if st.button("Sign in", key="sign-in", type="primary"):
        auth.begin_sign_in()


def _refuse(message: str, *, signed_in: bool) -> None:
    st.session_state.pop(PRODUCTION_CONSOLE_KEY, None)
    st.error(message)  # fail closed: nothing else is shown
    if signed_in and st.button("Sign out", key="sign-out-refused"):
        _sign_out()
    st.stop()


def _production_console() -> tuple[Console, auth.AuthenticatedIdentity]:
    """The signed-in user's console, or the sign-in page, or a refusal. Reads identity only from Streamlit's verified
    session (auth.session_claims): no query parameter, header, or form field is consulted."""
    try:
        runtime = production.process_runtime(auth.streamlit_auth_section())
    except deployment.DeploymentError as error:
        _refuse(str(error), signed_in=False)
    claims = auth.session_claims()
    if claims.get("is_logged_in") is not True:
        st.session_state.pop(PRODUCTION_CONSOLE_KEY, None)
        _sign_in_page()
        st.stop()
    try:
        signed_in = auth.identity_from_claims(claims, issuer=runtime.issuer, now=runtime.clock.now())
        cached = st.session_state.get(PRODUCTION_CONSOLE_KEY)
        if cached is not None and cached[0] == signed_in:
            return cached[1], signed_in
        st.session_state.pop(PRODUCTION_CONSOLE_KEY, None)
        console = runtime.console_for(signed_in)
    except auth.SignInRefused as error:
        _refuse(str(error), signed_in=True)
    st.session_state[PRODUCTION_CONSOLE_KEY] = (signed_in, console)
    return console, signed_in


def _sidebar(console: Console, signed_in: auth.AuthenticatedIdentity | None = None) -> None:
    with st.sidebar:
        html('<div class="bc-brand"><div class="bc-brand-mark">BAEC</div>'
             '<div class="bc-brand-sub">Trigger Intelligence</div></div>')
        current = "Accounts" if st.session_state.page == "Workspace" else st.session_state.page
        for page in PRODUCT_PAGES:
            if st.button(page, key=f"nav-{page}", type="primary" if page == current else "secondary"):
                go(page)
                st.rerun()
        html('<div class="bc-side-label">You</div>')
        if signed_in is not None:
            st.markdown(f"**{console.actor}**")  # from BAEC's identity records, never from the provider
            st.caption(f"Signed in{' as ' + signed_in.email if signed_in.email else ''} · "
                       f"{console.principal.organization_id} organization.")
            if st.button("Sign out", key="sign-out"):
                _sign_out()
        else:
            name = st.text_input("Your name", value=console.actor, key="actor-name", label_visibility="collapsed",
                                 help="Recorded on every human action. Not authentication.")
            if name.strip() and name.strip() != console.actor:
                attempt(lambda: console.set_actor(name))
            st.caption(f"Local development user · {console.principal.organization_id} organization. "
                       "Not authentication.")
        html('<div class="bc-side-label">AI Assist</div>')

        st.text_input(
            "Claude API key",
            type="password",
            key="baec-ai-token",
            placeholder="Enter Claude API key",
        )
        st.caption(
            "Uses your own Claude API account. "
            "The key is kept only for this session."
        )

        if st.session_state.get("baec-ai-token", "").strip():
            html(
                '<div class="bc-side-note">'
                'Claude connected for this session.'
                '</div>'
            )

            # A widget's value can only be cleared before it is drawn, so Disconnect clears it in a callback.
            st.button(
                "Disconnect Claude",
                key="disconnect-claude",
                on_click=_disconnect_claude,
            )
        else:
            html(
                '<div class="bc-side-note">'
                'Add your own Claude API key '
                'to enable AI Assist.'
                '</div>'
            )

        if signed_in is not None:  # production: no synthetic demo, which would act as the synthetic-seed identity
            html('<div class="bc-side-note" style="margin-top:14px">AI Assist is optional and runs only with your own '
                 'Claude API key. No outreach.</div>')
            return
        html('<div class="bc-side-label">Synthetic demo data</div>')
        if st.button("Load demo: start from call notes", key="seed-call-notes"):
            account_id = attempt(lambda: demo_seed.load(console, "call_notes"), success="Synthetic demo account loaded.")
            if account_id:
                go("Workspace", account_id)
                st.rerun()
        if st.button("Load demo: start from beginning", key="seed-beginning"):
            account_id = attempt(lambda: demo_seed.load(console, "beginning"), success="Synthetic demo account loaded.")
            if account_id:
                go("Workspace", account_id, "Remember")
                st.rerun()
        if st.button("Load demo: completed example", key="seed-completed"):
            account_id = attempt(lambda: demo_seed.load(console, "completed"), success="Completed synthetic example loaded.")
            if account_id:
                go("Workspace", account_id, "Prepare")
                st.rerun()
        if st.button("Reset synthetic data", key="seed-reset"):
            attempt(console.reset_synthetic, success="Synthetic data reset. Your accounts were not changed.")
            go("Dashboard")
            st.rerun()
        html('<div class="bc-side-note">Loading or resetting replaces only synthetic data. Accounts you create are '
             'never changed.</div>')
        html('<div class="bc-side-note" style="margin-top:14px">Local data. AI Assist is optional and runs only with '
             'your own Claude API key. No outreach.</div>')


def _unavailable(signed_in: bool) -> None:
    from baec_console.postgres.database import UNAVAILABLE

    _refuse(UNAVAILABLE, signed_in=signed_in)


def main() -> None:
    st.set_page_config(page_title="BAEC Console", layout="wide", initial_sidebar_state="expanded")
    html(CSS)
    signed_in = None
    try:
        mode = deployment.deployment_mode()
        if mode is deployment.DeploymentMode.PRODUCTION:
            log_redaction.install()
            console, signed_in = _production_console()
        else:
            console = _console(str(DATA_DIR), mode.value)
    except (deployment.DeploymentError, ConsoleError, IdentityError) as error:
        st.error(str(error))  # fail closed: no fallback principal, no data shown
        st.stop()
    except Exception as error:  # production only: the database could not serve this request in time
        if not _is_unavailable(error):
            raise
        _unavailable(signed_in=auth.session_claims().get("is_logged_in") is True)
    try:
        console.check_access()  # before anything is drawn
        _render(console, signed_in)
    except PrincipalInvalid as error:
        _refuse(str(error), signed_in=signed_in is not None)
    except Exception as error:
        if not _is_unavailable(error):
            raise
        _unavailable(signed_in=signed_in is not None)


def _is_unavailable(error: Exception) -> bool:
    """DatabaseUnavailable, without importing the PostgreSQL package in development (which never raises it)."""
    module = sys.modules.get("baec_console.postgres.database")
    return module is not None and isinstance(error, module.DatabaseUnavailable)


def _render(console: Console, signed_in: auth.AuthenticatedIdentity | None = None) -> None:
    if "page" not in st.session_state:  # first run of this browser session: honor a bookmarked workspace URL
        st.session_state.page = "Today"
        if st.query_params.get("account"):
            go("Workspace", st.query_params["account"], st.query_params.get("section"))
    _sidebar(console, signed_in)
    page = st.session_state.page
    if page == "Workspace" and st.session_state.get("account_id") in {
            a for service in console.services for a in service.account_ids()}:
        st.query_params["account"] = st.session_state.account_id
        ui_workspace.render(console, st.session_state.account_id)
        return
    st.query_params.clear()
    if page == "Today":
        ui_crm.today(console)
    elif page == "Accounts":
        ui_pages.accounts(console)
    elif page == "Review Queue":
        ui_pages.review_queue(console)
    elif page == "Preparation":
        ui_pages.preparation(console)
    elif page == "Activity":
        ui_pages.activity(console)
    else:
        ui_pages.dashboard(console)


main()
