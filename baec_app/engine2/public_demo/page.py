"""BAEC Engine 2 public demo (Streamlit): a guided, five-step walk from a confirmed buyer condition to human review.

    streamlit run streamlit_engine2_demo.py

A presentation layer only. The caller supplies a seed factory (scripts/engine2_public_demo_seed.py), which builds
each visitor's synthetic Engine 1 confirmation and Engine 2 state in memory. Every Engine 2 read is the Stage I
review packet (plus the Stage D authorized-source reader); the only write is Stage I submit_human_review, in the
submit button's callback, against the context_digest captured when the visitor opened the review. The locked
classifier derives the result; no control submits it. The review is split into four substeps for readability; the
visitor's answers are kept in this session between substeps and nothing is submitted before Submit Human Review.
Each visitor's state is a pair of serialized in-memory images in their own Streamlit session; nothing is written to
disk, and no AI, network, or credential is used. Stored text is rendered as plain text.
"""

from __future__ import annotations

from typing import Callable

import streamlit as st

from .. import persistence as store
from .. import review
from ..domain import CorrespondenceDimension, CorrespondenceOutcome, CrossCuttingCheckName
from ..operator_ui import views as operator_views
from . import content as copy

KEY = "pd2_"  # every key this page owns starts with this prefix, so a restart can clear all of them
STATUS_COLORS = {"USABLE": "green", "SUPERSEDED": "orange", "RETRACTED": "red", "INVALIDATED": "orange",
                 "REFUSED": "gray"}
STATUS_LABELS = {"USABLE": "CURRENT / USABLE", "SUPERSEDED": "SUPERSEDED", "RETRACTED": "RETRACTED",
                 "INVALIDATED": "INVALIDATED MEASUREMENT", "REFUSED": "REFUSED"}
STAGE_STYLE = {"done": ":green[✓ {number}] {label}", "current": ":blue[**● {number} {label}**]",
               "upcoming": ":gray[○ {number} {label}]"}
SUBSTEP_STYLE = {"done": ":green[**✓ {number}** {label}]", "current": ":blue[**● {number} {label}**]",
                 "upcoming": ":gray[○ {number} {label}]"}

# Every human-entered review field, by widget key. Answers are kept in KEY + "answers" between substeps.
FINDING_KEYS = tuple(f"{KEY}finding_{d.name}" for d in CorrespondenceDimension)
GROUP_KEYS = {g.key: tuple(f"{KEY}finding_{d.name}" for d in g.dimensions)
              + (f"{KEY}evidence_{g.key}", f"{KEY}reason_{g.key}") for g in copy.GROUPS}
QUALITY_KEYS = tuple(f"{KEY}{field}_{c.name}" for c in CrossCuttingCheckName
                     for field in ("check", "check_note", "check_evidence", "check_dimensions")) + (
    KEY + "sufficiency", KEY + "revalidation", KEY + "triggers", KEY + "alias")
SUBSTEP_KEYS = tuple(GROUP_KEYS[g.key] for g in copy.GROUPS) + (QUALITY_KEYS,)
ALL_KEYS = tuple(k for keys in SUBSTEP_KEYS for k in keys)


# --- session plumbing -------------------------------------------------------------------------------------------


def _clear() -> None:
    for key in [k for k in st.session_state if str(k).startswith(KEY)]:
        del st.session_state[key]


def _start(seed_factory: Callable) -> None:
    _clear()
    st.session_state[KEY + "seed"] = seed_factory()
    st.session_state[KEY + "step"] = 1


def _go(step: int) -> None:
    st.session_state[KEY + "step"] = step


def _connection():
    """This visitor's Engine 2 store, opened for one run and closed by the caller."""
    seed = st.session_state[KEY + "seed"]
    connection = store.open_engine2_database(":memory:")
    try:
        connection.deserialize(st.session_state.get(KEY + "engine2_image", seed.engine2_image))
        store.require_engine2_schema(connection)
    except BaseException:
        connection.close()
        raise
    return connection


def _packet(connection) -> review.ReviewPacket:
    seed = st.session_state[KEY + "seed"]
    return review.build_review_packet(connection, monitoring_plan_id=seed.monitoring_plan_id,
                                      plan_version=seed.plan_version, baec_id=seed.handoff.baec_id,
                                      candidate_id=seed.candidate_id, activation_id=seed.activation_id)


