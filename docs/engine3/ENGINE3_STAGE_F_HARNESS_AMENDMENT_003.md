# BAEC Engine 3: Stage F Harness Amendment 003

**Project:** BAEC Trigger Intelligence\
**Engine:** BAEC Engine 3: Re-engagement Preparation\
**Stage:** F (offline experimental harness)\
**Amendment:** `HARNESS_AMENDMENT_003`\
**Date:** 2026-10-15\
**Owner decision:** approved (close three remaining governance gaps before any human annotation)\
**Base commit:** `20513e03e9e04cdee95b21f7e72ef609694b5ec0`\
**Harness status:** `LOCKED_OFFLINE_HARNESS_NOT_EXECUTED` (unchanged)

This is an IMPLEMENTATION and governance change. It is not a research finding. `HARNESS_AMENDMENT_001`, `HARNESS_AMENDMENT_002`, and their records are unchanged.

---

## 1. Scope

This amendment is limited to:

- annotation-record provenance completeness;
- historical/current O-5 status separation;
- broader annotator-exposure governance.

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

It also does not change the packet guide (`ANNOTATION_GUIDE`), packet structure, packet ids, blinding, comparison semantics, `readiness.py`, the Stage F design or spec, the historical protocol, the frozen operational guide, or any execution boundary. No model was called, no credential was read, no network was used, no pilot or main experiment was run, and no human annotation was collected.

## 3. Annotation record provenance

`ENGINE3_STAGE_F_O3_ANNOTATOR_ELIGIBILITY_ADDENDUM.md` (section 3) and `ENGINE3_STAGE_F_HUMAN_REFERENCE_CONTAMINATION_RESOLUTION_001.md` (section 4.1) required three hashes on every future human annotation record and recorded the requirement as `ANNOTATION_RECORD_PROVENANCE_HASHES_REQUIRED` (open). This amendment implements it.

`PrimaryAnnotation` (Primary A and Primary B) and `Adjudication` in `experiments/engine3_stage_f/annotation.py` now carry:

| Field | Value | Rule |
|---|---|---|
| `locked_protocol_sha256` | `cba979c91cdcc96f419b0f3758cadd651f3427064a7f66ebebd23929c623b5a4` | SHA-256 of the historical locked protocol `ENGINE3_STAGE_F_HUMAN_REFERENCE_ANNOTATION_PROTOCOL.md`. Must equal `LOCKED_PROTOCOL_SHA256` exactly. |
| `operational_guide_sha256` | `fb4f0510254649dadfc68c06bb76bd1dfab73e2d33bc0db325faf791e61b9011` | SHA-256 of the frozen operational guide `ENGINE3_STAGE_F_HUMAN_REFERENCE_OPERATIONAL_GUIDE.md`, as bound by its freeze manifest. Must equal `OPERATIONAL_GUIDE_SHA256` exactly. |
| `annotation_packet_sha256` | per packet | SHA-256 of the UTF-8 bytes of `AnnotationPacket.to_json()`: the canonical JSON of the exact blinded packet shown to the reviewer, guide included. |

**Packet hashing rule.** `annotation_packet_sha256(packet) = SHA-256(UTF-8(canonical_json(packet)))`. The packet contains only human-visible content: packet id, opaque context label, artifact type, audience, rendered text, opaque segments with their text, grounding items with their nine disclosure fields, goal and channel scope, and the packet guide. Any change to that content changes the hash. Model annotations, model segment ids, artifact ids, run binding, arm, repeat, configuration id, and Stage D results never enter a packet (the builder cannot receive them), so they cannot change the hash. `packet_id` remains the deduplication key and is not a substitute: two packets can share a `packet_id` and differ in visible content.

**Builder.** `record_provenance(packet)` returns the three hashes for a record made on that packet. It takes only the packet.

**Validation (fail closed).**

