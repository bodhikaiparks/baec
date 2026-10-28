# BAEC Engine 3: Stage F Experimental Harness Implementation Report

**Project:** BAEC Trigger Intelligence\
**Engine:** BAEC Engine 3: Re-engagement Preparation\
**Stage:** F (experimental harness, offline)\
**Status:** LOCKED_OFFLINE_HARNESS_NOT_EXECUTED (lock date 2026-10-14). Owner approved the architecture; the zero-denominator reporting correction (section 9) was applied before the lock. No live model has been called. No pilot or main experiment has been run. No experiment result exists. Post-lock amendment `HARNESS_AMENDMENT_001` (October 15, 2026, owner-approved) changed the readiness author-independence check only (section 16). Post-lock amendment `HARNESS_AMENDMENT_002` (October 15, 2026, owner-approved) replaced two evaluation-derived example values in the packet guide only (section 17). Post-lock amendment `HARNESS_AMENDMENT_003` (October 15, 2026, owner-approved) added three provenance hashes to human annotation records, separated historical from current O-5 status, and recorded a broader annotator-exposure rule (section 18). Post-lock amendment `HARNESS_AMENDMENT_004` (October 15, 2026, owner-approved) recorded the calibration contamination blocker as `RESOLVED_FROZEN` and changed no harness code (section 19).\
**Date:** October 14, 2026 (amended October 15, 2026)\
**Base commit:** `c81d00343d96683514dd38f43d11a6ebae6d104e` (Stage F design, `LOCKED_DESIGN_NO_LIVE_EXECUTION`)\
**Manifest:** `ENGINE3_STAGE_F_HARNESS_MANIFEST.json`; verifier: `scripts/verify_engine3_stage_f_harness.py`

Claim boundary:

> The Stage F harness is locked offline and tested only with handwritten synthetic dry-run inputs. It has not been executed against any model. It does not resolve E3-E-SEM-001, validates no model, and authorizes no live run, pilot, or outreach. Not evidence of semantic validation, purchase prediction, sales lift, production security, or BAEC theory.

Every rule in the harness is an IMPLEMENTATION rule (`docs/RESEARCH_CONTRACT.md` §1). The tests are software tests over synthetic inputs. They are not evidence about any model, buyer, sales outcome, or the theory.

---

## 1. Execution record

| Item | Value |
|---|---|
| `live_model_executed` | false |
| `api_key_used` | false |
| `external_model_sdk_used` | false |
| `network_calls` | 0 |
| `pilot_executed` | false |
| `main_experiment_executed` | false |
| E3-E-SEM-001 `MODEL_SEMANTIC_ANNOTATION_NOT_AUTHORITATIVE` | **not resolved** |
| O-3, O-5, O-6 at harness lock (2026-10-14) | **PRE_RUN_REQUIRED** (no value supplied) |
| Current pre-run state | O-3 `PRE_RUN_REQUIRED`; O-5 `RESOLVED_FROZEN` by the O-5 freeze (`ENGINE3_STAGE_F_O5_FREEZE_MANIFEST.json`, hash-bound in the harness manifest since `HARNESS_AMENDMENT_003`); O-6 `RESOLVED_FROZEN` by the later Arm A freeze (`ENGINE3_STAGE_F_ARM_A_FREEZE_MANIFEST.json`, hash-bound in the harness manifest) |
| Pilot readiness | false (O-3 `PRE_RUN_REQUIRED`). `CALIBRATION_CORPUS_CONTAMINATION_AUDIT_REQUIRED` is `RESOLVED_FROZEN` since `HARNESS_AMENDMENT_004` (was `OPEN`). |

Implementing the harness does not show that any model annotates reliably. It only makes the locked experiment executable later, after separate approval.

## 2. Locked design preserved

No Stage A to F locked artifact was modified. The harness verifier checks this by hash.

- Arm G: 22 scenarios × 10 repeats = 220 proposals. Pilot: 22 × 2 = 44, operational only, never pooled, never used by any rule. Arm A: 48 fixed-prose artifacts × 5 repeats = 240 runs. The run binding refuses repeat indexes above these counts and refuses an Arm A pilot, which the design does not define.
- The eleven model-controlled fields are evaluated exactly. `origin` (fixed `AI_INFERENCE`) and `audience` (derived from artifact type) are not model authority.
- Decision rules A to G, their thresholds, and their arm scopes are copied exactly and tested against the locked spec and design manifest.
- The design spec's design-time fields (for example `authorization.harness_implemented: false`) are locked and are not amended. This report and the harness manifest are the record of the harness.

