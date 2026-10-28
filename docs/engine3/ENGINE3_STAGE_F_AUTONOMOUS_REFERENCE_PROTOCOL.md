# BAEC Engine 3: Stage F-AR — Autonomous Reference Protocol

**Project:** BAEC Trigger Intelligence\
**Engine:** BAEC Engine 3: Re-engagement Preparation\
**Track:** Stage F-AR — Autonomous Reference (parallel to the human-reference Stage F; does not modify or supersede it)\
**Status:** `DESIGNED_OFFLINE_NOT_EXECUTED`\
**Autonomous readiness:** `autonomous_reference_ready = false`\
**Date:** 2026-10-15\
**Base commit:** `ab4f3dbf0d124391dc974d60a019a456078107e5`\
**Manifest:** `docs/engine3/ENGINE3_STAGE_F_AUTONOMOUS_REFERENCE_MANIFEST.json`\
**Claim boundary:** `docs/engine3/ENGINE3_STAGE_F_AUTONOMOUS_REFERENCE_CLAIM_BOUNDARY.md`\
**Verifier:** `scripts/verify_engine3_stage_f_autonomous_reference.py`

Every rule in this protocol is labelled. **PROPOSED** rules are experiment-design choices for this track, awaiting owner review. **IMPLEMENTATION** rules are software choices. Nothing here is a research finding, and nothing here is evidence about BAEC theory.

---

## 1. Governing claim

> Stage F autonomous-reference execution measures the generating model's semantic annotations and Stage D outcomes relative to a blinded, independently generated autonomous reviewer consensus. Autonomous consensus annotations are not human reference labels, human ground truth, or evidence of human semantic reliability.

This is a narrower claim than the human-reference Stage F design. The human-reference design remains the stronger future validation path, and nothing in Stage F-AR replaces it.

## 2. Relation to the human-reference Stage F (PROPOSED)

Stage F-AR reads the locked Stage F material and changes none of it. Unchanged, and not superseded:

- the Stage F design, spec, design manifest, and research question;
- the human-reference annotation protocol, operational guide, and calibration corpus;
- Stage D, Stage E, and the Stage F decision rules A to G and their thresholds;
- Arm G and Arm A, their sizes, and the O-6 frozen Arm A supplement;
- the O-5 frozen generator configuration, prompt, schema, retry policy, and run order;
- the harness code and harness manifest.

> Stage F-AR does not execute, constitute, substitute for, or satisfy the Stage F human-reference pilot. The term pilot remains reserved for the locked human-reference track. O-3 remains PRE_RUN_REQUIRED and human pilot_ready remains false.

Stage F-AR does not resolve O-3, does not staff it, and is not a substitute for it. No Stage F-AR execution may change any human-track readiness state; the Stage F-AR code reads and writes none.

**Terminology.** Stage F-AR runs are called execution batches. The locked 44-slot material (22 Arm G scenarios × 2, run order `E3F_RUN_ORDER_V1`), when used by Stage F-AR, is the **AR-44 INITIAL AUTONOMOUS EXECUTION BATCH** (`AR_44_INITIAL_AUTONOMOUS_EXECUTION_BATCH`). The 220 Arm G and 240 Arm A runs are the AR full run (`AR_FULL_RUN`). Handwritten offline tests are `OFFLINE_DRY_RUN`. The locked harness binds generator runs with its own run-kind enum; its identifier for the 44-slot material belongs to the harness, cannot be renamed, and is mapped to AR-44. It never describes a Stage F-AR run.

**The human-reference track keeps its own preconditions.** The locked design requires O-3 before the human-reference pilot. An AR-44 or AR full-run execution never counts as any human-reference run and never satisfies a human-track precondition. Executing it needs the owner's explicit authorization naming Stage F-AR (section 11, AR-PRE-6); that authorization is the owner's decision, not something this protocol grants.

**Separation of results.** Generator runs, review records, and comparisons made under Stage F-AR live only in the Stage F-AR result namespace (`engine3_stage_f_autonomous_reference`). They are never pooled with, reused as, or written into any human-reference store. Reusing any Stage F-AR generator output in the human track would need a separate owner-approved amendment of the human track.

### 2.1 Human-track exposure (PROPOSED)

> All Stage F-AR annotations, disagreements, adjudications and comparison results are labelled evaluation-derived material for O-3 exposure purposes.