def _open_review() -> None:
    """Capture the exact review context the visitor is about to judge, and start an empty answer sheet."""
    connection = _connection()
    try:
        st.session_state[KEY + "digest"] = _packet(connection).context_digest
    finally:
        connection.close()
    st.session_state[KEY + "answers"] = {}
    st.session_state[KEY + "sub"] = 0
    _go(4)


def _save_answers() -> dict:
    """Copy every human-entered value currently in the session into the answer sheet. UI state only."""
    s = st.session_state
    answers = s.setdefault(KEY + "answers", {})
    for key in ALL_KEYS:
        if key in s:
            value = s[key]
            answers[key] = list(value) if isinstance(value, list) else value
    return answers


def _restore(keys) -> None:
    """Give the substep's widgets back exactly what the visitor entered before. Nothing else is ever set."""
    s, answers = st.session_state, st.session_state.get(KEY + "answers", {})
    for key in keys:
        if key not in s and key in answers:
            value = answers[key]
            s[key] = list(value) if isinstance(value, list) else value


def _substep(index: int) -> None:
    _save_answers()
    st.session_state[KEY + "sub"] = index


def _values() -> dict:
    return _as_values(_save_answers())


def _as_values(a: dict) -> dict:
    """The answer sheet in the form content.py reads. No value is added or changed."""
    values = {"sufficiency": a.get(KEY + "sufficiency"), "revalidation": a.get(KEY + "revalidation"),
              "triggers": a.get(KEY + "triggers"), "alias": a.get(KEY + "alias")}
    for group in copy.GROUPS:
        values[f"reason:{group.key}"] = a.get(f"{KEY}reason_{group.key}")
        values[f"evidence:{group.key}"] = a.get(f"{KEY}evidence_{group.key}")
        for d in group.dimensions:
            values[f"finding:{d.name}"] = a.get(f"{KEY}finding_{d.name}")
    for c in CrossCuttingCheckName:
        for field in ("check", "check_note", "check_evidence", "check_dimensions"):
            values[f"{field}:{c.name}"] = a.get(f"{KEY}{field}_{c.name}")
    return values


def _submit() -> None:
    s = st.session_state
    s.pop(KEY + "error", None)
    values = _values()
    missing = copy.missing_answers(values)
    if missing:
        s[KEY + "missing"] = missing
        return
    s.pop(KEY + "missing", None)
    seed = s[KEY + "seed"]
    connection = _connection()
    try:
        packet = _packet(connection)
        recorded = review.submit_human_review(
            connection, monitoring_plan_id=seed.monitoring_plan_id, plan_version=seed.plan_version,
            baec_id=seed.handoff.baec_id, candidate_id=seed.candidate_id, activation_id=seed.activation_id,
            context_digest=s[KEY + "digest"], review_id=operator_views.next_review_id(packet),
            reviewed_at=seed.review_at, recorded_at=seed.review_at, recorded_by=values["alias"],
            **copy.review_arguments(values))
        s[KEY + "engine2_image"] = connection.serialize()
        s[KEY + "result"] = (recorded.review.review_id, recorded.outcome.value)
        s[KEY + "step"] = 5
    except Exception as error:
        s[KEY + "error"] = operator_views.failure_message(error)
    finally:
        connection.close()


# --- shared pieces ---------------------------------------------------------------------------------------------


def _inert(value: object) -> None:
    """Stored text: plain text only, never Markdown or HTML."""
    st.text("" if value is None else str(value))


def _badge(status: str) -> None:
    st.badge(STATUS_LABELS[status], color=STATUS_COLORS[status])


def _header(step: int) -> None:
    title, badges = st.columns([3, 2], vertical_alignment="center")
    with title:
        st.markdown(f"**{copy.TITLE}**")
        st.caption(copy.SUBTITLE)
    with badges:
        with st.container(horizontal=True, horizontal_alignment="right"):
            st.badge("Research Prototype", color="gray")
            st.badge("Synthetic Data Only", color="gray")
    columns = st.columns(len(copy.STAGES))
    for number, (column, label) in enumerate(zip(columns, copy.STAGES), start=1):
        state = "current" if number == step else ("done" if number < step else "upcoming")
        column.markdown(STAGE_STYLE[state].format(number=number, label=label))
    st.divider()


