"""Public, read-only BAEC Console."""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from baec_console import research_overview, ui_pages, ui_workspace
from baec_console.public_runtime import PublicRuntime, new_public_runtime
from baec_console.styles import CSS
from baec_console.ui_common import PUBLIC_READ_ONLY_KEY, go, html


RUNTIME_KEY = "_baec_public_runtime"
PAGES = (
    "Research Overview",
    "Dashboard",
    "Accounts",
    "Review Queue",
    "Preparation",
    "Activity",
)


def _runtime() -> PublicRuntime:
    runtime = st.session_state.get(RUNTIME_KEY)

    if runtime is None:
        runtime = new_public_runtime()
        st.session_state[RUNTIME_KEY] = runtime
        go("Research Overview")
    elif "page" not in st.session_state:
        go("Research Overview")

    return runtime


def _restart_demo() -> None:
    old = st.session_state.get(RUNTIME_KEY)

    if old is not None:
        old.cleanup()

    runtime = new_public_runtime()
    st.session_state[RUNTIME_KEY] = runtime
    go("Workspace", runtime.account_id, "Remember")


def _sidebar(runtime: PublicRuntime) -> None:
    with st.sidebar:
        html(
            '<div class="bc-brand">'
            '<div class="bc-brand-mark">BAEC</div>'
            '<div class="bc-brand-sub">Full System Demo</div>'
            '</div>'
        )

        if st.button(
            "Guided walkthrough",
            key="nav-walkthrough",
            type="primary" if st.session_state.page == "Workspace" else "secondary",
        ):
            go("Workspace", runtime.account_id, "Remember")
            st.rerun()

        html('<div class="bc-side-label">Explore full console</div>')

        for page in PAGES:
            if st.button(
                page,
                key=f"nav-{page}",
                type="primary" if st.session_state.page == page else "secondary",
            ):
                go(page)
                st.rerun()

        html('<div class="bc-side-label">Demo controls</div>')

        if st.button("Restart synthetic demo", key="restart-demo"):
            _restart_demo()
            st.rerun()

        html(
            '<div class="bc-side-note">'
            'Full public-facing BAEC Console using synthetic data.'
            '</div>'
        )
        html(
            '<div class="bc-side-note" style="margin-top:8px">'
            'You can explore the complete workflow and forms. '
            'Record-creating and state-changing actions are locked.'
            '</div>'
        )


def main() -> None:
    st.set_page_config(
        page_title="BAEC Console",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    st.session_state[PUBLIC_READ_ONLY_KEY] = True

    html(CSS)

    runtime = _runtime()
    console = runtime.console

    _sidebar(runtime)

    st.caption(
        "PUBLIC DEMO · FULL CONSOLE · SYNTHETIC DATA · READ-ONLY"
    )

    page = st.session_state.page

    valid_accounts = {
        account_id
        for service in console.services
        for account_id in service.account_ids()
    }

    if (
        page == "Workspace"
        and st.session_state.get("account_id") in valid_accounts
    ):
        st.query_params["account"] = st.session_state.account_id
        ui_workspace.render(
            console,
            st.session_state.account_id,
        )
        return

    st.query_params.clear()

    if page == "Research Overview":
        research_overview.render(console, runtime.account_id)
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
