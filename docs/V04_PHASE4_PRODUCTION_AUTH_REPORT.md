# V0.4 Phase 4 — Production Sign-In and the Production Console: Report

Branch `baec-crm-v0.4`, uncommitted working tree on top of `5bdc321` (Phase 3C accepted gate). All data and identities
are synthetic. Every test cited is a software test of the sign-in, authorization, and storage layers, or a synthetic
attack case. None is evidence for the BAEC theory, a measure of sales or purchase outcomes, or a production security
assessment. Nothing was committed, merged, or deployed. No external identity provider was contacted. The frozen
public research release was not touched. Everything below is **implementation** (CLAUDE.md labels).

**Bottom line.** A person who signs in through one OpenID Connect provider (Streamlit's built-in `st.login`, Authlib
underneath) is resolved — by the exact (issuer, subject) only, through a narrow lookup role — to a pre-provisioned,
active HUMAN BAEC user and that user's organization, and the production Console opens on PostgreSQL for that
principal. `PRODUCTION_CONSOLE_NOT_BUILT` is gone. Every console operation re-checks the sign-in link, the user, and the
session age, so revocation or deactivation refuses the next request without sign-out or restart. RLS is unchanged and
still enforced; ConsoleService ownership checks are unchanged. No development, seed, demo, or unclassified identity can
become a production principal, even with a tampered link.

| Status | What |
|---|---|
| Implemented and tested | migration 0005 (`baec_auth.auth_identities`, guard trigger, RLS, grants for two new roles); sign-in boundary (`auth.py`); lookup (`postgres/auth_lookup.py`); production composition (`production.py`); per-operation credential re-check; provisioning CLI (`postgres/provision.py`); app sign-in page, per-session console, sign-out; gate checks for the sign-in schema and lookup role; local OIDC harness through Streamlit's own routes |
| Deliberately not built | public sign-up, invitations, organization switching, roles, admin UI, user deletion, billing, CRM/email integrations, outreach; re-enabling a revoked link (a new subject is linked instead) |
| Unchanged | BAEC/Engine 1/2/3 logic and schemas (migrations 0001–0004 byte-identical), RLS policies, ConsoleService tenancy checks, SQLite development behavior, public demo and its allowlist, Claude BYOK |
| Not done | deployment, a live external identity provider, TLS termination, secret storage (§24) |

---

## 1. Accepted Phase 3C baseline

Before any change: branch `baec-crm-v0.4`, clean tree at `5bdc321`. The PostgreSQL suite was re-run once with only
the updated fixtures and gate (§19 run 1): **858 passed** — the 853 of Phase 3C plus parametrized startup cases added
when the old production tests were rewritten. Engine 1 parity 537 and Engine 2 parity 115 are re-run in §20–21.

## 2. Auth provider selected, and why

**Selected: OpenID Connect through Streamlit's built-in sign-in (`st.login` / `st.user` / `st.logout`, Streamlit
1.65.0, Authlib 1.8.0).** One provider is configured (Streamlit's default `[auth]` section); which OIDC issuer
(Google, Microsoft Entra ID, Okta, Auth0, …) is an operator choice made by configuration, not code.

| Option in this environment | Assessment |
|---|---|
| **Streamlit built-in OIDC** (chosen) | Native to the framework the console already runs on; runs the authorization-code flow on Streamlit's own server routes; Authlib validates the ID token (signature against the provider's JWKS, issuer, audience, expiry, nonce); keeps claims in an HttpOnly cookie signed with a server secret; gives the immutable `(iss, sub)`; BAEC stores no password or token; testable locally with a mock provider (§12) |
| A hosted auth SDK (Supabase, Auth0 SDK, Clerk, …) | Not in the project; adds a second session system beside Streamlit's and a vendor dependency, for no capability OIDC lacks here |
| Reverse-proxy auth headers (oauth2-proxy, IAP) | Moves verification outside the app; BAEC would have to trust a header, which is only safe with network controls this phase cannot test |
| Passwords stored by BAEC | Excluded by the brief (§19) |

Why not choose on convenience: the decisive properties are that the subject is immutable within its issuer (OIDC Core
1.0 §2: "locally unique and never reassigned"), that verification happens server-side before the app sees a claim,
and that the whole path can be exercised on this machine. One new pinned dependency: `authlib==1.8.0`
(`requirements-postgres.txt`; released 2026-08-30).