def _footer() -> None:
    st.divider()
    st.caption(copy.FOOTER)
    st.caption(copy.SESSION_NOTE)


def _landing(seed_factory: Callable) -> None:
    st.title(copy.TITLE)
    st.subheader(copy.SUBTITLE)
    st.markdown(f"### {copy.ENGINE1_LINE}")
    st.markdown(copy.ENGINE2_LINE)
    with st.container(border=True):
        st.markdown(f"**{copy.BOUNDARY}**")
    with st.container(horizontal=True):
        for item in copy.NOTICE:
            st.badge(item, color="gray")
    st.button("Start Demo", key=KEY + "start", type="primary", on_click=_start, args=(seed_factory,))
    with st.expander("About this demonstration"):
        st.markdown("You will see one synthetic buyer condition, the evidence Engine 2 captured for it, and then make "
                    "the human correspondence judgment yourself. It takes three to five minutes.")
        st.caption(copy.AI_NOTE)


def _threshold(stringency) -> str:
    if stringency is None or stringency.numeric_value is None:
        return "none stated"
    return copy.threshold_phrase(stringency.comparator.value, stringency.numeric_value, stringency.unit)


# --- the five steps ----------------------------------------------------------------------------------------------


def _condition(seed) -> None:
    h = seed.handoff
    s = h.stringency
    st.header("1  What the buyer said")
    with st.container(border=True):
        st.caption("Synthetic account")
        st.markdown(f"**{seed.account_name}**")
        if h.buyer_role:
            st.caption("Buyer role")
            _inert(h.buyer_role)
        st.caption("The buyer's exact words")
        _inert(h.buyer_exact_statement)
        threshold, timing, status = st.columns(3)
        with threshold:
            st.caption("Threshold")
            st.markdown(f"**{_threshold(s)}**")
        with timing:
            st.caption("Timing")
            st.markdown(f"**{copy.timing_label(s.timing_text if s else None)}**")
        with status:
            st.caption("Status in Engine 1")
            st.badge("CONFIRMED", color="green")
    st.info(copy.CONDITION_NOT_INTENT)
    st.caption(copy.ENGINE2_STARTS_HERE)
    with st.expander("Technical details"):
        st.caption(copy.SYNTHETIC_DATES)
        _inert(f"Engine 1 BAEC: {h.baec_id}\nConfirmed by {h.confirmed_by} at {h.confirmed_at.isoformat()}\n"
               f"Staleness status: {h.staleness_status.value}\nHandoff: {h.engine1_reference}")
    st.button("Continue to Monitoring", key=KEY + "to_2", type="primary", on_click=_go, args=(2,))


def _monitoring(connection, packet: review.ReviewPacket) -> None:
    plan = packet.plan
    t = plan.threshold
    st.header("2  What Engine 2 monitored")
    with st.container(border=True):
        st.caption("Watching for")
        st.markdown(f"**{copy.watching_for(t.comparator.value, t.value, t.unit, plan.timing_context)}**" if t
                    else "**A change matching the buyer's own words.**")
    st.subheader("Authorized sources")
    for source_id in sorted(plan.authorized_source_ids):
        source = store.load_authorized_source(connection, source_id)
        with st.container(border=True):
            st.markdown(f"**{source.source_type.capitalize()}**")
            _inert(f"{source.owner_or_publisher}\nBasis: {source.legitimate_basis}"
                   + (f"\nLimitation: {source.known_limitations}" if source.known_limitations else ""))
    st.info(copy.SOURCES_MATTER)
    with st.expander("Technical details"):
        _inert("Questions a human answers about new evidence (required by the plan): "
               + ", ".join(d.label for d in CorrespondenceDimension if d in plan.required_dimensions)
               + "\nNot applicable under this plan: "
               + ", ".join(d.label for d in CorrespondenceDimension if d in plan.not_applicable_dimensions)
               + f"\nMonitoring Plan {plan.monitoring_plan_id}, version {plan.plan_version}\nActivation "
               f"{packet.activation_id} (Engine 1 reported the BAEC {packet.active_plan.currentness.staleness_status.value})"
               f"\nEngine 1 reference: {plan.engine1_currentness_reference}")
    with st.container(horizontal=True):
        st.button("Back", key=KEY + "back_1", on_click=_go, args=(1,))
        st.button("See New Evidence", key=KEY + "to_3", type="primary", on_click=_go, args=(3,))


