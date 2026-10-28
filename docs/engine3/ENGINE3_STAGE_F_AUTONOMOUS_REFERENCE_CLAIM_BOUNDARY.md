# BAEC Engine 3: Stage F-AR — Autonomous Reference Claim Boundary

**Project:** BAEC Trigger Intelligence\
**Engine:** BAEC Engine 3: Re-engagement Preparation\
**Track:** Stage F-AR — Autonomous Reference\
**Status:** `DESIGNED_OFFLINE_NOT_EXECUTED`\
**Date:** 2026-10-15\
**Protocol:** `docs/engine3/ENGINE3_STAGE_F_AUTONOMOUS_REFERENCE_PROTOCOL.md`

This document governs every claim made from Stage F-AR. It is PROPOSED for owner review. It is not a research finding.

---

## 1. Governing claim

> Stage F autonomous-reference execution measures the generating model's semantic annotations and Stage D outcomes relative to a blinded, independently generated autonomous reviewer consensus. Autonomous consensus annotations are not human reference labels, human ground truth, or evidence of human semantic reliability.

## 2. Terminology

In any claim-bearing statement, the word "reference" is always qualified as autonomous. The only labels for a Stage F-AR result are:

- `AUTONOMOUS_CONSENSUS_REFERENCE` (Reviewer A and Reviewer B agreed exactly);
- `AUTONOMOUS_ADJUDICATED_REFERENCE` (the Autonomous Adjudicator's complete annotation).

A Stage F-AR result is never called, labelled, or described as any of the following:

- human reference;
- human gold standard;
- ground truth;
- human annotation accuracy;
- human inter-rater reliability.

Disagreement between Reviewer A and Reviewer B is "autonomous-panel disagreement", never "inter-rater reliability". A Stage D difference is an "autonomous-relative potential false pass" or "autonomous-relative potential false refusal", never a confirmed false pass or false refusal.

## 3. Permitted claims

Each permitted claim names its numerator and denominator, its arm, and its Stage F-AR batch, and is never pooled across arms or batches:

- "The generator agreed with the blinded autonomous consensus on X% of scorable field judgments."
- "Y Stage D outcomes differed when the generator's annotations were replaced with the autonomous adjudicated annotation."
- "Reviewer A and Reviewer B disagreed on Z% of scorable field judgments."
- "The Autonomous Adjudicator was invoked for W of N reviewed packets."
- "0 of n compared artifacts were autonomous-relative potential false passes" (a bounded synthetic autonomous-consensus observation only).

## 4. Prohibited claims

- "The model was X% accurate relative to humans."
- "The autonomous reference is ground truth."
- "The system achieved human-level semantic reliability."
- "The experiment establishes annotation accuracy."
- "Zero autonomous false passes proves safety."
- Any claim that Stage F-AR resolves O-3, satisfies a human-track precondition, supports a human-reference reliability claim, or is (or substitutes for) the human-reference pilot.
- Any claim that Stage F-AR resolves E3-E-SEM-001.
- Any composite score, single accuracy figure, confidence percentage, or probability.
- Any claim of semantic validation, purchase prediction, sales lift, buyer response, production security, 100% accuracy, or evidence for BAEC theory.

## 5. Reporting rules

- Every rate is shown as numerator / denominator with both values.
- A zero count is stated as "0 of n" with n given; it never establishes safety.
- The adjudication rate and every item's Reviewer A / Reviewer B disagreement are reported prominently, alongside any generator agreement figure, so that panel instability is never hidden by adjudication.
- Shared reviewer families or developers are disclosed with every agreement figure (rule AR-F).
- Results are bounded to the exact generator, reviewer panel, prompts, schemas, sampling, and corpus.

## 6. Relation to the human-reference design

> Stage F-AR does not execute, constitute, substitute for, or satisfy the Stage F human-reference pilot. The term pilot remains reserved for the locked human-reference track. O-3 remains PRE_RUN_REQUIRED and human pilot_ready remains false.

The human-reference Stage F design is unchanged and remains the stronger future validation path. A Stage F-AR run over the locked 44-slot material is the AR-44 INITIAL AUTONOMOUS EXECUTION BATCH; no Stage F-AR execution changes any human-track readiness state. Nothing in Stage F-AR is evidence about human annotation, and nothing in it may be reported as a human-reference result.

## 7. Human-track exposure

> All Stage F-AR annotations, disagreements, adjudications and comparison results are labelled evaluation-derived material for O-3 exposure purposes.

Prospective future human Primary A, Primary B, or Adjudicator candidates must not be shown Stage F-AR labelled outputs before their eligibility is determined. Repository access alone does not establish exposure; actually viewing labelled evaluation-derived Stage F-AR content does.

## 8. Calibration

Calibration qualification results are a reviewer qualification diagnostic. They are never reported as an evaluation result, a reliability result, or human ground truth.
