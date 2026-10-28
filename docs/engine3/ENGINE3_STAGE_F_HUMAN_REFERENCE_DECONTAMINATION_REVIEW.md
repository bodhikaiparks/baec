# BAEC Engine 3: Stage F Human-Reference Decontamination Review

**Project:** BAEC Trigger Intelligence\
**Engine:** BAEC Engine 3: Re-engagement Preparation\
**Stage:** F\
**Issue:** `E3F-ISSUE-HR-001`\
**Date:** October 15, 2026\
**Base commit:** `726a67a320b1e05a20ef6ae608e0e41e72bfc44c`\
**Result:** candidate operational guide created and audited. The issue stays **open**: blinded packets still expose contaminated text, and fixing that needs a harness amendment the owner has not approved.

This is an IMPLEMENTATION record for owner review. It is not a research finding. No model was called, no credential was read, no network was used, no pilot or main run was executed, and no human-reference annotation exists.

---

## 1. Scope

Requested: remove evaluation-item contamination from the material future annotators see, without changing the locked Stage F semantic rules.

Not changed:

- the locked human-reference protocol;
- the issue record (`ENGINE3_STAGE_F_HUMAN_REFERENCE_CONTAMINATION_ISSUE.md`). Its SHA-256 is bound by the O-5 freeze manifest, so its status line is not edited. This review records the current state;
- the O-5 frozen configuration and freeze manifest;
- the O-6 corpus and freeze manifest;
- the harness, its tests, and its manifest;
- the experiment design and spec;
- O-3 records.

## 2. Inputs inspected

- the issue record, the locked protocol, and the experiment design;
- the O-5 frozen configuration, including its decontaminated prompt;
- `experiments/engine3_stage_f/annotation.py` and `tests/engine3/test_engine3_stage_f_annotation.py`;
- the evaluation prose: all 48 Arm A artifacts (32 Stage E seed recordings and 16 O-6 artifacts), Arm G scenario strings and grounding items, all Stage E prerecorded proposal prose and human edits, and all Stage F dry-run prose. That is 321 unique sentences, gathered by the O-5 audit's `evaluation_corpus()` unchanged;
- the guide emitted in an actual packet built by `build_annotation_packet` from dry-run input.

## 3. Packet-builder inspection

**Result: yes, packets expose contaminated examples.**

Every blinded packet carries `guide = ANNOTATION_GUIDE` (`experiments/engine3_stage_f/annotation.py`, lines 102 to 137). Three of its strings contain evaluation values from the Alder Ridge contexts:

| Packet guide path | Exposed text |
|---|---|
| `specificity_convention.90` | `exact or near-exact factual detail exposed, including formatting-only variants such as 9.00% -> 9%` |
| `reference_use_convention.formatting_only_numeric_variant` | `may be coded DIRECT (example 9.00% -> 9%) and is flagged formatting_variant` |
| `reference_use_convention.semantically_equivalent_rewording` | `coded PARAPHRASE (example more than 8% -> above 8%)` |

No other packet field carries instructional or example text. The other fields are the artifact under judgment and its grounding items, which the protocol requires annotators to see.

Not an exposure: `tests/engine3/test_engine3_stage_f_dry_run.py` checks the dry-run case `purpose` strings for "9.00% -> 9%". Those strings are fixture metadata and never enter a packet.

The operational guide alone therefore does **not** resolve the exposure. `ANNOTATION_GUIDE` is locked harness code (hash-bound in the harness manifest and in the O-5 freeze manifest), so the fix requires `HARNESS_AMENDMENT_002` (section 6). That code change was **not** made.

### 3.1 A second direction problem in a locked O-3 record

`ENGINE3_STAGE_F_O3_ANNOTATOR_STAFFING_REVIEW.md`, checklist item 2, asks each reviewer to confirm they have "read and understands the Stage F annotation protocol". Followed literally, this sends reviewers to the contaminated section 7 examples. The O-3 records are locked inputs of the O-5 freeze and are not edited here. Section 7 records the condition the future O-3 freeze must apply.

## 4. Operational guide

