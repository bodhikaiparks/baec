# BAEC Engine 3: Stage F Harness Amendment 002

**Project:** BAEC Trigger Intelligence\
**Engine:** BAEC Engine 3: Re-engagement Preparation\
**Stage:** F (offline experimental harness)\
**Amendment:** `HARNESS_AMENDMENT_002`\
**Date:** 2026-10-15\
**Owner decision:** approved (human-reference decontamination, `E3F-ISSUE-HR-001`)\
**Base commit:** `726a67a320b1e05a20ef6ae608e0e41e72bfc44c`\
**Harness status:** `LOCKED_OFFLINE_HARNESS_NOT_EXECUTED` (unchanged)

This is an IMPLEMENTATION change to instructional text shown to human annotators. It is not a research finding. `HARNESS_AMENDMENT_001` and its record are unchanged.

---

## 1. Reason

Every blinded annotation packet carries the packet guide (`ANNOTATION_GUIDE` in `experiments/engine3_stage_f/annotation.py`). Three of its strings illustrated a convention with values from the Alder Ridge evaluation contexts. Human annotators would therefore see evaluation-derived examples while annotating the evaluation items. This is one of the two exposures recorded under `E3F-ISSUE-HR-001`. The candidate review (`ENGINE3_STAGE_F_HUMAN_REFERENCE_DECONTAMINATION_REVIEW.md`, section 6) proposed this amendment, and the owner approved it.

## 2. Allowed semantic scope

Only the contaminated **examples** inside the packet guide are replaced, with values from the fictional account used by the frozen O-5 prompt and the frozen operational guide. The rule text around each example is unchanged character for character.

| Packet guide path | Before | After |
|---|---|---|
| `specificity_convention.90` | `exact or near-exact factual detail exposed, including formatting-only variants such as 9.00% -> 9%` | `exact or near-exact factual detail exposed, including formatting-only variants such as $46.00 -> $46` |
| `reference_use_convention.formatting_only_numeric_variant` | `may be coded DIRECT (example 9.00% -> 9%) and is flagged formatting_variant` | `may be coded DIRECT (example $46.00 -> $46) and is flagged formatting_variant` |
| `reference_use_convention.semantically_equivalent_rewording` | `coded PARAPHRASE (example more than 8% -> above 8%)` | `coded PARAPHRASE (example over $40 -> higher than $40)` |

The two substitutions are exactly:

- `9.00% -> 9%` became `$46.00 -> $46`;
- `more than 8% -> above 8%` became `over $40 -> higher than $40`.

Each new example teaches the same distinction as the old one:

- a formatting-only numeric variant is direct use, flagged `formatting_variant`, at specificity 90;
- a semantically equivalent rewording is paraphrase.

## 3. No experiment rule changed

This amendment does not change:

- field meanings, enum meanings, or vocabularies;
- annotation-status meanings (`SCORABLE`, `ADJUDICATION_REQUIRED`, `UNSCORABLE_SEGMENT_STRUCTURE`);
- the specificity convention (0/10/90; 50 excluded) or any rule text in the packet guide;
- Stage D behaviour, comparison semantics, or false-pass and false-refusal definitions;
- decision rules A to G, their thresholds, or their arm scopes;
- Arm G and Arm A definitions, sizes, or corpora (the O-6 corpus and freeze are unchanged);
- the O-5 model configuration, prompt, schema, or run order (the packet guide is never sent to the model);
- packet structure, packet ids, deduplication, ordering, or blinding;
- the locked Stage F design, the experiment spec, or the historical human-reference protocol;
- live-model authorization or any execution boundary.

The locked spec still contains the historical examples. The packet guide now differs from the spec only by the approved example substitution, and the tests check exactly that.

## 4. Files and hashes

