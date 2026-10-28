"""Daily seller workflow for the BAEC CRM operating layer."""

from __future__ import annotations

from datetime import datetime

import streamlit as st

from . import crm_ai
from . import engine1_adapter as e1a
from .console_service import STATE_LABELS, AccountView, Console, ConsoleService
from .ui_common import (
    STAGE_LABELS,
    attempt,
    e,
    go,
    html,
    notice,
    review_target,
    show_flash,
    when,
    write_disabled,
    write_label,
)


def _contact_label(contact: dict) -> str:
    if contact.get("title"):
        return f"{contact['name']} · {contact['title']}"
    return contact["name"]


def _due_text(task: dict) -> str:
    due = task.get("due_date")
    if due is None:
        return "No due date"
    return due.strftime("%b %d").replace(" 0", " ")




def _condition_form_from_analysis(analysis) -> e1a.ConditionForm:
    criteria = {
        "PRESENT_NON_EVALUATION": {
            "finding": analysis.present_non_evaluation.finding,
            "excerpt": analysis.present_non_evaluation.evidence_excerpt,
            "rationale": analysis.present_non_evaluation.rationale,
        },
        "PROSPECTIVE_CONDITION": {
            "finding": analysis.prospective_condition.finding,
            "excerpt": analysis.prospective_condition.evidence_excerpt,
            "rationale": analysis.prospective_condition.rationale,
        },
        "BUYER_ARTICULATION": {
            "finding": analysis.buyer_articulation.finding,
            "excerpt": analysis.buyer_articulation.evidence_excerpt,
            "rationale": analysis.buyer_articulation.rationale,
        },
        "EVALUATION_LINKAGE": {
            "finding": analysis.evaluation_linkage.finding,
            "excerpt": analysis.evaluation_linkage.evidence_excerpt,
            "rationale": analysis.evaluation_linkage.rationale,
        },
    }

    return e1a.ConditionForm(
        criteria=criteria,
        origin=analysis.articulation_origin,
        elicitation=analysis.elicitation_mode,
        threshold_text=analysis.threshold_text,
        comparator=analysis.comparator,
        threshold_value=analysis.numeric_value,
        threshold_unit=analysis.unit,
        qualitative_term=analysis.qualitative_term,
        recurrence_text=analysis.recurrence_text,
        timing_text=analysis.timing_text,
    )


def _promotion_problem(
    analysis,
    *,
    raw_notes: str,
    buyer_statement: str,
) -> str | None:
    """Conservative gate before any AI proposal reaches Engine 1."""

    statement = buyer_statement.strip()

    if not analysis.possible_baec:
        return (
            "AI did not identify this interaction as a possible "
            "buyer-articulated condition."
        )

    if not statement:
        return "Exact buyer language is required."

    if statement not in raw_notes:
        return (
            "The buyer statement must remain an exact passage from "
            "the original interaction notes."
        )

    evidence = (
        analysis.present_non_evaluation.evidence_excerpt,
        analysis.prospective_condition.evidence_excerpt,
        analysis.buyer_articulation.evidence_excerpt,
        analysis.evaluation_linkage.evidence_excerpt,
    )

    unsupported = [
        excerpt
        for excerpt in evidence
        if excerpt and excerpt not in statement
    ]

    if unsupported:
        return (
            "One or more proposed BAEC evidence excerpts are outside "
            "the exact buyer statement. Save the CRM activity or "
            "review the condition manually in Remember."
        )

    for label, value in (
        ("threshold wording", analysis.threshold_text),
        ("timing wording", analysis.timing_text),
    ):
        if value and value not in statement:
            return (
                f"The proposed {label} is outside the exact buyer "
                "statement. Review the condition manually in Remember."
            )

    return None


