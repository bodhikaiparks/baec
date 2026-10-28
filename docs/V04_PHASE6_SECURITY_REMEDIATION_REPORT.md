# BAEC CRM V0.4 Phase 6: Pre-Deployment Security Remediation

Phase 6 is the first controlled deployment. Before any external infrastructure was created, its preflight
vulnerability scan stopped the release: the accepted Phase 5 candidate carried an applicable critical finding. This
report records that scan, the approved remediation, the rescan, and the regression gate. **Nothing has been deployed
and no external infrastructure exists.** These are software tests and scanner results on this machine, not a
penetration test or evidence of production security.

> **`86f59a1` must not be deployed.** The candidate for the first controlled deployment is the commit that adds this
> report.

| Status | What |
|---|---|
| Implemented and tested | Authlib 1.8.0 → 1.9.1; certifi 2026.7.22 → 2026.10.23; nginx and PostgreSQL base digests refreshed; test PostgreSQL pinned to the ops base; forged-token cases on the real sign-in callback; in-image checks of versions, trust stores, Authlib forgeries, and TLS verification on the sign-in client path |
| Unchanged | BAEC, Engine 1/2/3 logic and schemas (no migration), RLS, ConsoleService checks, HUMAN-only sign-in, cookie and HTTPS boundary design, nginx template, compose services and secret boundaries, the Python base image (already current) |
| Not done | Any external deployment; a live OIDC provider; scanning of x86-64 images (the images here are linux/arm64) |

---

## 1. Preflight (against `86f59a1`)

Clean tree on `baec-crm-v0.4` at `86f59a1bbea046508598f1b41da2003221bff48f`. No tracked `.env`, key, certificate, or
credential; no secret pattern in tracked files. Images built from `git archive 86f59a1`. Every Phase 5 gate
reproduced: SQLite 584, PostgreSQL 1036, Engine 1 parity 537, Engine 2 parity 115, deployment 47, repository 7597
(2 `live_claude` deselected), `git diff --check` clean.

## 2. Scan method

Trivy **0.58.1** (`aquasec/trivy:0.58.1`, a release from before the March 2026 Trivy supply-chain compromise), run as
a container with no Docker socket: each image is exported with `docker save` and scanned with `--input`; the lock is
scanned as a read-only copy named `requirements.txt`. The vulnerability database is downloaded fresh before each scan
(2026-10-28 for both scans). Raw JSON is kept outside the repository.

## 3. Original findings (`86f59a1`)

| Target | Critical (entries / distinct) | High (entries / distinct) |
|---|---|---|
| web (`python:3.14-slim`, Debian 13.7) | 12 / 7 | 97 / 38 |
| proxy (`nginx:1.28-alpine`, Alpine 3.23.3) | 2 / 1 | 64 / 46 |
| ops (`postgres:16-alpine`, Alpine 3.24.2) | 1 / 1 | 25 / 25 |
| lock | 7 / 7 | 25 / 25 |

The release blocker and the authentication-boundary findings:

* **certifi 2026.7.22, GHSA-6q48-6c7w-9gw2 (critical).** The bundle trusts `emSign ECC Root CA - G3`, under which
  eight intermediates have compromised keys. Applicable: certifi ships in the runtime and is the trust store of
  `requests`.
* **Authlib 1.8.0: CVE-2026-96760 (critical), CVE-2026-114615, CVE-2026-114616 (high).** A JSON-serialized JWS with
  no signature returned as verified; zero-length HMAC keys accepted; a prefixed PEM public key accepted as an HMAC
  secret. **Confirmed on 1.8.0 during remediation** (the advisory text says "1.7.2 and below", but 1.8.0 accepted a
  JWS with no signatures, an empty `OctKey`, and newline/BOM-prefixed PEM keys). Authlib's OIDC client verifies ID
  tokens with joserfc, not these APIs, but it sits on the sign-in boundary, so it was upgraded rather than argued.
* **Proxy base:** the nginx 1.28 image had many fixable OpenSSL, pcre2, musl, expat, curl, and nginx findings.

