# V0.4 Phase 3B — Production Persistence Chain Report

Branch `baec-crm-v0.4`, on top of `ef15e8b` (Phase 3 accepted gate). All data is synthetic. Every test cited here
is a software test of persistence behavior, or a synthetic conformance case, not evidence for the theory and not a
production security assessment. Nothing was deployed, merged, or committed. No authentication was added.

**Bottom line.** ConsoleService now reaches every store through a storage backend, and the same service code runs on
SQLite (development) and on PostgreSQL (test mode: identity, console, CRM, and Engine 2 in PostgreSQL). Engine 2 has
a PostgreSQL store whose behavior is identical to SQLite's in every scenario tested, including all 92 trigger
invariants. **Production is still closed**: Engine 1 has no PostgreSQL store, and the startup refusal names it.

| Status | What |
|---|---|
| Implemented and tested | Storage backend boundary (`backends.py`); ConsoleService on SQLite and PostgreSQL with one service code path; one PostgreSQL transaction per service action; Engine 2 PostgreSQL schema (migration 0003) and adapter; Engine 2 dual-backend conformance; production gate covering Engine 2 tables; engine-blocker refusal naming Engine 1 |
| Designed, not built | Engine 1 PostgreSQL port (`docs/V04_ENGINE1_POSTGRES_MIGRATION_PLAN.md`); synthetic/demo dataset model on PostgreSQL (§10) |
| Unchanged | Engine 1, Engine 2, and Engine 3 code and semantics (no file under `baec_app/` was modified); development defaults; the frozen public research release |
| Not added | authentication, sessions, deployment, outreach, any new methodology |

---

## 1. Accepted Phase 3 baseline

Re-run before any change: `baec_console/tests` **459 passed**; `baec_console/tests_postgres` **71 passed** (local
PostgreSQL 16 container, `scripts/postgres_test_db.sh test`). Phase 3's state: PostgreSQL product schema with RLS on
20 tables, `PostgresProductStorage` implemented but unused by the console, Engine 1 class C and Engine 2 class B,
production refused by `ENGINE_STORAGE_BLOCKER`.

## 2. Product-store ConsoleService cutover architecture

`baec_console/backends.py` (new) is the one place ConsoleService gets a store. The service's methods, guards, domain
calls, and refusals are unchanged; only how a store is opened changed (`_cs()`, `_crm()`, `_e2()`, identity, and the
write-block helper `_writing(store)`, which replaced `transaction(cs.db)` at all 15 sites).

| | `SQLiteBackend` (development, default) | `PostgresBackend` (PostgreSQL test mode) |
|---|---|---|
| identity | `identity.sqlite3` | `PgIdentityStore` |
| console, CRM | `console.sqlite3`, `crm.sqlite3` per workspace | `PgConsoleStore`, `PgCRMStore` |
| Engine 2 | `engine2.sqlite3` per workspace | `baec_engine2` via `Engine2Connection` (§5) |
| Engine 1 | `engine1.sqlite3` per workspace | `engine1.sqlite3` per workspace (**no PostgreSQL store**) |
| service action (`unit()`) | nothing: each store is its own file | one tenant transaction (organization + workspace) shared by identity, console, CRM, Engine 2 |
| write block (`writing()`) | `BEGIN IMMEDIATE` on that file | savepoint of the action's transaction |
| synthetic reset | deletes the workspace files (unchanged) | refused, nothing read (`RESET_UNAVAILABLE`, §10) |

* Every public method was already wrapped by the principal-freshness guard; the guard now also opens the backend
  unit, so the principal check and the operation share one PostgreSQL transaction. Nested public calls join it.
* A unit commits when the action returns. It also commits when the action raises an exception marked
  `CommitsRecordedRefusal` — only `PreparationRefused`, whose generation path records `PREPARATION_REFUSED` before
  raising (exactly what SQLite keeps). Any other exception rolls the whole action back.
