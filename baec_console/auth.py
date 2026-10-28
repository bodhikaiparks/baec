"""Sign-in: what an authentication provider establishes, and nothing more. (V0.4 Phase 4; IMPLEMENTATION.)

The provider is OpenID Connect through Streamlit's built-in sign-in (st.login, st.user; Authlib underneath).
Streamlit runs the authorization-code flow on its own server routes: it checks the state, exchanges the code with
the provider, validates the ID token (signature against the provider's published keys, issuer, audience, expiry,
nonce), and keeps the token's claims in an HttpOnly cookie signed with the configured cookie_secret. A browser cannot
edit that cookie without the secret. BAEC stores no password, token, or provider secret.

What a signed-in session establishes (AuthenticatedIdentity): the issuer, the subject (OIDC Core 1.0: locally unique
and never reassigned within the issuer, so (issuer, subject) is the immutable external identity), the email only when
the provider marked it verified, and when the provider issued the token. It does not establish a BAEC user, an
organization, a role, or a name: those come only from BAEC's own records, through a link an operator provisioned
(postgres/provision.py) and that production.py resolves on every operation. Nothing a browser can send — a query
parameter, a claimed organization or user id, an email domain, a display name, a role claim — is read here.

Rules (IMPLEMENTATION):
  * exactly one provider: Streamlit's default [auth] section, whose issuer is BAEC_AUTH_ISSUER; a session from any
    other issuer or named provider is refused;
  * a session is accepted for SESSION_MAX_AGE after its ID token was issued (Streamlit's cookie lasts 30 days); after
    that the person signs in again;
  * production configuration must use https for the issuer, its discovery document, and the redirect URI, a cookie
    secret of at least 32 characters, and must not expose provider tokens to the app.

The Streamlit readers (session_claims, streamlit_auth_section, begin_sign_in, sign_out) are the only place st.user
or st.secrets is read. Tests replace them in-process; nothing in a deployment can.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Mapping

from .deployment import DeploymentError
from .identity import IdentityError

ISSUER_VARIABLE = "BAEC_AUTH_ISSUER"
DEFAULT_PROVIDER = "default"
SESSION_MAX_AGE = timedelta(hours=12)
MAX_SUBJECT_LENGTH = 255
MAX_ISSUER_LENGTH = 2048
MIN_COOKIE_SECRET_LENGTH = 32
REQUIRED_SETTINGS = ("redirect_uri", "cookie_secret", "client_id", "client_secret", "server_metadata_url")
# Settings Streamlit reads from the default [auth] section besides the required ones. Anything else is a named
# provider section or an unknown setting, and is refused (one provider only).
OPTIONAL_SETTINGS = ("client_kwargs",)

NO_ACCESS = ("This sign-in does not give access to BAEC Console. Ask your BAEC administrator. "
             "Nothing was read or changed.")
SESSION_EXPIRED = "Your sign-in has expired. Sign out, then sign in again. Nothing was read or changed."
AUTH_NOT_CONFIGURED = ("Sign-in is not configured correctly for production, so BAEC Console is closed. "
                       "Nothing was read or changed.")


class SignInRefused(IdentityError):
    """A provider session does not give access. The message never says which check failed."""


class SessionExpired(SignInRefused):
    """The provider session is older than SESSION_MAX_AGE."""


class AuthConfigurationError(DeploymentError):
    """Production sign-in configuration is missing or unsafe. `failures` names settings, never their values."""

    def __init__(self, failures: list[str]) -> None:
        self.failures = failures
        super().__init__(AUTH_NOT_CONFIGURED)


@dataclass(frozen=True)
class AuthenticatedIdentity:
    """What a verified provider session establishes. Not a principal and not an authorization."""

    issuer: str
    subject: str
    email: str | None  # only when the provider marked it verified; never used for authorization
    issued_at: datetime

    @property
    def key(self) -> tuple[str, str]:
        """The immutable external identity."""
        return (self.issuer, self.subject)

    def check_age(self, now: datetime) -> None:
        if not self.issued_at <= now + timedelta(minutes=5) or now - self.issued_at > SESSION_MAX_AGE:
            raise SessionExpired(SESSION_EXPIRED)


def _text(value, limit: int) -> str | None:
    if not isinstance(value, str) or not value or len(value) > limit:
        return None
    if any(not "\x21" <= c <= "\x7e" for c in value):  # printable ASCII, no space (OIDC sub; issuer URLs)
        return None
    return value


def identity_from_claims(claims: Mapping, *, issuer: str, now: datetime) -> AuthenticatedIdentity:
    """The identity a Streamlit-verified session establishes, or SignInRefused. `claims` is st.user as a dict."""
    if not isinstance(claims, Mapping) or claims.get("is_logged_in") is not True:
        raise SignInRefused(NO_ACCESS)
    if claims.get("provider") != DEFAULT_PROVIDER or claims.get("iss") != issuer:
        raise SignInRefused(NO_ACCESS)
    subject = _text(claims.get("sub"), MAX_SUBJECT_LENGTH)
    issued = claims.get("iat")
    if subject is None or isinstance(issued, bool) or not isinstance(issued, (int, float)):
        raise SignInRefused(NO_ACCESS)
    try:
        issued_at = datetime.fromtimestamp(issued, timezone.utc)
    except (OverflowError, OSError, ValueError):
        raise SignInRefused(NO_ACCESS) from None
    email = claims.get("email") if claims.get("email_verified") is True and isinstance(claims.get("email"), str) \
        else None
    identity = AuthenticatedIdentity(issuer, subject, email, issued_at)
    identity.check_age(now)
    return identity


def configured_issuer(environ: Mapping[str, str] | None = None) -> str | None:
    value = (os.environ if environ is None else environ).get(ISSUER_VARIABLE)
    return value if _text(value, MAX_ISSUER_LENGTH) else None


def _https_path(value) -> str | None:
    """The path of an https URL with a host and no credentials, query, or fragment; otherwise None. (No URL library:
    console modules import none, test_boundaries.py.)"""
    if _text(value, MAX_ISSUER_LENGTH) is None or not value.startswith("https://"):
        return None
    host, _, path = value[len("https://"):].partition("/")
    if not host or "@" in host or "?" in value or "#" in value:
        return None
    return "/" + path


def _https(value) -> bool:
    return _https_path(value) is not None


def configuration_failures(section: Mapping | None, issuer: str | None) -> list[str]:
    """What is missing or unsafe in the production sign-in configuration: Streamlit's [auth] secrets section and
    BAEC_AUTH_ISSUER. Names settings only, never their values."""
    failures = []
    if issuer is None:
        failures.append(f"{ISSUER_VARIABLE} is not set to an issuer URL")
    elif not _https(issuer):
        failures.append(f"{ISSUER_VARIABLE} is not an https URL")
    if not isinstance(section, Mapping) or not section:
        return failures + ["the [auth] section of Streamlit's secrets is missing"]
    for name in REQUIRED_SETTINGS:
        if not isinstance(section.get(name), str) or not section.get(name).strip():
            failures.append(f"[auth] {name} is missing")
    for name in sorted(set(section) - set(REQUIRED_SETTINGS) - set(OPTIONAL_SETTINGS)):
        if isinstance(section.get(name), Mapping):
            failures.append(f"[auth] has a named provider section [auth.{name}]; exactly one provider is allowed")
        else:
            failures.append(f"[auth] {name} is not a supported setting")
    if isinstance(section.get("redirect_uri"), str) and section["redirect_uri"].strip():
        if not (_https_path(section["redirect_uri"]) or "").endswith("/oauth2callback"):
            failures.append("[auth] redirect_uri is not an https URL ending in /oauth2callback")
    if isinstance(section.get("cookie_secret"), str) and \
            0 < len(section["cookie_secret"].strip()) < MIN_COOKIE_SECRET_LENGTH:
        failures.append(f"[auth] cookie_secret is shorter than {MIN_COOKIE_SECRET_LENGTH} characters")
    metadata = section.get("server_metadata_url")
    if isinstance(metadata, str) and metadata.strip() and issuer is not None:
        if not _https(metadata) or metadata != issuer.rstrip("/") + "/.well-known/openid-configuration":
            failures.append(f"[auth] server_metadata_url is not the https discovery document of {ISSUER_VARIABLE}")
    return failures


def require_configuration(section: Mapping | None, environ: Mapping[str, str] | None = None) -> str:
    """The configured issuer, if production sign-in is configured safely; otherwise AuthConfigurationError."""
    issuer = configured_issuer(environ)
    failures = configuration_failures(section, issuer)
    if failures:
        raise AuthConfigurationError(failures)
    return issuer


# --- Streamlit readers ------------------------------------------------------------------------------------------------


def session_claims() -> dict:
    """The current browser session's verified claims (st.user), or {} when Streamlit has none."""
    import streamlit as st

    try:
        return dict(st.user.to_dict())
    except Exception:  # no sign-in configured or no session context: no claims, so no access
        return {}


def streamlit_auth_section() -> dict | None:
    """Streamlit's [auth] secrets section, or None. Only passed to configuration_failures; never shown or logged."""
    import streamlit as st

    try:
        section = st.secrets.get("auth")
    except Exception:  # no secrets file
        return None
    return None if section is None else _plain(section)


def _plain(value):
    return {k: _plain(v) for k, v in value.items()} if isinstance(value, Mapping) else value


def begin_sign_in() -> None:
    import streamlit as st

    st.login()


def sign_out() -> None:
    import streamlit as st

    st.logout()
