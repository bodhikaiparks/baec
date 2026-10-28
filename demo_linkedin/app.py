"""BAEC Trigger Intelligence: LinkedIn product demo (synthetic, prerecorded, offline).

Four progressive steps: Remember, Monitor, Recognize, Prepare. Nothing is generated live, nothing is sent,
and no network service is called. The correspondence decision in step 3 is made by the person using the demo.

Run from this directory:  streamlit run app.py
Recording mode:           append ?recording=1 to the URL, or use the sidebar toggle.
"""

from __future__ import annotations

import sys
from html import escape
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent))

import demo_data as d  # noqa: E402
from styles import page_css  # noqa: E402

STEPS = ("Remember", "Monitor", "Recognize", "Prepare")
SCALE_MAX_PERCENT = 12


# ---------- state ----------

def _init_state() -> None:
    st.session_state.setdefault("step", 1)
    st.session_state.setdefault("decision", None)  # None | "verified" | "insufficient"


def _go(step: int) -> None:
    st.session_state.step = step


def _decide(decision: str | None) -> None:
    st.session_state.decision = decision


def _restart() -> None:
    st.session_state.step = 1
    st.session_state.decision = None


def _recording_mode() -> bool:
    return st.query_params.get("recording", "").lower() in ("1", "true", "on", "yes")


def _toggle_recording() -> None:
    if st.session_state.recording_toggle:
        st.query_params["recording"] = "1"
    elif "recording" in st.query_params:
        del st.query_params["recording"]


# ---------- shared pieces ----------

def _html(markup: str) -> None:
    st.html(markup)


def _nav() -> None:
    _html(
        '<div class="bt-nav">'
        '<div class="bt-brand"><span class="bt-brand-mark">BAEC</span>'
        '<span class="bt-brand-name">Trigger Intelligence</span></div>'
        '<span class="bt-badge">Synthetic Demo</span>'
        "</div>"
    )


def _step_done(index: int) -> bool:
    current = st.session_state.step
    return index < current or (index == current == 3 and st.session_state.decision == "verified")


def _progress() -> None:
    parts = []
    for index, name in enumerate(STEPS, start=1):
        done = _step_done(index)
        state = "is-done" if done else ("is-current" if index == st.session_state.step else "")
        dot = "" if done else str(index)
        if index > 1:
            line_state = "is-done" if _step_done(index - 1) else ""
            parts.append(f'<span class="bt-pline {line_state}"></span>')
        parts.append(
            f'<span class="bt-pstep {state}" data-step="{index}">'
            f'<span class="bt-pdot">{dot}</span><span class="bt-plabel">{name}</span></span>'
        )
    left, right = st.columns([5, 1], vertical_alignment="center")
    with left:
        _html(f'<div class="bt-progress">{"".join(parts)}</div>')
    with right:
        st.button("Restart demo", key="restart", type="tertiary", on_click=_restart)


def _heading(step: int, headline: str, sub: str = "") -> None:
    sub_html = f'<p class="bt-sub">{escape(sub)}</p>' if sub else ""
    _html(
        f'<div class="bt-eyebrow">Step {step} of 4 &middot; {STEPS[step - 1]}</div>'
        f'<h1 class="bt-headline">{escape(headline)}</h1>{sub_html}'
    )


def _highlighted_statement() -> str:
    text = escape(d.BUYER_STATEMENT)
    for _, _, phrase in d.EXTRACTED_FIELDS:
        text = text.replace(escape(phrase), f"<mark>{escape(phrase)}</mark>", 1)
    return text


def _footer() -> None:
    _html(
        '<div class="bt-footer">Synthetic demonstration. Buyer statements, account data, evidence, '
        "and prepared outputs are fictional.</div>"
    )


# ---------- steps ----------

