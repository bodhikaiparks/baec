"""pytest plugin: run the existing Engine 1 repository test suites, unchanged, on PostgreSQL.

    python -m pytest -p baec_console.tests_postgres.engine1_on_postgres tests/test_transition_persistence.py ...

Loaded by tests_postgres/test_engine1_suites_on_postgresql.py, which runs the suites in a subprocess and checks the
outcome. Test-only: it is never imported by application code.

Every call of baec_app.data.database.open_database made through a tests.* module (the `connection` fixture of
tests/persistence_builders.py, and tests that open their own) gets a PostgreSQL Engine1Connection instead of a SQLite
database: its own connection, one tenant transaction for a fresh synthetic organization in workspace 'local', acting
as the application role (baec_app, so row-level security and privileges apply), rolled back on close(). Each
open_database() is a new, empty database, as ':memory:' is in SQLite.

tamper() (tests/persistence_builders.py) simulates corruption with every guard off. Its SQLite statements are
answered with the closest PostgreSQL equivalent, and nothing else is emulated:

    SELECT name, sql FROM sqlite_master WHERE type = 'trigger'   no rows (nothing to drop and recreate)
    PRAGMA foreign_keys = OFF        SET ROLE baec_e1_tamper (all DML on baec_engine1, still under row-level security,
                                     so only this test's organization is touched) and session_replication_role =
                                     replica, which stops every trigger and foreign key; the corrupting statement
                                     itself (UPDATE or DELETE) then runs as given
    PRAGMA ignore_check_constraints  no equivalent: PostgreSQL cannot switch CHECK constraints off, so corruption that
                                     violates one cannot be stored at all (those tests are skipped: NOT_APPLICABLE)
    PRAGMA foreign_keys = ON         back to baec_app with triggers and foreign keys on

A later lifecycle phase setting baec_records.staleness_status is simulated in some tests by a raw UPDATE of that
column. SQLite allows it to any writer; on PostgreSQL the application role has no UPDATE on baec_records at all (a
deliberate implementation choice: no code writes it before a staleness phase exists). Exactly that statement shape
runs as baec_e1_tamper with every trigger still on, so baec_records_protected_no_update still guards the other
columns. Nothing else is given more privilege than the application role has.

NOT_APPLICABLE lists every test that exercises a SQLite mechanism rather than repository behavior, with the reason and
where the invariant is tested on PostgreSQL instead; they are skipped, so the run must otherwise pass completely.
The test role baec_e1_tamper is created by test_engine1_suites_on_postgresql.py, not here.
"""

from __future__ import annotations

import os
import re
import sys
import uuid

import pytest

from baec_app.data import database
from baec_console.postgres.database import act_for
from baec_console.postgres.engine1 import Engine1Connection, _Rows, _translate

TAMPER_ROLE = "baec_e1_tamper"
_ORIGINAL_OPEN = database.open_database


class SuiteConnection(Engine1Connection):
    """An Engine1Connection that owns a PostgreSQL connection and one tenant transaction; close() discards both."""

    def __init__(self) -> None:
        import psycopg

        raw = psycopg.connect(os.environ["BAEC_TEST_POSTGRES_ADMIN_URL"], autocommit=True)
        raw.execute("BEGIN")
        raw.execute("SET LOCAL ROLE baec_app")
        raw.execute("SET LOCAL lock_timeout = '10s'")
        organization_id = f"org-e1suite-{uuid.uuid4().hex[:12]}"
        act_for(raw, organization_id, "local")
        raw.execute("INSERT INTO baec.organizations (organization_id, name, created_at) VALUES (%s, %s, now())",
                    (organization_id, f"Engine 1 suite {organization_id} (synthetic)"))
        self.raw = raw
        self.closed = False
        self.tampering = False
        super().__init__(raw)

    def execute(self, sql: str, args: tuple | list = ()):
        if self.closed:
            import sqlite3
            raise sqlite3.ProgrammingError("Cannot operate on a closed database.")
        normalized = " ".join(sql.split()).upper()
        if normalized == "SELECT NAME, SQL FROM SQLITE_MASTER WHERE TYPE = 'TRIGGER'":
            return _Rows([])
        if normalized == "PRAGMA FOREIGN_KEYS = OFF":
            self.raw.execute(f"SET LOCAL ROLE {TAMPER_ROLE}")
            self.raw.execute("SET LOCAL session_replication_role = replica")
            self.tampering = True
            return _Rows([])
        if normalized == "PRAGMA FOREIGN_KEYS = ON":
            self.raw.execute("SET LOCAL session_replication_role = origin")
            self.raw.execute("SET LOCAL ROLE baec_app")
            self.tampering = False
            return _Rows([])
        if re.fullmatch(r"PRAGMA IGNORE_CHECK_CONSTRAINTS = (ON|OFF)", normalized):
            return _Rows([])
        if self.tampering:
            return self.raw.execute(_translate(sql), tuple(args))
        if re.fullmatch(r"UPDATE BAEC_RECORDS SET STALENESS_STATUS = (\?|'[A-Z_]+')( WHERE BAEC_ID = \?)?", normalized):
            self.raw.execute(f"SET LOCAL ROLE {TAMPER_ROLE}")
            try:
                return super().execute(sql, args)
            finally:
                self.raw.execute("SET LOCAL ROLE baec_app")
        return super().execute(sql, args)

    def close(self) -> None:
        if not self.closed:
            self.closed = True
            self.raw.execute("ROLLBACK")
            self.raw.close()


