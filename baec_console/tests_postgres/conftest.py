"""PostgreSQL integration fixtures. Separate from baec_console/tests, which forbids every network connection.

Run against the disposable local test database only (scripts/postgres_test_db.sh test). The connection URLs come
from the environment that script prints; they hold test-only, per-machine credentials and are never printed here.
Each session drops and re-creates the BAEC schemas with the ordered migrations (test database only), and each test
uses fresh organization ids, so tests never depend on one another's rows. All data is synthetic.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

import pytest

psycopg = pytest.importorskip("psycopg", reason="PostgreSQL integration tests need requirements-postgres.txt")

from baec_console.identity import UserKind  # noqa: E402
from baec_console.postgres.database import TenantDatabase  # noqa: E402
from baec_console.postgres.migrate import apply_migrations  # noqa: E402
from baec_console.postgres.stores import PostgresProductStorage  # noqa: E402

AT = datetime(2026, 9, 5, 9, 0, tzinfo=timezone.utc)
_VARIABLES = ("BAEC_TEST_POSTGRES_ADMIN_URL", "BAEC_TEST_POSTGRES_MIGRATOR_URL", "BAEC_TEST_POSTGRES_APP_URL",
              "BAEC_TEST_POSTGRES_AUTH_URL", "BAEC_TEST_POSTGRES_PROVISIONER_URL", "BAEC_TEST_POSTGRES_BACKUP_URL")
# A synthetic OpenID Connect issuer for tests (.invalid is never resolvable). Production configuration requires https.
TEST_ISSUER = "https://issuer.baec-test.invalid"


def _url(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        pytest.exit(f"{name} is not set: run scripts/postgres_test_db.sh test", returncode=2)
    if "127.0.0.1" not in value and "localhost" not in value:
        pytest.exit(f"{name} must point at the local test database", returncode=2)
    return value


@pytest.fixture(scope="session")
def urls() -> dict[str, str]:
    return {name.split("_")[3].lower(): _url(name) for name in _VARIABLES}


@pytest.fixture(scope="session")
def migrated(urls) -> dict[str, str]:
    """A freshly migrated test schema. The reset uses the test container's bootstrap superuser and exists only here."""
    with psycopg.connect(urls["admin"], autocommit=True) as admin:
        admin.execute("DROP SCHEMA IF EXISTS baec_auth CASCADE")
        admin.execute("DROP SCHEMA IF EXISTS baec_engine1 CASCADE")
        admin.execute("DROP SCHEMA IF EXISTS baec_engine2 CASCADE")
        admin.execute("DROP SCHEMA IF EXISTS baec CASCADE")
        admin.execute("DROP SCHEMA IF EXISTS baec_migrations CASCADE")
    assert apply_migrations(urls["migrator"]) == [1, 2, 3, 4, 5, 6]
    return urls


@pytest.fixture(scope="session")
def database(migrated):
    with TenantDatabase(migrated["app"], max_size=4) as db:
        yield db


@pytest.fixture
def storage(database) -> PostgresProductStorage:
    return PostgresProductStorage(database)


@pytest.fixture
def admin(migrated):
    with psycopg.connect(migrated["admin"], autocommit=True) as connection:
        yield connection


@pytest.fixture
def app(migrated):
    """A raw application-role connection: no store, no application filter. Tests set the tenant themselves."""
    with psycopg.connect(migrated["app"], autocommit=True) as connection:
        yield connection


def auth_section(issuer: str = TEST_ISSUER) -> dict:
    """A production-shaped Streamlit [auth] section with synthetic, test-only values."""
    return {"redirect_uri": "https://console.baec-test.invalid/oauth2callback", "cookie_secret": "c" * 48,
            "client_id": "baec-test-client", "client_secret": "baec-test-client-secret",
            "server_metadata_url": issuer + "/.well-known/openid-configuration"}


def production_environ(urls: dict, issuer: str = TEST_ISSUER) -> dict:
    return {"BAEC_DEPLOYMENT_MODE": "production", "BAEC_DATABASE_URL": urls["app"],
            "BAEC_AUTH_DATABASE_URL": urls["auth"], "BAEC_AUTH_ISSUER": issuer}


def fresh(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


def provision(storage: PostgresProductStorage, organization_id: str, user_id: str, name: str,
              kind: UserKind = UserKind.HUMAN) -> None:
    with storage.session(organization_id, "local") as s:
        s.identity.ensure_organization(organization_id, f"Organization {organization_id} (synthetic)", AT)
        s.identity.ensure_user(user_id, organization_id, name, AT, kind=kind)


ENGINE1_PROBE_ROLE = "baec_e1_probe"
ENGINE1_TAMPER_ROLE = "baec_e1_tamper"


@pytest.fixture(scope="session")
def engine1_roles(migrated):
    """Two test-only roles on the Engine 1 tables, both NOLOGIN, NOSUPERUSER, NOBYPASSRLS, and owning nothing, so
    row-level security still applies to them. The test superuser switches to them with SET ROLE.

        baec_e1_probe   every DML privilege (SELECT, INSERT, UPDATE, DELETE): what an owner or a widened grant could
                        do; used to show that the triggers, not only the application role's grants, refuse a change
        baec_e1_tamper  the same, plus SET on session_replication_role, which stops triggers and foreign keys: used
                        only to simulate corruption that no guarded write can produce (engine1_on_postgres.py)
    """
    with psycopg.connect(migrated["admin"], autocommit=True) as admin:
        for role in (ENGINE1_PROBE_ROLE, ENGINE1_TAMPER_ROLE):
            if admin.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone():
                admin.execute(f"REVOKE SET ON PARAMETER session_replication_role FROM {role}")
                admin.execute(f"DROP OWNED BY {role}")
                admin.execute(f"DROP ROLE {role}")
            admin.execute(f"CREATE ROLE {role} NOLOGIN NOSUPERUSER NOBYPASSRLS")
            admin.execute(f"GRANT USAGE ON SCHEMA baec, baec_engine1 TO {role}")
            admin.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA baec_engine1 TO {role}")
            admin.execute(f"GRANT INSERT ON baec.organizations TO {role}")
            admin.execute(f"GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA baec_engine1 TO {role}")
            admin.execute(f"GRANT EXECUTE ON FUNCTION baec.current_organization(), baec.current_workspace() TO {role}")
        admin.execute(f"GRANT SET ON PARAMETER session_replication_role TO {ENGINE1_TAMPER_ROLE}")
    yield {"probe": ENGINE1_PROBE_ROLE, "tamper": ENGINE1_TAMPER_ROLE}
    with psycopg.connect(migrated["admin"], autocommit=True) as admin:
        admin.execute(f"REVOKE SET ON PARAMETER session_replication_role FROM {ENGINE1_TAMPER_ROLE}")
        for role in (ENGINE1_PROBE_ROLE, ENGINE1_TAMPER_ROLE):
            admin.execute(f"DROP OWNED BY {role}")
            admin.execute(f"DROP ROLE {role}")
