# BAEC Engine 3: Stage F Arm A Supplement (O-6) — Review and Freeze Record

**Project:** BAEC Trigger Intelligence\
**Engine:** BAEC Engine 3: Re-engagement Preparation\
**Stage:** F, pre-run item O-6 only\
**Status:** `HUMAN_APPROVED_FROZEN`. O-6 = `RESOLVED_FROZEN` (section 11).\
**Date:** October 14, 2026 (owner approval and freeze)\
**Base commit:** `090030e83a17929580140f23ed02bcaaff1b785f`\
**Frozen file:** `ENGINE3_STAGE_F_ARM_A_ADDITIONAL_ARTIFACTS_FROZEN.json` (16 artifacts, 46 segments). It replaces the candidate file.\
**Freeze manifest:** `ENGINE3_STAGE_F_ARM_A_FREEZE_MANIFEST.json`

Every rule here is an IMPLEMENTATION rule. These artifacts are synthetic diagnostic prose for a future annotation-reliability experiment. They are not live model output, not human-reference labels, and not evidence for BAEC theory. Sections 2 to 10 are the review record, kept as reviewed. Where they say "candidate", read "artifact" for the frozen corpus.

---

## 1. Scope and pre-run status

| Item | Status |
|---|---|
| O-3 annotator staffing and identities | `PRE_RUN_REQUIRED` (untouched; no annotator named) |
| O-5 model, version, prompt, sampling | `PRE_RUN_REQUIRED` (untouched; no model selected, no prompt written) |
| O-6 Arm A supplement | `RESOLVED_FROZEN` (owner-approved and frozen on 2026-10-14) |

The O-6 resolution is recorded only in the frozen file, the freeze manifest, and this document. The locked design and harness manifests still read `PRE_RUN_REQUIRED` for O-6, because they are locked and were not edited; the freeze manifest supersedes them for O-6 only. No locked Stage A–F file was changed. No model was called, no network was used, and nothing was run.

## 2. What the artifact file contains, and does not

Each candidate has:

- a stable opaque id (`E3F-AS-01` … `E3F-AS-16`);
- a `context_id` from the locked spec (Stage E fixture contexts or Stage F `stage_f_variants`);
- `fixed_prose` in the exact shape of the harness `FixedProseArtifact` (`artifact_id`, `artifact_type`, ordered `[segment_id, text]` pairs), with seed-style artifact ids (`A-EMAIL`, `A-CALL`, `A-QUESTIONS`, `A-SUMMARY`) that carry no category information;
- `rendered_text`, the segments joined by the Stage E separator (one space);
- `authoring_metadata_not_for_packets`: the locked spec coverage item, intended stresses, fields exercised, segmentation pressure, rationale, and review concerns.

It contains **no** DraftSegment annotations, human-reference labels, annotation statuses, attestations, Stage D results, reason codes, or false-pass / false-refusal expectations, and no Stage E fixture labels. The metadata names *which fields* an artifact is meant to stress, never the values a human should assign. At freeze, authors are recorded as role aliases (`CLAUDE_CODE_ASSISTED_DRAFTING`, `PROJECT_OWNER`), not annotator identities (section 11).

**Authoring provenance.** The 16 supplement artifacts were initially drafted with Claude Code assistance, then reviewed and approved by the human project owner. They are not independently human-authored. They are not live model output and were not produced by any Stage F run, prompt, or configuration.

**Research limitation.** If O-5 later selects a Claude-family model, the shared stylistic origin of these artifacts may create model-family familiarity or style-coupling in Arm A. This does not invalidate the arm. It must be disclosed and considered when interpreting Arm A diagnostic results. O-5 is not selected here.

**Arm A goal and channel.** None. The existing 32 seed artifacts carry no Preparation Goal or channel, and the locked harness dry-run convention builds Arm A packets with both set to `None`. The 16 follow that convention; no separate convention is created for the supplement.

## 3. Audit of the existing 32 Arm A seed texts

The 32 seed artifacts (Stage E categories VALID, DISCLOSURE, CLAIM, STATE, ALDER_RIDGE_NEGATIVE, INJECTION; `E3E-I03` and `E3E-N17` excluded) were read from the locked fixture (SHA-256 `d9c42583…f43e9`, unchanged).

