"""Operational CRM storage for BAEC.

Contacts, tasks, and CRM interaction links live here so the validated
BAEC Engine 1/2/3 records remain separate and authoritative.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Iterator

SCHEMA_VERSION = 2


class CRMStoreError(Exception):
    pass


def _ts(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise CRMStoreError("timestamps must be timezone-aware")
    return value.isoformat()


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _day(value: date | None) -> str | None:
    return value.isoformat() if value else None


def _date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def open_crm_store(path: str | Path) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    db = sqlite3.connect(str(path), isolation_level=None)
    db.execute("PRAGMA foreign_keys = ON")

    empty = db.execute(
        "SELECT COUNT(*) FROM sqlite_master"
    ).fetchone()[0] == 0

    if empty:
        db.executescript(
            """
            BEGIN;

            CREATE TABLE crm_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            INSERT INTO crm_meta VALUES
                ('engine', 'BAEC_CRM'),
                ('schema_version', '2');

            CREATE TABLE contacts (
                contact_id TEXT PRIMARY KEY,
                account_id TEXT NOT NULL,
                name TEXT NOT NULL,
                title TEXT,
                email TEXT,
                phone TEXT,
                notes TEXT,
                active INTEGER NOT NULL DEFAULT 1
                    CHECK (active IN (0, 1)),
                created_at TEXT NOT NULL,
                created_by TEXT NOT NULL
            );

            CREATE INDEX idx_contacts_account
                ON contacts(account_id);

            CREATE TABLE tasks (
                task_id TEXT PRIMARY KEY,
                account_id TEXT NOT NULL,
                contact_id TEXT,
                title TEXT NOT NULL,
                due_date TEXT,
                priority TEXT NOT NULL DEFAULT 'NORMAL'
                    CHECK (priority IN ('LOW', 'NORMAL', 'HIGH')),
                status TEXT NOT NULL DEFAULT 'OPEN'
                    CHECK (status IN ('OPEN', 'DONE')),
                notes TEXT,
                created_at TEXT NOT NULL,
                created_by TEXT NOT NULL,
                completed_at TEXT,
                completed_by TEXT
            );

            CREATE INDEX idx_tasks_account
                ON tasks(account_id);

            CREATE INDEX idx_tasks_due
                ON tasks(status, due_date);

            CREATE TABLE interaction_links (
                interaction_id TEXT PRIMARY KEY,
                account_id TEXT NOT NULL,
                contact_id TEXT,
                interaction_type TEXT NOT NULL,
                linked_at TEXT NOT NULL
            );

            CREATE INDEX idx_interaction_links_account
                ON interaction_links(account_id);

            CREATE TABLE activities (
                activity_id TEXT PRIMARY KEY,
                account_id TEXT NOT NULL,
                contact_id TEXT,
                interaction_type TEXT NOT NULL,
                occurred_at TEXT NOT NULL,
                raw_notes TEXT NOT NULL,
                summary TEXT,
                buyer_role TEXT,
                buyer_statement TEXT,
                seller_notes TEXT,
                engine1_interaction_id TEXT,
                ai_assisted INTEGER NOT NULL DEFAULT 0
                    CHECK (ai_assisted IN (0, 1)),
                created_at TEXT NOT NULL,
                created_by TEXT NOT NULL
            );

            CREATE INDEX idx_activities_account
                ON activities(account_id, occurred_at);

            CREATE INDEX idx_activities_contact
                ON activities(contact_id, occurred_at);

            COMMIT;
            """
        )

    meta = dict(
        db.execute(
            "SELECT key, value FROM crm_meta"
        ).fetchall()
    )

    if meta == {
        "engine": "BAEC_CRM",
        "schema_version": "1",
    }:
        db.executescript(
            """
            BEGIN;

            CREATE TABLE activities (
                activity_id TEXT PRIMARY KEY,
                account_id TEXT NOT NULL,
                contact_id TEXT,
                interaction_type TEXT NOT NULL,
                occurred_at TEXT NOT NULL,
                raw_notes TEXT NOT NULL,
                summary TEXT,
                buyer_role TEXT,
                buyer_statement TEXT,
                seller_notes TEXT,
                engine1_interaction_id TEXT,
                ai_assisted INTEGER NOT NULL DEFAULT 0
                    CHECK (ai_assisted IN (0, 1)),
                created_at TEXT NOT NULL,
                created_by TEXT NOT NULL
            );

            CREATE INDEX idx_activities_account
                ON activities(account_id, occurred_at);

            CREATE INDEX idx_activities_contact
                ON activities(contact_id, occurred_at);

            UPDATE crm_meta
            SET value = '2'
            WHERE key = 'schema_version';

            COMMIT;
            """
        )

        meta = dict(
            db.execute(
                "SELECT key, value FROM crm_meta"
            ).fetchall()
        )

    expected = {
        "engine": "BAEC_CRM",
        "schema_version": str(SCHEMA_VERSION),
    }

    if meta != expected:
        db.close()
        raise CRMStoreError(f"unsupported CRM store: {meta}")

    return db


@contextmanager
def transaction(
    db: sqlite3.Connection,
) -> Iterator[sqlite3.Connection]:
    db.execute("BEGIN IMMEDIATE")
    try:
        yield db
        db.execute("COMMIT")
    except BaseException:
        if db.in_transaction:
            db.execute("ROLLBACK")
        raise


class CRMStore:
    def __init__(self, db: sqlite3.Connection) -> None:
        self.db = db

    def add_contact(
        self,
        contact_id: str,
        account_id: str,
        *,
        name: str,
        title: str,
        email: str,
        phone: str,
        notes: str,
        created_at: datetime,
        created_by: str,
    ) -> None:
        self.db.execute(
            """
            INSERT INTO contacts
            VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
            """,
            (
                contact_id,
                account_id,
                name.strip(),
                title.strip() or None,
                email.strip() or None,
                phone.strip() or None,
                notes.strip() or None,
                _ts(created_at),
                created_by,
            ),
        )

    def contacts(self, account_id: str) -> list[dict]:
        rows = self.db.execute(
            """
            SELECT
                contact_id, account_id, name, title, email,
                phone, notes, active, created_at, created_by
            FROM contacts
            WHERE account_id = ?
            ORDER BY active DESC, name COLLATE NOCASE
            """,
            (account_id,),
        ).fetchall()

        return [
            {
                "contact_id": r[0],
                "account_id": r[1],
                "name": r[2],
                "title": r[3],
                "email": r[4],
                "phone": r[5],
                "notes": r[6],
                "active": bool(r[7]),
                "created_at": _dt(r[8]),
                "created_by": r[9],
            }
            for r in rows
        ]

    def account_of(self, table: str, record_id: str) -> str | None:
        """The account that owns one contact, task, or activity, or None if there is no such record."""
        key = {"contacts": "contact_id", "tasks": "task_id", "activities": "activity_id"}[table]
        row = self.db.execute(
            f"SELECT account_id FROM {table} WHERE {key} = ?",
            (record_id,),
        ).fetchone()
        return None if row is None else row[0]

    def add_task(
        self,
        task_id: str,
        account_id: str,
        *,
        contact_id: str | None,
        title: str,
        due_date: date | None,
        priority: str,
        notes: str,
        created_at: datetime,
        created_by: str,
    ) -> None:
        self.db.execute(
            """
            INSERT INTO tasks
            VALUES (?, ?, ?, ?, ?, ?, 'OPEN', ?, ?, ?, NULL, NULL)
            """,
            (
                task_id,
                account_id,
                contact_id,
                title.strip(),
                _day(due_date),
                priority,
                notes.strip() or None,
                _ts(created_at),
                created_by,
            ),
        )

    def tasks(
        self,
        *,
        account_id: str | None = None,
        status: str | None = None,
    ) -> list[dict]:
        clauses = []
        args = []

        if account_id is not None:
            clauses.append("account_id = ?")
            args.append(account_id)

        if status is not None:
            clauses.append("status = ?")
            args.append(status)

        sql = """
            SELECT
                task_id, account_id, contact_id, title,
                due_date, priority, status, notes,
                created_at, created_by, completed_at, completed_by
            FROM tasks
        """

        if clauses:
            sql += " WHERE " + " AND ".join(clauses)

        sql += """
            ORDER BY
                CASE status WHEN 'OPEN' THEN 0 ELSE 1 END,
                CASE priority
                    WHEN 'HIGH' THEN 0
                    WHEN 'NORMAL' THEN 1
                    ELSE 2
                END,
                CASE WHEN due_date IS NULL THEN 1 ELSE 0 END,
                due_date,
                created_at DESC
        """

        rows = self.db.execute(sql, tuple(args)).fetchall()

        return [
            {
                "task_id": r[0],
                "account_id": r[1],
                "contact_id": r[2],
                "title": r[3],
                "due_date": _date(r[4]),
                "priority": r[5],
                "status": r[6],
                "notes": r[7],
                "created_at": _dt(r[8]),
                "created_by": r[9],
                "completed_at": _dt(r[10]),
                "completed_by": r[11],
            }
            for r in rows
        ]

    def complete_task(
        self,
        task_id: str,
        *,
        completed_at: datetime,
        completed_by: str,
    ) -> None:
        row = self.db.execute(
            "SELECT status FROM tasks WHERE task_id = ?",
            (task_id,),
        ).fetchone()

        if row is None:
            raise CRMStoreError("Task does not exist.")

        if row[0] == "DONE":
            raise CRMStoreError("Task is already complete.")

        self.db.execute(
            """
            UPDATE tasks
            SET status = 'DONE',
                completed_at = ?,
                completed_by = ?
            WHERE task_id = ?
            """,
            (_ts(completed_at), completed_by, task_id),
        )

    def link_interaction(
        self,
        interaction_id: str,
        account_id: str,
        *,
        contact_id: str | None,
        interaction_type: str,
        linked_at: datetime,
    ) -> None:
        self.db.execute(
            """
            INSERT INTO interaction_links
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                interaction_id,
                account_id,
                contact_id,
                interaction_type,
                _ts(linked_at),
            ),
        )

    def interaction_link(
        self,
        interaction_id: str,
    ) -> dict | None:
        row = self.db.execute(
            """
            SELECT interaction_id, account_id, contact_id,
                   interaction_type, linked_at
            FROM interaction_links
            WHERE interaction_id = ?
            """,
            (interaction_id,),
        ).fetchone()

        if row is None:
            return None

        return {
            "interaction_id": row[0],
            "account_id": row[1],
            "contact_id": row[2],
            "interaction_type": row[3],
            "linked_at": _dt(row[4]),
        }


    # -----------------------------------------------------------------
    # Sales activities
    # -----------------------------------------------------------------

    def add_activity(
        self,
        activity_id: str,
        account_id: str,
        *,
        contact_id: str | None,
        interaction_type: str,
        occurred_at: datetime,
        raw_notes: str,
        summary: str,
        buyer_role: str,
        buyer_statement: str,
        seller_notes: str,
        ai_assisted: bool,
        created_at: datetime,
        created_by: str,
    ) -> None:
        self.db.execute(
            """
            INSERT INTO activities
            (
                activity_id,
                account_id,
                contact_id,
                interaction_type,
                occurred_at,
                raw_notes,
                summary,
                buyer_role,
                buyer_statement,
                seller_notes,
                engine1_interaction_id,
                ai_assisted,
                created_at,
                created_by
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?)
            """,
            (
                activity_id,
                account_id,
                contact_id,
                interaction_type,
                _ts(occurred_at),
                raw_notes.strip(),
                summary.strip() or None,
                buyer_role.strip() or None,
                buyer_statement.strip() or None,
                seller_notes.strip() or None,
                int(ai_assisted),
                _ts(created_at),
                created_by,
            ),
        )

    def activities(
        self,
        account_id: str,
    ) -> list[dict]:
        rows = self.db.execute(
            """
            SELECT
                activity_id,
                account_id,
                contact_id,
                interaction_type,
                occurred_at,
                raw_notes,
                summary,
                buyer_role,
                buyer_statement,
                seller_notes,
                engine1_interaction_id,
                ai_assisted,
                created_at,
                created_by
            FROM activities
            WHERE account_id = ?
            ORDER BY occurred_at DESC, created_at DESC
            """,
            (account_id,),
        ).fetchall()

        return [
            {
                "activity_id": r[0],
                "account_id": r[1],
                "contact_id": r[2],
                "interaction_type": r[3],
                "occurred_at": _dt(r[4]),
                "raw_notes": r[5],
                "summary": r[6],
                "buyer_role": r[7],
                "buyer_statement": r[8],
                "seller_notes": r[9],
                "engine1_interaction_id": r[10],
                "ai_assisted": bool(r[11]),
                "created_at": _dt(r[12]),
                "created_by": r[13],
            }
            for r in rows
        ]

    def attach_engine1_interaction(
        self,
        activity_id: str,
        interaction_id: str,
    ) -> None:
        row = self.db.execute(
            """
            SELECT engine1_interaction_id
            FROM activities
            WHERE activity_id = ?
            """,
            (activity_id,),
        ).fetchone()

        if row is None:
            raise CRMStoreError("Activity does not exist.")

        if row[0] is not None:
            raise CRMStoreError(
                "Activity is already linked to Engine 1."
            )

        self.db.execute(
            """
            UPDATE activities
            SET engine1_interaction_id = ?
            WHERE activity_id = ?
            """,
            (interaction_id, activity_id),
        )

    def labelled_records(self) -> list[tuple]:
        """(table, record_id, action, account_id, actor label, at) for every CRM row with a human actor label."""
        q = self.db.execute
        out = [("contacts", r[0], "CREATED", r[1], r[2], _dt(r[3]))
               for r in q("SELECT contact_id, account_id, created_by, created_at FROM contacts ORDER BY rowid")]
        out += [("tasks", r[0], "CREATED", r[1], r[2], _dt(r[3]))
                for r in q("SELECT task_id, account_id, created_by, created_at FROM tasks ORDER BY rowid")]
        out += [("tasks", r[0], "COMPLETED", r[1], r[2], _dt(r[3]))
                for r in q("SELECT task_id, account_id, completed_by, completed_at FROM tasks "
                           "WHERE completed_by IS NOT NULL ORDER BY rowid")]
        out += [("activities", r[0], "CREATED", r[1], r[2], _dt(r[3]))
                for r in q("SELECT activity_id, account_id, created_by, created_at FROM activities ORDER BY rowid")]
        return out
