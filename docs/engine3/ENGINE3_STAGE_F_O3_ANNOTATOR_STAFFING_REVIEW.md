# BAEC Engine 3: Stage F O-3 Annotator Staffing — Owner Review and Checklist

**Project:** BAEC Trigger Intelligence\
**Engine:** BAEC Engine 3: Re-engagement Preparation\
**Stage:** F, pre-run item O-3 only\
**Status:** `O3_CANDIDATE_STAFFING_REQUIRED`. O-3 remains `PRE_RUN_REQUIRED`.\
**Date:** October 15, 2026\
**Base commit:** `3be674300c322a0a43aac670fa93c529b853b7df`\
**Candidate record:** `ENGINE3_STAGE_F_O3_ANNOTATOR_STAFFING_CANDIDATE.json`

Every rule here is an IMPLEMENTATION rule for a synthetic software evaluation. Nothing here is a research finding. No annotator has been named, no annotation has been performed, and no result exists.

---

## 1. Scope and pre-run status

| Item | Status |
|---|---|
| O-3 annotator staffing and identities | `PRE_RUN_REQUIRED`; record status `O3_CANDIDATE_STAFFING_REQUIRED` |
| O-5 frozen run configuration | `PRE_RUN_REQUIRED` (untouched; no model, version, prompt, or sampling configuration chosen) |
| O-6 Arm A supplement | `RESOLVED_FROZEN` (unchanged) |
| Pilot readiness | **false** |

This document does not modify any locked Stage A to F design or O-6 artifact, or the packet builder. The readiness author-independence check was changed separately by the owner-approved `HARNESS_AMENDMENT_001` (section 5). O-3 remains `O3_CANDIDATE_STAFFING_REQUIRED` until actual humans are supplied.

## 2. What O-3 needs

The locked protocol (section 3) requires `DUAL_PRIMARY_WITH_ADJUDICATION` for any reliability claim: two independent primary annotators plus a separate adjudicator. O-3 can be frozen only when the owner supplies three actual humans, each identified by a stable alias:

- **Primary Annotator A**
- **Primary Annotator B**
- **Adjudicator**, separate from both primaries

Claude Code cannot supply, suggest, or fill any identity.

### 2.1 Independence

| Requirement | Primary A | Primary B | Adjudicator |
|---|---|---|---|
| Real human | required | required | required |
| Not `PROJECT_OWNER` | required | required | preferred (intended research configuration) |
| Not `CLAUDE_CODE_ASSISTED_DRAFTING` | required | required | required (not a human) |
| Did not author, edit, or approve the 16 Arm A supplement artifacts | required | required | preferred |
| Distinct from the other two roles | required | required | required |

The adjudicator rows marked *preferred* go beyond the locked protocol, which requires only that the adjudicator be neither primary. If the owner adjudicates, the adjudicator alias must be exactly `PROJECT_OWNER` and the departure must be disclosed in every report.

Under the O-6 review (section 6, item 2), anyone who approved or edited the supplement text is an author for D-9. The project owner approved it, so the owner is an author.

### 2.2 Also recorded, not disqualifying

Protocol section 2 requires recording, for each annotator, whether they authored any Stage B, E, or F corpus items or the protocol, and whether they have seen Stage E fixture annotations. These are reported, not hidden. Under the locked protocol they do not by themselves disqualify anyone, so this checklist does not add a new exclusion.

### 2.3 No qualification threshold

The locked design defines no qualification score, certification, or statistical entry threshold for annotators, and none is added here. Calibration (protocol section 5) happens after staffing and before evaluation packets. It is a required step, not a pass/fail gate, and its agreement is not reported as a reliability result.

## 3. Blinding

### 3.1 Primary A and Primary B

Each primary annotates independently. Neither may see:

- the other primary's annotations;
- model-supplied annotations;
- model-labelled Stage D outcomes (`D_model`) or any Stage D reason code;
- adjudicated reference labels;
- scenario id or scenario family;
- Arm G / Arm A designation;
- repeat number, run id, or context id;
- model identity, prompt, or sampling settings;
- expected reason codes;
- false-pass / false-refusal expectations or classifications;
- Stage E fixture annotations or oracle outcomes;
- Arm A supplement authoring metadata.

### 3.2 Adjudicator

The adjudicator may receive the blinded source packet and both primary annotation records (H attestations, statuses, field values, notes).

The adjudicator must not receive:

- model-supplied annotations;
- the model-labelled Stage D result or any Stage D reason code;
- the false-pass / false-refusal classification;
- the expected outcome;
- model identity.

### 3.3 What enforces this

The unchanged `build_annotation_packet` enforces packet contents: it takes only an artifact and its context, drops model annotations, and replaces model segment ids with opaque ids. Everything else, including who can open which files, results, or conversations, is operational. The packet operator and owner enforce it. No code in this repository controls that access.

## 4. Owner checklist

Obtain each item directly from the reviewer. Record only the alias and the answers. Do not record email addresses, phone numbers, employer information, or other personal data. Keep the alias-to-person mapping outside the repository.

### 4.1 Aliases

Alias rules:

- stable and non-empty, used unchanged in every staffing, annotation, and adjudication record;
- all three different after stripping surrounding whitespace and casefolding (`R1` and ` r1 ` are the same reviewer); the alias is stored as given;
- a primary alias must not be `PROJECT_OWNER`, `CLAUDE_CODE_ASSISTED_DRAFTING`, or any case or spacing variant of either;
- if the owner fills any role, that alias is exactly `PROJECT_OWNER`. No other alias (for example `owner`, `PROJECT-OWNER`, or a personal name) may stand in for the owner. Choosing a different alias would hide the owner from the author check, so this is a condition of the record, and `relationship_to_project` must be declared truthfully.

For the author check, the readiness code compares aliases after stripping surrounding whitespace and casefolding. Either primary matching an Arm A author fails readiness.

```
Primary A stable alias:
Primary B stable alias:
Adjudicator stable alias:
```

### 4.2 Confirmations from each reviewer

Answer yes or no for each reviewer. Every item must be answered before freeze.

| # | Confirmation | Primary A | Primary B | Adjudicator |
|---|---|---|---|---|
| 1 | Is a real human | | | |
| 2 | Has read and understands the Stage F annotation protocol, including calibration before evaluation packets | | | |
| 3 | Agrees to remain blinded as in section 3 | | | |
| 4 | Will not coordinate or discuss primary annotations with anyone before both primaries lock their labels | | | n/a |
| 5 | Did not author, edit, or approve the 16 supplemental Arm A artifacts | | | |
| 6 | Has not seen model annotations or experimental results | | | |
| 7 | Will not use a model, classifier, or automated labeller for any label, status, or attestation | | | |
| 8 | Is the project owner (if yes, alias must be `PROJECT_OWNER`) | | | |
| 9 | Authored any Stage B, E, or F corpus items or the protocol (recorded, not disqualifying) | | | |
| 10 | Has seen Stage E fixture annotations (recorded, not disqualifying) | | | |
| 11 | Other material conflict (short text, or `NONE`) | | | |
| 12 | Date of acknowledgement (YYYY-MM-DD) | | | |

### 4.3 Freeze conditions

O-3 can be frozen only when:

1. All three aliases are filled and follow section 4.1.
2. Items 1, 2, 3, and 7 are *yes* for all three reviewers.
3. For Primary A and Primary B: item 4 is *yes*, and items 5, 6, and 8 give the required answers (no supplement authorship, no prior exposure, not the project owner).
4. For the adjudicator: item 6 is *no prior exposure*. Item 5 and item 8 are preferred to show independence; any departure is disclosed.
5. Item 11 is filled for everyone.
6. The owner approves the filled record, and it is frozen before any pilot and before any main experimental result is viewed.

The answers then go into the candidate JSON record, and its status changes only through a separate, owner-approved freeze step.

## 5. Author independence and the readiness check

`HARNESS_AMENDMENT_001` (October 15, 2026, owner decision Option B) closes the readiness-code gap recorded in the first version of this review. See `ENGINE3_STAGE_F_HARNESS_AMENDMENT_001.md`.

### 5.1 Mapping

At freeze, the readiness inputs would be:

- `AnnotatorStaffing(primary_a_alias, primary_b_alias, adjudicator_alias, frozen=True)` from the frozen O-3 record;
- 16 `SupplementArtifact` values from the frozen O-6 file and manifest, each with `author_aliases = ("CLAUDE_CODE_ASSISTED_DRAFTING", "PROJECT_OWNER")`.

`check_pilot_readiness` normalizes primary and author aliases by stripping surrounding whitespace and then casefolding, and compares them by exact equality. There is no substring or fuzzy matching.

### 5.2 What the amended check enforces

An artifact fails author independence if **either** Primary A or Primary B matches one of its authors. With the frozen O-6 records:

| Staffing (scratch placeholders, not saved) | O-6 author independence |
|---|---|
| O-3 not supplied, or a primary alias empty | not checkable |
| Two non-author primaries | passes |
| `PROJECT_OWNER` as Primary A or Primary B only | **fails** |
| `CLAUDE_CODE_ASSISTED_DRAFTING` as one primary | **fails** |
| `project_owner`, ` Project_Owner`, `claude_code_assisted_drafting`, ` CLAUDE_CODE_ASSISTED_DRAFTING` as one primary | **fails** |
| An alias that only contains "owner" or "claude" | passes |
| Owner or drafting role as adjudicator only | passes (adjudicator independence stays preferred, not a hard rule) |

The check still cannot:

- tell whether an alias belongs to a human;
- see the real person behind an alias. The owner is detected only if recorded as `PROJECT_OWNER`, which is why that alias rule (section 4.1) is a governance condition of the record;
- check any acknowledgement, disclosure, or blinding condition.

These remain owner-review conditions (section 4.3).

## 6. O-5 and pilot readiness

O-5 remains `PRE_RUN_REQUIRED`. No model family, model version, prompt, sampling configuration, or experiment configuration is chosen or implied here.

Pilot readiness is **false**. Even with O-3 and O-5 satisfied, owner approval of any pilot is still required (`owner_approval_still_required` is always true), and the harness has no executor.

## 7. Claims boundary

This is a staffing checklist and an empty candidate record. It names no one, performs no annotation, and produces no result. It is not evidence of annotation reliability, semantic validation, purchase prediction, sales lift, production security, or BAEC theory. E3-E-SEM-001 `MODEL_SEMANTIC_ANNOTATION_NOT_AUTHORITATIVE` remains unresolved.
