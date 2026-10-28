# Phase 7 Final Verification

**Project:** BAEC Trigger Intelligence
**Status:** Phase 7 (human-authorized AI proposal bridge) implemented and verified as software. Verified at commit `eb4b61bd1a12fdf60912d6036a1ba8f8c365ea1a` plus the Phase 7H verification tests and this document.
**Governing sources:** the manuscript, then `docs/RESEARCH_CONTRACT.md`, then `docs/PHASE7_HUMAN_AUTHORIZED_AI_PROPOSAL_BRIDGE_DESIGN.md` (with its 7D, 7F-B, and 7G implementation clarifications), then code. If any wording here conflicts with the manuscript or the Research Contract, they win.

> **Limitation.** Phase 7 tests demonstrate one software implementation of the managerial implications proposed in the BAEC manuscript. They do not validate the BAEC construct, establish prospective validity, or demonstrate that detected/confirmed BAECs predict purchases or opportunity creation.

All data used in Phase 7 verification is synthetic and test-only. Every AI artifact was produced offline by the real Phase 6 extraction service driven by a fake provider; none is real model output, and no AI call was made in Phase 7.

---

## 1. How to read this document

Each statement below belongs to exactly one of four kinds. They are never mixed.

| Kind | Meaning | Example |
|---|---|---|
| **Research / theoretical claim** | Comes from the manuscript, through the Research Contract. Conceptual and untested. Phase 7 makes no new one. | RC-01: a BAEC is "an explicit, buyer-generated statement…". |
| **Implementation choice** | A decision made for this software. Not a research finding. | The 15-minute grant lifetime; the one-tool MCP server. |
| **Deterministic software verification** | A software test or mutation result on synthetic data. Shows this code behaves as specified. Says nothing about the theory. | "A replayed grant is refused with `grant_already_consumed`." |
| **Accepted prototype limitation** | Something this prototype does not and cannot guarantee. | Actor labels are self-asserted. |

---

## 2. The implemented chain (implementation)

```
persisted synthetic interaction
↓
verified Phase 6 AI artifact
↓
deterministic AI_DRAFT mapping
↓
explicit human review
↓
immutable ACCEPTED review revision
↓
separate explicit human authorization
↓
persisted 15-minute single-use grant
↓
one-tool MCP execution
↓
atomic confirmed BAEC
```

| Step | Boundary in code |
|---|---|
| Verified Phase 6 artifact | `baec_app/ai` (Phase 6, unchanged); re-verified through `baec_app/ai/verification.py` |
| Deterministic AI_DRAFT mapping | `baec_app/application/ai_proposal_mapping.py` (`AiProposalMappingService`, `baec-ai-proposal-mapping/v1`) |
| Explicit human review | `baec_app/interfaces/review_page.py` → `baec_app/application/proposal_review.py` (`ProposalReviewService.accept_review` / `reject_proposal`), with `baec_app/application/human_normalization.py` (`baec-human-normalization-validation/v1`) |
| Immutable ACCEPTED revision | `ai_proposal_review_revisions`, `ai_proposal_review_decisions` (append-only; `baec_app/data/proposal_bridge.py`) |
| Separate explicit human authorization | the page's "Authorize BAEC confirmation" action → `ProposalAuthorizationService.authorize_confirmation` |
| Persisted 15-minute single-use grant | `human_authorization_grants` via `AuthorizationGrantStore` (issuance-capable store, held only by the issuance service) |
| One-tool MCP execution | `baec_app/mcp_write` (`confirm_baec(grant_id)`) → `ConfirmationExecutionService.execute`, which holds only `GrantExecutionStore` |
| Atomic confirmed BAEC | one `BEGIN IMMEDIATE` transaction: BAEC record, `HumanAuthorization`, `ai_proposal_confirmations` link, grant consumption |

**Distinctions that hold throughout (verified):**

- Review Accepted ≠ Authorization Granted. Accepting a review writes no grant.
- Authorization Granted ≠ BAEC Confirmed. Issuing a grant writes no BAEC.
- BAEC Confirmed ≠ Active Opportunity. Confirmation changes no account state.
- BAEC Confirmed ≠ purchase intent. Nothing in Phase 7 records or reports intent, commitment, or purchase (RC-03, RC-04).

**No Phase 7 operation changes account state.** No Phase 7 module can call or persist an account-state transition, a dormancy judgment, or a staleness change (§7).

A human may ACCEPT a completed review whose deterministic classification is not `CONFIRMED_BAEC` (for example, C1 `UNKNOWN`, or origin `SELLER_SEEDED`). That review is recorded, but `ProposalAuthorizationService` refuses to issue a grant (`not_confirmable`), so the MCP tool has no authority to execute anything for it.

---

## 3. Flagship end-to-end verification (deterministic software verification)

`tests/test_phase7_end_to_end.py::test_the_full_phase7_chain_confirms_exactly_one_baec_through_human_review_human_authorization_and_mcp`

On an ephemeral synthetic schema-v7 database:

1. The real Phase 6 `ExtractionService` (offline fake provider) persists one successful artifact for a synthetic interaction.
2. `AiProposalMappingService` maps it to one AI_DRAFT proposal.
3. The real Streamlit review page, driven by AppTest, receives every authoritative value from the "human": evidence selections and their provenance, findings, origin, elicitation mode, stringency, buyer statement, final normalization, and a reviewer label. The human clicks Accept. No grant exists afterwards.
4. The human clicks the separate "Authorize BAEC confirmation" action. One grant is persisted, and no BAEC exists afterwards.
5. The Phase 7G MCP server, through an in-process MCP client, executes `confirm_baec(grant_id)`; a second call is refused with `grant_already_consumed`.

