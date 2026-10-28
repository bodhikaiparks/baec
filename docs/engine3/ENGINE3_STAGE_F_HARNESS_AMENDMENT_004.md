# BAEC Engine 3: Stage F Harness Amendment 004

**Project:** BAEC Trigger Intelligence\
**Engine:** BAEC Engine 3: Re-engagement Preparation\
**Stage:** F (offline experimental harness)\
**Amendment:** `HARNESS_AMENDMENT_004`\
**Date:** 2026-10-15\
**Owner instruction:** freeze an offline human-annotator calibration corpus and, only if its contamination audit passes, record `CALIBRATION_CORPUS_CONTAMINATION_AUDIT_REQUIRED` as `RESOLVED_FROZEN`.\
**Owner approval:** `APPROVED` (2026-10-15). The calibration key was model-drafted and subsequently reviewed and approved by the human project owner with model-assisted review. This approval is not an independent human annotation or independent adjudication.\
**Base commit:** `d262a21c4c66b0890ef19094f4e4a46bf087c849`\
**Harness status:** `LOCKED_OFFLINE_HARNESS_NOT_EXECUTED` (unchanged)

This is an IMPLEMENTATION and governance change. It is not a research finding. `HARNESS_AMENDMENT_001` to `HARNESS_AMENDMENT_003` and their records are unchanged.

---

## 1. Scope

This amendment only records the calibration blocker's new status in the harness manifest's current pre-run state, and updates the harness verifier and manifest test to check it. It changes no harness code.

## 2. Not changed

This amendment does NOT change:

- Stage F research question;
- Arm G or Arm A corpus;
- semantic annotation definitions;
- decision-rule thresholds A-G;
- model configuration;
- prompt;
- schema;
- retry policy;
- run order;
- O-6 corpus;
- Stage D behavior;
- E3-E-SEM-001 status.

It also does not change `annotation.py`, the packet guide, packet structure, packet ids, blinding, comparison semantics, `readiness.py`, the Stage F design or spec, the historical protocol, the frozen operational guide, the contamination resolution record, either O-3 addendum, or any execution boundary. No model was called, no credential was read, no network was used, no pilot or main experiment was run, and no human annotation or calibration response was collected.

## 3. What resolved the blocker

The contamination resolution record (section 4.2) required that no human calibration start until a calibration corpus passes a deterministic contamination audit. That corpus now exists, outside the harness:

| Record | Path | SHA-256 |
|---|---|---|
| Calibration corpus | `docs/engine3/ENGINE3_STAGE_F_HUMAN_CALIBRATION_CORPUS.json` | `83e5843d10a575792379fc4f1e6b3f0570ff8f951d272889478380b03a317c46` |
| Calibration guide | `docs/engine3/ENGINE3_STAGE_F_HUMAN_CALIBRATION_GUIDE.md` | `5a1d83cbc0082b0a29c2f042854135efb6a8e0d0d2e8877f2d1a5d9a8a591584` |
| Calibration freeze manifest | `docs/engine3/ENGINE3_STAGE_F_HUMAN_CALIBRATION_FREEZE_MANIFEST.json` | `a88600bb392f9d853b07c8948ee41ce8af597f5a3025697c56dc5e6e0fefef97` |
| Calibration audit | `scripts/verify_engine3_stage_f_calibration.py` | `24994e7b956267802995d26a32ae6cf66ca29ed17d34e2b0c782a3788f7e4934` |
| Calibration tests | `tests/engine3/test_engine3_human_calibration_corpus.py` | `1457502faee7bed5f64cf4380b594fd5ec48d87f559c802155a1a0fa9d249ce0` |

The audit compares every calibration sentence against the six classes named in section 4.2 (the 48 Arm A artifacts; Arm G scenario and evaluation material; Stage E prerecorded prose; the dry-run inputs and oracle; the operational guide's worked examples; the O-5 prompt's worked examples) with the O-5 comparison rule, shared distinctive 4-grams, and banned terms. Result: 0 exact, 0 near, 0 distinctive 4-grams, 0 banned-term findings, no allowlist. The freeze manifest records the full result.

The contamination resolution record and both O-3 addenda still read the blocker `OPEN`. They are hash-bound history and are not edited. The freeze manifest and the harness manifest carry the current status.

## 4. Harness manifest

| Manifest field | Value |
|---|---|
| `pre_run_state_current.updated_by` | `HARNESS_AMENDMENT_004` |
| `pre_run_state_current.calibration_contamination_audit` | `RESOLVED_FROZEN` (was `OPEN`) |
| `pre_run_state_current.calibration` | freeze manifest, corpus, and guide paths with exact SHA-256 |
| `pre_run_state_current.pilot_readiness_blockers` | O-3 `PRE_RUN_REQUIRED` only |
| `pre_run_state_current.status` | O-3 `PRE_RUN_REQUIRED`, O-5 `RESOLVED_FROZEN`, O-6 `RESOLVED_FROZEN` (unchanged) |
| `pre_run_state_current_superseded` | The `HARNESS_AMENDMENT_003` entry is kept; the snapshot this amendment replaces is appended with the calibration blocker `OPEN` |

**Pilot readiness stays false.** O-3 is `PRE_RUN_REQUIRED`: no annotator identities, and the O-6 author cross-check waits on it.

## 5. Files and hashes

