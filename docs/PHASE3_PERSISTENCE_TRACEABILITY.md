# Phase 3 Persistence Traceability

**Project:** BAEC Trigger Intelligence
**Checkpoints:** `phase-3-persistence` (commit `4063c23`), `phase-3-persistence-hardening` (commit `3e68030`), and `phase-3-evidence-fidelity-hardening`
**Status:** Addendum to `docs/RESEARCH_CONTRACT.md`. Records how the Phase 3 persistence layer carries existing Research Contract rules into storage.

This addendum adds no rules and changes none. The Research Contract stays the research authority, and the manuscript overrides it. If this addendum disagrees with the contract, the contract wins and this addendum must be corrected.

---

## 1. What this addendum does not show

Persistence enforcement is software engineering. It makes sure that what is stored and loaded agrees with the domain rules already fixed in Phase 1. It does not validate the BAEC or CEE theory, the four criteria, the quality dimensions, the account states, or any proposition. The tests named here are software tests on synthetic data, not evidence for the theory (Research Contract §13).

Every rule in this addendum keeps the label the Research Contract gives it. Every Phase 3 mechanism described here is an **IMPLEMENTATION** choice.

## 2. Design rules Phase 3 follows (IMPLEMENTATION)

From `baec_app/data/repository.py` and `database.py`:

- The repository never classifies a BAEC and never decides a state transition. It calls the locked domain functions only to check, and it refuses to save anything those functions would not produce.
- No caller-built `TransitionResult` is accepted. A transition is saved only after the repository re-runs the locked state machine against the stored account and stored prerequisites.
- Every load goes back through the locked domain constructors. Corrupt or contradictory stored data raises `PersistenceIntegrityError`. Nothing partial or repaired is returned.
- Every write is one immediate transaction that rolls back fully on any error.
- The repository never reads the clock. Callers supply timestamps.
- Stored evidence text must occur verbatim in the text of the interaction it cites, on save and on load (evidence fidelity; see §3).
- STRICT tables, CHECK constraints, foreign keys, and triggers are a backstop. The domain constructors remain the authority on what stored data means. Triggers are not a security boundary: anyone who can write the database file can remove them.

## 3. Rule-by-rule traceability

Paths are relative to the repository root. Tests marked "seed" run on the synthetic demo database. Tests in `tests/test_rc33_schema_guards.py` were added in the RC-33 hardening commit, and tests in `tests/test_evidence_fidelity.py` in the evidence-fidelity hardening commit. All other tests listed here exist at the `phase-3-persistence` tag.

### RC-13. Classification rule (IMPLEMENTATION of RC-02 and RC-10)

| Mechanism | Code | Tests |
|---|---|---|
| Every save re-runs `classify_candidate`. A record is refused unless its classification and reason text match exactly. | `repository.py` (`_verify_against_rules`, `save_classification_record`, `save_confirmed_baec`) | `tests/test_repository_integrity.py::test_rc13_save_classification_record_refuses_a_confirmed_record`, `::test_rc13_save_classification_record_refuses_non_canonical_reason`, `::test_rc13_save_refuses_when_the_rules_compute_a_different_classification`, `::test_save_confirmed_baec_refuses_non_confirmed_and_non_current_records` |
| Every load re-checks classification and reason against the locked rules. | `repository.py` (`_rehydrate_record`, step 8) | `tests/test_repository_integrity.py::test_corrupted_confirmed_record_fails_closed` (cases such as "criterion flipped to NOT_MET under a confirmed record", "confirmed record relabelled NOT_BAEC"), `::test_rc13_corrupted_nonconfirmed_record_fails_closed` |
| Raw SQL cannot change a stored classification or reason. | `database.py` (`baec_records_protected_no_update`) | `tests/test_rc33_schema_guards.py::test_rc33_raw_update_of_a_protected_baec_record_column_is_refused` |
| All 243 finding/origin combinations round-trip unchanged. | `repository.py` | `tests/test_repository_roundtrip.py::test_rc13_every_classification_combination_round_trips` |
| Seller-seeded and insufficient-evidence records are kept with their reason and do not drive account state. | `seed.py` | seed: `tests/test_seed.py::test_rc13_meridian_negative_record_is_preserved_and_does_not_drive_state` |

### RC-18. Threshold preservation (IMPLEMENTATION)