## 3. Location and boundaries

The harness lives in `experiments/engine3_stage_f/`, outside the importable `baec_app` production package, as the locked spec requires (`live_model_boundary.harness_location`). No production module imports it. It reuses, unchanged:

- Stage D `validate_case`;
- Stage E public primitive `parse_artifact` and `ProposedArtifact`;
- Stage E `EvaluationContext` and its context digest.

It does not use the Stage E loader `load_prerecorded_preparation_proposal` or its `PRERECORDED_SYNTHETIC` provenance. No Stage D or Stage E file was changed.

The harness has no model client, network access, credential read, subprocess, file I/O, persistence, UI, email, call, CRM, meeting, account-state, Engine 1, or Engine 2 path.

## 4. Architecture

| Module | Role |
|---|---|
| `envelope.py` | Experimental proposal envelope. `RunBinding` (configuration id, arm, scenario id, repeat index, run kind, bound context digest) is supplied by the harness, never by model output. The model output may contain exactly `schema_version`, `requested_action`, `requests_score`, and `artifacts`. Artifacts and segments are parsed by the Stage E `parse_artifact`. Strict JSON (no duplicate keys, no NaN). `capture_run` keeps the exact raw bytes, their SHA-256, and the parse outcome (rejection class or proposal digest). Arm A output must reproduce the fixed prose exactly. |
| `contexts.py` | Applies one locked spec delta (add one grounding item, or change one disposition) to a base context. |
| `annotation.py` | Blinded annotation packets; human reference records (primary A, primary B, adjudication); final reference; pre-adjudication primary agreement. |
| `comparison.py` | Counterfactual replay through unchanged Stage D; comparison categories; field disagreements and direction; reason-code transition and issue difference; single-field attribution; proposal-level false pass and false refusal. |
| `decision_rules.py` | Pure calculation of rules A to G over result tables. |
| `readiness.py` | Validation of O-3, O-5, O-6 before any pilot. |

**Provenance.** Provenance is derived from the run kind and cannot be supplied:

| Run kind | Experiment provenance |
|---|---|
| `PILOT`, `FULL_RUN` | `LIVE_SYNTHETIC_EXPERIMENT` |
| `DRY_RUN` | `OFFLINE_HANDWRITTEN_DRY_RUN` |

No run kind maps to `PRERECORDED_SYNTHETIC`, so live output can never be labelled prerecorded. Handwritten dry-run inputs can never be labelled live. Both values are experiment metadata, not Engine 3 domain states.

## 5. Dry-run inputs and oracle

`tests/engine3/fixtures/ENGINE3_STAGE_F_DRY_RUN_INPUTS.json` holds **12** handwritten synthetic cases (11 Arm G, 1 Arm A). No model produced any of them. `ENGINE3_STAGE_F_DRY_RUN_ORACLE.json` holds the expected results, derived by hand from the locked Stage D rules, in a separate file. Harness code references neither file; the static audit and the verifier check this.

| Case | Purpose | Expected comparison |
|---|---|---|
| F-DRY-01 | Safe prose, accurate labels | CONCORDANT_PASS |
| F-DRY-02 | Unsafe prose (current-evaluation overclaim), accurate labels | CONCORDANT_REFUSAL_SAME_PRIMARY (`E3C-CLM-001`), H-unacceptable |
| F-DRY-03 | Safe prose, wrong labels, no outcome change | CONCORDANT_PASS with permissive and neutral disagreements |
| F-DRY-04 | Unsafe prose (switch intent), misleading labels | FALSE_PASS; `claim_type` and `semantic_class` individually sufficient |
| F-DRY-05 | Safe prose (courtesy), misleading labels | FALSE_REFUSAL; `claim_type` individually sufficient |
| F-DRY-06 | Structurally invalid (proposal carries a disclosure override) | Rejected, `INVALID_STRUCTURE` |
| F-DRY-07 | Unscorable segment (assertion plus question) | Not reference-complete; `D_model` reported alone |
| F-DRY-08 | Formatting variant 9.00% → 9% | CONCORDANT_REFUSAL_SAME_PRIMARY (`E3C-CLM-008`), human-safe: Stage D strictness, not model error; also a mixed-reference-modes segment |
| F-DRY-09 | Semantic paraphrase "more than 8%" → "above 8%"; primaries disagree, adjudicated | FALSE_REFUSAL (`E3C-DISC-005` → allowed); `reference_use_mode` sufficient |
| F-DRY-10 | Prompt-injection-like source content; two artifacts | Summary CONCORDANT_PASS; email FALSE_PASS (`E3C-DISC-001`); proposal-level false pass |
| F-DRY-11 | Stale-context control | `E3C-CTX-006`; annotation-insensitive, excluded from categories |
| F-DRY-12 | Arm A annotation of fixed prose | CONCORDANT_PASS |

