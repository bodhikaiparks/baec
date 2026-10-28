"""V0.4 Phase 5: the backup and restore drill, and restores that must not be served. (IMPLEMENTATION.)

The drill, on synthetic data only, with the real tools and roles:
  1. a fresh database, migrated by the migration role; organizations, HUMAN users, and sign-in links made by the
     provisioning role; then, through signed-in production consoles: accounts, buyer interactions, an Engine 1
     condition with its dormancy judgment and state change, Engine 2 monitoring with an observation, a correction,
     and a review, an Engine 3 preparation with its review, and CRM contacts, tasks, activities, and links;
  2. a backup by the ops image (deploy/ops: pg_dump as baec_backup over verified TLS, with a checksum);
  3. a second, empty database prepared exactly as the DBA bootstrap prepares one;
  4. a restore by the ops image (pg_restore as the cluster administrator, single transaction);
  5. the production startup gate on the restored database (migrations, checksums, roles, RLS, every schema);
  6-8. every BAEC table row for row (ids, buyer evidence, Engine 1 and 2 histories, sequences) equal to the source;
  9. row-level security on the restored database, as the application role;
 10. the same console reads, signed in through the restored sign-in links, as on the source; and new writes work.
Then damaged restores, each of which production must refuse to serve: a tampered file, a truncated file, a partial
restore, an altered schema, a missing migration record, and a restore over existing data.

The ops container shares the test server's network namespace (it reaches it as localhost, which the test
certificate names). Software tests on a disposable cluster, not a measurement of production recovery time.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from baec_console import demo_seed, production
from baec_console import storage as storage_selection
from baec_console.postgres import migrate, provision
from baec_console.postgres.gate import Engine1SchemaRefused, check_database

from .conftest import TEST_ISSUER, auth_section, fresh, production_environ, psycopg
from .test_migrations_and_gate import _with
from .test_production_auth import Clock, sign_in
from .test_service_parity import reads

DEPLOY = Path(__file__).resolve().parents[2] / "deploy"
OPS_IMAGE = "baec-ops:phase5-test"
SERVER = "baec-postgres-test"
CA_INSIDE = "/run/secrets/baec_postgres_ca.crt"
RUNTIME_ROLES = "baec_app, baec_auth_lookup, baec_provisioner, baec_backup"


def _docker() -> str:
    docker = shutil.which("docker")
    if docker is None:
        pytest.fail("the restore drill needs Docker, as scripts/postgres_test_db.sh does")
    return docker


@pytest.fixture(scope="module")
def ops_image():
    subprocess.run([_docker(), "build", "-q", "-t", OPS_IMAGE, str(DEPLOY / "ops")], check=True, capture_output=True)
    return OPS_IMAGE


def _create_database(migrated, name: str) -> dict[str, str]:
    """An empty database prepared as the DBA bootstrap prepares one (infra/postgres-test/initdb/00_roles.sh)."""
    with psycopg.connect(migrated["admin"], autocommit=True) as admin:
        admin.execute(f"CREATE DATABASE {name}")
        admin.execute(f"REVOKE ALL ON DATABASE {name} FROM PUBLIC")
        admin.execute(f"GRANT CONNECT, CREATE ON DATABASE {name} TO baec_migrator")
        admin.execute(f"GRANT CONNECT ON DATABASE {name} TO {RUNTIME_ROLES}")
    with psycopg.connect(_with(migrated["admin"], database=name), autocommit=True) as admin:
        admin.execute("REVOKE ALL ON SCHEMA public FROM PUBLIC")
    return {k: _with(v, database=name) for k, v in migrated.items()}


@pytest.fixture
def databases(migrated):
    """Fresh, uniquely named databases for one test; all dropped afterwards."""
    made = []

    def make(label: str) -> dict[str, str]:
        name = f"baec_{label}_{uuid.uuid4().hex[:8]}"
        made.append(name)
        return _create_database(migrated, name)
    yield make
    with psycopg.connect(migrated["admin"], autocommit=True) as admin:
        for name in made:
            admin.execute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")


def _inside(url: str) -> str:
    """The URL as the ops container sees the test server: localhost:5432, the CA mounted as a secret."""
    parts = urlsplit(url)
    return (f"postgresql://{parts.username}:{parts.password}@localhost:5432{parts.path}"
            f"?sslmode=verify-full&sslrootcert={CA_INSIDE}")


def ops(image: str, workdir: Path, variable: str, url: str, *args: str) -> subprocess.CompletedProcess:
    """Run the backup tool in the ops image, given only `variable`, as a mounted secret file."""
    secret = workdir / f"{variable.lower()}-{uuid.uuid4().hex[:6]}"
    secret.write_text(_inside(url), encoding="utf-8")
    secret.chmod(0o644)  # read by the image's postgres user through the mount; the directory is pytest's own
    ca = dict(p.split("=", 1) for p in urlsplit(url).query.split("&"))["sslrootcert"]
    backups = workdir / "backups"
    backups.mkdir(exist_ok=True)
    backups.chmod(0o777)
    try:
        return subprocess.run([_docker(), "run", "--rm", "--network", f"container:{SERVER}",
                               "-v", f"{ca}:{CA_INSIDE}:ro", "-v", f"{secret}:/run/secrets/url:ro",
                               "-v", f"{backups}:/backups", "-e", f"{variable}_FILE=/run/secrets/url", image, *args],
                              capture_output=True, text=True, timeout=300)
    finally:
        secret.unlink()


def _refusal(urls: dict) -> storage_selection.StorageRefused:
    with pytest.raises(storage_selection.StorageRefused) as refused:
        production.open_runtime(auth_section(), production_environ(urls))
    return refused.value


# --- representative data, through production paths ----------------------------------------------------------------


class Source:
    """Two organizations, populated as their signed-in users would. Everything synthetic."""

    def __init__(self, urls: dict) -> None:
        assert migrate.apply_migrations(urls["migrator"]) == [1, 2, 3, 4, 5, 6]
        self.org_a, self.org_b = fresh("org-a"), fresh("org-b")
        self.subject_a, self.subject_b = fresh("subject-a"), fresh("subject-b")
        environ = {"BAEC_PROVISIONING_DATABASE_URL": urls["provisioner"]}
        for org, user, name, subject in ((self.org_a, "user-a", "Avery (synthetic)", self.subject_a),
                                         (self.org_b, "user-b", "Blake (synthetic)", self.subject_b)):
            for argv in (["create-organization", "--organization", org, "--name", f"{name} organization"],
                         ["create-user", "--organization", org, "--user", f"{user}-{org}", "--name", name],
                         ["link", "--organization", org, "--user", f"{user}-{org}", "--issuer", TEST_ISSUER,
                          "--subject", subject, "--operator", "operator (synthetic)"]):
                assert provision.main(argv, environ) == 0
        self.clock = Clock()
        runtime = production.open_runtime(auth_section(), production_environ(urls))
        runtime.clock = self.clock
        try:
            self._populate_a(sign_in(runtime, self.clock, self.subject_a))
            self._populate_b(sign_in(runtime, self.clock, self.subject_b))
        finally:
            runtime.close()

    def _populate_a(self, console) -> None:
        service = console.local
        script = demo_seed.ScriptedClock(datetime(2026, 9, 4, 15, 0, tzinfo=timezone.utc))
        service.clock = script
        self.account_a = service.create_account(demo_seed.ACCOUNT, industry=demo_seed.INDUSTRY,
                                                owner_note="Synthetic restore-drill account")
        interaction = service.record_interaction(
            self.account_a, buyer_role=demo_seed.BUYER_ROLE,
            occurred_at=datetime(2026, 9, 4, 14, 0, tzinfo=timezone.utc), buyer_statement=demo_seed.BUYER_STATEMENT,
            seller_notes=demo_seed.SELLER_NOTES)
        demo_seed._complete(service, script, self.account_a, interaction)  # Engine 1, Engine 2, Engine 3, reviews
        contact = service.crm_add_contact(self.account_a, name="Jordan Lee (synthetic)", title="Operations lead")
        task = service.crm_add_task(self.account_a, contact_id=contact, title="Review renewal notes (synthetic)",
                                    due_date=None, priority="HIGH")
        service.crm_complete_task(task)
        service.crm_add_task(self.account_a, contact_id=None, title="Prepare follow-up (synthetic)", due_date=None,
                             priority="LOW")
        service.crm_link_interaction(interaction, self.account_a, contact_id=contact, interaction_type="CALL")
        service.crm_add_activity(self.account_a, contact_id=contact, interaction_type="MEETING",
                                 occurred_at=datetime(2026, 9, 5, 15, 0, tzinfo=timezone.utc),
                                 raw_notes="Met with the imaging team (synthetic).", summary="Met (synthetic)")

    def _populate_b(self, console) -> None:
        service = console.local
        self.account_b = service.create_account("Northwind Clinics (synthetic)", industry="Clinics")
        service.record_interaction(self.account_b, buyer_role="Director of operations (synthetic)",
                                   occurred_at=datetime(2026, 9, 10, 10, 0, tzinfo=timezone.utc),
                                   buyer_statement="We would revisit this if the contract price rises more than 5% "
                                                   "(synthetic).", seller_notes="Synthetic note.")
        service.crm_add_contact(self.account_b, name="Sam Ortiz (synthetic)")

    def reads(self, urls: dict) -> dict:
        """What each organization's signed-in user sees, keyed by organization."""
        runtime = production.open_runtime(auth_section(), production_environ(urls))
        runtime.clock = self.clock
        try:
            return {org: reads(sign_in(runtime, self.clock, subject))
                    for org, subject in ((self.org_a, self.subject_a), (self.org_b, self.subject_b))}
        finally:
            runtime.close()


