"""V0.4 Phase 5: the deployed HTTPS boundary, end to end, on this machine. (IMPLEMENTATION; nothing external.)

The production compose file (deploy/compose.yaml) with the smoke overlay: the real proxy image, the real web image
(non-root, read-only filesystem), the release job, the provisioning job, the disposable TLS-only test PostgreSQL, and
a synthetic OpenID Connect provider. The test acts as the browser. It checks what a browser would actually receive:

  * plain HTTP: only a redirect to HTTPS, never content, a cookie, or a proxied request;
  * HTTPS: HSTS and the other boundary headers; an unknown TLS name is refused;
  * the sign-in cookies as the browser receives them after a real authorization-code sign-in through Streamlit's own
    routes behind the proxy: Secure, HttpOnly where the framework sets it, SameSite=Lax;
  * that sign-in, decoded inside the web container, opens the BAEC console of the user the provisioning job linked;
  * the web container's credentials, user, and filesystem; readiness; the WebSocket through the proxy; the logs.

Everything is synthetic and bound to 127.0.0.1. Software tests of this configuration, not a penetration test.
"""

from __future__ import annotations

import json
import os
import secrets
import shutil
import socket
import ssl
import time
import uuid
import warnings
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from deploy_support import DEPLOY, compose_environment, docker, make_ca, make_certificate, run

psycopg = pytest.importorskip("psycopg", reason="the smoke test prepares its database with psycopg")
httpx = pytest.importorskip("httpx")
certifi = pytest.importorskip("certifi")

HTTP, HTTPS, OIDC = 18080, 18443, 19443
ORIGIN = f"localhost:{HTTPS}"
ISSUER = "https://oidc.baec-smoke.test:9443"
PROJECT = "baec-smoke"
CA_INSIDE = "/run/secrets/baec_postgres_ca.crt"
HSTS = "max-age=86400"


def _test_url(role: str) -> str:
    value = os.environ.get(f"BAEC_TEST_POSTGRES_{role.upper()}_URL")
    if not value:
        pytest.fail('the smoke test needs the test database: eval "$(scripts/postgres_test_db.sh env)"')
    return value


def _inside(url: str, database: str) -> str:
    """A test-database URL as the smoke containers reach it: by name, verified TLS, the CA as a mounted secret."""
    parts = urlsplit(url)
    return (f"postgresql://{parts.username}:{parts.password}@baec-postgres-test:5432/{database}"
            f"?sslmode=verify-full&sslrootcert={CA_INSIDE}")


def _with_database(url: str, database: str) -> str:
    parts = urlsplit(url)
    return parts._replace(path=f"/{database}").geturl()


class Stack:
    def __init__(self, work, environment, database, subject, secret_values):
        self.work, self.environment, self.database = work, environment, database
        self.subject, self.secret_values = subject, secret_values
        self.ca = str(work / "ca" / "ca.crt")
        self.compose = [docker(), "compose", "-p", PROJECT, "--project-directory", str(DEPLOY),
                        "-f", str(DEPLOY / "compose.yaml"), "-f", str(DEPLOY / "tests" / "compose.smoke.yaml")]

    def __call__(self, *args, check=True, timeout=900):
        return run(*self.compose, *args, env=self.environment, check=check, timeout=timeout)

    def web(self, *command, check=True):
        return self("exec", "-T", "web", *command, check=check)

    def client(self) -> httpx.Client:
        return httpx.Client(verify=ssl.create_default_context(cafile=self.ca), follow_redirects=False, timeout=30)


