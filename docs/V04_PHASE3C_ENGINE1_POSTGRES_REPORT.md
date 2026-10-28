# V0.4 Phase 3C — Engine 1 on PostgreSQL: Persistence, Parity, and Live-Schema Gate Report

Branch `baec-crm-v0.4`, on top of `9ad24e7` (Phase 3B accepted gate). All data is synthetic. Every test cited here is a
software test of the storage layer or a synthetic conformance case. None is evidence for the BAEC theory, a measure of
sales or purchase outcomes, or a production security assessment. Nothing was deployed, merged, or committed. No
authentication was added. The frozen public research release was not touched.

**Bottom line.** Engine 1 has a PostgreSQL store (schema `baec_engine1`, migration 0004). The unchanged Engine 1
repository and command facade run on it through a narrow adapter, and on PostgreSQL every ConsoleService action's
Engine 1 writes are part of the action's single tenant transaction. The same 277 direct-SQL attacks have identical
outcomes on SQLite and PostgreSQL; every one of the 92 SQLite triggers, and 93 individual trigger clauses, is shown to
be load-bearing on **both** backends; the existing Engine 1 repository suites (850 tests) pass unchanged on PostgreSQL.
The production gate now validates the live product, Engine 1, and Engine 2 schema against a catalog manifest generated
from the migrations. **Production remains closed, now only for the missing authenticated production console**
(`PRODUCTION_CONSOLE_NOT_BUILT`); the message "Engine 1 records have no PostgreSQL store" is gone.

| Status | What |
|---|---|
| Implemented and tested | migration 0004 (25 tables, 92 trigger equivalents, tenancy, RLS, grants); `postgres/engine1.py` adapter; Engine 1 in the PostgreSQL unit (atomic actions); dual-backend Engine 1 conformance, attack, and mutation suites; existing Engine 1 suites on PostgreSQL; live-schema gate (product, Engine 1, Engine 2) with a generated manifest; Engine 1 and Engine 2 import |
| Deliberately not built | synthetic reset on PostgreSQL (not required for parity; still refused, §16); Engine 1 AI-provenance/bridge *stores* on PostgreSQL (schema and all triggers are ported and tested; no ConsoleService path uses the stores) |
| Unchanged | Engine 1 decision logic, the state machine, classification, human-confirmation gate; Engine 2 and Engine 3 code; SQLite development behavior; the public demo |
| Not added | authentication, sessions, deployment, outreach, scheduling, monitoring, new BAEC methodology |

---

## 1. Accepted Phase 3B baseline

Re-run before any change: `baec_console/tests` **462 passed**; `baec_console/tests_postgres` **255 passed** (local
PostgreSQL 16.15 container, `scripts/postgres_test_db.sh test`). Engine 1 inventory re-counted from a fresh
`open_database(":memory:")`: **25 tables, 92 triggers, 4 explicit indexes, `user_version` 7** — the plan's numbers.
The 21 mutation-coverage tests (`tests/test_engine1_trigger_mutation_coverage.py`) pass. The SQLite firing order of
same-table BEFORE INSERT triggers was re-derived from `database._schema_statements()` (reverse creation order, also
re-measured on SQLite 3.50.4) and equals the plan's §6.2 table for all 12 multi-trigger tables.

What could not be re-run as such: the plan's §11 clause-level mutation tooling (`e1_drop_triggers.py`,
`run_mutants.py`) lived in the previous session's scratchpad and is not in the repository. Its role is now taken by
committed tests that mutate **both** backends (§9).

