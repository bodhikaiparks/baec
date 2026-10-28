"""V0.4 Phase 3C: the production gate validates the live product and Engine 1 schema, and fails closed on drift.

The expected schema is not hand-written: schema_manifest.json is a catalog snapshot of a freshly migrated database
(catalog.py), and the first test rebuilds it from a fresh migration and requires equality, so it cannot drift from the
migrations. Engine 1's triggers are additionally derived from Engine 1's own SQLite schema statements, one PostgreSQL
trigger for each of the 92. Every sabotage below is made by the migration role (the owner) or the test superuser on a
scratch database; production must refuse with one fixed message every time, name the changed object for operators,
and repair nothing.
"""

from __future__ import annotations

from urllib.parse import urlsplit

import pytest

from baec_app.data import database as engine1
from baec_console.postgres import catalog, gate, migrate
from baec_console.postgres.gate import ENGINE1_SCHEMA_MISMATCH, PRODUCT_SCHEMA_MISMATCH

from .conftest import psycopg
from .test_migrations_and_gate import _production_refusal, scratch_database  # noqa: F401


def test_the_schema_manifest_is_exactly_a_fresh_migration(scratch_database):  # noqa: F811
    assert migrate.apply_migrations(scratch_database["migrator"]) == [1, 2, 3, 4, 5, 6]
    with psycopg.connect(scratch_database["app"]) as db:
        live = catalog.snapshot(db)
    expected = catalog.manifest()
    assert expected["migrations"] == [[m.version, m.name, m.checksum] for m in migrate.migrations()]
    assert catalog.differences(live, expected["snapshot"]) == []
    assert {k: len(v) for k, v in live.items()} == {k: len(v) for k, v in expected["snapshot"].items()}


def test_a_stale_manifest_closes_production(migrated, monkeypatch):
    monkeypatch.setattr(catalog, "_migrations", lambda: [[1, "other", "0" * 64]])
    with psycopg.connect(migrated["app"]) as db:
        assert catalog.failures(db, ("baec",)) == ["the validated schema manifest was not taken from these migrations"]


def test_the_engine1_triggers_are_one_for_each_sqlite_trigger_plus_the_postgresql_additions(migrated):
    names = gate.engine1_trigger_names()
    expected = gate.expected_engine1_triggers()
    assert len(names) == 92 and len(set(names.values())) == 92 and set(names.values()) <= set(expected)
    additions = set(expected) - set(names.values())
    assert additions == ({f"{t}_no_truncate" for t in engine1.ALL_TABLES}
                         | {f"f9_{t}_assign_id" for t in gate.ENGINE1_ROWID_KEYS})
    with psycopg.connect(migrated["app"]) as db:
        assert gate.engine1_failures(db) == []
        live = {r[0] for r in db.execute("SELECT t.tgname FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
                                         "JOIN pg_namespace n ON n.oid = c.relnamespace "
                                         "WHERE n.nspname = 'baec_engine1' AND NOT t.tgisinternal")}
    assert live == set(expected) and len(live) == 123


