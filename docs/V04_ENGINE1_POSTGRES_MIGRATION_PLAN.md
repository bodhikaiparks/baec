# V0.4 — Engine 1 PostgreSQL Migration Plan

> **Phase 3C status (implemented).** This plan was written as a design in Phase 3B. It has since been implemented
> (migration `0004_engine1_schema.sql`, `baec_console/postgres/engine1.py`) and tested on both backends; results are
> in `docs/V04_PHASE3C_ENGINE1_POSTGRES_REPORT.md`. Sections 1–13 below are kept as written, as the design record.
> Where the implementation departs from them: (1) BEFORE INSERT guards are one PostgreSQL trigger per SQLite trigger,
> named `f<k>_<name>` so that alphabetical order equals SQLite's firing order, instead of one consolidated function
> per table (same effect on messages, and per-trigger mutation and gate checks stay 1:1); (2) the TTL CHECK and the
> consumption window use `baec_engine1.julianday()`, a reimplementation of SQLite's own julianday, so the §12.5
> millisecond-precision divergence does not occur; (3) owner decisions taken: no FK from Engine 1 accounts to
> `baec.account_ownership` (same strictness as SQLite; RLS isolates), no UPDATE on `baec_records` for the application
> role, timestamps kept as text, synthetic reset not built; (4) the AI-provenance and bridge stores stay SQLite-only
> (their schema and every trigger are in PostgreSQL); (5) the trigger-by-trigger checklist is §14.

Branch `baec-crm-v0.4`, on top of `ef15e8b`. **This is a design. Nothing in Engine 1 has been ported to
PostgreSQL**, no Engine 1 source file or existing test was changed, and no PostgreSQL database was used to write it.
All data mentioned is synthetic. Tests cited are software tests of the storage layer. They are not evidence for the
BAEC theory, and passing them is not a production security assessment.

Authority order (CLAUDE.md): the manuscript, then `docs/RESEARCH_CONTRACT.md`, then code. This plan changes no
theory rule. Every database rule below is labelled by source: **DEFINITIONAL**, **PROPOSED**, **MANAGERIAL**, or
**IMPLEMENTATION**. Nearly all of them are IMPLEMENTATION: storage-layer choices that enforce contract rules. None
is a research finding.

---

## 1. Summary

| Question | Answer |
|---|---|
| Engine 1 schema objects, counted on a fresh database | **25 tables, 92 triggers, 4 explicit indexes**, schema version 7 (`PRAGMA user_version`). This matches Phase 3 (§2 of `V04_PHASE3_POSTGRES_RLS_REPORT.md`). |
| Does any other module add Engine 1 tables or triggers at open time? | **No.** Only `baec_app/data/database.py` creates Engine 1 objects (`initialize_schema`, and `migrate_v6_to_v7` for an upgrade). Engine 2 has its own files and openers. `seed.py` writes rows (including `schema_meta`), not schema. |
| Trigger invariant groups | 8 groups, 92 triggers, each trigger in exactly one group (§4). Five more invariant groups have **no** trigger: they are CHECK/FK constraints or repository rules (§4.2). |
| Proposed PostgreSQL shape | Schema `baec_engine1`. Every table gets `organization_id` and `workspace`, keys and FKs that include them, FORCE RLS with the `tenant_isolation`/`tenant_boundary` pair, and `baec_app` with SELECT and INSERT only. One PL/pgSQL guard function **per table and event**, so the checks run in SQLite's observed order and keep SQLite's messages. A per-workspace transaction advisory lock stands in for `BEGIN IMMEDIATE` (§6). |
| Is the Engine 2 sqlite3-compatible adapter viable for Engine 1? | **Not unchanged.** It breaks on: exact `type(connection) is sqlite3.Connection` checks (4 places); `except sqlite3.IntegrityError` used for control flow (5 places); `cursor.lastrowid` (6 tables); `IS ?` with a parameter; `PRAGMA foreign_keys` and `PRAGMA query_only` probes; `connection.backup`, `serialize`/`deserialize`; SQLite's statement-level rollback inside a transaction; `ORDER BY` text under a non-C collation. The details are in §9. |
| Mutation coverage | Every one of the 92 triggers was dropped on its own: **92 of 92 killed** by an existing behavioral test, ignoring schema-inventory pins (§11.2). Going deeper, 56 clause-level mutants were run: 33 were killed by existing tests, 23 survived. The new `tests/test_engine1_trigger_mutation_coverage.py` (21 cases) kills 21 of the 23. The remaining 2 are equivalent mutants and are argued in §11.3. |
| Readiness | **Not ready to start the port yet.** It can start once the preconditions in §12.2 are met. Every invariant is mapped and every non-equivalent mutant is killed. What is missing is approved source changes at the `sqlite3` connection boundary and a PostgreSQL conformance harness. |

---

## 2. Counts and how they were obtained

Taken programmatically: `baec_app.data.database.open_database(<tmp>/e1.sqlite3)`, then
`SELECT type, name, tbl_name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'` (SQLite 3.50.4).

| Object | Count | Notes |
|---|---|---|
| tables | 25 | `schema_meta` + 12 domain tables (`DATA_TABLES[:12]`) + 5 AI provenance (`AI_PROVENANCE_TABLES`) + 7 bridge (`BRIDGE_TABLES`). Every table is `STRICT`. |
| triggers | 92 | 34 created by schema v1–v4 (domain), 20 by v5–v6 (AI provenance), 38 by v7 (bridge). This equals `tests/test_database.py::test_working_copy_is_complete_independent_and_writable`'s pin `34 + len(AI_TRIGGERS) + len(BRIDGE_TRIGGERS) == 92`. |
| explicit indexes | 4 | `ai_proposals_account`, `human_authorization_grants_proposal`, `ai_proposal_confirmations_account`, and the partial unique `ai_proposal_review_decisions_one_rejection` (`WHERE decision = 'REJECTED'`). |
| `PRAGMA user_version` | 7 | `SCHEMA_VERSION`. |
| `AUTOINCREMENT` | none | The six `INTEGER PRIMARY KEY` columns are rowid aliases (max + 1). |

Other openers do not create schema. They open an existing file and check it. They are listed because the port has to
replace each one:
`baec_app/application/composition.py::open_read_connection` (`mode=ro`, `PRAGMA query_only`),
`baec_app/mcp_write/composition.py::_open_write_connection` (`mode=rw`),
`baec_app/data/ai_provenance.py::open_ai_provenance_store` (`mode=rw`),
`baec_app/application/proposal_review.py::open_review_connection`,
`baec_app/data/database.py::create_working_copy` (`sqlite3.Connection.backup`),
`baec_app/application/public_demo_recording.py` (in-memory `serialize`/`deserialize`), and
`baec_console/console_service.py` / `baec_console/engine1_adapter.py` (call `open_database`).

---

## 3. Table map

Columns are listed as declared (`NN` = NOT NULL). "CHECKs" counts `CHECK` clauses, including the enum lists generated
from `baec_app/domain/enums.py`. Every FK is `ON DELETE RESTRICT`. Every table is `STRICT`. "DB-assigned" means an
`INTEGER PRIMARY KEY` rowid alias whose value the repository reads back through `cursor.lastrowid`.

| Table | Columns | Primary key | Other UNIQUE | Foreign keys | CHECKs | Triggers |
|---|---|---|---|---|---|---|
| `schema_meta` | `key` TEXT NN, `value` TEXT NN | (key) | — | — | 0 | 0 |
| `accounts` | `account_id` TEXT NN, `name` TEXT NN, `state` TEXT | (account_id) | — | — | 1 | 0 |
| `interactions` | `interaction_id` TEXT NN, `account_id` TEXT NN, `occurred_at` TEXT NN, `text` TEXT NN | (interaction_id) | (interaction_id,account_id) | (account_id)→accounts | 0 | 3 |
| `interaction_evidence` | `evidence_id` INTEGER, `interaction_id` TEXT NN, `provenance` TEXT NN, `text` TEXT NN | (evidence_id) **DB-assigned** | (evidence_id,interaction_id); (interaction_id,provenance,text) | (interaction_id)→interactions | 1 | 4 |
| `human_authorizations` | `authorization_id` INTEGER, `authorized_by` TEXT NN, `authorized_at` TEXT NN, `action` TEXT NN, `subject_id` TEXT NN, `target_state` TEXT | (authorization_id) **DB-assigned** | — | — | 3 | 3 |
| `baec_records` | `baec_id` TEXT NN, `account_id` TEXT NN, `source_interaction_id` TEXT NN, `source_excerpt_id` INTEGER NN, `captured_at` TEXT NN, `buyer_role` TEXT, `buyer_exact_statement` TEXT, `articulation_origin` TEXT NN, `elicitation_mode` TEXT NN, `classification` TEXT NN, `classification_reason` TEXT, `staleness_status` TEXT, `confirmation_authorization_id` INTEGER, `normalized_text` TEXT, `normalized_generated_at` TEXT, `normalized_model` TEXT | (baec_id) | (baec_id,account_id); (baec_id,source_interaction_id); (confirmation_authorization_id) | (source_excerpt_id,source_interaction_id)→interaction_evidence(evidence_id,interaction_id); (source_interaction_id,account_id)→interactions(interaction_id,account_id); (confirmation_authorization_id)→human_authorizations | 5 | 3 |
| `criterion_assessments` | `baec_id` TEXT NN, `criterion` TEXT NN, `position` INTEGER NN, `finding` TEXT NN, `rationale` TEXT | (baec_id,criterion) | (baec_id,position) | (baec_id)→baec_records | 3 | 3 |
| `criterion_evidence` | `baec_id` TEXT NN, `criterion` TEXT NN, `position` INTEGER NN, `evidence_id` INTEGER NN, `source_interaction_id` TEXT NN | (baec_id,criterion,position) | — | (evidence_id,source_interaction_id)→interaction_evidence(evidence_id,interaction_id); (baec_id,source_interaction_id)→baec_records(baec_id,source_interaction_id); (baec_id,criterion)→criterion_assessments | 1 | 3 |
| `stringency_expressions` | `baec_id` TEXT NN, `verbatim_text` TEXT NN, `comparator` TEXT, `numeric_value` TEXT, `unit` TEXT, `qualitative_term` TEXT, `recurrence_text` TEXT, `timing_text` TEXT | (baec_id) | — | (baec_id)→baec_records | 1 | 3 |
| `dormancy_judgments` | `judgment_id` INTEGER, `baec_id` TEXT NN, `plausibility` TEXT NN, `addressability` TEXT NN, `notes` TEXT, `authorization_id` INTEGER NN | (judgment_id) **DB-assigned** | (judgment_id,baec_id); (authorization_id) | (authorization_id)→human_authorizations; (baec_id)→baec_records | 2 | 3 |
| `evaluation_evidence` | `evaluation_evidence_id` INTEGER, `account_id` TEXT NN, `interaction_id` TEXT NN, `evidence_id` INTEGER NN, `observed_at` TEXT NN | (evaluation_evidence_id) **DB-assigned** | — | (evidence_id,interaction_id)→interaction_evidence; (interaction_id,account_id)→interactions | 0 | 3 |
| `non_evaluation_evidence` | same shape as `evaluation_evidence`, key `non_evaluation_evidence_id` | (non_evaluation_evidence_id) **DB-assigned** | — | same as `evaluation_evidence` | 0 | 3 |
| `account_state_transitions` | `transition_id` INTEGER, `account_id` TEXT NN, `from_state` TEXT, `to_state` TEXT NN, `authorization_id` INTEGER NN, `baec_id` TEXT, `judgment_id` INTEGER, `evaluation_evidence_id` INTEGER, `non_evaluation_evidence_id` INTEGER, `ground` TEXT, `reason` TEXT, `basis_interaction_id` TEXT, `unresolved` TEXT NN, `recorded_at` TEXT NN | (transition_id) **DB-assigned** | (authorization_id) | (account_id)→accounts; (authorization_id)→human_authorizations; (baec_id)→baec_records; (judgment_id)→dormancy_judgments; (evaluation_evidence_id)→evaluation_evidence; (non_evaluation_evidence_id)→non_evaluation_evidence; (basis_interaction_id,account_id)→interactions; (baec_id,account_id)→baec_records; (judgment_id,baec_id)→dormancy_judgments | 3 | 3 |
| `ai_runs` | `ai_run_id`, `provider`, `task_type`, `task_version`, `account_id`, `interaction_id`, `requested_model`, `sdk_name`, `sdk_version`, `prompt_version`, `prompt_digest`, `input_version`, `input_digest`, `output_schema_version`, `output_schema_digest`, `canonicalization_version`, `request_spec_version`, `request_digest`, `validation_version`, `requested_at` (all TEXT NN), `retry_of_ai_run_id` TEXT | (ai_run_id) | (ai_run_id,account_id,interaction_id,task_type,task_version,output_schema_version) | (interaction_id,account_id)→interactions; (retry_of_ai_run_id)→ai_runs | 18 | 3 |
| `ai_run_results` | `ai_run_id` TEXT NN, `status` TEXT NN, `remote_outcome` TEXT NN, `provider_message_id`, `response_model`, `stop_reason`, `provider_request_id` TEXT, `input_tokens`, `output_tokens`, `cache_creation_input_tokens`, `cache_read_input_tokens` INTEGER, `failure_category`, `failure_codes`, `output_digest` TEXT, `completed_at` TEXT NN | (ai_run_id) | — | (ai_run_id)→ai_runs | 27 | 5 |
| `ai_run_outputs` | `ai_run_id` TEXT NN, `raw_output_text` TEXT NN, `output_digest` TEXT NN | (ai_run_id) | — | (ai_run_id)→ai_run_results | 1 | 4 |
| `ai_artifacts` | `artifact_id`, `ai_run_id`, `task_type`, `task_version`, `output_schema_version`, `account_id`, `interaction_id`, `canonical_result`, `artifact_digest`, `created_at` (all TEXT NN) | (artifact_id) | (artifact_id,interaction_id); (ai_run_id) | (ai_run_id,account_id,interaction_id,task_type,task_version,output_schema_version)→ai_runs; (ai_run_id)→ai_run_results | 3 | 4 |
| `ai_artifact_excerpts` | `artifact_id`, `excerpt_id`, `interaction_id`, `text`, `attributed_speaker` (all TEXT NN) | (artifact_id,excerpt_id) | (artifact_id,interaction_id,text) | (artifact_id,interaction_id)→ai_artifacts | 2 | 4 |
| `ai_proposals` | `proposal_id`, `origin`, `mapping_version`, `content_version`, `artifact_id`, `artifact_digest`, `account_id`, `interaction_id`, `content`, `proposal_digest`, `created_by`, `created_at` (all TEXT NN) | (proposal_id) | (artifact_id,mapping_version); (proposal_id,account_id,interaction_id,artifact_id,proposal_digest) | (artifact_id,interaction_id)→ai_artifacts; (interaction_id,account_id)→interactions | 8 | 4 |
| `ai_proposal_review_revisions` | `review_revision_id`, `proposal_id`, `proposal_digest`, `account_id`, `interaction_id`, `artifact_id` TEXT NN, `revision_number` INTEGER NN, `previous_revision_id` TEXT, `review_content_version`, `normalization_validation_version`, `content`, `review_content_digest`, `actor_label`, `created_at` TEXT NN | (review_revision_id) | (proposal_id,revision_number); (review_revision_id,proposal_id,account_id,interaction_id,artifact_id,review_content_digest) | (proposal_id,account_id,interaction_id,artifact_id,proposal_digest)→ai_proposals; (previous_revision_id)→ai_proposal_review_revisions | 8 | 6 |
| `ai_proposal_review_decisions` | `decision_id` TEXT NN, `proposal_id` TEXT NN, `review_revision_id` TEXT, `account_id`, `decision`, `actor_label`, `decided_at` TEXT NN | (decision_id) | (review_revision_id); (decision_id,review_revision_id); **partial** (proposal_id) WHERE decision='REJECTED' | (proposal_id)→ai_proposals; (review_revision_id)→ai_proposal_review_revisions | 4 | 7 |
| `human_authorization_grants` | `grant_id`, `grant_format`, `action`, `issuing_surface`, `account_id`, `interaction_id`, `artifact_id`, `proposal_id`, `proposal_digest`, `review_revision_id`, `review_content_digest`, `decision_id` TEXT NN, `issue_sequence` INTEGER NN, `supersedes_grant_id` TEXT, `baec_id`, `actor_label`, `issued_at` TEXT NN, `ttl_seconds` INTEGER NN, `expires_at`, `grant_digest` TEXT NN | (grant_id) | (baec_id); (supersedes_grant_id); (review_revision_id,action,issue_sequence); (grant_id,baec_id,account_id,interaction_id,artifact_id,proposal_id,review_revision_id) | (review_revision_id,proposal_id,account_id,interaction_id,artifact_id,review_content_digest)→ai_proposal_review_revisions; (decision_id,review_revision_id)→ai_proposal_review_decisions; (supersedes_grant_id)→human_authorization_grants | 11 | 6 |
| `human_authorization_grant_supersessions` | `grant_id` TEXT NN, `reason` TEXT NN, `superseding_revision_id` TEXT, `rejection_decision_id` TEXT, `actor_label` TEXT NN, `superseded_at` TEXT NN | (grant_id) | — | (grant_id)→human_authorization_grants; (superseding_revision_id)→ai_proposal_review_revisions; (rejection_decision_id)→ai_proposal_review_decisions | 4 | 4 |
| `ai_proposal_confirmations` | `baec_id`, `artifact_id`, `proposal_id`, `review_revision_id`, `grant_id` TEXT NN, `authorization_id` INTEGER NN, `account_id`, `interaction_id` TEXT NN | (baec_id) | (artifact_id); (proposal_id); (review_revision_id); (grant_id); (authorization_id) | (baec_id,account_id)→baec_records; (baec_id,interaction_id)→baec_records(baec_id,source_interaction_id); (grant_id,baec_id,account_id,interaction_id,artifact_id,proposal_id,review_revision_id)→human_authorization_grants; (authorization_id)→human_authorizations | 0 | 6 |
| `human_authorization_grant_consumptions` | `grant_id`, `baec_id`, `executor_surface`, `consumed_at` (all TEXT NN) | (grant_id) | (baec_id) | (grant_id)→ai_proposal_confirmations(grant_id); (baec_id)→ai_proposal_confirmations(baec_id) | 1 | 5 |

