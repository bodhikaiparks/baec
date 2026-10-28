# BAEC Engine 3: Stage F O-3 Annotator Eligibility Addendum

**Project:** BAEC Trigger Intelligence\
**Engine:** BAEC Engine 3: Re-engagement Preparation\
**Stage:** F\
**Pre-run item:** O-3 (`PRE_RUN_REQUIRED`, not resolved by this addendum)\
**Status:** `ADOPTED_FUTURE_O3_CONDITION`\
**Date:** October 15, 2026\
**Source:** owner decision on `E3F-ISSUE-HR-001`

This addendum is additive. It does not modify the O-3 candidate record (`ENGINE3_STAGE_F_O3_ANNOTATOR_STAFFING_CANDIDATE.json`) or the O-3 review (`ENGINE3_STAGE_F_O3_ANNOTATOR_STAFFING_REVIEW.md`), both of which are locked inputs of the O-5 freeze. It adds conditions that the future O-3 freeze must apply. No reviewer identity is supplied, and O-3 is not resolved.

This is an IMPLEMENTATION rule for a synthetic software evaluation. It is not a research finding.

---

## 1. Eligibility rule

> A Primary A, Primary B, or Adjudicator who has previously seen the contaminated labelled evaluation examples in the historical protocol (`ENGINE3_STAGE_F_HUMAN_REFERENCE_ANNOTATION_PROTOCOL.md`, section 7) is **presumptively ineligible** for the blinded main human-reference collection.

- A documented, owner-approved exception may be granted later, for a named role, with its reason recorded before that reviewer receives any evaluation packet. **No exception is approved now.**
- Anyone who authored, edited, or reviewed the historical protocol has seen those examples. This includes the reserved author roles `PROJECT_OWNER` and `CLAUDE_CODE_ASSISTED_DRAFTING`. Without an approved exception, they are presumptively ineligible for all three roles, including adjudicator.
- The rule applies to the main blinded collection. Calibration (section 4) also uses only the operational guide.

## 2. Which guide annotators use

Annotators use the frozen operational guide:

`docs/engine3/ENGINE3_STAGE_F_HUMAN_REFERENCE_OPERATIONAL_GUIDE.md`

They do not use the contaminated worked examples in the historical protocol. The historical protocol remains available as the locked design record. It governs the rules, but it is not the training or operational guide shown to blinded annotators.

At the O-3 freeze:

- **Checklist item 2** ("Has read and understands the Stage F annotation protocol") is answered for the operational guide. A *yes* means the reviewer has read the operational guide. It does not mean they have read the historical protocol.
- **A new item is recorded for every role:** "Has seen the historical protocol's section 7 labelled examples (yes / no)". A *yes* makes the reviewer presumptively ineligible (section 1).
- The packet operator gives annotators the operational guide and the blinded packets only.

## 3. Annotation record provenance

Every future human annotation record (Primary A, Primary B, and adjudication) must carry all three hashes:

| Field | Meaning |
|---|---|
| `locked_protocol_sha256` | SHA-256 of the historical protocol, which is the governing design: `cba979c91cdcc96f419b0f3758cadd651f3427064a7f66ebebd23929c623b5a4` |
| `operational_guide_sha256` | SHA-256 of the frozen operational guide, which is the rule presentation used in practice: `fb4f0510254649dadfc68c06bb76bd1dfab73e2d33bc0db325faf791e61b9011` |
| `annotation_packet_sha256` | SHA-256 of the exact packet the annotator saw: the UTF-8 bytes of `AnnotationPacket.to_json()` (canonical JSON, guide included) |

Records also keep the existing blinded identifiers: `packet_id` (the deduplication key), the opaque packet segment ids, the opaque context label, the annotator alias and role, and the UTC timestamp. `packet_id` is not a substitute for `annotation_packet_sha256`, because it does not cover the guide or the grounding fields.

**Implementation status: not implemented.** The current harness records (`PrimaryAnnotation`, `Adjudication`) carry a single `protocol_version_sha256`. Adding the three fields changes the record schema. That is outside the approved scope of `HARNESS_AMENDMENT_002`, which is limited to example text. It is recorded as `ANNOTATION_RECORD_PROVENANCE_HASHES_REQUIRED` (open) and must be resolved before any human-reference annotation is collected.

## 4. Calibration

No human calibration may start until the calibration corpus passes the deterministic contamination audit described in the resolution record (`CALIBRATION_CORPUS_CONTAMINATION_AUDIT_REQUIRED`, open). No calibration corpus exists yet.

## 5. Open question for the owner (not adopted)

The same labelled evaluation examples also appear in other Stage F records that a reviewer might have read:

- the experiment design (sections 7.4 and 9.2);
- the experiment spec;
- the original issue record;
- the candidate decontamination review;
- `HARNESS_AMENDMENT_002`.

The adopted rule names only the historical protocol. The owner should decide whether exposure through these records counts the same way. Until then, the O-3 freeze records any such exposure for each reviewer, and the reviewer is not given these records.

## 6. Status

| Item | Status |
|---|---|
| This addendum | `ADOPTED_FUTURE_O3_CONDITION` |
| O-3 | `PRE_RUN_REQUIRED` (identities not supplied) |
| Exceptions approved | none |
| `ANNOTATION_RECORD_PROVENANCE_HASHES_REQUIRED` | `OPEN` |
| `CALIBRATION_CORPUS_CONTAMINATION_AUDIT_REQUIRED` | `OPEN` |