## 3. Authentication / session model

* Streamlit's routes (`/auth/login`, `/oauth2callback`, `/auth/logout`) run the flow. The callback stores the
  verified ID-token claims in `_streamlit_user`, an HttpOnly, SameSite=Lax cookie signed with `cookie_secret`
  (30 days). A browser cannot edit it without the secret (tested, §15).
* `auth.identity_from_claims` turns `st.user` into an `AuthenticatedIdentity(issuer, subject, email, issued_at)`:
  requires `is_logged_in is True`, provider `default`, `iss == BAEC_AUTH_ISSUER`, a subject of 1–255 printable ASCII
  characters, and an integer `iat`. The email is kept only when `email_verified is True` and is never used for
  authorization. Any other claim — `organization_id`, `user_id`, `role`, `groups`, `name`, `hd` — is ignored.
* **Session age (IMPLEMENTATION):** a session is accepted for 12 hours after its ID token was issued
  (`SESSION_MAX_AGE`), checked at sign-in and on every operation; after that the person signs in again. This bounds
  Streamlit's 30-day cookie.
* **Authentication never authorizes.** It yields only `(issuer, subject)`. Organization, user, kind, and name come
  from BAEC's records (§5–8). Every refusal shows one message (`NO_ACCESS`), so a refusal does not reveal whether a
  subject is linked, inactive, or of the wrong kind.

## 4. Auth identity mapping schema (migration 0005)

```
baec_auth.auth_identities
  issuer           text  NOT NULL  CHECK ^https://[!-~]+$, ≤ 2048
  subject          text  NOT NULL  CHECK ^[!-~]+$, ≤ 255
  organization_id  text  NOT NULL ┐ FOREIGN KEY → baec.users (organization_id, user_id)
  user_id          text  NOT NULL ┘
  linked_at        timestamptz NOT NULL,  linked_by text NOT NULL (operator label; a record, not authentication)
  revoked_at       timestamptz,           revoked_by text          CHECK both or neither; revoked_at ≥ linked_at
  seq              identity
  PRIMARY KEY (issuer, subject)                         -- a subject is linked once, ever, per issuer
  UNIQUE INDEX (user_id) WHERE revoked_at IS NULL       -- at most one live link per user
```

No email, name, token, password, CRM, or account data. Trigger `auth_identities_guard` (invoker rights, so its read of
`baec.users` is itself under tenant RLS): INSERT only inside the link's own organization's tenant transaction, unrevoked,
and only for an **active HUMAN** user; UPDATE only to revoke, once (every other column fixed; a revoked row never
changes); DELETE refused; TRUNCATE refused (`baec.refuse_change`). A revoked link is never restored; access returns
only through a new subject link. No function is SECURITY DEFINER.

## 5. RLS-safe user/org resolution

The organization is unknown at sign-in, so the lookup cannot be a tenant query. It is instead **narrower** than any
tenant query:

1. As `baec_auth_lookup` (separate pool, `BAEC_AUTH_DATABASE_URL`), in a READ ONLY transaction, set
   `baec.auth_issuer` and `baec.auth_subject` (transaction-local) and select the row `WHERE issuer = … AND subject = …`.
   Policies `subject_lookup` (permissive) and `subject_boundary` (restrictive) — both `FOR SELECT TO baec_auth_lookup`,
   predicate `issuer = current_issuer() AND subject = current_subject()` — make that the only row the role can ever
   see; a query without the WHERE clause still returns one row, and with no or partial settings, none. The role can read
   five columns only. A revoked row resolves to nothing.
2. The result `(user_id, organization_id)` is **not** a principal. As `baec_app`, inside that organization's tenant
   transaction (unchanged RLS), `IdentityStore.authenticated_principal(user_id)` must find the user in that
   organization, active, and HUMAN.

Global RLS is not disabled; no query searches organizations or emails; no client-supplied organization is read.

## 6. HUMAN-only enforcement

Three independent layers, each tested:

