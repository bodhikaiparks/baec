# BAEC Engine 3: Stage F Harness Amendment 001

**Project:** BAEC Trigger Intelligence\
**Engine:** BAEC Engine 3: Re-engagement Preparation\
**Stage:** F (offline experimental harness)\
**Amendment:** `HARNESS_AMENDMENT_001`\
**Date:** 2026-10-15\
**Owner decision:** Option B (owner-approved, narrowly scoped post-lock amendment)\
**Base commit:** `3be674300c322a0a43aac670fa93c529b853b7df`\
**Harness status:** `LOCKED_OFFLINE_HARNESS_NOT_EXECUTED` (unchanged)

This is an IMPLEMENTATION rule change to a readiness control. It is not a research finding.

---

## 1. Reason

The locked readiness implementation enforced only the minimum condition: an artifact failed author independence when both primary annotators were authors.

Owner review identified that this does not enforce the intended stronger Stage F staffing configuration, in which neither Primary A nor Primary B may be an author of the frozen supplemental Arm A artifacts.

Before commit, owner review also found that reviewer-to-reviewer distinctness used exact strings, so aliases differing only by case or surrounding whitespace counted as different reviewers, and that the harness manifest did not distinguish O-6's lock-time status from its current status.

## 2. Scope

Two readiness corrections in `experiments/engine3_stage_f/readiness.py`, and one harness-manifest clarification:

1. Either primary matching an artifact author fails author independence (sections 2.1 to 2.4).
2. Reviewer role aliases must be distinct after strip and casefold (section 2.6).
3. The harness manifest keeps the historical lock-time O-6 status and adds the current O-6 status, bound to the later freeze record (section 2.7).

Both readiness corrections were approved before this amendment was committed, so they are recorded together under `HARNESS_AMENDMENT_001`.

### 2.1 Rule before

For each supplement artifact, author independence failed only when every primary alias was in the artifact's `author_aliases`, by exact string comparison.

### 2.2 Rule after

For each supplement artifact:

1. Normalize each alias deterministically: strip surrounding whitespace, then casefold.
2. `primary_aliases` = {normalized Primary A, normalized Primary B}.
3. `artifact_authors` = the normalized author aliases of the artifact.
4. If `primary_aliases ∩ artifact_authors` is non-empty, author independence **fails**.

Either primary being an author is sufficient to fail. No substring or fuzzy matching is used: an alias fails only if, after normalization, it equals an author alias.

If either primary alias is empty, author independence is reported as not checkable ("author independence cannot be checked without O-3"). It does not pass by default.

### 2.3 Reserved author roles

The frozen O-6 supplement records the author role aliases `PROJECT_OWNER` and `CLAUDE_CODE_ASSISTED_DRAFTING`. They are author aliases for this check. As Primary A or Primary B, each of these fails:

- `PROJECT_OWNER`, `project_owner`, ` Project_Owner`
- `CLAUDE_CODE_ASSISTED_DRAFTING`, `claude_code_assisted_drafting`, ` CLAUDE_CODE_ASSISTED_DRAFTING`

An alias that only contains words such as "owner" or "claude" does not fail.

### 2.4 Adjudicator

The adjudicator is **not** made subject to a new hard author-independence rule. Independence of the adjudicator from the project owner and from corpus drafting remains the preferred research configuration recorded in the O-3 candidate record, not a locked eligibility rule.

### 2.5 Record governance (unchanged by code)

The code compares declared aliases. It does not infer real-world identity. The O-3 record therefore keeps the governance rule that if the project owner serves in any reviewer role, the alias is exactly `PROJECT_OWNER`. No other alias, such as `owner`, `PROJECT-OWNER`, or a personal name, may stand in for the owner, and `relationship_to_project` must be declared truthfully.

### 2.6 Reviewer distinctness

Before: Primary A, Primary B, and the adjudicator had to differ by exact string, so `R1` and `r1` counted as two reviewers.

After: the three aliases must be pairwise distinct after the same normalization (strip, then casefold). These fail O-3:

- Primary A `R1`, Primary B `r1`;
- Primary A ` Reviewer-A `, Primary B `reviewer-a`;
- Primary A `R1`, adjudicator ` r1 `.

`R1`, `R2`, `R3` pass. No substring or fuzzy matching is used. Stored and displayed aliases are not changed; normalization is for comparison only.

### 2.7 O-6 state in the harness manifest

When the harness was locked on 2026-10-14, O-6 was `PRE_RUN_REQUIRED`. That historical record is preserved in `pre_run_required` and in `o6_status_at_harness_lock`. O-6 was later resolved by the Arm A freeze. The manifest now also records `o6_current_status = RESOLVED_FROZEN` and `pre_run_state_current`, bound by SHA-256 to `docs/engine3/ENGINE3_STAGE_F_ARM_A_FREEZE_MANIFEST.json` and its frozen corpus. The O-6 files are not modified. The verifier checks both states, the freeze manifest hash, and that the frozen corpus matches the freeze manifest.

## 3. Not changed

This amendment does not change:

- the Stage F research question;
- the experiment design, or the Stage F design manifest and design hashes;
- decision thresholds A to G or their arm scopes;
- annotation fields;
- Arm G / Arm A definitions or arm sizes;
- corpus prose, the O-6 frozen corpus, or its freeze manifest and hashes;
- the human-reference annotation protocol;
- comparison semantics, or false-pass / false-refusal definitions;
- decision-rule semantics;
- live-model authorization or any execution boundary.

It does not resolve O-3 or O-5. No reviewer identity is supplied.

## 4. Files

| File | Change |
|---|---|
| `experiments/engine3_stage_f/readiness.py` | Author-independence rule and reviewer distinctness (section 2) |
| `tests/engine3/test_engine3_stage_f_readiness.py` | Regression tests; the existing test supplement's author alias changed from a primary alias to a separate test author, because the old fixture relied on the weaker rule |
| `tests/engine3/test_engine3_stage_f_harness_manifest.py` | Checks the amendment record and the lock-time and current O-6 states |
| `scripts/verify_engine3_stage_f_harness.py` | Checks the amendment record and its hash, and the lock-time and current pre-run states with the O-6 freeze binding |
| `docs/engine3/ENGINE3_STAGE_F_HARNESS_IMPLEMENTATION_REPORT.md` | Section 10 updated; section 16 added |
| `docs/engine3/ENGINE3_STAGE_F_HARNESS_MANIFEST.json` | Amendment recorded; lock-time and current O-6 state distinguished; harness hashes regenerated; test counts updated |

This record's SHA-256 is stored in the harness manifest (`harness_amendments`).

## 5. Status after amendment

| Item | Status |
|---|---|
| Harness | `LOCKED_OFFLINE_HARNESS_NOT_EXECUTED` |
| `live_model_executed`, `api_key_used`, `external_model_sdk_used` | false |
| `network_calls` | 0 |
| `pilot_executed`, `main_experiment_executed` | false |
| E3-E-SEM-001 | not resolved |
| O-3 | `PRE_RUN_REQUIRED` (record `O3_CANDIDATE_STAFFING_REQUIRED`) |
| O-5 | `PRE_RUN_REQUIRED` |
| O-6 | `PRE_RUN_REQUIRED` at harness lock; currently `RESOLVED_FROZEN` (freeze manifest unchanged) |
| Pilot readiness | false |

## 6. Claims boundary

A readiness-control change in an offline harness that has never been executed. It is not evidence of annotation reliability, semantic validation, purchase prediction, sales lift, production security, or BAEC theory.