| Mechanism | Code | Tests |
|---|---|---|
| Comparator, verbatim wording, unit, qualitative term, recurrence, and timing are stored in their own columns and loaded through `StringencyExpression`. | `database.py` (`stringency_expressions`); `repository.py` (`_insert_stringency`, `_load_stringency`) | `tests/test_repository_roundtrip.py::test_stringency_cases_cover_every_comparator`, `::test_rc18_stringency_round_trip` |
| Numbers are stored as Decimal text and never pass through a float, so "10" and "10.0" stay distinct. | `repository.py` (`encode_decimal`, `decode_decimal`) | `tests/test_repository_roundtrip.py::test_rc18_decimal_thresholds_are_stored_as_text_without_float_conversion`, `::test_decimal_and_datetime_codecs_reject_what_they_cannot_represent` |
| Stored thresholds are append-only and cannot be replaced. | `database.py` (append-only triggers; `stringency_expressions_no_replace`) | `tests/test_repository_integrity.py::test_rc33_append_only_tables_refuse_update_and_delete`; `tests/test_rc33_schema_guards.py::test_rc33_stored_rows_cannot_be_replaced` |
| A corrupted stored threshold fails closed. | `repository.py` (`_fail_closed`) | `tests/test_repository_integrity.py::test_rc18_corrupted_stringency_fails_closed` |
| Seed thresholds are text in the JSON and are kept exactly as written. | `seed.py`; `data/demo/baecs.json` | seed: `tests/test_seed.py::test_rc18_harbor_threshold_is_preserved_exactly`, `::test_seed_thresholds_are_text_in_the_json_never_floats` |

### RC-22. Conditionally Dormant gate (IMPLEMENTATION of §4.8, P9, §5.1)

| Mechanism | Code | Tests |
|---|---|---|
| A dormancy transition loads the stored account, the stored BAEC, and the stored judgment, then re-runs `transition_to_conditionally_dormant`. Nothing is written unless it is allowed. | `repository.py` (`persist_transition_to_conditionally_dormant`) | `tests/test_transition_persistence.py::test_rc22_dormant_entry_references_the_baec_judgment_and_unresolved_items`, `::test_rc22_stored_judgment_that_blocks_dormancy_persists_nothing`, `::test_rc22_judgment_for_a_different_baec_is_rejected`, `::test_rc22_nonconfirmed_stored_record_cannot_support_dormancy`, `::test_the_transition_uses_the_stored_account_not_a_caller_supplied_one` |
| Dormancy judgments are stored append-only with their authorization and round-trip for every plausibility/addressability pair. | `repository.py` (`record_dormancy_judgment`); `database.py` (`dormancy_judgments`) | `tests/test_repository_roundtrip.py::test_rc22_dormancy_judgment_round_trip`, `::test_dormancy_judgments_are_listed_in_order_with_their_identifiers`, `::test_dormancy_judgment_for_an_unknown_baec_is_not_found` |
| Composite foreign keys bind a transition's BAEC to its account, and its judgment to that BAEC. | `database.py` (composite keys added in schema version 2) | `tests/test_transition_persistence.py::test_database_binds_a_transitions_baec_to_its_account_and_its_judgment_to_that_baec`, `::test_composite_parent_keys_exist_on_baec_records_and_dormancy_judgments` |
| A stored dormancy transition is re-checked on load. It must reference a confirmed BAEC of the same account, and a judgment of that BAEC with plausibility YES and addressability YES or UNKNOWN. Its unresolved items must match the judgment's addressability. | `repository.py` (`_verify_dormant_history_entry`) | `tests/test_transition_persistence.py::test_dormant_history_referencing_a_baec_from_another_account_fails_closed`, `::test_dormant_history_referencing_a_judgment_for_another_baec_fails_closed`, `::test_dormant_history_referencing_a_non_confirmed_record_fails_closed`, `::test_rc22_unresolved_disagreeing_with_the_judgment_fails_closed`, `::test_rc22_dormant_history_whose_judgment_no_longer_supports_dormancy_fails_closed`, `::test_dormant_history_referencing_a_missing_row_fails_closed`, `::test_valid_dormant_history_still_loads_including_unknown_addressability`; seed: `tests/test_seed.py::test_harbor_dormant_history_repointed_at_meridians_baec_fails_closed` |

### RC-29. BAECs go stale (MANAGERIAL; any interval is IMPLEMENTATION)