The overlapping-invariant clauses. The plan names two equivalent mutants (another clause refuses the same rows):
`grant_accepted_decision` (`decision = 'ACCEPTED'` in `human_authorization_grants_accepted`; a non-accepted decision of
that proposal is a rejection, refused by the same trigger's "rejected" clause) and `failure_codes_text_type`
(`code.type <> 'text'`; a non-string element is never in the vocabulary). Both clauses are carried into PostgreSQL. This
phase found two more that are not isolable **on PostgreSQL**: the `target_state IS NULL` clause of
`ai_proposal_confirmations_authorization_binding` (a CONFIRM_BAEC authorization with a target state violates the
`human_authorizations` CHECK, which PostgreSQL cannot switch off), and `COLLATE "C"` in the failure-code ordering (no
pair in either validator vocabulary orders differently under the server's `en_US` collation; checked exhaustively).

## 2. Engine 1 abstraction chosen, and why

Evaluated: (A) a sqlite3-compatible adapter as for Engine 2; (B) a second, PostgreSQL repository; (C) a hybrid.
**Chosen: C** — an adapter for the SQL, plus three narrow source changes that remove the SQLite coupling the plan
found, without changing SQLite behavior:

| Coupling (plan §9) | Resolution |
|---|---|
| `except sqlite3.IntegrityError` in `Repository._write` | also catches `database.StorageRefusal` (new; *not* a `PersistenceError`, exactly like `sqlite3.IntegrityError`). The adapter raises it with PostgreSQL's primary message, so `RepositoryConflictError` text is byte-identical for trigger refusals |
| `cursor.lastrowid` (5 sites, 6 tables) | `INSERT … RETURNING <id>` (valid on SQLite ≥ 3.35; the minimum is 3.38), via one helper `_insert_returning` |
| `… AND state IS ?` | adapter translation `IS ?` → `IS NOT DISTINCT FROM %s` (SQLite 3.38 lacks `IS NOT DISTINCT FROM`, so the source keeps `IS`) |
| `BEGIN IMMEDIATE` / `COMMIT` / `ROLLBACK` | advisory lock per (organization, workspace) + `SAVEPOINT` / `RELEASE` / `ROLLBACK TO` |
| SQLite statement-level rollback | every INSERT/UPDATE runs in its own savepoint, so a refused statement leaves the block usable |
| `PRAGMA query_only` (Engine 2 handoff's read-only proof) | `Engine1Connection.read_only()`: a savepoint with `SET LOCAL transaction_read_only = on`, always rolled back; PostgreSQL itself refuses writes (25006); `PRAGMA query_only` answers from `transaction_read_only` |
| `PRAGMA user_version`, `PRAGMA foreign_keys` | 7 iff migration 0004 is recorded; 1 (FKs cannot be disabled by the app role) |
| exact `type(...) is sqlite3.Connection` (AI/bridge stores, proposal facade) | **not changed**: those stores stay SQLite-only (no ConsoleService path uses them) |

Why not B: the repository *is* Engine 1's storage rule set (verify before write, re-run the locked state machine on
stored data, fail-closed rehydration); a second implementation would duplicate it. Why not pure A: impersonating
`sqlite3` exception and connection types would hide behavior instead of preserving it. The adapter accepts only
SELECT, INSERT, UPDATE, the three transaction statements, and four PRAGMAs; DELETE, DDL, ATTACH, VACUUM, REPLACE,
unknown PRAGMAs, and empty statements raise `Engine1StatementRefused` before anything reaches PostgreSQL. Text
containing U+0000 is refused with a `StorageRefusal` (PostgreSQL text cannot hold it).

Source changes in `baec_app/`: `data/database.py` (+`StorageRefusal`, 9 lines) and `data/repository.py` (catch, and
RETURNING). All existing Engine 1 SQLite tests pass unchanged (§19).

## 3. 25-table migration result

Migration `0004_engine1_schema.sql` creates all 25 tables with SQLite's columns, primary keys, UNIQUE keys, and
foreign keys (all `ON DELETE RESTRICT`), each prefixed with `(organization_id, workspace)`, plus `seq` (identity) for
insertion order. `test_engine1_storage.py::test_the_postgresql_tables_have_sqlites_columns_keys_and_foreign_keys` reads
both catalogs and requires equality for every table (types `TEXT`→`text`, `INTEGER`→`bigint`; NOT NULL; PK; every
UNIQUE key and the partial unique index; every FK with its referenced columns and delete action). The two free-text
UNIQUE keys (`interaction_evidence (interaction_id, provenance, text)`, `ai_artifact_excerpts (artifact_id,
interaction_id, text)`) are unique indexes on `baec_engine1.text_key(text)` (SHA-256 of the UTF-8 bytes) because btree
entries are limited to about 2.7 kB; the `no_replace` guards still compare the full text.

## 4. 92-trigger / invariant mapping result

Every SQLite trigger has exactly one PostgreSQL trigger with the same message (derived and checked, §14). BEFORE INSERT
triggers are named `f<k>_<sqlite name>`, `k` = SQLite's firing position, so PostgreSQL's alphabetical firing order is
SQLite's newest-first order and a row violating two guards gets the same message on both (pinned by cases such as "G4
evidence: verbatim fires before no_replace"). PostgreSQL adds 25 `<table>_no_truncate` and 6 `f9_<table>_assign_id`
triggers: **123 triggers in all**.

| Group | SQLite triggers | PostgreSQL mechanism | Conformance cases | Per-trigger mutants killed (SQLite / PostgreSQL) |
|---|---|---|---|---|
| G1 append-only | 45 | `refuse_change()` BEFORE UPDATE/DELETE, `baec_records_no_delete()`; no UPDATE/DELETE grant; TRUNCATE trigger | 45 | 45 / 45 |
| G2 no replacement | 23 | `refuse_replace(keys…)` (rowid keys marked `#`: NULL reads as −1, as SQLite's `NEW.rowid`), decisions' own function | 89 | 23 / 23 |
| G3 protected columns | 1 | `BEFORE UPDATE OF <15 columns, organization_id, workspace>` | 16 | 1 / 1 |
| G4 verbatim evidence | 2 | `strpos`, `btrim(E' \t\n\x0b\f\r')`, tenant-scoped interaction lookup | 20 | 2 / 2 |
| G5 AI provenance | 4 | trigger functions; failure codes via `IS JSON ARRAY` and IF statements | 42 | 4 / 4 |
| G6 proposal lineage | 6 | trigger functions | 14 | 6 / 6 |
| G7 grants | 6 | trigger functions; the two writers as AFTER INSERT functions | 21 | 6 / 6 |
| G8 confirmation and consumption | 5 | trigger functions; `baec_engine1.julianday()` window | 30 | 5 / 5 |
| **Total** | **92** | | **277** | **92 / 92** |

The trigger-by-trigger checklist (source trigger → PostgreSQL trigger and function → conformance cases → clause
mutants) is generated from the test catalogue and printed in full in
`docs/V04_ENGINE1_POSTGRES_MIGRATION_PLAN.md` §14. The five invariant groups without a trigger (N1 account states, N2
transitions, N3 dormancy gate and signal rule, N4 ids and lineage, N5 schema version) are enforced by the same CHECKs
and composite FKs (structure test, 25 CHECK cases) and by the unchanged repository (lifecycle parity, existing suites).

## 5. PostgreSQL schema design

* **Timestamps stay text** in Engine 1 (and Engine 2): byte for byte what the repository writes. Product tables keep
  `timestamptz`.
* **`baec_engine1.julianday(text)`** reimplements SQLite 3.50's `julianday` (parse, ranges, digit-by-digit fraction,
  `.999` clamp, millisecond rounding, same double arithmetic) for ISO 8601 text and returns NULL otherwise. It is used by
  the grant TTL CHECK and the consumption window, so those rules match SQLite exactly — including SQLite's
  millisecond resolution, which the plan (§12.5) had expected to be an irreducible divergence. Verified equal to SQLite
  on 2,019 inputs per run (and 3,028 during development).
* **A planning trap found by the suite.** In PL/pgSQL, `NEW.<column>` is a plan-time parameter, so a cast to `json`
  inside a `CASE` branch that is never taken can be constant-folded and raise `22P02`. SQLite's `CASE` is lazy. The
  failure-code guard therefore uses `IF … IS JSON ARRAY` *statements* before any cast. A JSON `\u0000` escape (in no
  vocabulary, so SQLite refuses it) is refused explicitly, because PostgreSQL could not decode it to text.
* **Write serialization.** `lock_workspace(organization, workspace)` (`pg_advisory_xact_lock`) is taken by the adapter's
  `BEGIN IMMEDIATE` and by every guard that reads other rows, so the bridge's "latest revision / already rejected /
  one unretired grant" rules cannot be raced under READ COMMITTED, even by raw SQL.
* **No `SECURITY DEFINER`** anywhere; trigger functions run as the invoking role, so RLS applies inside them; every
  cross-row read is additionally filtered by `NEW.organization_id` and `NEW.workspace`.
* **Stricter than SQLite (deliberate, IMPLEMENTATION):** the application role cannot UPDATE `baec_records` at all
  (`staleness_status` has no writer before a staleness phase) and can UPDATE only `accounts.state`; nobody can TRUNCATE.

## 6. RLS / tenancy design

As Engine 2 (0003): every row has `organization_id` (FK to `baec.organizations`) and `workspace`, filled from the tenant
transaction's settings; three policies per table — `tenant_isolation` (permissive), `tenant_boundary` (restrictive),
`workspace_scope` (restrictive) — so the repository's unfiltered SQL sees exactly one SQLite file's worth of rows;
`ENABLE` and `FORCE` RLS; grants `SELECT, INSERT` plus `UPDATE (state)` on accounts. The six database-assigned ids are
numbered per (organization, workspace), dense from 1, exactly as one SQLite file numbers them, so no tenant can observe
another's write volume. ConsoleService ownership checks remain as defense in depth.

Tested (`test_engine1_storage.py`): another organization sees no row of any of the 25 tables, as the app role and as
a full-DML probe role (UPDATE/DELETE reach zero rows); another workspace sees nothing; no tenant context sees and
writes nothing; children cannot reference another tenant's account (repository: not found; raw SQL: FK refusal);
the verbatim guard treats another tenant's interaction as missing; forged `organization_id` or `workspace` is refused
by `WITH CHECK`; ids are per tenant; tenant context does not outlive its transaction across every pooled connection.
The Engine 1 and Engine 2 table names are disjoint (tested), so one transaction-local `search_path`
(`pg_catalog, baec_engine1, baec_engine2, pg_temp`) serves both adapters.

## 7. Transaction integration

On PostgreSQL, `PostgresWorkspace.engine1()` returns the unit's `Engine1Connection`, so the six actions the Phase 3B
report listed as non-atomic — create account, record interaction, confirm condition, record not-confirmable, dormancy
judgment, state change — are now one transaction with their product and attribution writes.
`test_service_parity.py::test_a_failed_postgresql_action_leaves_no_partial_product_or_engine2_record` injects a failure
into the final product write of **17 actions** (the 11 of Phase 3B plus these six), once as a database error and once
as an application exception (34 cases), and requires the row counts of all 74 tenant tables (20 product, 25 Engine 1,
29 Engine 2; both workspaces) to be unchanged. `test_an_engine1_write_is_inside_the_postgresql_transaction` replaces the
Phase 3B test that documented the opposite.

**Deliberate SQLite/PostgreSQL difference, kept:** on SQLite the stores are separate files, so a failure after the
Engine 1 write still leaves the Engine 1 record (an unowned account stays invisible: fails closed). This is unchanged
V0.3 behavior, pinned by `test_on_sqlite_a_failed_create_account_keeps_the_unowned_engine1_account`. Changing SQLite
would be an unrelated V0.3 semantic change.

Concurrency: two writers racing one account's transition serialize as `BEGIN IMMEDIATE` does (one stored, the other
gets the state machine's own "same state" answer); four concurrent writers get distinct consecutive ids. A residual
risk, not observed: an action that took the Engine 2 lock before the Engine 1 lock, concurrent with one taking them in
the other order, would deadlock; PostgreSQL detects it and aborts one action (fails closed). No current action writes
both engines.

## 8. Engine 1 parity results

| Suite | What is compared | Result |
|---|---|---|
| `test_engine1_parity.py` attacks | 277 direct-SQL statements on identical seeds (four AI lineages: confirmed+consumed, open, superseded, re-issued); exact trigger messages, constraint kinds, follow-up rows | **277 identical** |
| CHECK parity | 25 values a CHECK forbids (every enum column, normalization rule, TTL, JSON object, digests, blank validator) | **25 identical** |
| seed | every row of all 25 tables, ids included, after the same seed | identical |
| `test_engine1_storage.py` lifecycle | 65 Repository steps (accounts, interactions, both not-confirmable classifications, confirmed BAECs, two conditions per account, all 16 dormancy-judgment combinations, every transition among the three states and unclassified with valid and invalid prerequisites, every read); return values, refusal types and messages, and all 25 tables' rows | **identical** (27 refused or rejected steps, 5 allowed transitions) |
| existing suites on PostgreSQL | `tests/test_repository_integrity.py`, `test_repository_roundtrip.py`, `test_transition_persistence.py`, `test_application_account_state.py`, `test_application_classification.py`, `test_application_dormancy.py`, unchanged, through a plugin giving each test a fresh PostgreSQL tenant | **919 collected, 850 passed, 0 failed, 69 skipped** |
| service parity | Phase 3B's four ConsoleService scenarios, now with Engine 1 on PostgreSQL too | identical |

The 69 skips are listed with reasons in `engine1_on_postgres.NOT_APPLICABLE` and are SQLite mechanisms, not behavior:
44 assert `sqlite3.IntegrityError` or SQLite introspection (`typeof`, `PRAGMA table_info/index_list/
foreign_key_list`) — the same invariants run on both backends in the attack, CHECK, and structure tests; 25 simulate
corruption by switching CHECK constraints off, which PostgreSQL cannot do, so that corruption cannot be stored at all
(stricter, and refused identically at write time by the CHECK parity test). Corruptions that do not break a CHECK
(deleted rows, moved evidence, rewritten statements, wrong authorizations, naive timestamps, broken history) run on
PostgreSQL with triggers and FKs off (`session_replication_role = replica` under an RLS-bound test role) and fail
closed identically. Not run: the raw-SQL schema files (`test_rc33_schema_guards.py`, `test_evidence_fidelity.py`),
superseded by the dual-backend attack suite, and two files that open SQLite files by path for the SQLite-only proposal
facade.

**Differences found and resolved:** the planning trap in the failure-code guard (§5), found by 4 attack cases. **No
unexplained difference remains.**

## 9. Engine 1 mutation results

* **Per trigger (92):** `test_dropping_any_trigger_changes_an_outcome_on_both_backends` drops each SQLite trigger, and
  separately its one PostgreSQL equivalent, on freshly seeded databases: **92 of 92 killed on SQLite and 92 of 92 on
  PostgreSQL** (G2 cases switch off only the guards SQLite fires before `no_replace`, so each tests the replace guard
  alone).
* **Per clause (93):** `test_each_clause_is_load_bearing_on_both_backends` rewrites one clause to a tautology (or
  removes one UNIQUE key from a replace guard, or one column from the protected-column list) on both backends: **93 of
  93 killed on both**. They cover every conjunct and disjunct the plan's 21 surviving clause mutants named, plus the
  G4 blank checks, model binding, every failure-code rule, artifact rules, every replace-guard key of every multi-key
  table, and three protected columns.
* The 21 committed SQLite clause tests still pass; the equivalent mutants are in §1.

## 10. Direct SQL attack results

All attacks bypass ConsoleService and the repository. On PostgreSQL they run as `baec_app` (append-only refusals,
replacement, forged confirmation, fabricated evidence, illegal lineage, cross-tenant references, forged tenancy) or as
`baec_e1_probe` (all DML, still under RLS) where a statement needs UPDATE or DELETE, so the trigger — not only the
missing grant — is what refuses. As `baec_app`, UPDATE/DELETE/TRUNCATE of every Engine 1 table is refused by
privilege (`InsufficientPrivilege`, 25 tables × 3); TRUNCATE is refused even for the owner. Forging a confirmation
link without the grant-derived authorization (wrong id, action, subject, actor, or the same instant spelled
differently), linking a consumed, superseded, re-issued, non-latest, or rejected grant, a stale or non-confirmed BAEC,
or consuming outside the validity window are all refused identically (§8).

## 11. Engine 1 importer results

`python -m baec_console.postgres.importer SOURCE_ROOT [--dry-run]` (explicit CLI only) now also imports each
workspace's `engine1.sqlite3` (the 12 domain tables) and `engine2.sqlite3` (all 29 data tables) inside the same single
transaction as the product data (`postgres/engine_import.py`). Every row is written as the application role acting for
its owner and workspace, so every Engine 1 and Engine 2 guard checks it. Ids (including the six rowid ids and Engine 2
event ids) are kept; text — buyer statements, evidence, thresholds, timestamps — is copied as stored; nothing is
reclassified or regenerated; accounts keep their stored state and history. Ownership is resolved for every engine row
(shared `local/` holds several organizations); a row with no single owner refuses the import. A source holding Engine 1
AI-provenance or bridge rows is refused (their AFTER-INSERT state machine cannot be replayed table by table without
switching guards off). Verification re-reads every engine row column by column, every owned account through the
PostgreSQL Engine 1 repository against the SQLite repository, and Engine 2's audit history and reviews through its
loaders.

`test_importer.py`: **23 passed** — the 16 Phase 3 tests now with engine data included (dry run writes nothing;
real import inserts exactly the source counts; idempotent re-run inserts nothing; differing existing record and an id
held by another organization refuse everything; source never modified; operator log without credentials), plus 7
new: engine records imported and verified through both engines (> 100 checks), ids and buyer language byte for byte,
new ids continue after imported ids, a differing engine record refuses and is never overwritten, unowned engine rows
refuse, AI/bridge history refuses, and a failed engine insert rolls back the product rows too.

## 12. Engine 2 importer results

Built in the same tool (above): all 29 Engine 2 data tables, ownership through each row's Monitoring Plan (or review,
signal candidate, or audited entity, including `MonitoringPlan` events' `<plan>@v<version>` ids), the same conflict and
idempotency rules, and verification through Engine 2's own loaders (audit history, reviews). `engine2_meta` is store
metadata and is not copied. Production does not depend on any import.

## 13. Product live-schema gate result

Closed the Phase 3B gap. `postgres/catalog.py` snapshots, for `baec`, `baec_engine1`, `baec_engine2`, and
`baec_migrations`: relations (kind, RLS enabled/forced), columns (type, NOT NULL, default, identity, collation), every
PK/FK/UNIQUE/CHECK definition, indexes, triggers with enabled state, policies, functions (arguments, return type,
language, body, volatility, SECURITY DEFINER, settings, executable), and the connecting role's effective table, column,
and schema privileges. No role, owner, oid, or row is in it. `schema_manifest.json` is that snapshot of a fresh
migration, with the migrations' (version, name, checksum); `test_the_schema_manifest_is_exactly_a_fresh_migration`
rebuilds it on every PostgreSQL run and requires equality, so it is derived from the migrations, not hand-maintained,
and a stale manifest closes production. Regenerate with `python -m baec_console.postgres.catalog write <app URL>`.

Product sabotage (each refused twice with `PRODUCT_SCHEMA_MISMATCH`, object named, nothing repaired): dropped
append-only trigger, disabled update guard, dropped TRUNCATE refusal, weakened and dropped CHECK, dropped FK, dropped
NOT NULL, widened column privilege, column privilege on the operator log, extra table, extra view (11), plus migration
record tampering (existing and new cases) and the Phase 3 RLS/grant cases.

## 14. Engine 1 live-schema gate result

`gate.engine1_failures` (message `ENGINE1_SCHEMA_MISMATCH`): exactly the 25 tables; exactly the 123 expected triggers,
each enabled for ordinary sessions and with its exact definition — the 92 **derived from Engine 1's own SQLite schema
statements** (name, timing, event, `OF` columns, table, replace keys from `REPLACE_GUARDED_KEYS`), so the PostgreSQL set
cannot drift from SQLite's silently; every `baec_engine1` function with its migration's body and not SECURITY DEFINER;
and the catalog manifest for the schema (constraints, indexes, columns, privileges, RLS). The role checks also cover
`baec_engine1` (no ownership, no CREATE, no DELETE/TRUNCATE/REFERENCES/TRIGGER, no `session_replication_role`, no RLS
bypass). Sabotage (16, refused twice, nothing repaired): dropped, disabled, replica-only, reordered (renamed) trigger,
narrowed replace guard, weakened guard function, weakened date arithmetic, SECURITY DEFINER, weakened CHECK, dropped
FK, dropped text-key index, dropped NOT NULL, widened column and table privilege, extra column, extra function; and
four migration-record tamperings of version 4.

## 15. Production storage readiness status

`ENGINE_BACKENDS`: Engine 1 → `baec_engine1` (migration 0004), Engine 2 → `baec_engine2` (0003), Engine 3 → product
tables. No engine is missing. With a safe database the startup order is: mode → configuration → migrations → role →
RLS → Engine 2 schema → Engine 1 schema → product schema → **`PRODUCTION_CONSOLE_NOT_BUILT`** ("Production storage is
complete, but the production console (an authenticated user served from PostgreSQL storage) is not built yet. BAEC
Console is closed."). The storage layer is production-*capable* in the sense the brief defines; production is not open.

## 16. Synthetic reset result

Not built. It is not required for PostgreSQL ConsoleService parity: the seeded demo runs on PostgreSQL through the seed
user (service parity scenario `synthetic_demo`), and reset is refused on PostgreSQL before anything is read
(`RESET_UNAVAILABLE`, unchanged and tested). The Phase 3B logical-dataset design (§10 there) stands: it requires a
dataset id in every key of 74 tables, which is a schema change to be made only when reset on PostgreSQL is needed.
SQLite development reset is unchanged. No account-deletion primitive exists.

## 17. Files changed

New: `baec_console/postgres/migrations/0004_engine1_schema.sql`, `postgres/engine1.py` (adapter),
`postgres/catalog.py` and `postgres/schema_manifest.json` (generated live-schema manifest), `postgres/engine_import.py`;
tests `tests_postgres/test_engine1_parity.py`, `test_engine1_storage.py`, `test_engine1_suites_on_postgresql.py`,
`test_live_schema_gate.py`, and the test plugin `tests_postgres/engine1_on_postgres.py`; this report.

Modified: `baec_app/data/database.py` (`StorageRefusal`), `baec_app/data/repository.py` (catch `StorageRefusal`;
`RETURNING`); `baec_console/backends.py` (Engine 1 through the backend; `PostgresBackend(storage)` no longer takes an
Engine 1 directory), `console_service.py` (`_e1`, `_e1_repo`, handoff readers through the backend; docstrings),
`engine1_adapter.py` (`Engine1Session` takes a connection), `engine2_adapter.py` / `engine3_adapter.py` (handoff
reader passed in), `postgres/stores.py` (lazy Engine 1 connection), `postgres/database.py` (`ENGINE_SEARCH_PATH`),
`postgres/engine2.py` (shared search path), `postgres/gate.py` (Engine 1 tables, triggers, functions; manifest checks;
Engine 1 and product refusals), `postgres/importer.py` (engines), `storage.py` (Engine 1 backend; refusal message),
`deployment.py`, `README_CONSOLE.md` (docstrings/docs); tests `tests/test_storage.py`,
`tests/test_identity_freshness.py` (docstring), `tests_postgres/conftest.py` (schema reset, two test roles),
`test_migrations_and_gate.py` (version pins, 8 Engine 1 sabotage cases, a leaked SQLite connection closed),
`test_service_parity.py` (Engine 1 actions and tables), `test_importer.py` (+7);
`docs/V04_ENGINE1_POSTGRES_MIGRATION_PLAN.md` (status note, §14 checklist).

Not modified: Engine 1 decision logic, the state machine, the classifier, the confirmation gate, Engine 2 and Engine 3
code, the public demo, the frozen public research release, requirements files.

## 18. Migrations added

`0004_engine1_schema.sql` (schema `baec_engine1`: 25 tables, 32 functions, 123 triggers, 4 explicit indexes plus 2
text-key indexes, 75 policies, grants). No existing migration was edited.

## 19–24. Results

Local PostgreSQL 16.15 container (`scripts/postgres_test_db.sh`); main venv (SQLite 3.50.4) for SQLite and repository
suites; `.venv-postgres` (psycopg 3.2.10) for PostgreSQL.

| | Suite | Result |
|---|---|---|
| 19 | `baec_console/tests` (SQLite, no network) | **462 passed** |
| 20 | `baec_console/tests_postgres`, all (`-W error::ResourceWarning`) | **853 passed**, 0 failed |
| 21 | Engine 1 parity: `test_engine1_parity.py` 489 (seed 1, attacks 277, coverage 1, per-trigger mutation 92, clause mutation 93, CHECK 25) + `test_engine1_storage.py` 47 + existing suites on PostgreSQL 1 (850 inner tests passed, 69 classified skips) | **537 passed** |
| 22 | Engine 2 parity: `test_engine2_parity.py` | **115 passed**, unchanged |
| — | importer 23, live-schema gate 35, migrations and gate 64, service parity 47, RLS 22, pool 7, store parity 3 | all passed |
| 23 | repository `tests/`, three consecutive runs, machine held awake with `caffeinate`, nothing else running, no sleep in the power log between 10:31 and 10:38 | **7597 passed, 2 deselected (`live_claude`), 0 failures, 0 errors** in each: 98.96 s, 99.08 s, 99.51 s |
| 24 | `git diff --check`, plus a trailing-whitespace check of new files | clean |

The SQLite repository suites were also run directly after the source changes: 1,936 Engine 1 tests (repository,
transitions, schema guards, evidence fidelity, database, mutation coverage, seed, application, proposal authorization,
grant audit chain) passed unchanged.

Fixed along the way (each found by a test, none by loosening one): the failure-code planning trap (§5); a pre-existing
`with sqlite3.connect(...)` in `test_migrations_and_gate.py` that never closed its connection, whose garbage
collection raised an unraisable-exception error in whichever later test it landed on (it explains a one-off failure
seen during development and the warning text in the Phase 3B baseline output).

## 25. Exact remaining blocker before real authentication

Storage no longer blocks. The one production refusal left is `PRODUCTION_CONSOLE_NOT_BUILT`: there is no
authenticated HUMAN principal mechanism and no production composition of `Console(..., backend=PostgresBackend)`. In
order, what remains before or with authentication:

1. **Sign-in to principal resolution** (Phase 3 §12.3, unchanged): under RLS a user can be read only once the
   organization is known, so a verified organization claim or a narrowly scoped subject → (user, organization) lookup
   is needed. This is the first authentication-phase item.
2. **Production composition**: build the production console on `PostgresBackend` behind that principal (no SQLite
   anywhere), keeping principal freshness per action.
3. **Operations** (Phase 3 §12.4, unchanged): TLS required by the gate, secret storage, backups, connection limits;
   regenerate `schema_manifest.json` with every new migration (the suite refuses a stale one).
4. **Residual risks, documented, not blockers:** the application chooses its tenant context, so RLS protects against
   application bugs, not a compromised process; the SQLite development store keeps its non-atomic cross-file writes;
   synthetic reset is refused on PostgreSQL; the Engine 1 AI-provenance/bridge stores (MCP and review page) remain
   SQLite development tools; an Engine 1 / Engine 2 lock-order deadlock would abort one action (none exists today).

### Documented SQLite/PostgreSQL divergences (none reachable through the application)

| Behavior | SQLite | PostgreSQL |
|---|---|---|
| CHECK-violating corruption | storable with `ignore_check_constraints` | cannot be stored (stricter) |
| U+0000 in text | storable | refused by the adapter (`StorageRefusal`) |
| `julianday` of non-ISO text (`now`, time-only, Julian numbers, negative years) | a value | NULL, so the TTL CHECK and window refuse |
| constraint (not trigger) refusal messages | SQLite wording | PostgreSQL wording (same exception class: `RepositoryConflictError`) |
| `staleness_status` / account columns other than `state` | writable by any SQLite writer | not writable by the application role |
| deleting an unreferenced account row | allowed (no trigger) | no DELETE privilege for the application role |
| a failed later write in one console action | earlier Engine 1 write stays (separate files) | nothing stays (one transaction) |