def fingerprint(url: str) -> dict[str, tuple]:
    """Every BAEC table, row for row and column for column (as the superuser), and every sequence position."""
    with psycopg.connect(url) as admin:
        tables = [r[0] for r in admin.execute(
            "SELECT n.nspname || '.' || c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE c.relkind = 'r' AND n.nspname LIKE 'baec%' ORDER BY 1")]
        found = {t: tuple(admin.execute(f"SELECT count(*), md5(coalesce(string_agg(t::text, '|' ORDER BY t::text), "
                                        f"'')) FROM {t} t").fetchone()) for t in tables}
        for name, value in admin.execute(
                "SELECT schemaname || '.' || sequencename, last_value FROM pg_sequences "
                "WHERE schemaname LIKE 'baec%'").fetchall():
            found[f"sequence {name}"] = (value,)
    return found


def _buyer_evidence(url: str) -> tuple[list[tuple], list[tuple]]:
    """Engine 1's buyer-fact evidence rows, and every interaction's stored text (which holds the exact statement)."""
    with psycopg.connect(url) as admin:
        return (admin.execute("SELECT organization_id, interaction_id, evidence_id, text FROM "
                              "baec_engine1.interaction_evidence WHERE provenance = 'BUYER_FACT' ORDER BY 1, 2, 3"
                              ).fetchall(),
                admin.execute("SELECT organization_id, interaction_id, text FROM baec_engine1.interactions "
                              "ORDER BY 1, 2").fetchall())


