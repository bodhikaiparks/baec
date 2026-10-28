"""Controlled provisioning of organizations, HUMAN users, and sign-in links. An operator tool, never the application.

    python -m baec_console.postgres.provision create-organization --organization ID --name NAME
    python -m baec_console.postgres.provision create-user --organization ID --user ID --name NAME
    python -m baec_console.postgres.provision link --organization ID --user ID --issuer URL --subject SUB --operator WHO
    python -m baec_console.postgres.provision revoke --organization ID --issuer URL --subject SUB --operator WHO
    python -m baec_console.postgres.provision deactivate --organization ID --user ID
    python -m baec_console.postgres.provision reactivate --organization ID --user ID

Reads BAEC_PROVISIONING_DATABASE_URL (or BAEC_PROVISIONING_DATABASE_URL_FILE, a mounted secret file; V0.4 Phase 5),
which must require verified TLS and connect as baec_provisioner (migration 0005): that role can write
organizations, users, their audit, and sign-in links, and nothing else, and only inside one organization's tenant
transaction, so every command names its organization. Nothing here signs anyone in.

Rules (IMPLEMENTATION; V0.4 Phase 4):
  * users created here are HUMAN; no other kind can be created, and no kind is ever changed;
  * a sign-in link names an existing, active HUMAN user of the named organization (also enforced by the database
    trigger, whatever tool writes the row); a user has at most one unrevoked link; a subject is linked once, ever;
  * revocation is permanent; deactivation is reversible and audited (identity_audit);
  * there is no self-registration, no invitation, no email-domain joining, and no organization created at sign-in.
  * `--operator` labels who performed a link or revocation. It is a record, not authentication.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone

from .. import identity
from ..deployment import DeploymentError, configured_value
from ..identity import IdentityError, UserKind
from . import PostgresError, require_driver
from .database import connect, tenant_transaction, validate_database_url

URL_VARIABLE = "BAEC_PROVISIONING_DATABASE_URL"
PROVISIONING_ROLE = "baec_provisioner"


class ProvisioningError(PostgresError):
    """A provisioning command was refused. Nothing was changed."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Provisioner:
    """Provisioning commands over one connection as the provisioning role. Each command is one tenant transaction."""

    def __init__(self, url: str | None) -> None:
        self.connection = connect(validate_database_url(url, name=URL_VARIABLE), name=URL_VARIABLE)
        if self.connection.execute("SELECT current_user").fetchone()[0] != PROVISIONING_ROLE:
            self.connection.close()
            raise ProvisioningError(f"provisioning runs only as {PROVISIONING_ROLE}")

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> Provisioner:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _store(self, connection):
        from .stores import PgIdentityStore

        return PgIdentityStore(connection)

    def _run(self, organization_id: str, action):
        psycopg = require_driver()
        try:
            with tenant_transaction(self.connection, organization_id) as connection:
                return action(connection, self._store(connection))
        except IdentityError as error:
            raise ProvisioningError(str(error)) from None
        except psycopg.Error as error:
            primary = error.diag.message_primary if getattr(error, "diag", None) else None
            raise ProvisioningError(primary or type(error).__name__) from None

    def create_organization(self, organization_id: str, name: str, *, at: datetime | None = None):
        def action(_connection, store):
            if store.organization(organization_id) is not None:
                raise IdentityError(f"organization {organization_id!r} already exists")
            return store.ensure_organization(organization_id, name, at or _now())
        return self._run(organization_id, action)

    def create_user(self, organization_id: str, user_id: str, display_name: str, *, at: datetime | None = None):
        """A HUMAN user of an existing organization. Refuses an id already in use (in any organization)."""
        def action(connection, store):
            if store.organization(organization_id) is None:
                raise IdentityError(f"unknown organization {organization_id!r}")
            if store.user(user_id) is not None:
                raise IdentityError(f"user {user_id!r} already exists")
            return store.ensure_user(user_id, organization_id, display_name, at or _now(), kind=UserKind.HUMAN)
        return self._run(organization_id, action)

    def link(self, organization_id: str, user_id: str, issuer: str, subject: str, operator: str, *,
             at: datetime | None = None) -> None:
        def action(connection, store):
            user = store.user(user_id)
            if user is None or user.organization_id != organization_id:
                raise IdentityError(f"unknown user {user_id!r} in organization {organization_id!r}")
            if not user.authentication_eligible:
                raise IdentityError("only an active HUMAN user can be linked to a sign-in identity")
            connection.execute(
                "INSERT INTO baec_auth.auth_identities (issuer, subject, organization_id, user_id, linked_at, "
                "linked_by) VALUES (%s, %s, %s, %s, %s, %s)",
                (issuer, subject, organization_id, user_id, identity._ts(at or _now()), operator))
        self._run(organization_id, action)

    def revoke(self, organization_id: str, issuer: str, subject: str, operator: str, *,
               at: datetime | None = None) -> None:
        def action(connection, _store):
            changed = connection.execute(
                "UPDATE baec_auth.auth_identities SET revoked_at = %s, revoked_by = %s "
                "WHERE issuer = %s AND subject = %s AND revoked_at IS NULL",
                (identity._ts(at or _now()), operator, issuer, subject)).rowcount
            if changed != 1:
                raise IdentityError("no unrevoked sign-in link for that subject in this organization")
        self._run(organization_id, action)

    def set_active(self, organization_id: str, user_id: str, active: bool, *, at: datetime | None = None) -> None:
        def action(_connection, store):
            user = store.user(user_id)
            if user is None or user.organization_id != organization_id:
                raise IdentityError(f"unknown user {user_id!r} in organization {organization_id!r}")
            store.set_active(user_id, active, at=at or _now())
        self._run(organization_id, action)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m baec_console.postgres.provision",
                                     description="Provision BAEC organizations, HUMAN users, and sign-in links.")
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("create-organization")
    command.add_argument("--organization", required=True)
    command.add_argument("--name", required=True)
    command = commands.add_parser("create-user")
    command.add_argument("--organization", required=True)
    command.add_argument("--user", required=True)
    command.add_argument("--name", required=True)
    command = commands.add_parser("link")
    for option in ("--organization", "--user", "--issuer", "--subject", "--operator"):
        command.add_argument(option, required=True)
    command = commands.add_parser("revoke")
    for option in ("--organization", "--issuer", "--subject", "--operator"):
        command.add_argument(option, required=True)
    for name in ("deactivate", "reactivate"):
        command = commands.add_parser(name)
        command.add_argument("--organization", required=True)
        command.add_argument("--user", required=True)
    return parser


def main(argv: list[str] | None = None, environ=None) -> int:
    arguments = _parser().parse_args(argv)
    environ = os.environ if environ is None else environ
    try:
        url = configured_value(environ, URL_VARIABLE)
    except DeploymentError as error:
        print(f"provisioning refused: {error}", file=sys.stderr)
        return 1
    try:
        with Provisioner(url) as provisioner:
            if arguments.command == "create-organization":
                provisioner.create_organization(arguments.organization, arguments.name)
            elif arguments.command == "create-user":
                provisioner.create_user(arguments.organization, arguments.user, arguments.name)
            elif arguments.command == "link":
                provisioner.link(arguments.organization, arguments.user, arguments.issuer, arguments.subject,
                                 arguments.operator)
            elif arguments.command == "revoke":
                provisioner.revoke(arguments.organization, arguments.issuer, arguments.subject, arguments.operator)
            else:
                provisioner.set_active(arguments.organization, arguments.user, arguments.command == "reactivate")
    except PostgresError as error:
        print(f"provisioning refused: {error}", file=sys.stderr)
        return 1
    print(f"{arguments.command}: done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