**File:** `docs/engine3/ENGINE3_STAGE_F_HUMAN_REFERENCE_OPERATIONAL_GUIDE.md`\
**Status:** `CANDIDATE_DECONTAMINATED_GUIDE`\
**SHA-256 at this review:** `5daa5df95a4585a46f197ef1df7ee12b6df3eac6a94655d76007a1fd254e5985`\
**Change record:** `docs/engine3/ENGINE3_STAGE_F_HUMAN_REFERENCE_OPERATIONAL_GUIDE_CHANGES.json` (every substitution, omission, addition, allowlist entry, and the proposed packet text; SHA-256 `91325f2a55ffc3d8044a9f8c690f76259d73a611a56e543c9d11be2c05abf7a5`)\
**Verifier:** `scripts/verify_engine3_stage_f_operational_guide.py`

The guide keeps the protocol's section numbers. It copies every annotation-rule line verbatim and changes only example text. The examples use the same fictional account (a library, the courier firm Lakemoor, items X-STATEMENT to X-NOTE) as the frozen O-5 prompt. Of the 34 example fragments in the guide, 20 appear verbatim in the frozen prompt. The other 14 are the human-only section 7.0 examples (statuses are not shown to the model) or the same values in different markup. Humans and the model therefore learn from the same illustrations. This preserves the protocol 7.0 requirement that the model prompt contain "this same guide text".

### 4.1 Semantic-equivalence review

Method: the verifier, check 2, compares lines in both directions, against the change record:

- **Protocol to guide.** Each of the 172 content lines in protocol sections 2, 4, 5, 6, 7, 8, and 10 must meet one of these conditions:
  - it appears byte-for-byte in the guide;
  - it becomes a guide line exactly when its registered (old fragment → new fragment) example pairs are applied, and every pair must occur exactly once;
  - it is the one registered provenance line;
  - it is registered as omitted, with a reason.
- **Guide to protocol.** Every guide line must be one of the results above, or a registered orientation line (header, "Before you begin", the fictional account, the closing note).

Result: **172 = 132 verbatim + 28 example substitutions + 1 provenance replacement + 11 omitted; 0 unexplained guide lines.**

- **Verbatim:**
  - every rule in sections 7.0 to 7.11 for `claim_type`, `semantic_class`, `grounding_refs`, `reference_use_mode`, `source_identity_refs`, `claim_field`, `declared_value`, `specificity`, `question_has_premise`, `source_instruction_ref`, and `prescribes_buyer_choice`;
  - the `SCORABLE`, `ADJUDICATION_REQUIRED`, and `UNSCORABLE_SEGMENT_STRUCTURE` table and the "Do not force a label" rule;
  - the semantic-class precedence list and the representational-limitation paragraph;
  - H attestations (section 6), adjudication (section 8), annotator prohibitions (section 10), calibration (section 5), and what packets show and hide (sections 4.1 and 4.2).
- **Example substitutions (28 lines):** only quoted example text, example values, and the example labels describing them changed. Examples:
  - "9%" for `9.00%` became "$46" for `$46.00`;
  - "above 8%" for `more than 8%` became "higher than $40" for `over $40`;
  - "Worked examples (Alder Ridge)" became "Worked examples (fictional account)".

  Each pair is listed in the change record (`example_substitutions`), which is the authoritative list. The labels each example teaches (status, class, mode, flag, true/false) are unchanged in every case.
- **Provenance replacement (1 line):** "Worked examples use synthetic Stage E fixture text…" would be false for the new examples. It now says the examples use the fictional account, and that in a packet only ids shown in that packet are cited. The second part restates the protocol's existing definition of "Cite".
- **Omitted (11 lines):** the section 2 exposure-record and supplement-author rules, the two design notes at the end of section 4.2, and section 4.3 (ordering and deduplication). They are packet-operator and O-3 administration, not annotation rules. Two of them name the arms, which annotators must not be told per packet. They stay in force in the locked protocol.

No meaning, threshold, precedence, category, or tie-breaker was introduced. The guide states that the locked protocol governs if the two ever disagree.

