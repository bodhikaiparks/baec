"""V0.4 Phase 5: the deployment configuration and the production images, checked without deploying anything.

Compose (as Docker Compose itself resolves it): separate services and credentials for the web runtime, the release
job, provisioning, backup, and restore; only the proxy publishes ports; the web network is internal; every BAEC
service is read-only and unprivileged. The nginx boundary template, as nginx itself parses it. The web image: pinned,
hash-locked, non-root, no secrets, tests, local data, or MCP server. The backup tool's URL policy. All values synthetic.
"""

from __future__ import annotations

import importlib.util
import json
import re

import pytest

from deploy_support import (DEPLOY, RELEASE, ROOT, compose_config, compose_environment, docker, make_ca,
                            make_certificate, run)

OPERATOR_SECRETS = {"baec_migration_database_url", "baec_provisioning_database_url", "baec_backup_database_url",
                    "baec_restore_database_url"}
OPERATOR_VARIABLES = {"BAEC_MIGRATION_DATABASE_URL", "BAEC_PROVISIONING_DATABASE_URL", "BAEC_BACKUP_DATABASE_URL",
                      "BAEC_RESTORE_DATABASE_URL"}


@pytest.fixture(scope="module")
def environment(tmp_path_factory):
    secrets = tmp_path_factory.mktemp("secrets")
    for name in ("baec_app_database_url", "baec_auth_database_url", "baec_streamlit_auth.toml",
                 "baec_postgres_ca.crt", *OPERATOR_SECRETS):
        (secrets / name).write_text("placeholder (synthetic)\n", encoding="utf-8")
    return compose_environment(BAEC_PUBLIC_HOST="baec.example.invalid", BAEC_PUBLIC_ORIGIN="baec.example.invalid",
                               BAEC_AUTH_ISSUER="https://issuer.example.invalid", BAEC_TLS_DIR=str(secrets),
                               BAEC_SECRETS_DIR=str(secrets), BAEC_BACKUP_STAGING_DIR=str(secrets))


@pytest.fixture(scope="module")
def default(environment) -> dict:
    """What `docker compose up` starts: no profile."""
    return compose_config(environment)


@pytest.fixture(scope="module")
def everything(environment) -> dict:
    return compose_config(environment, profiles=("release", "ops", "restore"))


def _secrets(service: dict) -> set[str]:
    return {s["source"] for s in service.get("secrets", [])}


def test_compose_starts_only_the_proxy_and_the_web_service_by_default(default, everything):
    assert set(default["services"]) == {"proxy", "web"}
    assert set(everything["services"]) == {"proxy", "web", "migrate", "provision", "backup", "restore"}
    for name in ("migrate", "provision", "backup", "restore"):
        assert everything["services"][name]["restart"] == "no"


def test_the_web_service_receives_only_its_own_credentials(everything):
    web = everything["services"]["web"]
    assert _secrets(web) == {"baec_app_database_url", "baec_auth_database_url", "baec_streamlit_auth",
                             "baec_postgres_ca"}
    names = set(web["environment"])
    assert not names & (OPERATOR_VARIABLES | {n + "_FILE" for n in OPERATOR_VARIABLES})
    assert not [n for n in names if n.startswith("ANTHROPIC_")]  # Claude keys are per browser session only
    assert web["environment"]["BAEC_DEPLOYMENT_MODE"] == "production"
    assert web["environment"]["BAEC_DATABASE_URL_FILE"] == "/run/secrets/baec_app_database_url"
    assert not [v for v in web["environment"].values() if "postgresql://" in str(v)]  # never a URL in the environment


@pytest.mark.parametrize("service,secret,variable", [
    ("migrate", "baec_migration_database_url", "BAEC_MIGRATION_DATABASE_URL_FILE"),
    ("provision", "baec_provisioning_database_url", "BAEC_PROVISIONING_DATABASE_URL_FILE"),
    ("backup", "baec_backup_database_url", "BAEC_BACKUP_DATABASE_URL_FILE"),
    ("restore", "baec_restore_database_url", "BAEC_RESTORE_DATABASE_URL_FILE"),
])
def test_each_operator_job_receives_only_its_own_credential(everything, service, secret, variable):
    config = everything["services"][service]
    assert _secrets(config) == {secret, "baec_postgres_ca"}
    assert set(config["environment"]) == {variable}
    assert "ports" not in config


