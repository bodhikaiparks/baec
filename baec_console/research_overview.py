"""Public research and system overview for the unified BAEC Console."""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from .console_service import STATE_LABELS
from .ui_common import e, go, html, notice


def _measurement_story(view) -> tuple[str, str]:
    old_values = []
    current_values = []

    superseded = set(view.superseded)

    for measurement in view.measurements:
        value = f"{measurement.output_value}{measurement.output_unit}"
        if measurement.source_observation_ids & superseded:
            old_values.append(value)
        else:
            current_values.append(value)

    old = ", ".join(old_values) or "None"
    current = ", ".join(current_values) or "None"
    return old, current


def _latest_outcome(view) -> str:
    outcomes = [
        outcome.value
        for candidate in view.candidates
        for _, outcome in candidate["reviews"]
    ]
    return outcomes[-1] if outcomes else "None"


def render(console, account_id: str) -> None:
    view = console.synthetic.account_view(account_id)
    old_measurement, current_measurement = _measurement_story(view)
    outcome = _latest_outcome(view)

    html(
        '<div class="bc-eyebrow">BAEC Research Program</div>'
        '<div class="bc-title">When the Buyer Is Not In-Market</div>'
        '<div class="bc-sub">'
        'A public system demonstration of Buyer-Articulated Evaluation Contingencies '
        'across capture, monitoring, human verification, and re-engagement preparation.'
        '</div>'
    )

    html(
        '<div class="bc-card">'
        '<div class="bc-label">Research question</div>'
        '<div class="bc-quote">'
        'What should a salesperson preserve when a qualified buyer is not evaluating today, '
        'but can articulate what would make evaluation worthwhile later?'
        '</div>'
        '<div class="bc-muted">'
        'The BAEC framework begins from that narrow problem. It does not attempt to predict '
        'who will buy or infer hidden purchase intent.'
        '</div>'
        '</div>'
    )

    c1, c2 = st.columns(2)

    with c1:
        html(
            '<div class="bc-card">'
            '<div class="bc-label">BAEC</div>'
            '<div class="bc-head-value">Buyer-Articulated Evaluation Contingency</div>'
            '<div class="bc-muted" style="margin-top:8px">'
            'An explicit buyer-generated statement, made while the buyer is not currently '
            'evaluating relevant alternatives, that identifies a prospective condition the '
            'buyer expects would make initiating or reopening evaluation worthwhile.'
            '</div>'
            '</div>'
        )

    with c2:
        html(
            '<div class="bc-card">'
            '<div class="bc-label">CEE</div>'
            '<div class="bc-head-value">Contingency Elicitation Exchange</div>'
            '<div class="bc-muted" style="margin-top:8px">'
            'A salesperson behavior used after qualified present non-evaluation: invite the '
            'buyer to identify what future condition would make evaluation worthwhile before '
            'supplying another seller-generated reason to change.'
            '</div>'
            '</div>'
        )

    html('<div class="bc-h">The system built from the research</div>')

    cards = (
        (
            "1 · REMEMBER",
            "Engine 1",
            "Capture + Verification",
            "Preserve what the buyer actually said, separate evidence from inference, and require human confirmation.",
        ),
        (
            "2 · MONITOR",
            "Engine 2",
            "Monitoring",
            "Authorize what may be watched and preserve new evidence with its provenance and correction history.",
        ),
        (
            "3 · RECOGNIZE",
            "Engine 2",
            "Correspondence",
            "Require a human to decide whether current usable evidence corresponds to the buyer-defined condition.",
        ),
        (
            "4 · PREPARE",
            "Engine 3",
            "Re-engagement Preparation",
            "Convert eligible human-verified correspondence into grounded material for a salesperson to review.",
        ),
    )

    html(
        '<div class="bc-system-grid">'
        + "".join(
            '<div class="bc-card bc-system-card">'
            f'<div class="bc-label">{e(step)}</div>'
            f'<div class="bc-system-card-engine">{e(engine)}</div>'
            f'<div class="bc-system-card-title">{e(title)}</div>'
            f'<div class="bc-system-card-body">{e(body)}</div>'
            '</div>'
            for step, engine, title, body in cards
        )
        + '</div>'
    )

    notice(
        "Account state remains separate",
        "Active Opportunity, Conditionally Dormant, and No Plausible Path are account-management "
        "states. A confirmed condition, monitored signal, verified correspondence, or prepared draft "
        "does not automatically change account state.",
        "",
    )

    html('<div class="bc-h">What this completed synthetic case demonstrates</div>')

    metrics = (
        ("Account state", STATE_LABELS[view.account.state]),
        ("Evidence correction", f"{old_measurement} → {current_measurement}"),
        ("Correspondence", outcome.replace("_", " ").title()),
        ("Prepared artifacts", str(sum(len(p["artifacts"]) for p in view.preparations))),
    )

    html(
        '<div class="bc-metrics bc-metrics-four">'
        + "".join(
            '<div class="bc-metric">'
            f'<div class="bc-metric-value" style="font-size:18px">{e(value)}</div>'
            f'<div class="bc-metric-label">{e(label)}</div>'
            '</div>'
            for label, value in metrics
        )
        + '</div>'
    )

    html(
        '<div class="bc-card">'
        '<div class="bc-label">Riverside Outpatient Surgery Center · synthetic case</div>'
        '<div class="bc-muted">'
        'A human first confirms the buyer-defined condition and separately judges the account '
        'Conditionally Dormant. Monitoring later records an apparent 9% increase. A correction '
        'changes the current usable measurement to 7%. The earlier evidence stays in the historical '
        'record but is superseded. A human then verifies correspondence using the corrected evidence. '
        'Engine 3 prepares four grounded artifacts from the eligible record.'
        '</div>'
        '</div>'
    )

    notice(
        "Critical research boundary",
        "Correspondence is not buyer intent. Preparation is not permission to contact the buyer. "
        "Software verification on synthetic data is not empirical validation of the BAEC framework "
        "or evidence that it improves sales performance.",
        "warn",
    )

    if st.button(
        "Open full system walkthrough",
        key="overview-walkthrough",
        type="primary",
        use_container_width=True,
    ):
        go("Workspace", account_id, "Remember")
        st.rerun()

    system_overview_pdf = (
        Path(__file__).resolve().parents[1]
        / "docs"
        / "baec-system"
        / "BAEC_System_Research_and_Technical_Overview.pdf"
    )

    st.download_button(
        "Download System Research & Technical Overview",
        data=system_overview_pdf.read_bytes(),
        file_name="BAEC_System_Research_and_Technical_Overview.pdf",
        mime="application/pdf",
        key="overview-system-pdf",
        use_container_width=True,
    )

    html('<div class="bc-h" style="margin-top:24px">Individual engine releases</div>')

    releases = (
        (
            "Engine 1 · Capture + Verification",
            "Buyer evidence, human review, deterministic classification, and confirmation boundary.",
            "Open Engine 1 demo",
            "https://baec-engine1.streamlit.app",
        ),
        (
            "Engine 2 · Monitoring + Correspondence",
            "Authorized monitoring, evidence provenance, correction history, and human correspondence review.",
            "Open Engine 2 demo",
            "https://baec-engine2.streamlit.app",
        ),
        (
            "Engine 3 · Re-engagement Preparation",
            "Eligibility, grounding, disclosure boundaries, validation, and human review of prepared material.",
            "Open Engine 3 demo",
            "https://baec-engine3.streamlit.app",
        ),
    )

    release_cols = st.columns(3, gap="large")

    for col, (title, description, button, url) in zip(release_cols, releases):
        with col:
            html(
                '<div class="bc-release-copy">'
                f'<div class="bc-release-title">{e(title)}</div>'
                f'<div class="bc-release-desc">{e(description)}</div>'
                '</div>'
            )
            st.link_button(
                button,
                url,
                use_container_width=True,
            )

    st.caption(
        "The conceptual working paper is unpublished and is not packaged inside the public application. "
        "The individual engine research and technical materials remain available in the project repository."
    )
