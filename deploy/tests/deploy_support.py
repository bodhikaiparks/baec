"""Shared helpers for the deployment tests (V0.4 Phase 5): build, configuration, and the HTTPS boundary, locally.

    eval "$(scripts/postgres_test_db.sh env)"
    .venv-postgres/bin/python -m pytest deploy/tests -q -p no:cacheprovider

They need Docker, and the boundary smoke test also needs the disposable test PostgreSQL (scripts/postgres_test_db.sh
up). Nothing is deployed and no external service is contacted: certificates come from a throwaway CA made per run,
the OpenID Connect provider is synthetic (oidc_provider.py), and every port is bound to 127.0.0.1. Software tests of
configuration and behavior, not a penetration test.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "deploy"
RELEASE = "smoke"


def docker() -> str:
    found = shutil.which("docker")
    if found is None:
        pytest.fail("deployment tests need Docker")
    return found


def run(*command: str, check: bool = True, env: dict | None = None, timeout: int = 900,
        input: str | None = None) -> subprocess.CompletedProcess:
    result = subprocess.run(command, capture_output=True, text=True, env=env, timeout=timeout, input=input)
    if check and result.returncode != 0:
        raise AssertionError(f"{command[:4]} failed ({result.returncode}): {result.stderr[-2000:]}")
    return result


def openssl(*args: str) -> None:
    run("openssl", *args)


def make_ca(directory: Path) -> tuple[Path, Path]:
    directory.mkdir(parents=True, exist_ok=True)
    cert, key = directory / "ca.crt", directory / "ca.key"
    openssl("req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1", "-nodes", "-days", "2",
            "-subj", "/CN=BAEC smoke-test CA (synthetic)", "-addext", "basicConstraints=critical,CA:TRUE",
            "-addext", "keyUsage=critical,keyCertSign,cRLSign", "-keyout", str(key), "-out", str(cert))
    return cert, key


def make_certificate(directory: Path, name: str, sans: list[str], ca: tuple[Path, Path]) -> tuple[Path, Path]:
    cert, key, csr, ext = (directory / f"{name}.crt", directory / f"{name}.key", directory / f"{name}.csr",
                           directory / f"{name}.ext")
    openssl("req", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1", "-nodes", "-subj", f"/CN={sans[0]}",
            "-keyout", str(key), "-out", str(csr))
    ext.write_text("subjectAltName=" + ",".join(("IP:" if s[0].isdigit() else "DNS:") + s for s in sans)
                   + "\nextendedKeyUsage=serverAuth\nkeyUsage=critical,digitalSignature\n", encoding="utf-8")
    openssl("x509", "-req", "-in", str(csr), "-CA", str(ca[0]), "-CAkey", str(ca[1]), "-CAcreateserial", "-days", "2",
            "-extfile", str(ext), "-out", str(cert))
    return cert, key


def compose_environment(**values: str) -> dict:
    """The process environment plus deployment settings. Never the caller's own BAEC_* or ANTHROPIC_* values."""
    base = {k: v for k, v in os.environ.items() if not k.startswith(("BAEC_", "ANTHROPIC_", "SMOKE_"))}
    return {**base, "BAEC_RELEASE": RELEASE, **values}


def compose_config(environment: dict, *files: Path, profiles: tuple[str, ...] = ()) -> dict:
    command = [docker(), "compose", "--project-directory", str(DEPLOY), "-f", str(DEPLOY / "compose.yaml")]
    for file in files:
        command += ["-f", str(file)]
    for profile in profiles:
        command += ["--profile", profile]
    return json.loads(run(*command, "config", "--format", "json", env=environment).stdout)