Mutable tables. `accounts.state` is the only column the application updates. It changes through a compare-and-swap
`UPDATE accounts SET state = ? WHERE account_id = ? AND state IS ?` in `Repository._update_account_state`.
`baec_records.staleness_status` is left unlocked for a later phase, but no code updates it today. `schema_meta` is
written once by the seed. These three tables have no append-only trigger. `test_database.py` pins `accounts`,
`baec_records`, and `schema_meta` as the "unlocked" set.

---

## 4. Invariant groups (every trigger, exactly once)

### 4.1 Completeness check (trigger groups)

| # | Group | Count | Triggers (exact names) |
|---|---|---|---|
| G1 | Append-only: no UPDATE, no DELETE | 45 | `<t>_no_update` and `<t>_no_delete` for the 22 tables `interactions`, `interaction_evidence`, `human_authorizations`, `criterion_assessments`, `criterion_evidence`, `stringency_expressions`, `dormancy_judgments`, `evaluation_evidence`, `non_evaluation_evidence`, `account_state_transitions`, `ai_runs`, `ai_run_results`, `ai_run_outputs`, `ai_artifacts`, `ai_artifact_excerpts`, `ai_proposals`, `ai_proposal_review_revisions`, `ai_proposal_review_decisions`, `human_authorization_grants`, `human_authorization_grant_supersessions`, `ai_proposal_confirmations`, `human_authorization_grant_consumptions` (44); plus `baec_records_no_delete` (1) |
| G2 | No row replacement | 23 | `interactions_no_replace`, `interaction_evidence_no_replace`, `human_authorizations_no_replace`, `baec_records_no_replace`, `criterion_assessments_no_replace`, `criterion_evidence_no_replace`, `stringency_expressions_no_replace`, `dormancy_judgments_no_replace`, `evaluation_evidence_no_replace`, `non_evaluation_evidence_no_replace`, `account_state_transitions_no_replace`, `ai_runs_no_replace`, `ai_run_results_no_replace`, `ai_run_outputs_no_replace`, `ai_artifacts_no_replace`, `ai_artifact_excerpts_no_replace`, `ai_proposals_no_replace`, `ai_proposal_review_revisions_no_replace`, `ai_proposal_review_decisions_no_replace`, `human_authorization_grants_no_replace`, `human_authorization_grant_supersessions_no_replace`, `ai_proposal_confirmations_no_replace`, `human_authorization_grant_consumptions_no_replace` |
| G3 | Immutable source and decision columns | 1 | `baec_records_protected_no_update` |
| G4 | Verbatim evidence | 2 | `interaction_evidence_verbatim`, `ai_artifact_excerpts_verbatim` |
| G5 | AI run provenance chain and closed vocabularies | 4 | `ai_run_results_model_binding`, `ai_run_results_failure_codes_closed`, `ai_run_outputs_require_result`, `ai_artifacts_require_success` |
| G6 | Proposal lineage and the review state machine | 6 | `ai_proposals_artifact_binding`, `ai_proposal_review_revisions_sequence`, `ai_proposal_review_revisions_open`, `ai_proposal_review_decisions_binding`, `ai_proposal_review_decisions_latest`, `ai_proposal_review_decisions_terminal` |
| G7 | Grant issuance and retirement | 6 | `human_authorization_grants_accepted`, `human_authorization_grants_proposal_digest`, `human_authorization_grants_one_unretired`, `human_authorization_grant_supersessions_binding`, `ai_proposal_review_revisions_supersede_grants` (AFTER INSERT, writes), `ai_proposal_review_decisions_reject_supersedes` (AFTER INSERT, writes) |
| G8 | Confirmation and grant consumption (the human-authorization link) | 5 | `ai_proposal_confirmations_authorization_binding`, `ai_proposal_confirmations_unretired`, `ai_proposal_confirmations_classification`, `human_authorization_grant_consumptions_window`, `human_authorization_grant_consumptions_pairing` |
| | **Total** | **92** | 45 + 23 + 1 + 2 + 4 + 6 + 6 + 5 = 92 = the actual count |

Two triggers write rows: G7's two AFTER INSERT triggers. The other 90 are refusal-only: 88 `BEFORE INSERT/UPDATE/DELETE
… RAISE(ABORT, …)` plus the two AFTER triggers. The refusal-only triggers raise `sqlite3.IntegrityError`, whose
`sqlite_errorname` is `SQLITE_CONSTRAINT_TRIGGER`.

### 4.2 Invariant groups enforced without a trigger

Each of these also needs a PostgreSQL decision. None contributes to the 92.

| # | Group | Invariant | Where it is enforced today | Source label |
|---|---|---|---|---|
| N1 | Account states | Exactly three states. An unclassified account is `NULL`. | `CHECK (state IN (…))` generated from `AccountState`. NULL passes the CHECK. `Repository.add_account` refuses a non-None state. | MANAGERIAL (RC-20), IMPLEMENTATION (RC-21) |
| N2 | State-transition protections | A transition changes the state. The stored state equals the end of an unbroken `from_state → to_state` chain. Writes are compare-and-swap. | `Repository._update_account_state` (`… AND state IS ?`, 0 rows → `RepositoryConflictError`). `_verify_state_matches_history` on every load. `TransitionHistoryEntry` (records.py) on load. Domain `state_machine.py` on write. | IMPLEMENTATION |
| N3 | Dormancy gate and the signal rule | `CONDITIONALLY_DORMANT` needs a CURRENT confirmed BAEC, plausibility YES, and addressability not NO or NOT_YET. `ACTIVE_OPPORTUNITY` needs evaluation evidence and a human authorization for that destination. A signal alone never suffices. Leaving ACTIVE needs non-evaluation evidence. | Domain `state_machine.py` on write. `TransitionHistoryEntry` and `_verify_dormant_history_entry` on load (fail closed). In the database: `interaction_evidence.provenance IN ('BUYER_FACT','SELLER_OBSERVATION')`, so external (signal) evidence cannot be stored as interaction evidence at all. The composite FKs bind a transition's BAEC to its account and its judgment to that BAEC. **No CHECK ties `to_state` to `evaluation_evidence_id`.** A raw-SQL row is refused on load, not on insert. | IMPLEMENTATION (RC-22, RC-34), MANAGERIAL (RC-27) |
| N4 | IDs and lineage | A row references only rows of the same account, interaction, BAEC, run, artifact, proposal, and revision. | Composite UNIQUE parent keys and composite FKs (§3). `ON DELETE RESTRICT`. | IMPLEMENTATION |
| N5 | Schema and version metadata | The file is at exactly schema 7. The v6→v7 upgrade is additive and verified against the exact v6 object set. | `PRAGMA user_version`, `require_current_schema`, `_reference_v6_objects`, `check_sqlite_version` (≥ 3.38.0), and the `schema_meta` rows written by the seed. | IMPLEMENTATION |

Human authorization. The schema checks the **shape** of an authorization: `CHECK ((action = 'CHANGE_ACCOUNT_STATE') = (target_state IS NOT NULL))`,
G8's derived-authorization binding, and the grant TTL. It does **not** prove that a human acted. Per CLAUDE.md and
RC-31, `HumanAuthorization` is a domain requirement and not the security boundary. A PostgreSQL port does not change
that, and this plan does not claim otherwise.

---

## 5. Per group: SQLite mechanism → PostgreSQL mechanism

Conventions for every group (IMPLEMENTATION):

* **Schema and tenancy.** Schema `baec_engine1`. Every table gets `organization_id text NOT NULL` and
  `workspace text NOT NULL CHECK (workspace IN ('local','synthetic'))`. Every PK, UNIQUE key, and FK is prefixed with
  `(organization_id, workspace)`, as in `0001_product_schema.sql`. Each table gets `ENABLE`/`FORCE ROW LEVEL SECURITY`
  and the same `tenant_isolation` (PERMISSIVE) and `tenant_boundary` (RESTRICTIVE) policies as `0002`. `baec_app` gets
  `SELECT, INSERT` everywhere, plus `UPDATE (state)` on `accounts` only. It gets no DELETE, TRUNCATE, REFERENCES, or
  TRIGGER. A `BEFORE TRUNCATE` statement trigger refuses TRUNCATE for every role, as in 0001.
* **One guard function per table and event.** SQLite has several BEFORE INSERT triggers per table. In SQLite 3.50.4
  they fire in **reverse creation order**. This was verified empirically: two refusing triggers on one table, and the
  later-created one's message wins. The SQLite documentation leaves this order unspecified. PostgreSQL fires
  same-event triggers **alphabetically by name**, and for 9 of the 12 multi-trigger tables that order differs (§6.2).
  Each SQLite trigger therefore becomes one `IF … THEN RAISE` block inside a single function per table and event,
  with the blocks in SQLite's observed order. The first violated rule's message then wins on both backends.
* **Error class.** Every refusal uses `RAISE EXCEPTION '<exact SQLite message>' USING ERRCODE = …` with a class-23
  SQLSTATE: `restrict_violation` (23001) for append-only and immutability refusals, and `unique_violation` (23505)
  for no-replace refusals. Only class 23 makes psycopg raise a subclass of `psycopg.errors.IntegrityError`. A bare
  `RAISE EXCEPTION` is `P0001`, which becomes `psycopg.errors.RaiseException`, an `InternalError`. That would slip
  past every `IntegrityError` mapping in §7.
* **NULL-safe comparisons.** SQLite's `a IS b` and `a IS NOT b` on values become `a IS NOT DISTINCT FROM b` and
  `a IS DISTINCT FROM b`. `COALESCE(<bool>, 0)` becomes `COALESCE(<bool>, false)`.
* **Text collation.** Every text comparison where order matters uses `COLLATE "C"`, which matches SQLite's BINARY and
  Python's `sorted`. Equality under the default deterministic collation is already bytewise.

### G1 — Append-only: no UPDATE, no DELETE (45)

* **Invariant.** Once written, rows of the 22 history tables are never changed or removed. `baec_records` rows are
  never removed. This covers source evidence, authorizations, assessments, judgments, transitions, AI provenance, and
  every bridge record. **IMPLEMENTATION**, enforcing RC-33 (source evidence is immutable) and the Phase 6/7 designs'
  "append-only history".
* **SQLite.** `BEFORE UPDATE|DELETE ON <t> BEGIN SELECT RAISE(ABORT, '<t> is append-only'); END`. For
  `baec_records_no_delete` the message is `'baec_records rows cannot be deleted'`.
* **PostgreSQL. Trigger function plus privileges**, defense in depth as in 0001. Use `baec_engine1.refuse_change()`
  (`'% is append-only', TG_TABLE_NAME`, ERRCODE `restrict_violation`) as a `BEFORE UPDATE OR DELETE FOR EACH ROW`
  trigger on the 22 tables. `baec_records` gets its own `BEFORE DELETE` function with the exact message `baec_records
  rows cannot be deleted`. On top of that: no UPDATE/DELETE privilege for `baec_app`, and a `BEFORE TRUNCATE`
  statement trigger. A constraint cannot express "never updated", which is why a trigger function is needed.
  Privileges alone would surface as `InsufficientPrivilege` (42501), a `ProgrammingError`, not as SQLite's
  IntegrityError. Revoking privileges protects against the application role. The trigger protects against the owner,
  the migration role, and a future grant.
* **SQLite specifics.** A trigger fires per row, so an UPDATE matching zero rows raises nothing on both backends.
  `UPDATE … SET x = x` is refused on both. An `INSERT … ON CONFLICT DO UPDATE` upsert reaches the UPDATE trigger on
  both: SQLite's UPSERT runs the UPDATE path, and in PostgreSQL it fails on privilege first unless the role holds
  UPDATE. TRUNCATE has no SQLite equivalent. In SQLite `DELETE FROM t` without WHERE still fires row triggers (the
  truncate optimization is disabled when triggers exist).

### G2 — No row replacement (23)

* **Invariant.** No insert may collide with an existing row on **any** UNIQUE key, including through `INSERT OR
  REPLACE` or `INSERT OR IGNORE`. A stored row can therefore never be silently deleted-and-reinserted, or silently
  skipped. The `ai_proposal_review_decisions` guard also covers the partial index: no second REJECTED decision per
  proposal. **IMPLEMENTATION** (RC-33).
* **SQLite.** `BEFORE INSERT … WHEN EXISTS(…key1…) OR EXISTS(…key2…) … RAISE(ABORT, '<t> rows cannot be replaced')`.
  It exists because SQLite's REPLACE conflict resolution deletes the old row **without firing DELETE triggers**.
  Because a BEFORE trigger runs before conflict resolution, it also turns every plain duplicate INSERT, and every
  `OR IGNORE`, into this message.
* **PostgreSQL.** PostgreSQL has no REPLACE. `ON CONFLICT DO UPDATE` needs UPDATE (not granted) and would hit G1.
  `ON CONFLICT DO NOTHING`, however, **would silently skip** a duplicate. PostgreSQL row-level BEFORE INSERT triggers
  fire before the speculative insertion, so keeping a BEFORE INSERT collision check refuses DO NOTHING too, with the
  same message. Proposal: **a trigger function block** (first in each table's INSERT guard, §6.2 order) that tests
  the same key tuples with `EXISTS`, `RAISE … USING ERRCODE = 'unique_violation'`. The UNIQUE constraints stay as the
  race-proof backstop.
* **Risk: the race window.** Two concurrent inserts of the same key can both pass an `EXISTS` check under READ
  COMMITTED. The UNIQUE index then refuses the second with PostgreSQL's own `duplicate key value violates unique
  constraint …` message. The exception class is still `UniqueViolation`/`IntegrityError`. The per-workspace write
  lock (§6.1) removes this window for Engine 1 code paths.
* **Risk: index size limit.** `UNIQUE (interaction_id, provenance, text)` and `UNIQUE (artifact_id, interaction_id,
  text)` index **free text**. A PostgreSQL btree entry is limited to about 2.7 kB, so a long excerpt would fail to
  insert with `program_limit_exceeded` (54000), which SQLite never raises. Proposal: replace those two UNIQUE
  constraints with a unique index on `(…, sha256(convert_to(text,'UTF8')))` (IMPLEMENTATION). The G2 trigger keeps
  comparing the full text, so the message and behavior are unchanged, and the digest index is only the concurrency
  backstop. The composite FKs that reference `(evidence_id, interaction_id)` are unaffected.

### G3 — Immutable source and decision columns (1)

* **Invariant.** On `baec_records`, every column except `staleness_status` is immutable: the 15 columns in
  `BAEC_RECORD_PROTECTED_COLUMNS`, covering the buyer's exact statement, capture time, source interaction and
  excerpt, origin, elicitation mode, classification and reason, confirmation authorization, and AI normalization.
  **IMPLEMENTATION** (RC-33; RC-16 for classification). `staleness_status` stays open for RC-29's later
  reassessment phase.
* **SQLite.** `BEFORE UPDATE OF <15 columns> ON baec_records … RAISE(ABORT, 'baec_records source and decision columns
  are immutable')`. It fires whenever any listed column appears in the SET list, even if the value is unchanged.
* **PostgreSQL. Trigger function** `BEFORE UPDATE OF <same 15 columns>`. PostgreSQL has the same "named in SET"
  semantics. ERRCODE `restrict_violation`. Also add an `IS DISTINCT FROM` check of `(organization_id, workspace)` so
  tenancy columns are immutable too. Column-level `GRANT UPDATE (staleness_status)` is **withheld** until a staleness
  phase exists. This is an implementation choice, stricter than SQLite: no code updates the column today. The
  conformance test `test_rc33_staleness_status_is_the_only_mutable_baec_record_column` must then run its raw UPDATE as
  the migration/owner role, not as `baec_app`.
* **Risk.** A `BEFORE UPDATE OF` list must be maintained by hand. The PostgreSQL migration should generate it from
  the same `BAEC_RECORD_PROTECTED_COLUMNS` tuple, and a conformance test should compare the two.

### G4 — Verbatim evidence (2)

* **Invariant.** Stored buyer-fact and seller-observation text, and every AI artifact excerpt, is a non-blank, exact,
  case-sensitive substring of the cited interaction's text. A missing interaction is refused too. **IMPLEMENTATION**
  (RC-33, RC-32; schema v4 and Phase 6 §10). It makes "the buyer said X" verifiable in storage, and it keeps AI
  excerpts from inventing buyer statements.
* **SQLite.** `interaction_evidence_verbatim`: `NEW.text = '' OR NOT COALESCE(instr((SELECT text FROM interactions
  WHERE interaction_id = NEW.interaction_id), NEW.text) > 0, 0)`. `ai_artifact_excerpts_verbatim`: the same with
  `trim(NEW.text, ' '||char(9,10,11,12,13)) = ''`.
* **PostgreSQL. Trigger function** (a cross-table read, so it cannot be a CHECK).
  `strpos(<interaction text>, NEW.text) > 0`, using `COALESCE(…, false)` and `NEW.text = ''`. For excerpts,
  `btrim(NEW.text, E' \t\n\x0b\f\r') = ''`. The interaction lookup must also match `organization_id` and `workspace`,
  so a cross-tenant interaction counts as missing.
* **SQLite specifics and risks.**
  * `instr(x,'')` is 1 and `strpos(x,'')` is 1, so both need the explicit blank check. Both are code-point
    substring tests. Neither normalizes Unicode, which is correct: no normalization is wanted.
  * PostgreSQL `text` **cannot hold U+0000**. SQLite TEXT can. Interaction or evidence text containing NUL would be
    refused by PostgreSQL with `character_not_in_repertoire`/`untranslatable_character` (class 22, a `DataError`).
    Synthetic data has none. This narrowing of the input domain should be documented, and refused earlier by the
    repository.
  * A nondeterministic collation would make `strpos` raise. The schema must use the default deterministic
    collation.
  * The test variant "FK and CHECK off" (`PRAGMA foreign_keys = OFF`, `ignore_check_constraints = ON`) proves that
    the trigger alone refuses fabricated evidence. PostgreSQL cannot switch FKs off without also switching triggers
    off (`session_replication_role = replica` disables both). The conformance version must instead drop the FK in a
    scratch schema, or accept that this exact variant has no PostgreSQL analogue.

### G5 — AI run provenance chain and closed vocabularies (4)

* **Invariant.** A run's terminal result agrees with its requested model (`success` ⇒ same model; `model_mismatch` ⇒
  different). `failure_codes` is NULL except under `parse_failure` (exactly one code from the 5 parse codes) and
  `semantic_validation_failure` (≥ 1 codes from the vocabulary of **the run's recorded validator version**, strictly
  increasing). Raw output is stored only under a result naming the same digest. An artifact exists only for a
  successful run with its raw output. **IMPLEMENTATION** (Phase 6 §10–§11; Phase 6D §5–§6). AI output is
  interpretation, never buyer evidence (RC-32).
