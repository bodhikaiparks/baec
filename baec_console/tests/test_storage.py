"""V0.4 Phase 3: storage selection fails closed. No database or network is used here (the PostgreSQL integration
suite is baec_console/tests_postgres). All data is synthetic; these are software tests, not a security assessment."""

from __future__ import annotations

import pytest

from baec_console import deployment, storage
from baec_console.postgres import PostgresUnavailable
from baec_console.postgres.database import DatabaseConfigError, validate_database_url

from .conftest import StepClock

SECRET = "s3cr3t-test-password"
# V0.4 Phase 5: a correctly configured production URL requires verified TLS.
TLS = "?sslmode=verify-full&sslrootcert=system"

MALFORMED = {
    "empty": "",
    "blank": "   ",
    "sqlite file": "sqlite:///baec_console/.data/console.sqlite3",
    "plain path": "/var/lib/baec/console.sqlite3",
    "another database": f"mysql://baec:{SECRET}@127.0.0.1/baec",
    "no user": "postgresql://127.0.0.1:5432/baec",
    "no host": f"postgresql://baec:{SECRET}@/baec",
    "no database": f"postgresql://baec:{SECRET}@127.0.0.1:5432/",
    "bad port": f"postgresql://baec:{SECRET}@127.0.0.1:99999/baec",
    "not a url": f"baec:{SECRET} at the usual place",
}


def _production(root, monkeypatch, url: str | None):
    """Production startup (Phase 4: production.open_runtime). `root` is never used: production has no data root."""
    from baec_console import production
    monkeypatch.setenv("BAEC_DEPLOYMENT_MODE", "production")
    monkeypatch.setenv("BAEC_AUTH_DATABASE_URL", f"postgresql://baec_auth_lookup:{SECRET}@127.0.0.1:5432/baec{TLS}")
    if url is None:
        monkeypatch.delenv("BAEC_DATABASE_URL", raising=False)
    else:
        monkeypatch.setenv("BAEC_DATABASE_URL", url)
    return production.open_runtime(None)


def test_production_without_a_database_url_refuses_and_creates_nothing(tmp_path, monkeypatch):
    root = tmp_path / "data"
    with pytest.raises(storage.StorageRefused, match="BAEC_DATABASE_URL is not set"):
        _production(root, monkeypatch, None)
    assert not root.exists()


@pytest.mark.parametrize("case", sorted(MALFORMED))
def test_production_refuses_a_malformed_or_unsupported_database_url_without_echoing_it(tmp_path, monkeypatch, case):
    root = tmp_path / "data"
    with pytest.raises(storage.StorageRefused, match="Production storage refused") as refused:
        _production(root, monkeypatch, MALFORMED[case])
    assert SECRET not in str(refused.value) and (not MALFORMED[case].strip() or MALFORMED[case] not in str(refused.value))
    assert not root.exists()  # never a SQLite fallback


def test_production_refuses_when_the_postgres_driver_is_missing(tmp_path, monkeypatch):
    def missing():
        raise PostgresUnavailable("PostgreSQL support needs psycopg: install requirements-postgres.txt")
    monkeypatch.setattr("baec_console.postgres.database.require_driver", missing)
    root = tmp_path / "data"
    with pytest.raises(storage.StorageRefused, match="needs psycopg"):
        _production(root, monkeypatch, f"postgresql://baec_app:{SECRET}@127.0.0.1:5432/baec{TLS}")
    assert not root.exists()


def test_production_never_opens_a_console_by_user_id_before_storage_is_considered(tmp_path, monkeypatch):
    root = tmp_path / "data"
    monkeypatch.setenv("BAEC_DEPLOYMENT_MODE", "production")
    monkeypatch.setenv("BAEC_DATABASE_URL", "sqlite:///anything")
    with pytest.raises(deployment.ProductionAccessRefused):
        deployment.runtime_console(root, mode=deployment.DeploymentMode.PRODUCTION)
    assert not root.exists()


def test_development_refuses_a_database_url_instead_of_silently_using_sqlite(tmp_path, monkeypatch):
    root = tmp_path / "data"
    monkeypatch.setenv("BAEC_DATABASE_URL", f"postgresql://baec_app:{SECRET}@127.0.0.1:5432/baec")
    with pytest.raises(storage.StorageRefused, match="Unset it") as refused:
        deployment.runtime_console(root, mode=deployment.DeploymentMode.DEVELOPMENT, clock=StepClock())
    assert SECRET not in str(refused.value)
    assert not root.exists()


