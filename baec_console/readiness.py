"""Readiness: can this process's configuration safely serve production requests? (V0.4 Phase 5; IMPLEMENTATION.)

    python -m baec_console.readiness             exit 0 and print "ready", or exit 1 and print "not ready: CODE"
    python -m baec_console.readiness --explain   the same, plus the refusal's own safe message on stderr (operators)

It runs exactly the production startup the web process runs (production.open_runtime), with this process's
environment and Streamlit's sign-in secrets: the secret boundary, connection bounds, verified TLS, migrations and
checksums, roles and privileges, row-level security, the Engine 1, Engine 2, product, and sign-in schemas, the
sign-in lookup role, the sign-in configuration, connection limits, and the pooled sessions' timeouts. Then it closes
everything it opened.

It writes nothing and reads no organization's records (catalog and role facts only, and the sign-in lookup role's
read-only probe, which must see no row). It needs no BAEC sign-in. Its output is one fixed code, never a URL, a
secret, or an exception; --explain adds only the refusal's own message, which by design names settings and schema
objects, never values. Liveness (the process answers at all) is separate: Streamlit's /_stcore/health, checked by
the image's HEALTHCHECK.
"""

from __future__ import annotations

import sys

from . import auth, deployment, log_redaction, production, storage


def _code(error: BaseException) -> str:
    from .postgres.database import DatabaseUnavailable

    if isinstance(error, deployment.DeploymentModeNotConfigured):
        return "MODE"
    if isinstance(error, deployment.SecretBoundaryRefused):
        return "SECRET_BOUNDARY"
    if isinstance(error, auth.AuthConfigurationError):
        return "SIGN_IN_CONFIGURATION"
    if isinstance(error, storage.StorageRefused):
        return "DATABASE_SCHEMA" if getattr(error, "failures", None) else "DATABASE"
    if isinstance(error, DatabaseUnavailable):
        return "DATABASE_UNAVAILABLE"
    if isinstance(error, deployment.DeploymentError):
        return "CONFIGURATION"
    return "INTERNAL"


def check(auth_section=None, environ=None) -> tuple[str | None, Exception | None]:
    """(None, None) when ready, else (refusal code, the refusal). `auth_section` defaults to Streamlit's [auth]
    secrets."""
    try:
        runtime = production.open_runtime(auth.streamlit_auth_section() if auth_section is None else auth_section,
                                          environ)
    except Exception as error:  # every refusal and every unexpected failure is "not ready"
        return _code(error), error
    runtime.close()
    return None, None


def main(argv: list[str] | None = None) -> int:
    log_redaction.install()
    argv = sys.argv[1:] if argv is None else argv
    code, error = check()
    if code is None:
        print("ready")
        return 0
    print(f"not ready: {code}")
    if "--explain" in argv and code != "INTERNAL":
        print(log_redaction.redact(str(error)), file=sys.stderr)
        for failure in getattr(error, "failures", None) or ():
            print(f"  - {log_redaction.redact(str(failure))}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