def test_only_the_proxy_publishes_ports_and_the_web_network_is_internal(everything):
    published = {name: service.get("ports", []) for name, service in everything["services"].items()}
    assert {name for name, ports in published.items() if ports} == {"proxy"}
    assert sorted(int(p["target"]) for p in published["proxy"]) == [80, 443]
    assert everything["networks"]["frontend"].get("internal") is True
    assert set(everything["services"]["web"]["networks"]) == {"frontend", "egress"}
    assert set(everything["services"]["proxy"]["networks"]) == {"edge", "frontend"}
    for name in ("migrate", "provision", "backup", "restore"):
        assert "frontend" not in everything["services"][name]["networks"]


def test_every_baec_service_is_read_only_and_unprivileged(everything):
    for name, service in everything["services"].items():
        assert not service.get("privileged"), name
        assert not [v for v in service.get("volumes", []) if "docker.sock" in str(v.get("source", ""))], name
        if name == "proxy":
            continue  # nginx renders its configuration at start; it holds no BAEC credential
        assert service.get("read_only") is True and service.get("cap_drop") == ["ALL"], name
        assert "no-new-privileges:true" in service.get("security_opt", []), name


def test_the_web_health_check_is_readiness_plus_liveness(everything):
    test = " ".join(everything["services"]["web"]["healthcheck"]["test"])
    assert "python -m baec_console.readiness" in test and "/_stcore/health" in test


def test_compose_refuses_to_render_without_its_required_settings(environment):
    incomplete = {k: v for k, v in environment.items() if k != "BAEC_SECRETS_DIR"}
    result = run(docker(), "compose", "--project-directory", str(DEPLOY), "-f", str(DEPLOY / "compose.yaml"),
                 "config", env=incomplete, check=False)
    assert result.returncode != 0 and "BAEC_SECRETS_DIR" in result.stderr


# --- nginx boundary ---------------------------------------------------------------------------------------------------


TEMPLATE = (DEPLOY / "nginx" / "templates" / "baec.conf.template").read_text(encoding="utf-8")


def test_the_boundary_template_states_each_guarantee():
    assert "proxy_cookie_flags ~ secure;" in TEMPLATE
    hsts = [line.strip() for line in TEMPLATE.splitlines() if "add_header Strict-Transport-Security" in line]
    assert hsts == ['add_header Strict-Transport-Security "max-age=${BAEC_HSTS_MAX_AGE}" always;']  # no subdomains,
    # no preload (docs/PRODUCTION_RUNBOOK.md, HSTS)
    assert "proxy_set_header X-Forwarded-Proto https;" in TEMPLATE
    assert "proxy_set_header X-Forwarded-For $remote_addr;" in TEMPLATE  # never appends a client-sent chain
    assert "$proxy_add_x_forwarded_for" not in TEMPLATE and 'proxy_set_header Forwarded "";' in TEMPLATE
    assert "return 308 https://${BAEC_PUBLIC_ORIGIN}$uri;" in TEMPLATE  # a fixed origin, never the query string
    plain = TEMPLATE.split("# Plain HTTP for the BAEC host")[1].split("server {", 2)
    assert "proxy_pass" not in plain[1]  # the HTTP server for the BAEC host never proxies
    assert "ssl_reject_handshake on;" in TEMPLATE and "return 444;" in TEMPLATE
    assert re.search(r"log_format baec_safe .*\$uri", TEMPLATE) and "$request " not in TEMPLATE
    assert "$args" not in TEMPLATE and "$http_cookie" not in TEMPLATE and "$http_authorization" not in TEMPLATE


@pytest.fixture(scope="module")
def proxy_image():
    run(docker(), "build", "-q", "-t", f"baec-proxy:{RELEASE}", str(DEPLOY / "nginx"))
    return f"baec-proxy:{RELEASE}"