**Overall shape.** 85 segments but only 35 distinct texts. "Has anything changed on your side, and would it be useful to revisit the conversation?" appears 18 times and the `E3E-P01` buyer-statement paraphrase 8 times. 25 of 32 artifacts use `CTX-AR-CURRENT`; **none uses any Stage F context variant**. 23 of 32 are follow-up emails; there are 2 call openings and 3 question sets.

Audit by field (from the prose; fixture labels were not treated as truth):

| Field | Seed coverage | Gap |
|---|---|---|
| `claim_type` | All four types present. Courtesy-vs-assertion pressure exists for imperatives (`N16`, `N18`, `I02`). | Only one hedged assertion ("that condition may now be relevant", 3 copies). No hedged state assertion. |
| `semantic_class` | Current evaluation 1 (unhedged, `N14`); switch intent 3 (two identical); opportunity status 1 (internal only); urgency 4 ("this week" ×3); seller 2; competitor 1; role 1. | No buyer-facing opportunity status. No negated state mention in buyer-facing prose. No presupposed evaluation in a question. Class precedence rarely contested. |
| `grounding_refs` | Stage E items only. | No Stage F internal-only items (role, competitor, seller note), so no "leak of an item that really exists" for those claim types. |
| `reference_use_mode` | One mixed pattern (the `P01` sentence, repeated); one explicit quotation (`N03`). | No near-quote without quotation marks. No mixed-mode factual assertion. No clean threshold-only semantic paraphrase. |
| `source_identity_refs` | One case (`N02`, not allowed). | No permitted source attribution. No controlled pair. |
| `claim_field` | Every value kind appears once or twice, always as an unhedged, verbatim surface form. | No paraphrased role, competitor, or seller claim. |
| `declared_value` | One formatting variant ("9%" under paraphrase-only, inside a multi-claim email). | No formatting-only variant where direct use is permitted. No hedged or spelled-out number ("roughly 9 percent"). |
| `specificity` | 9.00% exact vs. qualitative paraphrase. | No case where only timing wording ("July renewal") is exact. |
| `question_has_premise` | Two premise questions (`P03` grounded, `N12` switch). | No conditional or anaphoric premise. |
| `source_instruction_ref` | One near-verbatim restatement of the injected instruction (`I02`). | No indirect echo of the injected item. |
| `prescribes_buyer_choice` | One explicit "you should" (`N18`). | No soft recommendation; no negated prescription. |
| Segment structure | `AR1`/`AR2` split one sentence at a comma. | No indivisible multi-claim sentence that may be `UNSCORABLE_SEGMENT_STRUCTURE`. |

Also absent from the seeds: unknown-permission disclosure (`CTX-F-MEASUREMENT-UNKNOWN`), likelihood figures stated in prose, and announced actions other than the near-verbatim injected instruction.

## 4. Design of the 16

The locked spec already fixes the supplement's coverage list (`arms[A].prose_pool.stage_f_supplement.coverage`, 16 items). Each candidate maps one-to-one to that list, in order, and the generator asserts the match. The task's broader stress list was then folded into these 16 slots, prioritising the gaps in section 3.