* `Console(..., backend=...)` and `ConsoleService(..., backend=...)` take the backend; omitted, it is
  `SQLiteBackend(root)`. `Console.local_development` and the public demo are unchanged and never import psycopg
  (`test_the_development_console_runs_on_the_sqlite_backend_and_never_imports_postgresql`).
* `identity.ensure_seed_principal(store, …)` was extracted from `seed_principal` so both backends create the seed user
  with the same code.

## 3. SQLite/PostgreSQL product parity results

`baec_console/tests_postgres/test_service_parity.py` runs the same scenario through the same Console/ConsoleService
methods on a fresh SQLite console and a fresh PostgreSQL console, with the same organization and user ids, the same
step clock, and the same generated ids (the console's and Engine 1's id generators are made deterministic in the
test; without that, two runs differ on *either* backend). It compares every return value, every refusal (type and
message), and afterwards every read the console offers: account views, timelines, attributions, CRM contacts, tasks,
and activities, review queue, preparation views, dashboard, activity feed.

| Scenario | Covers | Result |
|---|---|---|
| `crm_and_identity` (33 steps, 15 refusals) | organizations/users, rename and name-collision refusal, account creation and ownership, attribution snapshots, contacts (ordering, blank refusal), tasks (priorities, ordering, completion once, cross-account contact refusal), activities and Engine 1 link once, interaction links | identical |
| `engines_and_preparation` (37 steps, 14 refusals) | Engine 1 drafts, stale fingerprint, discard, dormancy judgment, Conditionally Dormant, Active Opportunity refused without evaluation evidence; Engine 2 monitoring, duplicate monitoring refusal, observation, correction, superseded-queue refusal, unknown source, stale digest, review; Engine 3 block/unblock, request, generation, edit, attestation refusal, decisions, replacement by a newer request | identical |
| `synthetic_demo` | the seeded demo workflow (`demo_seed._complete`) through the seed user, synthetic workspace | identical |
| `cross_tenant` (13 steps, 10 refusals) | another organization's console against this organization's accounts, contacts, tasks, observations, packets, reviews, handoff, links, attributions | identical (every attempt `AccessDenied`, nothing visible) |

Comparison rules. Engine 1 and Engine 2 ids are compared as they are, **including Engine 2 audit event ids**. Product
tables' database-assigned integers (console event ids, block-decision ids, insertion `seq`) are compared by order
within one workspace — SQLite numbers each workspace file from 1, PostgreSQL uses one sequence per table (Phase 3
importer convention; the console only relies on order). Timestamps compare as instants (product tables are
`timestamptz`, Phase 3). No SQLite or PostgreSQL metadata is compared.

**Differences found — all deliberate, each with its own test:**

1. *Failure atomicity.* When a later write of a service action fails, SQLite keeps the earlier writes of that action
   (separate files); PostgreSQL keeps none. The one refusal path where the service itself triggers this today:
   correcting an observation that is already corrected — Engine 2 refuses the second supersession identically on
   both, but SQLite has already committed the new observation (`test_a_refused_correction_differs_only_in_what_the_
   failed_action_leaves_behind`). This is pre-existing SQLite behavior, not introduced here; I did not change it.
2. *Synthetic reset* is refused on PostgreSQL (§10).

Phase 3's store-level parity (`test_parity.py`, 102 compared steps) and its deliberate store differences are unchanged.

## 4. Cross-store transaction limitations

On PostgreSQL, a service action's identity, console, CRM, and Engine 2 writes are one transaction. Proved by
`test_a_failed_postgresql_action_leaves_no_partial_product_or_engine2_record`: for 11 actions (create account, add
contact, add task, complete task, add activity, capture condition, start monitoring, add observation, review
correspondence, block preparation, request preparation), the action's final write is made to fail — once as a
PostgreSQL error that aborts the transaction, once as an application exception — and the row counts of all 49 tenant
tables (20 product + 29 Engine 2, both workspaces) are unchanged; a task's completion is rolled back too; the console
and pool stay usable. `test_a_postgresql_service_action_is_one_transaction_on_one_connection` shows the CRM and
attribution writes on one connection.