def open_on_postgres(path: str = ":memory:") -> SuiteConnection:
    return SuiteConnection()


@pytest.fixture(autouse=True)
def engine1_on_postgres(monkeypatch):
    for name, module in list(sys.modules.items()):
        if (name == "tests" or name.startswith("tests.")) and getattr(module, "open_database", None) is _ORIGINAL_OPEN:
            monkeypatch.setattr(module, "open_database", open_on_postgres)
    opened: list[SuiteConnection] = []
    real = SuiteConnection.__init__

    def tracking(self) -> None:
        real(self)
        opened.append(self)
    monkeypatch.setattr(SuiteConnection, "__init__", tracking)
    yield
    for connection in opened:  # never leak a PostgreSQL connection, whatever the test did
        connection.close()


_CHECK = ("stores a value a CHECK constraint forbids (PRAGMA ignore_check_constraints): PostgreSQL cannot switch "
          "CHECKs off, so this corruption cannot be stored at all; refused at write time on both backends "
          "(test_engine1_parity.py::test_check_constraints_refuse_the_same_values_on_both_backends)")
_RAW = ("asserts sqlite3.IntegrityError from raw SQL; the same statements are refused identically on both backends "
        "by test_engine1_parity.py (trigger messages compared exactly, constraint kinds compared)")
_INTROSPECTION = ("asserts SQLite catalog introspection (typeof, PRAGMA table_info / index_list / foreign_key_list); "
                  "the PostgreSQL columns, keys, and foreign keys are compared with SQLite's in "
                  "test_engine1_storage.py::test_the_postgresql_tables_have_sqlites_columns_keys_and_foreign_keys, "
                  "and stored text round trips byte for byte in test_engine1_storage.py lifecycle")
NOT_APPLICABLE = {
    "tests/test_repository_integrity.py::test_rc33_append_only_tables_refuse_update_and_delete": _RAW,
    "tests/test_repository_integrity.py::test_database_constraints_refuse_structurally_invalid_rows": _RAW,
    "tests/test_transition_persistence.py::test_database_binds_a_transitions_baec_to_its_account_and_its_judgment_"
    "to_that_baec": _RAW,
    "tests/test_transition_persistence.py::test_composite_parent_keys_exist_on_baec_records_and_dormancy_judgments":
        _INTROSPECTION,
    "tests/test_repository_roundtrip.py::test_rc18_decimal_thresholds_are_stored_as_text_without_float_conversion":
        _INTROSPECTION,
    "tests/test_repository_integrity.py::test_corrupted_account_and_interaction_rows_fail_closed": _CHECK,
    "tests/test_transition_persistence.py::test_corrupted_transition_history_fails_closed[unknown destination "
    "state]": _CHECK,
    "tests/test_repository_integrity.py::test_rc18_corrupted_stringency_fails_closed[unknown comparator]": _CHECK,
    **{f"tests/test_repository_integrity.py::test_rc22_corrupted_dormancy_judgment_fails_closed[{case}]": _CHECK
       for case in ("unknown plausibility answer", "unknown addressability answer")},
    **{f"tests/test_repository_integrity.py::test_corrupted_confirmed_record_fails_closed[{case}]": _CHECK
       for case in ("unknown classification text", "unknown articulation origin", "unknown elicitation mode",
                    "unknown staleness status", "unknown criterion", "unknown finding", "unknown provenance",
                    "external provenance on buyer evidence", "AI inference as evidence",
                    "confirmation carrying a target state", "normalized model without normalized text",
                    "normalized timestamp without normalized text", "normalized text without a timestamp")},
}


def pytest_collection_modifyitems(config, items):
    for item in items:
        reason = NOT_APPLICABLE.get(item.nodeid) or NOT_APPLICABLE.get(item.nodeid.split("[")[0])
        if reason:
            item.add_marker(pytest.mark.skip(reason=f"SQLite mechanism, not repository behavior: {reason}"))