@pytest.fixture(scope="module")
def drill(migrated, ops_image, tmp_path_factory):
    """Steps 1-4: populate, back up, and restore into a fresh database. Shared by the drill's checks."""
    names, workdir = [], tmp_path_factory.mktemp("drill")

    def database(label: str) -> dict:
        names.append(f"baec_{label}_{uuid.uuid4().hex[:8]}")
        return _create_database(migrated, names[-1])
    source_urls = database("drill_source")
    source = Source(source_urls)
    before = fingerprint(source_urls["admin"])
    backed_up = ops(ops_image, workdir, "BAEC_BACKUP_DATABASE_URL", source_urls["backup"], "backup", "--out",
                    "/backups")
    assert backed_up.returncode == 0, backed_up.stderr
    dump = workdir / "backups" / backed_up.stdout.split("backup written: ")[1].strip()
    target_urls = database("drill_restored")
    restored = ops(ops_image, workdir, "BAEC_RESTORE_DATABASE_URL", target_urls["admin"], "restore",
                   f"/backups/{dump.name}")
    assert restored.returncode == 0, restored.stderr
    after = fingerprint(target_urls["admin"])  # before any later test adds to the restored database
    yield {"source": source, "source_urls": source_urls, "target_urls": target_urls, "before": before,
           "after": after, "dump": dump, "workdir": workdir, "outputs": (backed_up, restored)}
    with psycopg.connect(migrated["admin"], autocommit=True) as admin:
        for name in names:
            admin.execute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")