**Not atomic** (Engine 1 is a SQLite file on both backends). In each, Engine 1 is written first and the product
records after; if the product write fails, the Engine 1 record remains:

| Action | Engine 1 write | Then (PostgreSQL) | If the PostgreSQL part fails |
|---|---|---|---|
| `create_account` | account | ownership, metadata, attribution, event | account has no owner, invisible to every organization (fail closed) — `test_an_engine1_write_is_outside_the_postgresql_transaction` |
| `record_interaction` | interaction | interaction metadata, attribution | interaction exists without console metadata; capture is refused for it |
| `confirm_condition` | confirmed BAEC (via approval) | draft resolution, attributions | BAEC confirmed in Engine 1, draft still pending in the console |
| `record_not_confirmable` | classification record | draft resolution, attribution | record exists, draft still pending |
| `record_dormancy_judgment` | judgment | attribution | judgment unattributed (never misattributed) |
| `change_account_state` | transition | attribution | transition unattributed |

Engine 1 *reads* inside a PostgreSQL action (ownership resolution, handoff, Engine 3 snapshots) are not part of the
PostgreSQL snapshot. These limitations are the same as on SQLite today; they end only with an Engine 1 PostgreSQL
store sharing the action's transaction.

## 5. Engine 2 repository design

No Engine 2 file changed. The released modules (`baec_app/engine2/persistence.py`, `review.py`) and
`baec_console/engine2_adapter.py` take a connection and use only `execute(sql, args)`, the cursor's `fetchone` /
`fetchall` / iteration, and `in_transaction`. `baec_console/postgres/engine2.py::Engine2Connection` provides exactly
that over a psycopg connection inside a tenant transaction that acts for one organization **and one workspace**, with
a closed translation table; anything else SQLite-specific is refused, not emulated:

| SQLite construct used by Engine 2 | On PostgreSQL |
|---|---|
| `BEGIN IMMEDIATE` / `COMMIT` / `ROLLBACK` (one write + its audit event) | `SAVEPOINT` / `RELEASE` / `ROLLBACK TO` within the tenant transaction |
| `ORDER BY rowid` (insertion order) | `seq`, an identity column on every table |
| `?` placeholders; `bool` parameters | `%s`; `int`, as sqlite3 stores them |
| `PRAGMA user_version` | 1 iff migration 0003 is recorded |
| `PRAGMA foreign_key_check` | no rows: PostgreSQL enforces foreign keys at write time and the application role cannot disable them |
| any other `PRAGMA` | `Engine2SchemaError` |
| unqualified table names | transaction-local `search_path = pg_catalog, baec_engine2, pg_temp` |

Tenancy. Every Engine 2 tenant row has `organization_id` and `workspace`, filled from the transaction's settings
(`baec.current_organization()`, new `baec.current_workspace()`), part of every key and composite foreign key, and
referencing `baec.organizations`. Three policies per table: Phase 3's `tenant_isolation` and `tenant_boundary`, plus a
restrictive `workspace_scope`, so Engine 2's own SQL — which has no organization or workspace filter — sees exactly one
SQLite file's worth of rows. Workspace scope is an implementation choice (dataset scope, enforced in the database);
the security boundary remains the organization policy. Grants: `SELECT, INSERT` only; `engine2_meta` read-only.

The gate (`postgres/gate.py`) now also requires, for the 29 Engine 2 tenant tables, forced RLS with exactly the three
policies, no `DELETE`/`TRUNCATE`/`REFERENCES`/`TRIGGER`, no `CREATE` on `baec_engine2`, and no write on `engine2_meta`
(8 new sabotage cases).

Not ported: the separate Engine 2 AI audit store (`ai_persistence.py`, `BAEC_ENGINE_2_AI`), which the console does not
use; and an Engine 2 SQLite→PostgreSQL importer (none exists; the Phase 3 importer still copies product data only).

## 6. Engine 2 trigger / invariant mapping