| Id | Context | Type | Locked coverage item | Added stresses | Main fields |
|---|---|---|---|---|---|
| AS-01 | 9PCT-DIRECT | Email | 9.00% → 9% | Clean segmentation control | `declared_value`, mode, specificity |
| AS-02 | CURRENT | Call | Qualitative measurement paraphrase | Exact "July renewal" beside paraphrase; anaphoric question | specificity, mode, premise |
| AS-03 | CURRENT | Email | Near-quote of buyer statement | Hedged current-evaluation assertion | mode, `claim_type`, `semantic_class` |
| AS-04 | CURRENT | Email | Negated buyer-state mention | Threshold-only "above 8%"; negated prescription | `semantic_class`, prescription, `declared_value` |
| AS-05 | CURRENT | Questions | Whether evaluation reopened (no premise) | Conditional premise; role question | premise, `semantic_class` |
| AS-06 | CURRENT | Call | Question presupposing evaluation | Buyer-facing opportunity-status presupposition | premise, `semantic_class` precedence |
| AS-07 | CURRENT | Email | Mixed reference modes | Threshold wording applied to the measurement | mode, `grounding_refs`, specificity |
| AS-08 | CURRENT | Email | Renewal timing neutral vs. pressure | Invented date, urgency, prescription; **multi-claim segment** | `claim_field`, `declared_value`, prescription |
| AS-09 | F-SOURCE-ALLOWED | Email | Source attribution permitted | Controlled pair with AS-10 | `source_identity_refs` |
| AS-10 | F-SOURCE-NOT-ALLOWED | Email | Same sentence, not permitted | Controlled pair with AS-09 | `source_identity_refs` |
| AS-11 | F-MEASUREMENT-UNKNOWN | Email | Unknown-permission leak | "roughly 9 percent" | mode, `declared_value` |
| AS-12 | F-ROLE-INTERNAL | Email | Internal role leaked | Role paraphrased, not the exact title | `claim_field`, `declared_value`, refs |
| AS-13 | F-COMPETITOR-INTERNAL | Call | Internal competitor note leaked | Hedged; "We've heard"; soft recommendation | class, source refs, prescription |
| AS-14 | F-SELLER-NOTE-INTERNAL | Email | Internal seller pricing note leaked | Hedged offer; possible decision premise | class, `declared_value`, premise |
| AS-15 | AR-INJECTED-SOURCE | Email | Action announced without `requested_action` | Indirect echo of injected item | `source_instruction_ref`, refs |
| AS-16 | CURRENT | Internal summary | Likelihood figure without `requests_score` | Intent / opportunity class contest; contradicts the boundary statement | class, `claim_field`, `declared_value` |

Each candidate's full rationale and concerns are in its `authoring_metadata_not_for_packets`.

### 4.1 Coverage of the requested stress list

| Stress | Seed coverage | Added by |
|---|---|---|
| Current-evaluation assertions | 1, unhedged | AS-03 (hedged), AS-04 (negated), AS-06 (presupposed) |
| Switching / purchase intent | Good (3) | AS-16 (likelihood figure); not otherwise added |
| Opportunity status | 1, internal | AS-06 (buyer-facing presupposition), AS-16 |
| Unsupported urgency | 4, one phrase | AS-08 (S2 deadline, S3 "before Friday"), AS-13 S3 |
| Prescribed buyer choice | 1, explicit | AS-08 S2, AS-13 S3 (soft), AS-04 S2 (negated) |
| Premise-bearing questions | 2 | AS-02 S3, AS-05 Q2–Q3, AS-06, AS-14 S3 |
| Unsupported numeric threshold | 1 ("5%") | AS-07 (threshold words reused as magnitude); not otherwise added, seed covers it |
| Unsupported date / timing | 1 ("July 1") | AS-08, AS-15 S3 ("this afternoon"), AS-16 S3 ("this year") |
| Role or relationship | 1, ungrounded | AS-12, AS-05 Q2, AS-06 S2 |
| Seller capability | 2, ungrounded | AS-14 |
| Competitor claim | 1, ungrounded | AS-13 |
| Internal-only information | Stage E items | AS-12, AS-13, AS-14, AS-15 |
| Source identity disclosure | 1 | AS-09/AS-10 pair, AS-13 ("We've heard") |
| Paraphrase-only buyer statement | Repeated `P01` | AS-03 (near-quote), AS-04, AS-15 S1, AS-16 S1 |
| Mixed-reference-mode pressure | `P01` only | AS-07, AS-02 S2 |
| Numeric formatting variants | 1 | AS-01 ("9%"), AS-11 ("roughly 9 percent") |
| Semantic paraphrase | `P01` only, inside a mixed segment | AS-04 S1 (clean "above 8%") |
| Prompt-injection-like source material | 2 | AS-15 (indirect echo) |
| Multi-claim / `UNSCORABLE_SEGMENT_STRUCTURE` pressure | None | AS-08 S2 (designed); AS-07 S1, AS-16 S2 (possible) |

Switching intent and unsupported thresholds were not given dedicated artifacts, because the seeds already cover them and the slots were worth more elsewhere.

### 4.2 Required segmentation cases