def _interaction_intake(
    service: ConsoleService,
    view: AccountView,
    contact_map: dict[str, dict],
) -> None:
    """One-box CRM intake. AI proposes; the rep decides what is saved."""

    account_id = view.account.account_id

    ai_token = st.session_state.get(
        "baec-ai-token",
        "",
    )

    html('<div class="bc-h">Log interaction</div>')

    st.caption(
        "Paste the messy call, email, meeting, or field notes once. "
        "BAEC can organize them before anything enters the BAEC workflow."
    )

    c1, c2, c3 = st.columns([1.2, 1.2, 1])

    contact_id = c1.selectbox(
        "Contact",
        [None, *contact_map],
        format_func=lambda cid:
            "No contact selected"
            if cid is None
            else _contact_label(contact_map[cid]),
        key=f"crm-intake-contact-{account_id}",
    )

    interaction_type = c2.selectbox(
        "Interaction type",
        [
            "Call",
            "Email",
            "Meeting",
            "In person",
            "Other",
        ],
        key=f"crm-intake-type-{account_id}",
    )

    day = c3.date_input(
        "Date",
        value=datetime.now().date(),
        key=f"crm-intake-day-{account_id}",
    )

    at = st.time_input(
        "Time",
        value=datetime.now().replace(
            second=0,
            microsecond=0,
        ).time(),
        key=f"crm-intake-time-{account_id}",
    )

    raw_key = f"crm-intake-raw-{account_id}"

    raw_notes = st.text_area(
        "Call, email, meeting, or field notes",
        key=raw_key,
        height=150,
        placeholder=(
            "Example: Spoke with Morgan today. Morgan said, "
            "\"We're not evaluating other suppliers today. "
            "If our contracted pricing increases by at least 7% "
            "at the April renewal, we would compare other options.\""
        ),
    )

    occurred_at = (
        datetime.combine(day, at)
        .astimezone()
    )

    b1, b2, b3 = st.columns([1.2, 1.6, 2.2])

    if b1.button(
        "Analyze with AI",
        key=f"crm-analyze-{account_id}",
        type="primary",
        disabled=(
            not raw_notes.strip()
            or not crm_ai.available(ai_token)
        ),
        use_container_width=True,
    ):
        contact = (
            contact_map.get(contact_id)
            if contact_id
            else None
        )

        result = attempt(
            lambda: crm_ai.analyze_interaction(
                raw_text=raw_notes,
                contact_name=(
                    contact["name"]
                    if contact else ""
                ),
                contact_title=(
                    contact.get("title") or ""
                    if contact else ""
                ),
                token=ai_token,
            ),
            failure="AI interaction analysis failed.",
        )

        if result:
            st.session_state[
                f"crm-analysis-{account_id}"
            ] = result

            st.session_state[
                f"crm-analysis-source-{account_id}"
            ] = raw_notes

            st.rerun()

    if b2.button(
        write_label("Save activity without AI"),
        key=f"crm-save-manual-{account_id}",
        disabled=(
            not raw_notes.strip()
            or write_disabled()
        ),
        use_container_width=True,
    ):
        contact = (
            contact_map.get(contact_id)
            if contact_id
            else None
        )

        activity_id = attempt(
            lambda: service.crm_add_activity(
                account_id,
                contact_id=contact_id,
                interaction_type=interaction_type,
                occurred_at=occurred_at,
                raw_notes=raw_notes,
                summary="",
                buyer_role=(
                    contact.get("title") or ""
                    if contact else ""
                ),
                buyer_statement="",
                seller_notes="",
                ai_assisted=False,
            ),
            success="Activity recorded.",
        )

        if activity_id:
            st.session_state.pop(raw_key, None)
            st.session_state.pop(
                f"crm-analysis-{account_id}",
                None,
            )
            st.session_state.pop(
                f"crm-analysis-source-{account_id}",
                None,
            )
            st.rerun()

    with b3:
        if crm_ai.available(ai_token):
            st.caption(
                "AI proposes structure only. "
                "You decide what is saved."
            )
        else:
            st.caption(
                "Connect your Claude API key in the sidebar. "
                "Manual CRM activity still works."
            )

    analysis = st.session_state.get(
        f"crm-analysis-{account_id}"
    )

    source = st.session_state.get(
        f"crm-analysis-source-{account_id}",
        "",
    )

    if analysis and source != raw_notes:
        notice(
            "Notes changed",
            "Analyze again before using the previous AI suggestion.",
            "warn",
        )
        analysis = None

    if not analysis:
        return

    if analysis.possible_baec:
        notice(
            "Possible buyer-articulated condition",
            (
                analysis.caution
                or "Review the extracted language before saving."
            ),
            "warn",
        )
    else:
        notice(
            "Normal CRM activity",
            (
                analysis.caution
                or "No clear BAEC condition was identified."
            ),
        )

    with st.container(border=True):
        html(
            '<div class="bc-label">'
            'AI-assisted review'
            '</div>'
        )

        summary = st.text_area(
            "Interaction summary",
            value=analysis.summary,
            key=f"crm-ai-summary-{account_id}",
        )

        buyer_role = st.text_input(
            "Buyer role",
            value=analysis.buyer_role,
            key=f"crm-ai-role-{account_id}",
        )

        buyer_statement = st.text_area(
            "Exact buyer statement",
            value=analysis.buyer_statement,
            key=f"crm-ai-statement-{account_id}",
            help=(
                "This must remain exact buyer language if it is "
                "later promoted into BAEC."
            ),
        )

        seller_notes = st.text_area(
            "Seller context",
            value=analysis.seller_notes,
            key=f"crm-ai-seller-{account_id}",
        )

        st.markdown(
            f"**Suggested next step:** "
            f"{analysis.suggested_next_action}"
        )

        promotion_problem = _promotion_problem(
            analysis,
            raw_notes=raw_notes,
            buyer_statement=buyer_statement,
        )
        can_promote = promotion_problem is None

        # Both paths stay explicit human choices. The BAEC review path leads only when the exact-evidence
        # checks pass; otherwise CRM-only save leads and the reason BAEC review is unavailable is shown.
        def promote_button() -> bool:
            return st.button(
                write_label("Save & review BAEC condition →"),
                key=f"crm-promote-{account_id}",
                type="primary" if can_promote else "secondary",
                disabled=write_disabled() or not can_promote,
                use_container_width=True,
            )

        def crm_only_button() -> bool:
            return st.button(
                write_label("Save to CRM only"),
                key=f"crm-save-ai-{account_id}",
                type="secondary" if can_promote else "primary",
                disabled=write_disabled(),
                use_container_width=True,
            )

        if can_promote:
            promote = promote_button()
            st.caption(
                "Saves the CRM activity and opens the Engine 1 preview. "
                "Nothing is confirmed until you confirm it."
            )
            save_only = crm_only_button()
        else:
            save_only = crm_only_button()
            promote = promote_button()
            st.caption(
                "BAEC review unavailable: "
                + promotion_problem
            )

        if save_only:
            activity_id = attempt(
                lambda: service.crm_add_activity(
                    account_id,
                    contact_id=contact_id,
                    interaction_type=interaction_type,
                    occurred_at=occurred_at,
                    raw_notes=raw_notes,
                    summary=summary,
                    buyer_role=buyer_role,
                    buyer_statement=buyer_statement,
                    seller_notes=seller_notes,
                    ai_assisted=True,
                ),
                success="AI-assisted CRM activity recorded.",
            )

            if activity_id:
                st.session_state.pop(raw_key, None)
                st.session_state.pop(
                    f"crm-analysis-{account_id}",
                    None,
                )
                st.session_state.pop(
                    f"crm-analysis-source-{account_id}",
                    None,
                )
                st.rerun()

        if promote:
            activity_id = attempt(
                lambda: service.crm_add_activity(
                    account_id,
                    contact_id=contact_id,
                    interaction_type=interaction_type,
                    occurred_at=occurred_at,
                    raw_notes=raw_notes,
                    summary=summary,
                    buyer_role=buyer_role,
                    buyer_statement=buyer_statement,
                    seller_notes=seller_notes,
                    ai_assisted=True,
                ),
                failure="Could not save the CRM activity.",
            )

            if activity_id:
                interaction_id = attempt(
                    lambda: service.record_interaction(
                        account_id,
                        buyer_role=buyer_role,
                        occurred_at=occurred_at,
                        buyer_statement=buyer_statement,
                        seller_notes=seller_notes,
                    ),
                    failure=(
                        "The CRM activity was saved, but the "
                        "Engine 1 interaction could not be created."
                    ),
                )

                if interaction_id:
                    attempt(
                        lambda: service.crm_attach_engine1_interaction(
                            activity_id,
                            interaction_id,
                        )
                    )

                    attempt(
                        lambda: service.crm_link_interaction(
                            interaction_id,
                            account_id,
                            contact_id=contact_id,
                            interaction_type=interaction_type,
                        )
                    )

                    form = _condition_form_from_analysis(
                        analysis
                    )

                    draft_id = attempt(
                        lambda: service.capture_condition(
                            account_id,
                            interaction_id,
                            form,
                        ),
                        failure=(
                            "The activity and interaction were saved, "
                            "but Engine 1 refused the proposed "
                            "condition draft."
                        ),
                    )

                    if draft_id:
                        st.session_state[
                            f"preview-{draft_id}"
                        ] = True

                        st.session_state.pop(
                            raw_key,
                            None,
                        )

                        st.session_state.pop(
                            f"crm-analysis-{account_id}",
                            None,
                        )

                        st.session_state.pop(
                            f"crm-analysis-source-{account_id}",
                            None,
                        )

                        go(
                            "Workspace",
                            account_id,
                            "Remember",
                            draft_id,
                        )

                        st.rerun()

        from .ui_common import boundary

        boundary(
            "AI may propose the condition. Engine 1 still classifies it, "
            "and a human must preview and confirm it before monitoring. "
            "Nothing here authorizes outreach."
        )