Proven at the end: exactly one AI run, artifact, AI_DRAFT proposal, accepted revision, ACCEPTED decision, grant, confirmed BAEC, `HumanAuthorization`, confirmation link, and consumption. Only the chain's own tables changed; the source interaction, the AI run and artifact rows, the AI proposal, the accepted revision, the grant, and `accounts` are byte-identical to their earlier states. There are zero state-transition, dormancy, and state-evidence rows, and no outreach storage exists. `baec_records.normalized_*` are NULL; the final human normalization is recovered from the review revision. One SQL join reconstructs the audit chain: confirmed BAEC → `HumanAuthorization` → confirmation link → grant (+ consumption) → review revision (content digest recomputed) → proposal (digest) → AI artifact (digest) → AI run → source interaction.

Two further end-to-end tests cover the design's 7H scenario (grant → edit → supersession → new human authorization → MCP confirm → repeat refused) and capture every SQL statement the human path and the MCP-composed executor execute, showing no state, dormancy, or staleness statement and only the confirmation tables as `INSERT` targets.

AppTest proves traversal of the prototype's human-interaction boundary, not authenticated real-world human identity.

---

## 4. Threat-model closure (design §18, T1–T22)

Status values: **VERIFIED** (implementation and test evidence both exist), **ACCEPTED PROTOTYPE LIMITATION**, **OUT OF PHASE 7 SCOPE**. Test references are `file::test` under `tests/`. "7H" marks the new Phase 7H tests.