| File | Change | SHA-256 before | SHA-256 after |
|---|---|---|---|
| `scripts/verify_engine3_stage_f_harness.py` | `PILOT_BLOCKERS` is O-3 only; `CALIBRATION_BOUND` (exact paths and hashes); current-state calibration check; second superseded snapshot; `AMENDMENTS` gains `HARNESS_AMENDMENT_004` | `2c0123fb46cbf71cc5295d6720c24dc84d5de4cf02b8082bd3e69f59a36c084d` | `df028084a69e26448adbc804f3483e073120af047a9f5cdc5988b820d036c055` |
| `tests/engine3/test_engine3_stage_f_harness_manifest.py` | Amendment list `[001, 002, 003, 004]`; current blockers O-3 only; four tests for this amendment (section 6) | `b87ac85f55866ac17702e6bd0ec18e6483551c73b81507da2f28acbbfcc63872` | `a7b6360ae22be7c5535dc5c807ee339fce5b737d4ff5fbf6ed8d2b2ff10d1e45` |
| `docs/engine3/ENGINE3_STAGE_F_HARNESS_IMPLEMENTATION_REPORT.md` | Status line; sections 1, 11, 13, 14; section 19 added | `270c82fb9a523c598e6b0d9c5394aba8f2f3e221d674787fc6818e2d80e15441` | `fca5fc37d693be55e06e9a524e5173b1fb122ecd65675421df14bcc5efe3dedf` |
| `docs/engine3/ENGINE3_STAGE_F_HARNESS_MANIFEST.json` | Amendment recorded with this record's hash; current state; superseded snapshot; harness hashes regenerated; test counts | `245f2cabd2ad22e847e937cc089c0ecb2f19086aa1e1b6788cfaec3e232868d1` | Recorded in `docs/engine3/ENGINE3_STAGE_F_O5_POSTFREEZE_REBINDING_003.json`. The manifest stores this record's hash, so this record cannot store the manifest's. |
| `scripts/verify_engine3_stage_f_o5_freeze.py` (outside the harness) | Accepts exactly `O5_POSTFREEZE_REBINDING_001`, `_002`, then `_003`, in order; binds `_002` by its exact hash | `bd8f8add185dd1bb6ba299541d26866c3ed7e444ecce53c42b4a4711b2184472` | Recorded in `docs/engine3/ENGINE3_STAGE_F_O5_POSTFREEZE_REBINDING_003.json`. |
| `tests/engine3/test_engine3_o5_postfreeze_rebinding.py` (outside the harness) | Chain tests for `O5_POSTFREEZE_REBINDING_003`; the verifier hash `_002` recorded is now a fixed constant | `824c9ec71e42337584f62953ee9138d4b6134361e3c6fbf218334a291688f69c` | not recordable here (it reads this record) |
| `docs/engine3/ENGINE3_STAGE_F_O5_POSTFREEZE_REBINDING_003.json` | New (binds this record) | none | Not recordable here |

No other harness file changed. The calibration corpus, guide, freeze manifest, audit, and tests (section 3) are new and outside the harness.

## 6. Tests

- **Amendment recorded.** `HARNESS_AMENDMENT_004` is the fourth amendment, hash-bound, with no harness code file modified; the three earlier amendment record hashes are unchanged.
- **Current calibration status.** Bound to the exact freeze manifest, corpus, and guide; the freeze manifest shows a passed audit, no responses, O-3 `PRE_RUN_REQUIRED`, and pilot readiness false.
- **History.** The snapshot this amendment supersedes keeps the calibration blocker `OPEN` and the earlier blockers.
- **Readiness.** Built from the frozen O-5 configuration and O-6 supplement, readiness still fails on O-3.
- **O-5 rebinding chain.** The verifier accepts exactly 001, 002, then 003; a changed 002 record, a broken chain, an extra file, a different identity, or an unrecorded verifier change fails.

## 7. Effect on O-5

The harness verifier, the harness manifest test, the implementation report, and the harness manifest are locked inputs of the O-5 freeze. `ENGINE3_STAGE_F_O5_POSTFREEZE_REBINDING_003.json` binds their new hashes, binds `O5_POSTFREEZE_REBINDING_002` by its exact hash, binds this record and the calibration freeze manifest, and attests the unchanged O-5 configuration. Nothing the model receives changed. There is no generic amendment bypass.

## 8. Status after amendment

| Item | Status |
|---|---|
| `HARNESS_AMENDMENT_004` | `APPLIED`; owner approval `APPROVED` |
| `CALIBRATION_CORPUS_CONTAMINATION_AUDIT_REQUIRED` | `RESOLVED_FROZEN` |
| O-3 | `PRE_RUN_REQUIRED` |
| O-5 | `RESOLVED_FROZEN` |
| O-6 | `RESOLVED_FROZEN` |
| `annotation_record_provenance_hashes` | `RESOLVED` |
| `pilot_ready` | false |
| `live_model_executed`, `pilot_executed`, `main_experiment_executed` | false |
| Calibration responses collected | none |
| E3-E-SEM-001 | unresolved |

## 9. Claims boundary

A governance status change in an offline harness that has never been executed. It collects no annotation or calibration response and reports no agreement figure. Passing the calibration audit shows only that calibration text does not match the prohibited material under fixed token rules. It is not evidence of annotation reliability, semantic validation, purchase prediction, sales lift, production security, or BAEC theory.