def _step_remember() -> None:
    _heading(1, "Remember what the buyer actually said.",
             "Conditions buyers state in their own words are kept exactly as they said them.")
    fields = "".join(
        f'<div class="bt-field"><div class="bt-label">{escape(label)}</div>'
        f'<div class="bt-field-value">{escape(value)}</div></div>'
        for label, value, _ in d.EXTRACTED_FIELDS
    )
    _html(
        '<div class="bt-card">'
        '<div class="bt-card-head"><div>'
        f'<div class="bt-account">{escape(d.ACCOUNT)}</div>'
        f'<div class="bt-role">{escape(d.BUYER_ROLE)}</div></div>'
        f'<span class="bt-tag">Seller: {escape(d.SELLER)}</span></div>'
        '<div class="bt-label" style="margin-top:18px">Buyer said</div>'
        f'<blockquote class="bt-quote" style="margin-top:0">&ldquo;{_highlighted_statement()}&rdquo;</blockquote>'
        f'<div class="bt-fields">{fields}</div>'
        "</div>"
    )
    st.button("Start monitoring", key="start_monitoring", type="primary", on_click=_go, args=(2,))


def _threshold_scale() -> str:
    threshold = d.THRESHOLD_PERCENT / SCALE_MAX_PERCENT * 100
    observed = d.OBSERVED_PERCENT / SCALE_MAX_PERCENT * 100
    return (
        '<div class="bt-scale" role="img" '
        f'aria-label="Buyer threshold {d.THRESHOLD_PERCENT}%, observed {d.OBSERVED_PERCENT}%">'
        '<div class="bt-scale-track"></div>'
        f'<div class="bt-scale-over" style="left:{threshold:.2f}%;right:0"></div>'
        f'<div class="bt-scale-threshold" style="left:{threshold:.2f}%"></div>'
        f'<div class="bt-scale-threshold-label" style="left:{threshold:.2f}%">'
        f"Buyer threshold &gt; {d.THRESHOLD_PERCENT}%</div>"
        f'<div class="bt-scale-point" style="left:{observed:.2f}%"></div>'
        f'<div class="bt-scale-point-label" style="left:{observed:.2f}%">Observed {d.OBSERVED_PERCENT}%</div>'
        '<div class="bt-scale-ends" style="left:0">0%</div>'
        f'<div class="bt-scale-ends" style="right:0">{SCALE_MAX_PERCENT}%</div>'
        "</div>"
    )


def _step_monitor() -> None:
    _heading(2, "Something changed.", "New evidence arrived for an account with a remembered condition.")
    change = escape(d.EVIDENCE_CHANGE).replace("+9%", "<b>+9%</b>")
    _html(
        '<div class="bt-card">'
        '<div class="bt-card-head"><div>'
        f'<div class="bt-evidence-type">{escape(d.EVIDENCE_TYPE)}</div>'
        f'<div class="bt-evidence-title">{escape(d.EVIDENCE_TITLE)}</div>'
        f'<div class="bt-evidence-change">{change}</div></div>'
        f'<span class="bt-tag">{escape(d.ACCOUNT)}</span></div>'
        "</div>"
        '<div class="bt-card"><div class="bt-compare">'
        f'<div><div class="bt-label">Buyer condition</div><div class="bt-stat-value">&gt; {d.THRESHOLD_PERCENT}%</div></div>'
        f'<div><div class="bt-label">Observed evidence</div>'
        f'<div class="bt-stat-value is-observed">{d.OBSERVED_PERCENT}%</div></div>'
        f"<div>{_threshold_scale()}</div>"
        "</div>"
        '<p class="bt-note">This evidence may correspond to a condition the buyer previously described.</p>'
        "</div>"
    )
    st.button("Review correspondence", key="review_correspondence", type="primary", on_click=_go, args=(3,))