* **SQLite.** Four `BEFORE INSERT` triggers. `ai_run_results_failure_codes_closed` uses `json_valid`, `json_type`,
  `json_array_length`, `json_extract`, a self-join of `json_each` on `key + 1`, and text `<`.
* **PostgreSQL. Trigger functions.** The failure-code rule becomes a `STABLE` helper,
  `baec_engine1.failure_codes_closed(status text, codes text, validation_version text) RETURNS boolean`:
  `codes IS JSON ARRAY`, then `jsonb_array_length`, `jsonb_typeof(elem) = 'string'`, and
  `jsonb_array_elements_text(…) WITH ORDINALITY` for the adjacency comparison. The vocabularies are generated from
  `AI_PARSE_FAILURE_CODES` and `AI_SEMANTIC_FAILURE_CODES_BY_VALIDATION_VERSION`, as `database.py` generates them
  today. The CHECKs on `ai_run_results` (27 CHECK clauses) port as CHECK constraints, using `IS NOT DISTINCT FROM` for
  SQLite's `stop_reason IS 'end_turn'`. `ai_run_outputs_require_result` could be a composite FK `(ai_run_id,
  output_digest) → ai_run_results`, but the FK message differs. Keep it as a trigger block for message parity.
* **Risks.**
  * **Ordering.** `earlier.value < later.value` compares under BINARY in SQLite. In PostgreSQL it must be
    `COLLATE "C"`. Under a libc or ICU collation such as `en_US`, underscores are ignored at the first level, so the
    "strictly increasing" check would accept or refuse different arrays from SQLite and from Python's `sorted()`.
  * **JSON decoding.** Both engines decode `\u` escapes before comparing, so `"invalid_json"` passes both. The
    store refuses non-canonical spelling before the database is reached, and that must remain so.
  * **JSON5.** `json_valid(x)` with no flags accepts RFC 8259 text only. This was verified on 3.50.4:
    `json_valid('[1,]')` and `json_valid('{a:1}')` are 0. It gates the JSON functions, so SQLite refuses JSON5 just
    as PostgreSQL `IS JSON` does. The same holds for the `json_valid(content) AND json_type(content) = 'object'`
    CHECKs on `ai_proposals` and `ai_proposal_review_revisions`, which become `content IS JSON OBJECT`. `content`
    stays `text`, not `jsonb`, so the stored bytes, which the digests cover, are kept exactly (as in 0001).

### G6 — Proposal lineage and the review state machine (6)

* **Invariant.** An AI_DRAFT proposal snapshots exactly one stored, successful artifact of the same account and
  interaction, validated under `baec-extraction-validation/v2`. Review revisions form one gap-free chain per proposal
  (n extends exactly the latest n−1) and cannot extend a rejected or confirmed proposal. A decision names its own
  proposal, revision, and account. Only the latest revision can be ACCEPTED. Nothing follows a rejection or a
  confirmation. **IMPLEMENTATION** (Phase 7 §6, §11–§13). This is a workflow state machine. It is not an account state
  (RC-20).
* **SQLite.** `BEFORE INSERT` triggers with `EXISTS`/`MAX`/`ORDER BY revision_number DESC LIMIT 1` subqueries and
  `IS NOT` comparisons.
* **PostgreSQL. Trigger functions**, in the per-table guard order of §6.2. Every subquery is additionally scoped to
  `NEW.organization_id` and `NEW.workspace`.
* **Concurrency (the material risk).** These rules read *other rows* ("latest revision", "already rejected"). In
  SQLite, `BEGIN IMMEDIATE` holds the database write lock, so the read and the insert are atomic with respect to every
  other writer. Under PostgreSQL READ COMMITTED, two transactions can both see "revision 1 is latest" and both insert
  revision 2. `UNIQUE (proposal_id, revision_number)` catches that case, but not "accept a superseded revision" or
  "decide after a rejection". **Proposal: the per-workspace write lock (§6.1).** The alternatives were rejected:
  SERIALIZABLE turns these refusals into `40001` retries, a new error type the code does not handle.
  `SELECT … FOR UPDATE` on `ai_proposals` requires UPDATE privilege, which `baec_app` must not have.

### G7 — Grant issuance and retirement (6)

* **Invariant.** A grant can be issued only for an ACCEPTED, latest revision of an open proposal (not rejected, not
  confirmed), carrying that proposal's digest. At most one unretired grant exists per (revision, action). A re-issue
  names exactly its unretired predecessor with `issue_sequence − 1`. A supersession retires an unretired grant only
  by a later revision of its proposal or by a rejection of it. A new revision and a rejection each supersede every
  unretired grant of the proposal **in the same statement**. A grant's status is derived from rows that reference
  it, never from a mutable column. No trigger reads the current time. **IMPLEMENTATION** (Phase 7 §12–§13; the
  15-minute TTL is an IMPLEMENTATION setting, `BRIDGE_GRANT_TTL_SECONDS = 900`).
* **SQLite.** Four `BEFORE INSERT` refusals. Two `AFTER INSERT` triggers run `INSERT INTO
  human_authorization_grant_supersessions … SELECT … WHERE <unretired>`. Those nested inserts fire the supersession
  table's own BEFORE triggers (`…_binding`, `…_no_replace`).
* **PostgreSQL.** Refusals become **trigger function** blocks. The two writers become **`AFTER INSERT FOR EACH ROW`
  trigger functions** running the same `INSERT … SELECT`, with `organization_id` and `workspace` copied from `NEW`.
  They run as the invoking role, so `baec_app` needs INSERT (which it has) and RLS `WITH CHECK` applies to the
  generated rows. They must **not** be `SECURITY DEFINER`, which would let them escape FORCE RLS only if the owner
  had BYPASSRLS. It does not, but invoker rights are the simpler argument.
* **Timing difference.** SQLite runs an AFTER ROW trigger immediately after its row. PostgreSQL queues AFTER ROW
  triggers to the end of the statement, and its FK checks are themselves AFTER triggers named
  `RI_ConstraintTrigger_*`, which sort before lowercase names. Every Engine 1 statement inserts one row, so the
  observable result is the same: the supersessions exist when the statement returns
  (`test_a_new_revision_supersedes_unretired_grants_in_the_same_statement`). A multi-row `INSERT … SELECT` into
  revisions would differ. No code issues one, and the conformance suite should include a test asserting that.
* **TTL check.** `CHECK (COALESCE(abs((julianday(expires_at) − julianday(issued_at)) * 86400.0 − 900.0) < 0.001, 0))`
  becomes `CHECK (baec_engine1.ts(expires_at) − baec_engine1.ts(issued_at) = interval '900 seconds')`. Here `ts()` is
  an `IMMUTABLE` safe-parse function that returns NULL on bad text, so the CHECK fails rather than raising a
  `DataError`. PostgreSQL is **exact to the microsecond**. SQLite's `julianday` resolves fractional seconds only to
  the **millisecond**. Observed on 3.50.4: `.000499` equals `.000000`, `.000500` is 1 ms later, and `.999999` is
  treated as `.999`, never carried into the next second. The SQLite CHECK also
  tolerates 1 ms. The application already sets the exact 15-minute equality from microsecond datetimes, so legal
  rows are identical. The difference is listed in §12.5.
* **Concurrency.** As in G6: `one_unretired` and `accepted` read other rows, and the per-workspace write lock (§6.1)
  is required.

### G8 — Confirmation and grant consumption (5)

* **Invariant.** An AI-lineage confirmation link requires all of the following. The BAEC's own `CONFIRM_BAEC`
  authorization must be derived from the grant: same actor label, and `authorized_at` equal to the grant's
  `issued_at` **as stored text**. The grant must be unretired, on the latest revision of a proposal that has not
  been rejected. The BAEC must be `CONFIRMED_BAEC` and `CURRENT`. A consumption must fall inside `[issued_at,
  expires_at)` and pair with its confirmation link. Each lineage yields at most one confirmed BAEC (UNIQUE on
  artifact, proposal, revision, grant, and authorization). **IMPLEMENTATION** (RC-31; Phase 7F-B). This makes a
  model-produced value insufficient on its own *at the storage layer*: there must be a grant row issued by the
  separate human-authorization path. It is not proof of a human action (CLAUDE.md).
* **SQLite.** Five `BEFORE INSERT` refusals. `consumptions_window` compares
  `julianday(issued_at) <= julianday(consumed_at) < julianday(expires_at)`.
* **PostgreSQL. Trigger functions.**
  * The window becomes `ts(issued_at) <= ts(consumed_at) AND ts(consumed_at) < ts(expires_at)`, using the same
    safe-parse `ts()`. It compares stored values only, never `now()`.
  * Keep `authorization.authorized_at = grant_row.issued_at` as a **text** comparison. Converting to timestamptz
    would accept two different texts naming the same instant, which SQLite refuses.
  * `ConfirmationExecutionService.execute` relies on `BEGIN IMMEDIATE` ("concurrent executions serialize here").
    Under PostgreSQL that serialization is the per-workspace write lock (§6.1). Without it, two executions of one
    grant could both pass `unretired`. The UNIQUE keys would still refuse the second link, with a different message.

---

## 6. Cross-cutting design

### 6.1 Write serialization (replaces `BEGIN IMMEDIATE`) — IMPLEMENTATION

The SQLite file is one workspace, and `BEGIN IMMEDIATE` takes that file's single write lock. The faithful PostgreSQL
equivalent is a **transaction-level advisory lock per (organization, workspace)**, taken as the first statement of
every Engine 1 write transaction:

`SELECT pg_advisory_xact_lock(hashtextextended('baec_engine1:' || organization_id || ':' || workspace, 0))`

It needs no table privilege, is released at commit or rollback, and serializes exactly what SQLite serialized: the
writers of one workspace. Readers are not blocked: PostgreSQL MVCC readers never wait on it. That is more permissive
than SQLite's default rollback journal, where a reader can briefly see `SQLITE_BUSY`, but it reads only committed
data, so no invariant depends on it. The guard functions should
**also** take the lock themselves, with the same key, so that a raw insert outside the Engine 1 code paths is
serialized as well. Re-taking an advisory lock in the same transaction is cheap and re-entrant. A hash collision
between two workspaces only over-serializes. It never under-serializes.

### 6.2 Guard order per table (message parity)

Observed SQLite firing order (reverse creation) versus PostgreSQL's alphabetical order for the same names. Where
they differ, the consolidated guard function uses the SQLite column.

| Table / event | SQLite order (observed) | PostgreSQL alphabetical | Same? |
|---|---|---|---|
| `interaction_evidence` INSERT | verbatim, no_replace | no_replace, verbatim | differ |
| `ai_run_results` INSERT | failure_codes_closed, model_binding, no_replace | same | same |
| `ai_run_outputs` INSERT | require_result, no_replace | no_replace, require_result | differ |
| `ai_artifacts` INSERT | require_success, no_replace | no_replace, require_success | differ |
| `ai_artifact_excerpts` INSERT | verbatim, no_replace | no_replace, verbatim | differ |
| `ai_proposals` INSERT | artifact_binding, no_replace | same | same |
| `ai_proposal_review_revisions` INSERT | open, sequence, no_replace | no_replace, open, sequence | differ |
| `ai_proposal_review_decisions` INSERT | terminal, latest, binding, no_replace | binding, latest, no_replace, terminal | differ |
| `human_authorization_grants` INSERT | one_unretired, proposal_digest, accepted, no_replace | accepted, no_replace, one_unretired, proposal_digest | differ |
| `human_authorization_grant_supersessions` INSERT | binding, no_replace | same | same |
| `ai_proposal_confirmations` INSERT | classification, unretired, authorization_binding, no_replace | authorization_binding, classification, no_replace, unretired | differ |
| `human_authorization_grant_consumptions` INSERT | pairing, window, no_replace | no_replace, pairing, window | differ |

All other tables have one trigger per event. SQLite's order is not a documented guarantee. The conformance suite must
pin the expected message for each multi-violation case on the SQLite side as well, so that a SQLite upgrade that
changed the order would be noticed. Constraint order follows the same rule on both backends: BEFORE ROW triggers run
before NOT NULL, CHECK, and UNIQUE, and FK checks run at the end of the statement.

### 6.3 Tenancy, RLS, roles

As in 0001/0002 (Phase 3 §4–§6), with schema `baec_engine1` added to the production gate's expected table set and to
the RLS check. Engine 1's `account_id` is not globally unique across organizations, so every key includes
`(organization_id, workspace)`. Whether Engine 1 accounts should also carry an FK to
`baec.account_ownership (organization_id, workspace, account_id)` is an **open design question** for the owner. It
would make an unowned Engine 1 account impossible, which is stricter than today. Phase 3 made the same choice for
console tables.

---

## 7. Error and refusal behavior that must be preserved

| Where | Catches | Maps to | What must hold on PostgreSQL |
|---|---|---|---|
| `Repository._write` (repository.py) | `sqlite3.IntegrityError` | `RepositoryConflictError(f"the database refused the write: {error}")` | A database refusal still becomes `RepositoryConflictError`, and the text after the colon is the **same trigger message**. Use psycopg's `diag.message_primary`, because `str(psycopg.Error)` may append `DETAIL`/`CONTEXT` lines; verify on the pinned psycopg version. |
| `AiProvenanceStore._write` | same | same | same |
| `ProposalBridgeStore._write` | same | same | same |
| `AuthorizationGrantStore._write` | same | same | same |
| `ConfirmationExecutionService.execute` | `RepositoryConflictError`, `sqlite3.IntegrityError` | `ExecutionRefused("conflict")` | Any class-23 refusal inside the execution transaction → `ExecutionRefused("conflict")`. |
| `open_read_connection`, `open_ai_provenance_store`, `_open_write_connection` | `sqlite3.Error`, `sqlite3.DatabaseError` | `ReadDatabaseUnavailable`, `AiProvenanceStoreUnavailable`, write-runtime unavailability | Connection or schema failures map to the same unavailability errors, and the message never contains the URL or password (Phase 3 gate rule). |
| `public_demo_recording.py` | `sqlite3.Error` | demo refusal | Not ported. The public demo stays an ephemeral in-memory SQLite database (§9). |
| Tests | `pytest.raises(sqlite3.IntegrityError, match=…)` in the schema tests | — | The PostgreSQL conformance suite asserts `psycopg.errors.IntegrityError` and the **same message regex**. |

Message strings that tests match today: `is append-only`, `rows cannot be replaced`, `must occur verbatim`, `are
immutable`, `cannot be deleted`, `closed vocabulary`, `accepted latest revision`, `one unretired grant`, `CURRENT
confirmed BAEC`, `validity window`, `confirmed`. Every trigger message in §4.1 is carried over verbatim.

STRICT type errors are a related case. SQLite STRICT raises `sqlite3.IntegrityError` (`SQLITE_CONSTRAINT_DATATYPE`)
for a value that cannot be converted losslessly. PostgreSQL raises `DataError` (`22P02`/`42804`), a different class.
No application path relies on that mapping, because the repository refuses wrong types first.
`test_strict_tables_refuse_a_value_of_the_wrong_storage_type` must become "raises a psycopg error", not "raises
IntegrityError".

---

## 8. IDs and lineage

| Kind | Columns | Assigned by | Ordering dependency |
|---|---|---|---|
| Caller-supplied text IDs | `account_id`, `interaction_id`, `baec_id` (`BAEC-…`, CHECK on grants), `ai_run_id`, `artifact_id`, `excerpt_id`, `proposal_id` (`aiprop_` + uuid4 hex), `review_revision_id` (`aireview_…`), `decision_id` (`aidecision_…`), `grant_id` (`grant_` + 64 hex) | application (`uuid.uuid4().hex`, seed files, test fixtures) | none; prefix checks via GLOB |
| Database-assigned integer IDs (rowid aliases, read via `cursor.lastrowid`) | `interaction_evidence.evidence_id`, `human_authorizations.authorization_id`, `dormancy_judgments.judgment_id`, `evaluation_evidence.evaluation_evidence_id`, `non_evaluation_evidence.non_evaluation_evidence_id`, `account_state_transitions.transition_id` | SQLite: max(rowid) + 1 in the file (no AUTOINCREMENT; with no deletes this is dense, starts at 1, and **reuses** an id whose insert rolled back) | `ORDER BY transition_id` (state-history chain), `ORDER BY judgment_id` |
| Insertion order of text-keyed tables | `ORDER BY rowid` on `accounts`, `interactions`, `baec_records`, `ai_proposals`, `human_authorization_grants` (`grants_for_proposal`), `ai_artifact_excerpts`, and decisions (`proposal_bridge.py:419`) | SQLite rowid | list order returned to the UI, MCP resources, and bridge verification |
| Text-ordered reads | `ORDER BY requested_at ASC, ai_run_id ASC` (`ai_provenance.py`); `dump()` in tests (`ORDER BY 1, 2`) | — | needs `COLLATE "C"` |

PostgreSQL proposal (IMPLEMENTATION):

* Every table gets `seq bigint GENERATED ALWAYS AS IDENTITY`, and `ORDER BY rowid` becomes `ORDER BY seq`, as in
  0001. Under the per-workspace lock, commit order equals `seq` order within a workspace.
* The six database-assigned IDs: assign them in a BEFORE INSERT guard, as `coalesce(max(id), 0) + 1` **within
  `(organization_id, workspace)`**, while holding the §6.1 lock. That reproduces SQLite's numbering exactly: per
  workspace, dense, starting at 1, reused after a rollback. It also avoids a cross-tenant side channel, because a
  global identity would let one tenant observe another's write volume. Use `INSERT … RETURNING <id>` in place of
  `lastrowid`. The importer preserves IDs (as in Phase 3).
* `GENERATED BY DEFAULT` would be the alternative, but its gaps on rollback and its global numbering are both
  observable differences.

---

## 9. Is the Engine 2 adapter approach viable for Engine 1?

The Engine 2 adapter (`baec_console/postgres/engine2.py`, being written in parallel) wraps a psycopg connection
inside a tenant transaction. It translates `BEGIN IMMEDIATE/COMMIT/ROLLBACK` to savepoints, `rowid` to `seq`, `?` to
`%s`, and `PRAGMA user_version` to a migration record. It works for Engine 2 because Engine 2 "catches no database
exception type". **Engine 1 does not have that property.** Where the adapter breaks:

| SQLite usage in Engine 1 | Where | Adapter outcome | What it would take |
|---|---|---|---|
| `type(connection) is not sqlite3.Connection` → refuse | `AiProvenanceStore`, `ProposalBridgeStore`, `GrantExecutionStore`/`AuthorizationGrantStore`, `composition.build_proposal_facade` | **breaks**: no wrapper, not even a subclass, passes an exact `type() is` check | source change: a connection protocol, or an explicit allowlist of the two types |
| `except sqlite3.IntegrityError` for control flow | 4 `_write` helpers + `ConfirmationExecutionService.execute` | **breaks**: psycopg raises `psycopg.errors.IntegrityError`, which is not a `sqlite3.IntegrityError` | either the adapter re-raises `sqlite3.IntegrityError(diag.message_primary)` (a type lie, fragile) or the code catches a backend-neutral `StorageRefusal` |
| A failed statement leaves the transaction usable (SQLite statement rollback) | raw-SQL tests; any caller that catches and continues | **breaks**: PostgreSQL aborts the whole transaction until `ROLLBACK TO SAVEPOINT` | a savepoint around **every** statement in the adapter (cost: two extra round trips per statement) |
| `cursor.lastrowid` | `_evidence_id`, `_insert_authorization`, `_insert_judgment_row`, `_insert_state_evidence`, `_insert_history_row` | **breaks**: psycopg `lastrowid` is not the identity value | rewrite the inserts as `INSERT … RETURNING` (source change), or adapter-side per-table rewriting (fragile) |
| `… AND state IS ?` | `Repository._update_account_state` | **breaks**: `IS $1` is a syntax error in PostgreSQL | rewrite `IS ?`/`IS NOT ?` to `IS [NOT] DISTINCT FROM %s` (a regex translation, or a source change) |
| `PRAGMA foreign_keys` read (must be 1) | every store constructor, `migrate_v6_to_v7` | refused by the Engine 2 adapter (unknown PRAGMA) | answer 1 (PostgreSQL FKs cannot be disabled by `baec_app`) |
| `PRAGMA query_only = ON` / read back | `make_read_only`, `composition._query_only` (the MCP read server's read-only proof) | **breaks** | `SET TRANSACTION READ ONLY` and `SHOW transaction_read_only`. This is a security-relevant translation that must not fail open. |
| `PRAGMA user_version` | `require_current_schema` | works (migration record), as in Engine 2 | — |
| `sqlite3.connect(uri mode=ro/rw)` openers | four openers (§2) | not applicable: they open files | new composition functions taking a tenant session |
| `connection.backup()` | `create_working_copy` (canonical seed → session copy) | **breaks** | a seed-into-workspace routine; synthetic reset needs the Phase 3 §12.2 design |
| `serialize`/`deserialize` | public demo | not applicable | the public demo stays SQLite (ephemeral, synthetic, read-only) |
| `executescript` | not used by Engine 1 runtime code | — | — |
| `json_each`, `json_valid`, `GLOB`, `instr`, `trim(x, chars)`, `julianday`, `IS 'literal'`, STRICT | schema only (`database.py`) | not adapter concerns: the PostgreSQL schema is written in PL/pgSQL (§5) | — |
| `ORDER BY rowid` | 8 reads | works (`rowid` → `seq`) | — |
| `ORDER BY <text>` | `ai_provenance.py` | **silently different** under a non-C database collation | `COLLATE "C"` on those columns (schema), or database `LC_COLLATE = C` |
| `connection.in_transaction` | `insert_confirmed_record`, `record_confirmation`, `record_run` | works (savepoint stack) | — |

**Verdict.** An adapter is viable **only together with** a small set of source changes in the Engine 1 data layer:
a connection protocol in place of `type() is sqlite3.Connection`; a backend-neutral refusal type; `RETURNING` in
place of `lastrowid`; NULL-safe comparison; a read-only primitive. These are structural changes to working code. Per
CLAUDE.md they must be explained and approved before they are made. Without them, an adapter would have to impersonate
`sqlite3` exception and connection types, which hides behavior instead of preserving it. The schema side (all 92
triggers) is a rewrite in PL/pgSQL either way. No adapter can translate `RAISE(ABORT)` triggers.

---

## 10. Test map (existing Engine 1 tests → invariants)

"Tests" counts `def test_` functions before parametrization. The 54 files run in §11 collect 4,312 test cases.

| Test file | Tests | Invariants covered |
|---|---|---|
| `tests/test_database.py` | 21 | N5 (version, SQLite ≥ 3.38, empty-database refusal), STRICT, enum CHECK lists, G1 inventory (`test_append_only_triggers_cover_exactly_the_approved_tables`), the 92-trigger pin, transactions (`BEGIN IMMEDIATE`, commit-failure rollback), read-only connection |
| `tests/test_rc33_schema_guards.py` | 12 | G1, G2 (every UNIQUE key, REPLACE/IGNORE), G3 (each protected column, upsert), presence of guards in the seed and working copies |
| `tests/test_evidence_fidelity.py` | 17 | G4 (interaction evidence; raw SQL with guards on and with FK/CHECK off; missing interaction) |
| `tests/test_repository_integrity.py` | 21 | G1 through the repository, N2/N3 fail-closed rehydration, atomic writes, `RepositoryConflictError` mapping |
| `tests/test_repository_roundtrip.py` | 24 | N4, round-trip fidelity of every Phase 3 object (verbatim text, thresholds as stated) |
| `tests/test_transition_persistence.py` | 34 | N1, N2, N3 (dormancy gate on persist and load, CAS update, history chain, RC-34) |
| `tests/test_state_machine.py` | 22 | N1, N3 in the domain (no database) |
| `tests/test_seed.py` | 28 | N5 `schema_meta`, canonical/working split, `make_read_only` |
| `tests/test_ai_provenance_schema.py` | 27 | G1/G2 for AI tables, G5 CHECKs, version-5 inventory |
| `tests/test_ai_failure_code_contract.py` | 36 | G5 `failure_codes_closed` (per validator version, sorted/unique, JSON shape) |
| `tests/test_ai_provenance_store.py`, `test_ai_provenance_atomicity.py`, `test_ai_provenance_integrity.py` | 16 / 6 / 7 | G5 via the store, G4 excerpts, terminal atomicity, corrupt-row fail-closed |
| `tests/test_proposal_bridge_schema.py` | 38 | G6, G7, G8 at the SQL level; v6 → v7 migration (N5); G1/G2 for bridge tables |
| `tests/test_proposal_bridge_store.py` | 16 | G6 via `ProposalBridgeStore` |
| `tests/test_proposal_authorization.py`, `test_grant_audit_chain.py` | 30 / 9 | G7, G8 through `ProposalAuthorizationService` and `ConfirmationExecutionService` |
| `tests/test_proposal_review.py`, `test_review_page*.py` | 37 / 23 / 8 | G6 via the review application and page |
| `tests/test_phase7_*`, `test_phase7c/e/fb_boundaries.py` | 3 / 23 / 16 / 6 / 6 | end-to-end G6–G8, adversarial bridge use, architecture boundaries |
| `tests/test_ai_proposal_mapping*.py`, `test_ai_verification.py`, `test_human_normalization.py` | 34 / 13 / 12 / 32 | G6 `artifact_binding` eligibility (validation v2), normalization validation |
| `tests/test_application_*.py` | 163 | N1–N3 via the facades, error pass-through (§7) |
| `tests/test_mcp_*.py` | 183 | read-only proof (`query_only`), write tool → G8 |
| `tests/test_engine1_trigger_mutation_coverage.py` (new) | 19 | G2 (one-rejection replace guard), G5 (failure codes outside parse/semantic statuses; artifact output digest), G6 (decision binding; artifact status), G7 (grant after rejection; re-issue predecessor revision; supersession scope and consumed grants), G8 (every clause of the authorization binding, the unretired check, and the classification check). Each test isolates one clause (§11.3). |
| `tests/test_public_demo*.py`, `test_live_*.py`, `test_ai_service.py`, `test_ai_composition.py`, `test_ai_grounding.py`, `test_ai_boundary.py` | — | use Engine 1 storage incidentally; not invariant-specific |

The empirical, per-trigger mapping is in §11.

---

## 11. Mutation coverage

### 11.1 Method (reproducible; the plugin is not in the repository)

The mutation tooling lives in the session scratchpad. It is described here so it can be rebuilt; it is not committed.

* **`e1_drop_triggers.py` (a pytest plugin, `-p e1_drop_triggers`).**
  * At import, it reads `BAEC_E1_DROP_TRIGGERS`, a comma-separated list of trigger names, and refuses any name that a
    fresh `open_database(":memory:")` does not have.
  * It wraps `baec_app.data.database.initialize_schema` and `migrate_v6_to_v7`. `open_database` and the seed call
    `initialize_schema` through the module global, so every Engine 1 schema in the test process is covered. Each
    wrapper calls the original first, which creates the schema and sets `user_version`, then runs `DROP TRIGGER`
    for each listed name. The drop therefore comes after creation and versioning.
  * The openers check only `PRAGMA user_version`, not a schema hash. The only "verification" is the v6 object
    comparison inside `migrate_v6_to_v7`, and that happens before the wrapper's drop.
  * File databases keep the mutation. The raw `sqlite3.connect(mode=ro|rw)` openers and `create_working_copy`
    (backup) therefore see it too.
  * A second mode, `BAEC_E1_REWRITE`, takes a JSON list of `[trigger, old, new]`. It recreates a trigger from its
    stored SQL with one clause replaced (the old text must occur exactly once). This is used for the clause-level
    mutants in §11.3.
* **`run_mutants.py`.** Runs one pytest process per mutant, nine in parallel, over the 54 Engine 1 test files:
  every `tests/test_*.py` that imports `baec_app.data`, `open_database`, or the persistence, bridge, AI-provenance,
  or MCP builders (4,312 test cases). Each run starts from a fresh database.
* **`classify.py`.** Re-runs each mutant's failing tests to capture the failure messages, then sorts every failure
  into one of three classes:
  * **inventory pin.** The test compares schema objects (`sqlite_master` names, counts, or object sets), so it fails
    for *any* missing trigger. There are 8 such tests, for example
    `test_database.py::test_append_only_triggers_cover_exactly_the_approved_tables`.
  * **artifact.** The test itself runs `DROP TRIGGER <the already dropped name>` and fails with `no such trigger`.
  * **behavioral.** Everything else: the protection's *behavior* was lost.

  A mutant counts as **killed** only if at least one *behavioral* test fails. Inventory pins and artifacts do not
  count.
* **Baseline (plugin loaded, nothing dropped).** 5 failures, all in `tests/test_live_report.py`. They come from
  parallel runs sharing the system temp directory: those tests assert on the set of `baec-live-eval-*` directories.
  They pass when run serially (§13), and they are subtracted from every mutant as noise.

To reproduce (zsh):

```sh
S=<scratchpad>/engine1_mutation; PY=.venv/bin/python
cd <repository root>
BAEC_E1_DROP_TRIGGERS=interaction_evidence_verbatim PYTHONPATH=$S \
  $PY -m pytest -q -p no:cacheprovider -p e1_drop_triggers tests/test_evidence_fidelity.py   # 7 failed
