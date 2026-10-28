"""V0.4 Phase 4: the sign-in boundary, without a database or network (the PostgreSQL flow is tests_postgres).

What a provider session establishes (auth.identity_from_claims), what production sign-in configuration must look like
(auth.configuration_failures), and that the console guard re-checks a sign-in credential before every operation. All
identities are synthetic. These are software tests, not a security assessment.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from baec_console import auth, identity
from baec_console.console_service import (
    RENAME_UNAVAILABLE,
    SEEDING_UNAVAILABLE,
    Console,
    ConsoleError,
    PrincipalInvalid,
)
from baec_console.identity import UserKind

from .conftest import StepClock

ISSUER = "https://issuer.baec-test.invalid"
NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
SECRET = "do-not-show-this-client-secret"
COOKIE = "do-not-show-this-cookie-secret-0123456789"


def claims(**overrides) -> dict:
    """What st.user holds after Streamlit verified an ID token from the default provider (synthetic values)."""
    base = {"is_logged_in": True, "provider": "default", "iss": ISSUER, "sub": "subject-a-0001", "aud": "client",
            "iat": int((NOW - timedelta(minutes=10)).timestamp()), "exp": int((NOW + timedelta(minutes=50)).timestamp()),
            "email": "avery@example.invalid", "email_verified": True, "name": "Avery (synthetic)"}
    base.update(overrides)
    return {k: v for k, v in base.items() if v is not ...}


def section(**overrides) -> dict:
    base = {"redirect_uri": "https://console.baec-test.invalid/oauth2callback", "cookie_secret": COOKIE,
            "client_id": "baec-client", "client_secret": SECRET,
            "server_metadata_url": ISSUER + "/.well-known/openid-configuration"}
    base.update(overrides)
    return {k: v for k, v in base.items() if v is not ...}


# --- what a session establishes -----------------------------------------------------------------------------------


def test_a_verified_session_establishes_issuer_subject_verified_email_and_issue_time_only():
    signed_in = auth.identity_from_claims(claims(), issuer=ISSUER, now=NOW)
    assert signed_in == auth.AuthenticatedIdentity(ISSUER, "subject-a-0001", "avery@example.invalid",
                                                   NOW - timedelta(minutes=10))
    assert signed_in.key == (ISSUER, "subject-a-0001")
    assert set(vars(signed_in)) == {"issuer", "subject", "email", "issued_at"}  # no user, organization, role, name


def test_claims_a_browser_or_provider_might_add_never_reach_the_identity():
    forged = claims(organization_id="org-b", user_id="user-b", baec_user_id="user-b", role="admin",
                    roles=["owner"], groups=["org-b"], name="Blake", hd="org-b.example.invalid")
    assert auth.identity_from_claims(forged, issuer=ISSUER, now=NOW) == auth.identity_from_claims(
        claims(), issuer=ISSUER, now=NOW)


@pytest.mark.parametrize("case,overrides", [
    ("not signed in", {"is_logged_in": False}),
    ("signed-in flag as text", {"is_logged_in": "true"}),
    ("no signed-in flag", {"is_logged_in": ...}),
    ("another issuer", {"iss": "https://other-issuer.example.invalid"}),
    ("issuer with a trailing slash", {"iss": ISSUER + "/"}),
    ("no issuer", {"iss": ...}),
    ("a named provider", {"provider": "google"}),
    ("no provider", {"provider": ...}),
    ("no subject", {"sub": ...}),
    ("empty subject", {"sub": ""}),
    ("numeric subject", {"sub": 12345}),
    ("subject with a space", {"sub": "subject a"}),
    ("subject with non-ASCII", {"sub": "subjéct"}),
    ("subject too long", {"sub": "s" * 256}),
    ("no issue time", {"iat": ...}),
    ("issue time as text", {"iat": "1790000000"}),
    ("issue time as a boolean", {"iat": True}),
    ("issue time out of range", {"iat": 10 ** 20}),
    ("issued in the future", {"iat": int((NOW + timedelta(hours=1)).timestamp())}),
])
def test_an_unusable_session_is_refused_with_one_message(case, overrides):
    with pytest.raises(auth.SignInRefused) as refused:
        auth.identity_from_claims(claims(**overrides), issuer=ISSUER, now=NOW)
    assert str(refused.value) in (auth.NO_ACCESS, auth.SESSION_EXPIRED)
    assert "subject" not in str(refused.value).lower()  # never says which check failed


def test_a_subject_of_exactly_255_printable_characters_is_accepted():
    subject = "".join(chr(0x21 + i % 94) for i in range(255))
    assert auth.identity_from_claims(claims(sub=subject), issuer=ISSUER, now=NOW).subject == subject


def test_a_session_older_than_the_maximum_age_has_expired():
    old = claims(iat=int((NOW - auth.SESSION_MAX_AGE - timedelta(seconds=1)).timestamp()))
    with pytest.raises(auth.SessionExpired) as refused:
        auth.identity_from_claims(old, issuer=ISSUER, now=NOW)
    assert str(refused.value) == auth.SESSION_EXPIRED
    signed_in = auth.identity_from_claims(claims(), issuer=ISSUER, now=NOW)
    signed_in.check_age(NOW + auth.SESSION_MAX_AGE - timedelta(minutes=11))
    with pytest.raises(auth.SessionExpired):
        signed_in.check_age(NOW + auth.SESSION_MAX_AGE)


@pytest.mark.parametrize("verified", [False, "true", None, ...])
def test_the_email_is_kept_only_when_the_provider_marked_it_verified(verified):
    assert auth.identity_from_claims(claims(email_verified=verified), issuer=ISSUER, now=NOW).email is None


def test_an_identity_needs_a_mapping_of_claims():
    for value in (None, [], "is_logged_in", 1):
        with pytest.raises(auth.SignInRefused):
            auth.identity_from_claims(value, issuer=ISSUER, now=NOW)


# --- production sign-in configuration -------------------------------------------------------------------------------


def test_a_safe_configuration_passes_and_names_the_issuer():
    assert auth.configuration_failures(section(), ISSUER) == []
    assert auth.configuration_failures(section(client_kwargs={"prompt": "login"}), ISSUER) == []
    assert auth.require_configuration(section(), {"BAEC_AUTH_ISSUER": ISSUER}) == ISSUER


@pytest.mark.parametrize("case,settings,issuer,expected", [
    ("no section", None, ISSUER, "[auth] section"),
    ("empty section", {}, ISSUER, "[auth] section"),
    ("no issuer", section(), None, "BAEC_AUTH_ISSUER is not set"),
    ("http issuer", section(server_metadata_url="http://issuer.baec-test.invalid/.well-known/openid-configuration"),
     "http://issuer.baec-test.invalid", "BAEC_AUTH_ISSUER is not an https URL"),
    ("missing client secret", section(client_secret=...), ISSUER, "client_secret is missing"),
    ("blank client id", section(client_id="  "), ISSUER, "client_id is missing"),
    ("missing redirect", section(redirect_uri=...), ISSUER, "redirect_uri is missing"),
    ("missing cookie secret", section(cookie_secret=...), ISSUER, "cookie_secret is missing"),
    ("missing metadata", section(server_metadata_url=...), ISSUER, "server_metadata_url is missing"),
    ("http redirect", section(redirect_uri="http://console.baec-test.invalid/oauth2callback"), ISSUER,
     "redirect_uri is not an https URL"),
    ("wrong redirect path", section(redirect_uri="https://console.baec-test.invalid/callback"), ISSUER,
     "redirect_uri is not an https URL ending in /oauth2callback"),
    ("redirect with credentials", section(redirect_uri="https://u:p@console.baec-test.invalid/oauth2callback"),
     ISSUER, "redirect_uri is not an https URL"),
    ("short cookie secret", section(cookie_secret="short-secret"), ISSUER, "cookie_secret is shorter than 32"),
    ("metadata of another issuer", section(server_metadata_url="https://other.example.invalid/.well-known/"
                                                                "openid-configuration"), ISSUER,
     "server_metadata_url is not the https discovery document"),
    ("http metadata", section(server_metadata_url="http://issuer.baec-test.invalid/.well-known/openid-configuration"),
     ISSUER, "server_metadata_url is not the https discovery document"),
    ("a second, named provider", section(google={"client_id": "x", "client_secret": "y",
                                                 "server_metadata_url": "https://accounts.example.invalid/"}), ISSUER,
     "named provider section [auth.google]"),
    ("exposed tokens", section(expose_tokens=["id", "access"]), ISSUER, "expose_tokens is not a supported setting"),
])
def test_an_unsafe_or_incomplete_configuration_is_refused_without_its_values(case, settings, issuer, expected):
    failures = auth.configuration_failures(settings, issuer)
    assert any(expected in f for f in failures), failures
    assert not any(SECRET in f or COOKIE in f or "short-secret" in f for f in failures)
    with pytest.raises(auth.AuthConfigurationError) as refused:
        auth.require_configuration(settings, {} if issuer is None else {"BAEC_AUTH_ISSUER": issuer})
    assert str(refused.value) == auth.AUTH_NOT_CONFIGURED and refused.value.failures == failures


# --- the console guard re-checks a sign-in credential ----------------------------------------------------------------


class FakeCredential:
    """Stands in for production.SignedInCredential on SQLite: valid for one (user, organization) until revoked."""

    def __init__(self, user_id: str, organization_id: str) -> None:
        self.user_id, self.organization_id, self.revoked, self.checks = user_id, organization_id, False, 0

    def check(self, principal) -> None:
        self.checks += 1
        if self.revoked or (principal.user_id, principal.organization_id) != (self.user_id, self.organization_id):
            raise auth.SignInRefused(auth.NO_ACCESS)


def _human(root, clock, organization_id="org-a", user_id="user-a", name="Avery (synthetic)"):
    return identity.provision(root / "identity.sqlite3", organization_id=organization_id,
                              organization_name=f"{organization_id} (synthetic)", user_id=user_id, display_name=name,
                              kind=UserKind.HUMAN, now=clock.now())


def test_a_revoked_credential_refuses_the_next_operation_of_a_running_console(tmp_path):
    root, clock = tmp_path / "data", StepClock()
    principal = _human(root, clock)
    credential = FakeCredential("user-a", "org-a")
    console = Console(root, clock, principal=principal, authentication_required=True, credential=credential)
    account_id = console.local.create_account("Signed-in account (synthetic)")
    assert credential.checks > 0
    before = console.local.account_ids()
    credential.revoked = True
    for operation in (console.local.account_ids, lambda: console.local.account_view(account_id), console.activity,
                      lambda: console.local.create_account("Not created (synthetic)"), console.dashboard):
        with pytest.raises(PrincipalInvalid):
            operation()
    credential.revoked = False
    assert console.local.account_ids() == before  # nothing was written while refused


def test_a_credential_for_another_user_refuses_the_console_at_construction(tmp_path):
    root, clock = tmp_path / "data", StepClock()
    principal = _human(root, clock)
    _human(root, clock, "org-b", "user-b", "Blake (synthetic)")
    for credential in (FakeCredential("user-b", "org-b"), FakeCredential("user-a", "org-b")):
        with pytest.raises(identity.IdentityError):
            Console(root, clock, principal=principal, authentication_required=True, credential=credential)


def test_a_signed_in_console_never_seeds_as_the_system_identity_or_renames_its_user(tmp_path):
    root, clock = tmp_path / "data", StepClock()
    principal = _human(root, clock)
    console = Console(root, clock, principal=principal, authentication_required=True,
                      credential=FakeCredential("user-a", "org-a"))
    with pytest.raises(ConsoleError) as refused:
        console.seed_service()
    assert str(refused.value) == SEEDING_UNAVAILABLE
    with pytest.raises(ConsoleError) as refused:
        console.set_actor("Someone else")
    assert str(refused.value) == RENAME_UNAVAILABLE
    with identity.identity_store(root / "identity.sqlite3") as store:
        assert store.user("user-a").display_name == "Avery (synthetic)"
        assert store.user(identity.seed_user_id("org-a")) is None  # no seed user was created


def test_an_attempted_action_does_not_hide_a_lost_session(tmp_path):
    """ui_common.attempt shows refusals of an action inline; a lost session is not one: it propagates, so the app
    stops the whole page instead of rendering more."""
    from baec_console.ui_common import attempt

    def lost():
        raise PrincipalInvalid()
    with pytest.raises(PrincipalInvalid):
        attempt(lost)