| Layer | Where | Evidence |
|---|---|---|
| Link creation | trigger (database, even for the superuser) and the provisioning tool | 4 non-HUMAN kinds × tool, provisioning role SQL, superuser SQL — all refused |
| Principal construction | `authenticated_principal` in `console_for` | a link forced to each non-HUMAN kind (triggers off, superuser) is refused |
| Console | `authentication_required=True` → `verify(require_authentication_eligible)` at construction and on every operation | mutation: removing the principal-construction layer alone is caught by this one (0 failures); removing both fails 6 tests |

`LOCAL_DEVELOPMENT`, `SYSTEM_SEED`, `PUBLIC_DEMO_SESSION`, `LEGACY_UNCLASSIFIED` are all refused. A signed-in console also
refuses `seed_service` (it would act as the synthetic-seed identity) and `set_actor` (self-rename; names are
provisioned).

## 7. Provisioning model

Pre-provisioned only: `python -m baec_console.postgres.provision` with `create-organization`, `create-user` (HUMAN only;
no kind option), `link`, `revoke`, `deactivate`, `reactivate`. It reads `BAEC_PROVISIONING_DATABASE_URL`, refuses any
role but `baec_provisioner`, and runs each command as one tenant transaction for the named organization. Errors print
the database's primary message only, never the URL. No sign-up, invitation, email-domain joining, organization
creation at sign-in, or admin UI exists.

## 8. Production principal construction

`ProductionRuntime.console_for(identity)`: (1) identity is an `AuthenticatedIdentity` of the configured issuer, and not
too old; (2) exact lookup → unrevoked link; (3) as `baec_app` in the link's organization, `authenticated_principal`
(exists there, active, HUMAN, organization exists); (4) principal organization equals the link's; (5)
`Console(PRODUCTION_ROOT, principal=…, authentication_required=True, backend=PostgresBackend, credential=…)`. The
principal's `user_id`, `organization_id`, and display name come from step 3. Any failure: `SignInRefused(NO_ACCESS)`.

## 9. Principal freshness / revocation behavior

The Phase 2 guard (`_guarded`, every public ConsoleService and Console method) now first calls
`SignedInCredential.check(principal)`: session age, then the exact lookup again, which must still return the same
`(user_id, organization_id)` as at sign-in and as the principal. Then the existing user check. Effects, each tested on
an open console in the running process:

| Event while signed in | Next operation |
|---|---|
| link revoked | refused, permanently (re-linking the same subject is impossible) |
| user deactivated | refused; restored on reactivation (Phase 2 semantics) |
| session passes 12 h | refused |
| link tampered to another organization or user | refused (no longer matches) |
| live object's principal tampered | refused (credential names someone else) |

`ui_common.attempt` now re-raises `PrincipalInvalid` instead of showing it as an action refusal, and the app calls a
guarded no-op `Console.check_access()` before drawing anything, so a session that lost access sees only the refusal
(found by a test: navigation had been drawn before the first refused read; no data was shown).

## 10. Cache / session isolation

* The only `st.cache_resource` is the development console, keyed `(path, mode)`. Production consoles are never in a
  process-wide cache.
* The process-wide `ProductionRuntime` holds pools and the issuer, no user.
* Each browser session keeps `(AuthenticatedIdentity, Console)` in its own `st.session_state`, reused only when the
  current verified identity is equal (issuer, subject, email, issued_at). A changed identity drops it and resolves
  again (tested: a session whose sign-in changes from A to B gets a new console for B; mutation "reuse for anyone" is
  caught).
* Sign-out clears the session state (console, page, Claude key), then `st.logout()` clears Streamlit's cookies; the
  run stops.
* Revocation does not depend on the browser: the guard re-checks on every operation (§9).

## 11. Database role / privilege model

| Role | Used by | Can | Cannot |
|---|---|---|---|
| `baec_migrator` | migrations | own and create the schemas | run at runtime (gate refuses it as app or lookup role) |
| `baec_app` | runtime console | tenant DML as before | anything in `baec_auth` (no USAGE); be a member of any role (new gate check) |
| `baec_auth_lookup` (new) | runtime sign-in lookup | USAGE on `baec_auth`; SELECT on 5 columns of `auth_identities`, one exact row; EXECUTE `current_issuer/current_subject` | see any row without exact settings; read `linked_by/linked_at`; read any other schema (no USAGE on `baec`, engines, migrations); write anything; SET ROLE; set `session_replication_role`; create objects |
| `baec_provisioner` (new) | operator CLI only | in one organization's tenant transaction: SELECT/INSERT organizations, users, identity_audit; UPDATE users.active; SELECT/INSERT links; UPDATE revoked_at/revoked_by | read CRM, accounts, engines; change a link's identity columns; delete or truncate |