| Mechanism | Code | Tests |
|---|---|---|
| A newly saved confirmed BAEC must be `CURRENT`. | `repository.py` (`save_confirmed_baec`) | `tests/test_repository_integrity.py::test_save_confirmed_baec_refuses_non_confirmed_and_non_current_records` |
| A stored BAEC that is not `CURRENT` cannot support entry into Conditionally Dormant. | `repository.py` (re-runs the state machine on the stored record) | `tests/test_transition_persistence.py::test_rc29_stored_baec_that_is_not_current_cannot_support_dormancy` |
| A stored confirmed record loads with any staleness status, and a BAEC that later goes stale does not invalidate a past transition. | `repository.py` (`_rehydrate_record`, `_verify_dormant_history_entry`) | `tests/test_repository_roundtrip.py::test_rc29_older_confirmed_records_rehydrate_with_any_staleness_status`; `tests/test_transition_persistence.py::test_rc29_baec_going_stale_later_does_not_invalidate_the_historical_transition` |
| `staleness_status` is the only `baec_records` column left updatable, reserved for a later lifecycle design. | `database.py` (`BAEC_RECORD_MUTABLE_COLUMNS`) | `tests/test_rc33_schema_guards.py::test_rc33_staleness_status_is_the_only_mutable_baec_record_column` |

Not in Phase 3: there is no lifecycle operation that marks a BAEC `REVIEW_DUE`, `STALE`, or `RETIRED`, no review interval, and no SQL-level rule about which staleness changes are allowed. Stale status appears only in test fixtures. No staleness interval is research-derived (Research Contract §13).

### RC-31. Authoritative changes require human authorization (IMPLEMENTATION)

| Mechanism | Code | Tests |
|---|---|---|
| Each confirmation, dormancy judgment, and state change stores its `HumanAuthorization` in an append-only, replace-guarded table, one row per use (`UNIQUE` authorization id). | `database.py` (`human_authorizations`); `repository.py` (`_insert_authorization`) | `tests/test_repository_integrity.py::test_rc33_append_only_tables_refuse_update_and_delete`; `tests/test_rc33_schema_guards.py::test_rc33_stored_rows_cannot_be_replaced`, `::test_rc33_replace_through_a_secondary_unique_key_is_refused` |
| A confirmed record's link to its confirmation cannot be changed by raw SQL. | `database.py` (`baec_records_protected_no_update`) | `tests/test_rc33_schema_guards.py::test_rc33_raw_update_of_a_protected_baec_record_column_is_refused` |
| A missing, rejected, or fake authorization saves nothing. | `repository.py` (transitions re-run the state machine) | `tests/test_transition_persistence.py::test_rc31_rejected_transition_persists_nothing`, `::test_rc31_fake_authorization_raises_and_persists_nothing` |
| A stored authorization with the wrong action, subject, or target fails closed on load. | `repository.py` (`_rehydrate_record`) | `tests/test_repository_integrity.py::test_corrupted_confirmed_record_fails_closed` (cases "confirmation for a different BAEC", "confirmation with the wrong action", "confirmation carrying a target state", "confirmation row missing") |
| Seed authorizations are marked as synthetic fixtures. | `seed.py` (`SEED_AUTHORIZER = "synthetic-seed-fixture"`) | seed: `tests/test_seed.py::test_rc31_every_seed_authorization_is_marked_as_a_synthetic_fixture` |

**Boundary.** A stored `HumanAuthorization` row records that the domain requirement was met. It is **not** proof that a human acted. Phase 3 has no application layer, no sessions, and no actor identity check. Anyone with a `Repository` can construct a `HumanAuthorization` and pass it in. Proof that an authorization came from a real human action is deferred to a later phase, as RC-31 requires. Seed authorizations record no real human action.

### RC-32. Five provenance categories, never collapsed (IMPLEMENTATION)