Engine 2 creates 92 triggers in SQLite (`_schema_statements`: three per table on 30 tables, plus two). Each has a
PostgreSQL equivalent in migration 0003 with the **same name and the same message**, reproducing the invariant rather
than the syntax:

| SQLite triggers | Count | Classification | PostgreSQL mechanism | Message |
|---|---|---|---|---|
| `<t>_no_update` on 28 domain tables | 28 | append-only protection | `BEFORE UPDATE` → `baec_engine2.refuse_change()`, ERRCODE `restrict_violation`; plus no `UPDATE` privilege for the app | `<t> is append-only` |
| `<t>_no_delete` on 28 domain tables | 28 | append-only protection | `BEFORE DELETE` → `refuse_change()`; plus no `DELETE` privilege | `<t> is append-only` |
| `<t>_no_replace` on 28 domain tables | 28 | integrity constraint (an existing key is never overwritten; SQLite's `INSERT OR REPLACE` would delete-then-insert) | `BEFORE INSERT` → `refuse_replace(key…)` scoped to organization + workspace; the primary key also refuses; `ON CONFLICT DO UPDATE` is refused by `_no_update` | `<t> rows cannot be replaced` |
| `audit_events_no_update` / `_no_delete` / `_no_replace` | 3 | audit behavior (history is immutable) | as above; event ids assigned by `audit_events_assign_event_id` (max + 1 per organization and workspace under an advisory lock), exactly SQLite's `INTEGER PRIMARY KEY` numbering per file | as above |
| `observations_needs_activation`, `source_refusals_needs_activation` | 2 | state-machine protection (evidence and refusals exist only under an ACTIVATED plan version) | `BEFORE INSERT` → `require_activation()`, ERRCODE `check_violation`; fires before `_no_replace` (PostgreSQL's alphabetical order matches SQLite's newest-first order, checked empirically) | `<t> requires an ACTIVATED Monitoring Plan` |
| `engine2_meta_no_update` / `_no_delete` / `_no_replace` | 3 | SQLite-specific implementation detail (store metadata, not tenant data) | same triggers on a non-tenant `engine2_meta` holding the same two rows; app can only read it | as above |
| *(added)* `<t>_no_truncate` | 30 | append-only (SQLite has no TRUNCATE) | `BEFORE TRUNCATE` → `refuse_change()` | `<t> is append-only` |

Other SQLite invariants and their equivalents: `STRICT` typing → native `text`/`bigint`; every `CHECK` (closed
vocabularies, flags `IN (0, 1)`, `length(content_sha256) = 64`, threshold all-or-none, activation result vs refusal
reason, reading consistency, no self-supersession) copied with the same value sets; `json_valid` → `IS JSON`;
`PRAGMA foreign_keys = ON` per connection → always-on foreign keys, all composite with organization + workspace.
Timestamps stay `text` (byte-for-byte the ISO strings Engine 2 writes, offset included), unlike the product tables.

## 7. Engine 2 conformance results

`baec_console/tests_postgres/test_engine2_parity.py` — **115 passed**:

| Group | Tests | What is compared |
|---|---|---|
| full lifecycle | 1 | 39 steps (monitoring setup, sources, three observations, correction, retraction, measurement, signals with and without dimensions, two candidates, three reviews with all three outcomes, every loader), 12 of them refusals (duplicates, naive time, blank actor, missing references, refused activation, capture under refusal, cross-version mixing) plus six missing-record lookups; every refusal is Engine 2's own type, none a database error |
| locked corpus | 64 | each Stage B case persisted by the existing Stage D harness on both backends: reviews, outcomes, all rows of 30 tables, audit history, verification counts |
| corpus coverage | 1 | the corpus reaches 29 of 30 tables; `signal_candidate_dimensions` is reached by the lifecycle |
| write atomicity | 4 | an injected failure after the rows of an observation, review, supersession, or plan: nothing remains, no write left open, on both |
| trigger parity | 30 + 2 | each table's `_no_update`, `_no_delete`, `_no_replace` and both `_needs_activation` triggers, by direct SQL as a fully privileged role under forced RLS: identical refusal messages |
| privileges | 2 | the application role is refused `UPDATE`/`DELETE`/`TRUNCATE` on every table before any trigger; `TRUNCATE` refused even for the owner |
| tamper detection | 5 | rows altered with triggers bypassed (SQLite: dropped; PostgreSQL: superuser with `session_replication_role = replica`): identical `Engine2IntegrityError`s on load; a closed-vocabulary `CHECK` holds even then |
| ids and isolation | 6 | per-workspace event ids 1..n under interleaving; 3 concurrent writers get 15 distinct consecutive ids; another organization or workspace sees nothing and may reuse ids; no workspace, forged organization, or forged workspace refused by RLS; unsupported statements refused; outcome recomputed from findings |

Plus Engine 2 through ConsoleService in §3. **No behavioral difference between the backends was found at the Engine 2
level.** IDs and lineage are identical (all Engine 2 ids are caller-supplied; the only database-assigned id, the audit
event id, matches).

## 8. Engine 1 25-table / 92-trigger migration map

`docs/V04_ENGINE1_POSTGRES_MIGRATION_PLAN.md` (design only; nothing ported, no Engine 1 file changed).

* Counted on a fresh database: **25 tables, 92 triggers**, 4 explicit indexes, schema version 7, all STRICT, no
  AUTOINCREMENT; only `database.py` creates Engine 1 schema.
* All 92 triggers grouped by invariant, each exactly once: G1 append-only (45), G2 no replacement (23), G3 protected
  `baec_records` columns (1), G4 verbatim evidence (2), G5 AI run provenance and closed vocabularies (4), G6 proposal
  lineage and review state machine (6), G7 grant issuance and retirement (6, including the two AFTER INSERT writers),
  G8 confirmation and grant consumption (5). Five more invariant groups have no trigger (account states and
  transitions, the dormancy/signal rule, IDs and lineage, schema version) and are mapped to constraints or repository
  rules.
* Per group: SQLite mechanism → proposed PostgreSQL mechanism (constraint, trigger function, stored function, or
  repository invariant), the exact messages and exception classes to preserve, IDs and lineage (six database-assigned
  ids, numbered per workspace as in SQLite), and a map of existing tests to invariants.
* Key design decisions: one guard function per table and event with checks in SQLite's observed firing order (9 of 12
  multi-trigger tables would otherwise report a different message under PostgreSQL's alphabetical order); class-23
  error codes; a per-(organization, workspace) advisory lock in place of `BEGIN IMMEDIATE`, because the bridge
  triggers can be raced under READ COMMITTED.
* **Mutation coverage (measured, not argued):** a scratch pytest plugin drops or rewrites triggers right after schema
  creation. Single-trigger mutants: **92 of 92 killed** by behavioral tests (schema-inventory pins not counted).
  Clause-level mutants: 56 run, 33 killed by existing tests, 23 survived; **21 new test cases**
  (`tests/test_engine1_trigger_mutation_coverage.py`) kill 21 of them, each targeted mutant failing exactly one test;
  the remaining 2 are equivalent mutants (another clause refuses the same rows) and are documented. I re-ran two of
  the reproduction commands independently and got the reported results.
* The Engine 2 adapter approach does **not** carry over unchanged: exact `sqlite3.Connection` type checks (4),
  `except sqlite3.IntegrityError` used for control flow (5), `lastrowid` (6 tables), `IS ?`, `PRAGMA query_only` as
  the read-only proof, `backup`/`serialize`, and SQLite's statement-level rollback inside a transaction.

## 9. Engine 1 readiness assessment

**Not ready to port yet; ready for a separately approved, controlled port phase.** The invariant map is complete
(every trigger mapped and every non-equivalent mutant killed), so the Engine 1 stop condition "cannot be mapped
comprehensively" did not trigger. What is missing are code changes at the connection boundary, which CLAUDE.md
requires to be explained and approved before they are made, and which I deliberately did not make:

1. a connection protocol replacing the four exact `sqlite3.Connection` checks;
2. a backend-neutral refusal error replacing the five `sqlite3.IntegrityError` catches (messages unchanged);
3. `RETURNING` in place of `lastrowid`, an explicit NULL-safe predicate for `IS ?`, a fail-closed read-only primitive
   for `PRAGMA query_only`;
4. owner decisions listed in plan §12.2 (Engine 1 → ownership FK, staleness UPDATE grant, synthetic reset, text
   timestamps);
5. a dual-backend Engine 1 conformance harness (plan §12.4), built like §7 here.

Documented irreducible divergences (plan §12.5): millisecond vs microsecond timestamp precision in the TTL and grant
window checks, U+0000 refused in PostgreSQL text, a digest index for two free-text unique keys above ~2.7 kB, and
`DataError` instead of `IntegrityError` for STRICT type errors.

## 10. Synthetic/demo persistence design (Part 5)

Implemented: only what PostgreSQL conformance needed. On PostgreSQL, `reset_synthetic` is refused before anything is
read (`RESET_UNAVAILABLE`); the seeded demo still runs through the seed user on the synthetic workspace (the
conformance scenario `synthetic_demo`). SQLite development reset is unchanged (deletes the organization's synthetic
directory; `test_the_sqlite_backend_keeps_the_development_files_and_reset_semantics`). Production account deletion is
untouched: there is none, and none is designed here.

Design (proposed, not built):

* A demo dataset is identified by `(organization_id, workspace = 'synthetic', dataset_id)`. Every synthetic-workspace
  row (product and Engine 2) carries `dataset_id`; keys and composite foreign keys include it, as they include the
  workspace today.
* `baec.demo_datasets (organization_id, dataset_id, created_at, created_by_user_id, replaced_at)` records each dataset,
  append-only except a single `replaced_at` stamp; `baec.current_demo_dataset` is a per-organization pointer row.
* "Reset" = create a new dataset id, seed into it, and move the pointer, in one transaction. Nothing is deleted:
  earlier datasets stay as history, invisible to the console because the transaction's dataset setting (like
  `baec.workspace`) is part of the `workspace_scope` policy.
