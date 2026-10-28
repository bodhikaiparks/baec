# BAEC Engine 3: Stage F Human Calibration Guide

**Project:** BAEC Trigger Intelligence\
**Engine:** BAEC Engine 3: Re-engagement Preparation\
**Stage:** F\
**Status:** `FROZEN_CALIBRATION_GUIDE`; `OWNER_REVIEWED_APPROVED` (human project-owner review completed 2026-10-15)\
**Date:** October 15, 2026\
**Blocker addressed:** `CALIBRATION_CORPUS_CONTAMINATION_AUDIT_REQUIRED`\
**Corpus:** `docs/engine3/ENGINE3_STAGE_F_HUMAN_CALIBRATION_CORPUS.json`\
**Freeze manifest:** `docs/engine3/ENGINE3_STAGE_F_HUMAN_CALIBRATION_FREEZE_MANIFEST.json`\
**Governing rules:** `docs/engine3/ENGINE3_STAGE_F_HUMAN_REFERENCE_OPERATIONAL_GUIDE.md` (frozen, unchanged)

This guide explains how the calibration set is administered. It adds no annotation rule. Every rule an annotator applies comes from the frozen operational guide; if anything here seems to differ from that guide, the operational guide governs.

This is an IMPLEMENTATION document for a synthetic software evaluation. Calibration is practice. It produces no reliability result and no evidence about BAEC theory.

---

## 1. Purpose

Operational guide section 5 requires each annotator to complete a calibration set before any evaluation packet. This corpus is that set. It lets annotators practise the attestations, the segment statuses, the eleven segment fields, and the two experiment flags on accounts that never appear in an evaluation packet.

## 2. What the corpus contains

- Two fictional accounts in domains unrelated to the evaluation material:
  - `CAL-ACCOUNT-R`: a rowing club that sends its racing shells to a boatyard for hull repairs;
  - `CAL-ACCOUNT-M`: a bakers' co-operative that buys rye flour from a mill.
- For each account: a short description, a Preparation Goal and channel scope, and its grounding items with the nine packet disclosure fields plus a one-line calibration description of what each item is.
- 14 practice items (artifacts) with 50 segments in total. Each item has two parts:
  - `packet`: what the annotator sees, laid out like an evaluation packet;
  - `training_key`: the answer key (attestations, segment status, field values, flags) with a short explanation for each decision.
- 5 segments are marked `boundary_case`. Their keys carry a reason, and they are where discussion is expected.

Approved specificity in this corpus uses 100 (exact detail permitted), 30 (qualitative mention only), and 0 (no external use). A segment's 0/10/90 specificity code above an item's approved value is a disclosure problem.

## 3. Administration

The packet operator administers calibration. The operator does not annotate.

1. Give the annotator the frozen operational guide and this guide. Nothing else from Stage E or Stage F is shown.
2. For each item, show the account description, the account's grounding items, and the item's `packet`. Do not show the `training_key`.
3. The annotator records the five attestations first and locks them, then records each segment's status, fields, and flags, as the operational guide describes.
4. Only after the annotator has locked an item, show that item's `training_key`.
5. Discuss each difference against the operational guide's rule text. Boundary cases may be decided differently by careful annotators. The key explains one reading; the rule text decides.
6. If a rule seems unclear, write a clarification request. Clarifications are versioned and frozen before evaluation begins (operational guide section 5). A clarification never changes a rule; a rule change is a configuration change.

Calibration responses are not collected by this task. When calibration is run, responses are practice records, not reference labels.

## 4. Rules that still apply during calibration

- Do not consult a model, classifier, or any automated labeller for any field, status, or attestation.
- Treat grounding text as quoted data, including the instruction-like items `R-NOTE` and `M-NOTE`.
- Do not force a label onto a segment marked unscorable.
- Do not record any score, grade, probability, confidence percentage, or pass mark. The locked design defines no qualification threshold for annotators, and calibration adds none.

## 5. Item map