All four are NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOINHERIT (`infra/postgres-test/initdb/00_roles.sh`, the
DBA step; migrations never create roles). Migration 0005 fails if the two new roles do not exist (observed once,
rolled back).

## 12. Production Console path

Startup (`production.open_runtime`): mode is exactly production → database gate → sign-in configuration → pools.
Then per request: sign-in page (if no verified session) → `identity_from_claims` → `console_for` (or the session's kept
console) → `check_access` → render. Storage: PostgreSQL identity, product, CRM, Engine 1, Engine 2, and Engine 3
product records through `PostgresBackend`; tenant transactions and RLS as in Phase 3C. `Console` creates no directory
on PostgreSQL; `PRODUCTION_ROOT` only names workspaces. Tests forbid `sqlite3.connect` for the whole production
end-to-end and app runs. Development (`BAEC_DEPLOYMENT_MODE=development`) is unchanged: local-development principal on
SQLite. `deployment.runtime_console` no longer accepts a user id at all: in production it always refuses.

## 13. Startup gate changes

`check_database(app_url, auth_url)` (both required) now also checks, after the product schema:

* the `baec_auth` schema against the generated manifest (tables, columns, constraints incl. PK and FK, the partial
  unique index, triggers and their enabled state, RLS flags, policies, functions, the app role's privileges — none);
* function bodies equal to the migration's, none SECURITY DEFINER;
* the four policies with their exact predicates **and role targets**;
* the lookup role (`auth_lookup_failures`): name, attributes, no memberships either way, owns nothing, schema
  privileges exactly USAGE on `baec_auth`, no table privilege anywhere, column privileges exactly the 5 SELECTs,
  executable functions exactly the two settings readers, cannot set `session_replication_role`, and — only once the
  schema itself passed — a live probe that it sees no row without exact settings;
* new for the app role: membership in any role is refused.

Failures raise `AuthSchemaRefused` (`AUTH_SCHEMA_MISMATCH`, fixed message; object names in `failures`). Any database
error during checking is now a `DatabaseRefused` (found by a test: pointing `BAEC_DATABASE_URL` at the lookup role
raised a raw `InsufficientPrivilege`). Sign-in configuration (`auth.require_configuration`): `BAEC_AUTH_ISSUER` https;
`[auth]` has exactly the default provider with `redirect_uri` (https, `/oauth2callback`), `cookie_secret` (≥ 32
characters), `client_id`, `client_secret`, `server_metadata_url` (= issuer + `/.well-known/openid-configuration`); no
named provider sections; no `expose_tokens`. Values are never echoed.

## 14. Attack-test results (§9 of the brief)

`tests_postgres/test_production_auth.py` (90), `test_production_app.py` (11), `test_oidc_flow.py` (12),
`tests/test_auth.py` (50). Organizations A and B, users A and B, fresh ids per test.

| Attack | Result |
|---|---|
| Session swapping | A's console/credential never serve B; a credential cannot carry the other principal; a browser session whose identity changes never reuses the kept console; two sessions never share one |
| Forged organization | `organization_id`, `org`, `groups` claims and `?organization=` ignored; principal stays A; B's accounts AccessDenied; Console with (A, org B) refused |
| Forged user id | `user_id`, `baec_user_id` claims and `?user=` ignored; Principal(B) with A's credential refused; tampering the live object refused |
| Subject collision | (issuer, subject) is the PK; B cannot take A's subject; one live link per user; same subject at another issuer is another identity and production accepts only its issuer; near-matches (`%`, case, prefix, trailing space) resolve to nothing |
| Inactive user | refused at sign-in, cannot be linked; reactivation restores |
| Wrong user kind | 4 kinds × (tool, provisioner SQL, superuser SQL) refused; tampered links to each refused at sign-in; HUMAN cannot seed as the seed identity |
| Mapping removed | open console refused on next operation; new sign-in refused; same subject never re-linkable |
| Deactivated while logged in | next read and write refused, nothing written; app shows only the refusal |
| Organization mismatch | link re-pointed to org B (superuser, triggers off) → open console refused, new sign-in refused (user absent in B); re-pointing to B's linked user blocked by the one-live-link index even for the superuser |
| Unknown vs known subject | identical message |
| Expired session | refused at sign-in and while open |

## 15. End-to-end auth results

* **App, as deployed** (`test_production_app.py`, real gate and pools, `sqlite3.connect` forbidden): sign-in page only
  → sign-in → own console with BAEC's name (not the provider's) and verified email → own account listed, B's not →
  forged query parameters ignored → revoke or deactivate → next request shows only the refusal and Sign out → sign-out
  clears the session including the Claude key → sign-in page. **11 passed.**