# --- the drill ----------------------------------------------------------------------------------------------------------


def test_the_backup_is_a_checksummed_owner_only_file_and_no_tool_output_shows_a_credential(drill, migrated):
    dump = drill["dump"]
    assert dump.stat().st_size > 10_000 and oct(dump.stat().st_mode & 0o777) == "0o600"
    checksum = dump.with_name(dump.name + ".sha256").read_text(encoding="utf-8").split()
    assert checksum[1] == dump.name and len(checksum[0]) == 64
    for output in drill["outputs"]:
        for url in (drill["source_urls"]["backup"], drill["target_urls"]["admin"]):
            assert urlsplit(url).password not in output.stdout + output.stderr
    assert dump.read_bytes()[:5] == b"PGDMP"  # pg_dump custom format


def test_the_restored_database_passes_the_production_startup_gate(drill):
    report = check_database(drill["target_urls"]["app"], drill["target_urls"]["auth"])
    assert report.schema_versions == (1, 2, 3, 4, 5, 6) and report.role == "baec_app"
    production.open_runtime(auth_section(), production_environ(drill["target_urls"])).close()


def test_every_row_id_and_sequence_is_restored_exactly(drill):
    after = drill["after"]
    assert after == drill["before"]
    populated = [t for t, v in after.items() if not t.startswith("sequence") and v[0] > 0]
    for table in ("baec.organizations", "baec.users", "baec_auth.auth_identities", "baec.crm_contacts",
                  "baec.crm_tasks", "baec.crm_activities", "baec.crm_interaction_links", "baec.preparations",
                  "baec.preparation_reviews", "baec.events", "baec.attributions", "baec_engine1.baec_records",
                  "baec_engine1.interaction_evidence", "baec_engine1.dormancy_judgments",
                  "baec_engine1.account_state_transitions", "baec_engine2.observations", "baec_engine2.reviews",
                  "baec_engine2.audit_events", "baec_migrations.schema_migrations"):
        assert table in populated, table  # the drill really covered it


def test_buyer_evidence_is_restored_byte_for_byte(drill):
    source, restored = _buyer_evidence(drill["source_urls"]["admin"]), _buyer_evidence(drill["target_urls"]["admin"])
    assert restored == source
    evidence, interactions = restored
    assert evidence and {row[0] for row in evidence} == {drill["source"].org_a}  # captured with A's condition
    assert len(interactions) == 2 and demo_seed.BUYER_STATEMENT in interactions[[r[0] for r in interactions].index(
        drill["source"].org_a)][2]
    assert "more than 5% (synthetic)." in interactions[[r[0] for r in interactions].index(drill["source"].org_b)][2]


def test_row_level_security_holds_on_the_restored_database(drill):
    source = drill["source"]
    with psycopg.connect(drill["target_urls"]["app"], autocommit=True) as app:
        assert app.execute("SELECT count(*) FROM baec.organizations").fetchone()[0] == 0  # no tenant: nothing
        for org, other in ((source.org_a, source.org_b), (source.org_b, source.org_a)):
            with app.transaction():
                app.execute("SELECT set_config('baec.organization_id', %s, true)", (org,))
                assert {r[0] for r in app.execute("SELECT organization_id FROM baec.organizations")} == {org}
                for table in ("baec.crm_contacts", "baec_engine1.interactions", "baec.users"):
                    assert app.execute(f"SELECT count(*) FROM {table} WHERE organization_id = %s",
                                       (other,)).fetchone()[0] == 0
    with psycopg.connect(drill["target_urls"]["admin"]) as admin:
        forced = admin.execute("SELECT count(*) FILTER (WHERE NOT (relrowsecurity AND relforcerowsecurity)), count(*) "
                               "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE c.relkind = 'r' "
                               "AND n.nspname IN ('baec', 'baec_engine1', 'baec_engine2') AND EXISTS (SELECT 1 FROM "
                               "pg_attribute a WHERE a.attrelid = c.oid AND a.attname = 'organization_id')").fetchone()
        assert forced[0] == 0 and forced[1] > 50


