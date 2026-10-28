"""The production console: a signed-in HUMAN user, served entirely from PostgreSQL. (V0.4 Phase 4; IMPLEMENTATION.)

Startup (open_runtime), in order, each refusing with a fixed message and opening nothing on failure:
  1. BAEC_DEPLOYMENT_MODE is exactly "production";
  2. the secret boundary (deployment.require_web_secret_boundary): no migration, provisioning, backup, or restore
     credential and no server-wide Claude credential in this process (V0.4 Phase 5);
  3. the connection bounds and timeouts (postgres.database.database_settings) are valid (V0.4 Phase 5);
  4. the database gate (storage.require_production_storage): configuration, verified TLS, and connectivity,
     migrations and their checksums, the application role, row-level security, the Engine 2, Engine 1, and product
     schemas, then the sign-in schema and the sign-in lookup role;
  5. the sign-in configuration (auth.require_configuration): one OpenID Connect provider, issuer BAEC_AUTH_ISSUER;
  6. each role's database-side connection limit admits this process's pool (V0.4 Phase 5);
  7. the pools open, and one session from each must use TLS and carry the configured timeouts (V0.4 Phase 5).
The app may show the sign-in page once startup has passed; nothing else is shown before a principal exists.

Per browser session (ProductionRuntime.console_for), after Streamlit has verified the provider session and
auth.identity_from_claims has accepted it (configured issuer, subject present, session age):
  1. the exact (issuer, subject) resolves, as baec_auth_lookup, to one unrevoked link: (user_id, organization_id);
  2. in that organization's tenant transaction, as baec_app, IdentityStore.authenticated_principal reads the user:
     it must exist in that organization, be active, and be HUMAN. The principal's user_id, organization_id, and
     display name come from there, never from the provider or the browser;
  3. the Console runs on PostgresBackend with authentication_required and a SignedInCredential, so before every
     operation its guard repeats the age check, the link lookup (same user and organization, unrevoked), and the user
     check. Revoking the link, deactivating the user, or the session growing too old refuses the next operation in the
     running process, without sign-out or restart.
Every refusal says the same thing (auth.NO_ACCESS), so it does not reveal whether a subject is linked.

Production never opens SQLite: there is no data directory, and PRODUCTION_ROOT only names workspaces inside the
service. The local-development principal, the synthetic-seed user, public-demo visitors, and unclassified users are
never HUMAN, so none of them can become a production principal even if a link to one existed.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Mapping

from baec_app.application.context import Clock, SystemClock

from . import auth, deployment, storage
from .auth import NO_ACCESS, AuthenticatedIdentity, SignInRefused
from .backends import PostgresBackend
from .console_service import Console, ConsoleError
from .identity import IdentityError, Principal

PRODUCTION_ROOT = Path("/baec-production-has-no-data-directory")


class SignedInCredential:
    """The sign-in a production console was opened for. check() runs before every operation (console_service guard)."""

    def __init__(self, lookup, identity: AuthenticatedIdentity, user_id: str, organization_id: str,
                 clock: Clock) -> None:
        self._lookup = lookup
        self.identity = identity
        self.user_id = user_id
        self.organization_id = organization_id
        self._clock = clock

    def check(self, principal: Principal) -> None:
        self.identity.check_age(self._clock.now())
        linked = self._lookup.resolve(*self.identity.key)
        if linked is None or (linked.user_id, linked.organization_id) != (self.user_id, self.organization_id) \
                or (principal.user_id, principal.organization_id) != (self.user_id, self.organization_id):
            raise SignInRefused(NO_ACCESS)


class ProductionRuntime:
    """One process's production storage and sign-in lookup. Holds no user; every console it opens is for one sign-in."""

    def __init__(self, database, lookup, issuer: str, clock: Clock | None = None) -> None:
        from .postgres.stores import PostgresProductStorage

        self.database = database  # postgres.database.TenantDatabase, as baec_app
        self.lookup = lookup  # postgres.auth_lookup.AuthLookup, as baec_auth_lookup
        self.issuer = issuer
        self.clock = clock or SystemClock()
        self.backend = PostgresBackend(PostgresProductStorage(database))

    def console_for(self, identity: AuthenticatedIdentity) -> Console:
        """The console of the BAEC user this sign-in is linked to, or SignInRefused (auth.NO_ACCESS or
        auth.SESSION_EXPIRED). Reads nothing else first."""
        if not isinstance(identity, AuthenticatedIdentity) or identity.issuer != self.issuer:
            raise SignInRefused(NO_ACCESS)
        identity.check_age(self.clock.now())
        linked = self.lookup.resolve(*identity.key)
        if linked is None:
            raise SignInRefused(NO_ACCESS)
        try:
            with self.backend.identity(linked.organization_id) as store:
                principal = store.authenticated_principal(linked.user_id)
            if principal.organization_id != linked.organization_id:
                raise SignInRefused(NO_ACCESS)
            credential = SignedInCredential(self.lookup, identity, principal.user_id, principal.organization_id,
                                            self.clock)
            return Console(PRODUCTION_ROOT, self.clock, principal=principal, authentication_required=True,
                           backend=self.backend, credential=credential)
        except SignInRefused:
            raise
        except (IdentityError, ConsoleError):
            raise SignInRefused(NO_ACCESS) from None

    def close(self) -> None:
        try:
            self.lookup.close()
        finally:
            self.database.close()