@pytest.fixture(scope="module")
def stack(tmp_path_factory):
    work = tmp_path_factory.mktemp("smoke")
    ca = make_ca(work / "ca")
    tls, oidc_tls, secrets_dir = work / "tls", work / "oidc", work / "secrets"
    for directory in (tls, oidc_tls, secrets_dir, work / "backups"):
        directory.mkdir()
    cert, key = make_certificate(work / "ca", "proxy", ["localhost", "127.0.0.1"], ca)
    shutil.copy(cert, tls / "fullchain.pem")
    shutil.copy(key, tls / "privkey.pem")
    cert, key = make_certificate(work / "ca", "oidc", ["oidc.baec-smoke.test", "localhost"], ca)
    shutil.copy(cert, oidc_tls / "oidc.crt")
    shutil.copy(key, oidc_tls / "oidc.key")

    admin = _test_url("admin")
    database = f"baec_smoke_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(admin, autocommit=True) as db:
        db.execute(f"CREATE DATABASE {database}")
        db.execute(f"REVOKE ALL ON DATABASE {database} FROM PUBLIC")
        db.execute(f"GRANT CONNECT, CREATE ON DATABASE {database} TO baec_migrator")
        db.execute(f"GRANT CONNECT ON DATABASE {database} TO baec_app, baec_auth_lookup, baec_provisioner")
    with psycopg.connect(_with_database(admin, database), autocommit=True) as db:
        db.execute("REVOKE ALL ON SCHEMA public FROM PUBLIC")

    client_secret, cookie_secret = secrets.token_urlsafe(24), secrets.token_urlsafe(36)
    files = {
        "baec_app_database_url": _inside(_test_url("app"), database),
        "baec_auth_database_url": _inside(_test_url("auth"), database),
        "baec_migration_database_url": _inside(_test_url("migrator"), database),
        "baec_provisioning_database_url": _inside(_test_url("provisioner"), database),
        "baec_backup_database_url": "unused in the smoke test",
        "baec_restore_database_url": "unused in the smoke test",
        "baec_streamlit_auth.toml": (f'[auth]\nredirect_uri = "https://{ORIGIN}/oauth2callback"\n'
                                     f'cookie_secret = "{cookie_secret}"\nclient_id = "baec-smoke-client"\n'
                                     f'client_secret = "{client_secret}"\n'
                                     f'server_metadata_url = "{ISSUER}/.well-known/openid-configuration"\n'),
        "baec_postgres_ca.crt": Path(dict(p.split("=", 1) for p in urlsplit(admin).query.split("&"))["sslrootcert"]
                                     ).read_text(encoding="utf-8"),
        "smoke_ca_bundle.pem": Path(certifi.where()).read_text(encoding="utf-8") + ca[0].read_text(encoding="utf-8"),
    }
    for name, value in files.items():
        (secrets_dir / name).write_text(value, encoding="utf-8")
    subject = f"smoke-subject-{uuid.uuid4().hex[:8]}"
    environment = compose_environment(
        BAEC_PUBLIC_HOST="localhost", BAEC_PUBLIC_ORIGIN=ORIGIN, BAEC_AUTH_ISSUER=ISSUER, BAEC_TLS_DIR=str(tls),
        BAEC_SECRETS_DIR=str(secrets_dir), BAEC_BACKUP_STAGING_DIR=str(work / "backups"),
        SMOKE_CLIENT_ID="baec-smoke-client", SMOKE_CLIENT_SECRET=client_secret, SMOKE_SUBJECT=subject,
        SMOKE_OIDC_TLS_DIR=str(oidc_tls), SMOKE_HTTP_PORT=str(HTTP), SMOKE_HTTPS_PORT=str(HTTPS),
        SMOKE_OIDC_PORT=str(OIDC))
    secret_values = [client_secret, cookie_secret] + [urlsplit(_test_url(r)).password
                                                      for r in ("app", "auth", "migrator", "provisioner", "admin")]
    opened = Stack(work, environment, database, subject, secret_values)
    try:
        opened("build", "web", "proxy")
        opened.release = opened("--profile", "release", "run", "--rm", "migrate")
        opened("up", "-d", "--wait", "--wait-timeout", "240", "proxy", "web", "oidc")
        yield opened
    finally:
        opened.logs = opened("logs", "--no-color", check=False).stdout
        opened("--profile", "release", "--profile", "ops", "down", "--volumes", "--remove-orphans", check=False)
        with psycopg.connect(admin, autocommit=True) as db:
            db.execute(f"DROP DATABASE IF EXISTS {database} WITH (FORCE)")


def _cookies(response) -> dict[str, dict]:
    """Set-Cookie headers as the browser receives them: name -> {value, attributes (lowercase)}."""
    found = {}
    for header in response.headers.get_list("set-cookie"):
        first, *attributes = [part.strip() for part in header.split(";")]
        name, _, value = first.partition("=")
        found[name] = {"value": value, **{a.partition("=")[0].lower(): a.partition("=")[2] for a in attributes}}
    return found


