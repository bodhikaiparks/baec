# V0.4 Phase 5 — Deployment Hardening: Report

Branch `baec-crm-v0.4`, uncommitted working tree on top of `7b808e4` (Phase 4 accepted gate). Nothing was committed,
merged, or deployed. No external identity provider, DNS, certificate authority, or cloud service was contacted; every
container and port in the tests ran on this machine, bound to 127.0.0.1. All data and identities are synthetic. Every
test cited is a software test of configuration and behavior on a disposable cluster, not a penetration test, a
production security assessment, a load test, or evidence about the BAEC theory. The frozen public research release
was not touched. Everything below is **implementation** (CLAUDE.md labels); no BAEC rule, state, threshold, or engine
behavior changed.

**Bottom line.** BAEC now has a reproducible production build and a documented deployment (`deploy/`,
`docs/PRODUCTION_RUNBOOK.md`) whose boundaries were exercised end to end locally: nginx terminates TLS, serves plain
HTTP only as a redirect, sends HSTS, and gives every application cookie `Secure` — verified on the real
`_streamlit_user` sign-in cookie after a real authorization-code sign-in through the proxy, which arrives
`Secure; HttpOnly; SameSite=Lax`. Every PostgreSQL connection requires verified TLS; the web process holds only its
own two database credentials and refuses to start with any other; pools are bounded with timeouts; a backup restored
into a fresh database passed the startup gate and matched the source row for row; damaged restores are refused.
**It is not production-ready yet**: the remaining blockers (§33) are the steps that need real infrastructure — a real
host, a real managed PostgreSQL, a real identity provider, and backup storage.

| Status | What |
|---|---|
| Implemented and tested | verified-TLS policy for every DB connection; live TLS check; bounded pools and timeouts; controlled unavailability; secret boundary; `_FILE` secrets; connection-limit and replication checks; synchronous pool reset (bug fix); log redaction; readiness command; migration verification; production image, proxy image, ops image; compose configuration; backup/restore tool; restore drill; damaged-restore refusals; HTTPS boundary smoke test with real sign-in |
| Documented, not executable here | DNS, public certificate, managed PostgreSQL settings, off-host encrypted backup storage, provider registration, monitoring/auto-restart of an unhealthy container |
| Unchanged | BAEC/Engine 1/2/3 logic and schemas (migrations 0001–0005 byte-identical; no new migration), RLS policies, ConsoleService checks, HUMAN-only sign-in, 12-hour session age, Claude BYOK, development mode on SQLite, public demo |
| Not done | an external deployment; a live provider; an RTO measurement on production-size data |

---

## 1. Accepted Phase 4 baseline

Clean tree at `7b808e4`. Before any code change, the test cluster was switched to TLS-only (§5) and the PostgreSQL
suite re-run with only the test URL helper changed to keep the TLS query string: **971 passed** (292 s), equal to the
accepted gate. After the code changes but before any new test was added: **971 passed** again.

## 2. Deployment architecture (§1 of the brief)

Selected: **one Docker host running Docker Compose, behind nginx, with a managed PostgreSQL 16**. Reasons: the
application is one Streamlit process with server-side sessions and a WebSocket; it needs a TLS-terminating proxy that
can rewrite cookies; a single host keeps the trust boundary small and fully testable on this machine; a managed
database gives TLS, snapshots, and point-in-time recovery without operating them. Roles share one database server and
one database (separate roles, not servers). The diagram and component table are `docs/PRODUCTION_RUNBOOK.md` §1.

| Runtime component | Where |
|---|---|
| BAEC web application | `web` service, image `deploy/Dockerfile`, uid 10001, read-only root, no published port |
| HTTPS/TLS termination | `proxy` service, image `deploy/nginx` (nginx 1.28, pinned digest) |
| PostgreSQL | managed PostgreSQL 16, TLS required, private |
| OIDC provider | external; configured, not contacted in this phase |
| application DB credentials | `baec_app_database_url` secret → web only |
| auth-lookup credentials | `baec_auth_database_url` secret → web only |
| provisioning credentials | `baec_provisioning_database_url` secret → `provision` job only |
| migration credentials | `baec_migration_database_url` secret → `migrate` release job only |
| persistent backup storage | off-host object store (encrypted, versioned, immutable); the host only stages |
| health checks | liveness `/_stcore/health`; readiness `python -m baec_console.readiness` |