- Each field must be lowercase SHA-256 hex; a missing field cannot be constructed.
- `locked_protocol_sha256` and `operational_guide_sha256` must equal the exact frozen hashes above.
- `finalize_reference` rejects any Primary A, Primary B, or adjudication record whose `annotation_packet_sha256` is not the hash of the packet being finalized.
- `protocol_version_sha256` is removed from both record types. No record existed that needed it (no human annotation has been collected), so it is not kept for compatibility. A record that supplies only `protocol_version_sha256` cannot be constructed, so nothing can fall back to it. Before this amendment, `finalize_reference` checked only that all records shared one protocol hash; that check is replaced by the exact checks above.

The dry-run test adapter populates the three fields through `record_provenance`. The dry-run fixtures are unchanged.

## 4. Historical and current O-5 status

The O-5 freeze (`726a67a`) resolved O-5, but the harness manifest's `pre_run_state_current` still read O-5 `PRE_RUN_REQUIRED` (listed in the O-5 freeze manifest's `records_not_updated_by_this_freeze` and in `O5_POSTFREEZE_REBINDING_001`'s `not_updated`). History is kept and the current state is corrected:

| Manifest field | Value |
|---|---|
| `pre_run_required` (with `pre_run_required_label`) | Unchanged lock-time list: O-3, O-5, O-6 `PRE_RUN_REQUIRED` on 2026-10-14. Historical. |
| `o5_status_at_harness_lock` | `PRE_RUN_REQUIRED` |
| `o5_current_status` | `RESOLVED_FROZEN` |
| `pre_run_state_current.status` | O-3 `PRE_RUN_REQUIRED`, O-5 `RESOLVED_FROZEN`, O-6 `RESOLVED_FROZEN` |
| `pre_run_state_current.o5` | O-5 freeze manifest `4a28ed081ab4b5a9899ad3888cb695f7e366491302ff37a66134897f912355e3`; frozen configuration `e74c4a46fd771c12e48ab78f492026f97250a2d0a90ee49158ee544fb6c86237` (configuration SHA-256 `5ec4f582d2a05e8fe2768e9030e8eadf1315c1cced89d5acc0de1916e28e7f21`); `O5_POSTFREEZE_REBINDING_001` `76119f1bcfc0096c2abf595d2e21002ab7bcbb5aa2a1d1efee7161cb6e0f9ba4`; `O5_POSTFREEZE_REBINDING_002` by path (it binds this manifest's hash) |
| `pre_run_state_current_superseded` | The previous current-state snapshot (O-5 `PRE_RUN_REQUIRED`), kept and labelled superseded by this amendment |

`readiness.py` is not changed. Its O-5 check already accepts the frozen configuration (the O-5 record's `readiness_mapping`); a test builds that configuration and shows readiness reports no O-5 item.

**Pilot readiness stays false.** Remaining blockers:

- O-3 `PRE_RUN_REQUIRED` (no annotator identities; the O-6 author cross-check waits on it);
- `CALIBRATION_CORPUS_CONTAMINATION_AUDIT_REQUIRED` `OPEN` (pre-human-annotation blocker; no calibration corpus exists and none is built here).

## 5. Annotator exposure (O-3)

The first eligibility addendum is hash-bound by the contamination resolution record, so it is not edited. The broadened rule is recorded in `ENGINE3_STAGE_F_O3_ANNOTATOR_ELIGIBILITY_ADDENDUM_002.md`:

> Any Primary A, Primary B, or Adjudicator who has previously seen labelled evaluation-derived examples from any project artifact is presumptively ineligible for the blinded human-reference collection unless the owner later approves a documented exception.

Exposure means actually having seen labelled content originating from Arm A, Stage E, or Stage F evaluation prose, in any document. Working on the repository is not, by itself, exposure. No identity matching is added beyond the existing alias rules. No exception is approved. No annotator identity is supplied. O-3 remains `PRE_RUN_REQUIRED`.

## 6. Files and hashes

| File | Change | SHA-256 before | SHA-256 after |
|---|---|---|---|
| `experiments/engine3_stage_f/annotation.py` | Three provenance fields; exact constants; `annotation_packet_sha256`; `record_provenance`; `protocol_version_sha256` removed; packet-hash binding in `finalize_reference` | `951b4fb34a5bdfd01a3e8966db457e23296014b5f8e0791b280958798b1a5a01` | `247ee4b00787393fbb47dbd7f564d6667bc398db669a93a73906b2b2bd926210` |
| `tests/engine3/stage_f_adapter.py` | Records built with `record_provenance(packet)`; guide hash helper | `a030631228755de648671bc9d45b47df7d33479d07c111ec581bc169ea7623d7` | `76a84c2bad3f2be8ebc58be8e73050fcd3c4f6f4a763603ca97e63d332953bff` |
| `tests/engine3/test_engine3_stage_f_annotation.py` | Three tests moved off `protocol_version_sha256`; provenance tests (section 7) | `c63a3aa14a760775d5a658eb368a1879ca2994bb6e8c358b417ea74714728b32` | `d40e31d4ce37755496903ed2c1d3bf772bdd8476fa96307aea7e3b78424eb79d` |
| `tests/engine3/test_engine3_stage_f_harness_manifest.py` | Amendment list `[001, 002, 003]`; current O-6 test reads O-5 `RESOLVED_FROZEN`; O-5, readiness, and O-3 tests (section 7) | `69e9d8bec729324ca28be2023a6e6e0b140733fc361b3f6341adc03d3eaf4864` | `b87ac85f55866ac17702e6bd0ec18e6483551c73b81507da2f28acbbfcc63872` |
| `scripts/verify_engine3_stage_f_harness.py` | Current O-5 state and exact bindings; superseded snapshot; pilot blockers; O-3 addenda; `AMENDMENTS` gains `HARNESS_AMENDMENT_003` | `cc6d57e7351ba3d15c5919843fc7fa0ae810511445ef70959fc73855c3f84e61` | `2c0123fb46cbf71cc5295d6720c24dc84d5de4cf02b8082bd3e69f59a36c084d` |
| `docs/engine3/ENGINE3_STAGE_F_HARNESS_IMPLEMENTATION_REPORT.md` | Status line; sections 1, 7, 11, 14; section 18 added | `b1d7445aeb0c5e2a15f7cac873e49780afda00e1b4a339a3eab6be5ce8bff6a6` | `270c82fb9a523c598e6b0d9c5394aba8f2f3e221d674787fc6818e2d80e15441` |
| `docs/engine3/ENGINE3_STAGE_F_HARNESS_MANIFEST.json` | Amendment recorded with this record's hash; O-5 historical/current fields; O-3 addenda; harness hashes regenerated; test counts | `c19780e7adf7464e6f8ee9c5cd96e9f06f551e4e395bf4fb495e7aadcac4a934` | Recorded in `ENGINE3_STAGE_F_O5_POSTFREEZE_REBINDING_002.json`. The manifest stores this record's hash, so this record cannot store the manifest's. |
| `scripts/verify_engine3_stage_f_o5_freeze.py` (outside the harness) | Accepts exactly `O5_POSTFREEZE_REBINDING_001` then `O5_POSTFREEZE_REBINDING_002`, in order | `86c744658835edf4297b7c2368b955a113fc9bb3ad9fb3dfaebc32a26732b3fd` | Recorded in `ENGINE3_STAGE_F_O5_POSTFREEZE_REBINDING_002.json`. The verifier stores this record's hash. |
| `tests/engine3/test_engine3_o5_postfreeze_rebinding.py` (outside the harness) | Chain tests for `O5_POSTFREEZE_REBINDING_002` | `d6da8709649edeb2598cf7efb045ca46b0a9bca6526ebb425d855382d7fe043e` | `824c9ec71e42337584f62953ee9138d4b6134361e3c6fbf218334a291688f69c` |
| `docs/engine3/ENGINE3_STAGE_F_O3_ANNOTATOR_ELIGIBILITY_ADDENDUM_002.md` | New | none | `9490a7213625a3e2e13cae1b98b57c47223ac60e56e69ff8f51af4f1ce72f5bd` |
| `docs/engine3/ENGINE3_STAGE_F_O5_POSTFREEZE_REBINDING_002.json` | New (binds this record) | none | Not recordable here |

No other file changed. In particular these are byte-identical: `readiness.py`, the dry-run fixtures, the historical protocol, the operational guide and its freeze manifest, change record, and verifier, the first O-3 addendum, the contamination resolution record, the O-3 candidate and review, the O-5 frozen configuration and freeze manifest, `O5_POSTFREEZE_REBINDING_001`, the O-6 corpus and freeze manifest, and every Stage A to E and Stage F design artifact.

## 7. Tests

- **Provenance, every record.** For every dry-run packet with a human reference, Primary A, Primary B, and (where present) adjudication records carry exactly the three hashes, each equal to an independent recomputation from the files and the packet.
- **Constants.** `LOCKED_PROTOCOL_SHA256` and `OPERATIONAL_GUIDE_SHA256` equal the files' hashes and the operational guide freeze manifest.
- **No fallback.** Neither record type has a `protocol_version_sha256` field; a record given only that field cannot be built; a record with the protocol hash copied into all three slots fails.
- **Fail closed.** Each of the three fields set to `None`, empty, malformed, uppercase, short, or a wrong well-formed hash fails, for all three record roles; a record missing any field cannot be built; a record made on another packet with the same `packet_id` is rejected.
- **Packet hash sensitivity.** The hash changes when rendered text, segment text, a grounding value, a grounding disclosure field, the guide, the context label, or the goal changes. It does not change when model annotations, model segment ids, the artifact id, or run metadata change.
- **O-5.** Lock-time O-5 `PRE_RUN_REQUIRED` preserved; the superseded snapshot preserved; current O-5 `RESOLVED_FROZEN` bound to the exact freeze manifest, frozen configuration, configuration SHA-256, and `O5_POSTFREEZE_REBINDING_001`, with `O5_POSTFREEZE_REBINDING_002` referencing it; readiness built from the frozen configuration reports no O-5 item; pilot readiness false with exactly the O-3 and calibration blockers.
- **O-3.** Both addenda bound by SHA-256; the broadened rule text, "no exception", and "repository work is not exposure" present; no exceptions and no identities recorded.
- **O-5 rebinding chain.** The verifier accepts exactly 001 then 002; a broken chain, an extra file, an unapproved hash, a wrong id or amendment, a changed 001 record, or an unrecorded verifier change fails.

## 8. Effect on O-5

`annotation.py`, the adapter, the two harness tests, the harness verifier, the implementation report, and the harness manifest are locked inputs of the O-5 freeze. The original O-5 records and `O5_POSTFREEZE_REBINDING_001` are not modified. `ENGINE3_STAGE_F_O5_POSTFREEZE_REBINDING_002.json` binds the new hashes, binds `O5_POSTFREEZE_REBINDING_001` by its exact hash, and attests the unchanged O-5 configuration. The O-5 verifier accepts exactly these two rebindings, in order, with fixed file lists and hashes. There is no generic amendment bypass.

## 9. Status after amendment

| Item | Status |
|---|---|
| `HARNESS_AMENDMENT_003` | `APPLIED` |
| O-3 | `PRE_RUN_REQUIRED` |
| O-5 | `RESOLVED_FROZEN` |
| O-6 | `RESOLVED_FROZEN` |
| `annotation_record_provenance_hashes` | `RESOLVED` |
| `calibration_contamination_audit` (`CALIBRATION_CORPUS_CONTAMINATION_AUDIT_REQUIRED`) | `OPEN` |
| Broadened O-3 exposure rule | `ADOPTED` (future O-3 condition; no exception) |
| `pilot_ready` | false |
| `live_model_executed`, `pilot_executed`, `main_experiment_executed` | false |
| E3-E-SEM-001 | unresolved |

## 10. Claims boundary

A record-schema and governance change in an offline harness that has never been executed. It collects no annotation and reports no agreement figure. It is not evidence of annotation reliability, semantic validation, purchase prediction, sales lift, production security, or BAEC theory.