* Synthetic ids keep their `SYN-` prefix; ids may repeat across datasets because the dataset is part of every key.
* The local workspace has no dataset concept; production account records are never in a demo dataset, so a demo reset
  cannot touch them by construction.
* Retention (eventually purging old demo datasets) would be an operator action outside the application role, never a
  console feature.

## 11. Production startup status

`storage.require_production_storage` (after the unchanged `ProductionAccessRefused` for no authenticated user):

1. database checks (`gate.check_database`), in order: configuration and connectivity; migrations (version, name,
   and checksum of each); role (no superuser, no RLS bypass, no ownership, no `CREATE`, no `DELETE`/`TRUNCATE`/
   `REFERENCES`/`TRIGGER`, and — new — cannot set `session_replication_role`); RLS on all 49 tenant tables; then the
   **Engine 2 schema integrity check** (`gate.engine2_failures`, added at the Phase 3B final gate):
   * exactly the 30 Engine 2 tables, no more;
   * exactly the 123 expected triggers, each **enabled for ordinary sessions** (`tgenabled = 'O'`; disabled or
     replica-only is refused) and with exactly its validated definition (`pg_get_triggerdef`: timing, event, level,
     function, and — for `_no_replace` — the key columns);
   * every function in `baec` and `baec_engine2` with exactly the body, language, and return type its migration
     created, none `SECURITY DEFINER`, no extra function (this includes `baec.current_organization()` and
     `baec.current_workspace()`, which every RLS predicate depends on);
   * the `engine2_meta` rows.

   How the expected set is derived: the 92 trigger names, events, and tables come from Engine 2's own schema
   definition (`persistence._schema_statements()`), the replace-guard key columns from `persistence._KEYS`, and the
   tables from `persistence.TABLES` — the same source that creates the SQLite triggers, so the two cannot drift
   apart silently. Only the PostgreSQL-only additions are listed in the gate (`<table>_no_truncate` on all 30 tables,
   `audit_events_assign_event_id`). Expected function bodies are read from the migration files, which the checksum
   check already pins. A mismatch refuses with one safe message (`ENGINE2_SCHEMA_MISMATCH`: "Production storage
   schema does not match the validated BAEC Engine 2 PostgreSQL schema. BAEC Console is closed. Nothing was read or
   changed."); the names of the offending schema objects are on the exception's `failures` for operators and tests,
   never a URL or credential. Nothing is repaired;
2. `ENGINE_BACKENDS` — `Engine 1: None`, `Engine 2: baec_engine2 (migration 0003)`, `Engine 3: no persistence`. Any
   engine without a PostgreSQL store → refused with `engine_storage_blocker(missing)`. Today:

   > Production storage is not available yet: Engine 1 records have no PostgreSQL store (it would be in a SQLite file
   > shared by every organization, and production never uses a shared SQLite store). BAEC Console is closed. Nothing
   > was read or changed.

3. if no engine is missing → still refused (`PRODUCTION_CONSOLE_NOT_BUILT`): the authenticated production console does
   not exist, and is not built ahead of its phase.

Tests: the message names Engine 1 and not Engine 2; production stays closed for every combination of unsafe engines
(4 cases); with every engine marked safe it is still closed; an unsafe Engine 2 schema is refused by the gate first;
nothing under the data root is created. Development is unchanged.

## 12. Files changed

New: `baec_console/backends.py`, `baec_console/postgres/engine2.py`,
`baec_console/postgres/migrations/0003_engine2_schema.sql`, `baec_console/tests_postgres/test_engine2_parity.py`,
`baec_console/tests_postgres/test_service_parity.py`, `docs/V04_ENGINE1_POSTGRES_MIGRATION_PLAN.md`, this report, and
`tests/test_engine1_trigger_mutation_coverage.py` (21 cases; Engine 1 schema behavior only, no source change).

Modified: `baec_console/console_service.py` (backend routing, unit per action), `identity.py`
(`ensure_seed_principal`), `storage.py` (engine backend table, blocker naming the missing engines),
`deployment.py`, `postgres/__init__.py`, `postgres/importer.py` (docstrings), `postgres/database.py` (workspace
setting), `postgres/stores.py` (workspace in sessions, lazy Engine 2 connection), `postgres/gate.py` (Engine 2
checks), `README_CONSOLE.md`, `tests/test_storage.py` (+3), `tests/test_identity_freshness.py` (docstring),
`tests_postgres/conftest.py` (migrations 1–3), `tests_postgres/test_migrations_and_gate.py` (+8 sabotage, +7 gate,
+18 Engine 2 schema integrity), `tests/test_live_report.py` (per-test temporary root; §14–18).

Not modified: anything under `baec_app/`, the public demo, the frozen public research release, requirements files.

## 13. Tests added

| File | Added | |
|---|---|---|
| `tests_postgres/test_engine2_parity.py` | 115 | §7 |
| `tests_postgres/test_service_parity.py` | 36 | §3, §4 |
| `tests_postgres/test_migrations_and_gate.py` | 15 | 8 Engine 2 gate sabotage cases, 7 production-gate cases |
| `tests/test_storage.py` | 3 | blocker wording; development path never imports psycopg; SQLite reset and file layout unchanged |
| `tests/test_engine1_trigger_mutation_coverage.py` | 21 | clause-level Engine 1 trigger invariants a PostgreSQL port must keep (§8) |
| `tests_postgres/test_migrations_and_gate.py` (final gate) | 18 | Engine 2 schema integrity: derivation check, 11 destructive schema sabotages, missing table, 3 migration-metadata tamperings, `session_replication_role` privilege, startup order |

## 14–18. Results

Local PostgreSQL 16 container (`scripts/postgres_test_db.sh`), main venv for SQLite/root suites, `.venv-postgres` for
the PostgreSQL suite.

| Suite | Result |
|---|---|
| 14. `baec_console/tests` (SQLite, no network) | **462 passed** (459 baseline + 3) |
| 15. `baec_console/tests_postgres` (RLS 22, pool 7, migrations/gate 56, parity 3, importer 16, Engine 2 parity 115, service parity 36) | **255 passed** |
| 16. Engine 2 parity alone (`test_engine2_parity.py`) | **115 passed**, unchanged by the gate work |
| 17. repository `tests/`, final gate | **3 consecutive runs, 7597 passed each, 0 failures, 0 errors** (2 deselected by `pytest.ini`: the `live_claude` marker), 100–103 s each, 08:55–09:00 on 28 Oct, machine held awake with `caffeinate`, no sleep recorded in the power log during any run, nothing else running. JUnit XML kept for each run. |
| 18. `git diff --check` (plus a trailing-whitespace check of the new files) | clean |

Earlier unclean repository runs, and their causes:

* **`tests/test_live_report.py` isolation leak (found, reproduced, fixed).** Five tests asserted that the set of
  `baec-live-eval-*` directories in the machine-wide temporary directory was unchanged across a run. Any other
  process using the live harness at the same time broke them although nothing leaked: they passed 66/66 three times
  alone and failed 5 every time a second process created such directories. Fix: a per-test `tempfile.tempdir`
  (autouse fixture), which the harness's `mkdtemp` also uses, so the check is exactly as strict but sees only its own
  test. Verified both ways: 66/66 under the same contention (3 runs), and a mutant harness that keeps its temporary
  database is still caught. No assertion, timeout, skip, or xfail was changed.
* **`tests/engine2/test_engine2_public_demo.py::test_visitor_sessions_are_isolated` (environmental).** A Streamlit
  AppTest 60 s timeout. Alone it takes 0.22 s (5 of 5 passes). The macOS power log shows the machine entered sleep 3
  times during that run; AppTest's timeout uses wall-clock time, which keeps running during sleep, while pytest's
  own timer does not (the run reported 150 s elapsed but took over 30 minutes).
* **The run with 2 uncaptured failures: cause probable, not proven.** Its output was not saved, so the test names
  are unknown. The power log shows the machine slept once during it (on battery). It is consistent with the AppTest
  timeout class above; it cannot be shown to be the live-report leak, because no other harness process is known to
  have been running then. It did not recur in any of the three controlled runs.

The existing direct RLS attack tests (22) and pool isolation tests (7) are unchanged and green.

## 19. Exact blockers remaining before production / authentication

1. **Engine 1 has no PostgreSQL store** (the production blocker named at startup). Its records would sit in a SQLite
   file shared by every organization, isolated only by application checks, and every Engine 1 write is outside the
   PostgreSQL action transaction (§4). Next: the controlled port in the migration plan, with dual-backend conformance.
2. **No production console path.** Even with every engine on PostgreSQL, production refuses
   (`PRODUCTION_CONSOLE_NOT_BUILT`): there is no authenticated principal, and no production composition of
   `Console(..., backend=PostgresBackend)` without Engine 1 files.
3. **Identity resolution for a sign-in** (Phase 3 §12.3): under RLS a user can be read only once the organization is
   known; a verified organization claim or a narrowly scoped subject → (user, organization) lookup is still needed.
4. **Synthetic data on PostgreSQL**: reset is refused; the dataset model in §10 is designed, not built.
5. **No Engine 2 data importer** from existing SQLite files (only product data has one).
6. **Operations** (Phase 3 §12.4): TLS not required by the gate, secret storage, backups, connection limits. The gate
   now verifies Engine 2's triggers and every BAEC function, but **not the product tables' triggers** (Phase 3 schema)
   or column-level `CHECK`/foreign-key constraints; a migration-role operator who drops one of those is not detected
   at startup.
7. **Residual risk** (Phase 3 §12.5): the application chooses its tenant context, so RLS protects against application
   bugs, not a compromised process.
