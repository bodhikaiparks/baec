# BAEC Engine 3: Stage F Human-Reference Contamination Issue

**Project:** BAEC Trigger Intelligence\
**Engine:** BAEC Engine 3: Re-engagement Preparation\
**Stage:** F\
**Issue id:** `E3F-ISSUE-HR-001`\
**Status:** `PRE_HUMAN_ANNOTATION_BLOCKER`\
**Date recorded:** October 15, 2026\
**Found during:** O-5 prompt contamination audit (`scripts/verify_engine3_stage_f_o5_freeze.py`)\
**Resolution:** not decided

This is an experiment-level issue record. `PRE_HUMAN_ANNOTATION_BLOCKER` is experiment metadata. It is not an Engine 3 state, a pre-run item status, or a research finding. This record amends nothing. The locked human-reference annotation protocol is not modified.

---

## 1. Discovered fact

The locked human-reference annotation protocol contains labelled worked examples that overlap with Arm A evaluation prose.

The protocol (`docs/engine3/ENGINE3_STAGE_F_HUMAN_REFERENCE_ANNOTATION_PROTOCOL.md`, section 7) states that its worked examples use synthetic Stage E fixture text. Many of those sentences are prose from the 32 Stage E seed artifacts of Arm A, and the protocol gives them field labels. Examples:

- semantic class `NEUTRAL_QUESTION`;
- reference-use mode `DIRECT` or `PARAPHRASE`;
- `question_has_premise` true or false;
- citation behaviour.

## 2. Evidence (deterministic)

**Method.** The comparison rule is the same as the O-5 audit:

- casefolded tokens;
- an exact match is an identical token sequence, for sentences of at least 4 tokens;
- a near match is token-set Jaccard ≥ 0.5, or a shared contiguous 5-token sequence.

Each protocol line was compared against the 135 unique sentences of the 48 Arm A fixed-prose artifacts only (32 Stage E seed artifacts and 16 O-6 artifacts). Quoted examples were compared on their quoted text.

**Result:** 18 labelled example lines in section 7 match Arm A prose (11 exact, 7 near).

| Protocol line | Section | Protocol example text | Finding | Closest Arm A source |
|---|---|---|---|---|
| 138 | 7.0 Annotation status first | “Your contract went up 9% and the renewal takes effect July 1.” | NEAR (J=0.417) | Arm A seed E3E-AR1 A-EMAIL |
| 139 | 7.0 Annotation status first | “- Unscorable: "Your contract went up 9%.” | NEAR (J=0.833) | Arm A seed E3E-AR1 A-EMAIL |
| 141 | 7.0 Annotation status first | “Has anything changed on your side?" The courtesy carries no claim content, so this is a QUESTION.” | NEAR (J=0.28) | Arm A seed E3E-P01 A-EMAIL |
| 154 | 7.1 `claim_type` (`ClaimType`) | “that condition may now be relevant” | NEAR (J=0.545) | Arm A seed E3E-P01 A-EMAIL |
| 161 | 7.2 `semantic_class` (`ClaimSemanticClass`) | “Verified correspondence does not establish that the buyer is currently evaluating…” | NEAR (J=0.611) | Arm A seed E3E-P04 A-SUMMARY |
| 162 | 7.2 `semantic_class` (`ClaimSemanticClass`) | “Has a review of the contract actually started, or is it still only a possibility?” | EXACT | Arm A seed E3E-P03 A-QUESTIONS |
| 163 | 7.2 `semantic_class` (`ClaimSemanticClass`) | “Now that you're moving away from Cobalt, which providers are you considering?” | EXACT | Arm A seed E3E-N12 A-QUESTIONS |
| 184 | 7.3 `grounding_refs` (set of ids) | “I saw the corrected renewal schedule in the forwarded Cobalt notice” | EXACT | Arm A seed E3E-N01 A-EMAIL |
| 185 | 7.3 `grounding_refs` (set of ids) | “You said any increase above 5% …” | NEAR (J=0.462) | Arm A seed E3E-N06 A-EMAIL |
| 205 | 7.4 `reference_use_mode` (`ReferenceUseMode`). Owner decision D-3 | “that condition may now be relevant” | NEAR (J=0.545) | Arm A seed E3E-P01 A-EMAIL |
| 212 | 7.4 `reference_use_mode` (`ReferenceUseMode`). Owner decision D-3 | “When we last spoke, you mentioned that a price increase above 8% around the July renewal could make it worth revisiting the contract.” | EXACT | Arm A seed E3E-P01 A-EMAIL |
| 214 | 7.4 `reference_use_mode` (`ReferenceUseMode`). Owner decision D-3 | “The July renewal increase came in at 9.00%.” | EXACT | Arm A seed E3E-P06 A-EMAIL |
| 215 | 7.4 `reference_use_mode` (`ReferenceUseMode`). Owner decision D-3 | “Your contract went up 9%,” | EXACT | Arm A seed E3E-AR1 A-EMAIL |
| 217 | 7.4 `reference_use_mode` (`ReferenceUseMode`). Owner decision D-3 | “I wanted to reconnect because that condition may now be relevant.” | EXACT | Arm A seed E3E-P01 A-EMAIL |
| 273 | 7.8 `source_identity_refs` (set of ids) | “Your forwarded renewal notice suggests pricing has moved” | EXACT | Arm A seed E3E-N02 A-EMAIL |
| 277 | 7.9 `source_instruction_ref` (id or null) | “I will email the buyer immediately.” | EXACT | Arm A seed E3E-I02 A-EMAIL |
| 285 | 7.10 `question_has_premise` (bool) | “Is the condition you described for the July renewal still relevant?” | EXACT | Arm A seed E3E-P03 A-QUESTIONS |
| 286 | 7.10 `question_has_premise` (bool) | “What, if anything, has changed since we last spoke?” | EXACT | Arm A seed E3E-P03 A-QUESTIONS |