Reviewer judgment (not mechanically checkable): each replacement example teaches the same distinction as the example it replaces. The one structural adjustment is the first unscorable example. It keeps the protocol's two value kinds (`EVENT_MAGNITUDE` and `DATE`) and therefore uses an ungrounded date ("April 6"). Status is decided on structure, so grounding does not affect what the example teaches.

## 5. Contamination audit

Method: the verifier, check 3. It reuses the O-5 audit unchanged:

- the same 321-sentence evaluation corpus;
- casefold tokens;
- exact = identical token sequence (at least 4 tokens);
- near = token-set Jaccard ≥ 0.5 or a shared contiguous 5-token sequence;
- the same banned terms: alder, ridge, cobalt, july, 8%, 9%, 9.00%, more than 8, above 8.

It adds **shared distinctive 4-grams**: a 4-token sequence that also occurs in the corpus and has at least two tokens outside a fixed function-word list.

| Audit | Result |
|---|---|
| Worked examples audited | 34 example fragments (28 substitution results, plus the account's buyer statement and instruction text) |
| Exact findings in worked examples | 0 |
| Near findings in worked examples | 0 |
| Shared distinctive 4-grams in worked examples | 0 |
| Banned terms in worked examples | 0 |
| Banned terms anywhere in the guide | 0 |
| Whole-guide sentence scan | 2 findings, both allowlisted; 0 unexplained |

Allowlisted normative overlaps, both locked definitions copied verbatim. Neither gives a label for any evaluation segment:

1. Section 7.11, `prescribes_buyer_choice`: "…leave the incumbent, or put the contract out for review." The list of commercial choices shares "put the contract out for review" with the buyer statement. This is the same allowlist entry as the frozen O-5 prompt.
2. Section 7.1, rule 2, `PRIOR_BUYER_STATEMENT_REFERENCE`: "it states what the buyer previously said…". It shares the 4-gram "the buyer previously said" with a Stage E internal summary.

## 6. `HARNESS_AMENDMENT_002` (proposed, not applied)

**Purpose:** substitute decontaminated example text in the packet guide, and nothing else.

**Proposed change**, `experiments/engine3_stage_f/annotation.py`, `ANNOTATION_GUIDE` only:

| Path | Old fragment | New fragment |
|---|---|---|
| `specificity_convention.90` | `9.00% -> 9%` | `$46.00 -> $46` |
| `reference_use_convention.formatting_only_numeric_variant` | `9.00% -> 9%` | `$46.00 -> $46` |
| `reference_use_convention.semantically_equivalent_rewording` | `more than 8% -> above 8%` | `over $40 -> higher than $40` |

The verifier applies these substitutions to a copy of `ANNOTATION_GUIDE` and audits the result. It finds 0 exact, near, 4-gram, or banned-term findings. Rule text around each example is unchanged.

**Consequent changes the amendment would need:**

1. `tests/engine3/test_engine3_stage_f_annotation.py`, `test_guide_conventions_match_the_locked_spec_exactly`. It asserts that `specificity_convention["90"]` equals the locked spec string, which itself contains "9.00% -> 9%". The locked spec is a design record and should stay unchanged. The test would instead assert equality after only the example fragment is substituted.
2. A regression test: the packet guide contains no banned evaluation term and no corpus finding.
3. `scripts/verify_engine3_stage_f_harness.py` `AMENDMENTS`, the harness manifest (`harness_amendments`, regenerated hashes), and the implementation report, as for `HARNESS_AMENDMENT_001`.

**Effect on O-5 (needs an owner decision).** The O-5 freeze manifest hash-binds `annotation.py`, the annotation test, the harness verifier, the harness manifest, and the implementation report as locked inputs. After the amendment, `scripts/verify_engine3_stage_f_o5_freeze.py` check 3 would fail "locked input changed since freeze". The O-5 frozen configuration itself would not change: prompt, schema, configuration hash, and run order are untouched, and the packet guide is never sent to the model. Recording the amendment therefore needs one of:

- (a) an owner-approved O-5 re-binding record that lists the new hashes and leaves the frozen configuration as is;
- (b) an owner-approved change to the O-5 verifier to recognise recorded harness amendments.

Neither is done here, because both touch frozen O-5 records.

**Out of scope for this amendment:** adding a packet field that names the operational guide. That would go beyond substituting example text. If the owner wants packets to point to the guide, it is a separate decision.

## 7. Future O-3 condition (recorded, not applied)

For the main blinded human-reference collection:

> Any Primary A, Primary B, or Adjudicator who has already reviewed the contaminated labelled evaluation examples in the locked protocol (`ENGINE3_STAGE_F_HUMAN_REFERENCE_ANNOTATION_PROTOCOL.md`, section 7) is not eligible, unless the owner explicitly approves a documented exception.

Related conditions for the O-3 freeze:

- Read checklist item 2 ("Has read and understands the Stage F annotation protocol") as the operational guide for annotation rules, not the locked protocol's section 7 examples. Each reviewer also confirms whether they have seen those examples.
- Anyone who authored, edited, or reviewed the locked protocol has seen those examples. This includes the reserved author roles `PROJECT_OWNER` and `CLAUDE_CODE_ASSISTED_DRAFTING`. Under this condition, such a person is ineligible for every reviewer role, including adjudicator, without an approved exception.

No reviewer identity is supplied. O-3 is not resolved, and the O-3 records are not modified.

## 8. Resolution criteria

| # | Criterion | Met? |
|---|---|---|
| 1 | The operational guide preserves semantic equivalence | **Yes** (candidate): mechanical line check passes; owner approval pending |
| 2 | The guide's worked examples pass the contamination audit | **Yes**: 0 exact, 0 near, 0 distinctive 4-grams, 0 banned |
| 3 | Actual annotation packets do not expose contaminated examples | **No**: 3 packet-guide strings expose evaluation values; needs `HARNESS_AMENDMENT_002` |
| 4 | Future annotators are directed to the operational guide, not the locked protocol | **Partly**: the guide says so; the locked O-3 checklist item 2 still names the protocol, and packets do not name a guide. Needs the section 7 O-3 condition applied at O-3 freeze |
| 5 | The historical locked protocol is preserved | **Yes**: SHA-256 equals the O-5-bound hash |
| 6 | No human-reference annotations have been collected | **Yes**: the O-3 record has `annotation_performed = false`; O-5 shows `experiment_results_exist = false` |

Criteria 3 and 4 are not met, so the blocker stays open.

## 9. Owner decisions needed

1. Approve, revise, or reject the candidate operational guide. If approved, freeze it with a hash.
2. Approve or reject `HARNESS_AMENDMENT_002` as scoped in section 6.
3. Choose how O-5 records that amendment: option (a) or (b) in section 6.
4. Adopt the section 7 eligibility condition at the O-3 freeze.
5. Decide which document hash human-reference records carry in `protocol_version_sha256` (protocol section 9): the locked protocol, the frozen operational guide, or both. Protocol section 5 allows guide clarifications to be versioned and frozen before evaluation begins. No annotation exists yet, so this is not a change to a running configuration.
6. Check that the calibration set (protocol section 5) is also free of evaluation-derived text before calibration begins. The verifier does not audit it, because it does not yet exist.

## 10. Status

| Item | Status |
|---|---|
| `E3F-ISSUE-HR-001` | `PRE_HUMAN_ANNOTATION_BLOCKER` (open) |
| `HARNESS_AMENDMENT_002` | `PROPOSED_NOT_APPLIED` |
| Operational guide | `CANDIDATE_DECONTAMINATED_GUIDE` |
| O-3 | `PRE_RUN_REQUIRED` |
| O-5 | `RESOLVED_FROZEN` (unchanged) |
| O-6 | `RESOLVED_FROZEN` (unchanged) |
| Pilot readiness | false |
| E3-E-SEM-001 | not resolved |

## 11. Claims boundary

This record describes documents and a document check. It reports no annotation, no agreement figure, and no experiment result. Passing the contamination audit shows the guide's examples do not match the evaluation corpus under a fixed token rule. It does not show annotators will be uninfluenced. It is not evidence about annotation reliability, model quality, semantic validation, purchase prediction, sales lift, production security, or BAEC theory.
