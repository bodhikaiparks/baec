# BAEC Engine 3: Stage F Human-Reference Contamination Resolution 001

**Project:** BAEC Trigger Intelligence\
**Engine:** BAEC Engine 3: Re-engagement Preparation\
**Stage:** F\
**Issue:** `E3F-ISSUE-HR-001`\
**Original issue record:** `docs/engine3/ENGINE3_STAGE_F_HUMAN_REFERENCE_CONTAMINATION_ISSUE.md`, SHA-256 `4d8af3e2bd70289b31608d1052d54b87085b6592fd0fcc320f7cde7dd653114e` (unchanged; it still reads `PRE_HUMAN_ANNOTATION_BLOCKER` as the state at the O-5 freeze)\
**Date:** October 15, 2026\
**Resolution:** `RESOLVED_DECONTAMINATED_OPERATIONAL_PATH`

This record resolves the issue by giving human annotators a decontaminated path. The historical protocol itself is **not** decontaminated. It is preserved unchanged as the locked design record, and its section 7 still contains the labelled evaluation examples. Annotators are not shown it.

This is an IMPLEMENTATION record. It is not a research finding. No model was called, no credential was read, no network was used, no pilot or main experiment was run, and no human annotation exists.

---

## 1. The two exposures and how each is closed

| Exposure (issue record) | Closure |
|---|---|
| Labelled worked examples in the historical protocol, section 7, overlap Arm A prose (18 lines) | Annotators use the frozen operational guide. It has the same rules word for word and examples from a fictional account. Anyone who has seen the historical examples is presumptively ineligible (O-3 addendum). |
| The packet guide in every blinded packet used evaluation values ("9.00% -> 9%", "more than 8% -> above 8%") | `HARNESS_AMENDMENT_002` replaced only those examples (`$46.00 -> $46`, `over $40 -> higher than $40`). The rule text is unchanged. |

## 2. Resolution criteria

Each criterion is checked by `scripts/verify_engine3_stage_f_operational_guide.py` (check 6). That check fails if this record claims resolution while any criterion fails.

| # | Criterion | Met | Evidence |
|---|---|---|---|
| 1 | Operational guide frozen | Yes | Freeze manifest status `OPERATIONAL_GUIDE_FROZEN`. The frozen guide equals the reviewed candidate except its status line: the candidate is reconstructed and its hash `5daa5df9…` matches the review. |
| 2 | Semantic rules preserved | Yes | 172 protocol rule lines = 132 verbatim + 28 example-only substitutions + 1 provenance line + 11 administration lines omitted. 0 unexplained guide lines. |
| 3 | Worked examples contamination-clean | Yes | 34 example fragments against 321 evaluation sentences: 0 exact, 0 near, 0 shared distinctive 4-grams, 0 banned terms. Two locked normative definitions are allowlisted with reasons. |
| 4 | Packet instructions contamination-clean | Yes | Packet guide: 0 evaluation-derived strings and 0 banned terms. In every dry-run packet (12), the instructional guide contains none of `9.00%`, `9%`, `above 8%`, `more than 8%`, or the evaluation names. A companion test shows the case content may still carry them. |
| 5 | Historical protocol preserved | Yes | SHA-256 `cba979c9…` equals the O-5-bound hash. |
| 6 | Future annotators directed to the operational guide | Yes | The O-3 eligibility addendum names the guide as the only annotator guide and reinterprets O-3 checklist item 2. The guide's "Before you begin" section says the same. |
| 7 | No human annotations collected | Yes | O-3 record `annotation_performed = false`; O-5 `experiment_results_exist = false`; freeze manifest `no_human_annotations_collected = true`. |

All seven criteria are met, so `E3F-ISSUE-HR-001` is recorded as `RESOLVED_DECONTAMINATED_OPERATIONAL_PATH`.

## 3. Bound records

| Record | SHA-256 |
|---|---|
| Original issue record | `4d8af3e2bd70289b31608d1052d54b87085b6592fd0fcc320f7cde7dd653114e` |
| Historical protocol (locked, unchanged) | `cba979c91cdcc96f419b0f3758cadd651f3427064a7f66ebebd23929c623b5a4` |
| Operational guide (frozen) | `fb4f0510254649dadfc68c06bb76bd1dfab73e2d33bc0db325faf791e61b9011` |
| Operational guide freeze manifest | `56952f30343a8844883e20b4d60bef1e3e07af8a4dafebd5a937870505cd2029` |
| Operational guide change record (as reviewed) | `91325f2a55ffc3d8044a9f8c690f76259d73a611a56e543c9d11be2c05abf7a5` |
| `HARNESS_AMENDMENT_002` record | `76d20bce96d4ac2a1617e3a71c4cf96b278570e71da648fb1423f17dc76e2d87` |
| `experiments/engine3_stage_f/annotation.py` (post-amendment) | `951b4fb34a5bdfd01a3e8966db457e23296014b5f8e0791b280958798b1a5a01` |
| Harness manifest (post-amendment) | `c19780e7adf7464e6f8ee9c5cd96e9f06f551e4e395bf4fb495e7aadcac4a934` |
| `O5_POSTFREEZE_REBINDING_001` | `76119f1bcfc0096c2abf595d2e21002ab7bcbb5aa2a1d1efee7161cb6e0f9ba4` |
| O-3 eligibility addendum | `b66e57b5c546346c031913981091cce21c6b00863b633b62143cdedfad43632f` |