def test_nginx_accepts_the_rendered_boundary_configuration(proxy_image, tmp_path):
    ca = make_ca(tmp_path / "ca")
    cert, key = make_certificate(tmp_path, "proxy", ["baec.example.invalid"], ca)
    (tmp_path / "tls").mkdir()
    (tmp_path / "tls" / "fullchain.pem").write_bytes(cert.read_bytes())
    (tmp_path / "tls" / "privkey.pem").write_bytes(key.read_bytes())
    result = run(docker(), "run", "--rm", "--add-host", "web:127.0.0.1",  # nginx resolves its upstream at load
                 "-v", f"{tmp_path / 'tls'}:/etc/nginx/tls:ro",
                 "-e", "BAEC_PUBLIC_HOST=baec.example.invalid", "-e", "BAEC_PUBLIC_ORIGIN=baec.example.invalid",
                 "-e", "BAEC_HSTS_MAX_AGE=86400", proxy_image, "sh", "-c",
                 "/docker-entrypoint.sh nginx -t && cat /etc/nginx/conf.d/baec.conf", check=False)
    assert result.returncode == 0, result.stderr
    assert "test is successful" in result.stderr
    rendered = result.stdout
    assert "server_name baec.example.invalid;" in rendered and 'max-age=86400"' in rendered
    assert "${" not in rendered  # every BAEC_ variable was supplied
    assert "$uri" in rendered and "$http_host" in rendered  # nginx's own variables were left alone


# --- the web image -----------------------------------------------------------------------------------------------------


DOCKERFILE = (DEPLOY / "Dockerfile").read_text(encoding="utf-8")


def test_the_web_image_is_pinned_locked_and_unprivileged_by_construction():
    assert re.search(r"^FROM python:3\.14-slim@sha256:[0-9a-f]{64}$", DOCKERFILE, re.M)
    assert "--require-hashes --no-deps -r /tmp/requirements.lock" in DOCKERFILE and "pip check" in DOCKERFILE
    assert re.search(r"^USER 10001:10001$", DOCKERFILE, re.M) and "HEALTHCHECK" in DOCKERFILE
    copied = [line.split()[1:] for line in DOCKERFILE.splitlines() if line.startswith(("COPY", "ADD"))]
    assert copied == [["deploy/requirements.lock", "/tmp/requirements.lock"], ["baec_app", "/app/baec_app"],
                      ["baec_console", "/app/baec_console"], ["deploy/streamlit/config.toml", "/app/.streamlit/config.toml"]]
    assert "ARG" not in DOCKERFILE and "secret" not in DOCKERFILE.lower().split("from python")[1].split("user")[0]
    ignore = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
    rules = [line for line in ignore if line and not line.startswith("#")]
    assert rules[0] == "*"  # an allowlist
    assert {r for r in rules if r.startswith("!")} == {"!baec_app/", "!baec_console/", "!deploy/requirements.lock",
                                                        "!deploy/streamlit/config.toml"}
    for excluded in ("**/.data/", "**/.env", "**/.streamlit/", "baec_console/tests/", "baec_console/tests_postgres/",
                     "baec_app/mcp/", "baec_app/mcp_write/"):
        assert excluded in rules


def test_the_lock_pins_exactly_the_reviewed_closure_with_hashes():
    pinned = {}
    for line in (DEPLOY / "requirements.in").read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            name, version = line.split("==")
            pinned[name.lower()] = version
    lock = (DEPLOY / "requirements.lock").read_text(encoding="utf-8")
    locked = dict(re.findall(r"^([a-z0-9._-]+)==(\S+) \\$", lock, re.M))
    assert locked == pinned
    blocks = re.split(r"^(?=[a-z0-9._-]+==)", lock, flags=re.M)[1:]
    assert all(re.search(r"--hash=sha256:[0-9a-f]{64}", block) for block in blocks)
    for development_only in ("pytest", "mcp", "pyflakes"):
        assert development_only not in pinned


def test_the_production_streamlit_configuration_hides_errors_and_reads_secrets_from_a_mount():
    config = (DEPLOY / "streamlit" / "config.toml").read_text(encoding="utf-8")
    for line in ('showErrorDetails = "none"', "enableXsrfProtection = true", "enableCORS = true",
                 "gatherUsageStats = false", 'files = ["/run/secrets/baec_streamlit_auth.toml"]',
                 'fileWatcherType = "none"', "developmentMode = false"):
        assert line in config


@pytest.fixture(scope="module")
def web_image():
    run(docker(), "build", "-q", "-f", str(DEPLOY / "Dockerfile"), "-t", f"baec-web:{RELEASE}", str(ROOT))
    return f"baec-web:{RELEASE}"