### Correction to the preflight analysis

The preflight said BAEC's outbound OIDC TLS used certifi. **It does not.** Authlib's Starlette client uses `httpx2`,
whose `verify=True` default builds its TLS context with `truststore` from the **system store** (OpenSSL's default
paths: `/etc/ssl/certs/ca-certificates.crt`, Debian `ca-certificates 20250419+deb13u1`) unless `SSL_CERT_FILE` is
set. The Anthropic SDK uses the same client. That store **never contained** `emSign ECC Root CA - G3`, so the sign-in
path was not exposed to GHSA-6q48-6c7w-9gw2. certifi is used only by `requests`, which no BAEC or Streamlit module
imports at load. certifi was still upgraded as approved, and the system store is now checked by a test (§7).

## 4. Remediation

### Dependencies

| Package | Old | New | Why this version |
|---|---|---|---|
| Authlib | 1.8.0 | **1.9.1** | fixes all three; 1.9.0 lacks the empty-HMAC-key fix. Requires `joserfc>=1.6.8`, `cryptography>=45.0.1`; the lock already has joserfc 1.7.7 and cryptography 50.0.2, so nothing else moved |
| certifi | 2026.7.22 | **2026.10.23** | latest. 2026.10.18 removed all emSign roots; 2026.10.19 restored the unaffected ones (without G3); 2026.10.23, an automated upstream bundle sync, also drops emSign C1 and C3 (118 roots). Checked by reading each wheel's `cacert.pem` |

`deploy/requirements.in` and `requirements-postgres.txt` changed; `deploy/requirements.lock` was regenerated with
`deploy/lock_requirements.py` (`--check`: matches). The lock diff is exactly the two version lines and their four
hashes. The test virtualenv (`.venv-postgres`) was moved to the same two versions from the lock's hashes, so every
suite below ran on the shipped set. `pip check`: no broken requirements, in the venv and in the image.

### Base images

| Image | Old | New | Why |
|---|---|---|---|
| proxy | `nginx:1.28-alpine@sha256:a8b39bd9cf0f83869a2162827a0caf6137ddf759d50a171451b335cecc87d236` (nginx 1.28.3, Alpine 3.23.3) | `nginx:1.30-alpine@sha256:47928bf67eb62a1863cb08d9ecb52d3e704304ba614eaee17884204677702951` (nginx 1.30.6, Alpine 3.24.3) | The `1.28-alpine` tag still resolves to the old digest: the 1.28 branch image is no longer rebuilt, so no newer 1.28 patch image exists. 1.30 is nginx's current stable branch (`stable-alpine` is the same digest). Template, directives, and architecture unchanged; the configuration and HTTPS-boundary tests pass on it |
| ops | `postgres:16-alpine@sha256:721873c34ceb9f8d8fc265984940dc982404c105f19ad51be9fdc5970a6080ea` (16.15, Alpine 3.24.2) | `postgres:16-alpine@sha256:0cefaabd882e42bcb2a2f3dc56d3ce4770560ba62fd7f197f1a2e008fd5731ab` (16.15, Alpine 3.24.3) | current digest of the same tag; PostgreSQL stays 16 |
| test PostgreSQL | `postgres:16-alpine` (floating tag) | the ops digest above | the integration suite now runs on the same server build as the ops tools; the cluster was recreated on it before the gate |
| web | `python:3.14-slim@sha256:cd89c30eda26ee911a4611b18680086e0ea42e6f5f8cbe653d07fa7958c9cec8` | unchanged | already the tag's current digest, and `apt list --upgradable` in it is empty: no newer Debian package to take |

## 5. Rescan after remediation

| Target | Critical (entries / distinct) | High (entries / distinct) |
|---|---|---|
| web (Debian 13.7) | 10 / 5 | 95 / 36 |
| proxy (Alpine 3.24.3) | **0** | 3 / 2 |
| ops (Alpine 3.24.3) | 1 / 1 | 25 / 25 |
| lock | 5 / 5 | 23 / 23 |