$PY $S/run_mutants.py $S/out1 9 $S/mutants.txt ${=FILES}      # all 92 + baseline
$PY $S/classify.py $S/out1/results.json $S/out1/classified.json
```

### 11.2 Single-trigger mutants: 92 of 92 killed

Every trigger, dropped on its own, makes at least one behavioral test fail. Across all mutants: 422 behavioral
failures and 369 inventory-pin failures. For two mutants (`ai_proposal_review_revisions_no_update` and
`ai_run_outputs_require_result`) the classification re-run hit a load glitch. Both were re-run by hand and fail as
listed. No mutant survived. Coverage by group:

| Group | Triggers | Killed | Representative killing test(s) |
|---|---|---|---|
| G1 append-only | 45 | 45 | `test_repository_integrity.py::test_rc33_append_only_tables_refuse_update_and_delete[<table>-<op>]`, `test_ai_provenance_schema.py::test_ai_tables_refuse_update_and_delete`, `test_proposal_bridge_schema.py::test_no_bridge_row_can_be_updated_deleted_or_replaced`, `test_rc33_schema_guards.py::test_rc33_baec_record_rows_cannot_be_deleted` |
| G2 no replace | 23 | 23 | `test_rc33_schema_guards.py::test_rc33_stored_rows_cannot_be_replaced[…]`, `::test_rc33_replace_through_a_secondary_unique_key_is_refused`, `test_ai_provenance_schema.py::test_ai_rows_cannot_be_replaced_or_silently_skipped`, `test_proposal_bridge_schema.py::test_no_bridge_row_can_be_updated_deleted_or_replaced` |
| G3 protected columns | 1 | 1 | `test_rc33_schema_guards.py::test_rc33_raw_update_of_a_protected_baec_record_column_is_refused` |
| G4 verbatim | 2 | 2 | `test_evidence_fidelity.py::test_raw_sql_fabricated_evidence_insert_is_refused_by_the_schema`, `test_ai_provenance_schema.py::test_excerpts_must_be_non_blank_exact_substrings_of_their_artifacts_interaction` |
| G5 AI provenance | 4 | 4 | `test_ai_provenance_schema.py::test_success_and_model_mismatch_are_bound_to_the_requested_model`, `test_ai_failure_code_contract.py::test_sql_refuses_every_illegal_code_set_in_a_raw_insert`, `test_ai_provenance_schema.py::test_output_requires_a_result_naming_the_same_digest`, `::test_an_artifact_for_a_non_success_result_is_refused` |
| G6 proposal lineage and review | 6 | 6 | `test_proposal_bridge_schema.py::test_a_proposal_binds_to_an_existing_eligible_artifact_…`, `::test_revisions_form_one_gap_free_chain`, `::test_only_the_latest_revision_can_be_accepted`, `::test_nothing_follows_a_rejection` |
| G7 grants | 6 | 6 | `test_proposal_bridge_schema.py::test_a_grant_requires_an_accepted_latest_revision`, `::test_at_most_one_unretired_grant_per_revision_and_action`, `::test_a_new_revision_supersedes_unretired_grants_in_the_same_statement`, `test_phase7_adversarial.py::test_lifecycle_rejected_proposal_cannot_be_authorized_and_its_grant_is_superseded` |
| G8 confirmation and consumption | 5 | 5 | `test_proposal_bridge_schema.py::test_a_confirmation_must_carry_the_authorization_derived_from_its_grant`, `::test_a_superseded_grant_cannot_be_linked_even_before_it_expires`, `::test_a_consumption_must_fall_inside_the_validity_window_and_pair_with_its_link`, `test_grant_audit_chain.py::test_the_authorization_must_carry_the_grant_actor_and_issuance_time` |
| **Total** | **92** | **92** | |

Per-trigger results: "Behavioral" is the number of behavioral test failures under that mutant, and the example is
the first of them in sort order.

| # | Trigger | Group | Result | Behavioral | Example killing test |
|---|---|---|---|---|---|
| 1 | `interactions_no_update` | G1 | killed | 1 | `tests/test_repository_integrity.py::test_rc33_append_only_tables_refuse_update_and_delete[interactions-UPDATE]` |
| 2 | `interactions_no_delete` | G1 | killed | 1 | `tests/test_repository_integrity.py::test_rc33_append_only_tables_refuse_update_and_delete[interactions-DELETE]` |
| 3 | `interaction_evidence_no_update` | G1 | killed | 1 | `tests/test_repository_integrity.py::test_rc33_append_only_tables_refuse_update_and_delete[interaction_evidence-UPDATE]` |
| 4 | `interaction_evidence_no_delete` | G1 | killed | 1 | `tests/test_repository_integrity.py::test_rc33_append_only_tables_refuse_update_and_delete[interaction_evidence-DELETE]` |
| 5 | `human_authorizations_no_update` | G1 | killed | 1 | `tests/test_repository_integrity.py::test_rc33_append_only_tables_refuse_update_and_delete[human_authorizations-UPDATE]` |
| 6 | `human_authorizations_no_delete` | G1 | killed | 1 | `tests/test_repository_integrity.py::test_rc33_append_only_tables_refuse_update_and_delete[human_authorizations-DELETE]` |
| 7 | `criterion_assessments_no_update` | G1 | killed | 1 | `tests/test_repository_integrity.py::test_rc33_append_only_tables_refuse_update_and_delete[criterion_assessments-UPDATE]` |
| 8 | `criterion_assessments_no_delete` | G1 | killed | 1 | `tests/test_repository_integrity.py::test_rc33_append_only_tables_refuse_update_and_delete[criterion_assessments-DELETE]` |
| 9 | `criterion_evidence_no_update` | G1 | killed | 1 | `tests/test_repository_integrity.py::test_rc33_append_only_tables_refuse_update_and_delete[criterion_evidence-UPDATE]` |
| 10 | `criterion_evidence_no_delete` | G1 | killed | 1 | `tests/test_repository_integrity.py::test_rc33_append_only_tables_refuse_update_and_delete[criterion_evidence-DELETE]` |
| 11 | `stringency_expressions_no_update` | G1 | killed | 1 | `tests/test_repository_integrity.py::test_rc33_append_only_tables_refuse_update_and_delete[stringency_expressions-UPDATE]` |
| 12 | `stringency_expressions_no_delete` | G1 | killed | 1 | `tests/test_repository_integrity.py::test_rc33_append_only_tables_refuse_update_and_delete[stringency_expressions-DELETE]` |
| 13 | `dormancy_judgments_no_update` | G1 | killed | 1 | `tests/test_repository_integrity.py::test_rc33_append_only_tables_refuse_update_and_delete[dormancy_judgments-UPDATE]` |
| 14 | `dormancy_judgments_no_delete` | G1 | killed | 1 | `tests/test_repository_integrity.py::test_rc33_append_only_tables_refuse_update_and_delete[dormancy_judgments-DELETE]` |
| 15 | `evaluation_evidence_no_update` | G1 | killed | 1 | `tests/test_repository_integrity.py::test_rc33_append_only_tables_refuse_update_and_delete[evaluation_evidence-UPDATE]` |
| 16 | `evaluation_evidence_no_delete` | G1 | killed | 1 | `tests/test_repository_integrity.py::test_rc33_append_only_tables_refuse_update_and_delete[evaluation_evidence-DELETE]` |
| 17 | `non_evaluation_evidence_no_update` | G1 | killed | 1 | `tests/test_repository_integrity.py::test_rc33_append_only_tables_refuse_update_and_delete[non_evaluation_evidence-UPDATE]` |
| 18 | `non_evaluation_evidence_no_delete` | G1 | killed | 1 | `tests/test_repository_integrity.py::test_rc33_append_only_tables_refuse_update_and_delete[non_evaluation_evidence-DELETE]` |
| 19 | `account_state_transitions_no_update` | G1 | killed | 1 | `tests/test_repository_integrity.py::test_rc33_append_only_tables_refuse_update_and_delete[account_state_transitions-UPDATE]` |
| 20 | `account_state_transitions_no_delete` | G1 | killed | 1 | `tests/test_repository_integrity.py::test_rc33_append_only_tables_refuse_update_and_delete[account_state_transitions-DELETE]` |
| 21 | `ai_runs_no_update` | G1 | killed | 2 | `tests/test_ai_failure_code_contract.py::test_the_validator_version_is_append_only_like_every_run_column` |
| 22 | `ai_runs_no_delete` | G1 | killed | 1 | `tests/test_ai_provenance_schema.py::test_ai_tables_refuse_update_and_delete[ai_runs]` |
| 23 | `ai_run_results_no_update` | G1 | killed | 1 | `tests/test_ai_provenance_schema.py::test_ai_tables_refuse_update_and_delete[ai_run_results]` |
| 24 | `ai_run_results_no_delete` | G1 | killed | 1 | `tests/test_ai_provenance_schema.py::test_ai_tables_refuse_update_and_delete[ai_run_results]` |
| 25 | `ai_run_outputs_no_update` | G1 | killed | 1 | `tests/test_ai_provenance_schema.py::test_ai_tables_refuse_update_and_delete[ai_run_outputs]` |
| 26 | `ai_run_outputs_no_delete` | G1 | killed | 1 | `tests/test_ai_provenance_schema.py::test_ai_tables_refuse_update_and_delete[ai_run_outputs]` |
| 27 | `ai_artifacts_no_update` | G1 | killed | 1 | `tests/test_ai_provenance_schema.py::test_ai_tables_refuse_update_and_delete[ai_artifacts]` |
| 28 | `ai_artifacts_no_delete` | G1 | killed | 1 | `tests/test_ai_provenance_schema.py::test_ai_tables_refuse_update_and_delete[ai_artifacts]` |
| 29 | `ai_artifact_excerpts_no_update` | G1 | killed | 1 | `tests/test_ai_provenance_schema.py::test_ai_tables_refuse_update_and_delete[ai_artifact_excerpts]` |
| 30 | `ai_artifact_excerpts_no_delete` | G1 | killed | 1 | `tests/test_ai_provenance_schema.py::test_ai_tables_refuse_update_and_delete[ai_artifact_excerpts]` |
| 31 | `baec_records_no_delete` | G1 | killed | 2 | `tests/test_rc33_schema_guards.py::test_rc33_baec_record_rows_cannot_be_deleted[fk-and-checks-off]` |
| 32 | `ai_proposals_no_update` | G1 | killed | 1 | `tests/test_proposal_bridge_schema.py::test_no_bridge_row_can_be_updated_deleted_or_replaced[ai_proposals]` |
| 33 | `ai_proposals_no_delete` | G1 | killed | 1 | `tests/test_proposal_bridge_schema.py::test_no_bridge_row_can_be_updated_deleted_or_replaced[ai_proposals]` |
| 34 | `ai_proposal_review_revisions_no_update` | G1 | killed | 1 | `tests/test_proposal_bridge_schema.py::test_no_bridge_row_can_be_updated_deleted_or_replaced[ai_proposal_review_revisions]` |
| 35 | `ai_proposal_review_revisions_no_delete` | G1 | killed | 1 | `tests/test_proposal_bridge_schema.py::test_no_bridge_row_can_be_updated_deleted_or_replaced[ai_proposal_review_revisions]` |
| 36 | `ai_proposal_review_decisions_no_update` | G1 | killed | 1 | `tests/test_proposal_bridge_schema.py::test_no_bridge_row_can_be_updated_deleted_or_replaced[ai_proposal_review_decisions]` |
| 37 | `ai_proposal_review_decisions_no_delete` | G1 | killed | 1 | `tests/test_proposal_bridge_schema.py::test_no_bridge_row_can_be_updated_deleted_or_replaced[ai_proposal_review_decisions]` |
| 38 | `human_authorization_grants_no_update` | G1 | killed | 1 | `tests/test_proposal_bridge_schema.py::test_no_bridge_row_can_be_updated_deleted_or_replaced[human_authorization_grants]` |
| 39 | `human_authorization_grants_no_delete` | G1 | killed | 1 | `tests/test_proposal_bridge_schema.py::test_no_bridge_row_can_be_updated_deleted_or_replaced[human_authorization_grants]` |
| 40 | `human_authorization_grant_supersessions_no_update` | G1 | killed | 1 | `tests/test_proposal_bridge_schema.py::test_no_bridge_row_can_be_updated_deleted_or_replaced[human_authorization_grant_supersessions]` |
| 41 | `human_authorization_grant_supersessions_no_delete` | G1 | killed | 1 | `tests/test_proposal_bridge_schema.py::test_no_bridge_row_can_be_updated_deleted_or_replaced[human_authorization_grant_supersessions]` |
| 42 | `ai_proposal_confirmations_no_update` | G1 | killed | 1 | `tests/test_proposal_bridge_schema.py::test_no_bridge_row_can_be_updated_deleted_or_replaced[ai_proposal_confirmations]` |
| 43 | `ai_proposal_confirmations_no_delete` | G1 | killed | 1 | `tests/test_proposal_bridge_schema.py::test_no_bridge_row_can_be_updated_deleted_or_replaced[ai_proposal_confirmations]` |
| 44 | `human_authorization_grant_consumptions_no_update` | G1 | killed | 1 | `tests/test_proposal_bridge_schema.py::test_no_bridge_row_can_be_updated_deleted_or_replaced[human_authorization_grant_consumptions]` |
| 45 | `human_authorization_grant_consumptions_no_delete` | G1 | killed | 1 | `tests/test_proposal_bridge_schema.py::test_no_bridge_row_can_be_updated_deleted_or_replaced[human_authorization_grant_consumptions]` |
| 46 | `interactions_no_replace` | G2 | killed | 7 | `tests/test_rc33_schema_guards.py::test_rc33_interaction_without_evidence_cannot_be_replaced` |
| 47 | `interaction_evidence_no_replace` | G2 | killed | 7 | `tests/test_rc33_schema_guards.py::test_rc33_replace_through_a_secondary_unique_key_is_refused[interaction_evidence]` |
| 48 | `human_authorizations_no_replace` | G2 | killed | 6 | `tests/test_rc33_schema_guards.py::test_rc33_stored_rows_cannot_be_replaced[human_authorizations-INSERT OR IGNORE-fk-and-checks-off]` |
| 49 | `baec_records_no_replace` | G2 | killed | 7 | `tests/test_rc33_schema_guards.py::test_rc33_replace_through_a_secondary_unique_key_is_refused[baec_records]` |
| 50 | `criterion_assessments_no_replace` | G2 | killed | 7 | `tests/test_rc33_schema_guards.py::test_rc33_replace_through_a_secondary_unique_key_is_refused[criterion_assessments]` |
| 51 | `criterion_evidence_no_replace` | G2 | killed | 6 | `tests/test_rc33_schema_guards.py::test_rc33_stored_rows_cannot_be_replaced[criterion_evidence-INSERT OR IGNORE-fk-and-checks-off]` |
| 52 | `stringency_expressions_no_replace` | G2 | killed | 6 | `tests/test_rc33_schema_guards.py::test_rc33_stored_rows_cannot_be_replaced[stringency_expressions-INSERT OR IGNORE-fk-and-checks-off]` |
| 53 | `dormancy_judgments_no_replace` | G2 | killed | 7 | `tests/test_rc33_schema_guards.py::test_rc33_replace_through_a_secondary_unique_key_is_refused[dormancy_judgments]` |
| 54 | `evaluation_evidence_no_replace` | G2 | killed | 6 | `tests/test_rc33_schema_guards.py::test_rc33_stored_rows_cannot_be_replaced[evaluation_evidence-INSERT OR IGNORE-fk-and-checks-off]` |
| 55 | `non_evaluation_evidence_no_replace` | G2 | killed | 6 | `tests/test_rc33_schema_guards.py::test_rc33_stored_rows_cannot_be_replaced[non_evaluation_evidence-INSERT OR IGNORE-fk-and-checks-off]` |
| 56 | `account_state_transitions_no_replace` | G2 | killed | 7 | `tests/test_rc33_schema_guards.py::test_rc33_replace_through_a_secondary_unique_key_is_refused[account_state_transitions]` |
| 57 | `ai_runs_no_replace` | G2 | killed | 3 | `tests/test_ai_provenance_schema.py::test_ai_rows_cannot_be_replaced_or_silently_skipped[ai_runs-INSERT OR IGNORE]` |
| 58 | `ai_run_results_no_replace` | G2 | killed | 3 | `tests/test_ai_provenance_schema.py::test_ai_rows_cannot_be_replaced_or_silently_skipped[ai_run_results-INSERT OR IGNORE]` |
| 59 | `ai_run_outputs_no_replace` | G2 | killed | 3 | `tests/test_ai_provenance_schema.py::test_ai_rows_cannot_be_replaced_or_silently_skipped[ai_run_outputs-INSERT OR IGNORE]` |
| 60 | `ai_artifacts_no_replace` | G2 | killed | 3 | `tests/test_ai_provenance_schema.py::test_ai_rows_cannot_be_replaced_or_silently_skipped[ai_artifacts-INSERT OR IGNORE]` |
| 61 | `ai_artifact_excerpts_no_replace` | G2 | killed | 3 | `tests/test_ai_provenance_schema.py::test_ai_rows_cannot_be_replaced_or_silently_skipped[ai_artifact_excerpts-INSERT OR IGNORE]` |
| 62 | `ai_proposals_no_replace` | G2 | killed | 1 | `tests/test_proposal_bridge_schema.py::test_no_bridge_row_can_be_updated_deleted_or_replaced[ai_proposals]` |
| 63 | `ai_proposal_review_revisions_no_replace` | G2 | killed | 1 | `tests/test_proposal_bridge_schema.py::test_no_bridge_row_can_be_updated_deleted_or_replaced[ai_proposal_review_revisions]` |
| 64 | `ai_proposal_review_decisions_no_replace` | G2 | killed | 2 | `tests/test_proposal_bridge_schema.py::test_a_rejection_is_terminal_and_happens_once` |
| 65 | `human_authorization_grants_no_replace` | G2 | killed | 1 | `tests/test_proposal_bridge_schema.py::test_no_bridge_row_can_be_updated_deleted_or_replaced[human_authorization_grants]` |
| 66 | `human_authorization_grant_supersessions_no_replace` | G2 | killed | 1 | `tests/test_proposal_bridge_schema.py::test_no_bridge_row_can_be_updated_deleted_or_replaced[human_authorization_grant_supersessions]` |
| 67 | `ai_proposal_confirmations_no_replace` | G2 | killed | 2 | `tests/test_proposal_bridge_schema.py::test_no_bridge_row_can_be_updated_deleted_or_replaced[ai_proposal_confirmations]` |
| 68 | `human_authorization_grant_consumptions_no_replace` | G2 | killed | 2 | `tests/test_proposal_bridge_schema.py::test_a_full_lineage_links_one_baec_and_one_consumption` |
| 69 | `baec_records_protected_no_update` | G3 | killed | 64 | `tests/test_rc33_schema_guards.py::test_rc33_raw_update_of_a_protected_baec_record_column_is_refused[account_id-changed-fk-and-checks-off]` |
| 70 | `interaction_evidence_verbatim` | G4 | killed | 7 | `tests/test_evidence_fidelity.py::test_raw_sql_evidence_for_a_missing_interaction_is_refused_even_with_foreign_keys_off` |
| 71 | `ai_artifact_excerpts_verbatim` | G4 | killed | 7 | `tests/test_ai_provenance_atomicity.py::test_a_refused_success_bundle_is_never_partially_successful` |
| 72 | `ai_run_results_model_binding` | G5 | killed | 1 | `tests/test_ai_provenance_schema.py::test_success_and_model_mismatch_are_bound_to_the_requested_model` |
| 73 | `ai_run_results_failure_codes_closed` | G5 | killed | 46 | `tests/test_ai_failure_code_contract.py::test_sql_refuses_a_semantic_code_under_a_validator_version_that_cannot_emit_it[baec-extraction-validation/v9]` |
| 74 | `ai_run_outputs_require_result` | G5 | killed | 1 | `tests/test_ai_provenance_schema.py::test_output_requires_a_result_naming_the_same_digest` |
| 75 | `ai_artifacts_require_success` | G5 | killed | 9 | `tests/test_ai_provenance_schema.py::test_an_artifact_for_a_non_success_result_is_refused[api_error]` |
| 76 | `ai_proposals_artifact_binding` | G6 | killed | 10 | `tests/test_proposal_bridge_schema.py::test_a_proposal_binds_to_an_existing_eligible_artifact_of_its_own_account_and_interaction` |
| 77 | `ai_proposal_review_revisions_sequence` | G6 | killed | 8 | `tests/test_proposal_bridge_schema.py::test_no_bridge_row_can_be_updated_deleted_or_replaced[ai_proposal_confirmations]` |
| 78 | `ai_proposal_review_revisions_open` | G6 | killed | 9 | `tests/test_proposal_bridge_schema.py::test_a_consumed_grant_cannot_be_superseded_or_succeeded` |
| 79 | `ai_proposal_review_decisions_binding` | G6 | killed | 8 | `tests/test_proposal_bridge_schema.py::test_decisions_are_a_closed_vocabulary_bound_to_their_proposal` |
| 80 | `ai_proposal_review_decisions_latest` | G6 | killed | 8 | `tests/test_proposal_bridge_schema.py::test_no_bridge_row_can_be_updated_deleted_or_replaced[ai_proposal_confirmations]` |
| 81 | `ai_proposal_review_decisions_terminal` | G6 | killed | 10 | `tests/test_proposal_bridge_schema.py::test_a_consumed_grant_cannot_be_superseded_or_succeeded` |
| 82 | `ai_proposal_review_revisions_supersede_grants` | G7 | killed | 7 | `tests/test_mcp_write_tool.py::test_a_superseded_unexpired_grant_is_refused_through_mcp` |
| 83 | `ai_proposal_review_decisions_reject_supersedes` | G7 | killed | 11 | `tests/test_phase7_adversarial.py::test_lifecycle_rejected_proposal_cannot_be_authorized_and_its_grant_is_superseded` |
| 84 | `human_authorization_grants_accepted` | G7 | killed | 9 | `tests/test_proposal_bridge_schema.py::test_a_grant_requires_an_accepted_latest_revision` |
| 85 | `human_authorization_grants_proposal_digest` | G7 | killed | 8 | `tests/test_proposal_bridge_schema.py::test_a_grant_is_bound_to_its_account_lineage_and_reviewed_content[proposal digest]` |
| 86 | `human_authorization_grants_one_unretired` | G7 | killed | 8 | `tests/test_proposal_bridge_schema.py::test_at_most_one_unretired_grant_per_revision_and_action` |
| 87 | `human_authorization_grant_supersessions_binding` | G7 | killed | 8 | `tests/test_proposal_bridge_schema.py::test_invalid_supersessions_are_refused` |
| 88 | `ai_proposal_confirmations_authorization_binding` | G8 | killed | 10 | `tests/test_grant_audit_chain.py::test_the_authorization_must_carry_the_grant_actor_and_issuance_time` |
| 89 | `ai_proposal_confirmations_unretired` | G8 | killed | 8 | `tests/test_proposal_bridge_schema.py::test_a_superseded_grant_cannot_be_linked_even_before_it_expires` |
| 90 | `ai_proposal_confirmations_classification` | G8 | killed | 7 | `tests/test_proposal_bridge_schema.py::test_no_bridge_row_can_be_updated_deleted_or_replaced[ai_proposal_confirmations]` |
| 91 | `human_authorization_grant_consumptions_window` | G8 | killed | 9 | `tests/test_proposal_bridge_schema.py::test_a_consumption_must_fall_inside_the_validity_window_and_pair_with_its_link[consumed_at0]` |
| 92 | `human_authorization_grant_consumptions_pairing` | G8 | killed | 7 | `tests/test_proposal_bridge_schema.py::test_no_bridge_row_can_be_updated_deleted_or_replaced[ai_proposal_confirmations]` |

### 11.3 Clause-level mutants (deeper than the brief)

Dropping a whole trigger can hide an untested *clause*, because one test that trips any clause kills the drop. So
56 clause mutants were also run. Each `BAEC_E1_REWRITE` replaces one conjunct or disjunct of a multi-condition
trigger with a tautology, or removes one guarded key or protected column. The targets were every multi-clause G5–G8
trigger, the G4 blank checks, two G3 columns, four G2 secondary keys, and the two G7 writers. They were first run
against the 21 files that killed single-trigger mutants, and the survivors were then re-run against all 54 files.

* **Killed by existing tests: 33.** Examples: `conf_auth_actor` and `conf_auth_time` by
  `test_grant_audit_chain.py::test_the_authorization_must_carry_the_grant_actor_and_issuance_time`;
  `window_lower`/`window_upper` by `test_a_consumption_must_fall_inside_the_validity_window_…`; `failure_codes_sorted`
  by `test_ai_failure_code_contract.py::test_sql_refuses_every_illegal_code_set_in_a_raw_insert[semantic duplicate]`;
  `supersede_after_revision_scope` by `test_mcp_write_tool.py::test_a_superseded_unexpired_grant_is_refused_through_mcp`.
* **Survived the existing suite: 23.** Re-run against all 54 files (4,312 cases), none was killed by a behavioral test. Three runs showed one extra failure each (`test_public_demo.py`, `test_review_page_authorization.py`), but those are Streamlit AppTest tests under parallel load that do not touch the mutated trigger, and they pass when re-run serially under the same mutant. In every case, another clause of the same trigger, or another
  guard, refused the existing test's row first. That is fine for SQLite today, but a port that dropped the clause
  would go unnoticed.
* **New tests.** `tests/test_engine1_trigger_mutation_coverage.py` has 21 test cases (19 functions). Each builds a row
  that violates exactly one clause and checks the trigger's own message. Some states are only reachable through
  corruption, and there the row is built with `persistence_builders.tamper` or `PRAGMA ignore_check_constraints`.
  Verified both ways: **21 passed on the unmodified code**, and under each of the 21 targeted mutants **exactly
  one** of them fails.

| Clause mutant (survived the existing suite) | New killing test |
|---|---|
| `conf_auth_authorization_id` | `test_a_link_must_name_the_baecs_own_confirmation_authorization` |
| `conf_auth_action`, `conf_auth_subject`, `conf_auth_target_state` | `test_a_link_requires_a_confirm_baec_authorization_for_this_baec_with_no_target_state[action\|subject\|target state]` |
| `conf_unretired_latest` | `test_a_link_requires_the_grants_revision_to_be_the_latest` |
| `conf_unretired_rejected` | `test_a_link_requires_a_proposal_that_was_never_rejected` |
| `conf_unretired_consumed` | `test_a_link_requires_a_grant_that_was_never_consumed` |
| `conf_unretired_superseded` | `test_a_link_requires_a_grant_with_no_supersession_record` |
| `conf_unretired_successor` | `test_a_link_cannot_use_a_grant_that_a_reissue_replaced` |
| `conf_classification_current` | `test_a_link_requires_the_baec_to_still_be_current` |
| `conf_classification_confirmed` | `test_a_link_requires_a_confirmed_classification` |
| `grant_accepted_rejected` | `test_no_grant_is_issued_after_the_proposal_was_rejected_even_for_an_accepted_latest_revision` |
| `grant_one_unretired_predecessor_revision` | `test_a_reissue_must_name_a_predecessor_on_its_own_revision` |
| `supersession_same_proposal` | `test_a_supersession_needs_a_later_revision_of_the_grants_own_proposal` |
| `supersession_unretired_consumed` | `test_a_consumed_grant_cannot_be_superseded_even_by_a_later_revision` |
| `decision_binding_account` | `test_a_decision_must_carry_its_proposals_account` |
| `decision_binding_revision` | `test_a_rejection_cannot_name_another_proposals_revision` |
| `replace_decision_one_rejection` | `test_the_replace_guard_alone_keeps_a_second_rejection_from_replacing_the_first` |
| `artifact_binding_status` | `test_a_proposal_cannot_snapshot_an_artifact_whose_run_result_is_not_success` |
| `failure_codes_other_null` | `test_failure_codes_outside_parse_and_semantic_statuses_are_refused_by_the_trigger_alone` |
| `artifacts_output_digest` | `test_an_artifact_requires_the_stored_output_to_carry_the_results_digest` |

**Two equivalent mutants (no test can kill them; documented, not covered):**

* `grant_accepted_decision` drops `decision = 'ACCEPTED'` from `human_authorization_grants_accepted`. The decision's
  `(decision_id, review_revision_id, proposal_id)` must still match, and `decision` is the closed vocabulary
  `ACCEPTED|REJECTED`. A non-accepted decision is therefore a REJECTED decision of `NEW.proposal_id`, and the same
  trigger's "proposal was rejected" disjunct refuses it with the same message.
* `failure_codes_text_type` drops `code.type <> 'text'` from `ai_run_results_failure_codes_closed`. A non-string JSON
  element can never equal a vocabulary token, so the vocabulary test refuses it anyway.

The PostgreSQL port should still carry both clauses, since they are cheap and keep the logic legible. The
conformance suite cannot distinguish them.

---

## 12. Readiness

### 12.1 Verdict

**Engine 1 is not ready to be ported yet.** It is ready for a separate, controlled port **phase** to be approved,
once the preconditions below are met. The reasons:

* The invariant design has no open hole. Every trigger is in a group with a PostgreSQL mechanism (§4–§5). Every
  trigger has a behavioral test that fails when it is removed (§11.2). Every non-equivalent clause has one too,
  after the new test file (§11.3).
* The Engine 1 data layer is still bound to `sqlite3` types (§9). Porting needs a small, explicit set of source
  changes that CLAUDE.md requires to be explained and approved first. They are deliberately **not** made here.
* A PostgreSQL conformance harness for Engine 1 does not exist yet. Equivalence with SQLite is therefore only argued,
  not tested.

### 12.2 Preconditions (each is a separate, reviewable change)

1. **Connection protocol.** Replace the four `type(connection) is not sqlite3.Connection` checks with an explicit
   protocol that admits `sqlite3.Connection` and the PostgreSQL Engine 1 connection, and nothing else. Keep the
   foreign-key and schema-version checks as protocol methods.
2. **Backend-neutral refusal.** The five `except sqlite3.IntegrityError` sites catch one `StorageRefusal`
   (message = the trigger or constraint's primary message). Both backends raise it. The messages wrapped into
   `RepositoryConflictError` stay byte-identical.
3. **`RETURNING` in place of `lastrowid`** for the six database-assigned IDs. `IS ?` becomes an explicit NULL-safe
   predicate. A read-only primitive replaces `PRAGMA query_only`, and it fails closed if read-only cannot be
   confirmed.
4. **Owner decisions:**
   * whether Engine 1 accounts get an FK to `baec.account_ownership` (§6.3);
   * whether `UPDATE (staleness_status)` is granted before a staleness phase exists (§5 G3: proposed no);
   * synthetic-workspace reset on append-only tables (Phase 3 §12.2);
   * timestamps kept as text, which is proposed for parity (§5 G8).
5. **Mutation coverage closed.** Done in this change. The 21 non-equivalent clause survivors of §11.3 are killed by
   `tests/test_engine1_trigger_mutation_coverage.py`, which was verified against the unmodified code and under each
   mutant. The two equivalent mutants are argued in §11.3.

### 12.3 Proposed port order

1. Migration `000N_engine1_schema.sql`: tables, keys, CHECKs, tenancy columns, `seq`. No triggers yet. The
   dual-backend harness compares `information_schema` against the SQLite inventory (§3) mechanically.
2. G1, G2, G3: append-only, no-replace, protected columns, TRUNCATE, privileges. These are the generic guards. Run
   the ported `test_rc33_schema_guards.py`, `test_repository_integrity.py` (append-only cases), and
   `test_ai_provenance_schema.py` (G1/G2 cases) on both backends.
3. G4: verbatim evidence, with `test_evidence_fidelity.py`.
4. G5: AI provenance, with `test_ai_failure_code_contract.py`, `test_ai_provenance_schema.py`, and
   `test_ai_provenance_store.py`.
5. §6.1 write lock, then G6, G7, G8 (the bridge) with `test_proposal_bridge_schema.py`,
   `test_proposal_bridge_store.py`, `test_proposal_authorization.py`, and `test_grant_audit_chain.py`. Add new
   concurrency tests: two sessions racing revision n+1, accept-vs-reject, issue-vs-reissue, and two executions of one
   grant. Each must end in exactly one success and one class-23 refusal.
6. Repository, provenance, bridge, and grant stores over the protocol (precondition 1–3); then the application and
   MCP compositions; then `engine1_adapter`/ConsoleService routing.
7. Importer: SQLite `engine1.sqlite3` → `baec_engine1`. It preserves IDs, text timestamps, and buyer text byte for
   byte. It refuses any conflict. It verifies by re-reading through both stores (as Phase 3 §11).

### 12.4 Conformance strategy (mirrors the Engine 2 approach)

* **One scenario suite, two backends.** Parametrize the Engine 1 storage tests over a `backend` fixture (`sqlite`,
  `postgres`). On PostgreSQL each test runs as `baec_app` inside a tenant transaction for a synthetic organization
  and workspace. Assertions compare *outcomes*: rows, returned domain objects, exception class (`StorageRefusal`,
  `RepositoryConflictError`, …), and the **message regex**.
* **Trace comparison.** Run the same scripted sequences (seed → classify → confirm → judge → transition; AI run →
  artifact → proposal → revisions → accept → grant → confirm/consume; and every refusal path) on both backends.
  Compare normalized dumps: same rows, same IDs (§8), and the same refusal message at the same step.
* **Mutation on both backends.** Reuse §11's method. On PostgreSQL, the mutant drops one guard *block* by
  regenerating the guard function without it. Every mutant must be killed on both backends.
* **Sabotage tests.** `tamper()` (drop triggers, FKs off, CHECKs off) runs as the bootstrap superuser in a scratch
  schema. `session_replication_role = replica` disables triggers and FKs together, so tests that need "FKs off but
  triggers on" must drop the FK explicitly.
* **Cross-tenant attacks.** Reuse `test_rls.py`'s pattern (A against B and B against A, as `baec_app` and as
  `probe`) for every Engine 1 table, including the guard functions' cross-row reads. A guard must treat another
  tenant's row as nonexistent.

### 12.5 What cannot be reproduced exactly (documented divergences)

| Behavior | SQLite | PostgreSQL | Why acceptable / mitigation |
|---|---|---|---|
| TTL and validity-window precision | `julianday` resolves to 1 ms (`.000499` = `.000000`), so a consumption less than about 0.5 ms before `expires_at` can compare equal and be refused; the TTL CHECK tolerates 1 ms | exact to 1 µs | legal application rows (exact 900 s, µs datetimes) behave identically; only sub-millisecond edge rows differ (PostgreSQL is stricter on the TTL, and exact on the window) |
| Duplicate insert under a true race | always the trigger message | the trigger message, or the UNIQUE index message if two writers bypass the write lock | same exception class; Engine 1 code paths always hold the lock |
| U+0000 in text | storable | refused (`DataError`) | synthetic data has none; refuse earlier in the repository |
| Free-text UNIQUE keys | any length | btree entry limit about 2.7 kB, so a digest index is used | the guard compares full text; the digest index is only the backstop |
| STRICT type errors | `IntegrityError` | `DataError` | the repository refuses wrong types first; one test's expected class changes |
| "FKs off, triggers on" sabotage | `PRAGMA foreign_keys = OFF` | no equivalent for a non-superuser | drop the FK in a scratch schema |
| Trigger order guarantee | observed (reverse creation), undocumented | explicit (one consolidated guard function) | PostgreSQL is *more* deterministic; pin the SQLite order in tests |
| Reader blocking | rollback journal can return `SQLITE_BUSY` to readers | MVCC readers never block | no invariant depends on reader blocking |

### 12.6 Stop conditions (halt the port and report)

* Any trigger, CHECK, or FK of §3–§4 has no PostgreSQL counterpart, or a mutant of it survives on either backend.
* Any refusal changes exception class or message text, except the documented rows of §12.5.
* Any Engine 1 rule would have to change to make PostgreSQL work. That covers theory rules (RC-xx), the three
  account states, NULL for unclassified, the signal rule, and the human-confirmation paths. Stop and ask.
* A concurrency test shows two successes where SQLite allows one.
* A cross-tenant read or write succeeds, or a guard function reveals another tenant's row through a different error.
* The port would need `baec_app` to hold UPDATE/DELETE/TRUNCATE, `BYPASSRLS`, ownership, or `SECURITY DEFINER`
  functions that bypass RLS.
* The importer would have to rewrite stored buyer text, thresholds, timestamps, or IDs.

---

## 13. Results of this change

| What | Result |
|---|---|
| Files created | `docs/V04_ENGINE1_POSTGRES_MIGRATION_PLAN.md` (this design), `tests/test_engine1_trigger_mutation_coverage.py` (21 test cases). No other file was changed. Nothing was ported to PostgreSQL. |
| New test file on unmodified code | 21 passed |
| New tests under their target clause mutants | each of the 21 targeted mutants: exactly 1 of the 21 fails |
| Single-trigger mutants (92) | 92 killed by behavioral tests (§11.2) |
| Clause-level mutants (56) | 33 killed by existing tests, 21 by the new file, 2 equivalent (§11.3) |
| Engine 1 suite, serial: the 54 Engine 1 storage files + `tests/test_state_machine.py` + the new file | **4,683 passed** |

These are software tests of the storage layer, run against synthetic data. They are not evidence for the BAEC theory,
not a measure of sales or purchase outcomes, and not a production security assessment.

---

## 14. Phase 3C implementation checklist (generated from the test catalogue)

Every SQLite trigger, its one PostgreSQL trigger and function (migration 0004), its conformance cases in
`baec_console/tests_postgres/test_engine1_parity.py` (identical outcome required on SQLite and PostgreSQL), and its
clause-level mutants in the same file. Every trigger is also dropped on its own on both backends
(`test_dropping_any_trigger_changes_an_outcome_on_both_backends`): 92 of 92 killed on each. A dash means the trigger has
a single clause, so the per-trigger mutant is its clause mutant.

| # | Group | SQLite trigger (table) | PostgreSQL trigger → function | Conformance cases (both backends) | Clause mutants |
|---|---|---|---|---|---|
| 1 | G1 | `interactions_no_update` (`interactions`) | `interactions_no_update` → `refuse_change` | 1 (e.g. G1 update interactions) | — |
| 2 | G1 | `interactions_no_delete` (`interactions`) | `interactions_no_delete` → `refuse_change` | 1 (e.g. G1 delete interactions) | — |
| 3 | G1 | `interaction_evidence_no_update` (`interaction_evidence`) | `interaction_evidence_no_update` → `refuse_change` | 1 (e.g. G1 update interaction_evidence) | — |
| 4 | G1 | `interaction_evidence_no_delete` (`interaction_evidence`) | `interaction_evidence_no_delete` → `refuse_change` | 1 (e.g. G1 delete interaction_evidence) | — |
| 5 | G1 | `human_authorizations_no_update` (`human_authorizations`) | `human_authorizations_no_update` → `refuse_change` | 1 (e.g. G1 update human_authorizations) | — |
| 6 | G1 | `human_authorizations_no_delete` (`human_authorizations`) | `human_authorizations_no_delete` → `refuse_change` | 1 (e.g. G1 delete human_authorizations) | — |
| 7 | G1 | `criterion_assessments_no_update` (`criterion_assessments`) | `criterion_assessments_no_update` → `refuse_change` | 1 (e.g. G1 update criterion_assessments) | — |
| 8 | G1 | `criterion_assessments_no_delete` (`criterion_assessments`) | `criterion_assessments_no_delete` → `refuse_change` | 1 (e.g. G1 delete criterion_assessments) | — |
| 9 | G1 | `criterion_evidence_no_update` (`criterion_evidence`) | `criterion_evidence_no_update` → `refuse_change` | 1 (e.g. G1 update criterion_evidence) | — |
| 10 | G1 | `criterion_evidence_no_delete` (`criterion_evidence`) | `criterion_evidence_no_delete` → `refuse_change` | 1 (e.g. G1 delete criterion_evidence) | — |
| 11 | G1 | `stringency_expressions_no_update` (`stringency_expressions`) | `stringency_expressions_no_update` → `refuse_change` | 1 (e.g. G1 update stringency_expressions) | — |
| 12 | G1 | `stringency_expressions_no_delete` (`stringency_expressions`) | `stringency_expressions_no_delete` → `refuse_change` | 1 (e.g. G1 delete stringency_expressions) | — |
| 13 | G1 | `dormancy_judgments_no_update` (`dormancy_judgments`) | `dormancy_judgments_no_update` → `refuse_change` | 1 (e.g. G1 update dormancy_judgments) | — |
| 14 | G1 | `dormancy_judgments_no_delete` (`dormancy_judgments`) | `dormancy_judgments_no_delete` → `refuse_change` | 1 (e.g. G1 delete dormancy_judgments) | — |
| 15 | G1 | `evaluation_evidence_no_update` (`evaluation_evidence`) | `evaluation_evidence_no_update` → `refuse_change` | 1 (e.g. G1 update evaluation_evidence) | — |
| 16 | G1 | `evaluation_evidence_no_delete` (`evaluation_evidence`) | `evaluation_evidence_no_delete` → `refuse_change` | 1 (e.g. G1 delete evaluation_evidence) | — |
| 17 | G1 | `non_evaluation_evidence_no_update` (`non_evaluation_evidence`) | `non_evaluation_evidence_no_update` → `refuse_change` | 1 (e.g. G1 update non_evaluation_evidence) | — |
| 18 | G1 | `non_evaluation_evidence_no_delete` (`non_evaluation_evidence`) | `non_evaluation_evidence_no_delete` → `refuse_change` | 1 (e.g. G1 delete non_evaluation_evidence) | — |
| 19 | G1 | `account_state_transitions_no_update` (`account_state_transitions`) | `account_state_transitions_no_update` → `refuse_change` | 1 (e.g. G1 update account_state_transitions) | — |
| 20 | G1 | `account_state_transitions_no_delete` (`account_state_transitions`) | `account_state_transitions_no_delete` → `refuse_change` | 1 (e.g. G1 delete account_state_transitions) | — |
| 21 | G1 | `ai_runs_no_update` (`ai_runs`) | `ai_runs_no_update` → `refuse_change` | 1 (e.g. G1 update ai_runs) | — |
| 22 | G1 | `ai_runs_no_delete` (`ai_runs`) | `ai_runs_no_delete` → `refuse_change` | 1 (e.g. G1 delete ai_runs) | — |
| 23 | G1 | `ai_run_results_no_update` (`ai_run_results`) | `ai_run_results_no_update` → `refuse_change` | 1 (e.g. G1 update ai_run_results) | — |
| 24 | G1 | `ai_run_results_no_delete` (`ai_run_results`) | `ai_run_results_no_delete` → `refuse_change` | 1 (e.g. G1 delete ai_run_results) | — |
| 25 | G1 | `ai_run_outputs_no_update` (`ai_run_outputs`) | `ai_run_outputs_no_update` → `refuse_change` | 1 (e.g. G1 update ai_run_outputs) | — |
| 26 | G1 | `ai_run_outputs_no_delete` (`ai_run_outputs`) | `ai_run_outputs_no_delete` → `refuse_change` | 1 (e.g. G1 delete ai_run_outputs) | — |
| 27 | G1 | `ai_artifacts_no_update` (`ai_artifacts`) | `ai_artifacts_no_update` → `refuse_change` | 1 (e.g. G1 update ai_artifacts) | — |
| 28 | G1 | `ai_artifacts_no_delete` (`ai_artifacts`) | `ai_artifacts_no_delete` → `refuse_change` | 1 (e.g. G1 delete ai_artifacts) | — |
| 29 | G1 | `ai_artifact_excerpts_no_update` (`ai_artifact_excerpts`) | `ai_artifact_excerpts_no_update` → `refuse_change` | 1 (e.g. G1 update ai_artifact_excerpts) | — |
| 30 | G1 | `ai_artifact_excerpts_no_delete` (`ai_artifact_excerpts`) | `ai_artifact_excerpts_no_delete` → `refuse_change` | 1 (e.g. G1 delete ai_artifact_excerpts) | — |
| 31 | G2 | `interactions_no_replace` (`interactions`) | `f1_interactions_no_replace` → `refuse_replace` | 3 (e.g. G2 interactions collides on (interaction_id)) | — |
| 32 | G2 | `interaction_evidence_no_replace` (`interaction_evidence`) | `f2_interaction_evidence_no_replace` → `refuse_replace` | 5 (e.g. G2 interaction_evidence collides on (evidence_id)) | 2 |
| 33 | G2 | `human_authorizations_no_replace` (`human_authorizations`) | `f1_human_authorizations_no_replace` → `refuse_replace` | 3 (e.g. G2 human_authorizations collides on (authorization_id)) | — |
| 34 | G2 | `baec_records_no_replace` (`baec_records`) | `f1_baec_records_no_replace` → `refuse_replace` | 4 (e.g. G2 baec_records collides on (baec_id)) | 2 |
| 35 | G2 | `criterion_assessments_no_replace` (`criterion_assessments`) | `f1_criterion_assessments_no_replace` → `refuse_replace` | 4 (e.g. G2 criterion_assessments collides on (baec_id, criterion)) | 2 |
| 36 | G2 | `criterion_evidence_no_replace` (`criterion_evidence`) | `f1_criterion_evidence_no_replace` → `refuse_replace` | 3 (e.g. G2 criterion_evidence collides on (baec_id, criterion, position)) | — |
| 37 | G2 | `stringency_expressions_no_replace` (`stringency_expressions`) | `f1_stringency_expressions_no_replace` → `refuse_replace` | 3 (e.g. G2 stringency_expressions collides on (baec_id)) | — |
| 38 | G2 | `dormancy_judgments_no_replace` (`dormancy_judgments`) | `f1_dormancy_judgments_no_replace` → `refuse_replace` | 4 (e.g. G2 dormancy_judgments collides on (judgment_id)) | 2 |
| 39 | G2 | `evaluation_evidence_no_replace` (`evaluation_evidence`) | `f1_evaluation_evidence_no_replace` → `refuse_replace` | 3 (e.g. G2 evaluation_evidence collides on (evaluation_evidence_id)) | — |
| 40 | G2 | `non_evaluation_evidence_no_replace` (`non_evaluation_evidence`) | `f1_non_evaluation_evidence_no_replace` → `refuse_replace` | 3 (e.g. G2 non_evaluation_evidence collides on (non_evaluation_evidence_id)) | — |
| 41 | G2 | `account_state_transitions_no_replace` (`account_state_transitions`) | `f1_account_state_transitions_no_replace` → `refuse_replace` | 4 (e.g. G2 account_state_transitions collides on (transition_id)) | 2 |
| 42 | G2 | `ai_runs_no_replace` (`ai_runs`) | `f1_ai_runs_no_replace` → `refuse_replace` | 3 (e.g. G2 ai_runs collides on (ai_run_id)) | — |
| 43 | G2 | `ai_run_results_no_replace` (`ai_run_results`) | `f3_ai_run_results_no_replace` → `refuse_replace` | 3 (e.g. G2 ai_run_results collides on (ai_run_id)) | — |
| 44 | G2 | `ai_run_outputs_no_replace` (`ai_run_outputs`) | `f2_ai_run_outputs_no_replace` → `refuse_replace` | 3 (e.g. G2 ai_run_outputs collides on (ai_run_id)) | — |
| 45 | G2 | `ai_artifacts_no_replace` (`ai_artifacts`) | `f2_ai_artifacts_no_replace` → `refuse_replace` | 4 (e.g. G2 ai_artifacts collides on (artifact_id)) | 2 |
| 46 | G2 | `ai_artifact_excerpts_no_replace` (`ai_artifact_excerpts`) | `f2_ai_artifact_excerpts_no_replace` → `refuse_replace` | 5 (e.g. G2 ai_artifact_excerpts collides on (artifact_id, excerpt_id)) | 2 |
| 47 | G4 | `interaction_evidence_verbatim` (`interaction_evidence`) | `f1_interaction_evidence_verbatim` → `interaction_evidence_verbatim` | 6 (e.g. G4 evidence: blank) | 2 |
| 48 | G3 | `baec_records_protected_no_update` (`baec_records`) | `baec_records_protected_no_update` → `baec_records_protected_no_update` | 16 (e.g. G3 update baec_id) | 3 |
| 49 | G1 | `baec_records_no_delete` (`baec_records`) | `baec_records_no_delete` → `baec_records_no_delete` | 1 (e.g. G1 delete baec_records) | — |
| 50 | G5 | `ai_run_results_model_binding` (`ai_run_results`) | `f2_ai_run_results_model_binding` → `ai_run_results_model_binding` | 4 (e.g. G5 success must name the requested model) | 2 |
| 51 | G5 | `ai_run_results_failure_codes_closed` (`ai_run_results`) | `f1_ai_run_results_failure_codes_closed` → `ai_run_results_failure_codes_closed` | 27 (e.g. G5 codes on a status without codes) | 6 |
| 52 | G5 | `ai_run_outputs_require_result` (`ai_run_outputs`) | `f1_ai_run_outputs_require_result` → `ai_run_outputs_require_result` | 3 (e.g. G5 output without a result) | 1 |
| 53 | G5 | `ai_artifacts_require_success` (`ai_artifacts`) | `f1_ai_artifacts_require_success` → `ai_artifacts_require_success` | 4 (e.g. G5 artifact without the raw output) | 2 |
| 54 | G4 | `ai_artifact_excerpts_verbatim` (`ai_artifact_excerpts`) | `f1_ai_artifact_excerpts_verbatim` → `ai_artifact_excerpts_verbatim` | 14 (e.g. G4 excerpt: blank) | 1 |
| 55 | G1 | `ai_proposals_no_update` (`ai_proposals`) | `ai_proposals_no_update` → `refuse_change` | 1 (e.g. G1 update ai_proposals) | — |
| 56 | G1 | `ai_proposals_no_delete` (`ai_proposals`) | `ai_proposals_no_delete` → `refuse_change` | 1 (e.g. G1 delete ai_proposals) | — |
| 57 | G1 | `ai_proposal_review_revisions_no_update` (`ai_proposal_review_revisions`) | `ai_proposal_review_revisions_no_update` → `refuse_change` | 1 (e.g. G1 update ai_proposal_review_revisions) | — |
| 58 | G1 | `ai_proposal_review_revisions_no_delete` (`ai_proposal_review_revisions`) | `ai_proposal_review_revisions_no_delete` → `refuse_change` | 1 (e.g. G1 delete ai_proposal_review_revisions) | — |
| 59 | G1 | `ai_proposal_review_decisions_no_update` (`ai_proposal_review_decisions`) | `ai_proposal_review_decisions_no_update` → `refuse_change` | 1 (e.g. G1 update ai_proposal_review_decisions) | — |
| 60 | G1 | `ai_proposal_review_decisions_no_delete` (`ai_proposal_review_decisions`) | `ai_proposal_review_decisions_no_delete` → `refuse_change` | 1 (e.g. G1 delete ai_proposal_review_decisions) | — |
| 61 | G1 | `human_authorization_grants_no_update` (`human_authorization_grants`) | `human_authorization_grants_no_update` → `refuse_change` | 1 (e.g. G1 update human_authorization_grants) | — |
| 62 | G1 | `human_authorization_grants_no_delete` (`human_authorization_grants`) | `human_authorization_grants_no_delete` → `refuse_change` | 1 (e.g. G1 delete human_authorization_grants) | — |
| 63 | G1 | `human_authorization_grant_supersessions_no_update` (`human_authorization_grant_supersessions`) | `human_authorization_grant_supersessions_no_update` → `refuse_change` | 1 (e.g. G1 update human_authorization_grant_supersessions) | — |
| 64 | G1 | `human_authorization_grant_supersessions_no_delete` (`human_authorization_grant_supersessions`) | `human_authorization_grant_supersessions_no_delete` → `refuse_change` | 1 (e.g. G1 delete human_authorization_grant_supersessions) | — |
| 65 | G1 | `ai_proposal_confirmations_no_update` (`ai_proposal_confirmations`) | `ai_proposal_confirmations_no_update` → `refuse_change` | 1 (e.g. G1 update ai_proposal_confirmations) | — |
| 66 | G1 | `ai_proposal_confirmations_no_delete` (`ai_proposal_confirmations`) | `ai_proposal_confirmations_no_delete` → `refuse_change` | 1 (e.g. G1 delete ai_proposal_confirmations) | — |
| 67 | G1 | `human_authorization_grant_consumptions_no_update` (`human_authorization_grant_consumptions`) | `human_authorization_grant_consumptions_no_update` → `refuse_change` | 1 (e.g. G1 update human_authorization_grant_consumptions) | — |
| 68 | G1 | `human_authorization_grant_consumptions_no_delete` (`human_authorization_grant_consumptions`) | `human_authorization_grant_consumptions_no_delete` → `refuse_change` | 1 (e.g. G1 delete human_authorization_grant_consumptions) | — |
| 69 | G2 | `ai_proposals_no_replace` (`ai_proposals`) | `f2_ai_proposals_no_replace` → `refuse_replace` | 4 (e.g. G2 ai_proposals collides on (proposal_id)) | 2 |
| 70 | G2 | `ai_proposal_review_revisions_no_replace` (`ai_proposal_review_revisions`) | `f3_ai_proposal_review_revisions_no_replace` → `refuse_replace` | 4 (e.g. G2 ai_proposal_review_revisions collides on (review_revision_id)) | 2 |
| 71 | G2 | `ai_proposal_review_decisions_no_replace` (`ai_proposal_review_decisions`) | `f4_ai_proposal_review_decisions_no_replace` → `ai_proposal_review_decisions_no_replace` | 5 (e.g. G2 ai_proposal_review_decisions collides on (decision_id)) | 3 |
| 72 | G2 | `human_authorization_grants_no_replace` (`human_authorization_grants`) | `f4_human_authorization_grants_no_replace` → `refuse_replace` | 6 (e.g. G2 human_authorization_grants collides on (grant_id)) | 4 |
| 73 | G2 | `human_authorization_grant_supersessions_no_replace` (`human_authorization_grant_supersessions`) | `f2_human_authorization_grant_supersessions_no_replace` → `refuse_replace` | 3 (e.g. G2 human_authorization_grant_supersessions collides on (grant_id)) | — |
| 74 | G2 | `ai_proposal_confirmations_no_replace` (`ai_proposal_confirmations`) | `f4_ai_proposal_confirmations_no_replace` → `refuse_replace` | 8 (e.g. G2 ai_proposal_confirmations collides on (baec_id)) | 6 |
| 75 | G2 | `human_authorization_grant_consumptions_no_replace` (`human_authorization_grant_consumptions`) | `f3_human_authorization_grant_consumptions_no_replace` → `refuse_replace` | 4 (e.g. G2 human_authorization_grant_consumptions collides on (grant_id)) | 2 |
| 76 | G6 | `ai_proposals_artifact_binding` (`ai_proposals`) | `f1_ai_proposals_artifact_binding` → `ai_proposals_artifact_binding` | 7 (e.g. G6 a proposal of an eligible artifact) | 5 |
| 77 | G6 | `ai_proposal_review_revisions_sequence` (`ai_proposal_review_revisions`) | `f2_ai_proposal_review_revisions_sequence` → `ai_proposal_review_revisions_sequence` | 5 (e.g. G6 revision 2 extends revision 1 and supersedes its grant) | 2 |
| 78 | G6 | `ai_proposal_review_revisions_open` (`ai_proposal_review_revisions`) | `f1_ai_proposal_review_revisions_open` → `ai_proposal_review_revisions_open` | 2 (e.g. G6 revision: a confirmed proposal) | 2 |
| 79 | G7 | `ai_proposal_review_revisions_supersede_grants` (`ai_proposal_review_revisions`) | `ai_proposal_review_revisions_supersede_grants` → `ai_proposal_review_revisions_supersede_grants` | 1 (e.g. G6 revision 2 extends revision 1 and supersedes its grant) | — |
| 80 | G6 | `ai_proposal_review_decisions_binding` (`ai_proposal_review_decisions`) | `f3_ai_proposal_review_decisions_binding` → `ai_proposal_review_decisions_binding` | 4 (e.g. G6 decision: another account) | 3 |
| 81 | G6 | `ai_proposal_review_decisions_latest` (`ai_proposal_review_decisions`) | `f2_ai_proposal_review_decisions_latest` → `ai_proposal_review_decisions_latest` | 2 (e.g. G6 decision: accept a revision that is not the latest) | 1 |
| 82 | G6 | `ai_proposal_review_decisions_terminal` (`ai_proposal_review_decisions`) | `f1_ai_proposal_review_decisions_terminal` → `ai_proposal_review_decisions_terminal` | 2 (e.g. G6 decision: after a confirmation) | 2 |
| 83 | G7 | `ai_proposal_review_decisions_reject_supersedes` (`ai_proposal_review_decisions`) | `ai_proposal_review_decisions_reject_supersedes` → `ai_proposal_review_decisions_reject_supersedes` | 1 (e.g. G7 a rejection supersedes the proposal's unretired grant) | — |
| 84 | G7 | `human_authorization_grants_accepted` (`human_authorization_grants`) | `f3_human_authorization_grants_accepted` → `human_authorization_grants_accepted` | 5 (e.g. G7 a grant for an accepted latest revision) | 4 |
| 85 | G7 | `human_authorization_grants_proposal_digest` (`human_authorization_grants`) | `f2_human_authorization_grants_proposal_digest` → `human_authorization_grants_proposal_digest` | 1 (e.g. G7 grant: proposal digest differs) | — |
| 86 | G7 | `human_authorization_grants_one_unretired` (`human_authorization_grants`) | `f1_human_authorization_grants_one_unretired` → `human_authorization_grants_one_unretired` | 6 (e.g. G7 grant: a second unretired grant for the revision) | 3 |
| 87 | G7 | `human_authorization_grant_supersessions_binding` (`human_authorization_grant_supersessions`) | `f1_human_authorization_grant_supersessions_binding` → `human_authorization_grant_supersessions_binding` | 7 (e.g. G7 supersession: a grant already superseded) | 6 |
| 88 | G8 | `ai_proposal_confirmations_authorization_binding` (`ai_proposal_confirmations`) | `f3_ai_proposal_confirmations_authorization_binding` → `ai_proposal_confirmations_authorization_binding` | 7 (e.g. G8 a link for the grant's own confirmed BAEC) | 5 |
| 89 | G8 | `ai_proposal_confirmations_unretired` (`ai_proposal_confirmations`) | `f2_ai_proposal_confirmations_unretired` → `ai_proposal_confirmations_unretired` | 6 (e.g. G8 a link for the grant's own confirmed BAEC) | 5 |
| 90 | G8 | `ai_proposal_confirmations_classification` (`ai_proposal_confirmations`) | `f1_ai_proposal_confirmations_classification` → `ai_proposal_confirmations_classification` | 4 (e.g. G8 a link for the grant's own confirmed BAEC) | 2 |
| 91 | G8 | `human_authorization_grant_consumptions_window` (`human_authorization_grant_consumptions`) | `f2_human_authorization_grant_consumptions_window` → `human_authorization_grant_consumptions_window` | 10 (e.g. G8 consumption: at issuance) | 2 |
| 92 | G8 | `human_authorization_grant_consumptions_pairing` (`human_authorization_grant_consumptions`) | `f1_human_authorization_grant_consumptions_pairing` → `human_authorization_grant_consumptions_pairing` | 2 (e.g. G8 consumption: pairs with another link) | 1 |