def test_development_without_a_database_url_keeps_its_sqlite_stores(tmp_path, monkeypatch):
    monkeypatch.delenv("BAEC_DATABASE_URL", raising=False)
    console = deployment.runtime_console(tmp_path / "data", mode=deployment.DeploymentMode.DEVELOPMENT,
                                         clock=StepClock())
    console.local.create_account("Development account (synthetic)")
    assert (tmp_path / "data" / "local" / "console.sqlite3").is_file()


def test_a_valid_postgres_url_is_accepted_by_the_parser():
    url = f"postgresql://baec_app:{SECRET}@127.0.0.1:5432/baec"
    assert validate_database_url(url) == url
    assert validate_database_url(" postgres://u@db.internal/baec ") == "postgres://u@db.internal/baec"
    with pytest.raises(DatabaseConfigError):
        validate_database_url(None)


def test_the_app_in_production_shows_a_refusal_and_no_data_when_startup_fails(tmp_path, monkeypatch):
    """Production startup fails here (no PostgreSQL driver, or no database: this suite opens no connection), so the app
    shows the refusal and nothing else: no sign-in page, no navigation, no data."""
    from streamlit.testing.v1 import AppTest

    def missing():
        raise PostgresUnavailable("PostgreSQL support needs psycopg: install requirements-postgres.txt")
    monkeypatch.setattr("baec_console.postgres.require_driver", missing)
    monkeypatch.setattr("baec_console.postgres.database.require_driver", missing)
    monkeypatch.setattr("baec_console.postgres.gate.require_driver", missing)
    root = tmp_path / "data"
    monkeypatch.setenv("BAEC_CONSOLE_DATA_DIR", str(root))
    monkeypatch.setenv("BAEC_DEPLOYMENT_MODE", "production")
    monkeypatch.setenv("BAEC_DATABASE_URL", f"postgresql://baec_app:{SECRET}@127.0.0.1:5432/baec{TLS}")
    monkeypatch.setenv("BAEC_AUTH_DATABASE_URL", f"postgresql://baec_auth_lookup:{SECRET}@127.0.0.1:5432/baec{TLS}")
    at = AppTest.from_file(str(__import__("pathlib").Path(deployment.__file__).parent / "app.py"), default_timeout=30)
    at.run()
    assert not at.exception
    assert len(at.error) == 1 and at.error[0].value.startswith("Production storage refused: PostgreSQL support needs")
    assert all(SECRET not in e.value for e in at.error)
    assert not at.button and not at.text_input
    assert not root.exists()


# --- Phase 3B ----------------------------------------------------------------------------------------------------------


def test_the_engine_storage_blocker_names_exactly_the_engines_without_a_postgresql_store():
    """Phase 3C: every engine store has a PostgreSQL backend, so no engine is named; the wording for any engine that
    lacked one is unchanged."""
    assert [engine for engine, backend in storage.ENGINE_BACKENDS.items() if backend is None] == []
    assert "Engine 1 records have no PostgreSQL store" in storage.engine_storage_blocker(["Engine 1"])
    assert "Engine 2" not in storage.engine_storage_blocker(["Engine 1"])
    assert "Engine 1 and Engine 2 records" in storage.engine_storage_blocker(["Engine 1", "Engine 2"])


def test_the_development_console_runs_on_the_sqlite_backend_and_never_imports_postgresql(tmp_path):
    import subprocess
    import sys

    script = ("import sys; from datetime import datetime, timezone; import os; "
              "os.environ['BAEC_DEPLOYMENT_MODE'] = 'development'; "
              "from baec_console.console_service import Console; from baec_console.backends import SQLiteBackend; "
              f"c = Console.local_development({str(tmp_path / 'data')!r}); "
              "c.local.create_account('Harbor Point Labs'); "
              "assert type(c._backend) is SQLiteBackend and c.local._backend is c._backend; "
              "assert not any(m.startswith(('baec_console.postgres', 'psycopg')) for m in sys.modules), "
              "[m for m in sys.modules if 'postgres' in m or 'psycopg' in m]")
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr


def test_the_sqlite_backend_keeps_the_development_files_and_reset_semantics(tmp_path, monkeypatch):
    from baec_console import demo_seed
    from baec_console.console_service import Console

    monkeypatch.setenv("BAEC_DEPLOYMENT_MODE", "development")
    console = Console.local_development(tmp_path / "data", StepClock())
    demo_seed.load(console, "beginning")
    assert len(console.synthetic.account_ids()) == 1
    console.reset_synthetic()
    assert console.synthetic.account_ids() == []
    names = sorted(p.name for p in (tmp_path / "data").rglob("*.sqlite3"))
    assert names == sorted(["identity.sqlite3"] + 2 * ["engine1.sqlite3", "engine2.sqlite3", "console.sqlite3",
                                                       "crm.sqlite3"])