def _sign_in(stack: Stack) -> tuple[dict, list]:
    """A real authorization-code sign-in through the proxy. Returns every cookie set on the way, and the responses."""
    token = stack.web("python", "-c", "from streamlit.auth_util import encode_provider_token; "
                                      "print(encode_provider_token('default'))").stdout.strip()
    cookies, responses = {}, []
    with stack.client() as browser:
        def send(url: str):
            jar = "; ".join(f"{k}={v['value']}" for k, v in cookies.items() if v["value"])
            response = browser.get(url, headers={"Cookie": jar} if jar and url.startswith(f"https://{ORIGIN}") else {})
            responses.append(response)
            if url.startswith(f"https://{ORIGIN}"):
                cookies.update(_cookies(response))
            return response
        login = send(f"https://{ORIGIN}/auth/login?provider={token}")
        assert login.status_code == 302 and login.headers["location"].startswith(ISSUER + "/authorize?")
        authorized = send(login.headers["location"].replace("https://oidc.baec-smoke.test:9443",
                                                            f"https://localhost:{OIDC}"))
        assert authorized.status_code == 302
        callback = authorized.headers["location"]
        assert callback.startswith(f"https://{ORIGIN}/oauth2callback?code=")
        finished = send(callback)
        assert finished.status_code == 302 and finished.headers["location"] == "/", finished.text[:200]
    return cookies, responses


@pytest.fixture(scope="module")
def signed_in(stack):
    return _sign_in(stack)


# --- plain HTTP --------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("method,path", [("GET", "/"), ("HEAD", "/"), ("POST", "/"), ("GET", "/_stcore/health"),
                                         ("GET", "/auth/login"), ("GET", "/_stcore/stream"),
                                         ("GET", "/oauth2callback?code=PLAINCODE&state=PLAINSTATE")])
def test_plain_http_serves_only_a_redirect_to_https(stack, method, path):
    response = httpx.request(method, f"http://localhost:{HTTP}{path}", follow_redirects=False, timeout=10,
                             headers={"Cookie": "_streamlit_user=forged"})
    assert response.status_code == 308
    assert response.headers["location"] == f"https://{ORIGIN}{path.split('?')[0]}"  # never the query (an OAuth code)
    assert "set-cookie" not in response.headers and "strict-transport-security" not in response.headers
    assert "streamlit" not in response.text.lower() and "baec" not in response.text.lower()


def test_plain_http_for_any_other_host_gets_no_answer(stack):
    with pytest.raises(httpx.RemoteProtocolError):
        httpx.get(f"http://127.0.0.1:{HTTP}/", headers={"Host": "attacker.example"}, timeout=10)


def test_the_web_service_is_not_reachable_except_through_the_proxy(stack):
    output = stack("ps", "--format", "json").stdout.strip()
    containers = json.loads(output) if output.startswith("[") else [json.loads(line) for line in output.splitlines()]
    published = {c["Service"]: sorted({(p["URL"], p["PublishedPort"]) for p in c.get("Publishers") or []
                                       if p.get("PublishedPort")}) for c in containers}
    assert published["web"] == []
    assert published["proxy"] == [("127.0.0.1", HTTP), ("127.0.0.1", HTTPS)]
    web = json.loads(run(docker(), "inspect", next(c["Name"] for c in containers if c["Service"] == "web")).stdout)[0]
    assert not web["HostConfig"]["PortBindings"] and not any(web["NetworkSettings"]["Ports"].values())
    networks = web["NetworkSettings"]["Networks"]
    assert any(name.endswith("_frontend") for name in networks) and not any(n.endswith("_edge") for n in networks)


# --- HTTPS -------------------------------------------------------------------------------------------------------------


def test_https_responses_carry_hsts_and_the_boundary_headers(stack):
    with stack.client() as browser:
        for path in ("/", "/_stcore/health", "/no-such-page"):
            response = browser.get(f"https://{ORIGIN}{path}")
            assert response.headers["strict-transport-security"] == HSTS  # no includeSubDomains, no preload
            assert response.headers["x-content-type-options"] == "nosniff"
            assert response.headers["referrer-policy"] == "no-referrer"
            assert response.headers["content-security-policy"] == "frame-ancestors 'self'"
            assert response.headers["server"] == "nginx"  # no version
        assert browser.get(f"https://{ORIGIN}/_stcore/health").text == "ok"  # liveness needs no sign-in


