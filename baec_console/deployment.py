"""Deployment mode: the one place a running process decides how it may obtain the principal it acts as.

    development  the deterministic local-development principal (not authentication), for a local SQLite workflow;
    production   only the principal of an active HUMAN user whose sign-in (an OpenID Connect issuer and subject that
                 Streamlit verified) an operator has linked to that user, served entirely from PostgreSQL
                 (production.py, auth.py). It never falls back to the local-development principal, never accepts a
                 user id, organization id, or name from the caller or the browser, and never uses SQLite.

The mode is read from BAEC_DEPLOYMENT_MODE and must be set explicitly to exactly "development" or "production".
Unset, empty, or any other value is a configuration error: nothing falls back to development, so no local-development
principal, identity store, or workspace data is ever created or opened without an explicit development setting.

Secret boundary (V0.4 Phase 5; IMPLEMENTATION). The production web process may hold only its own credentials: the
application and sign-in lookup database URLs (each as a value, or as NAME_FILE naming a mounted secret file) and
Streamlit's sign-in secrets file. It refuses to start if an operator-only credential is present in its environment
(OPERATOR_ONLY_VARIABLES, by value or by file: migrations, provisioning, backup, and restore each run as a separate
process with only their own), or any ANTHROPIC_ variable (the Claude SDK reads its credential, endpoint, and logging
from those): a Claude credential is entered by each person and kept only in that browser session, so a server-wide
one, or a server-wide redirect or log of Claude traffic, must never exist, not even unused.
"""

from __future__ import annotations

import os
from enum import Enum
from pathlib import Path
from typing import Mapping

ENVIRONMENT_VARIABLE = "BAEC_DEPLOYMENT_MODE"


class DeploymentMode(Enum):
    DEVELOPMENT = "development"
    PRODUCTION = "production"


class DeploymentError(Exception):
    """The deployment configuration is invalid, or this mode does not allow what was asked."""


class ProductionAccessRefused(DeploymentError):
    """Production mode was asked for a console without a sign-in. Nothing is read or written."""


PRODUCTION_NEEDS_SIGN_IN = ("In production, BAEC Console opens only for a signed-in user whose sign-in is linked to "
                            "an active BAEC user. Nothing was read or changed.")


class DeploymentModeNotConfigured(DeploymentError):
    """BAEC_DEPLOYMENT_MODE is unset, empty, or not one of the supported values. Nothing was read or created."""


OPERATOR_ONLY_VARIABLES = ("BAEC_MIGRATION_DATABASE_URL", "BAEC_PROVISIONING_DATABASE_URL", "BAEC_BACKUP_DATABASE_URL",
                           "BAEC_RESTORE_DATABASE_URL")
SESSION_ONLY_PREFIXES = ("ANTHROPIC_",)


class SecretBoundaryRefused(DeploymentError):
    """The web process was given a credential it must never hold. `names` lists variable names, never values."""

    def __init__(self, names: list[str]) -> None:
        self.names = names
        super().__init__("BAEC Console is closed: this process was given credentials only a separate operator process "
                         f"may hold ({', '.join(names)}). Remove them from its environment. Nothing was read or "
                         "changed.")


def require_web_secret_boundary(environ: Mapping[str, str] | None = None) -> None:
    """Refuse a web process that holds an operator-only or server-wide Claude credential (module docstring)."""
    environ = os.environ if environ is None else environ
    names = sorted(name for name in environ
                   if name in OPERATOR_ONLY_VARIABLES or name.removesuffix("_FILE") in OPERATOR_ONLY_VARIABLES
                   or name.upper().startswith(SESSION_ONLY_PREFIXES))
    if names:
        raise SecretBoundaryRefused(names)


def configured_value(environ: Mapping[str, str], name: str) -> str | None:
    """The value of `name`, or the contents of the file NAME_FILE names (a mounted secret), or None. Setting both is
    refused, as is an unreadable file; neither message shows a value."""
    value, path = environ.get(name), environ.get(name + "_FILE")
    if value and path:
        raise DeploymentError(f"Set {name} or {name}_FILE, not both. Nothing was read or changed.")
    if not path:
        return value
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        raise DeploymentError(f"{name}_FILE cannot be read. Nothing was read or changed.") from None


def deployment_mode(environ: Mapping[str, str] | None = None) -> DeploymentMode:
    """The explicitly configured mode. Fails closed on an unset, empty, or unsupported value; never defaults."""
    value = (os.environ if environ is None else environ).get(ENVIRONMENT_VARIABLE)
    try:
        return DeploymentMode(value)
    except ValueError:
        shown = "unset" if value is None else repr(value)
        raise DeploymentModeNotConfigured(
            f"BAEC Console is not configured: set {ENVIRONMENT_VARIABLE} to 'development' or 'production' "
            f"(it is {shown}). Nothing was read or changed.") from None


def require_development(what: str, environ: Mapping[str, str] | None = None) -> None:
    """Refuse a development-only facility (such as the local-development principal) outside development mode."""
    if deployment_mode(environ) is not DeploymentMode.DEVELOPMENT:
        raise DeploymentError(f"{what} is available only in development mode")


def runtime_console(root: str | Path, *, mode: DeploymentMode, clock=None):
    """The development Console, or a refusal.

    development: the local-development console on SQLite; BAEC_DATABASE_URL must be unset.
    production: always ProductionAccessRefused. A production console is opened only by
    production.ProductionRuntime.console_for, for a verified sign-in; there is no way to ask for one by user id.
    Nothing under `root` is created or opened in production; there is no SQLite fallback.
    """
    from . import storage
    from .console_service import Console

    if mode is DeploymentMode.DEVELOPMENT:
        storage.require_development_storage()
        return Console.local_development(root, clock)  # which itself refuses outside development mode
    if mode is not DeploymentMode.PRODUCTION:
        raise DeploymentError(f"unknown deployment mode {mode!r}")
    raise ProductionAccessRefused(PRODUCTION_NEEDS_SIGN_IN)
