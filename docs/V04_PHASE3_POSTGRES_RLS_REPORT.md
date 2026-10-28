# V0.4 Phase 3 — PostgreSQL Persistence and Row-Level Security Report

Branch `baec-crm-v0.4`, on top of `24cde4e` (Phase 2 accepted gate: 441 passed). All data is synthetic. Every test
cited here is a software test of the storage boundary, not evidence for the theory and not a production security
assessment. Nothing was deployed; no external database was used.

**Bottom line.** Product data (identity, console store, CRM) now has a PostgreSQL schema whose tenant isolation is
enforced by the database (row-level security, composite foreign keys, privileges, triggers), proven by direct attack
tests that bypass ConsoleService. **Production is still closed**: Engine 1 and Engine 2 records have no PostgreSQL
store, ConsoleService still runs only on SQLite, and production never uses SQLite. No subsystem outside PostgreSQL is
claimed to have database-level isolation.

| Status | What |
|---|---|
| Implemented and tested | PostgreSQL product schema (2 migrations), RLS on 20 tenant tables, role model, tenant context, pool reset, production database checks, SQLite → PostgreSQL importer, PostgreSQL stores with SQLite parity |
| Implemented, not yet used by the console | `PostgresProductStorage` (the console still opens its SQLite stores) |
| Unchanged | Engine 1, Engine 2, Engine 3 logic and persistence; Claude BYOK; the frozen public research release |
| Planned (blockers, §12) | Engine persistence in PostgreSQL, routing ConsoleService through the product storage, identity resolution for a real sign-in |

---

## 1. Persistence audit (before implementation)