* **Provider layer** (`test_oidc_flow.py`): a synthetic OIDC provider on 127.0.0.1 signs RS256 ID tokens; Streamlit's
  unmodified `/auth/login` and `/oauth2callback` run the code flow and Authlib validates the token; the cookie is
  parsed with Streamlit's websocket parser and fed to `identity_from_claims` → `console_for`: own account, foreign
  refused, revoke → refused although the browser cookie is still valid. No sign-in for: another signing key, another
  audience, another issuer, expired, replayed or missing nonce, replayed code, a callback started by another browser,
  a cookie signed with another secret, an edited cookie, another origin. Linked-nowhere subject → `NO_ACCESS`.
  **12 passed.**
* Engine/database: login → resolve → console → own account → foreign refused → revoke → refused, with SQLite forbidden
  (`test_sign_in_opens_the_production_console_on_postgresql_and_revocation_closes_it`).

Mutation checks (each applied to the source, the three Phase 4 PostgreSQL files run, file restored byte for byte):

| Mutant | Failing tests |
|---|---|
| credential check is a no-op | 9 |
| guard skips the credential | 8 |
| revoked links still resolve | 5 |
| app reuses a kept console for anyone | 1 |
| issuer claim not checked | 2 |
| no session age | 2 |
| gate skips the lookup role | 13 failed, 8 errors |
| principal construction skips the HUMAN check | 0 (equivalent: the console layer refuses; §6) |
| both HUMAN layers removed | 6 |

## 16. Files changed

New: `baec_console/auth.py`, `production.py`, `postgres/auth_lookup.py`, `postgres/provision.py`,
`postgres/migrations/0005_auth_identities.sql`; tests `tests/test_auth.py`, `tests_postgres/test_production_auth.py`,
`test_production_app.py`, `test_oidc_flow.py`; this report.

Modified: `app.py` (production sign-in flow, per-session console, sign-out; development unchanged),
`console_service.py` (credential in the guard; `check_access`; production refusals for seeding and rename; no
directory on PostgreSQL), `deployment.py` (no user-id seam), `storage.py` (gate returns its report; auth URL),
`backends.py` (docstring), `ui_common.py` (`attempt` re-raises `PrincipalInvalid`), `postgres/gate.py` (sign-in schema
and lookup role checks; app-role membership; database errors are refusals), `postgres/catalog.py` (`baec_auth` in the
manifest), `postgres/schema_manifest.json` (regenerated; additive only, 178 lines, no existing fact changed),
`README_CONSOLE.md`, `requirements-postgres.txt` (`authlib==1.8.0`), `infra/postgres-test/docker-compose.yml`,
`infra/postgres-test/initdb/00_roles.sh`, `scripts/postgres_test_db.sh` (two roles, generated passwords; `down`/`reset`
create missing entries first); tests `tests/test_identity_freshness.py`, `tests/test_storage.py`,
`tests_postgres/conftest.py`, `test_migrations_and_gate.py`, `test_live_schema_gate.py` (expectations moved from
`PRODUCTION_CONSOLE_NOT_BUILT` and `authenticated_user_id` to the new startup and sign-in path; version pins 4 → 5).

Not modified: Engine 1/2/3 code, BAEC methodology, migrations 0001–0004, RLS policies, the public demo and
`public_runtime.py`, `crm_ai.py` (Claude BYOK), the frozen research release.

## 17. Migrations added