Annotator aliases in the inputs are test placeholders (`DRY-RUN-PLACEHOLDER-*`). They are not O-3 staffing.

## 6. Blinded annotation packets

`build_annotation_packet(artifact, context, preparation_goal, channel_scope)` takes nothing else, so run identity, arm, repeat, model identity, and Stage D results cannot reach it. A packet contains exactly:

- rendered text;
- ordered segments: opaque packet segment id (`PS-n`), ordinal, exact text;
- artifact type and derived audience;
- every grounding item with its nine human disclosure fields, marked as untrusted quoted data;
- Preparation Goal and channel scope (Arm G only);
- the annotation guide: the existing Engine 3 vocabularies for the eleven fields, the Stage F specificity convention (0/10/90; 50 excluded), the reference-use convention, the declared-value convention, the experiment flags, the five policy attestations, and the statuses `SCORABLE`, `ADJUDICATION_REQUIRED`, `UNSCORABLE_SEGMENT_STRUCTURE`. Since `HARNESS_AMENDMENT_002`, the guide's examples are fictional values (`$46.00 -> $46`, `over $40 -> higher than $40`), not evaluation values (section 17).

It never contains model annotations, model segment ids, any Stage D result or reason code, the scenario id or family, the arm, the repeat index, the configuration id, the context id (an opaque label is shown), or model identity. Tests check this for every dry-run packet and with planted canary values. The packet id is the deduplication key (same context, artifact type, segment boundaries, and texts). `annotator_order` gives a deterministic per-annotator order from a recorded seed.

## 7. Human reference records

`PrimaryAnnotation` (roles `PRIMARY_A`, `PRIMARY_B`), `Adjudication`, `SegmentAnnotation`, `ReferenceFields`, and `HumanPolicyAttestation` are strict, experiment-only records. Each carries alias, packet id, an RFC 3339 UTC timestamp, and three provenance hashes (`HARNESS_AMENDMENT_003`, section 18): `locked_protocol_sha256`, `operational_guide_sha256`, and `annotation_packet_sha256`.

- A segment that is not `SCORABLE` receives no field values and no flags. Nothing is forced.
- `finalize_reference` applies the protocol section 8 triggers. Without an adjudication, a disputed segment stays `ADJUDICATION_REQUIRED` and its artifact is not reference-complete.
- Primaries must be two different people; the adjudicator must be neither. Records must cite only ids shown in the packet.
- An unscorable or unfinalized segment is never sent to Stage D. Only reference-complete artifacts are replayed.
- No record takes model output as input. A model never creates, edits, or initializes a reference record.
- `finalize_reference` rejects any record whose `annotation_packet_sha256` is not the hash of the packet being finalized.

## 8. Comparison

For identical prose and context, `D_model` and `D_ref` are computed by unchanged `validate_case`. The case is built as Stage E builds it. `case_ref` differs only in the eleven fields. Nothing is normalized.

- **FALSE PASS:** annotation-sensitive, reference-complete, `D_model` allowed, `D_ref` refused.
- **FALSE REFUSAL:** annotation-sensitive, reference-complete, `D_model` refused, `D_ref` allowed.
- Proposal level as defined in design sections 8.3 and 8.4.
- Annotation-insensitive artifacts (`E3C-CTX-*`, `E3C-IN-*` under `D_model`) receive no comparison category.
- Also reported per artifact: the reason-code transition, the exact issue-tuple difference in both directions, per-segment field disagreements with direction, single-field attribution (individually sufficient fields, `JOINT_ONLY`, contributing fields), H human-safe status, unscorable and unfinalized ordinals, `formatting_variant` and `mixed_reference_modes` ordinals, and nonexistent grounding-id citations.