## 3. HTTPS/TLS implementation (§2)

`deploy/nginx/templates/baec.conf.template`, rendered by the official image at start:

* TLS 1.2/1.3 only (TLS 1.1 refused: tested); an unknown SNI name is refused (`ssl_reject_handshake`: tested).
* Port 80 for the BAEC host: `return 308 https://<BAEC_PUBLIC_ORIGIN>$uri` — never proxied, no cookie, no content,
  the query string dropped so an OAuth code sent over HTTP is never forwarded. Other hosts: no answer (444). Tested
  for GET, HEAD, POST, `/`, `/_stcore/health`, `/auth/login`, `/_stcore/stream`, and `/oauth2callback?code=…`.
* Callback: the sign-in `redirect_uri` is a fixed https URL in the configuration (the web service refuses http);
  nothing derives a URL from forwarded headers.
* Forwarded headers are set, never passed through: `X-Forwarded-For $remote_addr`, `X-Forwarded-Proto https`,
  `Forwarded ""`. The application does not use them (uvicorn trusts forwarded headers only from 127.0.0.1, which the
  proxy is not), so there is nothing to spoof.
* Direct backend exposure: `web` has no published port and sits only on the internal `frontend` network and an
  egress network; the test checks `docker inspect` (no port bindings, no edge network) and the compose config (only
  `proxy` publishes ports).

## 4. Final authentication-cookie behavior

Streamlit 1.65 sets `_streamlit_user` (and `_streamlit_user_tokens`) `HttpOnly; SameSite=Lax; Path=/;
Max-Age=2592000` and deliberately **without `Secure`** (its source: Safari cookie bugs); its OAuth state session
cookie and XSRF cookie are `Secure` only when Streamlit itself terminates TLS. The trusted proxy adds `Secure` to
every `Set-Cookie` (`proxy_cookie_flags ~ secure`). Observed at the browser side of the deployed boundary after a real
authorization-code sign-in (Streamlit's own `/auth/login` and `/oauth2callback` behind nginx, a synthetic provider
over TLS):

| Cookie | Secure | HttpOnly | SameSite |
|---|---|---|---|
| `_streamlit_user` (the sign-in) | **yes** (added by the proxy) | yes (Streamlit) | Lax |
| `_streamlit_session` (OAuth state) | yes | yes | Lax |
| `_streamlit_xsrf` | yes | **no — must stay readable by Streamlit's page script for XSRF** | Lax |
| every other `Set-Cookie`, including deletions | yes | as set | as set |

Mutation: with `proxy_cookie_flags` removed from the template, both cookie tests fail. The same cookie, decoded inside
the web container, opened the console of the user the provisioning job had linked (§22). **The Secure sign-in cookie
is guaranteed by this architecture**, so this is not a blocker. It depends on the proxy: running the web image without
it would not be safe, and the compose file makes that the only route.

## 5. HSTS (§3)

`Strict-Transport-Security: max-age=86400` on every HTTPS response (`always`, including errors), at the proxy — the
authoritative TLS boundary — not in application code. No `includeSubDomains`, no `preload` (both tested absent).
Ramp-up and the preload decision: runbook §8.

## 6. Secret separation (§4)

Inventory and per-process classification: runbook §2.2. Enforced in three places:

