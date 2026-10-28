"""The sign-in lookup: one exact (issuer, subject) -> (user, organization), as the narrow role baec_auth_lookup.

At sign-in the organization is not known yet, so tenant row-level security cannot be used to find the user. This is
the one read that happens before it: on its own connection pool, as its own database role (BAEC_AUTH_DATABASE_URL),
which can read five columns of baec_auth.auth_identities and nothing else, and only the row whose issuer and subject
equal the transaction's baec.auth_issuer and baec.auth_subject settings (migration 0005). The query also names the
issuer and subject, so the application asks for exactly one link even if a policy were wrong, and the gate refuses a
role or policy that differs (gate.auth_lookup_failures).

The result names a user and an organization; it is not a principal. The principal is read afterwards as the
application role inside that organization's tenant transaction, which checks that the user exists there, is active,
and is a HUMAN user (production.py). A revoked link resolves to nothing.

Database URLs carry credentials. Nothing here prints, logs, or puts one in an error message.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import PostgresError
from .database import DatabaseSettings, available, open_pool

ISSUER_SETTING = "baec.auth_issuer"
SUBJECT_SETTING = "baec.auth_subject"


class AuthLookupError(PostgresError):
    """The sign-in lookup returned something the schema forbids. Fails closed."""


@dataclass(frozen=True)
class LinkedUser:
    """The BAEC user an unrevoked sign-in link names. Not a principal: production.py verifies it."""

    user_id: str
    organization_id: str


def _usable(value) -> bool:
    return isinstance(value, str) and bool(value) and "\x00" not in value


class AuthLookup:
    """A small pool of sign-in lookup connections (verified TLS, bounded, with the same timeouts as the application
    pool: database.open_pool). Pool exhaustion or a timeout is DatabaseUnavailable, never a sign-in result."""

    def __init__(self, url: str, *, max_size: int | None = None, settings: DatabaseSettings | None = None) -> None:
        settings = settings or DatabaseSettings()
        self.pool = open_pool(url, name="BAEC_AUTH_DATABASE_URL", min_size=1,
                              max_size=settings.auth_max_size if max_size is None else max_size, settings=settings)

    def resolve(self, issuer: str, subject: str) -> LinkedUser | None:
        """The user an unrevoked link of exactly (issuer, subject) names, or None. Never a list, never a partial
        match."""
        if not (_usable(issuer) and _usable(subject)):
            return None
        with available(self.pool) as connection, connection.transaction():
            connection.execute("SET TRANSACTION READ ONLY")
            connection.execute("SELECT pg_catalog.set_config(%s, %s, true), pg_catalog.set_config(%s, %s, true)",
                               (ISSUER_SETTING, issuer, SUBJECT_SETTING, subject))
            rows = connection.execute(
                "SELECT user_id, organization_id, revoked_at FROM baec_auth.auth_identities "
                "WHERE issuer = %s AND subject = %s", (issuer, subject)).fetchall()
        if len(rows) > 1:
            raise AuthLookupError("more than one sign-in link for one subject")
        if not rows or rows[0][2] is not None:
            return None
        return LinkedUser(rows[0][0], rows[0][1])

    def close(self) -> None:
        self.pool.close()

    def __enter__(self) -> AuthLookup:
        return self

    def __exit__(self, *exc) -> None:
        self.close()