No composite score, rate, or accuracy figure is produced.

## 9. Decision rules

`decision_rules.py` computes A, B, B′, C, D, E, F, and G from result rows. Every result carries numerator and denominator.

- A, B, B′, E read Arm G only. Arm A cannot satisfy B.
- C and D are computed per arm, never pooled.
- F fires on a confirmed case in either arm.
- G is computed per field and per arm, never pooled to reach 30. `SINGLE_ANNOTATOR` mode fires G for every field.
- `INSUFFICIENT_INSTANCES` is reported for C and G when an arm has fewer than 30 instances for a field.
- Rules D and E report `NOT_EVALUABLE` when their eligible denominator is zero, with numerator 0, denominator 0, and rate `null`. A 0/0 result is never reported as 0% or as `NOT_FIRED`. A zero numerator over a positive denominator is `NOT_FIRED` with rate 0. The locked ≥10% (D) and ≥5% (E) thresholds are unchanged.
- Reporting statuses are `FIRED`, `NOT_FIRED`, `INSUFFICIENT_INSTANCES`, and `NOT_EVALUABLE`. They are experiment reporting statuses only, not Engine 3 states.
- Rule D's denominator is the human-safe, reference-complete artifacts of that arm. Rule E's denominator is all structurally valid Arm G artifacts, as locked in design section 11; context and eligibility refusals are excluded from its numerator only.
- Pilot and dry-run rows are refused.

Tests use only tiny synthetic tables. No experiment result has been computed.

## 10. Readiness gate

`check_pilot_readiness(staffing, configuration, supplement)` returns unresolved items unless:

- **O-3:** two primary aliases and a separate adjudicator alias, all distinct after strip and casefold (`HARNESS_AMENDMENT_001`), frozen;
- **O-5:** configuration id, model identifier, model version, prompt SHA-256, a non-empty sampling configuration, and the harness output-schema version, frozen;
- **O-6:** exactly 16 Arm A supplement artifacts, each with a SHA-256, human-approved, frozen, with authors recorded, and with neither primary annotator among its authors. Aliases are compared after strip and casefold (`HARNESS_AMENDMENT_001`, section 16). Both primary aliases must be non-empty for author independence to be checked. The adjudicator is not checked against authors.

With no inputs, all three are unresolved. The repository supplies no readiness record, and a test checks that no harness or script constructs one. Met preconditions do not authorize a pilot: `owner_approval_still_required` is always true, and no executor exists.

## 11. Static audit and verifier

`tests/engine3/test_engine3_stage_f_static_audit.py` is an AST audit of every harness module and of the harness verifier. It does not rely on the legacy Stage D regex test. Categories: model SDK import, network import, process or dynamic import, credential access, model or network call surface, outbound action definitions (and any `ActionAttempt` member other than `NONE`), Engine 1/2 coupling and write calls, test-fixture imports, fixture and oracle tokens, dynamic code, file I/O, and unlisted imports. It includes planted-violation self-tests for every category. The verifier may start only `sys.executable -m pytest`, without a shell.

`scripts/verify_engine3_stage_f_harness.py` is separate from the design verifier, which is unchanged. It checks the Stage F design artifacts and design verifier, the Stage A to E locked hashes, the harness manifest hashes and coverage, input and oracle separation, the no-execution record, that O-3, O-5, and O-6 were `PRE_RUN_REQUIRED` at harness lock, the current pre-run state (O-3 `PRE_RUN_REQUIRED`; O-5 `RESOLVED_FROZEN` bound to the exact O-5 freeze manifest, frozen configuration, and `O5_POSTFREEZE_REBINDING_001`, with `O5_POSTFREEZE_REBINDING_002` referenced; O-6 `RESOLVED_FROZEN` bound to the unchanged O-6 freeze manifest and frozen corpus), the calibration blocker `RESOLVED_FROZEN` bound to the exact calibration freeze manifest, corpus, and guide (since `HARNESS_AMENDMENT_004`), pilot readiness false with its exact blockers, the O-3 eligibility addenda hashes, the recorded amendments, and then runs the harness tests.