1. **Compose** gives each service only its own secret files (tested on Docker Compose's own resolved config): web gets
   the app URL, lookup URL, `[auth]` file, DB CA; `migrate`, `provision`, `backup`, `restore` each get one credential
   and run only under a profile; no database URL is ever an environment value.
2. **The web process refuses to start** (`deployment.require_web_secret_boundary`, first step of
   `production.open_runtime`, hence of readiness) if `BAEC_MIGRATION_/PROVISIONING_/BACKUP_/RESTORE_DATABASE_URL`
   (value or `_FILE`) or any `ANTHROPIC_*` variable is present (the Claude SDK reads its key, endpoint, and logging from
   those). Tested for each name, in-process and in the built image (`not ready: SECRET_BOUNDARY`).
3. **In the running smoke stack** the web container's environment has no such name and `/run/secrets` holds exactly its
   four files (plus the smoke CA bundle of the test overlay).

`NAME_FILE` support (`deployment.configured_value`, and the migration and provisioning CLIs) reads a mounted secret;
both set, or an unreadable file, is refused without showing a value. No tracked file contains a credential;
`deploy/secrets/` and the per-machine test certificates are gitignored. Claude BYOK is unchanged (§17).

## 7. PostgreSQL TLS (§5)

* **Policy** (`database.require_verified_tls`): a URL must contain exactly one `sslmode`, equal to `verify-full`.
  Applied before any connection by `connect()`, every pool (`open_pool`), the gate (both URLs), and the backup tool.
  `disable`, `allow`, `prefer`, `require`, `verify-ca`, a missing or duplicated `sslmode` are refused, naming the
  variable, never the value.
* **Live check** (`gate.transport_failures`): each gate connection must report TLS in use client-side
  (`pgconn.ssl_in_use`) and server-side (`pg_stat_ssl.ssl`); each pooled session is re-checked after the pools open
  (`database.session_failures`).
* **Server identity**: tested that a certificate from another CA is refused and that the right address under a name
  the certificate does not carry is refused (`hostaddr` + wrong host).
* **Server side** (test cluster; production equivalent in runbook §3): `pg_hba.conf` accepts TCP only via `hostssl`
  and rejects plaintext; a client forcing `sslmode=disable` is refused for every role (tested).
* Errors say "unreachable or refused the connection" or name the variable; tests check no password appears.

## 8. Database roles (§6)

Exact live privileges after migrations 0001–0005: runbook §4 table. Proven by tests (`test_deployment_hardening.py`
unless noted):

| Claim | Evidence |
|---|---|
| web cannot run migrations | migrate refuses `baec_app` by name; no CREATE (Phase 3 tests) |
| web cannot provision | `Provisioner(app URL)` refused; no privilege on `baec_auth` |
| web cannot alter RLS, create/drop schema, disable triggers | `NO FORCE ROW LEVEL SECURITY`, `DISABLE TRIGGER ALL`, `DROP SCHEMA`, `CREATE SCHEMA` → InsufficientPrivilege |
| web cannot bypass RLS | `SET row_security = off` then a filtered query → refused; no tenant → 0 rows |
| web cannot modify auth mappings | SELECT/INSERT/UPDATE on `baec_auth.auth_identities` → refused |
| web cannot elevate | `SET ROLE` migrator/provisioner/backup, `ALTER ROLE … BYPASSRLS`, `CONNECTION LIMIT -1`, `CREATE ROLE`, `GRANT pg_read_all_data`, `SET session_replication_role` → refused |
| auth lookup cannot read business data | SELECT on every one of the 77 product, Engine 1, Engine 2, and migration tables → refused |
| provisioner not in the web runtime | web refuses to start with the provisioning credential present (with every other check passing); compose gives it only to `provision` |
| startup detects unsafe app credentials | app URL as backup (BYPASSRLS), admin (superuser), migrator (owner), provisioner, lookup → refused with the reason |
| startup detects privileges added later | `BYPASSRLS`, `REPLICATION`, `CREATEROLE`, `CONNECTION LIMIT -1`, membership in `pg_read_all_data`, `baec_provisioner`, `baec_migrator` → refused |

New gate checks: the app role may not have `REPLICATION`; both runtime roles must have a database-side connection
limit at least their pool size.

## 9. Connection pool and limits (§7)

* Streamlit sessions never own connections: one `ProductionRuntime` per process holds one application pool and one
  lookup pool; each request borrows a connection for one transaction.
* Defaults (all env-configurable within bounds; out-of-range refused at startup): app pool max 4, lookup max 2,
  wait ≤ 10 s, ≤ 64 waiting requests, idle connections closed after 300 s, every connection replaced after 30 min.
* Database-side caps: each role's `CONNECTION LIMIT` (required by the gate) bounds connections however many
  processes or replicas run.
* On exhaustion: `DatabaseUnavailable` with one fixed message (the app shows only it; no traceback); nothing is
  bypassed and no data is shown.
* Tests: 40 sequential sign-ins → pool and server sessions ≤ 4 (lookup ≤ 2); 24 concurrent sessions on a pool of 3 →
  all served, peak ≤ 3, each saw only its own organization, no tenant left on any connection; exhaustion → refusal
  after ~1 s, no third connection opened, recovery after release with a clean tenant context; queue overflow →
  immediate refusal; failed requests (application error, SQL error) → every connection returned, no tenant left.

**Bug found and fixed (regression test added).** psycopg_pool returns a connection through a background task when a
reset callback is configured; a pool closed before that task runs drops it without closing the connection, which
stays open on the server. A runtime that fails its post-open checks closes its pools immediately, so each refused
request could leak one server session until the role's limit. Fix: `database.available()` resets each connection
(`DISCARD ALL`, or closes it if it cannot be reset) in the requesting thread before returning it, and pools have no
reset callback, so returns are synchronous. Test
`test_closing_a_pool_right_after_use_leaves_no_session_open_on_the_server` (both pools) fails on the old code and
passes on the new one.

## 10. Timeouts (§8)

| Setting | Default | Why |
|---|---|---|
| connect timeout | 5 s | unchanged from Phase 3 |
| pool wait | 10 s | an interactive request should fail visibly rather than hang |
| `statement_timeout` | 30 s | BAEC statements are short OLTP reads/writes; the heaviest, the startup catalog snapshot, takes ~0.25 s for the whole readiness check on the test cluster |
| `lock_timeout` | 5 s | BAEC transactions hold row locks for milliseconds; waiting longer means something is wrong |
| `idle_in_transaction_session_timeout` | 60 s | no BAEC transaction waits on a person; an abandoned one is ended |

Applied as libpq startup options on every pooled connection and on the gate's connections, so `DISCARD ALL` returns
to them. Tests: a stuck statement is cancelled in ~1 s and **its transaction's earlier write is rolled back**; a held
table lock fails the request in ~0.5 s and the pool recovers; an abandoned transaction is ended by the server and its
connection discarded; a console CRM write blocked by a lock fails with the fixed message and writes nothing; a pooler
that drops startup options would be detected (`session_failures`). Integrity errors (constraints, triggers, RLS)
keep their own types; only timeouts, pool exhaustion, and lost connections become `DatabaseUnavailable`.

## 11. Backup design (§9)

Runbook §12: platform PITR as primary; daily and pre-release logical dumps (`deploy/ops/baec_backup.py` in the
`baec-ops` image: `pg_dump -Fc` as `baec_backup`, verified TLS, credentials via libpq environment only, owner-only
`0600` file, never overwritten, SHA-256 file) copied to encrypted, versioned, immutable off-host storage. RPO minutes
(PITR) / 24 h (dump only). RTO target 4 h, **not measured** at production size. The backup credential is a separate
role (`BYPASSRLS` + `pg_read_all_data`) only the backup job receives; the application role cannot make a complete
dump (tested: `pg_dump` as `baec_app` fails under RLS).

## 12. Restore-drill result (§10)

`baec_console/tests_postgres/test_backup_restore.py`, with the real `baec-ops` image and roles:

1. fresh database, migrated by `baec_migrator`; two organizations, users, and sign-in links created by the
   provisioning CLI; through signed-in production consoles: accounts, buyer interactions, an Engine 1 condition with
   its dormancy judgment and state change, Engine 2 monitoring with an observation, its correction, and a review, an
   Engine 3 preparation with its review, CRM contacts, tasks (one completed), an activity, an interaction link;
2. `baec_backup.py backup` → `baec-<UTC>.dump` (`PGDMP`, `0600`) + `.sha256`;
3. a second empty database prepared exactly as the DBA bootstrap prepares one;
4. `baec_backup.py restore` (single transaction, triggers disabled while loading);
5. `check_database` and `open_runtime` pass on the restored database (versions 1–5);
6–8. every BAEC table equal row for row and column for column (count + md5 of all rows), every sequence position
   equal; 19 named tables (identity, auth links, CRM, preparation, events, attributions, Engine 1 records, evidence,
   judgments, transitions, Engine 2 observations, reviews, audit events, migrations) confirmed populated; buyer
   evidence and the stored interaction text (with the exact buyer statement) identical;
9. RLS on the restored database: no tenant → 0 rows; each organization sees only itself; every tenant table still
   `ENABLE` + `FORCE`;
10. both users signed in through the restored links see exactly the console reads they saw on the source (the
    parity suite's `reads()`: dashboard, queue, activity, views, timelines, attributions, contacts, activities, tasks,
    preparations); and new writes work afterwards.

Result: **16 passed** (drill + damaged restores), ~13 s on synthetic data. No tool output contained a credential.

## 13. Disaster / corruption tests (§11)

| Damage | Outcome |
|---|---|
| tampered dump (one byte) | restore refused before touching the database ("does not match its checksum") |
| truncated dump with a matching checksum | `pg_restore` fails, single transaction rolls back, target empty → readiness refuses ("not at the expected migration version") |
| interrupted restore (rows and migration record loaded, no constraints, triggers, or policies) | refused (RLS policies missing) |
| restored, then a trigger dropped | refused (`ENGINE1_SCHEMA_MISMATCH`) |
| restored, then migration record 5 deleted | refused (migration version) |
| restored, then a migration checksum altered | refused (migration version) |
| restored, then `NO FORCE ROW LEVEL SECURITY` | refused |
| restore over a database that has BAEC schemas | refused by the tool; target unchanged |
| plaintext backup URL; backup as the app role | refused / `pg_dump` fails |

In the deployed stack, readiness turns `not ready: DATABASE` when RLS is weakened after start and `ready` when it is
restored.

## 14. Migration / release model (§12)

release job (`migrate` service, profile `release`, migration secret only) → all pending migrations in one
transaction (ordered, checksummed, forward-only; unchanged) → `verify_recorded` re-reads the record and requires it to
equal this release's migrations exactly (new) → web starts with application credentials → its startup gate (and
readiness) verifies everything again. Tested in the smoke stack ("applied migrations: [1, 2, 3, 4, 5]; schema
verified", then "schema is current"). Failed release recovery: runbook §18.

## 15. OIDC live-provider readiness (§13)

Checklist: runbook §9. Unchanged enforcement: exact `BAEC_AUTH_ISSUER`; discovery URL must be issuer +
`/.well-known/openid-configuration`; https redirect URI ending `/oauth2callback`; one provider; no `expose_tokens`;
organization only from the `(issuer, subject)` link (never email domain, tenant, display name, or claims). No live
provider was contacted; the smoke test used a synthetic provider over TLS inside the stack.

## 16. Session-security status (§14)

Unchanged: 12-hour BAEC session age from `iat`, checked on every operation; revocation and deactivation refuse the
next request; sign-out clears that browser's state and cookie. A copied cookie works until 12 h after sign-in unless
revoked or deactivated. **No per-session server-side logout exists**; rotating `cookie_secret` invalidates every
session at once (Phase 4 tested that a cookie signed with another secret is rejected). Added protection this phase:
the cookie can no longer travel over plain HTTP (Secure + HSTS + no HTTP content).

## 17. Health and readiness (§15)

* Liveness: Streamlit's `/_stcore/health` (`ok`), no sign-in, no database; the image's `HEALTHCHECK`.
* Readiness: `python -m baec_console.readiness` = exactly `production.open_runtime` (secret boundary, bounds, TLS,
  migrations, roles, RLS, all schemas, lookup role, OIDC configuration, connection limits, session timeouts), then
  close. Output: one fixed code; `--explain` adds only the refusal's own message (names, never values). It writes
  nothing (every BAEC row's md5 identical before and after), needs no BAEC session, and exposes no organization data;
  it is not served over HTTP. Compose health = readiness and liveness every 60 s.

## 18. Logging / redaction (§16)

Audit: BAEC's own messages are fixed or name settings (unchanged). Third-party risks: psycopg/pool errors can name
hosts and roles; Authlib and uvicorn could log URLs with OAuth codes; nginx's default format logs query strings.
Changes: `log_redaction.install()` (production app and readiness) redacts every log record from every logger —
URL user-info, password/secret/token values, OAuth `code`/`state`/`nonce`/token query parameters, bearer/basic
values, JWTs, Streamlit cookie values, Claude-style keys — including tracebacks; nginx logs `$uri` (no query), no
cookies or Authorization. Tests: 12 redaction patterns, ordinary text untouched, records from four loggers (one
created after install) with secrets in arguments and in an exception; in the smoke stack, the proxy and web logs
contain the callback path but none of: the OAuth code, state, the sign-in cookie value, the client secret, the cookie
secret, any database password, or a code sent over plain HTTP. Buyer statements are never logged by BAEC.

## 19. Production startup validation (§18)

`production.open_runtime` order: mode → secret boundary → bounds → database gate (verified TLS for both URLs, live
TLS, migrations and checksums, app-role attributes incl. replication and connection limit, privileges, RLS, Engine 2,
Engine 1, product, sign-in schema, lookup role incl. connection limit) → OIDC configuration → connection limits ≥ pool
sizes → open pools → each pool's session uses TLS and carries the timeouts. Every failure opens nothing (pools opened
are closed) and is a `DeploymentError` with a fixed message. Backup freshness is not checked at startup (no safe
lightweight source); it is an operations check (runbook §12).

## 20. Build / container result (§19)

* `baec-web`: `python:3.14-slim@sha256:cd89…cec8`; 56 packages from `deploy/requirements.lock` (`--require-hashes
  --no-deps`, SHA-256 of every published file, generated by `deploy/lock_requirements.py`; versions are those the
  suites ran on); `pip check` clean; uid 10001; `/app` read-only to it; no tests, `.data`, `.env`, `secrets.toml`,
  `mcp`, or `pytest`; every production module imports offline; HEALTHCHECK; ~885 MB (Streamlit brings pandas, numpy,
  pyarrow).
* `baec-proxy`: `nginx:1.28-alpine@sha256:a8b3…d236` + the template; default site removed.
* `baec-ops`: `postgres:16-alpine@sha256:7218…080ea` + Python 3 + the backup tool; runs as `postgres`.
* `.dockerignore` is an allowlist.
* Vulnerability scanning: **not run** — no scanner (pip-audit, trivy, docker scout) is installed locally, and
  installing one was out of scope. Dependencies were not changed to chase advisories. This is a remaining item (§33).

## 21. Deployment configuration created (§20)

`deploy/compose.yaml` (proxy, web, migrate, provision, backup, restore; networks `edge`, `frontend` internal, `egress`;
file secrets), `deploy/Dockerfile`, `deploy/nginx/`, `deploy/ops/`, `deploy/streamlit/config.toml`,
`deploy/requirements.in/.lock`, `deploy/lock_requirements.py`, `.dockerignore`. No public database endpoint. DNS and
the provider callback are documented (runbook §3, §9), not changed. `deploy/tests/compose.smoke.yaml` is a test
overlay only.

## 22. Provisioning operations (§21)

CLI only, through the `provision` job (the only holder of the provisioning secret). Runbook §7. Exercised in the
deployed smoke stack: create organization → create HUMAN user → link the synthetic provider's subject → sign in
through the proxy → the web container (holding only its own credentials) resolved the cookie to that user's console
(user, organization, BAEC display name, no accounts). No admin screen, no sign-up.

## 23. Runbook

`docs/PRODUCTION_RUNBOOK.md`: architecture, environment and secret contract, initial setup, DBA bootstrap and exact
privileges, images, migrations, provisioning, HTTPS/HSTS, OIDC checklist, start/readiness, revocation and
deactivation, backup, restore, sessions, credential rotation (DB, OIDC, cookie secret, TLS), incident shutdown,
upgrade, rollback/recovery. Placeholders only.

## 24. Files changed

New: `baec_console/log_redaction.py`, `baec_console/readiness.py`, `.dockerignore`, `deploy/Dockerfile`,
`deploy/compose.yaml`, `deploy/requirements.in`, `deploy/requirements.lock`, `deploy/lock_requirements.py`,
`deploy/streamlit/config.toml`, `deploy/nginx/Dockerfile`, `deploy/nginx/templates/baec.conf.template`,
`deploy/ops/Dockerfile`, `deploy/ops/baec_backup.py`, `deploy/tests/{deploy_support.py, oidc_provider.py,
compose.smoke.yaml, test_deploy_config.py, test_https_boundary.py}`, `infra/postgres-test/pg_hba.conf`,
`docs/PRODUCTION_RUNBOOK.md`, this report; tests listed in §25.

Modified: `postgres/database.py` (TLS policy, settings, bounded pools, synchronous reset, `DatabaseUnavailable`,
session check), `postgres/auth_lookup.py` (same pool), `postgres/gate.py` (TLS, replication, connection limits,
timeouts on its connections), `production.py` (startup order §19), `deployment.py` (secret boundary, `_FILE`),
`storage.py` (`_FILE`, settings), `app.py` (redaction, fixed unavailability message), `postgres/migrate.py`
(`_FILE`, post-apply verification), `postgres/provision.py` (`_FILE`), `postgres/importer.py` (named TLS checks),
`README_CONSOLE.md`, `.gitignore`; test infrastructure `infra/postgres-test/docker-compose.yml`,
`initdb/00_roles.sh`, `scripts/postgres_test_db.sh` (TLS-only cluster, backup role, connection limits); tests
`tests/test_storage.py` (production URLs now verified-TLS), `tests_postgres/conftest.py` (backup URL),
`test_migrations_and_gate.py` (URL helper keeps the TLS query; local-URL check includes TLS and backup).

Not modified: migrations 0001–0005, schema manifest, Engine 1/2/3 code, BAEC methodology, RLS policies,
`crm_ai.py`, `auth.py`, the public demo, the frozen research release.

## 25. Tests added

| File | Tests | Covers |
|---|---|---|
| `baec_console/tests/test_operations.py` | 71 | TLS URL policy, bounds, secret boundary, `_FILE`, redaction, readiness codes, app unavailability |
| `baec_console/tests_postgres/test_deployment_hardening.py` | 49 | TLS (client, server, identity), roles/privileges, dangerous-privilege detection, pools, timeouts, readiness, pool-leak regression |
| `baec_console/tests_postgres/test_backup_restore.py` | 16 | restore drill, damaged restores, backup-tool refusals |
| `deploy/tests/test_deploy_config.py` | 27 | compose separation, nginx config, image contents, lock, backup-tool policy |
| `deploy/tests/test_https_boundary.py` | 20 | HTTP redirect, HSTS, TLS names/versions, sign-in cookie flags, provisioning→sign-in, WebSocket origin, container credentials, logs |

## 26–32. Gate results (final code, one sequence under `caffeinate`, nothing else running)

| | Suite | Result |
|---|---|---|
| 26 | SQLite console (`baec_console/tests`): 513 + 71 | **584 passed** |
| 27 | PostgreSQL (`baec_console/tests_postgres`, `-W error::ResourceWarning`, TLS-only cluster): 971 + 65 | **1036 passed** |
| 28 | Engine 1 parity | **537 passed** (unchanged) |
| 29 | Engine 2 parity | **115 passed** (unchanged) |
| 30 | Auth: `tests/test_auth.py`; `test_production_auth.py` + `test_production_app.py` + `test_oidc_flow.py` | **50 passed; 113 passed** |
| — | Deployment (`deploy/tests`) | **47 passed** |
| 31 | Repository `tests/`, three consecutive runs | **7597 passed, 2 deselected (`live_claude`)**, each: 98.6 s, 97.4 s, 97.7 s |
| 32 | `git diff --check` (and a trailing-whitespace scan of new files) | clean |

## 33. Remaining blockers before the first controlled deployment

Not production-ready until each is done and recorded:

1. **Real infrastructure, not yet exercised:** a host, DNS for `BAEC_PUBLIC_HOST`, a publicly trusted certificate,
   and a managed PostgreSQL 16 with TLS enforced server-side, no public endpoint, and its CA pinned. Run the runbook
   §3–§10 and confirm readiness `ready`.
2. **Live OIDC provider:** register the client, confirm `iss` matches exactly, choose and freeze the subject type,
   register the redirect (and post-logout) URI, and complete one sign-in → provision → sign-in cycle.
3. **Backup storage:** an encrypted, versioned, immutable off-host store with a write-only upload credential;
   confirm platform PITR; run the restore drill once against the real platform and time it (RTO is unmeasured).
   Confirm the platform can create a BYPASSRLS backup role, or use its snapshots as the primary path.
4. **Unhealthy-container handling:** Compose does not restart an unhealthy container; add monitoring/alerting (or an
   orchestrator) that acts on readiness failure. The web process verifies the schema at startup, not continuously.
5. **Vulnerability scanning** of the images and the lock was not run (no scanner available locally).
6. **Residual risks, documented:** the Secure cookie depends on the proxy (never expose the web image directly);
   no per-session server-side logout (cookie-secret rotation is all-sessions); a copied cookie works ≤ 12 h unless
   revoked; backups contain every organization's data; `test_oidc_flow.py` and the smoke test pin Streamlit 1.65
   internals; Phase 4 §24.6 risks unchanged.