ENGINE1_SABOTAGE = {
    "dropped trigger": ("migrator", "DROP TRIGGER f1_interaction_evidence_verbatim ON baec_engine1.interaction_evidence",
                        "Engine 1 trigger f1_interaction_evidence_verbatim is missing"),
    "disabled trigger": ("migrator", "ALTER TABLE baec_engine1.human_authorizations DISABLE TRIGGER "
                                     "human_authorizations_no_update",
                         "Engine 1 trigger human_authorizations_no_update is not enabled"),
    "replica-only trigger": ("migrator", "ALTER TABLE baec_engine1.baec_records ENABLE REPLICA TRIGGER "
                                         "baec_records_protected_no_update",
                             "Engine 1 trigger baec_records_protected_no_update is not enabled"),
    "reordered trigger": ("migrator", "ALTER TRIGGER f1_ai_proposal_confirmations_classification ON "
                                      "baec_engine1.ai_proposal_confirmations RENAME TO "
                                      "f5_ai_proposal_confirmations_classification",
                          "Engine 1 trigger f1_ai_proposal_confirmations_classification is missing"),
    "narrowed replace guard": ("migrator", ["DROP TRIGGER f2_interaction_evidence_no_replace ON "
                                            "baec_engine1.interaction_evidence",
                                            "CREATE TRIGGER f2_interaction_evidence_no_replace BEFORE INSERT ON "
                                            "baec_engine1.interaction_evidence FOR EACH ROW EXECUTE FUNCTION "
                                            "baec_engine1.refuse_replace('#evidence_id')"],
                               "Engine 1 trigger f2_interaction_evidence_no_replace differs"),
    "weakened guard function": ("migrator", "CREATE OR REPLACE FUNCTION baec_engine1.refuse_change() RETURNS trigger "
                                            "LANGUAGE plpgsql AS $$ BEGIN RETURN NEW; END $$",
                                "function baec_engine1.refuse_change differs from its migration"),
    "weakened date arithmetic": ("migrator", "CREATE OR REPLACE FUNCTION baec_engine1.julianday(text) RETURNS double "
                                             "precision LANGUAGE sql IMMUTABLE AS $$ SELECT 0::double precision $$",
                                 "function baec_engine1.julianday differs from its migration"),
    "security definer": ("migrator", "ALTER FUNCTION baec_engine1.ai_proposal_confirmations_unretired() SECURITY "
                                     "DEFINER", "function baec_engine1.ai_proposal_confirmations_unretired is SECURITY "
                                                "DEFINER"),
    "weakened CHECK": ("migrator", ["ALTER TABLE baec_engine1.accounts DROP CONSTRAINT accounts_state_check",
                                    "ALTER TABLE baec_engine1.accounts ADD CONSTRAINT accounts_state_check CHECK "
                                    "(state IS NULL OR state <> '')"],
                       "constraint baec_engine1.accounts.accounts_state_check differs"),
    "dropped foreign key": ("migrator", "ALTER TABLE baec_engine1.interactions DROP CONSTRAINT "
                                        "interactions_organization_id_workspace_account_id_fkey",
                            "constraint baec_engine1.interactions.interactions_organization_id_workspace_account_id_"
                            "fkey is missing"),
    "dropped text-key index": ("migrator", "DROP INDEX baec_engine1.interaction_evidence_text_key",
                               "index baec_engine1.interaction_evidence_text_key is missing"),
    "dropped NOT NULL": ("migrator", "ALTER TABLE baec_engine1.interactions ALTER COLUMN text DROP NOT NULL",
                         "column baec_engine1.interactions.text differs"),
    "widened column privilege": ("migrator", "GRANT UPDATE (name) ON baec_engine1.accounts TO baec_app",
                                 "privilege baec_engine1.accounts differs"),
    "widened table privilege": ("migrator", "GRANT UPDATE ON baec_engine1.baec_records TO baec_app",
                                "privilege baec_engine1.baec_records differs"),
    "extra column": ("migrator", "ALTER TABLE baec_engine1.dormancy_judgments ADD COLUMN score integer",
                     "unexpected column baec_engine1.dormancy_judgments.score"),
    "extra function": ("migrator", "CREATE FUNCTION baec_engine1.approve() RETURNS boolean LANGUAGE sql AS "
                                   "$$ SELECT true $$", "unexpected function baec_engine1.approve"),
}

PRODUCT_SABOTAGE = {
    "dropped append-only trigger": ("migrator", "DROP TRIGGER account_meta_append_only ON baec.account_meta",
                                    "trigger baec.account_meta.account_meta_append_only is missing"),
    "disabled update guard": ("migrator", "ALTER TABLE baec.crm_activities DISABLE TRIGGER crm_activities_guard",
                              "trigger baec.crm_activities.crm_activities_guard differs"),
    "dropped truncate refusal": ("migrator", "DROP TRIGGER attributions_no_truncate ON baec.attributions",
                                 "trigger baec.attributions.attributions_no_truncate is missing"),
    "weakened CHECK": ("migrator", ["ALTER TABLE baec.crm_tasks DROP CONSTRAINT crm_tasks_priority_check",
                                    "ALTER TABLE baec.crm_tasks ADD CONSTRAINT crm_tasks_priority_check CHECK "
                                    "(priority IS NOT NULL)"],
                       "constraint baec.crm_tasks.crm_tasks_priority_check differs"),
    "dropped CHECK": ("migrator", "ALTER TABLE baec.crm_tasks DROP CONSTRAINT crm_tasks_check",
                      "constraint baec.crm_tasks.crm_tasks_check is missing"),
    "dropped foreign key": ("migrator", "ALTER TABLE baec.account_meta DROP CONSTRAINT "
                                        "account_meta_organization_id_workspace_account_id_fkey",
                            "constraint baec.account_meta.account_meta_organization_id_workspace_account_id_fkey "
                            "is missing"),
    "dropped NOT NULL": ("migrator", "ALTER TABLE baec.events ALTER COLUMN summary DROP NOT NULL",
                         "column baec.events.summary differs"),
    "widened column privilege": ("migrator", "GRANT UPDATE (name) ON baec.organizations TO baec_app",
                                 "privilege baec.organizations differs"),
    "widened select on the operator log's columns": ("migrator", "GRANT SELECT (recorded_at) ON baec.operator_log "
                                                                 "TO baec_app",
                                                     "privilege baec.operator_log differs"),
    "extra table without tenancy": ("migrator", "CREATE TABLE baec.shadow_notes (note text)",
                                    "unexpected relation baec.shadow_notes"),
    "extra view": ("migrator", "CREATE VIEW baec.all_users AS SELECT * FROM baec.users",
                   "unexpected relation baec.all_users"),
    "altered migration record": ("admin", "UPDATE baec_migrations.schema_migrations SET applied_at = applied_at",
                                 None),  # the record's facts are unchanged: not a failure (see the next case)
}