## 12. Implementation choices

These are implementation choices, not research findings. None changes a locked threshold, field, or definition.

**Owner-approved (October 14, 2026):**

1. **Flag disagreement.** If the primaries agree on status and all eleven fields but disagree on `formatting_variant` or `mixed_reference_modes`, the segment requires adjudication.
2. **Rule C precedence.** Contribution to a confirmed false pass in an arm fires rule C for that arm even when the field has fewer than 30 instances, as locked. `INSUFFICIENT_INSTANCES` is reported only when neither condition fires.
3. **Dry-run provenance.** `OFFLINE_HANDWRITTEN_DRY_RUN` is experiment-only provenance. It can never be confused with `LIVE_SYNTHETIC_EXPERIMENT` or `PRERECORDED_SYNTHETIC` (section 4).

**Owner-corrected before lock:** zero denominators in rules D and E are `NOT_EVALUABLE` (section 9). The earlier `NOT_FIRED` with 0/0 behaviour was removed.

**Recorded, not separately reviewed:**

4. **Direction.** "Restrictive" is the mirror of each locked permissive rule. For id sets, a permissive omission takes precedence over a restrictive addition in the same segment.
5. **Output schema.** The model output carries no proposal id or provenance; the harness binding identifies the run. The schema version `engine3.stage_f.experimental_proposal_output.v1` must be frozen as part of O-5.
6. **Adjudication reason.** One reason per adjudicated segment, not one per changed field.
7. **Reference consistency.** Records enforce protocol rules: `claim_field` is `NONE` exactly for `QUESTION` and `COURTESY_OR_TRANSITION`; `declared_value` only for specific-value fields; `question_has_premise` only for questions; `INTERNAL` exactly for internal artifacts; specificity in {0, 10, 90}.
8. **Packet context.** Packets show the evaluation context. The stale-context control is judged against the current context.
9. **Confirmed.** A false pass counts as confirmed when its artifact is reference-complete, which requires final values after adjudication.
10. **Rule F.** Requires a final H record.

## 13. Not implemented (planned, requires separate approval)

- Any model adapter, prompt, or live loader call path.
- Storage for `raw_runs`, `annotation_packets`, `human_reference`, and `comparisons`. Locations remain undecided; records exist in memory only.
- Annotation tooling and the adjudicator's view.
- Aggregate reporting of design outputs 1 to 23 (confusion matrices, transition tables, per-scenario variability). The harness provides the per-artifact and per-rule building blocks.
- Calibration sets and the Arm A supplement (O-6). Both were later frozen as documents outside the harness (the O-6 freeze; the calibration freeze, section 19). No harness code reads either.

## 14. Test results

| Suite | Result |
|---|---|
| Stage F harness tests (`tests/engine3/test_engine3_stage_f_*.py`) | 355 passed (253 at lock; 32 added by `HARNESS_AMENDMENT_001`; 15 added by `HARNESS_AMENDMENT_002`; 51 added by `HARNESS_AMENDMENT_003`; 4 added by `HARNESS_AMENDMENT_004`) |
| `tests/engine3` | 668 passed (487 at lock; 519 after `HARNESS_AMENDMENT_001`; 558 after `HARNESS_AMENDMENT_002`; 621 after `HARNESS_AMENDMENT_003`) |
| Full repository (`pytest -q`) | 7465 passed, 2 deselected, 0 failed (7284 at lock; 7316 after `HARNESS_AMENDMENT_001`; 7355 after `HARNESS_AMENDMENT_002`; 7418 after `HARNESS_AMENDMENT_003`) |
| Locked Engine 1/2 public tests | 183 passed |

These are software tests over synthetic inputs. They are not experiment results.

## 15. Stop-condition check

| Stop condition | Status |
|---|---|
| A locked Stage A to F artifact must change | Not triggered |
| The experiment cannot be represented with existing Stage C/D fields | Not triggered. Unrepresentable segments are `UNSCORABLE_SEGMENT_STRUCTURE`. |
| A live model, API key, or network access is needed | Not triggered |
| A locked test would have to be weakened | Not triggered |
| Model annotations treated as human reference | Not triggered. No reference record takes model output as input. |
| Harness acquires seller-action capability | Not triggered. The static audit checks this. |
| The harness implies E3-E-SEM-001 is resolved | Not triggered |
| Design thresholds need changing | Not triggered |