def test_tls_is_refused_for_a_name_the_deployment_does_not_serve(stack):
    context = ssl.create_default_context(cafile=stack.ca)
    context.check_hostname = False
    with socket.create_connection(("127.0.0.1", HTTPS), timeout=5) as raw:
        with pytest.raises(ssl.SSLError):
            context.wrap_socket(raw, server_hostname="attacker.example")
    with socket.create_connection(("127.0.0.1", HTTPS), timeout=5) as raw:  # the served name: TLS 1.2 or 1.3
        with context.wrap_socket(raw, server_hostname="localhost") as tls:
            assert tls.version() in ("TLSv1.2", "TLSv1.3")


def test_old_tls_versions_are_refused(stack):
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.load_verify_locations(stack.ca)
    with warnings.catch_warnings():  # Python deprecates offering TLS 1.1 at all; offering it is the point here
        warnings.simplefilter("ignore", DeprecationWarning)
        context.minimum_version = context.maximum_version = ssl.TLSVersion.TLSv1_1
    with socket.create_connection(("127.0.0.1", HTTPS), timeout=5) as raw:
        with pytest.raises(ssl.SSLError):
            context.wrap_socket(raw, server_hostname="localhost")


# --- the sign-in cookie, as the browser receives it ----------------------------------------------------------------


def test_the_sign_in_cookie_reaches_the_browser_secure_httponly_and_samesite_lax(signed_in):
    cookies, responses = signed_in
    user = cookies["_streamlit_user"]
    assert user["value"] and "secure" in user and "httponly" in user
    assert user["samesite"].lower() == "lax" and user["path"] == "/" and user["max-age"] == str(30 * 24 * 3600)
    for name in ("_streamlit_user_tokens", "_streamlit_session"):  # provider tokens; the OAuth state session
        if name in cookies and cookies[name]["value"]:
            assert "secure" in cookies[name] and "httponly" in cookies[name], name
    every_cookie = [c for r in responses if str(r.url).startswith(f"https://{ORIGIN}")
                    for c in r.headers.get_list("set-cookie")]
    assert every_cookie and all("; secure" in c.lower() for c in every_cookie)  # every one, including deletions
    for response in responses:
        if str(response.url).startswith(f"https://{ORIGIN}"):
            assert response.headers["strict-transport-security"] == HSTS


def test_the_xsrf_cookie_is_secure_and_left_readable_by_streamlits_own_page(stack):
    with stack.client() as browser:  # Streamlit sets it on the health response its page fetches first
        cookies = _cookies(browser.get(f"https://{ORIGIN}/_stcore/health"))
    assert "secure" in cookies["_streamlit_xsrf"] and "httponly" not in cookies["_streamlit_xsrf"]
    assert cookies["_streamlit_xsrf"]["samesite"].lower() == "lax"


def test_the_signed_in_cookie_opens_the_provisioned_users_console_inside_the_web_container(stack, signed_in):
    """The documented provisioning flow, through the deployed jobs: the provisioning job (and only it) links the
    synthetic subject to a HUMAN user; the web container, holding only its own credentials, turns the browser's cookie
    into that user's console."""
    organization, user = f"org-smoke-{uuid.uuid4().hex[:6]}", f"user-smoke-{uuid.uuid4().hex[:6]}"
    for argv in (["create-organization", "--organization", organization, "--name", "Smoke organization (synthetic)"],
                 ["create-user", "--organization", organization, "--user", user, "--name", "Sky (synthetic)"],
                 ["link", "--organization", organization, "--user", user, "--issuer", ISSUER, "--subject",
                  stack.subject, "--operator", "smoke test (synthetic)"]):
        assert stack("--profile", "ops", "run", "--rm", "provision", *argv).stdout.strip().endswith(": done")
    cookies, _ = signed_in
    script = (
        "import json, sys\n"
        "from baec_console import auth, production\n"
        "from streamlit.web.server.starlette.starlette_websocket import _parse_user_cookie_signed\n"
        f"claims = _parse_user_cookie_signed(sys.stdin.read().strip(), 'https://{ORIGIN}')\n"
        "runtime = production.open_runtime(auth.streamlit_auth_section())\n"
        "try:\n"
        "    console = runtime.console_for(auth.identity_from_claims(claims, issuer=runtime.issuer,\n"
        "                                                            now=runtime.clock.now()))\n"
        "    print(json.dumps([console.principal.user_id, console.principal.organization_id, console.actor,\n"
        "                      console.local.account_ids(), sorted(claims)]))\n"
        "finally:\n"
        "    runtime.close()\n")
    result = run(*stack.compose, "exec", "-T", "web", "python", "-c", script, env=stack.environment,
                 input=cookies["_streamlit_user"]["value"])
    user_id, organization_id, actor, accounts, claim_names = json.loads(result.stdout)
    assert (user_id, organization_id, actor, accounts) == (user, organization, "Sky (synthetic)", [])
    assert "id_token" not in claim_names and "access_token" not in claim_names