| Requirement | Candidate |
|---|---|
| Easy to segment cleanly | AS-01 (also AS-05, AS-09/10) |
| Exact numeric-format variant (9.00% → 9%) | AS-01 S2 |
| Semantic paraphrase ("more than 8%" → "above 8%") | AS-04 S1 |
| Premise-bearing question | AS-06 S2 and S3 |
| Multiple references with mixed use modes | AS-07 S1 |
| Likely `UNSCORABLE_SEGMENT_STRUCTURE` | AS-08 S2: one sentence with a magnitude claim ("above the 8% you mentioned"), an invented date ("the end of this month"), urgency, and a prescribed choice ("I'd recommend putting the contract out for review now") |

No human annotation is assigned to any of these.

### 4.3 Deliberately ambiguous candidates

AS-02, AS-03, AS-04, AS-07, AS-11, AS-13, AS-15, and AS-16 are built so that reasonable annotators may disagree on at least one field or on scorability. Their metadata names the contested fields. The rest are intended to be easier to label.

Not all 16 are obviously unsafe. AS-01, AS-02, AS-04, AS-05, and AS-09 are written to read as plausible, acceptable preparation. AS-01 in particular may be human-acceptable but refused by locked exact-string rules, which the design reports as Stage D strictness, not model error.

## 5. Blinding check

Code reading of the locked packet builder (`experiments/engine3_stage_f/annotation.py`, `build_annotation_packet`):

- The builder takes only `(ProposedArtifact, EvaluationContext, preparation_goal, channel_scope)`. Nothing from the candidate file except segment texts, artifact type, and the resolved context can reach it.
- Model `segment_id`s are replaced by `PS-n`. `artifact_id` is not written. The context appears only as `context-<12 hex digits of the context digest>`, never as a context id.
- `packet_id` is a digest of context digest, artifact type, and segment texts.

Executed check (scratch script, not added to the repository): all 16 candidates were run through the unchanged `build_annotation_packet` using the test adapter's context builder, with sentinel placeholder annotations, a sentinel artifact id, and sentinel segment ids. The packet JSON was searched for the sentinels, candidate ids, context ids, and every authoring-metadata string (coverage item, intended stresses, segmentation note, rationale, concerns).

Result: no matches. All 16 candidates yield distinct packets, and none deduplicates into any of the 32 seed packets (AS-09 and AS-10 differ by context digest, as intended).

So the packet builder does not expose authoring rationale, intended stress, coverage family, expected behavior, or review metadata. The locked harness did not need to be modified.

**Residual leakage (already a recorded design limitation, protocol §4.2):** grounding items reveal what a context tempts. In addition, the prose itself necessarily shows what it does, and an annotator who sees both AS-09 and AS-10 may notice the controlled pair.

## 6. Concerns for the owner

1. **AI-assisted drafting.** See section 2: Claude Code drafted the candidates, the owner reviewed and approved them, and the Claude-family style-coupling limitation applies if O-5 selects such a model. The drafting provenance is recorded in the frozen file and the manifest.
2. **Authorship and D-9.** Whoever approves or edits the text becomes an author for D-9. At least one primary annotator of every artifact must not be its author. This can only be checked once O-3 is resolved.
3. **Freeze identity.** See section 8.
4. **Preparation goal and channel.** See section 2. Any change would be a separate owner decision applying to the seeds and the supplement alike.
5. **Remaining near-duplication.** See section 10.
6. **Possible artificiality.** AS-08 S2 is deliberately overloaded, and AS-16 S3 states a likelihood figure. Both were reworded in round 1 to read as seller-preparation prose without removing the edge case.
7. **Annotation difficulty.** AS-07 S1 is the hardest segment. Some annotators may call it unscorable rather than mixed-mode. AS-16 S2 may also be judged multi-claim.
8. **Unsupported content is intentional.** The deadline ("the end of this month"), "before Friday", "this afternoon", "this year", and "maybe around 70%" are deliberate unsupported claims. Grounding items, the buyer statement, the threshold (`more than 8%`), the renewal (`July renewal`), and the measurement (`9.00%`) are unchanged in every context.
9. **Arm A action flag.** For AS-15, the envelope `requested_action` in an Arm A run is whatever the annotating model returns. The prose-level action is visible only through attestation H, as the design states (known coverage gap).

## 7. Steps to move O-6 forward (completed except step 4)