def account_overview(
    service: ConsoleService,
    view: AccountView,
) -> None:
    """The salesperson's operational view of one account."""

    account_id = view.account.account_id

    contacts = service.crm_contacts(account_id)
    active_contacts = [c for c in contacts if c["active"]]

    contact_map = {
        c["contact_id"]: c
        for c in active_contacts
    }

    tasks = service.crm_tasks(
        account_id=account_id,
        status="OPEN",
    )

    # ------------------------------------------------------------------
    # Account snapshot
    # ------------------------------------------------------------------

    account_state = STATE_LABELS[view.account.state]
    stage_label = STAGE_LABELS.get(view.stage, view.stage)

    html(
        '<div class="bc-account-metrics">'
        '<div class="bc-account-metric">'
        '<div class="bc-account-metric-label">Account state</div>'
        f'<div class="bc-account-metric-value">{e(account_state)}</div>'
        '</div>'
        '<div class="bc-account-metric">'
        '<div class="bc-account-metric-label">BAEC stage</div>'
        f'<div class="bc-account-metric-value">{e(stage_label)}</div>'
        '</div>'
        '<div class="bc-account-metric">'
        '<div class="bc-account-metric-label">Contacts</div>'
        f'<div class="bc-account-metric-value">{len(active_contacts)}</div>'
        '</div>'
        '<div class="bc-account-metric">'
        '<div class="bc-account-metric-label">Open tasks</div>'
        f'<div class="bc-account-metric-value">{len(tasks)}</div>'
        '</div>'
        '</div>'
    )

    if view.current_baec is not None:
        statement = (
            view.current_baec
            .candidate
            .buyer_exact_statement
        )

        html(
            '<div class="bc-card">'
            '<div class="bc-label">Buyer condition</div>'
            f'<div class="bc-quote">{e(statement)}</div>'
            '<div class="bc-label" style="margin-top:12px">'
            'BAEC next action'
            '</div>'
            f'<div>{e(view.next_action)}</div>'
            '</div>'
        )
    else:
        notice(
            "No confirmed buyer condition",
            (
                "Record buyer interactions normally. "
                "If a buyer-articulated condition exists, "
                "BAEC can capture and monitor it."
            ),
        )

    # ------------------------------------------------------------------
    # Continue BAEC
    # ------------------------------------------------------------------

    html('<div class="bc-h">Next move</div>')

    stage_section = {
        "REMEMBER": "Remember",
        "MONITOR": "Monitor",
        "RECOGNIZE": "Recognize",
        "PREPARE": "Prepare",
        "PREPARED": "Prepare",
    }.get(view.stage, "Remember")

    action_label = {
        "REMEMBER": "Review buyer condition →",
        "MONITOR": "Open monitoring →",
        "RECOGNIZE": "Review evidence →",
        "PREPARE": "Open preparation →",
        "PREPARED": "View preparation →",
    }.get(view.stage, "Continue workflow →")

    c1, c2 = st.columns([1.35, 3])

    if c1.button(
        action_label,
        key=f"overview-next-{account_id}",
        type="primary",
        use_container_width=True,
    ):
        go(
            "Workspace",
            account_id,
            stage_section,
        )
        st.rerun()

    with c2:
        st.markdown(
            f"**{view.next_action}**  \n"
            "BAEC recommends the step; the salesperson makes the decision."
        )

    _interaction_intake(
        service,
        view,
        contact_map,
    )

    # ------------------------------------------------------------------
    # Contacts
    # ------------------------------------------------------------------

    html('<div class="bc-h">Contacts</div>')

    if active_contacts:
        rows = [
            {
                "Name": c["name"],
                "Title": c["title"] or "",
                "Email": c["email"] or "",
                "Phone": c["phone"] or "",
            }
            for c in active_contacts
        ]

        st.dataframe(
            rows,
            hide_index=True,
            use_container_width=True,
        )
    else:
        st.caption("No contacts added yet.")

    with st.expander(
        "+ Add contact",
        expanded=not active_contacts,
    ):
        with st.form(
            f"overview-add-contact-{account_id}",
            clear_on_submit=True,
            enter_to_submit=False,
        ):
            c1, c2 = st.columns(2)

            name = c1.text_input("Name")
            title = c2.text_input("Title / role")

            email = c1.text_input("Email")
            phone = c2.text_input("Phone")

            notes = st.text_input(
                "Contact notes (optional)"
            )

            if st.form_submit_button(
                write_label("Add contact"),
                type="primary",
                disabled=write_disabled(),
            ):
                contact_id = attempt(
                    lambda: service.crm_add_contact(
                        account_id,
                        name=name,
                        title=title,
                        email=email,
                        phone=phone,
                        notes=notes,
                    ),
                    success="Contact added.",
                )

                if contact_id:
                    st.rerun()

    # Refresh after any possible additions on rerun.
    contacts = service.crm_contacts(account_id)
    active_contacts = [c for c in contacts if c["active"]]

    contact_map = {
        c["contact_id"]: c
        for c in active_contacts
    }

    # ------------------------------------------------------------------
    # Tasks
    # ------------------------------------------------------------------

    html('<div class="bc-h">Tasks</div>')

    if not tasks:
        st.caption("No open tasks.")

    for task in tasks:
        c1, c2 = st.columns([5, 1])

        c1.markdown(
            f"**{task['title']}**  \n"
            f"{_due_text(task)} · "
            f"{task['priority'].title()}"
            + (
                f" · {task['notes']}"
                if task.get("notes")
                else ""
            )
        )

        if c2.button(
            "Complete",
            key=f"overview-complete-{task['task_id']}",
            use_container_width=True,
        ):
            if attempt(
                lambda tid=task["task_id"]:
                    service.crm_complete_task(
                        tid,
                    ),
                success="Task completed.",
            ):
                st.rerun()

    with st.expander("+ Add task"):
        with st.form(
            f"overview-add-task-{account_id}",
            clear_on_submit=True,
            enter_to_submit=False,
        ):
            title = st.text_input(
                "Task"
            )

            c1, c2 = st.columns(2)

            due_date = c1.date_input(
                "Due date"
            )

            priority = c2.selectbox(
                "Priority",
                ["NORMAL", "HIGH", "LOW"],
                format_func=str.title,
            )

            contact_id = st.selectbox(
                "Contact (optional)",
                [None, *contact_map],
                format_func=lambda cid:
                    "None"
                    if cid is None
                    else _contact_label(contact_map[cid]),
            )

            notes = st.text_input(
                "Task notes (optional)"
            )

            if st.form_submit_button(
                write_label("Add task"),
                type="primary",
                disabled=write_disabled(),
            ):
                task_id = attempt(
                    lambda: service.crm_add_task(
                        account_id,
                        contact_id=contact_id,
                        title=title,
                        due_date=due_date,
                        priority=priority,
                        notes=notes,
                    ),
                    success="Task added.",
                )

                if task_id:
                    st.rerun()

    # ------------------------------------------------------------------
    # Recent activity
    # ------------------------------------------------------------------

    html('<div class="bc-h">Recent interactions</div>')

    if not view.interactions:
        st.caption(
            "No buyer interactions recorded yet."
        )

    for interaction, meta in reversed(
        view.interactions[-5:]
    ):
        meta = meta or {}

        link = service.crm_interaction_link(
            interaction.interaction_id
        )

        contact = None

        if (
            link
            and link.get("contact_id")
            and link["contact_id"] in contact_map
        ):
            contact = contact_map[
                link["contact_id"]
            ]

        who = (
            _contact_label(contact)
            if contact
            else (
                meta.get("buyer_role")
                or "Buyer"
            )
        )

        interaction_type = (
            link.get("interaction_type")
            if link
            else "Interaction"
        )

        statement = (
            meta.get("buyer_statement")
            or interaction.text
        )

        html(
            '<div class="bc-card">'
            '<div class="bc-label">'
            f'{e(interaction_type)} · '
            f'{e(who)} · '
            f'{e(when(interaction.occurred_at))}'
            '</div>'
            f'<div class="bc-quote">{e(statement)}</div>'
            + (
                '<div class="bc-muted">'
                f'{e(meta["seller_notes"])}'
                '</div>'
                if meta.get("seller_notes")
                else ""
            )
            + '</div>'
        )