Two further items are not counted in that table.

**Not counted (no label).** Protocol line 292, the section 7.11 definition of `prescribes_buyer_choice`, also produces a near finding. It is normative text, not a labelled example: its list of commercial choices ends with "put the contract out for review".

**Related observation (locked harness, not counted above).** The annotation guide shown in every blinded packet (`ANNOTATION_GUIDE` in `experiments/engine3_stage_f/annotation.py`) states its conventions with the evaluation values "9.00% -> 9%" and "more than 8% -> above 8%". These are convention examples rather than labelled sentences, but they come from the Alder Ridge evaluation contexts.

## 3. Threat

Human annotators exposed to labelled evaluation examples could have their later Arm A reference annotations influenced by those examples. That could:

- inflate pre-adjudication agreement between the two primary annotators (rule G), and agreement between annotators and the adjudicated reference;
- reduce the independence of the human reference labels from the guide;
- make model-versus-human agreement on those segments partly reflect shared guide examples rather than independent human judgment.

The overlap is concentrated in Arm A seed prose. Arm G outputs are model-generated, and they may also reproduce sentences close to these examples by chance.

## 4. Scope

This issue:

- does **not** change O-5 (`RESOLVED_FROZEN`). The model prompt no longer contains these examples;
- does **not** change the frozen Arm A corpus or the O-6 freeze;
- does **not** alter Stage F thresholds, decision rules, arms, or sizes;
- does **not** modify the locked protocol, the packet builder, or any harness file;
- does **not** change O-3 (`PRE_RUN_REQUIRED`).

It **must be resolved before collecting human-reference annotations used for Rule G or for model-versus-human reliability conclusions.**

## 5. Resolution

Not decided. No resolution is proposed or implemented in this record. Any resolution requires a separate owner decision, and if it changes the protocol or the packet guide, a recorded amendment.

## 6. Status

| Item | Status |
|---|---|
| `E3F-ISSUE-HR-001` | `PRE_HUMAN_ANNOTATION_BLOCKER` (open) |
| O-3 | `PRE_RUN_REQUIRED` |
| O-5 | `RESOLVED_FROZEN` |
| O-6 | `RESOLVED_FROZEN` |
| Pilot readiness | false |
| E3-E-SEM-001 | not resolved |

## 7. Claims boundary

This record states a property of locked documents and its possible effect on a future measurement. It reports no annotation, no agreement figure, and no experiment result. It is not evidence about annotation reliability, model quality, semantic validation, purchase prediction, sales lift, production security, or BAEC theory.