def _evidence_card(row) -> None:
    with st.container(border=True):
        top, badge = st.columns([3, 2], vertical_alignment="center")
        with top:
            st.caption(f"{row.observed_at[:10]} · {row.source_type}")
        with badge:
            with st.container(horizontal=True, horizontal_alignment="right"):
                _badge(row.status)
        _inert(row.exact_text)
        if row.superseded_by:
            word = "Retracted" if row.status == "RETRACTED" else "Corrected"
            st.caption(f"{word} by a later item ({row.superseded_by}). Kept in the history; no longer usable.")
        st.caption(row.observation_id)


def _measurement_card(m, usable: bool) -> None:
    with st.container(border=True):
        top, badge = st.columns([3, 2], vertical_alignment="center")
        with top:
            st.markdown(f"**Price change: {copy.percent(m.output_value, m.output_unit)}**")
        with badge:
            with st.container(horizontal=True, horizontal_alignment="right"):
                _badge("USABLE" if usable else "INVALIDATED")
        inputs = ", ".join(f"{i.quantity.replace('_', ' ')} {i.value} {i.unit}" for i in m.inputs)
        _inert(f"Computed exactly from {m.inputs[0].observation_id}: {inputs}")
        if not usable:
            st.caption("Its source was corrected, so this value stays in the history but cannot be used.")


def _evidence(packet: review.ReviewPacket) -> None:
    rows = copy.timeline(packet, operator_views.evidence_rows(packet))
    usable = [m for m in packet.ledger.measurements if m.measurement_id in packet.usable_measurement_ids]
    historical = [m for m in packet.ledger.measurements if m.measurement_id not in packet.usable_measurement_ids]
    stringency = st.session_state[KEY + "seed"].handoff.stringency
    st.header("3  New evidence appeared")
    st.caption(copy.SYNTHETIC_DATES)
    with st.container(border=True):
        threshold, measured, timing = st.columns(3)
        with threshold:
            st.caption("Buyer threshold")
            st.markdown(f"**{_threshold(stringency)}**")
        with measured:
            st.caption("Current usable measurement")
            st.markdown("**" + (", ".join(copy.percent(m.output_value, m.output_unit) for m in usable) or "none")
                        + "**")
        with timing:
            st.caption("Relevant timing")
            st.markdown(f"**{copy.timing_label(packet.plan.timing_context)}**")
        if historical and usable:
            old = ", ".join(copy.percent(m.output_value, m.output_unit) for m in historical)
            now = copy.percent(usable[0].output_value, usable[0].output_unit)
            st.markdown(f"{old} initially reported → corrected → **{now} current**")
            st.caption(f"{old} is kept in the history as an invalidated measurement. It is not used.")
    st.markdown(f"**{copy.HISTORY_LINE}**")
    current, history = st.tabs([copy.CURRENT_HEADING, copy.HISTORY_HEADING])
    with current:
        primary = {i.observation_id for m in usable for i in m.inputs}
        for m in usable:
            _measurement_card(m, True)
        for row in rows:
            if row.status == "USABLE" and row.observation_id in primary:
                _evidence_card(row)
        others = [row for row in rows if row.status == "USABLE" and row.observation_id not in primary]
        if others:
            with st.expander("Other current context"):
                for row in others:
                    _evidence_card(row)
    with history:
        st.caption("Every item captured for this plan, in the order it was observed. Nothing is edited or hidden.")
        for row in rows:
            _evidence_card(row)
        for m in packet.ledger.measurements:
            _measurement_card(m, m.measurement_id in packet.usable_measurement_ids)
        for item in packet.ledger.rejected_items:
            with st.container(border=True):
                _badge("REFUSED")
                _inert(f"{item.item_id}: {item.reason}")
                st.caption("Refused at capture. It never became evidence.")
    st.button("Review Possible Correspondence", key=KEY + "to_4", type="primary", on_click=_open_review)


