"""V0.4 Phase 5: operational safety that needs no database or network. Verified-TLS URL policy, connection bounds,
the web process's secret boundary, secrets from files, log redaction, readiness codes, and the app's fixed message
when the database is unavailable. All values are synthetic; software tests, not a security assessment."""

from __future__ import annotations

import io
import logging
from pathlib import Path

import pytest

from baec_console import auth, deployment, log_redaction, production, readiness, storage
from baec_console.postgres.database import (
    UNAVAILABLE,
    DatabaseConfigError,
    DatabaseSettings,
    DatabaseUnavailable,
    database_settings,
    require_verified_tls,
)

SECRET = "s3cr3t-test-password"
BASE = f"postgresql://baec_app:{SECRET}@db.baec-test.invalid:5432/baec"
VERIFIED = BASE + "?sslmode=verify-full&sslrootcert=/run/secrets/baec_postgres_ca.crt"


# --- verified TLS ------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("url", [VERIFIED, BASE + "?sslmode=verify-full", BASE + "?sslrootcert=system&sslmode=verify-full"])
def test_a_url_that_requires_verified_tls_is_accepted(url):
    assert require_verified_tls(url) == url


@pytest.mark.parametrize("query", ["", "?sslmode=disable", "?sslmode=allow", "?sslmode=prefer", "?sslmode=require",
                                   "?sslmode=verify-ca", "?sslmode=VERIFY-FULL", "?sslmode=",
                                   "?sslmode=verify-full&sslmode=disable", "?sslmode=disable&sslmode=verify-full"])
def test_any_url_without_exactly_verify_full_is_refused_without_echoing_it(query):
    with pytest.raises(DatabaseConfigError, match="must use a verified TLS connection") as refused:
        require_verified_tls(BASE + query, name="BAEC_AUTH_DATABASE_URL")
    assert "BAEC_AUTH_DATABASE_URL" in str(refused.value) and SECRET not in str(refused.value)


def test_production_refuses_a_plaintext_url_before_any_connection(monkeypatch):
    """No driver or network here: the policy is decided from the URL alone, for each URL."""
    monkeypatch.setenv("BAEC_DEPLOYMENT_MODE", "production")
    for plaintext in ("BAEC_DATABASE_URL", "BAEC_AUTH_DATABASE_URL"):
        monkeypatch.setenv("BAEC_DATABASE_URL", VERIFIED)
        monkeypatch.setenv("BAEC_AUTH_DATABASE_URL", VERIFIED)
        monkeypatch.setenv(plaintext, BASE + "?sslmode=require")  # encrypted, but the server is not verified
        with pytest.raises(storage.StorageRefused, match=f"{plaintext} must use a verified TLS") as refused:
            production.open_runtime(None)
        assert SECRET not in str(refused.value)


# --- connection bounds and timeouts ----------------------------------------------------------------------------------


def test_default_bounds_and_timeouts():
    settings = database_settings({})
    assert settings == DatabaseSettings()
    assert (settings.max_size, settings.auth_max_size, settings.pool_timeout, settings.max_waiting) == (4, 2, 10, 64)
    assert (settings.statement_timeout_ms, settings.lock_timeout_ms, settings.idle_in_transaction_timeout_ms) == (
        30_000, 5_000, 60_000)
    assert settings.options == ("-c statement_timeout=30000 -c lock_timeout=5000 "
                                "-c idle_in_transaction_session_timeout=60000")
    assert settings.kwargs()["options"] == settings.options and settings.kwargs()["prepare_threshold"] is None


def test_bounds_and_timeouts_can_be_configured_within_their_ranges():
    settings = database_settings({"BAEC_DB_POOL_MAX_SIZE": "8", "BAEC_AUTH_DB_POOL_MAX_SIZE": "3",
                                  "BAEC_DB_POOL_TIMEOUT_SECONDS": "5", "BAEC_DB_STATEMENT_TIMEOUT_MS": "20000",
                                  "BAEC_DB_LOCK_TIMEOUT_MS": "2000", "BAEC_DB_IDLE_IN_TRANSACTION_TIMEOUT_MS": "30000"})
    assert (settings.max_size, settings.auth_max_size, settings.pool_timeout) == (8, 3, 5)
    assert (settings.statement_timeout_ms, settings.lock_timeout_ms, settings.idle_in_transaction_timeout_ms) == (
        20_000, 2_000, 30_000)