| ID | Threat | Mitigation | Implementation boundary | Test / evidence | Status |
|---|---|---|---|---|---|
| T1 | AI self-authorization | Grants are issued only by the explicit human page action through `ProposalAuthorizationService`. The MCP-composed executor holds only `GrantExecutionStore`, with no issuance capability. (The design's Phase 4 gate route was replaced by this path; see the 7F-B clarification.) | `proposal_authorization.py`; `authorization_grants.py` (capability split); `mcp_write/` | `test_mcp_write_boundary.py::test_the_server_reaches_one_executor_and_no_issuance_review_state_or_ai_object`, `::test_the_capability_walk_would_fail_if_the_executor_held_the_issuance_capable_store`, `::test_the_capability_walk_detects_every_other_issuance_path`; 7H `test_phase7_adversarial.py::test_a_valid_grant_is_required_and_only_the_human_authorization_path_issues_one`; mutations G01–G03, W04, W05, W33, W34 | VERIFIED |
| T2 | Forged grant (fabricated row values) | Execution checks the action, recomputes the 19-field binding digest, reloads the lineage, and `authorize_grant` re-validates on its own. | `ConfirmationExecutionService._execute`; `authority.authorize_grant`; `GrantRecord.binding_is_intact` | `test_proposal_authorization.py::test_a_grant_rebound_to_other_authority_fails_closed`, `::test_a_grant_whose_digest_no_longer_covers_its_binding_fails_closed`, `::test_a_grant_for_another_action_cannot_exist_or_execute`; `test_grant_audit_chain.py::test_a_rebound_action_is_refused_even_with_a_recomputed_digest`, `::test_a_damaged_binding_digest_is_refused` | VERIFIED (a fully self-consistent row written by someone with raw database write access is limitation L4) |
| T3 | Model-created grant | No MCP tool, resource, or prompt creates or lists grants; grant ids are random (`secrets`). | `mcp_write/server.py`; Phase 5 `baec_app/mcp/` exposes no grant data | `test_mcp_write_tool.py::test_exactly_one_tool_and_no_resources_templates_or_prompts_are_listed`; `test_mcp_write_boundary.py::test_the_phase5_package_names_nothing_from_phase7g`; 7H `test_phase7_adversarial.py::test_phase7g_mcp_executes_only_an_existing_grant_and_reaches_no_issuance`; mutations W01–W03 | VERIFIED |
| T4 | Replay | Single-use consumption (PRIMARY KEY), checked inside `BEGIN IMMEDIATE`. | executor; `human_authorization_grant_consumptions` | `test_proposal_authorization.py::test_replaying_a_consumed_grant_fails_and_writes_nothing`, `::test_two_concurrent_executions_of_one_grant_confirm_exactly_once`; `test_mcp_write_tool.py::test_replaying_a_consumed_grant_through_mcp_is_refused_and_writes_nothing`, `::test_two_concurrent_calls_on_separate_servers_confirm_exactly_once`; 7H flagship and lifecycle tests | VERIFIED |
| T5 | Expired grant | `now >= expires_at` is expired (executor); the schema trigger requires consumption inside the validity window. | executor; grant/consumption triggers | `test_proposal_authorization.py::test_expiry_is_now_at_or_after_expires_at`, `::test_a_grant_executes_one_microsecond_before_expiry`; `test_proposal_bridge_schema.py::test_a_consumption_must_fall_inside_the_validity_window_and_pair_with_its_link`; `test_mcp_write_tool.py::test_an_expired_grant_is_refused_through_mcp_with_no_write_and_no_reauthorization`, `::test_a_grant_just_before_expiry_still_confirms`; `test_mcp_write_stdio.py::test_an_expired_grant_is_refused_over_stdio_with_no_write` | VERIFIED (local clock manipulation is limitation L13) |
| T6 | Grant extension | Grant rows are append-only; TTL and `expires_at` are CHECKed; at most one live grant per revision; no extend operation exists. | schema triggers and CHECKs; `AuthorizationGrantStore` (no update method) | `test_proposal_bridge_schema.py::test_no_bridge_row_can_be_updated_deleted_or_replaced`, `::test_grant_columns_are_closed`, `::test_at_most_one_unretired_grant_per_revision_and_action`; `test_proposal_authorization.py::test_a_second_authorization_while_a_grant_is_active_is_refused_and_nothing_is_extended` | VERIFIED |
| T7 | Cross-account grant use | Composite foreign keys bind the grant to its account and lineage; the executor refuses a mismatched binding; the tool takes no account argument. | schema; executor; tool input schema | `test_proposal_bridge_schema.py::test_a_grant_is_bound_to_its_account_lineage_and_reviewed_content[account]`; `test_proposal_authorization.py::test_a_grant_rebound_to_other_authority_fails_closed[cross-account]`; `test_mcp_write_tool.py::test_the_advertised_input_and_output_schemas_are_pinned` | VERIFIED |
| T8 | Stale review revision / superseded grant | A new revision or a rejection writes a supersession row in the same statement; the executor checks supersession before expiry and the latest revision as defense in depth. | supersession triggers; executor steps | `test_proposal_bridge_schema.py::test_a_new_revision_supersedes_unretired_grants_in_the_same_statement`; `test_proposal_authorization.py::test_a_new_revision_supersedes_an_unexpired_grant_at_once`, `::test_a_rejection_supersedes_the_grant_and_blocks_reauthorization`, `::test_a_grant_on_a_stale_revision_fails_even_without_its_supersession_row`; `test_review_page_authorization.py::test_a_revision_edit_supersedes_the_earlier_grant_and_it_cannot_execute_changed_content`; `test_mcp_write_tool.py::test_a_superseded_unexpired_grant_is_refused_through_mcp`; 7H `test_phase7_end_to_end.py::test_an_edit_supersedes_the_first_grant_and_only_a_new_human_authorization_confirms` | VERIFIED |
| T9 | Content changed after approval | Revisions are immutable; the grant binds the reviewed-content digest; execution rebuilds the candidate only from the bound revision and requires a byte-identical content round trip. (The design's gate-displayed digest is not used; issuance is the explicit page action.) | `ai_proposal_review_revisions` triggers; `GrantRecord` binding; `rebuild_reviewed_candidate` | `test_proposal_bridge_schema.py::test_no_bridge_row_can_be_updated_deleted_or_replaced`; `test_proposal_authorization.py::test_the_grant_digest_covers_every_binding_field`, `::test_a_grant_rebound_to_other_authority_fails_closed[changed content digest]`, `::test_execution_reruns_normalization_and_classification`; 7H flagship (digest recomputed in the audit join) | VERIFIED |
| T10 | Duplicate BAEC | One confirmation per artifact and per proposal (UNIQUE); issuance and execution refuse an already-confirmed lineage. | `ai_proposal_confirmations` UNIQUE keys; `lineage_already_confirmed` | `test_proposal_bridge_schema.py::test_one_lineage_yields_at_most_one_confirmation`; `test_proposal_authorization.py::test_replaying_a_consumed_grant_fails_and_writes_nothing` (re-authorization refused `lineage_already_confirmed`); `test_ai_proposal_mapping.py` (re-mapping a confirmed lineage refused) | VERIFIED |
| T11 | AI speaker becomes buyer fact | Provenance comes only from the human's assertion on each selection; the AI speaker is displayed as AI_INFERENCE and never read into provenance. | `proposal_review.py` | `test_proposal_review.py::test_the_ai_speaker_never_sets_provenance`, `::test_an_ai_attributed_buyer_without_a_human_buyer_fact_assertion_is_refused`; 7H `test_phase7_adversarial.py::test_favorable_ai_suggestions_alone_yield_no_buyer_fact_findings_acceptance_grant_or_baec`, `::test_evidence_and_provenance_failures_stop_before_any_review_grant_or_baec[ai buyer attribution without human buyer fact]` | VERIFIED |
| T12 | AI normalization replaces buyer evidence | Normalization lives only in the review revision; `baec_records.normalized_*` stay NULL; the buyer statement must equal one BUYER_FACT selection. | `proposal_review.py`; executor; record factory | `test_proposal_authorization.py::test_a_valid_grant_confirms_exactly_one_baec_atomically_with_its_audit_link_and_consumption`; `test_mcp_write_tool.py::test_a_valid_grant_confirms_exactly_one_baec_through_mcp`; 7H flagship; 7H `test_phase7_adversarial.py::test_two_expressions_each_supported_by_their_own_excerpt_pass_and_confirm_through_mcp` | VERIFIED |
| T13 | Prompt injection | Untrusted text is rendered with `st.text` only (no markdown or HTML); AI text controls no decision; the tool takes only a grant id. | `review_page.py`; `mcp_write/server.py` | `test_review_page.py::test_source_and_ai_text_resembling_markup_or_instructions_render_as_inert_data`, `::test_the_page_never_enables_html_and_renders_untrusted_text_with_st_text_only`; 7H `test_phase7_adversarial.py::test_injected_text_renders_inertly_decides_nothing_and_only_the_human_chain_confirms` | VERIFIED (a human persuaded by injected text is limitation L14) |
| T14 | MCP argument broadening | Closed argument model: `grant_id` only, `additionalProperties: false`, strict, pattern, input hidden in errors. | `ConfirmBaecArguments` | `test_mcp_write_tool.py::test_caller_supplied_authority_is_rejected_at_the_schema_not_ignored`, `::test_a_malformed_grant_id_is_rejected_by_the_input_schema_without_echo`; 7H `test_phase7_adversarial.py::test_extra_authority_fields_are_rejected_by_the_schema_and_never_reach_the_executor`; mutations W06–W09, W21, W22 | VERIFIED |
| T15 | Direct account-state change | No Phase 7 module imports or calls the state machine, account-state, or dormancy services; confirmation inserts only confirmation rows. | all Phase 7 modules; executor | `test_mcp_write_tool.py::test_a_valid_grant_confirms_exactly_one_baec_through_mcp`; 7H `test_phase7_adversarial.py::test_no_phase7_module_can_call_or_persist_a_state_transition_dormancy_or_staleness_change`; 7H `test_phase7_end_to_end.py::test_the_mcp_execution_itself_issues_no_state_dormancy_or_staleness_sql`; mutation W16 | VERIFIED |
| T16 | Partial transaction | One `BEGIN IMMEDIATE` transaction; any failure, including at COMMIT, rolls back every write and leaves the grant unconsumed. | executor; `data.database.transaction` | `test_proposal_authorization.py::test_a_failure_inside_the_transaction_rolls_everything_back_and_keeps_the_grant`; `test_database.py::test_a_commit_failure_rolls_back_and_leaves_the_connection_reusable`; 7H `test_phase7_adversarial.py::test_a_fault_at_any_execution_write_writes_nothing_and_leaves_the_grant_usable` (all 8 execution write targets, through MCP), `::test_a_failure_at_commit_rolls_back_every_execution_write` | VERIFIED |
| T17 | Audit omission | Triggers enforce a 1:1 link between the authorization, the confirmation, and the consumption; `verify_bridge_integrity` detects contradictory history. | schema triggers; `ProposalBridgeStore.verify_bridge_integrity` | `test_proposal_bridge_schema.py::test_a_confirmation_must_carry_the_authorization_derived_from_its_grant`, `::test_a_consumption_must_fall_inside_the_validity_window_and_pair_with_its_link`; `test_proposal_bridge_store.py::test_integrity_verification_detects_contradictory_grant_history`; `test_grant_audit_chain.py` (12 tests) | VERIFIED |
| T18 | Provenance loss | Foreign keys from proposal to artifact; the proposal and artifact digests are recomputed on load; ineligible artifacts are refused (E1–E8). | `ai_proposal_mapping.py`; `proposal_bridge.py` | `test_ai_proposal_mapping.py` eligibility tests (`test_e*`); `test_proposal_review.py::test_a_tampered_proposal_is_never_reviewed`; `test_proposal_bridge_store.py::test_a_corrupted_proposal_is_never_returned`; 7H flagship audit join back to the source interaction | VERIFIED (no cryptographic proof that rows were never copied between databases: limitation L5) |
| T19 | AI_DRAFT bypass via the Phase 4 in-memory path | Every Phase 4 request entry point refuses AI_DRAFT. | `baec_app/application` Phase 4 path | `test_phase7c_boundaries.py::test_every_phase4_request_entry_point_refuses_ai_draft`, `::test_request_from_proposal_refuses_a_forged_ai_draft_before_any_dispatch`; `test_ai_proposal_mapping.py::test_a_mapped_ai_draft_can_never_enter_the_phase4_request_or_approval_path` | VERIFIED |
| T20 | Rewritten or paraphrased evidence | Every selection must be an exact contiguous span of the proposal's own persisted interaction. | `proposal_review.py` | `test_proposal_review.py::test_every_selection_is_rechecked_against_the_stored_source` (rewritten, case, paraphrased, summarized), `::test_text_from_another_stored_interaction_is_refused`; `test_human_normalization.py::test_selected_excerpts_are_never_joined_into_one_token_stream`; 7H evidence matrix (paraphrased, case-altered, another interaction) | VERIFIED |
| T21 | Normalization changes a threshold | `baec-human-normalization-validation/v1`: same-excerpt successive filters; 20 closed codes. | `human_normalization.py` | `test_human_normalization.py` (172 tests); 7H `test_phase7_adversarial.py::test_a_number_from_one_excerpt_and_a_comparator_from_another_never_combine_end_to_end`, `::test_two_expressions_each_supported_by_their_own_excerpt_pass_and_confirm_through_mcp` | VERIFIED |
| T22 | Fixture artifacts presented as real model output | Fixtures are FIXTURE-prefixed, test-only, and never committed as databases; seed and demo sources contain no AI provenance. | `tests/*_builders.py`; repository contents | `test_phase7c_boundaries.py::test_no_database_file_is_part_of_the_repository`, `::test_no_production_or_seed_source_can_introduce_ai_provenance`; 7H `test_phase7_adversarial.py::test_no_database_runtime_report_or_key_material_is_part_of_the_repository`, `::test_the_prototype_banner_and_synthetic_wording_are_present_and_no_outreach_capability_exists` | VERIFIED |

**Residual risks the design lists as not fully solvable** (design §18; no threat IDs are assigned there, and none are invented here):

| Residual risk (design wording) | Status |
|---|---|
| Real-world identity: `actor_label` is self-asserted | ACCEPTED PROTOTYPE LIMITATION |
| Local process with write access | ACCEPTED PROTOTYPE LIMITATION |
| Same-process code | ACCEPTED PROTOTYPE LIMITATION |
| Grant id disclosure | ACCEPTED PROTOTYPE LIMITATION |
| Automation bias and persuasive injected text | ACCEPTED PROTOTYPE LIMITATION |
| Clock manipulation on the local machine | ACCEPTED PROTOTYPE LIMITATION |

---

## 5. Research Contract traceability: RC-31 to RC-34

The direction is **research requirement → implementation boundary**, never implementation test → research finding. All four clauses are labeled IMPLEMENTATION in the contract. The contract text is quoted exactly and is unchanged.

### RC-31. Authoritative changes require human authorization (IMPLEMENTATION)

> Confirming a BAEC, approving monitoring, recording a review, and changing account state all require human authorization.
>
> In the Phase 1 domain layer, `HumanAuthorization` expresses this as a domain requirement only. It is **not** the security boundary. Later application, service, and MCP server code must establish that the authorization originated from an actual human action. A value produced by a model is never proof of human approval.
>
> A state-change authorization names its destination state and is valid only for that destination. It is not bound to the originating state. This is an implementation clarification.

- **What Phase 7 implements.** For confirming a BAEC from an AI proposal: the authorization must originate from the explicit human page action that issues a persisted, bound, single-use grant; `authority.authorize_grant` builds the `HumanAuthorization` only from that persisted grant (actor, issuance time, action, subject). The MCP tool accepts only a grant id; model-supplied actor, subject, content, or state fields are rejected. Review acceptance and rejection are recorded only through the human review service, with a reviewer label.
- **Code boundary.** `review_page.py` (the only production caller of `authorize_confirmation`); `ProposalAuthorizationService`; `AuthorizationGrantStore` (issuance) vs `GrantExecutionStore` (execution only); `authority.authorize_grant`; `ConfirmationExecutionService`; `mcp_write/server.py` (`ConfirmBaecArguments`).
- **Tests.** `test_review_page_authorization.py::test_an_explicit_authorize_click_issues_exactly_one_grant_and_confirms_nothing`, `::test_render_and_accept_issue_no_grant`; `test_grant_audit_chain.py::test_a_valid_confirm_baec_grant_yields_exactly_its_own_authorization`, `::test_no_caller_can_substitute_the_actor_time_or_subject`; `test_mcp_write_tool.py::test_caller_supplied_authority_is_rejected_at_the_schema_not_ignored`; 7H `test_phase7_adversarial.py::test_an_ai_draft_alone_cannot_confirm`, `::test_an_accepted_review_alone_cannot_confirm`, `::test_a_valid_grant_is_required_and_only_the_human_authorization_path_issues_one`; 7H flagship.
- **Limitation / non-coverage.** "Actual human action" is established as traversal of the prototype's human-interaction path, not authenticated identity; actor labels are self-asserted. Approving monitoring and changing account state are not part of Phase 7. The destination-state clause is not exercised: Phase 7 issues no state-change authorization (grants are CHECKed to `CONFIRM_BAEC`, and confirmations require `target_state IS NULL`).

### RC-32. Five categories, never collapsed (IMPLEMENTATION)

> | Category | Meaning |
> |---|---|
> | BUYER FACT | What the buyer actually said. |
> | SELLER OBSERVATION | What the human seller documented. |
> | EXTERNAL EVIDENCE | What a later authorized source reports. |
> | AI INFERENCE | How AI interprets relationships among the above. |
> | UNKNOWN | What has not been established. |

- **What Phase 7 implements.** All AI output (attributed speaker, suggested excerpts, normalization, hypotheses, uncertainties) is carried and displayed as AI_INFERENCE. A selection becomes BUYER_FACT or SELLER_OBSERVATION only by the human's explicit assertion; a buyer exact statement requires a BUYER_FACT selection with identical text.
- **Code boundary.** `proposal_review.py` (`EVIDENCE_PROVENANCE`, `EvidenceSelection`, `AiSuggestedExcerpt.status`, `AiCriterionHypothesis.status`).
- **Tests.** `test_proposal_review.py::test_only_buyer_fact_or_seller_observation_may_be_asserted`, `::test_the_ai_speaker_never_sets_provenance`, `::test_an_exact_seller_observation_text_is_refused_as_the_buyer_exact_statement`; 7H `test_phase7_adversarial.py::test_favorable_ai_suggestions_alone_yield_no_buyer_fact_findings_acceptance_grant_or_baec` and the evidence matrix.
- **Limitation / non-coverage.** In Phase 7 a human may assert only BUYER FACT or SELLER OBSERVATION for a selected excerpt. EXTERNAL EVIDENCE is not used (Phase 7 has no external sources). UNKNOWN appears as a criterion finding, not as an evidence provenance.

### RC-33. Source evidence is immutable (IMPLEMENTATION)

> The buyer's exact statement, source excerpt, source interaction, and capture time are never silently overwritten. AI-derived normalization is stored separately and never replaces them. Corrections use an auditable amendment.

- **What Phase 7 implements.** Source interactions, Phase 6 artifacts, AI proposals, and review revisions are append-only and never updated. Evidence is selected verbatim. The final normalization (AI value and human final value) is stored in the review revision only; `baec_records.normalized_*` stay NULL. An edit is a new immutable revision, never an in-place change.
- **Code boundary.** append-only triggers on the source, Phase 6, and bridge tables; `proposal_review.py`; the confirmed-record factory and `insert_confirmed_record`.
- **Tests.** `test_proposal_bridge_schema.py::test_no_bridge_row_can_be_updated_deleted_or_replaced`; `test_proposal_review.py::test_an_edit_appends_revision_2_and_never_changes_revision_1`; `test_review_page.py::test_an_edit_creates_revision_2_and_revision_1_never_changes`; 7H flagship (source, artifact, proposal, and revision rows byte-identical; `normalized_*` NULL).
- **Limitation / non-coverage.** "Corrections use an auditable amendment": Phase 7 implements **no amendment workflow**. A confirmed BAEC cannot be corrected through Phase 7, and one AI lineage yields at most one confirmed BAEC, so a replacement BAEC cannot be used as a substitute for an amendment.
- **Conclusion.** RC-33 is **not fully implemented** by Phase 7. Phase 7 implements immutable original evidence and immutable proposal/review history. The auditable evidence-amendment workflow required for corrections is not implemented in Phase 7 and remains an explicit limitation (L6).

### RC-34. Leaving Active Opportunity (IMPLEMENTATION)

> `ACTIVE_OPPORTUNITY` means the buyer is currently evaluating. V0.1 therefore requires `BUYER_FACT` or `SELLER_OBSERVATION` evidence that the buyer is no longer evaluating before an account moves from `ACTIVE_OPPORTUNITY` to `CONDITIONALLY_DORMANT` or `NO_PLAUSIBLE_PATH`. This evidence-form requirement is a software guard that preserves the meaning of the three states. It is not a requirement of the manuscript.

- **What Phase 7 implements.** Nothing new. RC-34 governs an account-state transition, and Phase 7 exposes no account-state transition. RC-34 is therefore not exercised by Phase 7, and no Phase 7 mutation targets it. Its existing enforcement (Research Contract §15) is unchanged.
- **Code boundary.** None in Phase 7. Phase 7's relevant constraint is that it cannot reach the state machine at all.
- **Tests.** Isolation only: 7H `test_phase7_adversarial.py::test_no_phase7_module_can_call_or_persist_a_state_transition_dormancy_or_staleness_change`; 7H `test_phase7_end_to_end.py::test_the_mcp_execution_itself_issues_no_state_dormancy_or_staleness_sql`. The RC-34 rule itself remains covered by `tests/test_state_machine.py::test_rc34_leaving_active_opportunity_requires_non_evaluation_evidence`.
- **Limitation / non-coverage.** Not covered by Phase 7 by design.

---

## 6. Other Research Contract clauses implemented or constrained by Phase 7

Not exhaustive and not a manuscript validation. Each row is research requirement → implementation boundary.

| Clause (label) | Phase 7 boundary | Evidence |
|---|---|---|
| RC-01, RC-02 (DEFINITIONAL) | The human records a finding for each of C1–C4, each with human-selected evidence; AI hypotheses are never findings. | `test_proposal_review.py::test_all_four_criterion_findings_are_required`, `::test_finding_evidence_must_be_selected_and_met_needs_evidence`; 7H AI-authority test |
| RC-03, RC-04 (DEFINITIONAL) | Nothing records or returns intent, commitment, purchase, or opportunity; the MCP result is confirmation metadata only. | `test_mcp_write_tool.py::test_the_description_is_narrow_and_makes_no_buyer_claim`, `::test_the_success_result_carries_metadata_only` |
| RC-05 (DEFINITIONAL) | UNKNOWN is a permitted C1 finding; such a review may be accepted but is not confirmable. | 7H `test_phase7_adversarial.py::test_a_human_may_accept_a_non_confirmable_review_but_no_grant_and_so_no_mcp_authority_follows[c1-unknown]` |
| RC-10, RC-11 (IMPLEMENTATION) | Origin and elicitation mode are explicit human decisions; `SELLER_SEEDED` classifies `NOT_BAEC` and receives no grant. | 7H `…[seller-seeded]`; `test_proposal_review.py::test_the_revision_content_is_exactly_the_approved_authoritative_shape` |
| RC-12, RC-13 (IMPLEMENTATION) | The locked classifier runs at accept, at grant issuance, and again inside the execution transaction; only `CONFIRMED_BAEC` receives a grant. | `test_proposal_authorization.py::test_issuance_recomputes_the_classification_and_never_trusts_the_preview`, `::test_execution_reruns_normalization_and_classification`, `::test_a_non_confirmable_accepted_review_never_receives_a_grant` |
| RC-15 (IMPLEMENTATION) | No score, probability, confidence, or ranking exists in the review content, the grant, or the MCP wire contract. | pinned shapes: `test_proposal_review.py::test_the_revision_content_is_exactly_the_approved_authoritative_shape`; `test_mcp_write_tool.py::test_the_advertised_input_and_output_schemas_are_pinned` |
| RC-17, RC-18 (PROPOSED / IMPLEMENTATION) | Stringency is an explicit human decision whose verbatim text must occur in a selection; the final normalization may not change a retained number, kind, currency, unit, or comparator. | `test_proposal_review.py::test_stringency_is_an_explicit_decision`; `test_human_normalization.py`; 7H normalization tests |
| RC-19 (PROPOSED) | `buyer_role` is always `None` on Phase 7 BAECs; representational authority stays deferred. | `test_proposal_review.py::test_buyer_role_is_always_none_and_cannot_be_supplied`; 7H flagship |
| RC-20, RC-21, RC-22, RC-24 (MANAGERIAL / IMPLEMENTATION) | Untouched; no Phase 7 path reaches the state machine's write side. | 7H isolation tests (§7) |
| RC-27 (MANAGERIAL) | Not engaged: Phase 7 has no signals and no Active Opportunity path; a confirmed BAEC is not an opportunity. | 7H isolation tests; flagship (`accounts` unchanged) |
| RC-29 (MANAGERIAL) | New confirmed BAECs are `CURRENT` through the locked factory; staleness is never changed. | 7H flagship (`staleness_status = 'CURRENT'`); 7H static scan (no `staleness_status =`, no `UPDATE baec_records`) |
| RC-30 (MANAGERIAL) | No outreach, messaging, or contact path exists. | 7H `test_phase7_adversarial.py::test_the_prototype_banner_and_synthetic_wording_are_present_and_no_outreach_capability_exists` |
| §13 (claims never made) | Phase 7 claims no validation, prediction, sales lift, production security, or accuracy. | this document; page banner |

The Research Contract wording proposed in design §19 remains **NOT APPLIED**. `docs/RESEARCH_CONTRACT.md` is unchanged.

---

## 7. State-machine isolation (deterministic software verification)

- **Static, across every Phase 7 production module** (mapper, normalization, review, authorization, grant store, bridge store, review page, the three `mcp_write` modules): no call to any Active Opportunity, Conditionally Dormant, or No Plausible Path transition (domain, repository, or facade form), no dormancy-judgment call, no non-confirmed classification save, no import of the state machine, account-state, or dormancy services, and no `UPDATE baec_records`, `UPDATE accounts`, transition or dormancy `INSERT`, or `staleness_status =` SQL. (`test_phase7_adversarial.py::test_no_phase7_module_can_call_or_persist_a_state_transition_dormancy_or_staleness_change`)
- **Runtime, by SQLite trace**: every statement the human review and authorization path executes, and every statement the MCP-composed executor executes during a successful confirmation. The executor's only `INSERT` targets are the confirmation tables, and it executes no `UPDATE`, `DELETE`, or `REPLACE`.
- **Before/after**: `accounts` is byte-identical across the flagship chain, and the transition, dormancy, and state-evidence tables stay empty.

Phase 7 confirmation is not opportunity activation.

---

## 8. Capability graph (deterministic software verification)

| Property | Evidence |
|---|---|
| Phase 5 MCP cannot write | query-only connection; a write is refused by SQLite (7H `test_phase5_mcp_cannot_write`; Phase 5 suite) |
| Phase 7G MCP can execute only an existing grant | one tool, `grant_id` only; `issuance_capabilities(runtime) == []` (7G boundary suite; 7H) |
| The MCP-reachable executor cannot issue grants | it holds only `GrantExecutionStore` (`get_grant`, `lifecycle`, `record_confirmation`); negative controls fail when the issuance-capable store, the issuance service, a bound `authorize_confirmation` or `add_grant`, the `GrantRecord` class, or a stored grant is attached |
| MCP cannot reach `ProposalAuthorizationService` | no instance or class in the walked graph from the runtime and from `main()`'s runtime |
| Streamlit can issue a grant but cannot execute it | the page imports only `AuthorizationRefused` and `ProposalAuthorizationService`; it never calls `execute`, `add_grant`, `record_confirmation`, or `insert_confirmed_record`, and never imports `mcp_write` |
| AI modules and the mapper cannot issue grants | `baec_app/ai/**`, `ai_proposal_mapping.py`, and `human_normalization.py` import no grant, execution, or write-server module and call no issuance or execution operation |

Scope: arbitrary Python code with unrestricted local import, filesystem, or database access is outside this prototype's sandbox claim. The claim covers what the composed servers hold.

---

## 9. Phase 5 and Phase 7 MCP servers

| | Phase 5 MCP Core (`baec_app/mcp`) | Phase 7 MCP Write (`baec_app/mcp_write`) |
|---|---|---|
| Resources | 8 (2 fixed + 6 templates) | 0 |
| Tools | 4 read-only preview tools | 1: `confirm_baec(grant_id)` |
| Prompts | 0 | 0 |
| Connection | `mode=ro`, `PRAGMA query_only` | writable, existing current-schema file only, never created or migrated |
| Annotations | `readOnlyHint=true` | `readOnlyHint=false`, `destructiveHint=false`, `openWorldHint=false`; `idempotentHint` unset |
| Input | per-tool closed models | exactly `{grant_id}`, pattern `^grant_[0-9a-f]{64}$`, `additionalProperties: false` |
| Can issue a grant | no | **no** |

The write server cannot issue, extend, alter, or supersede a grant. It executes one previously issued grant, once.

---

## 10. Phase 6 model qualification (preserved, unchanged)

From `docs/PHASE6_FINAL_VERIFICATION.md`, unchanged by Phase 7:

- Phase 6 is "a successful engineering closeout with a documented model-qualification limitation, **not** a successful model qualification". Operational verification: **PASS**. Behavioral eligibility: **FAIL**.
- "`claude-sonnet-5-5` qualified as default model: **NO**." "No model is designated as the product default on the basis of Phase 6."

Phase 7 consumes valid persisted artifacts. It does not reverse, override, or re-evaluate the Phase 6 behavioral-eligibility result, makes no live AI call, and qualifies no model.

---

## 11. Limitations register (accepted prototype limitations)

| # | Limitation |
|---|---|
| L1 | Synthetic data only. Every Phase 7 test database is ephemeral and test-only. |
| L2 | No authenticated reviewer identity. |
| L3 | Actor labels are self-asserted. |
| L4 | Arbitrary local Python, process, or database access is outside the sandbox claim; such access could insert rows, run raw SQL, or drop triggers. |
| L5 | No cryptographic proof that rows were never copied between SQLite databases. |
| L6 | No evidence-amendment workflow (RC-33's "auditable amendment" is not implemented in Phase 7). |
| L7 | No account-state transition in Phase 7. |
| L8 | No dormancy or staleness workflow in Phase 7. |
| L9 | No outbound buyer contact. |
| L10 | No automatic opportunity creation. |
| L11 | No default AI model qualified by Phase 6. |
| L12 | No new live AI invocation in Phase 7. |
| L13 | Clock manipulation on the local machine is not defended against. |
| L14 | Automation bias: the design keeps AI suggestions separate and requires explicit decisions but cannot guarantee independent human judgment. |
| L15 | A grant id passes through the human's MCP client and may persist in a chat log; single use, supersession, and the 15-minute window limit, but do not remove, that exposure. |
| L16 | Refused executions leave no durable failed-attempt audit record (design §14). |
| L17 | Evidence provenance a human can assert is limited to BUYER FACT and SELLER OBSERVATION; `buyer_role` is always `None`. |
| L18 | Importing the locked executor loads pure deterministic Phase 6 verification and grounding modules transitively (approved); `mcp_write` imports no AI module directly and invokes no model. |
| L19 | The mutation runners and definitions are not tracked in the repository (§13). |

---

## 12. Test inventory (actual collected counts)

| Increment | Principal test files (collected tests) | Major invariant categories | Tests | Mutation definitions |
|---|---|---|---|---|
| 7C | `test_proposal_bridge_schema.py` (89), `test_proposal_bridge_store.py` (40), `test_phase7c_boundaries.py` (32) | schema v7, append-only bridge, grant/supersession/confirmation triggers, AI_DRAFT refusal on the Phase 4 path | 161 | 41 |
| 7D | `test_ai_proposal_mapping.py` (56), `test_ai_proposal_mapping_contract.py` (13), `test_ai_verification.py` (20) | eligibility E1–E8, deterministic mapping, snapshot digest, A9 verification façade | 89 | 30 |
| 7F-A | `test_human_normalization.py` (172) | 20 closed codes, same-excerpt support, threshold preservation | 172 | 23 |
| 7E | `test_proposal_review.py` (49), `test_review_page.py` (25), `test_phase7e_boundaries.py` (10) | verbatim evidence, human provenance, exact buyer statement, immutable revisions, inert rendering, AppTest | 84 | 24 |
| 7F-B | `test_proposal_authorization.py` (38), `test_grant_audit_chain.py` (12), `test_review_page_authorization.py` (8), `test_phase7fb_boundaries.py` (6) | grant issuance, TTL, supersession, precedence, atomic execution, audit chain, AppTest | 64 | 37 |
| 7G | `test_mcp_write_tool.py` (61), `test_mcp_write_boundary.py` (35), `test_mcp_write_stdio.py` (18) | one-tool surface, closed schema, refusal mapping, capability graph, stdio lifecycle | 114 | 34 |
| 7H | `test_phase7_end_to_end.py` (3), `test_phase7_adversarial.py` (40) | full chain, human authority, AI authority, evidence, normalization, lifecycle, tool authority, capability graph, isolation, injection, partial transactions, public-prototype checks | 43 | none (see §13) |
| **Total** | | | **727** | **189** |

Each increment also updated earlier guard tests in place, keeping their test IDs; those are counted under their own files, not here.

---

## 13. Mutation verification

**Statement.** 188 of 189 historical mutation definitions still applied, and all 188 were caught. Historical 7F-B mutation G23 no longer applied, because its original anchor was replaced by the Phase 7G grant-store capability split. A re-anchored equivalent G23 mutation against the current code was caught separately. The mutation runner and mutation definitions are session scratch artifacts and are not tracked in the repository, so these mutation reruns are not reproducible from repository HEAD or a `git archive` alone.

Re-run in Phase 7H against `eb4b61b` plus the 7H tests (the 7H changes add tests and documentation only):

| Set | Definitions | Applied | Caught |
|---|---|---|---|
| 7C | 41 | 41 | 41 |
| 7D | 30 | 30 | 30 |
| 7F-A | 23 | 23 | 23 |
| 7E | 24 | 24 | 24 |
| 7F-B | 37 | 36 | 36 |
| 7G | 34 | 34 | 34 |
| **Total** | **189** | **188** | **188** |

No mutation survived. One 7F-B definition, **G23** ("execution holds a broad repository"), **was not applied**: its anchor text was the executor's former `AuthorizationGrantStore(connection)` line, which the 7G capability split replaced with `GrantExecutionStore(connection)`. The original definition was left unchanged. A re-anchored copy (G23′, the same mutation on the current line) was run separately and was **caught** by its intended test, `test_phase7fb_boundaries.py::test_the_executor_holds_no_broad_repository_and_offers_only_execute`.

**Reproducibility caveat.** The mutation runner (`mutate.py`) and every mutation set (`mutate_7c.py` … `mutate_7g.py`) exist only in the local session scratch directory. **They are not tracked in the repository and are not contained in a `git archive HEAD` export.** These results are therefore not reproducible from repository HEAD alone. Each mutation is applied to a fresh scratch copy of the working tree; it counts as caught only when both its named (intended) test and the full suite fail.

No new 7H mutation set was created: the 7H tests compose invariants that the 7C–7G sets already mutate, except T16's COMMIT-time and per-write faults, which the new 7H tests inject directly.

---

## 14. Final gates

All offline, with no live AI test executed (the one `live_claude` test is deselected by default) and no network access.

| Gate | Result |
|---|---|
| 7H end-to-end and adversarial | 43 passed |
| 7G | 114 passed |
| 7F-B (including AppTest) | 64 passed |
| 7E (including AppTest) | 84 passed |
| 7F-A | 172 passed |
| 7D | 89 passed |
| 7C | 161 passed |
| AI boundary | 99 passed |
| Application and authority boundaries | 224 passed |
| Phase 5 MCP | 583 passed |
| **Complete offline suite** | **5652 passed, 1 deselected, 0 failed, 0 skipped, 0 xfailed** |

All 5609 test IDs collected before Phase 7H are still present; Phase 7H adds 43.

---

## 15. Differences from the design's 7H plan

- The design listed `docs/PHASE7_TRACEABILITY.md`. The threat and Research Contract traceability is kept in this document (§4–§6) instead of a second file.
- The planned files `tests/test_phase7_adversarial.py` and `tests/test_phase7_end_to_end.py` exist as named.
- The planned end-to-end scenario (fixture artifact → map → review → grant → edit → supersession → new grant → MCP confirm → repeat refused) is `test_phase7_end_to_end.py::test_an_edit_supersedes_the_first_grant_and_only_a_new_human_authorization_confirms`; the flagship additionally drives review and authorization through the real page with AppTest.
- No production code changed in Phase 7H.