The original O-5 freeze manifest (`4a28ed08…`) and frozen configuration (`e74c4a46…`) are byte-identical to their freeze state.

## 4. Requirements carried forward

These are not resolution criteria for `E3F-ISSUE-HR-001`. They are pre-human-annotation requirements created by the same owner decision.

### 4.1 Annotation record provenance

Every future human annotation record must carry `locked_protocol_sha256`, `operational_guide_sha256`, and `annotation_packet_sha256`, as well as the existing blinded packet and context identifiers (O-3 addendum, section 3):

- `locked_protocol_sha256` identifies the governing historical design;
- `operational_guide_sha256` identifies the decontaminated rule presentation used in practice;
- `annotation_packet_sha256` is the SHA-256 of the packet's canonical JSON, and proves what the annotator actually saw.

The current harness record types carry only `protocol_version_sha256`. Adding these fields is a record-schema change outside `HARNESS_AMENDMENT_002`'s approved scope, so it is **not implemented**. It is recorded as `ANNOTATION_RECORD_PROVENANCE_HASHES_REQUIRED` (open) and must be closed by a separately approved change before any human annotation is collected.

### 4.2 Calibration corpus contamination audit

No human calibration may start until the calibration corpus passes a deterministic contamination audit. The audit uses the O-5 comparison rule and banned terms, plus shared distinctive 4-grams, as in the guide verifier. It checks against:

- all 48 Arm A artifacts;
- all Arm G scenario and evaluation material (scenario strings and grounding items);
- Stage E proposal prose (all recordings and human edits);
- Stage F dry-run prose;
- the operational guide's worked examples;
- the frozen O-5 prompt's worked examples.

Calibration items may carry known or adjudicated labels for training. Their prose must not be reused from evaluation items, and it must also not reuse the guide's or the O-5 prompt's example sentences, so that calibration is not merely a repeat of the guide. They use a novel fictional domain or domains.

No calibration corpus exists in the repository, so none was audited. The requirement is recorded as `CALIBRATION_CORPUS_CONTAMINATION_AUDIT_REQUIRED` (open).

## 5. Residual exposure (recorded)

- The historical protocol, the experiment design, the spec, the original issue record, the candidate review, and `HARNESS_AMENDMENT_002` still quote the evaluation examples. They are design and governance records, not annotator material. Whether exposure through them counts for eligibility is an open owner question (O-3 addendum, section 5).
- Blinded case content (segment text and grounding items) legitimately contains evaluation values. That is the material being judged, not instruction.
- The protocol's residual leakage note (grounding content can reveal what a context tests) is unchanged.

## 6. Status

| Item | Status |
|---|---|
| `E3F-ISSUE-HR-001` | `RESOLVED_DECONTAMINATED_OPERATIONAL_PATH` |
| `HARNESS_AMENDMENT_002` | `APPLIED` |
| Operational guide | `FROZEN_OPERATIONAL_GUIDE` |
| `O5_POSTFREEZE_REBINDING_001` | `RECORDED` |
| O-3 eligibility addendum | `ADOPTED_FUTURE_O3_CONDITION` |
| `ANNOTATION_RECORD_PROVENANCE_HASHES_REQUIRED` | `OPEN` |
| `CALIBRATION_CORPUS_CONTAMINATION_AUDIT_REQUIRED` | `OPEN` |
| O-3 | `PRE_RUN_REQUIRED` |
| O-5 | `RESOLVED_FROZEN` |
| O-6 | `RESOLVED_FROZEN` |
| Pilot readiness | false |
| `live_model_executed`, `pilot_executed`, `main_experiment_executed` | false |
| E3-E-SEM-001 | not resolved |

## 7. Claims boundary

This record resolves an exposure of evaluation-derived instructional text in documents and packet instructions. It reports no annotation, no agreement figure, and no experiment result. Passing the audits shows the instructional text does not match the evaluation corpus under fixed token rules. It does not show annotators will be uninfluenced. It is not evidence about annotation reliability, model quality, semantic validation, purchase prediction, sales lift, production security, or BAEC theory.