def _step_recognize() -> None:
    _heading(3, "You decide whether the evidence actually corresponds.",
             "BAEC surfaces the comparison. A person makes the call.")
    _html(
        '<div class="bt-side">'
        '<div class="bt-card"><div class="bt-label">What the buyer said</div>'
        f'<div class="bt-side-text">{escape(d.RECOGNIZE_BUYER_SAID)}</div></div>'
        '<div class="bt-card"><div class="bt-label">What changed</div>'
        f'<div class="bt-side-text">{escape(d.RECOGNIZE_WHAT_CHANGED)}</div></div>'
        "</div>"
    )
    decision = st.session_state.decision
    if decision is None:
        _html('<div class="bt-question">Does this evidence correspond to the buyer&rsquo;s previously stated '
              "condition?</div>")
        yes, no, _ = st.columns([1.4, 1.4, 1.75])
        with yes:
            st.button("Yes — verify correspondence", key="verify_yes", type="primary",
                      on_click=_decide, args=("verified",), use_container_width=True)
        with no:
            st.button("Not enough evidence", key="verify_no", type="secondary",
                      on_click=_decide, args=("insufficient",), use_container_width=True)
    elif decision == "verified":
        _html(
            '<div class="bt-verified"><span class="bt-check"></span><div>'
            '<div class="bt-verified-title">Human-verified correspondence</div>'
            '<div class="bt-verified-note">This does not establish buyer intent or authorize outreach.</div>'
            "</div></div>"
        )
        st.button("Prepare re-engagement", key="prepare", type="primary", on_click=_go, args=(4,))
    else:
        _html(
            '<div class="bt-declined">'
            '<div class="bt-declined-title">No correspondence recorded</div>'
            '<div class="bt-declined-note">The evidence stays unverified. Nothing about the account changes, '
            "and nothing is prepared.</div></div>"
        )
        st.button("Back to review", key="reconsider", type="secondary", on_click=_decide, args=(None,))


def _step_prepare() -> None:
    _heading(4, "Walk into the conversation prepared.",
             "Grounded in the buyer's own words and the verified evidence. Nothing has been sent.")
    why, email, call, questions = st.tabs(["Why now", "Email draft", "Call opening", "Discovery questions"])
    with why:
        _html(f'<div class="bt-output"><p>{escape(d.WHY_NOW)}</p></div>')
    with email:
        body = "".join(f"<p>{escape(paragraph).replace(chr(10), '<br>')}</p>" for paragraph in d.EMAIL_BODY)
        _html(
            '<div class="bt-output">'
            f'<div class="bt-email-meta">Subject: <b>{escape(d.EMAIL_SUBJECT)}</b></div>'
            f"{body}"
            '<div class="bt-draft-flag">Draft only. Not sent.</div>'
            "</div>"
        )
    with call:
        _html(f'<div class="bt-output"><p>{escape(d.CALL_OPENING)}</p></div>')
    with questions:
        items = "".join(f"<li>{escape(q)}</li>" for q in d.DISCOVERY_QUESTIONS)
        _html(f'<div class="bt-output"><ol class="bt-questions">{items}</ol></div>')
    _html(
        '<div class="bt-boundary">'
        '<div class="bt-boundary-main">BAEC prepares the conversation. The salesperson decides whether to have it.</div>'
        '<div class="bt-boundary-sub">No buyer intent inferred &middot; No automatic outreach &middot; '
        "No opportunity created</div>"
        "</div>"
    )


def _how_this_works() -> None:
    with st.expander("How this works"):
        st.markdown(
            "- **Remember.** A buyer's stated condition is stored in their own words, with the threshold, timing, "
            "and potential action kept exactly as stated.\n"
            "- **Monitor.** New evidence is compared against remembered conditions. A match is a possible "
            "correspondence, not a conclusion about the buyer.\n"
            "- **Recognize.** A person decides whether the evidence actually corresponds. Nothing advances "
            "without that decision.\n"
            "- **Prepare.** BAEC drafts talking points from the buyer's words and the verified evidence. "
            "It does not send anything, create an opportunity, or infer intent.\n\n"
            "In this demo every input and output is synthetic and prerecorded. No AI model or network service "
            "is called."
        )


# ---------- page ----------

def main() -> None:
    st.set_page_config(page_title="BAEC Trigger Intelligence", layout="wide", initial_sidebar_state="collapsed")
    _init_state()
    recording = _recording_mode()
    st.html(page_css(recording))

    if not recording:
        with st.sidebar:
            st.toggle("Recording mode", value=False, key="recording_toggle", on_change=_toggle_recording,
                      help="Hides app chrome and tightens spacing for a 16:9 screen recording. "
                           "Remove ?recording=1 from the URL to exit.")

    _nav()
    _progress()
    {1: _step_remember, 2: _step_monitor, 3: _step_recognize, 4: _step_prepare}[st.session_state.step]()
    if not recording:
        _how_this_works()
    _footer()


main()
