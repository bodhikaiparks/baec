"""V0.4 Phase 4: the provider layer, end to end on this machine. Streamlit's own sign-in routes against a local OIDC
provider, then BAEC's production authorization on PostgreSQL.

No external provider or deployment is used. A synthetic OpenID Connect provider runs on 127.0.0.1 (its discovery
document, signing keys, and token endpoint); it signs real RS256 ID tokens. Streamlit's unmodified routes
(/auth/login, /oauth2callback, /auth/logout, with the session middleware Streamlit installs) run the
authorization-code flow, and Authlib validates each ID token (signature against the published key, issuer, audience,
expiry, nonce). The resulting signed cookie is read with the same function Streamlit's websocket uses to build st.user,
and those claims go through auth.identity_from_claims and production.ProductionRuntime.console_for, exactly as the
app does. Nothing in BAEC's code is replaced. (Only the provider's discovery URL is http on 127.0.0.1; production
configuration requires https, which test_auth.py checks. Its issuer is the https test issuer.)

This pins Streamlit 1.65 internals (route names, cookie parser), so a Streamlit upgrade must re-run it.
Synthetic identities only; software tests, not a security assessment.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import pytest

pytest.importorskip("authlib", reason="Streamlit's sign-in needs Authlib (requirements-postgres.txt)")

from joserfc import jwt  # noqa: E402  (installed with Authlib)
from joserfc.jwk import RSAKey  # noqa: E402
from starlette.applications import Starlette  # noqa: E402
from starlette.middleware import Middleware  # noqa: E402
from starlette.middleware.sessions import SessionMiddleware  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402
from streamlit import config  # noqa: E402
from streamlit.auth_util import encode_provider_token  # noqa: E402
from streamlit.runtime.secrets import secrets_singleton  # noqa: E402
from streamlit.web.server.starlette import starlette_app_utils  # noqa: E402
from streamlit.web.server.starlette.starlette_auth_routes import create_auth_routes  # noqa: E402
from streamlit.web.server.starlette.starlette_websocket import _parse_user_cookie_signed  # noqa: E402

from baec_console import auth, production  # noqa: E402
from baec_console.console_service import AccessDenied, PrincipalInvalid  # noqa: E402

from .conftest import TEST_ISSUER, fresh  # noqa: E402
from .test_production_auth import World, provisioner, runtime, lookup, clock  # noqa: E402,F401

ORIGIN = "http://localhost:8501"
CLIENT_ID, CLIENT_SECRET = "baec-test-client", "baec-test-client-secret-" + secrets.token_hex(8)
COOKIE_SECRET = secrets.token_hex(32)
USER_COOKIE = "_streamlit_user"


class Provider:
    """A synthetic OpenID Connect provider: discovery, JWKS, and a single-use authorization-code token endpoint."""

    def __init__(self) -> None:
        self.key = RSAKey.generate_key(2048, parameters={"kid": "baec-test-key", "use": "sig", "alg": "RS256"})
        self.codes: dict[str, dict] = {}
        provider = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args) -> None:
                pass

            def _json(self, status: int, body: dict) -> None:
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self) -> None:
                if self.path == "/.well-known/openid-configuration":
                    self._json(200, provider.metadata)
                elif self.path == "/jwks":
                    self._json(200, {"keys": [provider.key.as_dict(private=False)]})
                else:
                    self._json(404, {})

            def do_POST(self) -> None:
                form = parse_qs(self.rfile.read(int(self.headers["Content-Length"])).decode())
                grant = provider.codes.pop(form.get("code", [""])[0], None)  # single use
                if self.path != "/token" or grant is None:
                    self._json(400, {"error": "invalid_grant"})
                    return
                key = grant.pop("_signing_key", provider.key)
                forge = grant.pop("_forge", None)  # a hand-built token in place of a validly signed one
                token = jwt.encode({"alg": "RS256", "kid": "baec-test-key"}, grant, key) if forge is None else \
                    forge(grant, provider.key)
                self._json(200, {"access_token": secrets.token_hex(8), "token_type": "Bearer", "expires_in": 600,
                                 "id_token": token})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.metadata = {"issuer": TEST_ISSUER, "authorization_endpoint": self.url + "/authorize",
                         "token_endpoint": self.url + "/token", "jwks_uri": self.url + "/jwks",
                         "response_types_supported": ["code"], "subject_types_supported": ["public"],
                         "id_token_signing_alg_values_supported": ["RS256"]}
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def authenticate(self, authorize_url: str, subject: str, **overrides) -> str:
        """The person signs in at the provider: a code for an ID token answering this authorization request."""
        request = {k: v[0] for k, v in parse_qs(urlsplit(authorize_url).query).items()}
        assert authorize_url.startswith(self.metadata["authorization_endpoint"])
        assert request["client_id"] == CLIENT_ID and request["redirect_uri"] == ORIGIN + "/oauth2callback"
        now = int(time.time())
        claims = {"iss": TEST_ISSUER, "sub": subject, "aud": CLIENT_ID, "iat": now, "exp": now + 600,
                  "nonce": request["nonce"], "email": f"{subject}@example.invalid", "email_verified": True,
                  "name": "Name from the provider (synthetic)"}
        claims.update(overrides)
        code = secrets.token_urlsafe(16)
        self.codes[code] = {k: v for k, v in claims.items() if v is not ...}
        return code

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture(scope="module")
def provider():
    opened = Provider()
    yield opened
    opened.close()


@pytest.fixture
def streamlit_auth(provider, tmp_path):
    """Streamlit configured with one default provider (this one), through a secrets file, as a deployment would be."""
    path = tmp_path / "secrets.toml"
    path.write_text(f'[auth]\nredirect_uri = "{ORIGIN}/oauth2callback"\ncookie_secret = "{COOKIE_SECRET}"\n'
                    f'client_id = "{CLIENT_ID}"\nclient_secret = "{CLIENT_SECRET}"\n'
                    f'server_metadata_url = "{provider.url}/.well-known/openid-configuration"\n', encoding="utf-8")
    previous = config.get_option("secrets.files")
    config.set_option("secrets.files", [str(path)])
    secrets_singleton._reset()
    yield
    config.set_option("secrets.files", previous)
    secrets_singleton._reset()


@pytest.fixture
def browser(streamlit_auth):
    app = Starlette(routes=create_auth_routes(""),
                    middleware=[Middleware(SessionMiddleware, secret_key=COOKIE_SECRET, same_site="lax")])
    with TestClient(app, base_url=ORIGIN, follow_redirects=False) as client:
        yield client


@pytest.fixture
def world(provisioner) -> World:  # noqa: F811
    return World(provisioner)


def start_sign_in(browser) -> str:
    response = browser.get("/auth/login", params={"provider": encode_provider_token("default")})
    assert response.status_code == 302
    return response.headers["location"]


def finish_sign_in(browser, provider, subject: str, *, authorize_url: str | None = None, **overrides):
    authorize_url = authorize_url or start_sign_in(browser)
    state = parse_qs(urlsplit(authorize_url).query)["state"][0]
    code = provider.authenticate(authorize_url, subject, **overrides)
    return browser.get("/oauth2callback", params={"code": code, "state": state})


def st_user(browser) -> dict:
    """st.user as Streamlit's websocket would build it from this browser's cookie ({} if none or invalid)."""
    cookie = browser.cookies.get(USER_COOKIE)
    return {} if cookie is None else _parse_user_cookie_signed(cookie, ORIGIN)


def console_for(runtime, clock, claims: dict):  # noqa: F811
    return runtime.console_for(auth.identity_from_claims(claims, issuer=TEST_ISSUER, now=clock.now()))


def test_a_real_sign_in_reaches_the_users_production_console_and_revocation_ends_it(browser, provider, runtime, clock,
                                                                                    world):
    response = finish_sign_in(browser, provider, world.subject_a)
    assert response.status_code == 302 and response.headers["location"] == "/"
    user = st_user(browser)
    assert user["is_logged_in"] is True and user["iss"] == TEST_ISSUER and user["sub"] == world.subject_a
    assert user["provider"] == "default"
    assert "id_token" not in json.dumps(user)  # no token is exposed to the app
    console = console_for(runtime, clock, user)
    assert (console.principal.user_id, console.principal.organization_id) == (world.user_a, world.org_a)
    assert console.actor == "Avery (synthetic)"
    own = console.local.create_account("A's account (synthetic)")
    foreign = console_for(runtime, clock, {**user, "sub": world.subject_b}).local.create_account("B (synthetic)")
    assert console.local.account_ids() == [own]
    with pytest.raises(AccessDenied):
        console.local.account_view(foreign)
    world.provisioner.revoke(world.org_a, TEST_ISSUER, world.subject_a, "operator (synthetic)")
    with pytest.raises(PrincipalInvalid):
        console.local.account_ids()
    with pytest.raises(auth.SignInRefused):
        console_for(runtime, clock, st_user(browser))  # the browser's cookie is still valid; BAEC refuses it


def test_sign_out_removes_the_browsers_sign_in(browser, provider, world):
    finish_sign_in(browser, provider, world.subject_a)
    assert st_user(browser)["is_logged_in"] is True
    response = browser.get("/auth/logout")
    assert response.status_code == 302
    browser.cookies.clear()  # the browser applies the deleting Set-Cookie
    assert USER_COOKIE in response.headers.get("set-cookie", "")
    assert st_user(browser) == {}


def _segment(value) -> str:
    raw = value if isinstance(value, bytes) else json.dumps(value).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _hmac_token(claims: dict, secret: bytes) -> str:
    signing_input = f"{_segment({'alg': 'HS256', 'kid': 'baec-test-key'})}.{_segment(claims)}"
    return f"{signing_input}.{_segment(hmac.new(secret, signing_input.encode(), hashlib.sha256).digest())}"


# V0.4 Phase 6 (Authlib 1.8.0 -> 1.9.1): tokens with no valid signature at all, in the shapes Authlib's fixed
# vulnerabilities accepted (no signature; an HMAC keyed with empty or public-key bytes), through the real callback.
FORGERIES = {
    "unsigned (alg none)": lambda claims, key: f"{_segment({'alg': 'none'})}.{_segment(claims)}.",
    "signature removed": lambda claims, key: jwt.encode({"alg": "RS256", "kid": "baec-test-key"}, claims,
                                                        key).rsplit(".", 1)[0] + ".",
    "HMAC with an empty key": lambda claims, key: _hmac_token(claims, b""),
    "HMAC with the provider's public key": lambda claims, key: _hmac_token(claims, key.as_pem(private=False)),
}


@pytest.mark.parametrize("case,overrides", [
    ("signed by another key", {"_signing_key": RSAKey.generate_key(2048, parameters={"kid": "baec-test-key"})}),
    ("another audience", {"aud": "another-client"}),
    ("another issuer", {"iss": "https://other-issuer.baec-test.invalid"}),
    ("expired", {"iat": int(time.time()) - 7200, "exp": int(time.time()) - 3600}),
    ("replayed nonce", {"nonce": "a-nonce-from-another-request"}),
    ("no nonce", {"nonce": ...}),
    *((case, {"_forge": forge}) for case, forge in FORGERIES.items()),
])
def test_an_id_token_the_provider_did_not_validly_issue_gives_no_sign_in(browser, provider, world, case, overrides):
    response = finish_sign_in(browser, provider, world.subject_a, **overrides)
    assert response.status_code == 302
    assert st_user(browser) == {}  # Authlib refused the token; Streamlit set no identity cookie


def test_a_code_or_callback_cannot_be_replayed(browser, provider, world):
    authorize_url = start_sign_in(browser)
    state = parse_qs(urlsplit(authorize_url).query)["state"][0]
    code = provider.authenticate(authorize_url, world.subject_a)
    browser.get("/oauth2callback", params={"code": code, "state": state})
    assert st_user(browser)["sub"] == world.subject_a
    browser.cookies.clear()
    browser.get("/oauth2callback", params={"code": code, "state": state})
    assert st_user(browser) == {}


def test_a_callback_from_another_browsers_sign_in_is_refused(streamlit_auth, provider, world):
    """Session fixation: a callback carrying a state this browser never started gives it no sign-in."""
    app = Starlette(routes=create_auth_routes(""),
                    middleware=[Middleware(SessionMiddleware, secret_key=COOKIE_SECRET, same_site="lax")])
    with TestClient(app, base_url=ORIGIN, follow_redirects=False) as victim, \
            TestClient(app, base_url=ORIGIN, follow_redirects=False) as attacker:
        authorize_url = start_sign_in(attacker)
        state = parse_qs(urlsplit(authorize_url).query)["state"][0]
        code = provider.authenticate(authorize_url, world.subject_b)
        victim.get("/oauth2callback", params={"code": code, "state": state})
        assert st_user(victim) == {}


def test_a_forged_or_edited_identity_cookie_is_not_a_sign_in(browser, provider, world, runtime, clock):  # noqa: F811
    finish_sign_in(browser, provider, world.subject_a)
    genuine = browser.cookies.get(USER_COOKIE)
    payload = {"iss": TEST_ISSUER, "sub": world.subject_b, "iat": int(time.time()), "is_logged_in": True,
               "provider": "default", "origin": ORIGIN}
    forged = starlette_app_utils.create_signed_value("not-the-cookie-secret-" + "x" * 40, USER_COOKIE,
                                                     json.dumps(payload)).decode()
    assert _parse_user_cookie_signed(forged, ORIGIN) == {}
    edited = genuine.replace(world.subject_a, world.subject_b) if world.subject_a in genuine else genuine[:-2] + "AA"
    assert _parse_user_cookie_signed(edited, ORIGIN) == {}
    assert _parse_user_cookie_signed(genuine, "https://elsewhere.example.invalid") == {}  # another origin
    for claims in ({}, _parse_user_cookie_signed(forged, ORIGIN)):
        with pytest.raises(auth.SignInRefused):
            console_for(runtime, clock, claims)


def test_a_valid_sign_in_that_no_operator_linked_gives_no_access(browser, provider, runtime, clock, world):  # noqa: F811
    finish_sign_in(browser, provider, fresh("never-linked"))
    user = st_user(browser)
    assert user["is_logged_in"] is True
    with pytest.raises(auth.SignInRefused) as refused:
        console_for(runtime, clock, user)
    assert str(refused.value) == auth.NO_ACCESS
    assert production.PRODUCTION_ROOT.exists() is False