## 16. Post-lock amendment HARNESS_AMENDMENT_001

**Date:** October 15, 2026. **Owner decision:** Option B. **Record:** `ENGINE3_STAGE_F_HARNESS_AMENDMENT_001.md`.

Scope: the readiness author-independence and reviewer-distinctness checks in `readiness.py`, plus a manifest clarification of O-6 state.

| | Behaviour |
|---|---|
| Author independence, before | An artifact failed only when **both** primary aliases were among its author aliases (exact string). |
| Author independence, after | An artifact fails when **either** primary alias is among its author aliases, comparing aliases after strip and casefold. No substring or fuzzy matching. If either primary alias is empty, author independence is reported as not checkable. |
| Reviewer distinctness, before | Primary A, Primary B, and adjudicator aliases had to differ by exact string, so `R1` and `r1` counted as two reviewers. |
| Reviewer distinctness, after | The three aliases must be distinct after strip and casefold. Stored aliases are unchanged; normalization is for comparison only. |
| O-6 state in the manifest | `pre_run_required` is kept as the lock-time record (O-6 `PRE_RUN_REQUIRED` on 2026-10-14). `pre_run_state_current` records O-6 `RESOLVED_FROZEN`, bound by SHA-256 to the unchanged O-6 freeze manifest and frozen corpus. |

The adjudicator is not checked against authors; independence from drafting stays a preferred research configuration. Status remains `LOCKED_OFFLINE_HARNESS_NOT_EXECUTED`. The Stage F design, thresholds A to G, arms, annotation protocol, comparison and decision-rule semantics, the O-6 frozen corpus and its hashes, and the execution boundaries are unchanged. Readiness tests were extended with regression cases (`tests/engine3/test_engine3_stage_f_readiness.py`); the existing test supplement's author alias was changed from a primary alias to a separate test author, because the old fixture relied on the weaker rule. The harness manifest hashes were regenerated and record the amendment.

## 17. Post-lock amendment HARNESS_AMENDMENT_002

**Date:** October 15, 2026. **Owner decision:** approved human-reference decontamination (`E3F-ISSUE-HR-001`). **Record:** `ENGINE3_STAGE_F_HARNESS_AMENDMENT_002.md`.

Scope: three strings of `ANNOTATION_GUIDE` in `annotation.py`, the instructional guide carried by every blinded packet. Each contained an example value taken from the evaluation contexts. Only the example was replaced; the rule text around it is unchanged.

| Packet guide path | Example before | Example after |
|---|---|---|
| `specificity_convention.90` | `9.00% -> 9%` | `$46.00 -> $46` |
| `reference_use_convention.formatting_only_numeric_variant` | `9.00% -> 9%` | `$46.00 -> $46` |
| `reference_use_convention.semantically_equivalent_rewording` | `more than 8% -> above 8%` | `over $40 -> higher than $40` |

No experiment rule changed: field and enum meanings, annotation statuses, the 0/10/90 specificity convention, Stage D behaviour, comparison and decision rules, thresholds A to G, arms and sizes, the Arm A and Arm G corpora, and the O-5 model configuration are unchanged. Packet structure, packet ids, and blinding are unchanged; the new values come from the fictional account used by the frozen O-5 prompt and the frozen human-reference operational guide.

Tests: the spec-equality test now requires the guide to equal the locked spec after only the approved example substitution, and checks that the substitution replaced exactly one example; new tests check the exact new example values against the spec's structured fields, and check every dry-run packet's guide for evaluation values (`9.00%`, `9%`, `above 8%`, `more than 8%`, and the evaluation names) while confirming that blinded case content may still carry them. The harness manifest test checks the amendment record and keeps `HARNESS_AMENDMENT_001` unchanged. Status remains `LOCKED_OFFLINE_HARNESS_NOT_EXECUTED`.

## 18. Post-lock amendment HARNESS_AMENDMENT_003

**Date:** October 15, 2026. **Owner decision:** approved closure of three governance gaps. **Record:** `ENGINE3_STAGE_F_HARNESS_AMENDMENT_003.md`.