def today(console: Console) -> None:
    """The salesperson's daily operating queue."""

    from .ui_common import heading

    heading(
        "Today",
        "Today's work",
        (
            "Follow-ups, human reviews, and accounts "
            "that need attention."
        ),
    )

    show_flash()

    views = console.account_views()

    names = {
        v.account.account_id:
            (
                v.display_name
                + (" (synthetic)" if v.synthetic else "")
            )
        for v in views
    }

    today_date = datetime.now().date()

    task_rows = []

    for service in console.services:
        for task in service.crm_tasks(
            status="OPEN"
        ):
            task_rows.append(
                (service, task)
            )

    overdue = [
        row
        for row in task_rows
        if (
            row[1]["due_date"] is not None
            and row[1]["due_date"] < today_date
        )
    ]

    due_today = [
        row
        for row in task_rows
        if row[1]["due_date"] == today_date
    ]

    queue = console.review_queue()
    pending = queue["pending"]

    preparation_work = [
        v
        for v in views
        if v.next_action in {
            "Request preparation",
            "Generate preparation",
            "Review prepared drafts",
        }
    ]

    html(
        '<div class="bc-metrics">'
        f'<div class="bc-metric">'
        f'<div class="bc-metric-value">{len(due_today)}</div>'
        '<div class="bc-metric-label">Due today</div>'
        '</div>'
        f'<div class="bc-metric">'
        f'<div class="bc-metric-value">{len(overdue)}</div>'
        '<div class="bc-metric-label">Overdue</div>'
        '</div>'
        f'<div class="bc-metric">'
        f'<div class="bc-metric-value">{len(pending)}</div>'
        '<div class="bc-metric-label">Human reviews</div>'
        '</div>'
        f'<div class="bc-metric">'
        f'<div class="bc-metric-value">{len(preparation_work)}</div>'
        '<div class="bc-metric-label">Preparation work</div>'
        '</div>'
        f'<div class="bc-metric">'
        f'<div class="bc-metric-value">{len(views)}</div>'
        '<div class="bc-metric-label">Accounts</div>'
        '</div>'
        '</div>'
    )

    # --------------------------------------------------------------
    # Tasks
    # --------------------------------------------------------------

    html('<div class="bc-h">Tasks that need attention</div>')

    priority_work = overdue + due_today

    if not priority_work:
        st.caption(
            "No overdue or due-today tasks."
        )

    for service, task in priority_work:
        c1, c2, c3 = st.columns([5, 1, 1])

        account_name = names.get(
            task["account_id"],
            task["account_id"],
        )

        status = (
            "OVERDUE"
            if (
                task["due_date"] is not None
                and task["due_date"] < today_date
            )
            else "TODAY"
        )

        c1.markdown(
            f"**{task['title']}**  \n"
            f"{account_name} · "
            f"{status} · "
            f"{task['priority'].title()}"
        )

        if c2.button(
            "Open",
            key=f"today-open-task-{task['task_id']}",
        ):
            go(
                "Workspace",
                task["account_id"],
                "Overview",
            )
            st.rerun()

        if c3.button(
            "Done",
            key=f"today-done-task-{task['task_id']}",
        ):
            if attempt(
                lambda s=service, tid=task["task_id"]:
                    s.crm_complete_task(
                        tid,
                    ),
                success="Task completed.",
            ):
                st.rerun()

    # --------------------------------------------------------------
    # Human reviews
    # --------------------------------------------------------------

    html('<div class="bc-h">Decisions waiting on you</div>')

    if not pending:
        st.caption(
            "No pending BAEC reviews."
        )

    section_for_stage = {
        "REMEMBER": "Remember",
        "RECOGNIZE": "Recognize",
        "PREPARE": "Prepare",
    }

    for row in pending[:10]:
        c1, c2 = st.columns([5, 1])

        c1.markdown(
            f"**{names.get(row['account_id'], row['account'])}**  \n"
            f"{row['type']} · {row['status']}"
        )

        if c2.button(
            "Review",
            key=(
                f"today-review-"
                f"{row['account_id']}-"
                f"{row['ref']}"
                + (
                    f"-{row['artifact_id']}"
                    if row.get("artifact_id")
                    else ""
                )
            ),
        ):
            go(
                "Workspace",
                row["account_id"],
                section_for_stage[row["stage"]],
                review_target(row),
            )
            st.rerun()

    # --------------------------------------------------------------
    # Accounts
    # --------------------------------------------------------------

    html('<div class="bc-h">Accounts to move</div>')

    local_views = [
        v for v in views
        if not v.synthetic
    ]

    local_views.sort(
        key=lambda v:
            v.last_activity.timestamp()
            if v.last_activity
            else 0,
        reverse=True,
    )

    if not local_views:
        st.caption(
            "No live accounts yet."
        )

    for view in local_views[:10]:
        c1, c2 = st.columns([5, 1])

        c1.markdown(
            f"**{view.display_name}**  \n"
            f"{STATE_LABELS[view.account.state]} · "
            f"{view.next_action}"
        )

        if c2.button(
            "Open",
            key=f"today-open-account-{view.account.account_id}",
        ):
            go(
                "Workspace",
                view.account.account_id,
                "Overview",
            )
            st.rerun()