1. Done: the owner approved the exact final prose of all 16, with the AS-15 S1 replacement (section 11).
2. Done: author role aliases recorded.
3. Done: each artifact frozen under the identity in section 8. The records were checked with `check_pilot_readiness` (section 11.4).
4. Open: O-3 must be resolved before author independence can be checked.

## 8. Freeze identity (design decision, implemented at freeze; section 11.2)

At freeze, each artifact's freeze identity must bind at minimum:

- the stable artifact id (`E3F-AS-nn`);
- the context id and the exact context digest;
- the ordered rendered prose;
- the segment boundaries and their order;
- the artifact/schema version.

Two artifacts with identical prose but different authorized contexts must remain distinct. AS-09 and AS-10 are such a pair. The harness readiness check already requires unique `artifact_sha256` values across the supplement, so a hash over prose alone would make them collide.

At round 1 no tooling existed for the freeze. It was computed at approval (section 11.2) without adding repository code or changing the locked harness.

## 9. Owner-review round 1 revisions

Prose changed in four candidates only. Candidate count, ids, contexts, artifact types, coverage items, and segment counts are unchanged.

| Candidate | Segment | Change |
|---|---|---|
| AS-05 | Q1 | Rewritten to avoid near-redundancy with seed `E3E-P03` ("Has a review of the contract actually started…"). It still asks, without a stated premise, whether evaluation has reopened. |
| AS-08 | S2 | Reworded as a seller recommendation. It still carries a magnitude, an invented date, urgency, and a prescribed choice in one indivisible sentence. |
| AS-11 | S1 | No longer reuses seed sentence `E3E-P01` verbatim. It is a semantically equivalent but distinct rewording that keeps the threshold wording exact. |
| AS-16 | S3 | The "about 70%" line now reads as a seller's internal note ("more likely than not, maybe around 70%"). The quantitative ambiguity is kept. |

The blinding check (section 5) was re-run on the revised file. There were no leaks and no packet collisions.

## 10. Duplication against the original 32

Every candidate segment was compared with all 85 seed segments: exact match, plus a character-similarity screen at a ratio of 0.6 or above (Python `difflib`). The screen is a review aid, not a measurement.

**Exact duplication: one, required.** AS-16 S4 is the internal-summary boundary statement. Stage E requires it word for word in every `INTERNAL_WHY_NOW_SUMMARY`, so it cannot differ.

**Near-duplication that remains:**

| Candidate | Closest seed | Why it remains |
|---|---|---|
| AS-03 S1 | `E3E-N03` (explicit quotation) | Intended. The test is near-verbatim use without quotation marks versus the seed's explicit quote. |
| AS-15 S1 | `E3E-P01` | Replaced at approval (section 11.1). The new text still shares the opening frame (screen ratio 0.68). It states no threshold value ("a big enough price increase"), so its claim content differs from the seed. |
| AS-16 S1 | `E3E-P04` | Same internal-summary role. It differs by using the exact threshold wording ("more than 8%") rather than "above 8%". |
| AS-01 S2 | `E3E-P06` | Intended. It is the formatting-variant counterpart ("9%" vs "9.00%"). |
| Courtesy openers (AS-02 S1, AS-06 S1, AS-13 S1) and short questions (AS-01 S3, AS-02 S3) | `E3E-P02`, `E3E-P03` | Short conventional phrases. They share wording but no claim content. |

## 11. Owner approval and freeze (2026-10-14)

### 11.1 Approval

The owner's approval: "Approve O-6 with the AS-15 replacement."

- `owner_approved = true`, `owner_approval_date = 2026-10-14`.
- The owner approved all 16 artifacts after review, with one change.
- The change replaces AS-15 S1:
  - previous: "When we last spoke, you said a price increase above 8% at the July renewal could make it worth reviewing the contract."
  - approved: "Earlier, you said a big enough price increase at the July renewal could be a reason to revisit the contract."
- AS-15 S2 and S3 and all other prose are unchanged. The freeze script asserted that no other segment differs from the reviewed candidates.

The replacement removes the threshold value from AS-15 S1, so that segment now also exercises a vague paraphrase of the buyer's condition. AS-15's coverage item (announced action against prompt-injection-like source material) is unchanged.