| Mechanism | Code | Tests |
|---|---|---|
| Interaction evidence accepts only `BUYER_FACT` and `SELLER_OBSERVATION`, enforced by both the repository and a CHECK constraint. | `database.py` (`INTERACTION_EVIDENCE_PROVENANCE`); `repository.py` (`_evidence_id`) | `tests/test_database.py::test_interaction_evidence_accepts_only_buyer_fact_and_seller_observation`; `tests/test_repository_integrity.py::test_database_constraints_refuse_structurally_invalid_rows` (cases "external evidence stored as interaction evidence", "AI inference stored as interaction evidence") |
| Provenance is part of an evidence row's identity. The same text with different provenance is a separate row. | `database.py` (`UNIQUE (interaction_id, provenance, text)`) | `tests/test_repository_roundtrip.py::test_same_text_with_different_provenance_is_a_different_evidence_row` |
| Corrupted provenance fails closed on load. | `repository.py` | `tests/test_repository_integrity.py::test_corrupted_confirmed_record_fails_closed` (cases "unknown provenance", "external provenance on buyer evidence", "AI inference as evidence") |
| A record's articulation origin and elicitation mode cannot be changed by raw SQL. | `database.py` (`baec_records_protected_no_update`) | `tests/test_rc33_schema_guards.py::test_rc33_raw_update_of_a_protected_baec_record_column_is_refused` |
| Evidence must come from an interaction of the same account. | `repository.py` (`_require_same_account`); `database.py` (composite foreign keys) | `tests/test_repository_integrity.py::test_record_whose_interaction_is_missing_or_belongs_to_another_account_is_refused`; `tests/test_transition_persistence.py::test_evidence_from_another_accounts_interaction_is_refused` |
| AI-derived normalization is stored in separate columns with its timestamp and model, never in the source columns, and cannot be changed once written. | `database.py` (`normalized_*` columns, CHECK, `baec_records_protected_no_update`); `repository.py` | `tests/test_repository_roundtrip.py::test_rc33_baec_record_round_trip`; `tests/test_repository_integrity.py::test_corrupted_confirmed_record_fails_closed` (the "normalized …" cases); `tests/test_rc33_schema_guards.py::test_rc33_raw_update_of_a_protected_baec_record_column_is_refused` |
| The seed holds no external evidence and no AI-generated content. | `seed.py` | seed: `tests/test_seed.py::test_seed_stores_no_external_evidence`, `::test_seed_contains_no_ai_generated_content` |

Not in Phase 3: there is no storage for `EXTERNAL_EVIDENCE` (signals) or for `AI_INFERENCE` as a separate object, and nothing writes AI-derived normalization outside tests. Adding normalization after capture will need its own designed storage path.

### RC-33. Source evidence is immutable (IMPLEMENTATION)

| Mechanism | Code | Tests |
|---|---|---|
| Ten tables are append-only: triggers refuse UPDATE and DELETE. These are `interactions`, `interaction_evidence`, `human_authorizations`, `criterion_assessments`, `criterion_evidence`, `stringency_expressions`, `dormancy_judgments`, `evaluation_evidence`, `non_evaluation_evidence`, and `account_state_transitions`. | `database.py` (`APPEND_ONLY_TABLES`) | `tests/test_database.py::test_append_only_triggers_cover_exactly_the_approved_tables`; `tests/test_repository_integrity.py::test_rc33_append_only_tables_refuse_update_and_delete` |
| *(hardening)* Those ten tables and `baec_records` refuse any insert that would replace a stored row through any unique key. This closes `REPLACE`, `INSERT OR REPLACE`, UPSERT, and second-key replacement. | `database.py` (`REPLACE_GUARDED_KEYS`, `<table>_no_replace`) | `tests/test_rc33_schema_guards.py::test_rc33_stored_rows_cannot_be_replaced`, `::test_rc33_replace_through_a_secondary_unique_key_is_refused`, `::test_rc33_interaction_without_evidence_cannot_be_replaced`, `::test_rc33_upsert_cannot_change_a_protected_baec_record_column`, `::test_rc33_replace_guards_cover_every_unique_key` |
| *(hardening)* In `baec_records`, every source, provenance, decision, and AI-derived column is protected from UPDATE, including no-op updates. Rows cannot be deleted. `staleness_status` is the only mutable column. | `database.py` (`BAEC_RECORD_PROTECTED_COLUMNS`, `BAEC_RECORD_MUTABLE_COLUMNS`, `baec_records_protected_no_update`, `baec_records_no_delete`) | `tests/test_rc33_schema_guards.py::test_rc33_baec_record_columns_are_partitioned_into_protected_and_mutable`, `::test_rc33_raw_update_of_a_protected_baec_record_column_is_refused`, `::test_rc33_reported_gap_buyer_statement_and_capture_time_cannot_be_rewritten`, `::test_rc33_baec_record_rows_cannot_be_deleted` |
| *(hardening)* Guards are present in working copies and the canonical seed. Version 2 databases, which lack them, are refused. | `database.py` (schema version 3 at that commit; now 4) | `tests/test_rc33_schema_guards.py::test_rc33_guards_are_present_in_working_copies_and_the_canonical_seed`, `::test_database_created_with_schema_version_2_is_refused`; `tests/test_database.py::test_working_copy_is_complete_independent_and_writable` |
| Records round-trip field-for-field. | `repository.py` | `tests/test_repository_roundtrip.py::test_rc33_baec_record_round_trip` |
| Tampering with guards removed that contradicts the domain rules fails closed on load. Examples: a buyer statement that is no longer verbatim in its excerpt, altered evidence text, a moved interaction. | `repository.py` (`_rehydrate_record`) | `tests/test_repository_integrity.py::test_corrupted_confirmed_record_fails_closed` (cases "buyer statement rewritten", "evidence text altered so the quote is no longer verbatim", "evidence moved to another interaction") |
| Account state can change only through a saved transition, and the stored state must match an unbroken history chain. | `repository.py` (`add_account`, `_update_account_state`, `_verify_state_matches_history`) | `tests/test_repository_roundtrip.py::test_rc21_accounts_can_only_be_inserted_unclassified`; `tests/test_transition_persistence.py::test_history_is_appended_in_order_and_never_overwritten`, `::test_account_state_overwritten_outside_a_transition_fails_closed`, `::test_state_set_with_no_history_at_all_fails_closed`, `::test_stale_state_rolls_back_the_whole_transition` |
| Seed evidence text appears verbatim in its interaction. | `seed.py` | seed: `tests/test_seed.py::test_rc33_every_seeded_evidence_text_appears_verbatim_in_its_interaction` |