def open_runtime(auth_section: Mapping | None, environ: Mapping[str, str] | None = None,
                 clock: Clock | None = None) -> ProductionRuntime:
    """Check everything production needs, then open its connection pools. `auth_section` is Streamlit's [auth]
    secrets section (auth.streamlit_auth_section). Raises a DeploymentError subclass; opens nothing on failure."""
    from .postgres import PostgresError
    from .postgres.auth_lookup import AuthLookup
    from .postgres.database import TenantDatabase, available, database_settings, session_failures

    environ = os.environ if environ is None else environ
    if deployment.deployment_mode(environ) is not deployment.DeploymentMode.PRODUCTION:
        raise deployment.DeploymentError("the production console runs only in production mode")
    deployment.require_web_secret_boundary(environ)
    try:
        settings = database_settings(environ)
    except PostgresError as error:
        raise storage.StorageRefused(f"Production storage refused: {error}. Nothing was read or changed.") from None
    report = storage.require_production_storage(environ, settings)
    issuer = auth.require_configuration(auth_section, environ)
    limits = (("application", report.connection_limits[0], settings.max_size, "BAEC_DB_POOL_MAX_SIZE"),
              ("sign-in lookup", report.connection_limits[1], settings.auth_max_size, "BAEC_AUTH_DB_POOL_MAX_SIZE"))
    for label, limit, size, variable in limits:
        if limit < size:
            raise storage.StorageRefused(f"Production storage refused: the {label} role's connection limit ({limit}) "
                                         f"is below {variable} ({size}). Nothing was read or changed.")
    try:
        database = TenantDatabase(deployment.configured_value(environ, storage.DATABASE_URL_VARIABLE),
                                  settings=settings)
        try:
            lookup = AuthLookup(deployment.configured_value(environ, storage.AUTH_DATABASE_URL_VARIABLE),
                                settings=settings)
            try:
                failures = []
                for label, pool in (("application", database.pool), ("sign-in lookup", lookup.pool)):
                    with available(pool) as connection:
                        failures += session_failures(connection, settings, label)
                if failures:
                    raise storage.StorageRefused(f"Production storage refused: {'; '.join(failures)}. "
                                                 f"Nothing was read or changed.")
            except BaseException:
                lookup.close()
                raise
        except BaseException:
            database.close()
            raise
    except PostgresError as error:
        raise storage.StorageRefused(f"Production storage refused: {error}. Nothing was read or changed.") from None
    return ProductionRuntime(database, lookup, issuer, clock)


_PROCESS: ProductionRuntime | None = None
_PROCESS_LOCK = threading.Lock()


def process_runtime(auth_section: Mapping | None) -> ProductionRuntime:
    """This process's runtime, opened on first successful use. A refusal is not remembered: the next request checks
    again. The runtime holds no user or session, so sharing it between sessions shares nothing a user can see."""
    global _PROCESS
    with _PROCESS_LOCK:
        if _PROCESS is None:
            _PROCESS = open_runtime(auth_section)
        return _PROCESS


def close_process_runtime() -> None:
    global _PROCESS
    with _PROCESS_LOCK:
        if _PROCESS is not None:
            _PROCESS.close()
            _PROCESS = None