def _sabotage(urls: dict, role: str, sabotage) -> None:
    with psycopg.connect(urls[role], autocommit=True) as db:
        for statement in [sabotage] if isinstance(sabotage, str) else sabotage:
            db.execute(statement)


@pytest.mark.parametrize("case", sorted(ENGINE1_SABOTAGE))
def test_engine1_schema_drift_closes_production(scratch_database, tmp_path, monkeypatch, case):  # noqa: F811
    role, sabotage, expected = ENGINE1_SABOTAGE[case]
    assert migrate.apply_migrations(scratch_database["migrator"]) == [1, 2, 3, 4, 5, 6]
    _sabotage(scratch_database, role, sabotage)
    for attempt in range(2):  # refused every time: the gate never repairs the schema
        refused = _production_refusal(scratch_database, tmp_path / f"data-{attempt}", monkeypatch)
        assert str(refused) == ENGINE1_SCHEMA_MISMATCH
        assert any(expected in f for f in refused.failures), refused.failures
        assert urlsplit(scratch_database["app"]).password not in str(refused) + "".join(refused.failures)


@pytest.mark.parametrize("case", sorted(k for k, v in PRODUCT_SABOTAGE.items() if v[2]))
def test_product_schema_drift_closes_production(scratch_database, tmp_path, monkeypatch, case):  # noqa: F811
    role, sabotage, expected = PRODUCT_SABOTAGE[case]
    assert migrate.apply_migrations(scratch_database["migrator"]) == [1, 2, 3, 4, 5, 6]
    _sabotage(scratch_database, role, sabotage)
    for attempt in range(2):
        refused = _production_refusal(scratch_database, tmp_path / f"data-{attempt}", monkeypatch)
        assert str(refused) == PRODUCT_SCHEMA_MISMATCH
        assert any(expected in f for f in refused.failures), refused.failures


@pytest.mark.parametrize("tamper", [
    "UPDATE baec_migrations.schema_migrations SET checksum = repeat('0', 64) WHERE version = 4",
    "DELETE FROM baec_migrations.schema_migrations WHERE version = 4",
    "UPDATE baec_migrations.schema_migrations SET name = 'other' WHERE version = 4",
    "INSERT INTO baec_migrations.schema_migrations (version, name, checksum) VALUES (7, 'unknown', repeat('0', 64))",
])
def test_altered_engine1_migration_metadata_closes_production(scratch_database, tmp_path, monkeypatch,  # noqa: F811
                                                              tamper):
    migrate.apply_migrations(scratch_database["migrator"])
    _sabotage(scratch_database, "admin", tamper)
    refused = _production_refusal(scratch_database, tmp_path / "data", monkeypatch)
    assert "not at the expected migration version" in str(refused)


def test_an_unchanged_migration_record_is_not_drift(scratch_database, tmp_path, monkeypatch):  # noqa: F811
    migrate.apply_migrations(scratch_database["migrator"])
    _sabotage(scratch_database, "admin", PRODUCT_SABOTAGE["altered migration record"][1])
    from baec_console import production
    from .conftest import auth_section, production_environ
    production.open_runtime(auth_section(), production_environ(scratch_database)).close()  # opens: not drift