def _in_image(image: str, script: str) -> str:
    return run(docker(), "run", "--rm", "--read-only", "--tmpfs", "/tmp", "--network", "none", image, "python", "-c",
               script).stdout


def test_the_built_image_runs_as_an_unprivileged_user_with_only_runtime_contents(web_image):
    inspected = json.loads(run(docker(), "image", "inspect", web_image).stdout)[0]["Config"]
    assert inspected["User"] == "10001:10001" and inspected["Healthcheck"]["Test"][0] == "CMD"
    assert "BAEC_DEPLOYMENT_MODE=production" in inspected["Env"]
    assert not [e for e in inspected["Env"] if re.match(r"(BAEC_.*URL|ANTHROPIC_)", e)]
    report = json.loads(_in_image(web_image, """
import json, os, pathlib, importlib.util
app = pathlib.Path('/app')
names = {p.relative_to(app).as_posix() for p in app.rglob('*')}
print(json.dumps({
  'uid': os.getuid(), 'writable_app': os.access('/app', os.W_OK),
  'tests': sorted(n for n in names if '/tests' in n or n.startswith('tests')),
  'local': sorted(n for n in names if '.data' in n or n.endswith(('.sqlite3', '.db', '.env')) or '/.env' in n),
  'secrets_toml': sorted(n for n in names if n.endswith('secrets.toml')),
  'mcp': [m for m in ('mcp', 'baec_app.mcp', 'pytest') if importlib.util.find_spec(m.split('.')[0]) and
          (m != 'baec_app.mcp' or (app / 'baec_app' / 'mcp').exists())],
  'config': (app / '.streamlit' / 'config.toml').exists(),
}))"""))
    assert report == {"uid": 10001, "writable_app": False, "tests": [], "local": [], "secrets_toml": [], "mcp": [],
                      "config": True}


def test_the_built_image_loads_every_production_module(web_image):
    out = _in_image(web_image, "import baec_console.production, baec_console.readiness, baec_console.auth, "
                               "baec_console.ui_workspace, baec_console.ui_crm, baec_console.ui_pages, "
                               "baec_console.crm_ai, baec_console.postgres.migrate, baec_console.postgres.provision, "
                               "baec_console.postgres.gate, streamlit, authlib, psycopg, psycopg_pool; "
                               "print(psycopg.pq.version() >= 160000)")
    assert out.strip() == "True"
    assert run(docker(), "run", "--rm", "--network", "none", web_image, "pip", "check").stdout.strip() == \
        "No broken requirements found."


def test_the_built_image_is_not_ready_without_its_configuration(web_image):
    result = run(docker(), "run", "--rm", "--read-only", "--tmpfs", "/tmp", "--network", "none", web_image,
                 "python", "-m", "baec_console.readiness", check=False)
    assert result.returncode == 1 and result.stdout == "not ready: DATABASE\n" and result.stderr == ""
    leaked = run(docker(), "run", "--rm", "--network", "none", "-e", "BAEC_MIGRATION_DATABASE_URL=x", web_image,
                 "python", "-m", "baec_console.readiness", check=False)
    assert leaked.returncode == 1 and leaked.stdout == "not ready: SECRET_BOUNDARY\n"


# --- Phase 6 security remediation: Authlib 1.9.1, certifi 2026.10.23 (docs/V04_PHASE6_SECURITY_REMEDIATION_REPORT.md)

COMPROMISED_ROOT = "emSign ECC Root CA - G3"  # GHSA-6q48-6c7w-9gw2: intermediates under it have compromised keys


def test_the_built_image_ships_the_remediated_versions_and_no_compromised_root(web_image):
    report = json.loads(_in_image(web_image, f"""
import json, pathlib, ssl, authlib, certifi
site = pathlib.Path(certifi.__file__).parents[1]
bundles = [str(p) for p in site.rglob('cacert.pem')]
system = ssl.get_default_verify_paths().cafile
print(json.dumps({{
  'authlib': authlib.__version__, 'certifi': certifi.__version__, 'bundles': sorted(bundles),
  'compromised': [p for p in bundles + [system] if {COMPROMISED_ROOT!r} in pathlib.Path(p).read_text()],
}}))"""))
    assert report["authlib"] == "1.9.1" and report["certifi"] == "2026.10.23"
    # certifi's own bundle and pip's vendored copy; nothing older left behind
    assert report["bundles"] == ["/usr/local/lib/python3.14/site-packages/certifi/cacert.pem",
                                 "/usr/local/lib/python3.14/site-packages/pip/_vendor/certifi/cacert.pem"]
    assert report["compromised"] == []  # certifi, pip's copy, and the system store the sign-in client uses