@pytest.mark.parametrize("name,value", [("BAEC_DB_POOL_MAX_SIZE", "0"), ("BAEC_DB_POOL_MAX_SIZE", "33"),
                                        ("BAEC_DB_POOL_MAX_SIZE", "-1"), ("BAEC_DB_POOL_MAX_SIZE", "four"),
                                        ("BAEC_DB_POOL_MAX_SIZE", "2.5"), ("BAEC_AUTH_DB_POOL_MAX_SIZE", "9"),
                                        ("BAEC_DB_POOL_TIMEOUT_SECONDS", "0"), ("BAEC_DB_POOL_TIMEOUT_SECONDS", "61"),
                                        ("BAEC_DB_STATEMENT_TIMEOUT_MS", "0"),
                                        ("BAEC_DB_STATEMENT_TIMEOUT_MS", "300001"),
                                        ("BAEC_DB_LOCK_TIMEOUT_MS", "99"),
                                        ("BAEC_DB_IDLE_IN_TRANSACTION_TIMEOUT_MS", "999")])
def test_an_unbounded_or_invalid_setting_is_refused_by_name(name, value):
    with pytest.raises(DatabaseConfigError, match=name):
        database_settings({name: value})


def test_a_lock_timeout_must_be_shorter_than_the_statement_timeout():
    with pytest.raises(DatabaseConfigError, match="LOCK_TIMEOUT_MS must be shorter"):
        database_settings({"BAEC_DB_STATEMENT_TIMEOUT_MS": "5000", "BAEC_DB_LOCK_TIMEOUT_MS": "5000"})


def test_production_refuses_invalid_bounds_before_contacting_the_database(monkeypatch):
    monkeypatch.setenv("BAEC_DEPLOYMENT_MODE", "production")
    monkeypatch.setenv("BAEC_DATABASE_URL", VERIFIED)
    monkeypatch.setenv("BAEC_AUTH_DATABASE_URL", VERIFIED)
    monkeypatch.setenv("BAEC_DB_POOL_MAX_SIZE", "1000")
    with pytest.raises(storage.StorageRefused, match="BAEC_DB_POOL_MAX_SIZE must be a whole number"):
        production.open_runtime(None)


# --- the web process's secret boundary -------------------------------------------------------------------------------


OPERATOR_ONLY = ["BAEC_MIGRATION_DATABASE_URL", "BAEC_PROVISIONING_DATABASE_URL", "BAEC_BACKUP_DATABASE_URL",
                 "BAEC_RESTORE_DATABASE_URL"]
# The Claude SDK's own environment variables: a server-wide credential, endpoint, or traffic log. Names built here so
# no credential-shaped name is written into a console file.
CLAUDE_SDK = ["ANTHROPIC_" + "API" + "_KEY", "ANTHROPIC_AUTH_" + "TOKEN", "ANTHROPIC_BASE_URL", "ANTHROPIC_LOG"]


@pytest.mark.parametrize("name", OPERATOR_ONLY + [n + "_FILE" for n in OPERATOR_ONLY] + CLAUDE_SDK)
def test_the_web_process_refuses_a_credential_it_must_never_hold(monkeypatch, name):
    monkeypatch.setenv("BAEC_DEPLOYMENT_MODE", "production")
    monkeypatch.setenv(name, VERIFIED)
    with pytest.raises(deployment.SecretBoundaryRefused) as refused:
        production.open_runtime(None)  # refused before any URL is read or any connection is attempted
    assert refused.value.names == [name]
    assert name in str(refused.value) and SECRET not in str(refused.value) and VERIFIED not in str(refused.value)


def test_the_web_process_accepts_its_own_credentials_only():
    deployment.require_web_secret_boundary({"BAEC_DEPLOYMENT_MODE": "production", "BAEC_DATABASE_URL": VERIFIED,
                                            "BAEC_AUTH_DATABASE_URL_FILE": "/run/secrets/x", "BAEC_AI_MODEL": "m",
                                            "CLAUDE_CODE_SESSION_ID": "unrelated tool variable"})
    with pytest.raises(deployment.SecretBoundaryRefused) as refused:
        deployment.require_web_secret_boundary({"BAEC_PROVISIONING_DATABASE_URL_FILE": "/run/secrets/p",
                                                "BAEC_MIGRATION_DATABASE_URL": VERIFIED, "anthropic_base_url": "x"})
    assert refused.value.names == ["BAEC_MIGRATION_DATABASE_URL", "BAEC_PROVISIONING_DATABASE_URL_FILE",
                                   "anthropic_base_url"]