`0005_auth_identities.sql` — schema `baec_auth`, 1 table, 3 functions, 2 triggers, 1 partial unique index, 4 policies,
grants to `baec_auth_lookup` and `baec_provisioner`. Checksum
`eb3a2b6be86ebd4e5e58c9f4313c260c3bfccc42cae9595f40aaf106c715ec56`. Requires the two roles to exist (DBA bootstrap).

## 18–23. Results

Local PostgreSQL 16 container (`scripts/postgres_test_db.sh reset`, so the two new roles exist); `.venv-postgres`
(psycopg 3.2.10, Authlib 1.8.0, Streamlit 1.65.0) for PostgreSQL; the main venv (no psycopg, no Authlib) for SQLite and
the repository suite.

| | Suite | Result |
|---|---|---|
| 18 | `baec_console/tests` (SQLite, no network): 462 existing (updated where they pinned the removed seam) + 50 `test_auth.py` + 1 new public-runtime case (`Console.check_access` is refused by the unchanged public allowlist) | **513 passed** |
| 19 | `baec_console/tests_postgres`, all, `-W error::ResourceWarning`: Phase 3C 853 + rewritten startup cases + `test_production_auth.py` 90 + `test_production_app.py` 11 + `test_oidc_flow.py` 12 | **971 passed**, 0 failed |
| 20 | Engine 1 parity (`test_engine1_parity.py`, `test_engine1_storage.py`, `test_engine1_suites_on_postgresql.py`) | **537 passed** (unchanged) |
| 21 | Engine 2 parity (`test_engine2_parity.py`) | **115 passed** (unchanged) |
| 22 | repository `tests/`, three consecutive runs, `caffeinate`, nothing else running | **7597 passed, 2 deselected (`live_claude`), 0 failed** each: 100.29 s, 99.19 s, 99.15 s |
| 23 | `git diff --check`, and a trailing-whitespace check of the new files | clean |

Fixed along the way, each found by a test (none by loosening a test): the gate's `::regclass` lookup of `baec_auth`
failed as `baec_app` (no USAGE; now joined by name); a raw `InsufficientPrivilege` escaped the gate for a wrong role
(now `DatabaseRefused`); the live lookup probe could error on an already-broken schema (now runs only once the schema
passed); missing-URL vs missing-driver ordering; `urllib` in a console module (the offline boundary test forbids it;
replaced by an explicit https check); navigation drawn before a refusal (§9); sign-out continuing to render after
clearing the session; `scripts/postgres_test_db.sh reset` failing on an older `.env`.

## 24. Remaining blockers before deployment

1. **TLS and cookies.** Streamlit sets its sign-in cookie without `Secure` (deliberately, per its source), and its
   session cookie is `Secure` only when Streamlit itself terminates TLS. Deployment must serve only over HTTPS (with
   HSTS) and never expose plain HTTP; the gate cannot see this.
2. **Server-side sign-out.** `st.logout` deletes the cookie in that browser only; a copied cookie stays valid to
   Streamlit until it expires. BAEC bounds it (12 h session age) and can kill it (revoke the link or deactivate the
   user), but there is no per-session server-side revocation.
3. **A real provider.** Not exercised against a live issuer. Before deployment: register the client, confirm the
   issuer's `iss` equals `BAEC_AUTH_ISSUER` exactly, that `sub` is the provider's stable subject (some providers offer
   pairwise or per-app subjects — choose one and never change it), and run the sign-in once end to end.
4. **Secrets and operations** (Phase 3 §12.4, unchanged): secret storage for the three database URLs, `cookie_secret`
   and `client_secret`; the provisioning URL never in the app environment; TLS to PostgreSQL; backups; connection
   limits; regenerate `schema_manifest.json` with each migration.
5. **Streamlit coupling.** `test_oidc_flow.py` uses Streamlit 1.65 internals (route names, cookie parser); re-run on
   any Streamlit or Authlib upgrade.
6. **Residual risks, documented:** the database is the authority for links, so a superuser can re-point one (the gate
   pins privileges, policies, and trigger; the one-live-link index blocks re-pointing to a linked user); the lookup
   role can test whether a guessed (issuer, subject) is linked — equivalent to attempting a sign-in, and subjects are
   provider-issued; a revoked person needs a new subject link (by design); the application chooses its tenant context,
   so RLS protects against application bugs, not a compromised process (Phase 3C §25, unchanged).