Data root layout (unchanged): `identity.sqlite3` once per root; then one workspace directory per store set —
`local/` (**shared by every organization**), `synthetic/` (the local-development organization's demo) and
`synthetic-organizations/<sha256(org)[:32]>/` (each other organization's demo). Each workspace directory holds
`engine1.sqlite3`, `engine2.sqlite3`, `console.sqlite3`, `crm.sqlite3`. The public demo uses a temporary directory per
session (`public_runtime.py`, `mkdtemp`), read-only and synthetic.

| Store / table | Owner module | Mutability | Account | Organization | User attribution | FKs | Abstracted? | Reached outside ConsoleService? | Migration difficulty |
|---|---|---|---|---|---|---|---|---|---|
| identity: `organizations` | `identity.py` | no delete (trigger) | — | is the org | — | — | `IdentityStore` | `deployment.py` (Phase 2 production path; removed now), `public_runtime.py` (provision) | low |
| identity: `users` | `identity.py` | rename, (de)activate; org and kind fixed (triggers); no delete | — | `organization_id` FK | — | → organizations | `IdentityStore` | same | low |
| identity: `identity_audit` | `identity.py` | append-only | — | via user | `user_id` | → users | `IdentityStore` | no | low |
| console: `account_ownership` | `console_store.py` | append-only | key | **the tenancy mapping** | `recorded_by_user_id` | none | `ConsoleStore` | no | low |
| console: `account_meta` | `console_store.py` | never mutated (no trigger) | key | via ownership | label `created_by` | none | `ConsoleStore` | no | low |
| console: `interaction_meta`, `condition_drafts`, `preparation_requests`, `preparation_blocks`, `events` | `console_store.py` | append-only | `account_id` | via ownership | labels | none | `ConsoleStore` | no | low |
| console: `draft_resolutions`, `preparations`, `preparation_edits`, `preparation_reviews`, `preparation_provenance` | `console_store.py` (Engine 3 records; Engine 3 has no persistence) | append-only | via draft / request / preparation | via ownership | labels | yes (within the file) | `ConsoleStore` | no | low |
| console: `attributions` | `console_store.py` | append-only | `account_id` | `organization_id` | `user_id` + name snapshot | none (user lives in another file) | `ConsoleStore` | no | low |
| console: `migration_log` | `console_store.py` | append-only | — | **none** (workspace-wide; `local/` spans orgs) | — | — | `ConsoleStore` | no | not tenant data |
| console: `console_meta`, `prefs` | `console_store.py` | mutable metadata | — | — | — | — | raw | `legacy_actor_name` reads `prefs` | not migrated |
| CRM: `contacts`, `interaction_links` | `crm_store.py` | never mutated | `account_id` | via ownership (other file) | label | none | `CRMStore` | no | low |
| CRM: `tasks` | `crm_store.py` | complete once | `account_id` | via ownership | labels | none | `CRMStore` | no | low |
| CRM: `activities` | `crm_store.py` | Engine 1 link once | `account_id` | via ownership | label | none | `CRMStore` | no | low |
| Engine 1 (`engine1.sqlite3`): 25 tables, 92 triggers | `baec_app/data/database.py`, `repository.py`, `ai_provenance.py`, `proposal_bridge.py`, `authorization_grants.py` | append-only, verbatim-evidence triggers, STRICT, GLOB / JSON1 checks, `PRAGMA user_version` | `account_id` | **none** | actor labels | yes | `Repository(sqlite3.Connection)`; raw connections elsewhere | `engine1_adapter.Engine1Session`, `baec_app` application/MCP composition, scripts | **high** |
| Engine 2 (`engine2.sqlite3`): 30 tables, 92 triggers | `baec_app/engine2/persistence.py` | append-only, STRICT, JSON checks, `rowid` order, audit events in the same transaction | via plan → BAEC | **none** | actor labels | yes (composite) | module functions taking `sqlite3.Connection` | `engine2_adapter`, operator UI, public demo, scripts | **high** |
| Engine 3 | `baec_app/engine3` | no persistence (pure validator) | — | — | — | — | — | — | n/a |

Ownership and attribution are product-side mappings in `console.sqlite3`; engine records carry no organization.

## 2. Engine storage classification (3B)

| Engine | Class | Reason |
|---|---|---|
| Engine 1 | **C — too coupled to migrate safely in this phase** | 92 triggers carry validated invariants (append-only, no row replacement, verbatim interaction evidence, immutable decision columns, the AI proposal bridge's closed vocabularies and grant/consumption rules) written in SQLite dialect (`RAISE(ABORT)`, `GLOB`, `json_each`, STRICT). The repository, AI provenance, proposal bridge, and grant modules take a raw `sqlite3.Connection`, catch `sqlite3.IntegrityError`, read `PRAGMA user_version`, and order by `rowid`. Exact parity would mean re-proving every trigger in PL/pgSQL. |
| Engine 2 | **B — separable without changing semantics, but not attempted** | All SQL is confined to `persistence.py`, so a PostgreSQL port is possible without touching decision logic, but it has no repository interface (module functions over a raw SQLite connection) and 92 triggers to port and re-prove. That is a phase of its own. |
| Engine 3 | **A — nothing to migrate** | No persistence. Its records (requests, preparations, edits, reviews, provenance) are product-owned console tables and **were** moved. |

No engine code or engine decision logic was changed. Engine persistence stays SQLite (§12).

## 3. Storage boundary (3C)

* `baec_console/storage.py` — one decision point per deployment mode, made before any store is opened:
  development = SQLite only (refused if `BAEC_DATABASE_URL` is set, instead of silently ignoring it); production =
  PostgreSQL only, checked by `postgres/gate.py`, then refused by `ENGINE_STORAGE_BLOCKER`.
* `baec_console/postgres/stores.py` — `PostgresProductStorage.session(organization_id, workspace)` yields a
  `ProductSession` with `.identity`, `.console`, `.crm`: `PgIdentityStore`, `PgConsoleStore`, `PgCRMStore`, with the
  same method names, arguments, return values, and error types as `IdentityStore`, `ConsoleStore`, `CRMStore`.
  `PgIdentityStore` subclasses `IdentityStore`, so `principal`, `verify`, and `authenticated_principal` are the same
  code on both backends. One session is one transaction, so its three stores commit together.
* No `if postgres` branches were added to business logic. **ConsoleService is not yet routed through this
  boundary** (§12.2).

## 4. PostgreSQL schema (3E)

`baec_console/postgres/migrations/0001_product_schema.sql`, schema `baec`: `organizations`, `users`,
`identity_audit`, `account_ownership`, `account_meta`, `interaction_meta`, `condition_drafts`, `draft_resolutions`,
`preparation_requests`, `preparations`, `preparation_edits`, `preparation_reviews`, `preparation_provenance`,
`preparation_blocks`, `events`, `attributions`, `crm_contacts`, `crm_tasks`, `crm_interaction_links`,
`crm_activities`, and the operator-only `operator_log`. Migration versions live in
`baec_migrations.schema_migrations`.

* Every tenant row carries `organization_id`; every workspace row also carries `workspace` (`local` / `synthetic`), so
  rows from separate SQLite files keep their own ids. Keys are `(organization_id, workspace, id)`.
* **Composite foreign keys include the organization.** Every account-scoped row references
  `account_ownership (organization_id, workspace, account_id)`; attributions, ownership, and the identity audit
  reference `users (organization_id, user_id)`; a CRM contact on a task, link, or activity must be a contact of the
  **same account** (`(organization_id, workspace, account_id, contact_id)`). A cross-tenant relationship is a
  foreign-key violation, and referencing another organization's id fails exactly like referencing a nonexistent id.
* `NOT NULL`, `CHECK` (kinds, workspace, bases, resolutions, decisions, priorities, task completion consistency, the
  fixed provenance origins, `model_used = false`, non-blank ids), and `IS JSON` checks on every JSON column.
* JSON columns are `text`, so drafts and preparations are stored byte for byte as the console wrote them (no
  `jsonb` re-normalization of numbers or key order). Buyer text is copied unchanged.
* Timestamps are `timestamptz`: the same instant; the original UTC offset text is not kept.
* Append-only triggers on every table except the three mutations the stores make: user rename/activation (org,
  kind, id fixed), task completion (once, completion fields only), activity → Engine 1 link (once). `TRUNCATE` is
  refused by trigger on every table, for every role.
* **Deliberately stricter than SQLite** (tested in `test_parity.py`): records of unowned accounts are refused;
  `account_meta`, contacts, and links refuse changes SQLite merely never made; task titles cannot change.

## 5. Row-level security (3F)

`0002_row_level_security.sql`, applied to all 20 tenant tables:

```sql
ALTER TABLE baec.<t> ENABLE ROW LEVEL SECURITY;
ALTER TABLE baec.<t> FORCE ROW LEVEL SECURITY;           -- the owning migration role is filtered too
CREATE POLICY tenant_isolation ON baec.<t> AS PERMISSIVE  FOR ALL
  USING (organization_id = baec.current_organization()) WITH CHECK (organization_id = baec.current_organization());
CREATE POLICY tenant_boundary  ON baec.<t> AS RESTRICTIVE FOR ALL
  USING (organization_id = baec.current_organization()) WITH CHECK (organization_id = baec.current_organization());
```

`baec.current_organization()` is `nullif(current_setting('baec.organization_id', true), '')`. With no organization
set it is NULL, the predicate is never true, and nothing is visible or writable (fail closed). The restrictive
policy is ANDed with every permissive policy, so a permissive policy added later cannot widen access; the gate also
refuses any extra policy. Policies contain no interpolated organization id: the id reaches PostgreSQL only as a bound
parameter of `set_config`.

## 6. Database roles (3G)

| Role | Created by | Attributes | Privileges |
|---|---|---|---|
| bootstrap superuser (`baec_bootstrap` in the test container) | the container / a DBA | superuser | creates the two roles and database grants (`infra/postgres-test/initdb/00_roles.sh`). Never used by the application or the migrations. |
| `baec_migrator` | bootstrap | `NOSUPERUSER NOBYPASSRLS NOCREATEROLE NOCREATEDB NOINHERIT` | `CONNECT, CREATE` on the database; owns `baec` and `baec_migrations`; runs migrations; advances id sequences and writes `operator_log` during an import. Subject to RLS on tenant tables (`FORCE`). |
| `baec_app` | bootstrap | `NOSUPERUSER NOBYPASSRLS NOCREATEROLE NOCREATEDB NOINHERIT` | `CONNECT`; `USAGE` on both schemas; `SELECT, INSERT` on tenant tables; `UPDATE` only on `users(display_name, active)`, `crm_tasks(status, completed_at, completed_by)`, `crm_activities(engine1_interaction_id)`; `SELECT` on `schema_migrations`. **No** `DELETE`, `TRUNCATE`, `REFERENCES`, `TRIGGER`, `CREATE`, or ownership; no access to `operator_log`. |

The role name `baec_app` is fixed by the grants in migration 0002; a deployment creates it with that name. Tests
that need elevated setup (schema reset, sabotage, probe roles) use the bootstrap superuser; every assertion about the
application runs as `baec_app`. The migration runner refuses to run as `baec_app`.

## 7. Tenant context (3F)

`postgres/database.py`:

* `act_for(connection, org)` runs `SELECT pg_catalog.set_config('baec.organization_id', %s, true)` — transaction-local,
  bound parameter, refused outside a transaction, refused for a blank id.
* `tenant_transaction(connection, org)` refuses a connection that already carries an organization, opens one
  transaction, and acts for `org`. PostgreSQL discards the setting at commit or rollback.
* `TenantDatabase.tenant(org)` takes a pooled connection and runs `tenant_transaction`; it is the only way the stores
  are reached.

RLS protects against omitted or wrong application filters and query bugs. It does not protect against a compromised
application process, which chooses the organization it acts for (§12.5).

## 8. Connection-pool safety (3I)

`psycopg_pool.ConnectionPool` with `autocommit=True` (the only transactions are the explicit tenant ones),
`reset = DISCARD ALL` on every return (clears even a session-wide setting), `check_connection` on checkout, and the
pre-check that refuses a connection still carrying an organization. **Bug found and fixed during this phase:**
`DISCARD ALL` drops server-side prepared statements while psycopg's auto-prepare still referenced them
(`prepared statement "_pg3_0" does not exist`). Pooled connections now use `prepare_threshold=None`. Regression test:
`test_alternating_requests_on_one_pooled_connection_never_inherit_the_previous_tenant`.

`test_pool.py` (7 tests): 20 alternating A/B requests on a one-connection pool (same backend pid every time) never
see the previous tenant, and between requests the connection has none; a deliberately session-wide setting is gone
before the next request; a failed request leaves nothing behind; a connection that carries an organization is
refused; context is never set outside a transaction or without an id; 4 threads × 25 concurrent requests on two
organizations see only their own; a session's writes roll back together.

## 9. Direct-database attack tests (3H)

`test_rls.py` (22 tests) never uses ConsoleService or a store filter. Organizations A and B each have a row in
every one of the 20 tenant tables; each case runs as A against B **and** as B against A. To show each layer on its
own, a `probe` role holds every DML privilege but is still `NOBYPASSRLS` and not an owner, and runs with
`session_replication_role = replica` (no triggers, no foreign keys), so RLS is the only check left.

| Attack (as tenant X against tenant Y) | Role | Result | Layer proven |
|---|---|---|---|
| `SELECT` every tenant table, no `WHERE` | app | only X's rows; `WHERE organization_id = Y` → 0 | RLS |
| look up Y's user / task by id; identical buyer text | app | no rows; only X's copy | RLS |
| no organization, `''`, `x' OR '1'='1`, `% OR true` | app | 0 rows in every table; inserts refused | RLS (fail closed, no injection) |
| `INSERT` a copy of a row claiming Y, every table | app | `new row violates row-level security policy` | RLS `WITH CHECK` |
| `UPDATE` Y's task / activity / user by id; unfiltered `UPDATE` | app | 0 rows; unfiltered touches only X's 1 row; Y unchanged | RLS |
| `UPDATE … WHERE organization_id = Y`, every table | probe | 0 rows | RLS alone |
| move X's rows to Y (`SET organization_id = Y`), every table | probe | `row-level security` violation | RLS alone |
| `DELETE … WHERE organization_id = Y`, every table | probe | 0 rows, while X's own rows *are* deletable by the same role | RLS alone |
| `DELETE`, `TRUNCATE`, `UPDATE organization_id`, every table | app | `permission denied` | privileges |
| contact on Y's account; task/activity with Y's contact; attribution, ownership, audit naming Y's user; preparation on Y's request; event on Y's account | app | `ForeignKeyViolation`, identical to a nonexistent id | composite foreign keys |
| `UPDATE` / `DELETE` X's own append-only rows (8 tables) | probe, triggers on | `… is append-only` | triggers |
| store reads/writes of Y's ids (account, interaction, events, contacts, task completion, activity link, user, verify, rename, ownership claim) | stores as X | `None` / `[]` / the store's own "does not exist" error / RLS violation | stores + RLS |

A mutation check during development (RLS temporarily disabled on `crm_tasks`) made a tenant see two organizations'
tasks, confirming these queries detect a missing policy.

The 144+ application-layer cross-tenant tests from Phases 1–2 are retained unchanged in `baec_console/tests`.

## 10. Production startup (3D, 3M)

`deployment.runtime_console` in production:

1. no authenticated user → `ProductionAccessRefused` (unchanged; nothing is checked or opened);
2. `storage.require_production_storage()` → `postgres/gate.check_database(BAEC_DATABASE_URL)`. A refusal lists
   every failure and never contains the URL or its password:
   * URL absent, blank, malformed, bad port, or not `postgresql://` / `postgres://` with user, host, and database
     (a `sqlite:` URL or file path is refused);
   * psycopg not installed;
   * database unreachable, authentication failed, or unknown database (server message withheld);
   * schema not at exactly the migrations this code has (same versions, same SHA-256);
   * the role is a superuser, can bypass RLS, can create roles or databases, belongs to a role that can, owns any
     BAEC object or schema, can `CREATE` in `baec`, `baec_migrations`, or `public`, can write `schema_migrations`,
     can read `operator_log`, or has `DELETE`, `TRUNCATE`, `REFERENCES`, or `TRIGGER` on any tenant table;
   * the set of tables with `organization_id` is not exactly the 20 expected, or any lacks enabled **and** forced RLS
     with exactly the two expected policies on the expected predicate;
3. if every check passes, production is still refused with `ENGINE_STORAGE_BLOCKER`.

Nothing under the data directory is created or opened in production; there is no SQLite fallback. The gate never
changes a privilege, policy, or schema: `test_an_unsafe_schema_or_grant_is_refused_and_never_repaired` sabotages a
scratch database nine ways and checks each is refused twice. In development, `BAEC_DATABASE_URL` set → refused.

**Changed accepted behavior.** Phase 2 let production serve an eligible HUMAN user from the SQLite identity store
(`test_production_accepts_an_eligible_human_and_keeps_checking_it`). Phase 3 forbids production on SQLite, so seven
assertions in `test_identity_freshness.py` now expect `storage.StorageRefused` before any identity lookup. Nothing
they covered was dropped: the eligibility refusals are asserted directly through `authenticated_principal`, and the
"accepts an eligible human and keeps re-checking it" behavior moved to
`test_an_authentication_required_console_accepts_an_eligible_human_and_keeps_checking_it`.

## 11. Migrations and the SQLite import (3J, 3K)

**Migrations** (`python -m baec_console.postgres.migrate`, `BAEC_MIGRATION_DATABASE_URL`): `NNNN_name.sql`, versions
1..n with no gaps, each applied once in its own transaction under an advisory lock and recorded with its SHA-256.
Re-running applies nothing. A changed or unknown recorded migration stops the run. Forward only; nothing is dropped
or reset. RLS policies and grants are migration 0002.

**Import** (`python -m baec_console.postgres.importer SOURCE_ROOT [--dry-run]`, `BAEC_DATABASE_URL` = application
role, `BAEC_MIGRATION_DATABASE_URL` = migration role for a real import). Never automatic.

* Opens the source read-only; requires current schemas and completed backfills (refuses rather than migrating).
* Every row must resolve to an owning organization; synthetic workspaces must belong to their directory's owner.
* Inserts as the application role, acting for each row's organization, so RLS checks every row.
* Preserves ids (including integer event, block-decision, and audit ids), timestamps (as instants), ownership,
  attribution, actor labels, JSON text, and buyer text exactly. Regenerates nothing.
* Identical existing row → skipped; differing existing row, or a database refusal (for example an id held by an
  organization the import cannot see) → conflict. **Any conflict refuses the whole import**; nothing is updated.
* One transaction. `--dry-run` performs and verifies the full import, then rolls back.
* Verification re-reads every organization through the PostgreSQL stores and compares with the SQLite stores.
* Reports counts per organization and table: source, target before, inserted, already present, target after.
* Real import: advances id sequences past imported ids (forward only) and writes the report plus the source
  `migration_log` entries to `operator_log`. No credential is printed or stored.
* Not imported: Engine 1/2 stores, `prefs`, store metadata rows.

Operator run on a fresh synthetic development root (local-development org, completed demo, one account and contact):
dry run 61 rows inserted then rolled back, verification matched (29 checks); real import 61 inserted, matched;
second run 0 inserted, 61 already present; source file hashes unchanged; no credential in the output. Running it
against a database that already held a *different* root's `local` organization refused with 5 conflicts and wrote
nothing.

## 12. Unresolved production blockers

1. **Engine persistence (the blocker).** Engine 1 (class C) and Engine 2 (class B) records stay in SQLite, in a
   `local/` workspace shared by every organization, with no organization column. Their isolation is still only the
   application-layer ownership check. Production is closed until they have a PostgreSQL store proven by
   conformance tests on both backends, or another design that never shares them.
2. **ConsoleService is not on PostgreSQL.** It opens SQLite paths directly and resets the synthetic workspace by
   deleting files. PostgreSQL tables are append-only, so synthetic reset needs a design (for example a separate
   synthetic database, or no synthetic data in production) before the service can be routed through
   `PostgresProductStorage`. Product and engine writes would also span two databases with no shared transaction (as
   today across SQLite files).
3. **Identity resolution for a real sign-in.** Under RLS a user can only be read once the organization is known. A
   sign-in must therefore supply the organization (a verified claim), or a narrowly scoped lookup that maps a
   provider subject to `(user_id, organization_id)` is needed. Neither exists.
4. **Production operations not covered.** TLS (`sslmode`) is not required by the gate; secret storage for
   `BAEC_DATABASE_URL`, backups, and connection limits are undesigned.
5. **Residual risks, documented.** The application chooses its tenant context, so RLS guards against application
   bugs, not a compromised process. `organizations.organization_id` and `users.user_id` are globally unique, so an
   insert can learn that an id exists elsewhere (no data is revealed); tenant data keys include the organization and
   have no such channel.

**The exact blocker before adding real authentication:** there is no production storage a signed-in user could be
served from with database-enforced isolation. Engine 1 and Engine 2 records still live only in shared SQLite, and
ConsoleService does not yet use the PostgreSQL product store, so production refuses every request
(`ENGINE_STORAGE_BLOCKER`) whoever signs in.

## 13. How to run

```sh
# one-time: an isolated environment with psycopg (gitignored)
"$MAIN/.venv/bin/python" -m venv .venv-postgres
.venv-postgres/bin/python -m pip install -r requirements.txt -r requirements-dev.txt -r requirements-postgres.txt

scripts/postgres_test_db.sh test     # start a disposable local container (127.0.0.1:55439) and run the suite
scripts/postgres_test_db.sh down     # stop it; data is on tmpfs and is discarded
"$MAIN/.venv/bin/python" -m pytest baec_console/tests -q   # the ordinary suite: no database, no network
```

The container is `postgres:16-alpine`, named `baec-postgres-test`, bound to `127.0.0.1` only. Test passwords are
generated per machine into `infra/postgres-test/.env` (gitignored, mode 600); none are in the repository.

## 14. Results

| Suite | Result |
|---|---|
| `baec_console/tests` (SQLite, no network), run 1 | 459 passed |
| `baec_console/tests` (SQLite, no network), run 2 | 459 passed |
| `baec_console/tests_postgres` (local PostgreSQL 16), run 1 / run 2 / from a freshly reset cluster | 71 / 71 / 71 passed |
| `baec_console/tests_postgres` in a new venv built only from the three requirements files | 71 passed |
| repository `tests/` | 7576 passed (after the stale requirements test was corrected; see below) |

The former root-suite failure, `tests/test_public_demo.py::test_the_runtime_requirements_are_exactly_the_two_verified_pins`,
predated Phase 3: it required `requirements.txt` to be exactly `streamlit==1.65.0` and `pydantic==2.13.5`, but the
file has had a third, bounded AI-provider line since before V0.3 (`openai>=2.0.0,<3.0.0`, replaced for Claude BYOK by
`anthropic>=1.0.0,<2.0.0` in `4153f46`). It is now
`test_the_runtime_requirements_are_exactly_the_verified_pins_and_the_bounded_claude_sdk`, which requires exactly those
three entries (no addition, removal, unpinning, or widening), the dev pin `anthropic==1.9.0` alongside
`streamlit==1.65.0`, and the installed versions 1.65.0 / 2.13.5 / 1.9.0. Phase 3 does not touch `requirements.txt`
(the driver is in `requirements-postgres.txt`).

459 = the 441 Phase 2 tests (seven assertions updated, one test split in two) + 17 in `test_storage.py`. The 71
PostgreSQL tests: 22 RLS attack, 7 pool, 23 migration and gate, 3 parity (102 compared steps), 16 import.
