"""Shared Streamlit helpers for BAEC Console: navigation state, escaping, notices, and guarded actions."""

from __future__ import annotations

from datetime import datetime
from html import escape

import streamlit as st

from baec_app.application import ApplicationError
from baec_app.data.database import PersistenceError
from baec_app.domain.models import DomainValidationError
from baec_app.engine2.errors import Engine2Error
from baec_app.engine2.persistence import Engine2PersistenceError
from baec_app.engine3.errors import Engine3ValidationError

from .console_service import STAGES, ConsoleError, PrincipalInvalid
from .console_store import ConsoleStoreError, display_time
from .engine1_adapter import CaptureError

# Every refusal the engines or the console raise on a user action. Anything else is a programming error and is
# allowed to surface normally.
REFUSALS = (ConsoleError, CaptureError, ApplicationError, DomainValidationError, PersistenceError, Engine2Error,
            Engine2PersistenceError, Engine3ValidationError, ConsoleStoreError, ValueError)

PAGES = ("Dashboard", "Accounts", "Review Queue", "Preparation", "Activity")
STAGE_LABELS = {"REMEMBER": "Remember", "MONITOR": "Monitor", "RECOGNIZE": "Recognize", "PREPARE": "Prepare",
                "PREPARED": "Prepared", "ACCOUNT": "Account"}


PUBLIC_READ_ONLY_KEY = "_baec_public_read_only"


def public_read_only() -> bool:
    return bool(st.session_state.get(PUBLIC_READ_ONLY_KEY, False))


def write_label(label: str) -> str:
    return f"🔒 {label}" if public_read_only() else label


def write_disabled(disabled: bool = False) -> bool:
    return bool(disabled or public_read_only())


def html(markup: str) -> None:
    st.markdown(markup, unsafe_allow_html=True)


def e(text: object) -> str:
    return escape("" if text is None else str(text))


def review_target(row: dict) -> str | tuple[str, str]:
    """What a review-queue row opens. Artifact IDs repeat across preparations, so a draft is (preparation, artifact)."""
    return (row["ref"], row["artifact_id"]) if row.get("artifact_id") else row["ref"]


def preparation_target(preparation_id: str) -> tuple[str, None]:
    """What a Preparation page row opens: that preparation, with no particular draft."""
    return (preparation_id, None)


def go(page: str, account_id: str | None = None, section: str | None = None,
       focus: str | tuple[str, str | None] | None = None) -> None:
    st.session_state.page = page
    if account_id is not None:
        st.session_state.account_id = account_id
    st.session_state.section = section
    st.session_state.focus = focus
    st.session_state.prep_history = None  # a replaced preparation is shown only when navigated to explicitly


def heading(eyebrow: str, title: str, sub: str = "") -> None:
    html(f'<div class="bc-eyebrow">{e(eyebrow)}</div><div class="bc-title">{e(title)}</div>'
         + (f'<div class="bc-sub">{e(sub)}</div>' if sub else ""))


def badge(text: str, kind: str = "") -> str:
    return f'<span class="bc-badge {kind}">{e(text)}</span>'


def notice(title: str, text: str = "", kind: str = "") -> None:
    html(f'<div class="bc-notice {kind}"><div class="bc-notice-title">{e(title)}</div>'
         + (f'<div class="bc-notice-text">{e(text)}</div>' if text else "") + "</div>")


def boundary(text: str) -> None:
    html(f'<div class="bc-boundary-line">{e(text)}</div>')


def when(value: datetime | None, with_time: bool = True) -> str:
    if value is None:
        return ""
    local = display_time(value)
    return f"{local:%d %b %Y, %H:%M}".lstrip("0") if with_time else f"{local:%d %b %Y}".lstrip("0")


def flash(message: str, kind: str = "ok") -> None:
    st.session_state.flash = (message, kind)


def show_flash() -> None:
    item = st.session_state.pop("flash", None)
    if item:
        message, kind = item
        notice(message, kind=kind)


def technical(details: dict, label: str = "Technical details") -> None:
    with st.expander(label):
        for key, value in details.items():
            st.markdown(f"**{key}**")
            st.code(str(value), language=None)


def attempt(action, *, success: str | None = None, failure: str = "That action was refused."):
    """Run one user action. Engine and console refusals are shown in plain language; nothing is bypassed."""
    try:
        result = action()
    except PrincipalInvalid:  # the session lost access: not a refusal of this action; the app stops the page
        raise
    except REFUSALS as error:
        notice(failure, _plain(error), "stop")
        technical({"Refusal": f"{type(error).__name__}: {error}"})
        return None
    if success:
        flash(success)
    # Many product actions intentionally return None after a successful write.
    # Return a truthy sentinel so callers can distinguish success from refusal
    # and rerun immediately.
    return True if result is None else result


def _plain(error: Exception) -> str:
    text = str(error).strip()
    if isinstance(error, ApplicationError) and type(error).__name__ == "NotConfirmable":
        return "Engine 1 does not classify this as a confirmable buyer-articulated condition."
    return (text[:1].upper() + text[1:]) if text else type(error).__name__


def flow(stage: str) -> None:
    """The four-step workflow strip. Done, current, and upcoming come from stored records."""
    order = list(STAGES)
    current = len(order) if stage == "PREPARED" else order.index(stage)
    parts = []
    for i, name in enumerate(order):
        state = "done" if i < current else ("current" if i == current else "")
        dot = "" if state == "done" else str(i + 1)
        if i:
            parts.append(f'<div class="bc-line {"done" if i <= current else ""}"></div>')
        parts.append(f'<div class="bc-step {state}"><span class="bc-dot">{dot}</span>{name}</div>')
    html('<div class="bc-flow">' + "".join(parts) + "</div>")