# --- secrets from mounted files ----------------------------------------------------------------------------------------


def test_a_database_url_can_come_from_a_mounted_secret_file(tmp_path):
    path = tmp_path / "baec_app_database_url"
    path.write_text(VERIFIED + "\n", encoding="utf-8")
    assert deployment.configured_value({"BAEC_DATABASE_URL_FILE": str(path)}, "BAEC_DATABASE_URL") == VERIFIED
    assert deployment.configured_value({"BAEC_DATABASE_URL": VERIFIED}, "BAEC_DATABASE_URL") == VERIFIED
    assert deployment.configured_value({}, "BAEC_DATABASE_URL") is None


def test_a_secret_given_twice_or_unreadable_is_refused_without_showing_it(tmp_path):
    path = tmp_path / "url"
    path.write_text(VERIFIED, encoding="utf-8")
    with pytest.raises(deployment.DeploymentError, match="not both") as refused:
        deployment.configured_value({"BAEC_DATABASE_URL": VERIFIED, "BAEC_DATABASE_URL_FILE": str(path)},
                                    "BAEC_DATABASE_URL")
    assert SECRET not in str(refused.value)
    with pytest.raises(deployment.DeploymentError, match="BAEC_DATABASE_URL_FILE cannot be read") as refused:
        deployment.configured_value({"BAEC_DATABASE_URL_FILE": str(tmp_path / "missing")}, "BAEC_DATABASE_URL")
    assert str(tmp_path) not in str(refused.value)


def test_production_reads_its_urls_from_secret_files(tmp_path, monkeypatch):
    """The file's content is what the policy sees: a plaintext URL in the file is refused like a plaintext value."""
    (tmp_path / "app").write_text(BASE + "?sslmode=disable", encoding="utf-8")
    (tmp_path / "auth").write_text(VERIFIED, encoding="utf-8")
    monkeypatch.setenv("BAEC_DEPLOYMENT_MODE", "production")
    monkeypatch.delenv("BAEC_DATABASE_URL", raising=False)
    monkeypatch.setenv("BAEC_DATABASE_URL_FILE", str(tmp_path / "app"))
    monkeypatch.setenv("BAEC_AUTH_DATABASE_URL_FILE", str(tmp_path / "auth"))
    with pytest.raises(storage.StorageRefused, match="BAEC_DATABASE_URL must use a verified TLS") as refused:
        production.open_runtime(None)
    assert SECRET not in str(refused.value)


# --- log redaction ------------------------------------------------------------------------------------------------------


JWT = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJzeW50aGV0aWMifQ.c2lnbmF0dXJlLXN5bnRoZXRpYw"
CLAUDE_STYLE = "sk-" + "ant" + "-api03-" + "x" * 40  # assembled: no key-shaped literal in a console file


@pytest.mark.parametrize("text,leak", [
    (f"connecting to {VERIFIED}", SECRET),
    (f"host=db port=5432 password={SECRET} sslmode=verify-full", SECRET),
    (f'client_secret = "{SECRET}"', SECRET),
    (f"cookie_secret: {SECRET}", SECRET),
    ("GET /oauth2callback?code=AUTHCODE123&state=STATE456 200", "AUTHCODE123"),
    ("GET /oauth2callback?code=AUTHCODE123&state=STATE456 200", "STATE456"),
    (f"Authorization: Bearer {JWT}", JWT),
    (f"id_token={JWT}", JWT),
    (f"token payload {JWT} rejected", JWT),
    ("Cookie: _streamlit_user=c2lnbmVk.dmFsdWU; _streamlit_xsrf=2|abc|def", "c2lnbmVk.dmFsdWU"),
    ("Cookie: _streamlit_user_tokens_1=chunkvalue", "chunkvalue"),
    (f"Claude request failed for key {CLAUDE_STYLE}", CLAUDE_STYLE),
])
def test_redaction_removes_credentials_and_keeps_the_rest(text, leak):
    redacted = log_redaction.redact(text)
    assert leak not in redacted and log_redaction.REDACTED in redacted


def test_redaction_leaves_ordinary_operational_text_alone():
    for text in ("account state=CONDITIONALLY_DORMANT changed", "pool max_size=4 timeout=10",
                 "migration 5 applied", "GET /_stcore/health 200"):
        assert log_redaction.redact(text) == text


