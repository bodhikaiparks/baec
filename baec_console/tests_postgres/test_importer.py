"""V0.4 Phase 3: the explicit SQLite -> PostgreSQL import preserves ids, text, timestamps, ownership, and
attribution; it rehearses without writing; it never overwrites; and it leaves the SQLite source untouched.

The source is produced by the real development console: two organizations sharing one data root with the full
Remember -> Prepare workflow and CRM records, one organization's synthetic demo, and the local-development
workspace with its synthetic demo. All data is synthetic.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from baec_console import demo_seed
from baec_console.console_service import Console
from baec_console.postgres.importer import ImportRefused, main, run_import
from baec_console.tests.conftest import StepClock
from baec_console.tests.test_tenancy import build_world, tenant

from .conftest import AT, fresh, psycopg


def _hashes(root: Path) -> dict[str, str]:
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file()}


@pytest.fixture(scope="module")
def source(tmp_path_factory):
    root = tmp_path_factory.mktemp("import") / "data"
    clock = StepClock()
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("BAEC_DEPLOYMENT_MODE", "development")
        orgs = {"A": fresh("org-a"), "B": fresh("org-b")}
        a = tenant(root, clock, orgs["A"], fresh("user-a"), "Avery (Org A, synthetic)")
        b = tenant(root, clock, orgs["B"], fresh("user-b"), "Blake (Org B, synthetic)")
        worlds = {"A": build_world(a), "B": build_world(b)}
        demo_seed.load(a, "completed")  # Org A's own synthetic workspace (synthetic-organizations/<digest>/)
        local = Console.local_development(root, clock)
        demo_seed.load(local, "beginning")  # the local-development organization's synthetic workspace
        local.local.create_account("Local account (synthetic)")
    return {"root": root, "orgs": orgs, "worlds": worlds, "hashes": _hashes(root)}


@pytest.fixture(scope="module")
def imported(source, migrated):
    """A dry run, then the real import, of the module's source."""
    dry = run_import(source["root"], migrated["app"], dry_run=True)
    real = run_import(source["root"], migrated["app"], dry_run=False, migration_url=migrated["migrator"])
    return {"dry": dry, "real": real}


def _count(app_url: str, organization_id: str, table: str) -> int:
    with psycopg.connect(app_url) as db:
        db.execute("SELECT set_config('baec.organization_id', %s, true)", (organization_id,))
        return db.execute(f"SELECT count(*) FROM baec.{table}").fetchone()[0]


def test_a_dry_run_performs_and_verifies_the_import_then_writes_nothing(source, migrated):
    report = run_import(source["root"], migrated["app"], dry_run=True)
    assert report.dry_run and not report.conflicts
    assert report.verification["matches"] and report.verification["checked"] > 50
    assert report.inserted == report.source_counts  # every row was inserted inside the rehearsal ...
    for organization_id in source["orgs"].values():
        for table in ("organizations", "users", "events", "crm_contacts"):
            assert _count(migrated["app"], organization_id, table) == 0  # ... and rolled back


def test_the_import_copies_every_row_and_verifies_it(source, imported):
    report = imported["real"]
    assert not report.dry_run and not report.conflicts and report.verification["matches"]
    assert report.inserted == report.source_counts == imported["dry"].source_counts
    for organization_id, tables in report.source_counts.items():
        for table, n in tables.items():
            assert report.target_counts_after[organization_id][table] - \
                report.target_counts_before[organization_id][table] == n
    org_a = report.source_counts[source["orgs"]["A"]]
    for table in ("organizations", "users", "account_ownership", "interaction_meta", "condition_drafts",
                  "preparation_requests", "preparations", "preparation_provenance", "events", "attributions",
                  "crm_contacts", "crm_tasks", "crm_activities"):
        assert org_a.get(table, 0) > 0, table
    assert {"local", source["orgs"]["A"], source["orgs"]["B"]} <= set(report.source_counts)


