"""V0.4 Phase 4: production sign-in to a BAEC principal, on PostgreSQL, against attacks.

Two synthetic organizations, each with one HUMAN user linked to one synthetic OpenID Connect subject, plus users of
every other kind. Identities enter exactly as in production: claims as Streamlit holds them after verifying an ID
token (auth.identity_from_claims), then production.ProductionRuntime.console_for. The database checks connect as the
real roles (baec_app, baec_auth_lookup, baec_provisioner); tampering that no role can do is done by the test superuser.
These are software tests on synthetic data, not a security assessment.
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

import pytest

from baec_console import auth, production
from baec_console.console_service import AccessDenied, Console, ConsoleError, PrincipalInvalid
from baec_console.identity import IdentityError, Principal, UserKind
from baec_console.postgres import migrate, provision
from baec_console.postgres.auth_lookup import AuthLookup, LinkedUser
from baec_console.postgres.gate import AUTH_SCHEMA_MISMATCH, DatabaseRefused, check_database

from .conftest import TEST_ISSUER, auth_section, fresh, production_environ, provision as provision_user, psycopg
from .test_migrations_and_gate import scratch_database  # noqa: F401

OTHER_ISSUER = "https://other-issuer.baec-test.invalid"
NON_HUMAN = [k for k in UserKind if k is not UserKind.HUMAN]


class Clock:
    """A settable clock (timezone-aware), shared by the runtime and the claims a test makes."""

    def __init__(self) -> None:
        self.at = datetime.now(timezone.utc).replace(microsecond=0)
        self._lock = threading.Lock()

    def now(self) -> datetime:
        with self._lock:
            self.at += timedelta(milliseconds=1)
            return self.at


@pytest.fixture(scope="module")
def lookup(migrated):
    with AuthLookup(migrated["auth"]) as opened:
        yield opened


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def runtime(database, lookup, clock):
    """The production runtime over the shared test pools (closed by their own fixtures)."""
    return production.ProductionRuntime(database, lookup, TEST_ISSUER, clock)


@pytest.fixture
def provisioner(migrated):
    with provision.Provisioner(migrated["provisioner"]) as opened:
        yield opened


class World:
    """Organization A with user A (subject A), organization B with user B (subject B). Fresh ids per test."""

    def __init__(self, provisioner: provision.Provisioner) -> None:
        self.provisioner = provisioner
        self.org_a, self.org_b = fresh("org-a"), fresh("org-b")
        self.user_a, self.user_b = fresh("user-a"), fresh("user-b")
        self.subject_a, self.subject_b = fresh("subject-a"), fresh("subject-b")
        provisioner.create_organization(self.org_a, "Organization A (synthetic)")
        provisioner.create_organization(self.org_b, "Organization B (synthetic)")
        provisioner.create_user(self.org_a, self.user_a, "Avery (synthetic)")
        provisioner.create_user(self.org_b, self.user_b, "Blake (synthetic)")
        provisioner.link(self.org_a, self.user_a, TEST_ISSUER, self.subject_a, "operator (synthetic)")
        provisioner.link(self.org_b, self.user_b, TEST_ISSUER, self.subject_b, "operator (synthetic)")


@pytest.fixture
def world(provisioner) -> World:
    return World(provisioner)


def claims(subject: str, clock: Clock, **overrides) -> dict:
    base = {"is_logged_in": True, "provider": "default", "iss": TEST_ISSUER, "sub": subject, "aud": "baec-test-client",
            "iat": int(clock.at.timestamp()) - 60, "email": f"{subject}@example.invalid", "email_verified": True,
            "name": "Name from the provider (synthetic)"}
    base.update(overrides)
    return base


def sign_in(runtime, clock: Clock, subject: str, **overrides) -> Console:
    signed_in = auth.identity_from_claims(claims(subject, clock, **overrides), issuer=TEST_ISSUER, now=clock.now())
    return runtime.console_for(signed_in)


def refused(runtime, clock: Clock, subject: str, **overrides) -> str:
    with pytest.raises(auth.SignInRefused) as error:
        sign_in(runtime, clock, subject, **overrides)
    return str(error.value)


def superuser(urls: dict, *statements, replica: bool = False) -> None:
    """Tampering no BAEC role can do: as the test superuser, optionally with triggers and foreign keys off."""
    with psycopg.connect(urls["admin"], autocommit=True) as admin, admin.transaction():
        if replica:
            admin.execute("SET LOCAL session_replication_role = replica")
        for statement, args in statements:
            admin.execute(statement, args)


# --- end to end ---------------------------------------------------------------------------------------------------


def test_sign_in_opens_the_production_console_on_postgresql_and_revocation_closes_it(runtime, clock, world,
                                                                                     monkeypatch):
    """login -> resolve principal -> production console -> own account -> foreign account refused -> revoke ->
    refused, with every SQLite connection forbidden throughout."""
    def no_sqlite(*args, **kwargs):
        raise AssertionError("production opened SQLite")
    monkeypatch.setattr(sqlite3, "connect", no_sqlite)

    console_b = sign_in(runtime, clock, world.subject_b)
    b_account = console_b.local.create_account("B's account (synthetic)")
    console_a = sign_in(runtime, clock, world.subject_a)
    assert (console_a.principal.user_id, console_a.principal.organization_id) == (world.user_a, world.org_a)
    assert console_a.actor == "Avery (synthetic)"  # from BAEC's records, not the provider's "name" claim
    assert console_a.authentication_required and isinstance(console_a._backend, production.PostgresBackend)
    a_account = console_a.local.create_account("A's account (synthetic)")
    assert console_a.local.account_ids() == [a_account]
    assert [v.account.account_id for v in console_a.account_views()] == [a_account]
    with pytest.raises(AccessDenied):
        console_a.local.account_view(b_account)
    with pytest.raises(AccessDenied):
        console_a.owner(b_account)

    world.provisioner.revoke(world.org_a, TEST_ISSUER, world.subject_a, "operator (synthetic)")
    with pytest.raises(PrincipalInvalid):  # the running console, next operation: no sign-out or restart needed
        console_a.local.account_ids()
    assert refused(runtime, clock, world.subject_a) == auth.NO_ACCESS
    assert console_b.local.account_ids() == [b_account]  # B is unaffected
    assert not production.PRODUCTION_ROOT.exists()


def test_the_provisioning_cli_creates_links_and_revokes_without_echoing_its_url(migrated, runtime, clock):
    environ = {"BAEC_PROVISIONING_DATABASE_URL": migrated["provisioner"]}
    org, user, subject = fresh("org-cli"), fresh("user-cli"), fresh("subject-cli")
    for argv in (["create-organization", "--organization", org, "--name", "CLI organization (synthetic)"],
                 ["create-user", "--organization", org, "--user", user, "--name", "Casey (synthetic)"],
                 ["link", "--organization", org, "--user", user, "--issuer", TEST_ISSUER, "--subject", subject,
                  "--operator", "operator (synthetic)"]):
        assert provision.main(argv, environ) == 0
    assert sign_in(runtime, clock, subject).principal.user_id == user
    assert provision.main(["deactivate", "--organization", org, "--user", user], environ) == 0
    assert refused(runtime, clock, subject) == auth.NO_ACCESS
    assert provision.main(["reactivate", "--organization", org, "--user", user], environ) == 0
    assert sign_in(runtime, clock, subject).principal.user_id == user
    assert provision.main(["revoke", "--organization", org, "--issuer", TEST_ISSUER, "--subject", subject,
                           "--operator", "operator (synthetic)"], environ) == 0
    assert refused(runtime, clock, subject) == auth.NO_ACCESS


@pytest.mark.parametrize("role", ["app", "auth", "migrator", "admin"])
def test_provisioning_runs_only_as_the_provisioning_role(migrated, role, capsys):
    with pytest.raises(provision.ProvisioningError, match="only as baec_provisioner"):
        provision.Provisioner(migrated[role])
    assert provision.main(["create-organization", "--organization", fresh("org"), "--name", "x"],
                          {"BAEC_PROVISIONING_DATABASE_URL": migrated[role]}) == 1
    assert urlsplit(migrated[role]).password not in capsys.readouterr().err


# --- HUMAN only -------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("kind", NON_HUMAN)
def test_a_non_human_user_cannot_be_linked_by_the_tool_or_by_sql(storage, world, migrated, kind):
    user = fresh(f"user-{kind.value.lower()}")
    provision_user(storage, world.org_a, user, f"{kind.value} (synthetic)", kind)
    with pytest.raises(provision.ProvisioningError, match="only an active HUMAN user"):
        world.provisioner.link(world.org_a, user, TEST_ISSUER, fresh("subject"), "operator (synthetic)")
    with psycopg.connect(migrated["provisioner"], autocommit=True) as db:  # the trigger, as the provisioning role
        with pytest.raises(psycopg.errors.CheckViolation, match="only an active HUMAN user"), db.transaction():
            db.execute("SELECT set_config('baec.organization_id', %s, true)", (world.org_a,))
            db.execute("INSERT INTO baec_auth.auth_identities (issuer, subject, organization_id, user_id, linked_at, "
                       "linked_by) VALUES (%s, %s, %s, %s, now(), 'x')", (TEST_ISSUER, fresh("s"), world.org_a, user))
    with psycopg.connect(migrated["admin"], autocommit=True) as admin:  # even the superuser, past RLS
        with pytest.raises(psycopg.errors.CheckViolation, match="only an active HUMAN user"), admin.transaction():
            admin.execute("SELECT set_config('baec.organization_id', %s, true)", (world.org_a,))
            admin.execute("INSERT INTO baec_auth.auth_identities (issuer, subject, organization_id, user_id, "
                          "linked_at, linked_by) VALUES (%s, %s, %s, %s, now(), 'x')",
                          (TEST_ISSUER, fresh("s"), world.org_a, user))


@pytest.mark.parametrize("kind", NON_HUMAN)
def test_a_non_human_user_is_refused_even_if_a_link_to_it_exists(storage, world, migrated, runtime, clock, kind):
    """A mapping to a system, development, demo, or unclassified identity can only be created by switching the guard
    off; production still refuses it, because the principal must be an active HUMAN user."""
    user, subject = fresh(f"user-{kind.value.lower()}"), fresh("subject")
    provision_user(storage, world.org_a, user, f"{kind.value} (synthetic)", kind)
    superuser(migrated, ("INSERT INTO baec_auth.auth_identities (issuer, subject, organization_id, user_id, linked_at, "
                         "linked_by) VALUES (%s, %s, %s, %s, now(), 'tampered')",
                         (TEST_ISSUER, subject, world.org_a, user)), replica=True)
    assert runtime.lookup.resolve(TEST_ISSUER, subject) == LinkedUser(user, world.org_a)
    assert refused(runtime, clock, subject) == auth.NO_ACCESS


def test_the_development_and_seed_identities_have_no_way_into_production(storage, world, migrated, runtime, clock):
    """The organization's synthetic-seed identity, linked by tampering, is still not a production principal, and a
    signed-in HUMAN cannot act as it. (Every other non-HUMAN kind: the parametrized test above.)"""
    from baec_console import identity
    seed = identity.seed_user_id(world.org_a)
    provision_user(storage, world.org_a, seed, identity.SEED_DISPLAY_NAME, UserKind.SYSTEM_SEED)
    subject = fresh("subject")
    superuser(migrated, ("INSERT INTO baec_auth.auth_identities (issuer, subject, organization_id, user_id, "
                         "linked_at, linked_by) VALUES (%s, %s, %s, %s, now(), 'tampered')",
                         (TEST_ISSUER, subject, world.org_a, seed)), replica=True)
    assert refused(runtime, clock, subject) == auth.NO_ACCESS
    console = sign_in(runtime, clock, world.subject_a)
    with pytest.raises(ConsoleError, match="not available to a signed-in production user"):
        console.seed_service()  # the HUMAN user cannot act as the seed identity either


def test_an_inactive_user_is_refused_at_sign_in_and_access_returns_on_reactivation(runtime, clock, world):
    world.provisioner.set_active(world.org_a, world.user_a, False)
    assert refused(runtime, clock, world.subject_a) == auth.NO_ACCESS
    with pytest.raises(provision.ProvisioningError, match="only an active HUMAN user"):  # nor can it be linked anew
        world.provisioner.link(world.org_a, world.user_a, OTHER_ISSUER, fresh("subject"), "operator (synthetic)")
    world.provisioner.set_active(world.org_a, world.user_a, True)
    assert sign_in(runtime, clock, world.subject_a).principal.user_id == world.user_a


# --- revocation, deactivation, session age while signed in ------------------------------------------------------------


def test_a_user_deactivated_while_signed_in_is_refused_on_the_next_operation(runtime, clock, world):
    console = sign_in(runtime, clock, world.subject_a)
    account = console.local.create_account("A's account (synthetic)")
    world.provisioner.set_active(world.org_a, world.user_a, False)
    for operation in (console.local.account_ids, lambda: console.local.account_view(account), console.activity,
                      lambda: console.local.crm_add_task(account, contact_id=None, title="Not created (synthetic)",
                                                         due_date=None, priority="NORMAL"),
                      lambda: console.local.create_account("Not created (synthetic)")):
        with pytest.raises(PrincipalInvalid):
            operation()
    world.provisioner.set_active(world.org_a, world.user_a, True)  # reversible, as in Phase 2
    assert console.local.account_ids() == [account]  # nothing was written while refused
    assert console.local.crm_tasks(account_id=account) == []


def test_a_link_revoked_while_signed_in_refuses_every_later_operation_permanently(runtime, clock, world):
    console = sign_in(runtime, clock, world.subject_a)
    account = console.local.create_account("A's account (synthetic)")
    world.provisioner.revoke(world.org_a, TEST_ISSUER, world.subject_a, "operator (synthetic)")
    for _ in range(3):
        with pytest.raises(PrincipalInvalid):
            console.local.account_view(account)
    with pytest.raises(provision.ProvisioningError):  # a revoked subject is never linked again
        world.provisioner.link(world.org_a, world.user_a, TEST_ISSUER, world.subject_a, "operator (synthetic)")
    new_subject = fresh("subject-a2")  # the person gets access again only through a new link
    world.provisioner.link(world.org_a, world.user_a, TEST_ISSUER, new_subject, "operator (synthetic)")
    with pytest.raises(PrincipalInvalid):
        console.local.account_ids()  # the old session stays refused
    assert sign_in(runtime, clock, new_subject).local.account_ids() == [account]


def test_a_session_older_than_the_maximum_age_is_refused_while_open(runtime, clock, world):
    console = sign_in(runtime, clock, world.subject_a)
    console.local.account_ids()
    clock.at += auth.SESSION_MAX_AGE
    with pytest.raises(PrincipalInvalid):
        console.local.account_ids()
    with pytest.raises(auth.SessionExpired):
        sign_in(runtime, clock, world.subject_a, iat=int((clock.at - auth.SESSION_MAX_AGE).timestamp()) - 1)


# --- §9 attacks ---------------------------------------------------------------------------------------------------


def test_session_swapping_a_console_serves_only_the_sign_in_it_was_opened_for(runtime, clock, world):
    console_a, console_b = sign_in(runtime, clock, world.subject_a), sign_in(runtime, clock, world.subject_b)
    a_account = console_a.local.create_account("A (synthetic)")
    b_account = console_b.local.create_account("B (synthetic)")
    assert console_a is not console_b and console_a._credential is not console_b._credential
    assert (console_a.local.account_ids(), console_b.local.account_ids()) == ([a_account], [b_account])
    for console, foreign in ((console_a, b_account), (console_b, a_account)):
        with pytest.raises(AccessDenied):
            console.local.account_view(foreign)
    # A's credential cannot carry B's principal, nor B's credential A's.
    for credential, principal in ((console_a._credential, console_b.principal),
                                  (console_b._credential, console_a.principal)):
        with pytest.raises(IdentityError):
            Console(production.PRODUCTION_ROOT, clock, principal=principal, authentication_required=True,
                    backend=runtime.backend, credential=credential)


def test_a_forged_organization_or_user_claim_changes_nothing(runtime, clock, world):
    b_account = sign_in(runtime, clock, world.subject_b).local.create_account("B (synthetic)")
    console = sign_in(runtime, clock, world.subject_a, organization_id=world.org_b, user_id=world.user_b,
                      baec_user_id=world.user_b, org=world.org_b, role="admin", roles=["owner"],
                      name="Blake (synthetic)", email="blake@example.invalid")
    assert (console.principal.user_id, console.principal.organization_id) == (world.user_a, world.org_a)
    assert console.actor == "Avery (synthetic)"
    with pytest.raises(AccessDenied):
        console.local.account_view(b_account)


def test_a_principal_naming_another_organization_or_user_is_refused(runtime, clock, world):
    console = sign_in(runtime, clock, world.subject_a)
    credential = console._credential
    for principal in (Principal(world.user_a, world.org_b, "Avery (synthetic)"),
                      Principal(world.user_b, world.org_b, "Blake (synthetic)"),
                      Principal(world.user_b, world.org_a, "Blake (synthetic)")):
        with pytest.raises(IdentityError):
            Console(production.PRODUCTION_ROOT, clock, principal=principal, authentication_required=True,
                    backend=runtime.backend, credential=credential)
    forged = Principal(world.user_b, world.org_b, "Blake (synthetic)")
    console.principal = forged  # tampering with the live objects: the console, then one of its services
    with pytest.raises(PrincipalInvalid):
        console.account_views()
    console.local.principal = forged
    with pytest.raises(PrincipalInvalid):
        console.local.account_ids()


def test_a_subject_is_unique_per_issuer_and_never_matched_across_issuers(runtime, clock, world, provisioner):
    with pytest.raises(provision.ProvisioningError, match="duplicate key"):  # B cannot take A's subject
        provisioner.link(world.org_b, world.user_b, TEST_ISSUER, world.subject_a, "operator (synthetic)")
    with pytest.raises(provision.ProvisioningError, match="duplicate key"):  # one unrevoked link per user
        provisioner.link(world.org_a, world.user_a, OTHER_ISSUER, fresh("subject"), "operator (synthetic)")
    # The same subject text at another issuer is another identity, and production accepts only its configured issuer.
    other = fresh("user-c")
    provisioner.create_user(world.org_b, other, "Casey (synthetic)")
    provisioner.link(world.org_b, other, OTHER_ISSUER, world.subject_a, "operator (synthetic)")
    assert runtime.lookup.resolve(OTHER_ISSUER, world.subject_a) == LinkedUser(other, world.org_b)
    assert sign_in(runtime, clock, world.subject_a).principal.user_id == world.user_a
    with pytest.raises(auth.SignInRefused):
        auth.identity_from_claims(claims(world.subject_a, clock, iss=OTHER_ISSUER), issuer=TEST_ISSUER,
                                  now=clock.now())
    foreign = auth.AuthenticatedIdentity(OTHER_ISSUER, world.subject_a, None, clock.now())
    with pytest.raises(auth.SignInRefused):
        runtime.console_for(foreign)
    for near in (world.subject_a.upper(), world.subject_a + " ", world.subject_a[:-1], world.subject_a + "%",
                 "%", "*", ""):
        assert runtime.lookup.resolve(TEST_ISSUER, near) is None
    assert runtime.lookup.resolve(TEST_ISSUER + "/", world.subject_a) is None


def test_an_unknown_subject_and_a_known_one_are_refused_alike(runtime, clock, world):
    world.provisioner.set_active(world.org_a, world.user_a, False)
    assert refused(runtime, clock, fresh("never-linked")) == refused(runtime, clock, world.subject_a) == auth.NO_ACCESS


def test_a_tampered_link_naming_another_organization_fails_closed(runtime, clock, world, migrated):
    console = sign_in(runtime, clock, world.subject_a)
    superuser(migrated, ("UPDATE baec_auth.auth_identities SET organization_id = %s WHERE issuer = %s AND subject = %s",
                         (world.org_b, TEST_ISSUER, world.subject_a)), replica=True)
    assert runtime.lookup.resolve(TEST_ISSUER, world.subject_a) == LinkedUser(world.user_a, world.org_b)
    with pytest.raises(PrincipalInvalid):  # the open console: the link no longer names its user and organization
        console.local.account_ids()
    assert refused(runtime, clock, world.subject_a) == auth.NO_ACCESS  # user A does not exist in organization B


def test_a_tampered_link_naming_another_user_fails_closed(runtime, clock, world, migrated):
    console = sign_in(runtime, clock, world.subject_a)
    with pytest.raises(psycopg.errors.UniqueViolation):  # even the superuser cannot give B a second live link
        superuser(migrated, ("UPDATE baec_auth.auth_identities SET user_id = %s, organization_id = %s "
                             "WHERE issuer = %s AND subject = %s", (world.user_b, world.org_b, TEST_ISSUER,
                                                                    world.subject_a)), replica=True)
    unlinked = fresh("user-c")
    world.provisioner.create_user(world.org_b, unlinked, "Casey (synthetic)")
    superuser(migrated, ("UPDATE baec_auth.auth_identities SET user_id = %s, organization_id = %s "
                         "WHERE issuer = %s AND subject = %s", (unlinked, world.org_b, TEST_ISSUER, world.subject_a)),
              replica=True)
    with pytest.raises(PrincipalInvalid):  # the open console never follows the link to someone else
        console.local.account_ids()
    # A fresh sign-in now resolves to the new user: the database is the authority, which is exactly why only the
    # provisioning role (and the superuser) can write links, the guard trigger refuses re-pointing, and the gate pins
    # the privileges, policies, and trigger.
    assert sign_in(runtime, clock, world.subject_a).principal.user_id == unlinked


# --- the link table itself ----------------------------------------------------------------------------------------


def test_a_link_is_immutable_except_for_one_revocation(world, migrated):
    key = (TEST_ISSUER, world.subject_a)
    with psycopg.connect(migrated["provisioner"], autocommit=True) as db:
        for statement in ("UPDATE baec_auth.auth_identities SET user_id = 'x' WHERE issuer = %s AND subject = %s",
                          "UPDATE baec_auth.auth_identities SET organization_id = 'x' WHERE issuer = %s AND subject = %s",
                          "UPDATE baec_auth.auth_identities SET subject = 'x' WHERE issuer = %s AND subject = %s",
                          "DELETE FROM baec_auth.auth_identities WHERE issuer = %s AND subject = %s"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege), db.transaction():
                db.execute("SELECT set_config('baec.organization_id', %s, true)", (world.org_a,))
                db.execute(statement, key)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            db.execute("TRUNCATE baec_auth.auth_identities")
    with psycopg.connect(migrated["admin"], autocommit=True) as admin:  # past every privilege, the trigger holds
        for statement, args, message in (
                ("UPDATE baec_auth.auth_identities SET user_id = %s, organization_id = %s "
                 "WHERE issuer = %s AND subject = %s", (world.user_b, world.org_b, *key), "can only be revoked"),
                ("UPDATE baec_auth.auth_identities SET subject = %s WHERE issuer = %s AND subject = %s",
                 ("other-subject", *key), "can only be revoked"),
                ("DELETE FROM baec_auth.auth_identities WHERE issuer = %s AND subject = %s", key, "never deleted")):
            with pytest.raises(psycopg.errors.RestrictViolation, match=message):
                admin.execute(statement, args)
        with pytest.raises(psycopg.errors.RestrictViolation, match="append-only"):
            admin.execute("TRUNCATE baec_auth.auth_identities")
    world.provisioner.revoke(world.org_a, *key, "operator (synthetic)")
    with pytest.raises(provision.ProvisioningError):  # revocation happens once
        world.provisioner.revoke(world.org_a, *key, "operator (synthetic)")
    with psycopg.connect(migrated["admin"], autocommit=True) as admin:
        with pytest.raises(psycopg.errors.RestrictViolation, match="never changes"):
            admin.execute("UPDATE baec_auth.auth_identities SET revoked_at = NULL, revoked_by = NULL "
                          "WHERE issuer = %s AND subject = %s", key)


def test_links_are_provisioned_only_inside_their_own_organization(world, migrated):
    with psycopg.connect(migrated["provisioner"], autocommit=True) as db:
        with pytest.raises(psycopg.Error), db.transaction():  # acting for A, writing a link of B
            db.execute("SELECT set_config('baec.organization_id', %s, true)", (world.org_a,))
            db.execute("INSERT INTO baec_auth.auth_identities (issuer, subject, organization_id, user_id, linked_at, "
                       "linked_by) VALUES (%s, %s, %s, %s, now(), 'x')", (TEST_ISSUER, fresh("s"), world.org_b,
                                                                         world.user_b))
        with db.transaction():  # acting for A, B's links and users are invisible
            db.execute("SELECT set_config('baec.organization_id', %s, true)", (world.org_a,))
            assert db.execute("SELECT count(*) FROM baec_auth.auth_identities WHERE organization_id = %s",
                              (world.org_b,)).fetchone()[0] == 0
            assert db.execute("SELECT count(*) FROM baec.users WHERE organization_id = %s",
                              (world.org_b,)).fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM baec_auth.auth_identities").fetchone()[0] == 0  # no tenant: nothing
        for table in ("baec.account_ownership", "baec.crm_contacts", "baec.events", "baec_engine1.accounts"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                db.execute(f"SELECT 1 FROM {table}")
    with pytest.raises(provision.ProvisioningError):  # the tool: B's user is unknown inside A
        world.provisioner.link(world.org_a, world.user_b, TEST_ISSUER, fresh("s"), "operator (synthetic)")


# --- §10 the sign-in lookup role ---------------------------------------------------------------------------------


@pytest.fixture
def lookup_db(migrated):
    with psycopg.connect(migrated["auth"], autocommit=True) as db:
        yield db


def _as_subject(db, issuer: str | None, subject: str | None, sql: str):
    with db.transaction():
        if issuer is not None:
            db.execute("SELECT set_config('baec.auth_issuer', %s, true)", (issuer,))
        if subject is not None:
            db.execute("SELECT set_config('baec.auth_subject', %s, true)", (subject,))
        return db.execute(sql).fetchall()


def test_the_lookup_role_resolves_exactly_one_subject_and_cannot_enumerate(world, lookup_db, lookup):
    assert lookup.resolve(TEST_ISSUER, world.subject_a) == LinkedUser(world.user_a, world.org_a)
    everything = "SELECT issuer, subject, organization_id, user_id FROM baec_auth.auth_identities"
    assert _as_subject(lookup_db, TEST_ISSUER, world.subject_a, everything) == [
        (TEST_ISSUER, world.subject_a, world.org_a, world.user_a)]  # no WHERE clause: still one row
    for issuer, subject in ((None, None), (TEST_ISSUER, None), (None, world.subject_a), (TEST_ISSUER, "%"),
                            (TEST_ISSUER, ".*"), ("%", "%"), (TEST_ISSUER, ""), (OTHER_ISSUER, world.subject_a)):
        assert _as_subject(lookup_db, issuer, subject, everything) == []
        assert _as_subject(lookup_db, issuer, subject,
                           "SELECT count(DISTINCT organization_id) FROM baec_auth.auth_identities") == [(0,)]
    assert _as_subject(lookup_db, TEST_ISSUER, world.subject_a, everything + " WHERE subject = "
                       + repr(world.subject_b).replace('"', "'")) == []


@pytest.mark.parametrize("sql", [
    "SELECT linked_by FROM baec_auth.auth_identities",
    "SELECT linked_at FROM baec_auth.auth_identities",
    "SELECT * FROM baec_auth.auth_identities",
    "SELECT * FROM baec.users",
    "SELECT * FROM baec.organizations",
    "SELECT * FROM baec.identity_audit",
    "SELECT * FROM baec.account_ownership",
    "SELECT * FROM baec.account_meta",
    "SELECT * FROM baec.crm_contacts",
    "SELECT * FROM baec.crm_tasks",
    "SELECT * FROM baec.crm_activities",
    "SELECT * FROM baec_engine1.accounts",
    "SELECT * FROM baec_engine1.baec_records",
    "SELECT * FROM baec_engine2.monitoring_plans",
    "SELECT * FROM baec_migrations.schema_migrations",
    "INSERT INTO baec_auth.auth_identities (issuer, subject, organization_id, user_id, linked_at, linked_by) "
    "VALUES ('https://x.invalid', 's', 'o', 'u', now(), 'x')",
    "UPDATE baec_auth.auth_identities SET revoked_at = now(), revoked_by = 'x'",
    "DELETE FROM baec_auth.auth_identities",
    "TRUNCATE baec_auth.auth_identities",
    "CREATE TABLE baec_auth.shadow (x int)",
    "CREATE TABLE public.shadow (x int)",
    "SET ROLE baec_app",
    "SET ROLE baec_provisioner",
    "SET session_replication_role = replica",
    "SELECT baec_auth.auth_identities_guard()",
])
def test_the_lookup_role_can_read_nothing_else_and_change_nothing(world, lookup_db, sql):
    with pytest.raises(psycopg.Error) as error, lookup_db.transaction():
        lookup_db.execute("SELECT set_config('baec.auth_issuer', %s, true), set_config('baec.auth_subject', %s, true), "
                          "set_config('baec.organization_id', %s, true)", (TEST_ISSUER, world.subject_a, world.org_a))
        lookup_db.execute(sql)
    assert isinstance(error.value, (psycopg.errors.InsufficientPrivilege, psycopg.errors.UndefinedFunction))


def test_the_application_role_cannot_read_or_write_sign_in_links(world, app):
    for sql in ("SELECT * FROM baec_auth.auth_identities", "SELECT subject FROM baec_auth.auth_identities",
                "INSERT INTO baec_auth.auth_identities (issuer, subject, organization_id, user_id, linked_at, "
                "linked_by) VALUES ('https://x.invalid', 's', 'o', 'u', now(), 'x')"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege), app.transaction():
            app.execute("SELECT set_config('baec.organization_id', %s, true)", (world.org_a,))
            app.execute(sql)


def test_tenant_rls_is_unchanged_for_the_application_role(world, runtime, clock, app):
    a_account = sign_in(runtime, clock, world.subject_a).local.create_account("A (synthetic)")
    with app.transaction():
        assert app.execute("SELECT count(*) FROM baec.users").fetchone()[0] == 0  # no tenant: nothing
    with app.transaction():
        app.execute("SELECT set_config('baec.organization_id', %s, true)", (world.org_b,))
        assert app.execute("SELECT count(*) FROM baec.account_ownership WHERE account_id = %s",
                           (a_account,)).fetchone()[0] == 0
        assert {r[0] for r in app.execute("SELECT organization_id FROM baec.users")} == {world.org_b}


def test_the_gate_accepts_the_migrated_sign_in_schema_and_lookup_role(migrated):
    assert check_database(migrated["app"], migrated["auth"]).schema_versions == (1, 2, 3, 4, 5, 6)


# --- §11 sign-in schema drift --------------------------------------------------------------------------------------

AUTH_SABOTAGE = {
    "whole-table read": ("migrator", "GRANT SELECT ON baec_auth.auth_identities TO baec_auth_lookup",
                         "may SELECT baec_auth.auth_identities.linked_by"),
    "lookup may revoke": ("migrator", "GRANT UPDATE (revoked_at) ON baec_auth.auth_identities TO baec_auth_lookup",
                          "may UPDATE baec_auth.auth_identities.revoked_at"),
    "lookup may insert": ("migrator", "GRANT INSERT ON baec_auth.auth_identities TO baec_auth_lookup",
                          "'INSERT'] on baec_auth.auth_identities"),
    "lookup reaches tenant schema": ("migrator", "GRANT USAGE ON SCHEMA baec TO baec_auth_lookup",
                                     "has ['USAGE'] on schema baec"),
    "lookup reads users": ("migrator", "GRANT SELECT ON baec.users TO baec_auth_lookup", "on baec.users"),
    "lookup reads CRM": ("migrator", "GRANT SELECT ON baec.crm_contacts TO baec_auth_lookup", "on baec.crm_contacts"),
    "lookup may create": ("migrator", "GRANT CREATE ON SCHEMA baec_auth TO baec_auth_lookup",
                          "on schema baec_auth"),
    "lookup runs the guard": ("migrator", "GRANT EXECUTE ON FUNCTION baec_auth.auth_identities_guard() "
                                          "TO baec_auth_lookup", "may execute"),
    "open lookup policy": ("migrator", "ALTER POLICY subject_lookup ON baec_auth.auth_identities USING (true)",
                           "not the expected sign-in policies"),
    "dropped lookup boundary": ("migrator", "DROP POLICY subject_boundary ON baec_auth.auth_identities",
                                "not the expected sign-in policies"),
    "lookup policy for the app": ("migrator", "ALTER POLICY subject_lookup ON baec_auth.auth_identities "
                                              "TO baec_auth_lookup, baec_app", "not the expected sign-in policies"),
    "extra open policy": ("migrator", "CREATE POLICY anyone ON baec_auth.auth_identities FOR SELECT USING (true)",
                          "not the expected sign-in policies"),
    "RLS not forced": ("migrator", "ALTER TABLE baec_auth.auth_identities NO FORCE ROW LEVEL SECURITY",
                       "relation baec_auth.auth_identities differs"),
    "RLS disabled": ("migrator", "ALTER TABLE baec_auth.auth_identities DISABLE ROW LEVEL SECURITY",
                     "relation baec_auth.auth_identities differs"),
    "disabled guard": ("migrator", "ALTER TABLE baec_auth.auth_identities DISABLE TRIGGER auth_identities_guard",
                       "trigger baec_auth.auth_identities.auth_identities_guard differs"),
    "dropped truncate guard": ("migrator", "DROP TRIGGER auth_identities_no_truncate ON baec_auth.auth_identities",
                               "auth_identities_no_truncate is missing"),
    "weakened guard": ("migrator", "CREATE OR REPLACE FUNCTION baec_auth.auth_identities_guard() RETURNS trigger "
                                   "LANGUAGE plpgsql AS $$ BEGIN RETURN NEW; END $$",
                       "function baec_auth.auth_identities_guard differs from its migration"),
    "weakened subject setting": ("migrator", "CREATE OR REPLACE FUNCTION baec_auth.current_subject() RETURNS text "
                                             "LANGUAGE sql STABLE AS $$ SELECT subject FROM baec_auth.auth_identities "
                                             "LIMIT 1 $$", "function baec_auth.current_subject differs"),
    "security definer": ("migrator", "ALTER FUNCTION baec_auth.current_subject() SECURITY DEFINER",
                         "baec_auth.current_subject is SECURITY DEFINER"),
    "no subject uniqueness": ("migrator", "ALTER TABLE baec_auth.auth_identities DROP CONSTRAINT auth_identities_pkey",
                              "auth_identities_pkey is missing"),
    "no one-link-per-user": ("migrator", "DROP INDEX baec_auth.auth_identities_one_active_link_per_user",
                             "auth_identities_one_active_link_per_user is missing"),
    "no user foreign key": ("migrator", "ALTER TABLE baec_auth.auth_identities DROP CONSTRAINT "
                                        "auth_identities_organization_id_user_id_fkey",
                            "auth_identities_organization_id_user_id_fkey is missing"),
    "weakened subject check": ("migrator", "ALTER TABLE baec_auth.auth_identities DROP CONSTRAINT "
                                           "auth_identities_subject_check", "auth_identities_subject_check is missing"),
    "app reads links": ("migrator", ["GRANT USAGE ON SCHEMA baec_auth TO baec_app",
                                     "GRANT SELECT ON baec_auth.auth_identities TO baec_app"],
                        "privilege baec_auth.auth_identities differs"),
    "extra table": ("migrator", "CREATE TABLE baec_auth.shadow (subject text)", "unexpected relation baec_auth.shadow"),
    "extra column": ("migrator", "ALTER TABLE baec_auth.auth_identities ADD COLUMN email text",
                     "unexpected column baec_auth.auth_identities.email"),
}


@pytest.mark.parametrize("case", sorted(AUTH_SABOTAGE))
def test_sign_in_schema_drift_closes_production(scratch_database, case):  # noqa: F811
    role, sabotage, expected = AUTH_SABOTAGE[case]
    assert migrate.apply_migrations(scratch_database["migrator"]) == [1, 2, 3, 4, 5, 6]
    with psycopg.connect(scratch_database[role], autocommit=True) as db:
        for statement in [sabotage] if isinstance(sabotage, str) else sabotage:
            db.execute(statement)
    for _ in range(2):  # refused every time: the gate never repairs anything
        with pytest.raises(production.storage.StorageRefused) as error:
            production.open_runtime(auth_section(), production_environ(scratch_database))
        assert str(error.value) == AUTH_SCHEMA_MISMATCH
        assert any(expected in f for f in error.value.failures), error.value.failures
        for name in ("app", "auth"):
            assert urlsplit(scratch_database[name]).password not in str(error.value) + "".join(error.value.failures)


@pytest.mark.parametrize("change,expected", [
    ("ALTER ROLE baec_auth_lookup BYPASSRLS", "sign-in lookup role can bypass row-level security"),
    ("GRANT baec_app TO baec_auth_lookup", "member of a role, or has members"),
    ("GRANT baec_auth_lookup TO baec_app", "the application role is a member of another role"),
    ("GRANT baec_provisioner TO baec_auth_lookup", "member of a role, or has members"),
    ("ALTER ROLE baec_auth_lookup CREATEROLE", "sign-in lookup role can create roles"),
])
def test_an_unsafe_lookup_role_closes_production(migrated, change, expected):
    """Role attributes and memberships are cluster-wide, so each is reverted whatever happens."""
    undo = {"ALTER ROLE baec_auth_lookup BYPASSRLS": "ALTER ROLE baec_auth_lookup NOBYPASSRLS",
            "ALTER ROLE baec_auth_lookup CREATEROLE": "ALTER ROLE baec_auth_lookup NOCREATEROLE"}.get(
        change, change.replace("GRANT", "REVOKE").replace(" TO ", " FROM "))
    with psycopg.connect(migrated["admin"], autocommit=True) as admin:
        admin.execute(change)
        try:
            with pytest.raises(DatabaseRefused) as error:
                check_database(migrated["app"], migrated["auth"])
        finally:
            admin.execute(undo)
    assert any(expected in f for f in error.value.failures), error.value.failures
    check_database(migrated["app"], migrated["auth"])


def test_a_tampered_sign_in_migration_record_closes_production(scratch_database):  # noqa: F811
    migrate.apply_migrations(scratch_database["migrator"])
    with psycopg.connect(scratch_database["admin"], autocommit=True) as admin:
        admin.execute("UPDATE baec_migrations.schema_migrations SET checksum = repeat('0', 64) WHERE version = 5")
    with pytest.raises(production.storage.StorageRefused, match="not at the expected migration version"):
        production.open_runtime(auth_section(), production_environ(scratch_database))


def test_the_lookup_role_check_sees_rows_a_broken_policy_would_expose(world, migrated):
    """The gate's live probe: with the lookup policy opened (by the owner), the lookup role would see links without a
    subject; on this database (which has links) the gate names exactly that, besides the policy difference."""
    with psycopg.connect(migrated["migrator"], autocommit=True) as owner:
        owner.execute("ALTER POLICY subject_lookup ON baec_auth.auth_identities USING (true)")
        owner.execute("ALTER POLICY subject_boundary ON baec_auth.auth_identities USING (true)")
        try:
            with psycopg.connect(migrated["auth"], autocommit=True) as db:
                from baec_console.postgres.gate import auth_lookup_failures
                assert auth_lookup_failures(db) == [
                    "the sign-in lookup role sees sign-in links without an exact issuer and subject"]
        finally:
            for name in ("subject_lookup", "subject_boundary"):
                owner.execute(f"ALTER POLICY {name} ON baec_auth.auth_identities USING "
                              "(issuer = baec_auth.current_issuer() AND subject = baec_auth.current_subject())")
    check_database(migrated["app"], migrated["auth"])
