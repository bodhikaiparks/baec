# BAEC Engine 3: Stage F O-3 Annotator Eligibility Addendum 002

**Project:** BAEC Trigger Intelligence\
**Engine:** BAEC Engine 3: Re-engagement Preparation\
**Stage:** F\
**Pre-run item:** O-3 (`PRE_RUN_REQUIRED`, not resolved by this addendum)\
**Status:** `ADOPTED_FUTURE_O3_CONDITION`\
**Date:** October 15, 2026\
**Source:** owner decision recorded with `HARNESS_AMENDMENT_003`\
**Supplements:** `ENGINE3_STAGE_F_O3_ANNOTATOR_ELIGIBILITY_ADDENDUM.md`, SHA-256 `b66e57b5c546346c031913981091cce21c6b00863b633b62143cdedfad43632f` (unchanged)

This addendum is additive. The first addendum is hash-bound by `ENGINE3_STAGE_F_HUMAN_REFERENCE_CONTAMINATION_RESOLUTION_001.md`, so it is not edited. This record widens its exposure rule and answers its open question (first addendum, section 5). It does not modify the O-3 candidate record or the O-3 review. No reviewer identity is supplied, and O-3 is not resolved.

This is an IMPLEMENTATION rule for a synthetic software evaluation. It is not a research finding.

---

## 1. Broadened eligibility rule

> Any Primary A, Primary B, or Adjudicator who has previously seen labelled evaluation-derived examples from any project artifact is **presumptively ineligible** for the blinded human-reference collection unless the owner later approves a documented exception.

This replaces the narrower condition in the first addendum (section 1), which named only the historical protocol's section 7 examples. Everything that condition made ineligible stays ineligible under this one.

## 2. What counts as exposure

**Evaluation-derived exposure** means having seen labelled examples that originate from Arm A, Stage E, or Stage F evaluation prose, regardless of which document showed them. "Labelled" means the prose is shown together with an annotation value, a classification, or a judgement about how it should be coded.

Project artifacts that may carry such examples include:

- the historical protocol (`ENGINE3_STAGE_F_HUMAN_REFERENCE_ANNOTATION_PROTOCOL.md`);
- design and spec documents (for example the Stage F experiment design and the experiment spec);
- issue and review records (for example the human-reference contamination issue and the decontamination review);
- amendment records (for example `HARNESS_AMENDMENT_002`);
- candidate materials (for example candidate guides or candidate examples).

The list is illustrative. The test is what the person saw, not which file it was in.

**Working on the repository is not, by itself, exposure.** A person is exposed only if they actually saw labelled evaluation-derived content. This rule adds no automatic exposure finding for any role, alias, or contributor, and it adds no identity matching beyond the existing alias rules (strip and casefold, exact match; `HARNESS_AMENDMENT_001`).

The first addendum's finding still holds: anyone who authored, edited, or reviewed the historical protocol has seen its section 7 examples. That is actual exposure, not an inference from repository work.

## 3. Exceptions

An exception may be granted only by the owner, later, for a named role, with its reason recorded before that reviewer receives any evaluation packet.

**No exception is approved now.**

## 4. Recording at the O-3 freeze

At the O-3 freeze, each role records, in place of the first addendum's narrower item:

- "Has seen labelled evaluation-derived examples (Arm A, Stage E, or Stage F evaluation prose) in any project artifact (yes / no). If yes, which artifacts."

A *yes* makes the reviewer presumptively ineligible (section 1) unless an approved exception is recorded. Reviewers who are eligible are not given the artifacts in section 2. Annotators use only the frozen operational guide and the blinded packets (first addendum, section 2, unchanged).

## 5. Relation to the first addendum

| First addendum section | Effect of this addendum |
|---|---|
| 1. Eligibility rule | Widened by section 1 of this record. |
| 2. Which guide annotators use | Unchanged. |
| 3. Annotation record provenance | Implemented by `HARNESS_AMENDMENT_003` (record types carry all three hashes). The first addendum's "not implemented" note is the state on its date and is not rewritten. |
| 4. Calibration | Unchanged. `CALIBRATION_CORPUS_CONTAMINATION_AUDIT_REQUIRED` stays open. |
| 5. Open question for the owner | Answered: exposure through any project artifact counts the same way. |

## 6. Status

| Item | Status |
|---|---|
| This addendum | `ADOPTED_FUTURE_O3_CONDITION` |
| Broadened exposure rule | `ADOPTED` (applies at the future O-3 freeze) |
| O-3 | `PRE_RUN_REQUIRED` (identities not supplied) |
| Exceptions approved | none |
| `CALIBRATION_CORPUS_CONTAMINATION_AUDIT_REQUIRED` | `OPEN` |

## 7. Claims boundary

An eligibility condition for future reviewers of a synthetic software evaluation. It names no person, approves no exception, and is not evidence about annotation reliability, model quality, semantic validation, purchase prediction, sales lift, production security, or BAEC theory.