def test_ids_buyer_text_json_and_attribution_are_preserved_exactly(source, imported, migrated):
    root, world = source["root"], source["worlds"]["A"]
    organization_id = source["orgs"]["A"]
    sqlite_console = sqlite3.connect(f"file:{root / 'local' / 'console.sqlite3'}?mode=ro", uri=True)
    sqlite_crm = sqlite3.connect(f"file:{root / 'local' / 'crm.sqlite3'}?mode=ro", uri=True)
    try:
        with psycopg.connect(migrated["app"]) as db:
            db.execute("SELECT set_config('baec.organization_id', %s, true)", (organization_id,))
            for sql_sqlite, sql_pg, args in (
                    ("SELECT buyer_statement FROM interaction_meta WHERE interaction_id = ?",
                     "SELECT buyer_statement FROM baec.interaction_meta WHERE interaction_id = %s",
                     (world.interaction_id,)),
                    ("SELECT form_json FROM condition_drafts WHERE draft_id = ?",
                     "SELECT form_json FROM baec.condition_drafts WHERE draft_id = %s", (world.draft_id,)),
                    ("SELECT request_json || snapshot_json FROM preparation_requests WHERE request_id = ?",
                     "SELECT request_json || snapshot_json FROM baec.preparation_requests WHERE request_id = %s",
                     (world.request_id,)),
                    ("SELECT artifacts_json FROM preparations WHERE preparation_id = ?",
                     "SELECT artifacts_json FROM baec.preparations WHERE preparation_id = %s", (world.preparation_id,)),
                    ("SELECT group_concat(event_id || ':' || event_type, ',') FROM (SELECT * FROM events "
                     "WHERE account_id = ? ORDER BY event_id)",
                     "SELECT string_agg(event_id || ':' || event_type, ',' ORDER BY event_id) FROM baec.events "
                     "WHERE account_id = %s", (world.account_id,)),
                    ("SELECT group_concat(record_type || record_id || action || user_id || organization_id || "
                     "display_name_snapshot, ',') FROM (SELECT * FROM attributions WHERE account_id = ? ORDER BY rowid)",
                     "SELECT string_agg(record_type || record_id || action || user_id || organization_id || "
                     "display_name_snapshot, ',' ORDER BY seq) FROM baec.attributions WHERE account_id = %s",
                     (world.account_id,)),
            ):
                expected = sqlite_console.execute(sql_sqlite, args).fetchone()[0]
                assert expected and db.execute(sql_pg, args).fetchone()[0] == expected, sql_pg
            raw = sqlite_crm.execute("SELECT raw_notes, created_at FROM activities WHERE activity_id = ?",
                                     (world.activity_id,)).fetchone()
            got = db.execute("SELECT raw_notes, created_at FROM baec.crm_activities WHERE activity_id = %s",
                             (world.activity_id,)).fetchone()
            assert got[0] == raw[0]
            assert got[1] == __import__("datetime").datetime.fromisoformat(raw[1])  # the same instant
    finally:
        sqlite_console.close()
        sqlite_crm.close()


def test_imported_organizations_stay_isolated(source, imported, migrated):
    a, b = source["worlds"]["A"], source["worlds"]["B"]
    with psycopg.connect(migrated["app"]) as db:
        db.execute("SELECT set_config('baec.organization_id', %s, true)", (source["orgs"]["A"],))
        assert db.execute("SELECT count(*) FROM baec.account_ownership WHERE account_id = %s",
                          (b.account_id,)).fetchone() == (0,)
        assert db.execute("SELECT count(*) FROM baec.account_ownership WHERE account_id = %s",
                          (a.account_id,)).fetchone() == (1,)
        workspaces = {r[0] for r in db.execute("SELECT DISTINCT workspace FROM baec.account_ownership").fetchall()}
        assert workspaces == {"local", "synthetic"}  # Org A's own accounts and its own synthetic demo


def test_running_the_import_again_inserts_nothing_and_overwrites_nothing(source, imported, migrated):
    again = run_import(source["root"], migrated["app"], dry_run=False, migration_url=migrated["migrator"])
    assert again.inserted == {} and not again.conflicts and again.verification["matches"]
    assert again.already_present == imported["real"].source_counts