def test_authlib_refuses_the_forgeries_its_fixed_vulnerabilities_allowed(web_image):
    # Regression tests on the shipped library, whether or not BAEC's sign-in reaches these APIs: each case was accepted
    # by Authlib 1.8.0 (checked when the remediation was made) and must be refused now.
    report = json.loads(_in_image(web_image, """
import base64, json, warnings
warnings.simplefilter('ignore')
from authlib.jose import JsonWebSignature, OctKey, jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
b64 = lambda raw: base64.urlsafe_b64encode(raw).rstrip(b'=').decode()
def refused(action):
    try:
        action()
    except Exception:
        return True
    return False
payload = b64(b'{"sub": "forged (synthetic)"}')
public = rsa.generate_private_key(public_exponent=65537, key_size=2048).public_key().public_bytes(
    serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
jws = JsonWebSignature()
print(json.dumps({
  # CVE-2026-96760: a JSON-serialized JWS with no signature was returned as verified, with or without a key
  'no signatures, no key': refused(lambda: jws.deserialize_json({'payload': payload, 'signatures': []}, None)),
  'no signatures, a key': refused(lambda: jws.deserialize_json({'payload': payload, 'signatures': []},
                                                              OctKey.import_key('k' * 32))),
  'empty signature': refused(lambda: jws.deserialize_json(
      {'payload': payload, 'protected': b64(b'{"alg": "HS256"}'), 'signature': ''}, None)),
  # CVE-2026-114616: zero-length HMAC key material
  'empty HMAC key (bytes)': refused(lambda: OctKey.import_key(b'')),
  'empty HMAC key (str)': refused(lambda: OctKey.import_key('')),
  'HS256 with an empty key': refused(lambda: jwt.encode({'alg': 'HS256'}, {'sub': 'x'}, b'')),
  # CVE-2026-114615: a public key with a leading prefix accepted as an HMAC secret
  'public key as HMAC secret': refused(lambda: OctKey.import_key(public)),
  'newline-prefixed public key': refused(lambda: OctKey.import_key(b'\\n' + public)),
  'BOM-prefixed public key': refused(lambda: OctKey.import_key(b'\\xef\\xbb\\xbf' + public)),
}))"""))
    assert report == {case: True for case in report} and len(report) == 9