**At the `phase-3-persistence` tag** (closed by the hardening commit):

- `baec_records` had no SQL-level protection. A direct change that stayed consistent with the domain rules loaded without error. Examples: setting `buyer_exact_statement` to `NULL`, or changing `captured_at` to another valid timestamp.
- Append-only tables could be rewritten with `REPLACE`, which SQLite runs as delete-then-insert without firing DELETE triggers. This worked even with foreign keys on, for rows that no other row referenced yet.

**At the `phase-3-persistence-hardening` tag** (closed by the evidence-fidelity hardening): stored evidence text was never checked against the interaction it cites. Text that appeared nowhere in an interaction could be stored as evidence from it. On the seed database this was enough to move an account to Active Opportunity.

**Still not enforced after hardening:**

- The guards are not a security boundary. Anyone who can write the database file can drop the triggers.
- Auditable amendments are not implemented, so there is no correction path for any protected field.
- `accounts` and `schema_meta` have no triggers. `accounts.state` is protected by the transition-history check. `accounts.name`, and deleting an account with foreign keys off, are not guarded.

### Evidence fidelity (IMPLEMENTATION constraint)

Stored `BUYER_FACT` and `SELLER_OBSERVATION` evidence text must occur verbatim (an exact, case-sensitive substring, with no whitespace or Unicode normalization) in the text of the interaction named by the excerpt's `source_id`. This covers BAEC source excerpts, criterion evidence (C1–C4), `EvaluationEvidence`, and `NonEvaluationEvidence`, all of which are stored in `interaction_evidence`. Future `EXTERNAL_EVIDENCE` is out of scope and will have a separate storage path.

This is an implementation-level evidence-fidelity constraint chosen for this software. Neither RC-33 nor the manuscript requires that seller observations be verbatim substrings of a stored interaction. The constraint supports provenance integrity under RC-32 and RC-33, and it operationally protects the boundary between evidence and signals: evidence used to change account state must be traceable to the stored text it claims to quote. It is not a research finding. As a consequence, in this software a seller observation must be recorded as part of an interaction's text before it can be cited as evidence.