def _finding_controls(packet: review.ReviewPacket, group, citations: dict) -> None:
    for d in group.dimensions:
        note = "" if d in packet.plan.required_dimensions else " The plan marks this not applicable."
        st.segmented_control(f"**{d.label}**", copy.FINDINGS, key=f"{KEY}finding_{d.name}",
                             format_func=copy.FINDING_LABELS.get, help=copy.QUESTIONS[d] + note)
    st.multiselect("Evidence you are relying on", list(citations), format_func=citations.get,
                   key=f"{KEY}evidence_{group.key}")
    st.text_area("Your reason", key=f"{KEY}reason_{group.key}", height=68)
    st.caption(copy.GROUP_NOTE)


def _quality_controls(packet: review.ReviewPacket, check_citations: dict) -> None:
    s = st.session_state
    st.markdown("**Evidence quality checks**")
    for c in CrossCuttingCheckName:
        st.segmented_control(f"**{c.label}**", tuple(copy.CHECK_LABELS), key=f"{KEY}check_{c.name}",
                             format_func=copy.CHECK_LABELS.get, help=copy.CHECK_QUESTIONS[c])
        if s.get(f"{KEY}check_{c.name}") == "CONCERN":
            st.text_area("Describe the concern", key=f"{KEY}check_note_{c.name}", height=68)
            st.multiselect("Questions it affects", [d.name for d in CorrespondenceDimension],
                           format_func=lambda n: CorrespondenceDimension[n].label, key=f"{KEY}check_dimensions_{c.name}")
            st.multiselect("Evidence it concerns", list(check_citations), format_func=check_citations.get,
                           key=f"{KEY}check_evidence_{c.name}")
    st.markdown("**Your conclusion**")
    st.segmented_control("Is the evidence sufficient for a human correspondence finding?", copy.SUFFICIENCY,
                         key=KEY + "sufficiency", format_func=copy.SUFFICIENCY_LABELS.get)
    st.segmented_control("Should the buyer's condition itself be revalidated before relying on it?",
                         tuple(copy.REVALIDATION_LABELS), key=KEY + "revalidation",
                         format_func=copy.REVALIDATION_LABELS.get)
    if s.get(KEY + "revalidation") == "REQUIRED":
        st.text_area("Why (one reason per line)", key=KEY + "triggers", height=68)
    st.text_input("Reviewer alias", key=KEY + "alias", help=copy.ALIAS_NOTE)


def _human_review(packet: review.ReviewPacket) -> None:
    s = st.session_state
    sub = s.get(KEY + "sub", 0)
    _restore(SUBSTEP_KEYS[sub])
    st.header("4  Human review")
    st.markdown(f"**{copy.REVIEW_HEADLINE}**")
    st.caption(copy.REVIEW_INTRO)
    current = {**s.get(KEY + "answers", {}), **{k: s[k] for k in ALL_KEYS if k in s}}
    findings_done = sum(1 for key in FINDING_KEYS if current.get(key))
    checks_done = sum(1 for c in CrossCuttingCheckName if current.get(f"{KEY}check_{c.name}"))
    st.markdown("**Review progress**")
    st.progress(findings_done / len(FINDING_KEYS),
                text=f"{findings_done} of {len(FINDING_KEYS)} findings completed · {checks_done} of 4 quality checks")
    values = _as_values(current)
    columns = st.columns(len(copy.SUBSTEP_NAV))
    for index, (column, label) in enumerate(zip(columns, copy.SUBSTEP_NAV)):
        # A check means the substep is fully answered; visiting it is not enough. Display only.
        state = "done" if copy.substep_complete(values, index) else ("current" if index == sub else "upcoming")
        number, name = label.split(" ", 1)
        column.markdown(SUBSTEP_STYLE[state].format(number=number, label=name))
    with st.expander("Show walkthrough guidance"):
        for line in copy.GUIDANCE:
            st.markdown(f"- {line}")
    with st.expander("Evidence summary"):
        for row in copy.timeline(packet, operator_views.evidence_rows(packet)):
            _inert(f"[{STATUS_LABELS[row.status]}] {row.observation_id}: {row.exact_text}")
        for m in packet.ledger.measurements:
            status = "USABLE" if m.measurement_id in packet.usable_measurement_ids else "INVALIDATED"
            _inert(f"[{STATUS_LABELS[status]}] {m.measurement_id}: {copy.percent(m.output_value, m.output_unit)}")
    with st.container(border=True):
        st.subheader(copy.SUBSTEPS[sub])
        if sub < len(copy.GROUPS):
            _finding_controls(packet, copy.GROUPS[sub], operator_views.finding_citations(packet))
        else:
            _quality_controls(packet, operator_views.check_citations(packet))
    _save_answers()
    if s.get(KEY + "missing"):
        st.warning("Not submitted yet. Still to answer: " + "; ".join(s[KEY + "missing"]) + ".")
    if s.get(KEY + "error"):
        kind, text = s[KEY + "error"]
        (st.error if kind in ("integrity", "context", "stale") else st.warning)(text)
    with st.container(horizontal=True):
        if sub > 0:
            st.button("Back", key=KEY + "sub_back", on_click=_substep, args=(sub - 1,))
        if sub < len(copy.SUBSTEPS) - 1:
            st.button("Next", key=KEY + "sub_next", type="primary", on_click=_substep, args=(sub + 1,))
        else:
            st.button("Submit Human Review", key=KEY + "submit", type="primary", on_click=_submit)


