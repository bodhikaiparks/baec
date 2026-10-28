from datetime import date

from baec_console.console_service import Console

from .conftest import ACTOR, StepClock, make_console


def test_crm_contact_and_task_lifecycle(tmp_path):
    console = make_console(tmp_path / "data", StepClock())
    service = console.local

    account_id = service.create_account(
        "Northstar Surgical",
        industry="ASC",
    )

    contact_id = service.crm_add_contact(
        account_id,
        name="Jordan Lee",
        title="Materials Manager",
        email="jordan@example.test",
        phone="555-0100",
        notes="Primary contact",
    )

    contacts = service.crm_contacts(account_id)

    assert len(contacts) == 1
    assert contacts[0]["contact_id"] == contact_id
    assert contacts[0]["name"] == "Jordan Lee"

    task_id = service.crm_add_task(
        account_id,
        contact_id=contact_id,
        title="Follow up after renewal review",
        due_date=date(2026, 11, 1),
        priority="HIGH",
        notes="Check whether condition is still relevant",
    )

    tasks = service.crm_tasks(
        account_id=account_id,
        status="OPEN",
    )

    assert len(tasks) == 1
    assert tasks[0]["task_id"] == task_id
    assert tasks[0]["priority"] == "HIGH"

    service.crm_complete_task(
        task_id,
    )

    assert service.crm_tasks(
        account_id=account_id,
        status="OPEN",
    ) == []

    done = service.crm_tasks(
        account_id=account_id,
        status="DONE",
    )

    assert len(done) == 1
    assert done[0]["completed_by"] == ACTOR


def test_crm_database_is_separate_from_baec_stores(tmp_path):
    console = make_console(tmp_path / "data", StepClock())
    service = console.local

    assert service.crm_path.endswith("crm.sqlite3")
    assert service.crm_path != service.console_path
    assert service.crm_path != service.engine1_path
    assert service.crm_path != service.engine2_path


def test_crm_activity_can_exist_without_engine1_condition(tmp_path):
    from datetime import datetime, timezone

    console = make_console(tmp_path / "data", StepClock())
    service = console.local

    account_id = service.create_account(
        "Beacon Endoscopy",
        industry="ASC",
    )

    activity_id = service.crm_add_activity(
        account_id,
        contact_id=None,
        interaction_type="Call",
        occurred_at=datetime(
            2026, 10, 26, 14, 0,
            tzinfo=timezone.utc,
        ),
        raw_notes=(
            "Routine check-in. Buyer asked for the current catalog "
            "and said they would review it internally."
        ),
        summary="Routine catalog follow-up.",
        buyer_role="Materials Manager",
        buyer_statement="",
        seller_notes="",
        ai_assisted=False,
    )

    activities = service.crm_activities(account_id)

    assert len(activities) == 1
    assert activities[0]["activity_id"] == activity_id
    assert activities[0]["engine1_interaction_id"] is None
    assert activities[0]["ai_assisted"] is False

    # A normal CRM activity does not manufacture an Engine 1 interaction.
    assert service.account_view(account_id).interactions == []


def test_crm_activity_can_link_to_engine1_interaction(tmp_path):
    from datetime import datetime, timezone

    console = make_console(tmp_path / "data", StepClock())
    service = console.local

    account_id = service.create_account(
        "Beacon Endoscopy",
    )

    occurred_at = datetime(
        2026, 10, 26, 15, 0,
        tzinfo=timezone.utc,
    )

    statement = (
        "If pricing increases by more than 8% at renewal, "
        "we would compare alternatives."
    )

    activity_id = service.crm_add_activity(
        account_id,
        contact_id=None,
        interaction_type="Meeting",
        occurred_at=occurred_at,
        raw_notes=statement,
        summary="Buyer described a future pricing condition.",
        buyer_role="Administrator",
        buyer_statement=statement,
        seller_notes="No active evaluation underway.",
        ai_assisted=True,
    )

    interaction_id = service.record_interaction(
        account_id,
        buyer_role="Administrator",
        occurred_at=occurred_at,
        buyer_statement=statement,
        seller_notes="No active evaluation underway.",
    )

    service.crm_attach_engine1_interaction(
        activity_id,
        interaction_id,
    )

    activity = service.crm_activities(account_id)[0]

    assert activity["engine1_interaction_id"] == interaction_id
    assert activity["ai_assisted"] is True
    assert len(service.account_view(account_id).interactions) == 1