Gone: certifi; all three Authlib findings; the proxy's critical and 44 of its 46 distinct highs (OpenSSL, pcre2,
musl, expat, zlib, nginx, and others).

### Remaining critical findings: none applicable

| Finding | Where | Reachability |
|---|---|---|
| Pillow 12.3.0: CVE-2026-112393, -112398, -112403, -112900, -96639 (fixed in 13.0.0) | web, lock | **Unreachable.** Pillow is a Streamlit dependency. BAEC has no `PIL`, `st.image`, `st.file_uploader`, `st.camera_input`, `st.pyplot`, `st.logo`, or media element in any production module, and accepts no file or image input. Importing every production module in the built image leaves `PIL` unimported (checked). No untrusted image ever reaches a decoder. Not upgraded: 13.0 is a major version under Streamlit 1.65 with no reachable benefit. Upgrade when Streamlit's supported range allows |
| Go stdlib 1.24.6 in `gosu`: CVE-2025-68121 (crypto/tls session resumption) | ops | **Unreachable.** `gosu` comes with the postgres base image. The ops image's entrypoint is `python3 /usr/local/bin/baec_backup.py` as user `postgres`, and compose overrides it only with the same tool. Nothing runs `gosu`, and it makes no TLS connections. Unchanged in the newest postgres:16 image |

### Remaining high findings

| Finding | Where | Assessment |
|---|---|---|
| Pillow 12.3.0, 22 highs (DoS, one JPEG-encoder code execution, info disclosure) | web, lock | Unreachable, as above |
| urllib3 2.7.0 (CVE-2026-97687, -97689), msgpack 1.1.2 (CVE-2026-114613, GHSA-6v7p-g79w-8964), setuptools 70.3.0 (CVE-2025-47273) | web | **Unreachable: pip's own vendored copies** (`pip/_vendor`, listed in `vendor.txt`), not packages BAEC imports. The runtime urllib3 is 2.8.0; `setuptools` is not importable. pip runs only at image build. The setuptools CVE is in `PackageIndex`, which pip does not use |
| Debian 13.7: util-linux ×4 (mount/nsenter), acl CVE-2026-54369, systemd CVE-2026-16742, ncurses CVE-2025-69720, perl CVE-2026-9538 | web | **Base image only; no fix published** (`apt list --upgradable` empty). Local privilege-escalation and mount-helper issues in tools the application never runs. Mitigated: the container runs as uid 10001 with every capability dropped, `no-new-privileges`, a read-only root filesystem, and no shell entrypoint |
| brotli-libs 1.2.0 (CVE-2026-111497), curl/libcurl 8.22.0 (CVE-2026-92392) | proxy | **Base image only; unreachable.** nginx links only pcre2, OpenSSL, zlib, and musl (`ldd`) and loads no dynamic module. brotli-libs is required only by freetype and libcurl; `curl` is in the base image but nothing in BAEC's proxy runs it (no proxy health check uses it). Fixed packages exist in Alpine; not added by `apk upgrade` because that would make the build depend on the repository's state at build time. Taken when the official image is rebuilt |
| Go stdlib 1.24.6 in `gosu`, 25 highs | ops | Unreachable, as above |

No finding is suppressed in scanner configuration. Every remaining critical or high is listed here with its reason.

## 6. Reproducibility

Images are built from a snapshot of exactly the tracked files (no `.env`, certificates, keys, `.venv`, or local data;
`infra/postgres-test/.env` and `certs/` are untracked and checked absent). Each image was then built twice with
`--no-cache`, and every regular file was hashed (excluding `.pyc` bytecode and Docker-managed `/etc/hosts`,
`/etc/hostname`, and `/etc/resolv.conf`):

| Image | Files | Two builds |
|---|---|---|
| web | 13,018 | identical |
| proxy | 1,225 | identical |
| ops | 3,817 | identical except `/var/log/apk.log` (install timestamps) |