def _result(packet: review.ReviewPacket) -> None:
    review_id, outcome = st.session_state[KEY + "result"]
    result = CorrespondenceOutcome(outcome)
    st.header("5  Result")
    with st.container(border=True):
        st.caption("Authoritative Engine 2 result")
        st.subheader(copy.OUTCOME_HEADLINES[result])
        st.badge(outcome, color="green" if result is CorrespondenceOutcome.HUMAN_VERIFIED_CORRESPONDENCE else "gray")
        st.markdown(copy.OUTCOME_TEXT[result])
        st.caption("Derived by the locked Engine 2 rule from your answers.")
    if result is CorrespondenceOutcome.HUMAN_VERIFIED_CORRESPONDENCE:
        usable = [m for m in packet.ledger.measurements if m.measurement_id in packet.usable_measurement_ids]
        with st.container(border=True):
            st.markdown("**Why this matched**")
            threshold, measured, timing = st.columns(3)
            with threshold:
                st.caption("Buyer threshold")
                st.markdown(f"**{_threshold(st.session_state[KEY + 'seed'].handoff.stringency)}**")
            with measured:
                st.caption("Current usable evidence")
                st.markdown("**" + (", ".join(copy.percent(m.output_value, m.output_unit) for m in usable) or "none")
                            + "**")
            with timing:
                st.caption("Relevant timing")
                st.markdown(f"**{copy.timing_label(packet.plan.timing_context)}**")
            st.markdown(copy.WHY_MATCHED)
    with st.container(border=True):
        st.markdown("**What this does NOT mean**")
        for line in copy.NOT_MEAN:
            st.markdown(f"- {line}")
    st.markdown(f"**{copy.BOUNDARY}**")
    with st.container(border=True):
        st.markdown(copy.flow_line())
    with st.expander("Technical details"):
        _inert(f"Recorded as {review_id}.")
        for h in packet.review_history:
            _inert(f"{h.review.review_id} by {h.review.reviewer}: {h.outcome.value}")
    st.button("Restart Demo", key=KEY + "restart", type="primary", on_click=_clear)


def main(seed_factory: Callable) -> None:
    st.set_page_config(page_title=copy.PAGE_TITLE, layout="centered")
    if KEY + "seed" not in st.session_state:
        _landing(seed_factory)
        _footer()
        return
    step = st.session_state[KEY + "step"]
    _header(step)
    seed = st.session_state[KEY + "seed"]
    if step == 1:
        _condition(seed)
    else:
        connection = _connection()
        try:
            packet = _packet(connection)
            if step == 2:
                _monitoring(connection, packet)
            elif step == 3:
                _evidence(packet)
            elif step == 4:
                _human_review(packet)
            else:
                _result(packet)
        except (review.ReviewContextError, store.Engine2PersistenceError) as error:
            kind, text = operator_views.failure_message(error)
            st.error(text)
        finally:
            connection.close()
    _footer()
