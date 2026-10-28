"""A synthetic OpenID Connect provider for the deployment smoke test only. (V0.4 Phase 5; never deployed.)

Serves HTTPS with a certificate from the smoke test's own CA: discovery, signing keys (RS256), an authorization
endpoint that signs in one synthetic subject without a login form, and a single-use authorization-code token
endpoint. It runs inside the smoke stack (from the web image, which has joserfc through Authlib) so Streamlit's real
sign-in routes, behind the real HTTPS proxy, complete the authorization-code flow. Settings come from SMOKE_*
variables; every value is synthetic.
"""

from __future__ import annotations

import base64
import json
import os
import secrets
import ssl
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlencode, urlsplit

from joserfc import jwt
from joserfc.jwk import RSAKey

ISSUER = os.environ["SMOKE_ISSUER"]
CLIENT_ID = os.environ["SMOKE_CLIENT_ID"]
CLIENT_SECRET = os.environ["SMOKE_CLIENT_SECRET"]
REDIRECT_URI = os.environ["SMOKE_REDIRECT_URI"]
SUBJECT = os.environ["SMOKE_SUBJECT"]
KEY = RSAKey.generate_key(2048, parameters={"kid": "smoke-key", "use": "sig", "alg": "RS256"})
CODES: dict[str, dict] = {}
METADATA = {"issuer": ISSUER, "authorization_endpoint": ISSUER + "/authorize", "token_endpoint": ISSUER + "/token",
            "jwks_uri": ISSUER + "/jwks", "response_types_supported": ["code"], "subject_types_supported": ["public"],
            "id_token_signing_alg_values_supported": ["RS256"], "scopes_supported": ["openid", "email", "profile"],
            "token_endpoint_auth_methods_supported": ["client_secret_basic", "client_secret_post"]}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args) -> None:  # the smoke test checks the logs it controls; this one writes none
        pass

    def _send(self, status: int, body: dict | None = None, location: str | None = None) -> None:
        data = json.dumps(body or {}).encode()
        self.send_response(status)
        if location:
            self.send_header("Location", location)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        url = urlsplit(self.path)
        if url.path == "/.well-known/openid-configuration":
            self._send(200, METADATA)
        elif url.path == "/jwks":
            self._send(200, {"keys": [KEY.as_dict(private=False)]})
        elif url.path == "/authorize":
            request = {k: v[0] for k, v in parse_qs(url.query).items()}
            if request.get("client_id") != CLIENT_ID or request.get("redirect_uri") != REDIRECT_URI:
                self._send(400, {"error": "invalid_request"})
                return
            now = int(time.time())
            code = secrets.token_urlsafe(24)
            CODES[code] = {"iss": ISSUER, "sub": SUBJECT, "aud": CLIENT_ID, "iat": now, "exp": now + 600,
                           "nonce": request.get("nonce"), "email": "smoke@example.invalid", "email_verified": True}
            self._send(302, location=REDIRECT_URI + "?" + urlencode({"code": code, "state": request.get("state", "")}))
        else:
            self._send(404)

    def do_POST(self) -> None:
        form = {k: v[0] for k, v in parse_qs(self.rfile.read(int(self.headers["Content-Length"])).decode()).items()}
        basic = self.headers.get("Authorization", "")
        if basic.startswith("Basic "):
            client_id, _, client_secret = base64.b64decode(basic[6:]).decode().partition(":")
        else:
            client_id, client_secret = form.get("client_id"), form.get("client_secret")
        claims = CODES.pop(form.get("code", ""), None)  # single use
        if self.path != "/token" or claims is None or (client_id, client_secret) != (CLIENT_ID, CLIENT_SECRET):
            self._send(400, {"error": "invalid_grant"})
            return
        self._send(200, {"access_token": secrets.token_hex(16), "token_type": "Bearer", "expires_in": 600,
                         "id_token": jwt.encode({"alg": "RS256", "kid": "smoke-key"}, claims, KEY)})


def main() -> None:
    server = ThreadingHTTPServer(("0.0.0.0", 9443), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain("/smoke/tls/oidc.crt", "/smoke/tls/oidc.key")
    server.socket = context.wrap_socket(server.socket, server_side=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
