"""Fixtures and helpers shared by the Phase 3 persistence tests.

Like tests/builders.py, this file only sets things up. Expected outcomes are
stated in the tests.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from baec_app.data import database
from baec_app.data.database import DATA_TABLES, open_database
from baec_app.data.records import SourceInteraction
from baec_app.data.repository import Repository
from baec_app.domain.enums import AccountState, NoPlausiblePathGround, ReviewAnswer
from baec_app.domain.models import Account
from tests.builders import (
    AO,
    CD,
    HARBOR_QUOTE,
    NOW,
    NP,
    confirmed_record,
    evaluation_evidence,
    judgment,
    non_evaluation_evidence,
    state_auth,
)

LATER = datetime(2026, 2, 1, 9, 30, tzinfo=timezone.utc)

# Interaction texts contain the excerpt texts used by tests/builders.py.
INTERACTION_TEXTS = {
    "INT-1": "Buyer: " + HARBOR_QUOTE + "\nRep note: buyer tied it to renewal.\nRep note: pricing at renewal.",
    "INT-2": "Buyer: We have opened a formal supplier review.",
    "INT-3": "Buyer: We closed the review and are staying put.",
}


@pytest.fixture(autouse=True)
def no_leaked_connections(monkeypatch):
    """Every connection opened during a test must be closed by the end of it."""
    opened: list[sqlite3.Connection] = []
    real_connect = database._raw_connect

    def tracking_connect(*args, **kwargs):
        connection = real_connect(*args, **kwargs)
        opened.append(connection)
        return connection

    monkeypatch.setattr(database, "_raw_connect", tracking_connect)
    yield
    leaked = 0
    for connection in opened:
        try:
            connection.execute("SELECT 1")
        except sqlite3.ProgrammingError:
            continue  # closed, as required
        leaked += 1
        connection.close()
    assert leaked == 0, f"{leaked} database connection(s) were left open"


@pytest.fixture
def connection():
    conn = open_database()
    yield conn
    conn.close()


@pytest.fixture
def repo(connection):
    """A repository with two unclassified accounts and their interactions."""
    repository = Repository(connection)
    repository.add_account(Account("ACC-1", "Harbor Surgical Center"))
    repository.add_account(Account("ACC-2", "Other Synthetic Account"))
    for interaction_id, text in INTERACTION_TEXTS.items():
        repository.add_interaction(SourceInteraction(interaction_id, "ACC-1", NOW, text))
    repository.add_interaction(SourceInteraction("INT-9", "ACC-2", NOW, "Buyer: " + HARBOR_QUOTE))
    return repository


def counts(conn) -> dict[str, int]:
    return database.table_counts(conn)


def dump(conn) -> dict[str, list[tuple]]:
    """Logical contents of every data table, in primary-key order."""
    return {
        table: conn.execute(f"SELECT * FROM {table} ORDER BY 1, 2").fetchall()
        for table in DATA_TABLES + ("schema_meta",)
    }


def tamper(conn, sql: str, parameters: tuple = ()) -> None:
    """Simulate corruption: edit rows directly with every guard switched off."""
    triggers = conn.execute("SELECT name, sql FROM sqlite_master WHERE type = 'trigger'").fetchall()
    for name, _ in triggers:
        conn.execute(f"DROP TRIGGER {name}")
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("PRAGMA ignore_check_constraints = ON")
    try:
        conn.execute(sql, parameters)
    finally:
        conn.execute("PRAGMA ignore_check_constraints = OFF")
        conn.execute("PRAGMA foreign_keys = ON")
        for _, trigger_sql in triggers:
            conn.execute(trigger_sql)


def save_confirmed(repository: Repository, baec_id: str = "B-1"):
    record = confirmed_record(baec_id=baec_id)
    repository.save_confirmed_baec(record)
    return record


def save_judgment(repository: Repository, baec_id: str = "B-1", **answers) -> int:
    return repository.record_dormancy_judgment(judgment(baec_id=baec_id, **answers))


def ensure_dormancy_prerequisites(repository: Repository) -> int:
    """Store BAEC B-1 and one YES/YES judgment if not already stored; return the judgment id."""
    try:
        repository.get_baec_record("B-1")
    except database.RepositoryNotFoundError:
        save_confirmed(repository)
    stored = repository.list_dormancy_judgments("B-1")
    if stored:
        return stored[0].judgment_id
    return save_judgment(repository)


def move(repository: Repository, to_state: AccountState, **overrides):
    """Request a persisted transition for ACC-1 with valid prerequisites, then apply overrides."""
    current = repository.get_account("ACC-1").state
    leaving_active = non_evaluation_evidence() if current is AO else None
    if to_state is CD:
        if "judgment_id" not in overrides:
            overrides["judgment_id"] = ensure_dormancy_prerequisites(repository)
        kwargs = dict(
            baec_id="B-1",
            authorization=state_auth(CD),
            non_evaluation_evidence=leaving_active,
            recorded_at=LATER,
        )
        kwargs.update(overrides)
        return repository.persist_transition_to_conditionally_dormant("ACC-1", **kwargs)
    if to_state is AO:
        kwargs = dict(
            evaluation_evidence=evaluation_evidence(), authorization=state_auth(AO), recorded_at=LATER
        )
        kwargs.update(overrides)
        return repository.persist_transition_to_active_opportunity("ACC-1", **kwargs)
    kwargs = dict(
        ground=NoPlausiblePathGround.NO_PLAUSIBLE_BAEC,
        reason="CEE produced no foreseeable condition.",
        authorization=state_auth(NP),
        non_evaluation_evidence=leaving_active,
        recorded_at=LATER,
    )
    kwargs.update(overrides)
    return repository.persist_transition_to_no_plausible_path("ACC-1", **kwargs)


def put_in_state(repository: Repository, state: AccountState | None) -> None:
    """Bring ACC-1 from unclassified to the given state through a real persisted transition."""
    if state is not None:
        assert move(repository, state).allowed