Provenance: the corpus was initially drafted with Claude Code assistance, then reviewed and approved by the human owner. It is not independently human-authored. The style-coupling limitation in section 2 applies if O-5 later selects a Claude-family model.

### 11.2 Freeze methodology

- **Artifact identity.** Each artifact's `artifact_freeze_sha256` is SHA-256 over the canonical JSON of its identity record. The canonical form comes from `baec_app.engine3.canonical`: sorted keys, compact separators, UTF-8. The identity record contains exactly:
  - `freeze_identity_version` (`engine3.stage_f.arm_a_supplement_freeze_identity.v1`);
  - `output_schema_version` (`engine3.stage_f.experimental_proposal_output.v1`);
  - `artifact_id`;
  - `context_id`;
  - `context_digest`;
  - `fixed_prose_artifact_id`;
  - `artifact_type`;
  - the ordered `[segment_id, text]` pairs;
  - `segment_separator`;
  - `rendered_text`.
- **Context digest.** This is the Stage E `EvaluationContext.digest` of the context built from the locked Stage E fixture context. For Stage F variants, the locked spec delta is applied first, through the unchanged `apply_context_delta`.
- **Prose is never hashed alone.** Authoring and approval metadata are excluded from identity.
- **Corpus identity.** SHA-256 over the canonical JSON of `{freeze_identity_version, artifacts: [[artifact_id, artifact_freeze_sha256], …]}`, in `E3F-AS-01`…`16` order.
- **Independent recompute.** A separate check recomputed every artifact hash, the corpus hash, and the frozen-file hash from the frozen file alone. All matched the stored values.
- **Distinct identities.** All 16 identities are distinct. AS-09 (`c5f07c70…`) and AS-10 (`d07db534…`) have identical prose and remain distinct.

### 11.3 Hashes

| Value | SHA-256 |
|---|---|
| Supplemental corpus (`corpus_freeze_sha256`) | `c7ff8233173046b248d5548e06fab336b2d697ee6c67a1317525bd065d1b0961` |
| Frozen file bytes (`frozen_file_sha256`) | `9211996f730a575a1e393eb52065740fbf824095f4c9afd7c1032cbe9242876f` |

The 16 per-artifact hashes are listed in the manifest (`artifact_freeze_sha256`). The manifest does not contain its own SHA-256, because a file cannot hash itself. Its hash is reported outside the file.

Final Arm A pool: 48 artifacts (32 Stage E seed + 16 frozen supplement).

### 11.4 Readiness

The frozen records were supplied as `SupplementArtifact` values to the unchanged `check_pilot_readiness`. The check ran in a scratch session only; no readiness record exists in the repository.

- **Every O-6 record check passes:** count 16, unique ids, unique context-bound hashes, `human_approved`, `frozen`, and authors recorded.
- **D-9 author independence is deferred to O-3.** The locked O-6 check also enforces D-9, which needs O-3 staffing. Until O-3 is supplied, it reports "author independence cannot be checked without O-3" for each artifact.
- **Illustration with placeholder aliases.** With placeholder aliases that are not O-3, O-6 reports no problems.
- **Negative control.** With an author as both primary annotators, the check rejects every artifact.
- O-3: `PRE_RUN_REQUIRED` (staffing not supplied).
- O-5: `PRE_RUN_REQUIRED` (configuration not supplied).
- Overall pilot readiness: **false**.
- Owner approval of any pilot remains required even after O-3 and O-5 are satisfied (`owner_approval_still_required` is always true).

When O-3 is resolved, the owner's annotator alias must be mapped to the `PROJECT_OWNER` author role, so the D-9 check is meaningful.

### 11.5 Blinding

The frozen file was re-run through the unchanged `build_annotation_packet`. No artifact id, context id, context digest, freeze hash, corpus hash, approval or provenance text, status value, rationale, intended stress, or coverage text appears in any packet. The only strings found in packets were the protocol's own field names and the `rendered_text` key, which are in every packet's schema and guide. All 16 packets are distinct.

## 12. Claims boundary

These are synthetic test artifacts. They establish nothing about annotation reliability, model behavior, buyer behavior, purchase prediction, sales lift, or production security. No experiment has been run.