def test_new_ids_continue_after_the_imported_ids(source, imported, storage):
    world = source["worlds"]["A"]
    with closing(sqlite3.connect(f"file:{source['root'] / 'local' / 'console.sqlite3'}?mode=ro", uri=True)) as db:
        largest = db.execute("SELECT max(event_id) FROM events").fetchone()[0]
    with storage.session(source["orgs"]["A"], "local") as s:
        assert s.console.add_event(world.account_id, "NOTE", "Human", "x", AT, "after import") > largest


def test_the_source_is_never_modified(source, imported):
    assert _hashes(source["root"]) == source["hashes"]


def test_the_import_is_recorded_for_operators_without_credentials(imported, migrated):
    with psycopg.connect(migrated["migrator"]) as db:
        details = [r[0] for r in db.execute("SELECT detail_json FROM baec.operator_log WHERE kind = 'SQLITE_IMPORT'")]
    assert details
    for url in migrated.values():
        assert all(urlsplit(url).password not in d for d in details)
    assert "source_migration_logs" in json.loads(details[-1])
    with psycopg.connect(migrated["app"]) as app:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            app.execute("SELECT * FROM baec.operator_log")


# --- refusals -------------------------------------------------------------------------------------------------------


@pytest.fixture
def small_source(tmp_path):
    """A fresh one-organization source, safe to alter."""
    root, org = tmp_path / "data", fresh("org-c")
    console = tenant(root, StepClock(), org, fresh("user-c"), "Casey (synthetic)")
    account_id = console.local.create_account("Small account (synthetic)")
    console.local.crm_add_contact(account_id, name="Pat Lee (synthetic)")
    return root, org


def _no_rows(app_url: str, organization_id: str) -> bool:
    return all(_count(app_url, organization_id, t) == 0 for t in ("organizations", "users", "account_ownership",
                                                                   "crm_contacts"))


def test_a_differing_existing_record_refuses_the_whole_import(small_source, migrated):
    root, org = small_source
    with psycopg.connect(migrated["app"]) as db:
        db.execute("SELECT set_config('baec.organization_id', %s, true)", (org,))
        db.execute("INSERT INTO baec.organizations (organization_id, name, created_at) VALUES (%s, 'Other name', "
                   "now())", (org,))
    with pytest.raises(ImportRefused, match="conflicting") as refused:
        run_import(root, migrated["app"], dry_run=False, migration_url=migrated["migrator"])
    assert refused.value.report.conflicts[0]["table"] == "organizations"
    assert _count(migrated["app"], org, "organizations") == 1 and _count(migrated["app"], org, "users") == 0


def test_an_id_held_by_another_organization_refuses_the_whole_import(small_source, source, imported, migrated):
    root, org = small_source
    taken = source["worlds"]["A"].console.principal.user_id  # already imported, in Org A
    with closing(sqlite3.connect(root / "identity.sqlite3")) as db, db:
        db.execute("INSERT INTO users VALUES (?, ?, 'Imposter (synthetic)', ?, 1, 'HUMAN')", (taken, org, AT.isoformat()))
    with pytest.raises(ImportRefused, match="conflicting") as refused:
        run_import(root, migrated["app"], dry_run=True)
    assert any(c["table"] == "users" and c.get("refused_by_database") for c in refused.value.report.conflicts)
    assert _no_rows(migrated["app"], org)


@pytest.mark.parametrize("damage,expected", [
    ("console: INSERT INTO account_meta VALUES ('ACC-ORPHAN', 'x', NULL, NULL, 0, '2026-09-05T09:00:00+00:00', 'x')",
     "no owning organization"),
    ("crm: INSERT INTO contacts (contact_id, account_id, name, created_at, created_by) VALUES ('C-ORPHAN', "
     "'ACC-ORPHAN', 'x', '2026-09-05T09:00:00+00:00', 'x')", "no owning organization"),
    ("console: UPDATE console_meta SET value = 'PENDING' WHERE key = 'attribution_backfill'", "pending backfill"),
    ("crm: UPDATE crm_meta SET value = '1' WHERE key = 'schema_version'", "not at the current schema"),
])
def test_a_source_that_cannot_be_imported_safely_is_refused_before_anything_is_written(small_source, migrated,
                                                                                         damage, expected):
    root, org = small_source
    store, sql = damage.split(": ", 1)
    with closing(sqlite3.connect(root / "local" / f"{store}.sqlite3")) as db, db:
        db.execute(sql)
    with pytest.raises(ImportRefused, match=expected):
        run_import(root, migrated["app"], dry_run=False, migration_url=migrated["migrator"])
    assert _no_rows(migrated["app"], org)