@pytest.fixture
def redacting(monkeypatch):
    """install() for this test only: the log-record factory is restored afterwards."""
    original = logging.getLogRecordFactory()
    monkeypatch.setattr(log_redaction, "_INSTALLED", False)
    log_redaction.install()
    log_redaction.install()  # idempotent
    yield
    logging.setLogRecordFactory(original)


def test_every_log_record_is_redacted_after_install_whatever_logger_writes_it(redacting):
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(name)s %(message)s"))
    for name in ("psycopg.pool", "streamlit.web.server", "authlib", "a.logger.created.later"):
        logger = logging.getLogger(name)
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        try:
            logger.warning("error connecting to %s", VERIFIED)  # the secret arrives as an argument
            try:
                raise RuntimeError(f"token exchange failed: client_secret={SECRET}")
            except RuntimeError:
                logger.exception("callback failed for /oauth2callback?code=AUTHCODE123")
        finally:
            logger.removeHandler(handler)
    output = stream.getvalue()
    assert SECRET not in output and "AUTHCODE123" not in output
    assert output.count("[redacted]") >= 12 and "RuntimeError" in output  # the traceback is kept, redacted


# --- readiness ------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("error,code", [
    (deployment.DeploymentModeNotConfigured("x"), "MODE"),
    (deployment.SecretBoundaryRefused(["BAEC_PROVISIONING_DATABASE_URL"]), "SECRET_BOUNDARY"),
    (auth.AuthConfigurationError(["[auth] client_secret is missing"]), "SIGN_IN_CONFIGURATION"),
    (storage.StorageRefused("Production storage refused: x"), "DATABASE"),
    (DatabaseUnavailable("statement timeout"), "DATABASE_UNAVAILABLE"),
    (deployment.DeploymentError("BAEC_DATABASE_URL_FILE cannot be read"), "CONFIGURATION"),
    (RuntimeError(f"unexpected {SECRET}"), "INTERNAL"),
])
def test_readiness_reports_one_fixed_code_per_refusal(error, code):
    assert readiness._code(error) == code


def test_a_schema_refusal_is_reported_as_a_schema_code():
    refused = storage.StorageRefused("Production storage schema does not match")
    refused.failures = ["Engine 1 trigger x is missing"]
    assert readiness._code(refused) == "DATABASE_SCHEMA"


def test_readiness_prints_only_a_code_and_exits_nonzero(monkeypatch, capsys):
    monkeypatch.setenv("BAEC_DEPLOYMENT_MODE", "production")
    monkeypatch.setenv("BAEC_DATABASE_URL", BASE)  # plaintext: refused from the URL alone
    monkeypatch.setenv("BAEC_AUTH_DATABASE_URL", VERIFIED)
    monkeypatch.setattr(auth, "streamlit_auth_section", lambda: None)
    monkeypatch.setattr(log_redaction, "install", lambda: None)  # process-wide; covered above
    assert readiness.main([]) == 1
    out = capsys.readouterr()
    assert out.out == "not ready: DATABASE\n" and out.err == ""
    assert readiness.main(["--explain"]) == 1
    out = capsys.readouterr()
    assert out.out == "not ready: DATABASE\n" and "BAEC_DATABASE_URL must use a verified TLS" in out.err
    assert SECRET not in out.out + out.err


def test_readiness_refuses_development_mode(monkeypatch, capsys):
    monkeypatch.setattr(auth, "streamlit_auth_section", lambda: None)
    assert readiness.check()[0] == "CONFIGURATION"  # development is never "ready" for production
    monkeypatch.delenv("BAEC_DEPLOYMENT_MODE")
    assert readiness.check()[0] == "MODE"


# --- the app when the database cannot serve a request ---------------------------------------------------------------


def test_the_app_shows_one_fixed_message_when_the_database_is_unavailable(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest

    def unavailable(section):
        raise DatabaseUnavailable("no connection became free in time (pool exhausted)")
    monkeypatch.setattr(production, "process_runtime", unavailable)
    monkeypatch.setattr(log_redaction, "install", lambda: None)
    monkeypatch.setenv("BAEC_DEPLOYMENT_MODE", "production")
    monkeypatch.setenv("BAEC_CONSOLE_DATA_DIR", str(tmp_path / "data"))
    at = AppTest.from_file(str(Path(deployment.__file__).parent / "app.py"), default_timeout=30)
    at.run()
    assert not at.exception  # no traceback reaches the page
    assert [e.value for e in at.error] == [UNAVAILABLE]
    assert "pool exhausted" not in UNAVAILABLE and not at.text_input
    assert not (tmp_path / "data").exists()