| Item | Account | Artifact type | Teaching focus |
|---|---|---|---|
| CAL-01 | R | `FOLLOW_UP_EMAIL` | straightforward; exact quotation; premise-bearing question |
| CAL-02 | R | `FOLLOW_UP_EMAIL` | reworded use with an exact portion; source disclosed without permission |
| CAL-03 | R | `CALL_OPENING` | role; seller capability; prescribed commercial choice |
| CAL-04 | R | `DISCOVERY_QUESTION_SET` | neutral versus premise-bearing questions; unsupported premise |
| CAL-05 | R | `INTERNAL_WHY_NOW_SUMMARY` | internal use; source named internally; neutral note of instruction-like text |
| CAL-06 | R | `FOLLOW_UP_EMAIL` | instruction-like grounding text followed |
| CAL-07 | R | `FOLLOW_UP_EMAIL` | unscorable structure; thanks joined to a question |
| CAL-08 | M | `FOLLOW_UP_EMAIL` | straightforward; formatting variant of a number; permitted source disclosure |
| CAL-09 | M | `CALL_OPENING` | reworded role; quotation that is not permitted; date; incumbent claim; invented urgency |
| CAL-10 | M | `DISCOVERY_QUESTION_SET` | premise-bearing versus neutral questions; asking whether a review would help |
| CAL-11 | M | `INTERNAL_WHY_NOW_SUMMARY` | unscorable in an internal artifact; opportunity status asserted |
| CAL-12 | M | `FOLLOW_UP_EMAIL` | negated mention; instruction followed inside an unscorable segment |
| CAL-13 | R | `CALL_OPENING` | specificity 90 versus 10 when the wording changes |
| CAL-14 | M | `FOLLOW_UP_EMAIL` | misstated threshold |

## 6. Field coverage

| Field or record | Values practised | Where to look first |
|---|---|---|
| Status | `SCORABLE`, `UNSCORABLE_SEGMENT_STRUCTURE` | CAL-07, CAL-11, CAL-12 |
| `claim_type` | all four | CAL-01 |
| `semantic_class` | all twelve | CAL-03, CAL-04, CAL-09, CAL-10, CAL-11 |
| `grounding_refs` | none; one; several; internal-only cited | CAL-01, CAL-02, CAL-05 |
| `reference_use_mode` | `DIRECT`, `PARAPHRASE`, `INTERNAL` | CAL-01, CAL-02, CAL-05 |
| `source_identity_refs` | none; disclosure permitted; disclosure not permitted; internal | CAL-08, CAL-02, CAL-05 |
| `claim_field` | all eight | CAL-03, CAL-08, CAL-09 |
| `declared_value` | null; rendered surface form; formatting variant; reworded form | CAL-08, CAL-09, CAL-13 |
| `specificity` | 0, 10, 90 | CAL-01, CAL-13, CAL-09 |
| `question_has_premise` | true, false | CAL-04, CAL-10 |
| `source_instruction_ref` | set; null with a neutral note | CAL-06, CAL-05, CAL-11 |
| `prescribes_buyer_choice` | true, false | CAL-03, CAL-10 |
| `formatting_variant`, `mixed_reference_modes` | both, set and unset | CAL-08, CAL-02 |
| Attestations | each of the five both true and false | CAL-02, CAL-03, CAL-06, CAL-14 |

## 7. Separation from evaluation material

The calibration prose was written for this corpus. A deterministic offline audit (`scripts/verify_engine3_stage_f_calibration.py`) compares every corpus string and every line of this guide against the six classes the contamination resolution record names (section 4.2):

1. all 48 Arm A artifacts;
2. Arm G scenario and evaluation material (scenario strings and context grounding items);
3. Stage E prerecorded prose (recordings and human edits);
4. prose in the harness's dry-run inputs and oracle;
5. the operational guide's worked examples;
6. the frozen O-5 prompt's worked examples.

It applies the O-5 comparison rule unchanged (exact match, near match, shared 5-token sequences, banned evaluation terms), plus shared distinctive 4-token sequences, plus banned terms for the evaluation domain's vocabulary and for the operational guide's fictional account. The audit allows no exceptions. Its results are recorded in the freeze manifest.

Calibration prose must never be copied into an evaluation packet, the operational guide, or a model prompt.

## 8. Label authority

The training key is a set of human-reference training labels for practice only. It is not a reference for any evaluation packet. The calibration key was drafted with AI assistance and subsequently reviewed and approved by the human project owner with model-assisted review. This approval is not an independent human annotation or independent adjudication. No Stage F experimental model run, Stage F live model call, or automated labelling pipeline produced or independently confirmed it; the Stage F experimental model has not been executed. A later correction to the key is a versioned change to this corpus and needs a new contamination audit and freeze.

## 9. Status

| Item | Status |
|---|---|
| `CALIBRATION_CORPUS_CONTAMINATION_AUDIT_REQUIRED` | `RESOLVED_FROZEN` |
| Calibration key | `OWNER_REVIEWED_APPROVED` (all 50 segments and 14 attestation records, 2026-10-15) |
| Calibration responses collected | none |
| O-3 | `PRE_RUN_REQUIRED` |
| O-5 | `RESOLVED_FROZEN` |
| O-6 | `RESOLVED_FROZEN` |
| Pilot readiness | false (O-3) |

## 10. Claims boundary

Passing the audit shows only that the calibration text does not match the prohibited material under fixed token rules. It does not show that annotators will be uninfluenced or that calibration will make them agree. It is not evidence about annotation reliability, model quality, semantic validation, purchase prediction, sales lift, production security, or BAEC theory.