def test_a_real_import_needs_the_migration_role(small_source, migrated):
    root, org = small_source
    with pytest.raises(ImportRefused, match="BAEC_MIGRATION_DATABASE_URL"):
        run_import(root, migrated["app"], dry_run=False)
    assert _no_rows(migrated["app"], org)


def test_the_command_line_prints_a_report_and_never_a_credential(small_source, migrated, monkeypatch, capsys):
    root, org = small_source
    monkeypatch.setenv("BAEC_DATABASE_URL", migrated["app"])
    monkeypatch.setenv("BAEC_MIGRATION_DATABASE_URL", migrated["migrator"])
    assert main([str(root), "--dry-run"]) == 0
    out = capsys.readouterr()
    report = json.loads(out.out)
    assert report["dry_run"] and report["verification"]["matches"]
    for url in migrated.values():
        assert urlsplit(url).password not in out.out + out.err
    assert _no_rows(migrated["app"], org)
    shutil.rmtree(root)
    assert main([str(root)]) == 1  # a missing source is refused


# --- Engine 1 and Engine 2 (Phase 3C) ---------------------------------------------------------------------------------


def _engine_rows(app_url: str, organization_id: str, workspace: str, sql: str, args: tuple = ()) -> list[tuple]:
    with psycopg.connect(app_url) as db:
        db.execute("SELECT set_config('baec.organization_id', %s, true), set_config('baec.workspace', %s, true)",
                   (organization_id, workspace))
        return [tuple(r) for r in db.execute(sql, args).fetchall()]


def test_engine1_and_engine2_records_are_imported_and_verified_through_both_engines(source, imported):
    report = imported["real"]
    org_a = report.source_counts[source["orgs"]["A"]]
    for table in ("engine1.accounts", "engine1.interactions", "engine1.interaction_evidence",
                  "engine1.human_authorizations", "engine1.baec_records", "engine1.criterion_assessments",
                  "engine1.stringency_expressions", "engine1.dormancy_judgments", "engine2.monitoring_plans",
                  "engine2.observations", "engine2.correspondence_candidates", "engine2.reviews",
                  "engine2.audit_events"):
        assert org_a.get(table, 0) > 0, table
    labels = report.verification["checked"]
    assert labels > 100 and report.verification["matches"]


def test_engine_ids_buyer_language_and_history_are_preserved_byte_for_byte(source, imported, migrated):
    organization_id, world = source["orgs"]["A"], source["worlds"]["A"]
    for table, columns in (("baec_records", "baec_id, account_id, buyer_exact_statement, captured_at, "
                                            "classification, classification_reason, confirmation_authorization_id"),
                           ("interaction_evidence", "evidence_id, interaction_id, provenance, text"),
                           ("human_authorizations", "authorization_id, authorized_by, authorized_at, action, "
                                                    "subject_id, target_state"),
                           ("stringency_expressions", "baec_id, verbatim_text, comparator, numeric_value, unit, "
                                                      "timing_text"),
                           ("dormancy_judgments", "judgment_id, baec_id, plausibility, addressability, notes, "
                                                  "authorization_id")):
        with closing(sqlite3.connect(f"file:{source['root'] / 'local' / 'engine1.sqlite3'}?mode=ro", uri=True)) as db:
            owned = {r[0] for r in db.execute("SELECT account_id FROM accounts")}
            source_rows = db.execute(f"SELECT {columns} FROM {table}").fetchall()
        target = _engine_rows(migrated["app"], organization_id, "local", f"SELECT {columns} FROM baec_engine1.{table}")
        assert set(target) <= set(source_rows) and target, table  # this organization's share, unchanged
    assert any(world.baec_id == r[0] for r in _engine_rows(
        migrated["app"], organization_id, "local", "SELECT baec_id FROM baec_engine1.baec_records"))
    assert owned