This applies `ENGINE3_STAGE_F_O3_ANNOTATOR_ELIGIBILITY_ADDENDUM_002.md` unchanged. Prospective future human Primary A, Primary B, or Adjudicator candidates must not be shown Stage F-AR labelled outputs before their eligibility is determined. Repository access alone does not establish exposure; actually viewing labelled evaluation-derived Stage F-AR content does, and makes the person presumptively ineligible unless the owner records an exception.

## 3. Autonomous review panel (PROPOSED)

| Role | Receives | Invoked |
|---|---|---|
| Reviewer A | the blinded packet, the frozen operational guide, its prompt bundle | for every packet |
| Reviewer B | the same request as Reviewer A, byte for byte | for every packet |
| Autonomous Adjudicator | the same blinded packet and guide, its own prompt bundle, the two recorded annotations as anonymized Review X and Review Y, and the items on which they disagree | only when A and B disagree on a governed item (section 5) |

**Reviewer A and Reviewer B** must be different model families. They should also differ from the frozen generator family (O-5: `Claude Sonnet`). Each annotates independently. Neither receives generator annotations, any Stage D result, any expected answer, the other reviewer's output, the scenario family, the arm, an outcome label, or any evaluation metadata not present in the blinded packet.

**The Autonomous Adjudicator** is separately configured. It never sees generator annotations or Stage D results. It receives the two annotations only after both records exist, and only as anonymized Review X and Review Y (section 4.1). It produces an explicit, complete annotation of the packet, not a choice of "X" or "Y". The fields that required adjudication are recorded.

**Family independence.** Distinct model families for A, B, the adjudicator, and the generator are preferred where practical. A and B sharing a family is not permitted. Any other shared family, or a shared model developer, is a correlation limitation: it is recorded at the correlation assessment (AR-PRE-3) and disclosed under rule AR-F.

**Reviewer model ids are not selected by this protocol.** A future reviewer-configuration freeze (AR-PRE-1, AR-PRE-2) must record, per role: model family, model developer, exact model id and version, sampling configuration, prompt bundle SHA-256, output schema version, and the retry and malformed-output policy. Any change after freeze is a new configuration and a new setup (section 8).

## 4. Blinding (PROPOSED; IMPLEMENTATION enforcement)

A reviewer request is built from exactly three inputs: the locked harness `AnnotationPacket`, the frozen operational guide (checked against SHA-256 `fb4f0510254649dadfc68c06bb76bd1dfab73e2d33bc0db325faf791e61b9011`), and a prompt bundle. The builder accepts nothing else, so the following can never reach a reviewer:

- generator annotations or generator segment ids;
- the Stage D verdict, refusal reason, or any reason code;
- Arm G or Arm A identity, scenario id, scenario family, repeat index, run id, or configuration id;
- any expected annotation, oracle, or comparison label;
- the human calibration answer key;
- another reviewer's output (except to the adjudicator, after both records exist);
- generator raw output or reasoning;
- buyer-state outcome labels.

The packet is the same blinded packet a human annotator would see (human protocol section 4.1). Its hash is the SHA-256 of its canonical JSON. Changing hidden generator metadata never changes it; changing any human-visible content always does. Reviewer A and Reviewer B receive identical request bytes, and the request carries no role.

### 4.1 Adjudicator blinding (PROPOSED; IMPLEMENTATION enforcement)

The adjudicator-visible request contains only:

- the exact blinded `AnnotationPacket`;
- the frozen operational guide;
- the adjudicator prompt bundle;
- two anonymized parsed annotation payloads, identified only as **Review X** and **Review Y**: per segment, the packet segment id, status, the eleven fields, and the two flags;
- the (packet segment id, item) pairs on which Review X and Review Y disagree.

It never exposes Reviewer A or B identity, reviewer family, reviewer model id, reviewer configuration hash, provider identity, record hashes, generator identity, generator annotations, or any Stage D result. Which review is X and which is Y is fixed by the SHA-256 of each payload's canonical content, so the order cannot reveal which came from Reviewer A. Reviewer free-text notes are not passed on (IMPLEMENTATION choice), because a note could identify its author. Changing reviewer identity metadata leaves the visible request byte-identical; changing either annotation's content changes it. The exact A and B record hashes and full reviewer provenance are bound in machine metadata outside the visible request, and in the adjudicator's record.

## 5. Consensus and adjudication logic (PROPOSED; locked here before execution)

Deterministic, per packet:

1. Reviewer A and Reviewer B annotate independently. Both records are written before anything is compared.
2. The governed items are compared segment by segment: annotation status; a reviewer's own `ADJUDICATION_REQUIRED` flag; each of the eleven fields (when both mark the segment `SCORABLE`); and the `formatting_variant` and `mixed_reference_modes` flags. Equality is exact. Notes are not governed.
3. If every governed item agrees exactly, the result is **`AUTONOMOUS_CONSENSUS_REFERENCE`** (Reviewer A's annotation, identical to B's). The adjudicator is not invoked; invoking it is refused.
4. If any governed item disagrees, the Autonomous Adjudicator is invoked with the packet, the guide, the two recorded annotations as anonymized Review X and Review Y, and the disagreeing items (section 4.1).
5. The adjudicator returns a complete annotation of every segment under the operational guide. It may mark a segment `ADJUDICATION_REQUIRED` or `UNSCORABLE_SEGMENT_STRUCTURE`; such segments stay unresolved and are never forced.
6. The result is **`AUTONOMOUS_ADJUDICATED_REFERENCE`**: the adjudicator's annotation.

Both reviewers marking the same segment `ADJUDICATION_REQUIRED` is not consensus; it is adjudicated. A disagreement with no adjudication yields no autonomous reference. Where A and B agreed but the adjudicator's complete annotation differs, that departure is recorded.

Preserved for every packet: raw Reviewer A output, parsed Reviewer A annotation, raw Reviewer B output, parsed Reviewer B annotation, raw adjudicator output when used, the final autonomous reference, every disagreement (segment, item, A value, B value), and all request and configuration hashes. Disagreement history is never overwritten; a record that would erase it is invalid.

## 6. Records and provenance (IMPLEMENTATION)

Every review record carries: `reviewer_role`, `reviewer_family`, `reviewer_model_id`, `reviewer_configuration_sha256`, `prompt_bundle_sha256`, `locked_protocol_sha256`, `operational_guide_sha256`, `annotation_packet_sha256`, `request_sha256`, `raw_output_sha256`, `parsed_annotation_sha256`, `run_slot_id`, `attempt_number`, `batch`, `provenance`, and `track`. An adjudicator record also carries `reviewed_record_sha256s` (the exact A and B records).

| Stage F-AR batch | Reviewer provenance |
|---|---|
| `AR_44_INITIAL_AUTONOMOUS_EXECUTION_BATCH`, `AR_FULL_RUN` | `AUTONOMOUS_SYNTHETIC_REFERENCE` |
| `OFFLINE_DRY_RUN` (offline handwritten tests) | `OFFLINE_HANDWRITTEN_AUTONOMOUS_DRY_RUN` |

Reviewer provenance is never `LIVE_SYNTHETIC_EXPERIMENT`, which stays the generator's provenance, so generator and reviewer records cannot be confused. Provenance is derived from the batch, never supplied. An AR-44 or AR full-run record requires a frozen reviewer configuration. Records are write-once; a retry is a new record with the next `attempt_number`.

The two reference labels above are the only labels an autonomous result can carry. No record type in this track can be labelled `HUMAN_REFERENCE` or ground truth, and an autonomous reference cannot be passed to the human-track comparison.

## 7. Fail-closed handling (IMPLEMENTATION)

Reviewer output is parsed strictly: UTF-8, JSON without duplicate keys or non-standard constants, exactly the Stage F-AR output schema (`engine3.stage_f_ar.reviewer_output.v1`), every packet segment in order, and the same constraints the human records meet (valid vocabularies, citations only of ids shown in the packet, `INTERNAL` exactly for internal artifacts). Any deviation is recorded as `NOT_UTF8`, `NOT_JSON`, or `INVALID_STRUCTURE` with the raw bytes kept. Nothing is repaired. A rejected A, B, or adjudicator record yields no autonomous reference for that packet; the artifact is reported as having no autonomous reference. Any retry follows the retry policy frozen under AR-PRE-2.

## 8. Calibration qualification (PROPOSED)

The frozen 50-segment calibration corpus (`ENGINE3_STAGE_F_HUMAN_CALIBRATION_CORPUS.json`, freeze manifest SHA-256 `a88600bb392f9d853b07c8948ee41ce8af597f5a3025697c56dc5e6e0fefef97`) is human-owner-reviewed. For Stage F-AR it is a reviewer qualification diagnostic, not an evaluation result and not a continuously reusable tuning set. Its training key was model-drafted and owner-reviewed; it is not human ground truth and is not described as such.

**Freeze before qualification.** Before any qualification is executed, Stage F-AR must freeze, in an owner-approved qualification governance record:

1. the qualification metrics;
2. the qualification acceptance and refusal criteria;
3. what constitutes structural failure;
4. what constitutes semantic disagreement for qualification;
5. whether qualification can disqualify a configuration;
6. the rules for rerunning qualification.

None of these, and no qualification threshold, is chosen here. Until that record is frozen and bound by SHA-256, AR-PRE-4 stays unresolved, and the offline readiness check reports it unresolved even for a completed qualification.

**No tuning against calibration.** No reviewer prompt, model, or configuration may be iteratively tuned against calibration results and then treated as the same setup.

**Configuration versions.** Any material change to the reviewer model, model version, prompt, output schema, sampling, annotation instructions, or adjudication instructions creates a new reviewer-configuration version (a new `configuration_sha256`) and requires a new qualification run. A qualification is bound to the exact panel hash, so a changed configuration never inherits an earlier qualification.

**Attempts.** Preserve every prior qualification attempt. Each attempt is a new record naming every earlier attempt by hash; a rerun never overwrites an earlier attempt.

**Administration.** Reviewers receive calibration packets without the answer key; agreement with the key is computed afterwards, outside the reviewer. Qualification results are diagnostic, reported to the owner, and never reported as reliability results. Evaluation packets are disjoint from calibration items, reviewers never receive the calibration answer key during evaluation, and reviewer prompts are never tuned against evaluation results.

Qualification is not executed by this protocol, and no qualification harness exists yet (planned).

## 9. Experiment compatibility (PROPOSED)

Stage F-AR operates over the locked Stage F material without modifying it:

| Run | Size (locked design) | Stage F-AR use |
|---|---|---|
| AR-44 INITIAL AUTONOMOUS EXECUTION BATCH | the locked 44-slot material: 44 Arm G proposals (22 × 2) | operational only; never used for any AR decision rule |
| AR full run, Arm G | 220 proposals | primary AR comparison |
| AR full run, Arm A | 240 annotation runs over 48 fixed-prose artifacts | diagnostic AR comparison |

The generator runs under the O-5 frozen configuration unchanged. Review units are blinded packets; byte-identical artifacts (same context, artifact type, segment boundaries, and texts) are reviewed once, as in the human protocol. Each review has an opaque `run_slot_id` assigned by the AR run order frozen under AR-PRE-5; the slot id is never part of a reviewer request. Arms and batches are never pooled. All results live in the Stage F-AR namespace, separate from any human-reference result.

## 10. Comparison, metrics, and decision rules

**Replay (IMPLEMENTATION).** For identical prose and context, Stage D is called unchanged with the generator's annotations (`D_generator`) and with the eleven fields replaced by the final autonomous reference (`D_autonomous`), built exactly as the locked Stage F comparison builds its case. Only autonomous-reference-complete, annotation-sensitive artifacts are compared.

**Metrics (PROPOSED).** Computed separately per AR batch and per arm, each with numerator and denominator, never combined:

1. Reviewer A / Reviewer B exact agreement by item (status over all segments; fields and flags over segments both marked `SCORABLE`).
2. Reviewer A / Reviewer B disagreement rate by item.
3. Adjudication rate (adjudicated packets / reviewed packets), and how often each item required adjudication.
4. Generator vs autonomous reference exact agreement by field.
5. Generator vs autonomous reference annotation-status agreement (segments the autonomous reference also marked `SCORABLE`).
6. `D_generator` vs `D_autonomous` outcome table.
7. Autonomous-relative potential false-pass count.
8. Autonomous-relative potential false-refusal count.
9. Disagreement families (field and permissive / restrictive / neutral direction).
10. Coverage: artifacts with an autonomous reference, autonomous-reference-complete artifacts, unscorable and unresolved segments.
11. Identical-prose annotation sensitivity: compared artifacts whose outcome changes under the autonomous reference, and repeated packets whose generator outcome varies across repeats.

There is no composite score, no single accuracy figure, and no confidence or probability.

**Decision rules AR-A to AR-G (PROPOSED).** Separate from, and never substituted for, the human rules A to G. Computed per arm on AR full-run data only; AR-44 and dry-run data are refused.