def test_the_sign_in_client_verifies_tls_against_the_system_trust_store(web_image, tmp_path):
    # Streamlit's own OAuth client class does OpenID discovery and fetches the signing keys from a local HTTPS server
    # inside the container (no network). Authlib's HTTP client (httpx2) builds its TLS context from the system store
    # (truststore) unless SSL_CERT_FILE names another, so a throwaway CA is untrusted by default.
    certs = tmp_path / "certs"
    ca = make_ca(certs)
    make_certificate(certs, "server", ["localhost"], ca)
    for path in certs.iterdir():
        path.chmod(0o644)
    script = """
import asyncio, json, os, ssl, sys, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from authlib.integrations import starlette_client
from streamlit.web.server.starlette.starlette_auth_routes import _create_streamlit_oauth_class
class Provider(BaseHTTPRequestHandler):
    def do_GET(self):
        body = {'/.well-known/openid-configuration': {'issuer': 'https://localhost', 'jwks_uri': ORIGIN + '/jwks'},
                '/jwks': {'keys': []}}[self.path]
        raw = json.dumps(body).encode()
        self.send_response(200); self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(raw))); self.end_headers(); self.wfile.write(raw)
    def log_message(self, *args):
        pass
server = ThreadingHTTPServer(('127.0.0.1', 0), Provider)
context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
context.load_cert_chain('/certs/server.crt', '/certs/server.key')
server.socket = context.wrap_socket(server.socket, server_side=True)
threading.Thread(target=server.serve_forever, daemon=True).start()
ORIGIN = 'https://' + sys.argv[1] + ':' + str(server.server_address[1])
oauth = _create_streamlit_oauth_class(starlette_client)()
client = oauth.register('baec', client_id='baec-test (synthetic)', client_secret='synthetic',
                        server_metadata_url=ORIGIN + '/.well-known/openid-configuration')
async def discover():
    metadata = await client.load_server_metadata()
    return metadata['issuer'], await client.fetch_jwk_set()
try:
    print(json.dumps({'ok': list(asyncio.run(discover()))}))
except Exception as error:
    print(json.dumps({'refused': type(error).__name__, 'verify': 'CERTIFICATE_VERIFY_FAILED' in repr(error)}))
"""

    def discover(host: str, *environment: str) -> dict:
        command = [docker(), "run", "--rm", "--read-only", "--tmpfs", "/tmp", "--network", "none",
                   "-v", f"{certs}:/certs:ro"]
        for value in environment:
            command += ["-e", value]
        return json.loads(run(*command, web_image, "python", "-c", script, host).stdout.strip().splitlines()[-1])

    # as deployed: the system store, which does not hold the throwaway CA, so the provider is refused
    assert discover("localhost") == {"refused": "ConnectError", "verify": True}
    # the same client, told to trust the CA: discovery and the key set complete over verified TLS
    assert discover("localhost", "SSL_CERT_FILE=/certs/ca.crt") == {"ok": ["https://localhost", {"keys": []}]}
    # a trusted chain for another name is still refused
    assert discover("127.0.0.1", "SSL_CERT_FILE=/certs/ca.crt") == {"refused": "ConnectError", "verify": True}


# --- the backup tool's policy (no database needed) ---------------------------------------------------------------------


def _backup_tool():
    spec = importlib.util.spec_from_file_location("baec_backup", DEPLOY / "ops" / "baec_backup.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SECRET = "s3cr3t-backup-password"


@pytest.mark.parametrize("query", ["", "?sslmode=require", "?sslmode=verify-ca", "?sslmode=disable",
                                   "?sslmode=verify-full&sslmode=disable", "?sslmode=verify-full&host=/tmp",
                                   "?sslmode=verify-full&sslcert=x"])
def test_the_backup_tool_refuses_any_url_but_verified_tls_without_echoing_it(query):
    tool = _backup_tool()
    with pytest.raises(tool.Refused) as refused:
        tool.libpq_environment(f"postgresql://baec_backup:{SECRET}@db.example.invalid:5432/baec{query}",
                               "BAEC_BACKUP_DATABASE_URL")
    assert SECRET not in str(refused.value) and "BAEC_BACKUP_DATABASE_URL" in str(refused.value)


def test_the_backup_tool_passes_credentials_to_libpq_by_environment_only():
    tool = _backup_tool()
    environment = tool.libpq_environment(
        f"postgresql://baec_backup:{SECRET.replace('-', '%2D')}@db.example.invalid:6543/baec_prod"
        "?sslmode=verify-full&sslrootcert=/run/secrets/baec_postgres_ca.crt", "BAEC_BACKUP_DATABASE_URL")
    assert environment == {"PGHOST": "db.example.invalid", "PGPORT": "6543", "PGUSER": "baec_backup",
                           "PGPASSWORD": SECRET, "PGDATABASE": "baec_prod", "PGSSLMODE": "verify-full",
                           "PGSSLROOTCERT": "/run/secrets/baec_postgres_ca.crt"}


def test_the_backup_tool_reads_its_url_from_one_place_only(tmp_path, capsys):
    tool = _backup_tool()
    secret = tmp_path / "url"
    secret.write_text("postgresql://u:p@h/d?sslmode=verify-full", encoding="utf-8")
    assert tool.main(["backup", "--out", str(tmp_path / "out")],
                     {"BAEC_BACKUP_DATABASE_URL": "x", "BAEC_BACKUP_DATABASE_URL_FILE": str(secret)}) == 1
    assert "not both" in capsys.readouterr().err
    assert tool.main(["backup", "--out", str(tmp_path / "out")], {}) == 1
    assert "BAEC_BACKUP_DATABASE_URL is not set" in capsys.readouterr().err