Residual: the ops image's `apk add python3` is not version-pinned (Phase 5 design; today python3 3.14.10-r1). A later
rebuild can take a newer Alpine patch, so rescan after every rebuild.

## 7. New regression tests

* `deploy/tests/test_deploy_config.py`, against the built web image:
  * `test_the_built_image_ships_the_remediated_versions_and_no_compromised_root`: Authlib 1.9.1; certifi
    2026.10.23; the only `cacert.pem` files are certifi's and pip's vendored copy; neither of them, nor the system
    store the sign-in client uses, contains `emSign ECC Root CA - G3`.
  * `test_authlib_refuses_the_forgeries_its_fixed_vulnerabilities_allowed`: 9 cases (JWS with no signatures, with
    and without a key; an empty signature; empty HMAC keys as bytes and str; HS256 with an empty key; a public key as
    HMAC secret, plain, newline-prefixed, and BOM-prefixed). Several were accepted by 1.8.0, so the test tells the
    versions apart.
  * `test_the_sign_in_client_verifies_tls_against_the_system_trust_store`: Streamlit's own OAuth class does OIDC
    discovery and the JWKS fetch from a local HTTPS server inside the container (`--network none`). As deployed (the
    system store), a throwaway CA is refused (`CERTIFICATE_VERIFY_FAILED`). With that CA trusted, discovery and the
    key set complete. A trusted chain for another hostname is refused. No external provider is contacted.
* `baec_console/tests_postgres/test_oidc_flow.py`: four ID-token forgeries through the real
  `/oauth2callback` with Authlib 1.9.1: unsigned (`alg: none`), signature removed, HMAC with an empty key, HMAC with
  the provider's public key. None produces a sign-in. They join the existing cases (another key, audience, issuer,
  expired, replayed nonce, no nonce), code and callback replay, cross-browser callback, forged cookies, logout, and
  revocation and deactivation, all passing unchanged.

## 8. Regression gate (after every change; test cluster recreated on the pinned image; machine held awake with `caffeinate -i`)

| Suite | Result |
|---|---|
| SQLite console (`baec_console/tests`) | **584 passed** |
| PostgreSQL (`baec_console/tests_postgres`, `-W error::ResourceWarning`, TLS-only) | **1040 passed** (1036 + 4 forgeries) |
| Engine 1 parity (`test_engine1_parity.py`, `test_engine1_storage.py`, `test_engine1_suites_on_postgresql.py`) | **537 passed** (unchanged) |
| Engine 2 parity (`test_engine2_parity.py`) | **115 passed** (unchanged) |
| Auth: `baec_console/tests/test_auth.py` | **50 passed** |
| Auth: `test_production_auth.py` + `test_production_app.py` + `test_oidc_flow.py` | **117 passed** (113 + 4) |
| Backup / restore and damaged restores (`test_backup_restore.py`) | **16 passed** |
| Deployment (`deploy/tests`) | **50 passed** (47 + 3) |
| HTTPS boundary and cookies (`deploy/tests/test_https_boundary.py`, nginx 1.30) | **20 passed** |
| Repository `tests/`, three consecutive runs | **7597 passed, 2 deselected** each (104.4 s, 99.5 s, 100.3 s) |
| `git diff --check` | clean |

## 9. Residual risks before the controlled deployment

* The images here are **linux/arm64**. If the host is x86-64, build there from the candidate commit and rescan before
  exposure. Package sets can differ by architecture.
* The sign-in trust store is Debian's `ca-certificates`, which still includes emSign Root CA G1, C1, and C3 (not the
  compromised G3). The newest certifi drops C1 and C3, and the reason was not verified. Pointing BAEC's OIDC client at
  certifi (`SSL_CERT_FILE`) would be a deliberate trust-path change and was not made here.
* Pillow (5 critical) and the Debian, brotli, curl, and gosu findings remain. Each is unreachable as documented, but
  an upgrade or new feature that decodes images, runs pip, or executes `gosu` or `curl` would change that. Rescan on
  every rebuild.
* Trivy's database and advisories change daily. This scan is a point-in-time result (2026-10-28).