| Rule | Condition | Consequence | Does not justify |
|---|---|---|---|
| **AR-A** | A generator Stage D pass becomes a refusal under the autonomous reference | An autonomous-relative potential false pass. Every case is reported individually. | Calling it a confirmed or human-confirmed false pass |
| **AR-B** | Zero autonomous-relative potential false passes, with at least one compared artifact | A bounded synthetic autonomous-consensus observation only | Any safety claim, any human semantic accuracy claim, resolving E3-E-SEM-001 |
| **AR-C** | Any governed item with Reviewer A / Reviewer B disagreement | Reported per item as autonomous-reference instability, with counts, even when adjudication resolved it | Hiding instability behind the adjudicated result |
| **AR-D** | Always | The adjudication rate is reported prominently. A high rate means the autonomous panel itself is semantically unstable. | Treating adjudicated results as stable |
| **AR-E** | Identical prose whose Stage D outcome changes solely because semantic annotations change | Evidence of E3-E-SEM-001-type outcome sensitivity | Resolving E3-E-SEM-001 |
| **AR-F** | Panel members (or the generator) share a model family or developer | The independence claim is weakened and must be disclosed | Claiming independent agreement |
| **AR-G** | Always | Nothing in Stage F-AR resolves O-3 or supports a human-reference reliability claim | Any change to the human track |

No single pass/fail threshold is set. AR-C reports every item without a cut-off; a threshold for "substantial" disagreement would need to be justified and frozen by the owner before execution. A zero count is stated as "0 of n" with n given.

## 11. Readiness (PROPOSED)

| Track | Item | Status |
|---|---|---|
| Human | O-3 | `PRE_RUN_REQUIRED` (unchanged) |
| Human | human `pilot_ready` | false (unchanged) |
| Autonomous | `autonomous_reference_ready` | false |

Autonomous blockers (O-3 is not one of them, and autonomous readiness never reads it):

| ID | Blocker | Status |
|---|---|---|
| AR-PRE-1 | Reviewer model and configuration freeze (families, developers, model ids and versions, sampling; A and B different families) | `PRE_RUN_REQUIRED` |
| AR-PRE-2 | Autonomous reviewer prompt, output schema, and retry / malformed-output policy freeze | `PRE_RUN_REQUIRED` |
| AR-PRE-3 | Reviewer-family correlation assessment, owner-reviewed and bound to the exact panel | `PRE_RUN_REQUIRED` |
| AR-PRE-4 | Qualification diagnostic and its frozen decision rules: qualification governance frozen first (section 8), then qualification run on the frozen calibration corpus, bound to the exact panel, every attempt preserved | `PRE_RUN_REQUIRED` |
| AR-PRE-5 | Autonomous run-order freeze, bound to the exact panel | `PRE_RUN_REQUIRED` |
| AR-PRE-6 | Explicit owner authorization to execute Stage F-AR (generator and reviewers) | `PRE_RUN_REQUIRED` |

The offline readiness check validates AR-PRE-1 to AR-PRE-5 only. AR-PRE-4 stays unresolved while the qualification governance is not frozen. AR-PRE-6 is always still required: no code, record, or model-supplied value satisfies it. No Stage F-AR execution changes any human-track readiness state.

## 12. Implementation status

| Component | Status |
|---|---|
| This protocol, the claim boundary, the manifest | Written; awaiting owner review |
| `experiments/engine3_stage_f_ar/` (panel, review, analysis) | Implemented offline; tested with handwritten synthetic records only |
| Blinding (including anonymized adjudication), consensus, adjudication, provenance, replay, metrics, AR rules, readiness | Implemented and tested offline |
| Reviewer model selection, prompts, output schema freeze, retry policy | Planned (AR-PRE-1, AR-PRE-2) |
| Qualification governance record, qualification harness, and execution | Planned (AR-PRE-4); not frozen, not implemented, not executed |
| Live reviewer or generator execution, AR-44 batch, AR full run | Not implemented; not authorized; not executed |

The package contains no model client, network access, credential read, file I/O, persistence, or outbound action.

## 13. Limitations

- **Not human judgment.** The autonomous reference is model output. Agreement with it says nothing about agreement with humans.
- **Correlated errors.** Models can share training data and blind spots. Distinct families reduce, but do not remove, correlated error; agreement between A and B is not independent evidence of correctness.
- **Single synthetic account and model-chosen segmentation,** as in the human design.
- **The adjudicator is a model.** Its decisions are a third autonomous opinion, not an authority.
- **Bounded results.** Any result applies only to the exact generator, reviewer panel, prompts, schemas, sampling, and corpus.

## 14. Claims

See `ENGINE3_STAGE_F_AUTONOMOUS_REFERENCE_CLAIM_BOUNDARY.md`. In short: claims are relative to a blinded autonomous reviewer consensus only, and never about accuracy against humans, ground truth, safety, semantic validation, purchase prediction, sales lift, production security, or BAEC theory.