| File | Change | SHA-256 before | SHA-256 after |
|---|---|---|---|
| `experiments/engine3_stage_f/annotation.py` | Three example substitutions (section 2) | `07f169b72d35872bf06bb1a06eb486974b5807bfbff66f61be62b1ef2a53e2fd` | `951b4fb34a5bdfd01a3e8966db457e23296014b5f8e0791b280958798b1a5a01` |
| `tests/engine3/test_engine3_stage_f_annotation.py` | Spec equality allows only the approved substitution; exact new values; per-packet guide regression; case-content separation | `8966bef36c84113ac77b7be148c6ab76eba85ac9350c67c3819aa05a84ac79b3` | `c63a3aa14a760775d5a658eb368a1879ca2994bb6e8c358b417ea74714728b32` |
| `tests/engine3/test_engine3_stage_f_harness_manifest.py` | Amendment list `[001, 002]`; checks the 002 entry; `HARNESS_AMENDMENT_001` record hash unchanged | `9e7d62503eca140804dc5d2a835810abe1c48a3d3a89881803fef3b1b4a30efd` | `69e9d8bec729324ca28be2023a6e6e0b140733fc361b3f6341adc03d3eaf4864` |
| `scripts/verify_engine3_stage_f_harness.py` | `AMENDMENTS` gains `HARNESS_AMENDMENT_002` | `ff8dd103084fbf7e260cb798e3865b4b4a29d6da340b045730e04e9a08847220` | `cc6d57e7351ba3d15c5919843fc7fa0ae810511445ef70959fc73855c3f84e61` |
| `docs/engine3/ENGINE3_STAGE_F_HARNESS_IMPLEMENTATION_REPORT.md` | Status line, section 6 note, section 14 counts, section 17 added | `d95d628948c9ff9468a5c9b677eddee987af24e49fe055f33bffd2c0dd440786` | `b1d7445aeb0c5e2a15f7cac873e49780afda00e1b4a339a3eab6be5ce8bff6a6` |
| `docs/engine3/ENGINE3_STAGE_F_HARNESS_MANIFEST.json` | Amendment recorded with this record's hash; harness hashes regenerated; test counts updated | `c16de526b2ddaa6160ed79f0140cf71e35519dabec45d2d196d47e4331233800` | Recorded in `ENGINE3_STAGE_F_O5_POSTFREEZE_REBINDING_001.json`. The manifest stores this record's hash, so this record cannot store the manifest's. |

No other harness file changed.

## 5. Tests

- **Normative equivalence.** `specificity_convention` values equal the locked spec after only the approved substitution, and the substitution replaces exactly one example. The two reference-use example strings are rebuilt from the spec's structured fields (`may_be_coded`, `flag`, `coded`) with the approved example, and must match exactly.
- **New values.** The exact post-amendment strings are asserted.
- **Regression, per packet.** For every dry-run packet, the instructional guide contains none of `9.00%`, `9%`, `above 8%`, `more than 8%`, or the evaluation names. The check is restricted to the `guide` key. A companion test shows that blinded case content in the same packets still carries `9.00%` and `more than 8%`, so the guide check is not passing just because the values occur nowhere.
- **Not vacuous.** With the pre-amendment `annotation.py`, these tests fail (14 failures: 12 packets and 2 convention tests).
- **Guide alignment.** `tests/engine3/test_engine3_human_reference_operational_guide.py` (outside the harness globs) checks that the packet guide and the frozen operational guide use the same example values for the same rules.

## 6. Effect on O-5

The O-5 freeze manifest hash-binds `annotation.py`, the two changed tests, the harness verifier, the implementation report, and the harness manifest as locked inputs. The original O-5 records are not modified. The new hashes are bound by `ENGINE3_STAGE_F_O5_POSTFREEZE_REBINDING_001.json`. The O-5 verifier accepts exactly that rebinding and nothing else.

## 7. Status after amendment

| Item | Status |
|---|---|
| Harness | `LOCKED_OFFLINE_HARNESS_NOT_EXECUTED` |
| `HARNESS_AMENDMENT_001` | applied 2026-10-15 (unchanged) |
| `HARNESS_AMENDMENT_002` | applied 2026-10-15 |
| `live_model_executed`, `api_key_used`, `external_model_sdk_used` | false |
| `network_calls` | 0 |
| `pilot_executed`, `main_experiment_executed` | false |
| E3-E-SEM-001 | not resolved |

## 8. Claims boundary

A change to instructional example text in an offline harness that has never been executed. It is not evidence of annotation reliability, semantic validation, purchase prediction, sales lift, production security, or BAEC theory.