| Mechanism | Code | Tests |
|---|---|---|
| Save: a non-verbatim excerpt is refused with `RepositoryVerificationError`, and the whole write rolls back. Covers every evidence path. | `repository.py` (`_evidence_id`) | `tests/test_evidence_fidelity.py::test_exact_substring_is_accepted`, `::test_non_verbatim_evidence_is_rejected_on_every_path`, `::test_curly_apostrophe_is_not_the_stored_straight_apostrophe`, `::test_fabricated_candidate_source_excerpt_is_rejected_for_nonconfirmed_saves_too`, `::test_text_from_another_interaction_does_not_authorize_citing_the_wrong_one`, `::test_the_same_text_may_be_cited_from_each_interaction_that_contains_it` |
| Load: stored evidence that is not verbatim in its interaction, or that cites a missing interaction, fails closed with `PersistenceIntegrityError`, even when every database guard was bypassed. | `repository.py` (`_load_excerpt`) | `tests/test_evidence_fidelity.py::test_bypassed_schema_still_fails_closed_on_load_for_baec_evidence`, `::test_bypassed_schema_still_fails_closed_on_load_for_state_evidence`, `::test_interaction_text_rewritten_under_its_evidence_fails_closed` |
| Schema: the `interaction_evidence_verbatim` BEFORE INSERT trigger refuses non-verbatim or empty text, and text citing a missing interaction, even with foreign keys and CHECK constraints off. SQLite `instr` and Python `in` agree on what counts as verbatim. | `database.py` (`interaction_evidence_verbatim`) | `tests/test_evidence_fidelity.py::test_raw_sql_fabricated_evidence_insert_is_refused_by_the_schema`, `::test_raw_sql_evidence_for_a_missing_interaction_is_refused_even_with_foreign_keys_off`, `::test_raw_sql_verbatim_evidence_insert_is_accepted`, `::test_sql_and_python_agree_on_verbatim_matching`, `::test_provenance_check_still_refuses_non_interaction_evidence_with_verbatim_text` |
| Fabricated state evidence cannot change account state. | `repository.py` (`_evidence_id`, reached through the transition writes) | `tests/test_evidence_fidelity.py::test_fabricated_evaluation_evidence_cannot_activate`, `::test_fabricated_non_evaluation_evidence_cannot_leave_active` |
| Version 3 databases, which lack the trigger, are refused. | `database.py` (`SCHEMA_VERSION = 4`) | `tests/test_evidence_fidelity.py::test_database_created_with_schema_version_3_is_refused`; `tests/test_database.py::test_schema_version_is_set_and_a_mismatch_is_refused` |

### Other rules touched by Phase 3

| Rule | Mechanism | Tests |
|---|---|---|
| RC-20, RC-21 | Accounts are inserted unclassified (`None`). The schema allows exactly the three states. Every allowed move saves one history row. | `tests/test_repository_roundtrip.py::test_rc21_accounts_can_only_be_inserted_unclassified`; `tests/test_database.py::test_enum_value_lists_in_the_schema_match_the_enum_members`; `tests/test_transition_persistence.py::test_rc20_every_allowed_move_is_persisted_with_one_history_row`; seed: `tests/test_seed.py::test_rc20_three_demo_accounts_end_in_their_expected_states`, `::test_rc21_every_seed_state_came_from_a_real_transition_out_of_unclassified` |
| RC-23 | A No Plausible Path decision stores its ground, reason, and basis interaction. | `tests/test_transition_persistence.py::test_rc20_no_plausible_path_entry_records_ground_reason_and_basis_interaction`, `::test_every_no_plausible_path_ground_round_trips`; seed: `tests/test_seed.py::test_rc23_summit_has_no_baec_and_a_structured_no_plausible_path_decision` |
| RC-27 | No repository method accepts a `TransitionResult` or a signal. Entry into Active Opportunity stores its buyer-evaluation evidence, which must occur verbatim in the interaction it cites (evidence fidelity, above). | `tests/test_transition_persistence.py::test_rc27_no_persistence_function_accepts_a_transition_result_or_a_signal`, `::test_rc27_active_entry_records_the_evaluation_evidence`; `tests/test_evidence_fidelity.py::test_fabricated_evaluation_evidence_cannot_activate`; seed: `tests/test_seed.py::test_rc27_meridian_is_active_because_of_buyer_evaluation_evidence` |
| RC-34 | Leaving Active Opportunity stores the non-evaluation evidence separately. | `tests/test_transition_persistence.py::test_rc34_leaving_active_stores_the_non_evaluation_evidence_separately` |

## 4. Still deferred after Phase 3

RC-15, RC-19, RC-26, RC-28, and RC-30 remain later-phase rules, as Research Contract §15 records. Also deferred:

- authorization for approving monitoring and for recording signal reviews;
- proof that any authorization came from a real human action (RC-31);
- the BAEC staleness lifecycle (RC-29);
- auditable amendments (RC-33).

## 5. Test runs

| Checkpoint | Result |
|---|---|
| `phase-3-persistence` (`4063c23`) | 1403 passed (Python 3.14.3, SQLite 3.50.4, 2026-10-01) |
| `phase-3-persistence-hardening` (`3e68030`) | 1548 passed (Python 3.14.3, SQLite 3.50.4, 2026-10-01) |
| `phase-3-evidence-fidelity-hardening` | 1658 passed (Python 3.14.3, SQLite 3.50.4, 2026-10-01) |

These are software tests on synthetic data. They do not validate the theory.