def test_new_engine_ids_continue_after_the_imported_ids(source, imported, storage):
    from baec_app.data.repository import Repository
    from baec_console.postgres.engine1 import Engine1Connection
    organization_id = source["orgs"]["A"]
    with storage.database.tenant(organization_id, "local") as connection:
        engine1 = Engine1Connection(connection)
        largest = engine1.execute("SELECT max(evidence_id) FROM interaction_evidence").fetchone()[0]
        interaction = engine1.execute("SELECT interaction_id, text FROM interactions ORDER BY rowid").fetchone()
        new_id = Repository(engine1)._insert_returning(
            "INSERT INTO interaction_evidence (interaction_id, provenance, text) VALUES (?, ?, ?) RETURNING evidence_id",
            (interaction[0], "SELLER_OBSERVATION", interaction[1][:5]))
        assert new_id == largest + 1


def test_a_differing_engine_record_refuses_the_whole_import(small_source, migrated):
    root, org = small_source
    run_import(root, migrated["app"], dry_run=False, migration_url=migrated["migrator"])
    with closing(sqlite3.connect(root / "local" / "engine1.sqlite3")) as db, db:
        db.execute("UPDATE accounts SET name = 'Renamed in SQLite after the import'")  # accounts are not append-only
    with pytest.raises(ImportRefused, match="conflicting") as refused:
        run_import(root, migrated["app"], dry_run=False, migration_url=migrated["migrator"])
    conflicts = refused.value.report.conflicts
    assert [c["table"] for c in conflicts] == ["engine1.accounts"] and conflicts[0]["existing_record_differs"]
    assert _engine_rows(migrated["app"], org, "local", "SELECT name FROM baec_engine1.accounts") == [
        ("Small account (synthetic)",)]  # never overwritten


def test_engine_rows_without_an_owner_refuse_the_import(small_source, migrated):
    root, org = small_source
    with closing(sqlite3.connect(root / "local" / "engine1.sqlite3")) as db, db:
        db.execute("INSERT INTO accounts (account_id, name) VALUES ('ACC-UNOWNED', 'No owner (synthetic)')")
    with pytest.raises(ImportRefused, match="engine rows have no single owning organization"):
        run_import(root, migrated["app"], dry_run=True)
    assert _no_rows(migrated["app"], org)


def test_ai_provenance_or_bridge_history_refuses_the_import(small_source, migrated):
    root, org = small_source
    path = root / "local" / "engine1.sqlite3"
    with closing(sqlite3.connect(path)) as db, db:
        account_id = db.execute("SELECT account_id FROM accounts").fetchone()[0]
        db.execute("INSERT INTO interactions VALUES ('INT-AI', ?, '2026-09-05T09:00:00+00:00', 'Buyer: synthetic')",
                   (account_id,))
        db.execute("INSERT INTO ai_runs VALUES ('RUN-1', 'anthropic', 't', 'v', ?, 'INT-AI', 'm', 's', '1', 'p', ?, "
                   "'i', ?, 'o', ?, 'c', 'r', ?, 'v2', '2026-09-05T09:00:00+00:00', NULL)",
                   (account_id, *(("0" * 64,) * 4)))
    with pytest.raises(ImportRefused, match="ai_runs rows; AI provenance and proposal-bridge history is not imported"):
        run_import(root, migrated["app"], dry_run=True)
    assert _no_rows(migrated["app"], org)


def test_a_failed_engine_insert_rolls_back_the_product_rows_too(small_source, migrated, monkeypatch):
    """The whole import is one transaction: a refusal by an engine guard leaves nothing, product rows included."""
    from baec_console.postgres import engine_import
    root, org = small_source
    original = engine_import.insert

    def failing(connection, row):
        if row.table == "accounts":
            connection.execute("SELECT 1 / 0")
        return original(connection, row)
    monkeypatch.setattr(engine_import, "insert", failing)
    with pytest.raises(ImportRefused, match="conflicting"):
        run_import(root, migrated["app"], dry_run=False, migration_url=migrated["migrator"])
    assert _no_rows(migrated["app"], org)
    assert _engine_rows(migrated["app"], org, "local", "SELECT count(*) FROM baec_engine1.accounts") == [(0,)]