def test_the_websocket_works_through_the_proxy_only_for_the_deployments_origin(stack):
    import websockets.sync.client as ws

    with stack.client() as browser:
        xsrf = _cookies(browser.get(f"https://{ORIGIN}/_stcore/health"))["_streamlit_xsrf"]["value"]
    context = ssl.create_default_context(cafile=stack.ca)
    headers = {"Cookie": f"_streamlit_xsrf={xsrf}"}
    with ws.connect(f"wss://{ORIGIN}/_stcore/stream", ssl=context, origin=f"https://{ORIGIN}",
                    additional_headers=headers, subprotocols=["streamlit", xsrf], open_timeout=10) as connection:
        assert connection.subprotocol == "streamlit"
    with pytest.raises(Exception) as refused:  # another site's page cannot open the session
        with ws.connect(f"wss://{ORIGIN}/_stcore/stream", ssl=context, origin="https://attacker.example",
                        additional_headers=headers, subprotocols=["streamlit", xsrf], open_timeout=10):
            pass
    assert "403" in str(refused.value) or "rejected" in str(refused.value).lower()


# --- the web container -------------------------------------------------------------------------------------------------


def test_the_release_job_migrated_and_verified_the_schema(stack):
    assert stack.release.stdout.strip() == "applied migrations: [1, 2, 3, 4, 5, 6]; schema verified"
    again = stack("--profile", "release", "run", "--rm", "migrate")
    assert again.stdout.strip() == "schema is current; nothing applied"


def test_the_web_container_holds_only_its_own_credentials(stack):
    names = {line.split("=", 1)[0] for line in stack.web("env").stdout.splitlines()}
    assert not {n for n in names if "MIGRATION" in n or "PROVISIONING" in n or "BACKUP" in n or "RESTORE" in n}
    assert not {n for n in names if n.startswith("ANTHROPIC_")}
    assert set(stack.web("ls", "/run/secrets").stdout.split()) == {
        "baec_app_database_url", "baec_auth_database_url", "baec_streamlit_auth.toml", "baec_postgres_ca.crt",
        "smoke_ca_bundle.pem"}
    assert stack.web("id", "-u").stdout.strip() == "10001"
    assert stack.web("touch", "/app/x", check=False).returncode != 0  # read-only root filesystem
    assert stack.web("python", "-m", "baec_console.readiness").stdout == "ready\n"


def test_the_logs_contain_no_credential_code_or_cookie(stack, signed_in):
    cookies, responses = signed_in
    logs = stack("logs", "--no-color", "proxy", "web").stdout
    assert "/oauth2callback" in logs  # the callback was logged, without its query
    callback = next(str(r.url) for r in responses if "/oauth2callback?" in str(r.url))
    code, state = (parse_qs(urlsplit(callback).query)[k][0] for k in ("code", "state"))
    for value in [code, state, cookies["_streamlit_user"]["value"], *stack.secret_values]:
        assert value not in logs
    assert "PLAINCODE" not in logs and "PLAINSTATE" not in logs


def test_readiness_turns_unready_when_the_database_stops_being_safe(stack):
    """The health check follows the database: a schema altered after start makes readiness fail (the orchestrator
    then stops routing to, or restarts, the web service; runbook), and restoring it makes it pass again."""
    admin = _with_database(_test_url("admin"), stack.database)
    with psycopg.connect(admin, autocommit=True) as db:
        db.execute("ALTER TABLE baec.crm_tasks NO FORCE ROW LEVEL SECURITY")
        try:
            result = stack.web("python", "-m", "baec_console.readiness", check=False)
            assert (result.returncode, result.stdout) == (1, "not ready: DATABASE\n")
        finally:
            db.execute("ALTER TABLE baec.crm_tasks FORCE ROW LEVEL SECURITY")
    deadline = time.monotonic() + 30
    while stack.web("python", "-m", "baec_console.readiness", check=False).returncode != 0:
        assert time.monotonic() < deadline
        time.sleep(1)