Scope: annotation-record provenance completeness, historical/current O-5 status separation, and broader annotator-exposure governance.

| | Before | After |
|---|---|---|
| Record provenance | `PrimaryAnnotation` and `Adjudication` carried one `protocol_version_sha256`. | They carry `locked_protocol_sha256`, `operational_guide_sha256`, and `annotation_packet_sha256`. `protocol_version_sha256` is removed, so no record can fall back to it. The first two must equal the exact frozen hashes; the third must equal the SHA-256 of the UTF-8 bytes of `AnnotationPacket.to_json()` for the packet being finalized. `record_provenance(packet)` builds all three. |
| O-5 status in the manifest | `pre_run_state_current` recorded O-5 `PRE_RUN_REQUIRED`, stale since the O-5 freeze. | `o5_status_at_harness_lock` = `PRE_RUN_REQUIRED` (history); `o5_current_status` = `RESOLVED_FROZEN`, bound to the O-5 freeze manifest, frozen configuration, and `O5_POSTFREEZE_REBINDING_001` by SHA-256. The superseded snapshot is kept in `pre_run_state_current_superseded`. |
| O-3 exposure rule | Exposure meant having seen the historical protocol's section 7 examples. | Exposure means having seen labelled evaluation-derived examples from any project artifact (`ENGINE3_STAGE_F_O3_ANNOTATOR_ELIGIBILITY_ADDENDUM_002.md`). No exception approved; no identity supplied. |

No experiment rule changed. The packet guide, packet structure, packet ids, blinding, comparison, decision rules, thresholds A to G, arms, corpora, Stage D behaviour, and the O-5 model configuration, prompt, schema, retry policy, and run order are unchanged. `readiness.py` is unchanged: the frozen O-5 configuration already satisfies its O-5 check, and pilot readiness stays false because O-3 is unresolved and `CALIBRATION_CORPUS_CONTAMINATION_AUDIT_REQUIRED` is open. No calibration corpus was built. Status remains `LOCKED_OFFLINE_HARNESS_NOT_EXECUTED`.

## 19. Post-lock amendment HARNESS_AMENDMENT_004

**Date:** October 15, 2026. **Owner decision:** approved (freeze an offline human-annotator calibration corpus and, only if its contamination audit passes, record the calibration blocker resolved). The calibration key was model-drafted and subsequently reviewed and approved by the human project owner with model-assisted review. This approval is not an independent human annotation or independent adjudication. **Record:** `ENGINE3_STAGE_F_HARNESS_AMENDMENT_004.md`.

Scope: the current pre-run state in the harness manifest, and the verifier and test checks of it. No harness code changed.

| | Before | After |
|---|---|---|
| `calibration_contamination_audit` | `OPEN` (no calibration corpus existed) | `RESOLVED_FROZEN`, bound by SHA-256 to `ENGINE3_STAGE_F_HUMAN_CALIBRATION_FREEZE_MANIFEST.json`, the calibration corpus, and the calibration guide |
| Pilot readiness blockers | O-3 `PRE_RUN_REQUIRED`; calibration `OPEN` | O-3 `PRE_RUN_REQUIRED` |
| Superseded snapshot | one (`HARNESS_AMENDMENT_003`) | two; the snapshot written by `HARNESS_AMENDMENT_003` is kept with the calibration blocker `OPEN` |

The calibration corpus, guide, freeze manifest, and audit (`scripts/verify_engine3_stage_f_calibration.py`) are outside the harness. The audit compares every calibration sentence against the 48 Arm A artifacts, Arm G scenario and evaluation material, Stage E prerecorded prose, the dry-run inputs and oracle, the operational guide's worked examples, and the O-5 prompt's worked examples, and found no exact, near, distinctive 4-gram, or banned-term match. No calibration response was collected.

No experiment rule changed. The packet guide, packet structure, blinding, comparison, decision rules, thresholds A to G, arms, corpora, `readiness.py`, Stage D behaviour, the historical protocol, the operational guide, and the O-5 model configuration are unchanged. Pilot readiness stays false because O-3 is unresolved. Status remains `LOCKED_OFFLINE_HARNESS_NOT_EXECUTED`.