def test_signed_in_users_see_the_same_console_on_the_restored_database(drill):
    source = drill["source"]
    before, after = source.reads(drill["source_urls"]), source.reads(drill["target_urls"])
    assert after == before
    assert after[source.org_a]["local"]["accounts"] == [source.account_a]
    assert after[source.org_b]["local"]["accounts"] == [source.account_b]
    assert after[source.org_a]["local"]["preparations"] and after[source.org_a]["local"]["contacts"] != [[]]


def test_the_restored_database_accepts_new_work_after_its_restored_history(drill):
    source, urls = drill["source"], drill["target_urls"]
    runtime = production.open_runtime(auth_section(), production_environ(urls))
    runtime.clock = source.clock
    try:
        console = sign_in(runtime, source.clock, source.subject_a)
        account = console.local.create_account("Account created after the restore (synthetic)")
        console.local.crm_add_contact(account, name="New contact (synthetic)")
        assert set(console.local.account_ids()) == {source.account_a, account}
    finally:
        runtime.close()


# --- damaged or incomplete restores -------------------------------------------------------------------------------------


def test_a_tampered_backup_is_refused_before_the_database_is_touched(drill, ops_image, databases):
    workdir, dump = drill["workdir"], drill["dump"]
    tampered = dump.with_name("tampered-" + dump.name)
    data = bytearray(dump.read_bytes())
    data[len(data) // 2] ^= 0xFF
    tampered.write_bytes(bytes(data))
    shutil.copy(dump.with_name(dump.name + ".sha256"), tampered.with_name(tampered.name + ".sha256"))
    target = databases("tampered")
    result = ops(ops_image, workdir, "BAEC_RESTORE_DATABASE_URL", target["admin"], "restore",
                 f"/backups/{tampered.name}")
    assert result.returncode == 1 and "does not match its checksum" in result.stderr
    assert "not at the expected migration version" in str(_refusal(target))


def test_a_truncated_backup_fails_as_a_whole_and_leaves_nothing_to_serve(drill, ops_image, databases):
    """A file cut short before its checksum was taken: the checksum matches, pg_restore fails, the single
    transaction rolls back, and the empty database is refused."""
    workdir, dump = drill["workdir"], drill["dump"]
    truncated = dump.with_name("truncated-" + dump.name)
    truncated.write_bytes(dump.read_bytes()[: dump.stat().st_size * 2 // 3])
    verified = subprocess.run(["python3", str(DEPLOY / "ops" / "baec_backup.py"), "verify", str(truncated)],
                              capture_output=True, text=True)
    assert verified.returncode == 1  # no checksum file yet
    truncated.with_name(truncated.name + ".sha256").write_text(
        f"{hashlib.sha256(truncated.read_bytes()).hexdigest()}  {truncated.name}\n", encoding="utf-8")
    target = databases("truncated")
    result = ops(ops_image, workdir, "BAEC_RESTORE_DATABASE_URL", target["admin"], "restore",
                 f"/backups/{truncated.name}")
    assert result.returncode == 1 and "pg_restore failed" in result.stderr
    with psycopg.connect(target["admin"]) as admin:
        assert admin.execute("SELECT count(*) FROM pg_namespace WHERE nspname LIKE 'baec%'").fetchone()[0] == 0
    assert "not at the expected migration version" in str(_refusal(target))


def test_an_interrupted_restore_is_refused(drill, databases):
    """A restore that stopped after loading rows but before constraints, indexes, triggers, and row-level security
    policies (what running pg_restore by hand, without the tool's single transaction, can leave): the migration record
    says complete and the data is there, but production stays closed."""
    target = databases("partial")
    parts = urlsplit(_inside(target["admin"]))
    ca = dict(p.split("=", 1) for p in urlsplit(target["admin"]).query.split("&"))["sslrootcert"]
    result = subprocess.run(
        [_docker(), "run", "--rm", "--network", f"container:{SERVER}", "-v", f"{ca}:{CA_INSIDE}:ro",
         "-v", f"{drill['workdir'] / 'backups'}:/backups:ro", "-e", f"PGPASSWORD={parts.password}",
         "-e", "PGSSLMODE=verify-full", "-e", f"PGSSLROOTCERT={CA_INSIDE}", "--entrypoint", "pg_restore",
         OPS_IMAGE, "--host", "localhost", "--username", parts.username, "--dbname", parts.path.strip("/"),
         "--single-transaction", "--section=pre-data", "--section=data", f"/backups/{drill['dump'].name}"],
        capture_output=True, text=True, timeout=300)
    assert result.returncode == 0, result.stderr
    with psycopg.connect(target["admin"]) as admin:  # the rows and the migration record are there...
        assert admin.execute("SELECT count(*) FROM baec_migrations.schema_migrations").fetchone()[0] == 6
        assert admin.execute("SELECT count(*) FROM baec_engine1.interactions").fetchone()[0] == 2
    refused = _refusal(target)  # ...but not the policies, triggers, or constraints that make them safe to serve
    assert "row-level security policies on" in str(refused)


@pytest.mark.parametrize("damage,expected", [
    ("DROP TRIGGER baec_records_protected_no_update ON baec_engine1.baec_records", Engine1SchemaRefused),
    ("DELETE FROM baec_migrations.schema_migrations WHERE version = 5", "not at the expected migration version"),
    ("ALTER TABLE baec.crm_contacts NO FORCE ROW LEVEL SECURITY", "not enabled and forced on crm_contacts"),
    ("UPDATE baec_migrations.schema_migrations SET checksum = repeat('0', 64) WHERE version = 4",
     "not at the expected migration version"),
])
def test_a_restored_database_that_was_altered_afterwards_is_refused(drill, ops_image, databases, damage, expected):
    target = databases("altered")
    restored = ops(ops_image, drill["workdir"], "BAEC_RESTORE_DATABASE_URL", target["admin"], "restore",
                   f"/backups/{drill['dump'].name}")
    assert restored.returncode == 0, restored.stderr
    production.open_runtime(auth_section(), production_environ(target)).close()  # intact first
    with psycopg.connect(target["admin"], autocommit=True) as admin:
        admin.execute(damage)
    refused = _refusal(target)
    if isinstance(expected, str):
        assert expected in str(refused)
    else:
        assert str(refused) == str(expected(["x"])) and refused.failures


def test_a_restore_over_an_existing_database_is_refused(drill, ops_image):
    before = fingerprint(drill["target_urls"]["admin"])
    result = ops(ops_image, drill["workdir"], "BAEC_RESTORE_DATABASE_URL", drill["target_urls"]["admin"], "restore",
                 f"/backups/{drill['dump'].name}")
    assert result.returncode == 1 and "already contains BAEC schemas" in result.stderr
    assert fingerprint(drill["target_urls"]["admin"]) == before  # untouched


def test_the_backup_tool_refuses_a_plaintext_url_and_the_application_role(drill, ops_image):
    workdir, urls = drill["workdir"], drill["source_urls"]
    secret = workdir / "plaintext-url"
    secret.write_text(_inside(urls["backup"]).replace("sslmode=verify-full", "sslmode=require"), encoding="utf-8")
    secret.chmod(0o644)
    result = subprocess.run([_docker(), "run", "--rm", "--network", f"container:{SERVER}", "-v",
                             f"{secret}:/run/secrets/url:ro", "-e", "BAEC_BACKUP_DATABASE_URL_FILE=/run/secrets/url",
                             ops_image, "backup", "--out", "/tmp/out"], capture_output=True, text=True, timeout=120)
    assert result.returncode == 1 and "verified TLS connection (sslmode=verify-full)" in result.stderr
    assert urlsplit(urls["backup"]).password not in result.stdout + result.stderr
    # The application role cannot make a complete backup: row-level security stops pg_dump (row_security = off).
    as_app = ops(ops_image, workdir, "BAEC_BACKUP_DATABASE_URL", urls["app"], "backup", "--out", "/backups")
    assert as_app.returncode == 1 and "pg_dump failed" in as_app.stderr
