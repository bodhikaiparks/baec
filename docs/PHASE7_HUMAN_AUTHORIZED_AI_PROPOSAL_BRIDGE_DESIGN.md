# Phase 7 Design: Human-Authorized AI Proposal Bridge

> **Approved design — implemented and verified.**
> Phase 7 final verification is recorded in `docs/PHASE7_FINAL_VERIFICATION.md`. Sections 1–22 are the approved Phase 7B design text, kept as written; where the implementation differs, the implementation-clarification sections at the end of this document record it. The Research Contract wording in §19 remains proposed and **NOT APPLIED**.

**Project:** BAEC Trigger Intelligence
**Baseline:** `phase-6-ai-reasoning` (commit `0f1b22e`), schema version 6. Phase 6 and the Phase 7A survey are closed.
**Authority:** The manuscript, then `docs/RESEARCH_CONTRACT.md`, then the approved Phase 4, 5, and 6 designs, then this design, then code. This design adds no research rules. Every mechanism here is an **IMPLEMENTATION** choice. Section 19 proposes Research Contract wording that is **not applied**.

---

## 1. Purpose and invariants

Phase 7 connects a persisted, validated AI extraction artifact to exactly one authoritative domain write: confirming one BAEC. A human reviews the artifact and decides every authoritative value. The human-interaction surface records that decision as a grant. A separate MCP write tool can then execute that grant once.

**Core invariant.** AI may propose. Humans authorize. Domain rules decide. MCP executes. Audit records.

**Additional invariant.** AI-derived text never becomes buyer evidence merely because a model generated it.

Derived design invariants (each is tested; see §18):

| ID | Invariant |
|---|---|
| I1 | No AI output is ever an authoritative value. Evidence selection, evidence provenance, findings, origin, elicitation mode, buyer statement, stringency, final normalization, and acceptance are all explicit human decisions. |
| I2 | A model-supplied value is never proof of approval. A grant is created only by the human-interaction path. MCP has no callable path that creates, extends, supersedes, or alters a grant. |
| I3 | The only Phase 7 domain mutation is confirming one BAEC. No account-state transition, dormancy judgment, staleness change, or non-confirmed classification is reachable from any Phase 7 path. |
| I4 | Three immutable layers are kept: (1) source evidence, (2) the AI proposal snapshot, (3) the human-reviewed final content. No layer is ever updated in place. |
| I5 | Execution is atomic. Grant checks, grant consumption, the `human_authorizations` row, the BAEC record, and the lineage link commit together or not at all. |
| I6 | One AI lineage (artifact → proposal) yields at most one confirmed BAEC. One grant executes at most once. At most one ACTIVE grant exists for one review revision and action. |
| I7 | The Phase 5 read-only MCP server is unchanged: 8 resources, 4 preview tools, 0 prompts, 0 write tools. |
| I8 | Phase 7 makes no model request. It consumes only artifacts already persisted in the product database. |
| I9 | A final normalization is derived text. It must pass `baec-human-normalization-validation/v1` against the human-selected evidence, and it never enters an evidence field. |

---

## 2. Locked decisions

These are requirements and are not reopened here.

| ID | Decision | Where it is designed |
|---|---|---|
| D1 | Use a persisted human authorization grant, created outside MCP and consumed by MCP. A boolean is forbidden. The grant proves which path created it, not who the person is. | §12, §13 |
| D2 | The human-interaction surface is Streamlit. Application behavior stays UI-agnostic and is tested without Streamlit. | §8, §17 |
| D3 | The only domain mutation is confirming one BAEC from an AI_DRAFT proposal. | §14, §16 |
| D4 | Add `ProposalOrigin.AI_DRAFT`. Do not add `AI_MODEL`. Historical Phase 4–6 documents stay as written. | §6.4 |
| D5 | Grants expire 15 minutes after issuance and are single-use. The interval is IMPLEMENTATION. | §12 |
| D6 | AI criterion hypotheses are shown as AI_INFERENCE suggestions only. The human decides each of the four findings explicitly. | §8 |
| D7 | A human-reviewed final normalization may be stored with the confirmed BAEC. It stays derived text, linked to the lineage, and never becomes evidence. | §10 |
| D8 | One artifact/proposal lineage yields at most one confirmed BAEC. There is no global text deduplication. | §15 |
| D9 | Evidence amendments stay outside Phase 7. There is no replacement BAEC as a simulated correction. | §9.5 |
| D10 | Evidence provenance is human-authorized. `attributed_speaker` never populates BUYER_FACT or SELLER_OBSERVATION. | §9 |
| D11 | No live AI inference and no default model in Phase 7. | §6.1 |
| D12 | Successful execution is one transaction. | §14 |
| D13 | Keep the Phase 5 server unchanged. A separate write composition exposes exactly `confirm_baec(grant_id)`. | §16 |
| D14 | Keep three immutable layers. A grant binds to the exact review revision, digest, account, lineage, and action. Editing requires a new revision and a new grant. | §7, §12 |
| D15 | No durable or public seeded demo database and no new live extraction. Tests and AppTest use ephemeral, deterministic, explicitly test-only fixtures, never presented as evidence of an actual Anthropic model invocation. No Phase 6 schema change for a synthetic-provider label. Public or demo seeding is deferred. | §6.5 |
| D16 | AI excerpts are suggestions only. The human may select any exact verbatim evidence from the same persisted interaction and explicitly assigns its provenance. `buyer_exact_statement` requires BUYER_FACT evidence. The human may not invent, rewrite, or paraphrase evidence. | §9 |
| D17 | Final normalization is validated by a separate deterministic contract, `baec-human-normalization-validation/v1`, against the human-selected evidence. It is neither the AI validation v2 identity nor a blank-and-length check. | §11 |
| D18 | At most one ACTIVE grant per exact review revision and action. Supersession is persisted and deterministic. A superseded grant fails closed even before it expires. Old grants are never deleted or rewritten. | §12 |
| D19 | Pin `streamlit==1.65.0`. This is an IMPLEMENTATION dependency choice. | §17 |
| D20 | `streamlit.testing.v1.AppTest` tests are mandatory gate tests in the ordinary offline suite and the clean-export gate. Application-layer tests stay independent of Streamlit. AppTest proves UI behavior, not human identity. | §17 |

---

## 3. Starting point (from the Phase 7A survey)

- **Approval today.** The Phase 4 `HumanConfirmationGate` is in memory, single-use, session-bound, and digest-bound. It loses everything on restart. Its own docstring calls it "accident resistance, not a security boundary". `authorized_by` is self-asserted, and there is no authentication.
- **Proposals today.** Proposals are in-memory only. `ProposalOrigin` is `{HUMAN_DRAFT, DETERMINISTIC}`. Phase 4 design §18 forbids model-origin proposals until AI-origin provenance is stored. Phase 7C satisfies that precondition before any AI_DRAFT proposal exists.
- **Confirmation today.** `ClassificationService.confirm` redeems an approval, then `authority.authorize`, then `create_confirmed_baec_record`, then `Repository.save_confirmed_baec`. That is one transaction inserting the `human_authorizations` row, `interaction_evidence` (deduplicated by interaction, provenance and text), `baec_records`, `criterion_assessments`, `criterion_evidence` and `stringency_expressions`. Confirming does not change account state.
- **AI provenance.** `ai_runs`, `ai_run_results`, `ai_run_outputs`, `ai_artifacts` and `ai_artifact_excerpts` are append-only, in the same SQLite file as the domain tables. Today no foreign key joins any `ai_*` table to any domain table.
- **Evidence.** Verbatim triggers exist on `interaction_evidence` and `ai_artifact_excerpts`. Fifteen `baec_records` columns are protected. There is no amendment mechanism. The domain allows `buyer_exact_statement` to come from a SELLER_OBSERVATION excerpt.
- **Grounding.** `baec_app/ai/grounding.py` is a pure module. It has a public `tokenize()`, public token types (`Magnitude`, `Range`, `Compound`, `Multiplier`), and closed vocabularies. Its comparator decision function is private. It is pinned by digest and is not changed by Phase 7.
- **MCP.** The read-only composition opens with `mode=ro` and `query_only`. The static rules M1–M10 and S1–S6 apply to every file under `baec_app/mcp/`.
- **Streamlit.** No `baec_app/interfaces/` package exists, and Streamlit is not a dependency. Rule R3 already forbids `baec_app.interfaces.*` from importing `baec_app.data`.

---

## 4. Research Contract mapping

| Clause | Label | How Phase 7 honors it |
|---|---|---|
| RC-01, RC-02 (definition, four criteria) | DEFINITIONAL | The human records a finding for each of C1–C4, each with human-selected evidence. AI hypotheses are never findings (§8). |
| RC-03, RC-04 (information, evaluation endpoint) | DEFINITIONAL | Nothing in Phase 7 states intent, commitment, or purchase. AI text is labeled interpretation. |
| RC-05 (C1 needs state qualification; UNKNOWN allowed) | DEFINITIONAL | The human may record UNKNOWN for C1. AI `present_non_evaluation` is a suggestion only. Account state is not consulted or changed. |
| RC-06 (what a BAEC is not) | DEFINITIONAL | The AI's `analysis_status` makes an artifact eligible for review (§6.2). It never classifies. |
| RC-10, RC-11 (origin, elicitation mode) | IMPLEMENTATION | Both are explicit human decisions. The AI artifact holds neither, and the mapper invents neither. |
| RC-12, RC-13 (classification) | IMPLEMENTATION | The locked `classify_candidate` runs when a revision is saved, when a grant is issued, and again inside the execution transaction. A grant can be issued only for a candidate the classifier rates `CONFIRMED_BAEC`. |
| RC-14 to RC-16 (quality separate; no scoring) | PROPOSED / IMPLEMENTATION | There are no scores, confidences, or rankings anywhere. AI hypothesis statuses are shown as closed AI tokens, not as probabilities. Quality judgments are out of scope. |
| RC-17, RC-18 (stringency; threshold preservation) | PROPOSED / IMPLEMENTATION | The structured stringency is an explicit human decision. Its verbatim text must occur in human-selected evidence (§9.4). The final normalization may not change any retained threshold (§11). |
| RC-19 (one contact is not the account) | PROPOSED | `buyer_role` is stored as `None` (not established) on every Phase 7 BAEC. Representational authority stays deferred. |
| RC-20, RC-21, RC-22, RC-24, RC-34 (three states; gates) | MANAGERIAL / IMPLEMENTATION | Untouched. No Phase 7 path reaches the state machine's write side (§16). |
| RC-27 (a signal is not an opportunity) | MANAGERIAL | Not engaged. Phase 7 has no signals and no Active Opportunity path. |
| RC-29 (staleness) | MANAGERIAL | New confirmed BAECs are `CURRENT` through the locked factory. Staleness is never changed. |
| RC-30 (governance; no automated outreach) | MANAGERIAL | There is no outreach, messaging, or contact path. |
| **RC-31** (human authorization; model value is not proof) | IMPLEMENTATION | A persisted grant, issued only by the human path through the Phase 4 gate, bound to digest, revision, lineage, account and action. Supersession is persisted. MCP consumes the grant once. Actor identity remains self-asserted (§12.6). |
| **RC-32** (five provenance categories) | IMPLEMENTATION | `attributed_speaker`, normalization, hypotheses and uncertainties are AI_INFERENCE throughout. BUYER_FACT and SELLER_OBSERVATION come only from an explicit human assertion for each human-selected excerpt. |
| **RC-33** (immutable source; normalization separate; amendments) | IMPLEMENTATION | Source evidence and the AI snapshot are never edited. Evidence is selected verbatim, never rewritten. Final normalization is stored in the review revision, never in a source column. Amendments stay deferred (D9). |
| **RC-34** (leaving Active needs non-evaluation evidence) | IMPLEMENTATION | Not reachable. Confirmation causes no transition. |
| §13 (claims never made) | — | Phase 7 claims no validation, prediction, lift, security, or accuracy. Tests are software tests on synthetic, ephemeral data. |

---

## 5. Architecture overview

```
 persisted ai_artifacts (Phase 6, unchanged)
        │  7D mapper (application; human path only; deterministic; fail closed)
        ▼
 ai_proposals ── immutable AI_DRAFT snapshot + proposal_digest
        │  7E review (application object; Streamlit renders it)
        │     human selects verbatim evidence from the interaction, asserts provenance,
        │     decides findings, origin, mode, stringency, and final normalization
        │     (validated by baec-human-normalization-validation/v1)
        ▼
 ai_proposal_review_revisions ── immutable human-final content + review_content_digest (1..n)
        │  7F-B human authorizes through the Phase 4 gate (displayed digest)
        ▼
 ai_proposal_review_decisions (ACCEPTED per revision | REJECTED per proposal, terminal)
 human_authorization_grants ── bound to revision, digest, lineage, account, action, baec_id; 15 min; single use
 human_authorization_grant_supersessions ── persisted, trigger-written on a new revision or a rejection
        │  grant_id handed by the human to the MCP client
        ▼
 MCP write composition (separate process): confirm_baec(grant_id)          ← 7G
        │  ONE transaction
        ▼
 human_authorizations + baec_records (+ evidence, assessments, stringency)
 ai_proposal_confirmations (lineage link)
 human_authorization_grant_consumptions
```

**Processes.**
- The Streamlit process holds a writable connection. It can map, review, decide, and issue grants. It cannot execute a grant: the execution facade is not composed there.
- The MCP write process holds a writable connection. It can execute a grant and nothing else.
- The Phase 5 read-only MCP process is unchanged.

---

## 6. Artifact eligibility and the AI_DRAFT proposal

### 6.1 Source of artifacts (D11)

The bridge reads artifacts only from the product database it is composed over. All reads happen on that one connection, and artifacts are referenced by foreign key. An artifact identifier from a live-evaluation report cannot be imported, because live-evaluation databases are temporary and deleted (`tests/live/harness.py`). Their artifacts are not durable and are never eligible.

Phase 7 never constructs an `ExtractionService`, a provider, or an AI composition (§16.4).

### 6.2 Eligibility (all required; checked by the mapper, fail closed)

| ID | Condition |
|---|---|
| E1 | `AiProvenanceStore.get_artifact(artifact_id)` succeeds on the product connection. This re-verifies the artifact digest and the run binding. |
| E2 | The artifact's run result is `status = 'success'`, `remote_outcome = 'response_received'`, `failure_codes IS NULL`, `stop_reason = 'end_turn'`, and `response_model = requested_model`. These are re-read and re-checked, not assumed from the triggers. |
| E3 | The run carries `task_type = 'baec_extraction'`, `task_version = 'baec-extraction-task/v1'`, `output_schema_version = 'baec-extraction-output/v1'`, `canonicalization_version = 'baec-canonical-json/v1'`, and `validation_version = 'baec-extraction-validation/v2'`. |
| E4 | `canonical_result` strictly re-parses into `BaecExtractionOutput`. Re-running `validate_extraction` against the stored interaction text returns no codes. The stored artifact excerpts equal the parsed `source_excerpts` exactly (id, text, speaker, order). |
| E5 | `analysis_status == 'possible_baec_language'`. The other two statuses are not eligible, because the only Phase 7 write is a confirmation. |
| E6 | The artifact's account and interaction exist, and the interaction belongs to the account. |
| E7 | No `ai_proposals` row exists for (`artifact_id`, mapping version). If one exists, the mapper returns the stored proposal, verified, and creates nothing. |
| E8 | No `ai_proposal_confirmations` row exists for the `artifact_id`. |

**E3 is an implementation compatibility allowlist, not a BAEC research proposition.** It names the artifact formats and the validator version that mapping v1 was designed and tested against. It says nothing about the theory, and nothing about the quality of artifacts produced under other versions. A future mapping version may extend the allowlist only by explicit design.

Any failure raises a closed `ArtifactNotEligible(code)`, with no text from the artifact, and writes nothing.

### 6.3 Mapping `baec-ai-proposal-mapping/v1`

This is a pure function from (verified artifact, run, result, excerpts) to proposal content. It is deterministic: identical inputs give byte-identical canonical content.

**It may use:** exact source excerpts, normalized condition, normalized evaluation link, criterion hypotheses, uncertainties, and artifact provenance.

**It may not:** confirm a BAEC, select authoritative evidence, set evidence provenance, set any finding, set origin or elicitation mode, set `buyer_exact_statement`, `buyer_role` or stringency, read or infer account state, or contact anyone.

**Static guarantee.** The mapper module imports no domain write path, no gate, no `authority`, no `Repository` write method, no `baec_app.ai.service`, no provider, and no model SDK. It is pure apart from the read it is given.

**Proposal content `baec-ai-proposal-content/v1`** (canonical JSON; key order fixed by the format; no floats):

```
{
  "content_version": "baec-ai-proposal-content/v1",
  "mapping_version": "baec-ai-proposal-mapping/v1",
  "origin": "AI_DRAFT",
  "account_id": str, "interaction_id": str,
  "artifact": {
    "artifact_id", "artifact_digest", "ai_run_id", "provider", "requested_model", "response_model",
    "prompt_version", "prompt_digest", "validation_version", "output_schema_version",
    "request_digest", "artifact_created_at"
  },
  "ai_analysis_status": "possible_baec_language",
  "ai_suggested_excerpts": [                    // artifact order; verbatim source text; suggestions only
    {"excerpt_id": str, "text": str,
     "ai_attributed_speaker": {"value": "buyer|seller|unclear", "provenance": "AI_INFERENCE"}}
  ],
  "ai_normalized_condition":        {"value": str|null, "provenance": "AI_INFERENCE"},
  "ai_normalized_evaluation_link":  {"value": str|null, "provenance": "AI_INFERENCE"},
  "ai_criterion_hypotheses": [                  // fixed order C1, C2, C3, C4
    {"criterion": "PRESENT_NON_EVALUATION", "ai_status": "supported|not_supported|unclear",
     "excerpt_refs": [str], "explanation": str, "provenance": "AI_INFERENCE"}
  ],
  "ai_uncertainties": {"values": [str], "provenance": "AI_INFERENCE"}
}
```

- The criterion tokens map one-to-one to `BaecCriterion` names, for display alignment only. `ai_status` is never translated into a `CriterionFinding`.
- Every AI-authored value is wrapped with `provenance: "AI_INFERENCE"`. The excerpt `text` values are verbatim source text offered as **suggestions**. They carry no provenance and have no authoritative status until a human selects them and asserts a provenance (§9).
- `proposal_digest = SHA-256(UTF-8(canonical JSON))`, using the `baec-canonical-json/v1` rules (sorted keys, compact separators, UTF-8, no floats, no NaN).

### 6.4 `ProposalOrigin.AI_DRAFT` (D4)

- `ProposalOrigin` gains `AI_DRAFT = "AI_DRAFT"`, and `AI_MODEL` stays absent. The Phase 4 test asserting exactly two origins is updated prospectively to exactly three, still asserting that `AI_MODEL` is absent. Historical documents are not edited.
- **AI_DRAFT exists only as a persisted `ai_proposals` row.** No Phase 4 proposal dataclass may carry `AI_DRAFT`.
- `build_request` refuses `origin=AI_DRAFT` for every Phase 4 `RequestKind`, so `ClassificationService.open_confirmation_request(origin=AI_DRAFT)` and `request_from_proposal` fail closed. Without this, an AI-origin confirmation could bypass the grant through the in-memory Phase 4 path.

### 6.5 Test fixtures and demo data (D15)

- Phase 7 creates **no durable or public seeded demo database** and performs **no new live AI extraction**. Public or demo seeding is deferred.
- Automated tests and Streamlit AppTest use **ephemeral, deterministic, test-only** databases, created in temporary directories or in memory and discarded after each test. Their artifacts are produced with the existing test builders and `FakeProvider`.
- Fixture artifacts necessarily satisfy the Phase 6 schema, including `provider = 'anthropic'`. They are therefore **explicitly test-only**:
  - they live only in `tests/`;
  - their account and interaction ids, and their model ids, use a reserved fixture prefix;
  - no document, UI text, or report presents them as evidence of an actual Anthropic model invocation.
- No Phase 6 provenance-schema change is made to add a synthetic-provider label.
- A static test checks that no production module or committed database file contains fixture artifacts. Committed `.db`, `.sqlite`, or seed files with `ai_*` rows are refused.

---

## 7. Three immutable layers (D14)

| Layer | Stored in | Written by | Ever updated? |
|---|---|---|---|
| 1. Source evidence | `interactions`; `interaction_evidence` (created at execution from human selections); `ai_artifact_excerpts` (Phase 6 suggestions) | Phase 3 and 6 paths; the execution transaction | Never (existing triggers) |
| 2. AI proposal snapshot | `ai_proposals.content` + `proposal_digest` | 7D mapper, human path | Never (append-only) |
| 3. Human-reviewed final content | `ai_proposal_review_revisions.content` + `review_content_digest`, revisions 1..n | 7E review, human path | Never. An edit is a new revision. |

A grant binds to one revision's id and digest. Any later revision supersedes it through a persisted supersession record (§12.4).

---

## 8. Human review model (application layer, UI-agnostic)

### 8.1 Read object: `ProposalReview` (frozen, built by the application from stored rows)

| Section | Content | Display label |
|---|---|---|
| `source` | Full interaction text (the selection surface for evidence), account and interaction ids, `occurred_at` | SOURCE; untrusted text, rendered as plain text |
| `ai_suggested_excerpts` | `excerpt_id`, verbatim text | AI SUGGESTION; not evidence until selected and attributed by a human |
| `ai_speaker` | `attributed_speaker` for each suggested excerpt | AI_INFERENCE |
| `ai_normalization` | Condition and evaluation link | AI_INFERENCE |
| `ai_hypotheses` | Four entries: status, excerpt refs, explanation | AI_INFERENCE; suggestion only |
| `ai_uncertainties` | List | AI_INFERENCE |
| `ai_provenance` | artifact_id, artifact_digest, run id, provider, requested and response model, prompt version and digest, validator version, request digest, created_at, mapping version, proposal_digest | provenance metadata |
| `latest_revision` | The latest human revision, if any, with its decision and grant status (ACTIVE, EXPIRED, SUPERSEDED, CONSUMED) | HUMAN |
| `classification_preview` | The locked classifier's result for the latest revision | DETERMINISTIC |

### 8.2 Write object: `ReviewDecisions` (frozen; every authoritative field required; no defaults; no AI-derived constructor)

| Field | Type | Rule |
|---|---|---|
| `evidence_selections` | tuple of (`selection_id`, `text`, `provenance`, `suggested_excerpt_id` or None) | `text` is any exact, case-sensitive, non-blank substring of the persisted interaction text, chosen by the human (§9.1). `provenance` is BUYER_FACT or SELLER_OBSERVATION, chosen by the human. `suggested_excerpt_id` is set only when the human took an AI suggestion unchanged, and then `text` must equal that suggestion byte for byte. |
| `source_selection_id` | str | Must be one of the selections. |
| `buyer_exact_statement` | str or None | None, or an exact case-sensitive substring of the source selection's text. Allowed only when the source selection is asserted BUYER_FACT (§9.3). |
| `findings` | exactly four (criterion, finding, evidence selection ids) | Each `finding` is an explicit MET, NOT_MET or UNKNOWN. MET and NOT_MET need at least one selection. `rationale` is always None in Phase 7, so AI explanation text can never land in an untagged column. |
| `articulation_origin` | `ArticulationOrigin` | Explicit human choice (RC-10). |
| `elicitation_mode` | `ElicitationMode` | Explicit human choice (RC-11). |
| `stringency` | explicit "none stated", or a `StringencyExpression` | Explicit human choice. A null stringency must be chosen as "none stated", not left as a default (§9.4). |
| `normalization` | for each of condition and link: (`disposition`, `final_text`) | `disposition` ∈ {`KEEP_AI`, `EDITED`, `NONE`}, chosen explicitly. `KEEP_AI` copies the AI value; `EDITED` takes the human text; `NONE` stores null. Every non-null final value must pass §11. |

**Rules that prevent silent population**
- `ReviewDecisions` has no default values and no factory taking the proposal or AI fields.
- A static test forbids any read of `ai_status`, `ai_attributed_speaker`, `explanation`, or `ai_uncertainties` inside the module that builds `ReviewDecisions` or the candidate.
- Mutation tests seed each AI field into an authoritative field and require a test failure.
- The Streamlit page renders AI suggestions beside empty controls: no preselected evidence, an empty provenance selector for every selection, unselected finding radios, and an empty normalization disposition.
- Submitting with any control undecided is refused by the application, not only by the UI.

### 8.3 Review commands (human path only; none reachable from MCP)

| Command | Effect |
|---|---|
| `map_artifact(session, artifact_id) -> ProposalRef` | Runs §6.2 and §6.3 and inserts `ai_proposals`, or returns the existing verified proposal. |
| `save_revision(session, proposal_id, decisions) -> RevisionRef` | Validates §9 and §11, builds the candidate through the domain constructors, and inserts revision n+1 (append-only). A database trigger persists the supersession of any unconsumed grant on an earlier revision (§12.4). Returns the classification preview. Any validation failure stores nothing. |
| `request_grant(session, revision_id) -> ApprovalRequest` | Registers a Phase 4 gate request of the new kind `ISSUE_CONFIRMATION_GRANT`. Its payload is (revision_id, review_content_digest, account_id, proposal_id, baec_id), and it is refused unless the preview is confirmable. |
| `issue_confirmation_grant(approval) -> GrantView` | Redeems the approval, then in one transaction inserts the ACCEPTED decision (if absent) and the grant. The signature is `(self, approval)`, consistent with `test_command_signatures_are_exactly_self_and_approval`. |
| `request_rejection(session, proposal_id)` / `reject_proposal(approval)` | The same gate pattern. Inserts a terminal REJECTED decision for the proposal. A trigger persists the supersession of any unconsumed grant. |

Both decision commands go through the Phase 4 gate, so the human-interaction adapter must pass the digest it displayed (`approve(..., displayed_digest=...)`). The gate is still accident resistance, not a security boundary. What Phase 7 adds is the persisted, bound, expiring, supersedable grant.

---

## 9. Evidence rule (D9, D10, D16)

### 9.1 Human evidence selection

- **AI excerpts are suggestions only.** The human may select any exact verbatim evidence from the **same persisted source interaction**: an AI suggestion unchanged, or any other span of the interaction text.
- A selection's `text` must be:
  - non-blank;
  - an exact, case-sensitive substring of `interactions.text` for the proposal's interaction.
- There is no whitespace normalization, case folding, trimming, ellipsis, or joining of non-contiguous spans. A text that is not an exact substring is refused.
- **The human may not invent, rewrite, or paraphrase source evidence.** The only accepted form of selection is an exact substring of stored source text, and that is the structural guarantee.
- Each `text` appears at most once per revision, with exactly one human-asserted provenance.
- When `suggested_excerpt_id` is set, `text` must equal that `ai_artifact_excerpts.text`. This records truthfully whether a selection came from an AI suggestion; it does not make the suggestion authoritative.
- Unselected AI suggestions are not evidence and are not stored as evidence.

### 9.2 Provenance

- Every selection carries an explicit human assertion of BUYER_FACT or SELLER_OBSERVATION.
- `attributed_speaker` is AI_INFERENCE. It is shown beside a suggestion and never read by the code that builds selections, assertions, or the candidate.
- A selection the human cannot attribute is not selected, because UNKNOWN or AI_INFERENCE provenance cannot be evidence (RC-32 and the existing domain rule).

### 9.3 `buyer_exact_statement`

- It stays verbatim source text: an exact substring of the source selection's text (domain check).
- Phase 7 adds an application rule (IMPLEMENTATION): a non-null `buyer_exact_statement` requires the source selection to be explicitly asserted BUYER_FACT. This closes the Phase 7A gap for the Phase 7 path only. The domain is not changed.
- It is never derived from, compared to, or replaced by any normalization field. No code path assigns a normalization value to a candidate field, and a static test plus a mutation enforce this.

### 9.4 Stringency (RC-17, RC-18)

- `StringencyExpression` is recorded only from an explicit human choice.
- `verbatim_text` must be an exact substring of a selected evidence text. `qualitative_term`, `recurrence_text` and `timing_text`, when present, must be exact substrings of `verbatim_text`. These are Phase 7 application rules.
- `comparator`, `numeric_value` and `unit` are human-entered and validated by the existing domain rules: no invented number, and "more than" is not "at least".
- Choosing "none stated" stores `stringency = None`.

### 9.5 At execution; fail-closed conditions; no amendment

At execution, each selection becomes `EvidenceExcerpt(text, provenance, interaction_id)` from the bound revision only. The execution re-checks each text as a substring of `interactions.text`. The existing `interaction_evidence_verbatim` trigger and the `Repository._evidence_id` checks then apply unchanged.

Saving a revision, issuing a grant, and executing all refuse (closed code, nothing written) when:
- a selection is blank or not verbatim, or comes from another interaction;
- a selection claims a suggestion it does not equal;
- a provenance value is outside {BUYER_FACT, SELLER_OBSERVATION};
- a finding references an unknown selection;
- `buyer_exact_statement` fails §9.3;
- stringency fails §9.4;
- normalization fails §11.

After confirmation, Phase 7 has no path that edits, replaces, or supersedes the BAEC or its evidence. D8 plus the lineage link prevent re-confirming the same lineage as a "correction". RC-33 amendments remain a later, separately designed phase.

---

## 10. Normalization storage (D7)

**Decision: store the final normalization in the immutable review revision, and link it to the BAEC through `ai_proposal_confirmations`. Do not write `baec_records.normalized_*` on the Phase 7 path.** Those columns stay NULL for AI_DRAFT BAECs.

**Why this is the minimum safe change.**
- `baec_records.normalized_*` are protected columns typed as `AiDerivedText(text, generated_at, model)`. Writing human-edited text there would label human text as model output.
- There is no column for an evaluation link.
- Adding columns to a protected table means changing `BAEC_RECORD_PROTECTED_COLUMNS`, the factories, the repository mapping, and the MCP read adapters. The linked revision needs none of these.
- The final normalization is still written in the same transaction as the BAEC, through a 1:1 link, so it is "stored with the confirmed BAEC" and immutable.

**What is retained.** For each of `normalized_condition` and `normalized_evaluation_link`, the lineage holds:
1. the AI value, in the proposal snapshot;
2. the human disposition (`KEEP_AI`, `EDITED`, or `NONE`) and the final value, in the revision;
3. the validation contract version and result, in the revision (§11).

`EDITED` with text equal to the AI value is refused; the human must use `KEEP_AI`. Final values are labeled DERIVED (human-reviewed) and never appear in any evidence field.

**Read-side consequence.** The Phase 5 resource `baec://baecs/{id}` will show `ai_derived_normalized_condition: null` for AI_DRAFT BAECs. Phase 5 is unchanged by design. Exposing lineage on the read side is a later phase.

---

## 11. Human-normalization validation contract `baec-human-normalization-validation/v1` (D17)

### 11.1 Purpose and scope

The contract decides whether a final, human-reviewed `normalized_condition` and `normalized_evaluation_link` may be stored. It is a deterministic IMPLEMENTATION safeguard supporting RC-18.

- **It is not AI validation.** It does not carry the `baec-extraction-validation/v2` identity and never writes AI failure codes.
- **It is not BAEC classification**, not evidence-provenance determination, and not human authorization.

### 11.2 Inputs

- The final normalization values, both of them, for every non-null value regardless of disposition. A `KEEP_AI` value is validated too: the AI value passed validation v2 against the whole interaction, not against the human's selected evidence.
- The **evidence source**: the texts of all human-selected evidence in the same revision, of both asserted provenances. Each selection is tokenized **separately**. Selections are never concatenated, so no numeric expression is formed across a selection boundary.

### 11.3 Rules (all fail closed)

**Structural**
- Non-blank.
- At most 500 Unicode code points.
- A non-null value requires at least one evidence selection.

**Numeric grounding**

Each numeric token in a final value is computed with the unchanged `baec_app.ai.grounding.tokenize`. It must be supported by a token in the evidence source, compared in this fixed order:

| Order | Check | Failure code ending |
|---|---|---|
| 1 | Some evidence magnitude has the same value | `number_unsupported` |
| 2 | Same numeric kind among those. Where either side is a currency kind (`currency:*`), a mismatch reports `currency_changed`. | `currency_changed` or `numeric_kind_changed` |
| 3 | Same unit | `unit_changed` |
| 4 | Same comparator. Classified comparators (GREATER_THAN, AT_LEAST, LESS_THAN, AT_MOST, EXACTLY, APPROXIMATELY, or none) must be identical. | `comparator_changed` |
| 5 | Unresolved comparator wording: when the evidence magnitude carries `unresolved:past`, `unresolved:beyond`, `unresolved:within`, or `unresolved:over` (or CONFLICTING) and the final value **retains that numeric expression**, the final value must carry exactly the same unresolved comparator. The same word must precede it, so it is never paraphrased into a classified comparator and never dropped. If the final value omits the numeric expression entirely, no token exists and nothing is checked. | `comparator_unresolved` |

- Ranges, compounds (dates, scale words), and multiplier words pass only by exact equality with an evidence token. Failure gives `compound_unsupported` or `number_unsupported`.

**Coherent same-excerpt support (IMPLEMENTATION grounding rule under `baec-human-normalization-validation/v1`).**
- Grounding across several selected excerpts must never build support by combining unrelated pieces from separate excerpts.
- For each retained numeric expression in a final value, **one single evidence token, from one selected excerpt**, must coherently support it. That one token must match the magnitude, the numeric kind (including currency), the unit, and the comparator, including any unresolved comparator wording.
- Checks 1–5 are therefore applied as successive filters over the **same candidate tokens**. Each check narrows the tokens that passed the previous one, and support exists only if at least one token survives all five. A value from one excerpt can never be paired with a unit, currency, or comparator from another.
- Because each selection is tokenized on its own, no evidence token spans two excerpts. Separate excerpts are never joined into a synthetic numeric expression that no individual excerpt contains.
- Different numeric expressions in the same final value may each be supported by a different excerpt. Selected excerpts may collectively support different statements.
- This rule belongs to the Phase 7 contract only. The Phase 6 grounding implementation is not changed.
- Codes are `human_normalization_{condition|evaluation_link}_{ending}`. They form a closed vocabulary owned by the contract and carry no source text or values.

### 11.4 Implementation placement

Yes: implementation needs one **new small application-layer module**, `baec_app/application/human_normalization.py`.
- It reuses only the unchanged public primitives of `baec_app/ai/grounding.py`: `tokenize`, the token types `Magnitude`, `Range`, `Compound` and `Multiplier`, and the constants for numeric kinds, comparators and unresolved expressions.
- The comparator decision function in `grounding.py` is private, so the contract implements its own small, documented decision function.

**Why the application layer.**
- The domain layer must not import `baec_app.ai`.
- The AI package must not host human-review logic.
- The contract needs the selected evidence that only the review layer assembles.

**Guards.**
- `baec_app/ai/grounding.py` and the Phase 6 validation design are not changed. The existing digest pin (`test_validation_v2_is_untouched`) keeps that true.
- A new static rule: `human_normalization.py` imports from `baec_app.ai` only those named public symbols of `grounding`, and nothing from `service`, `validation`, `provider`, `composition`, `prompts`, or any model SDK.

### 11.5 When it runs

- At `save_revision`; a failure stores no revision.
- At grant issuance.
- Again inside the execution transaction (step 12, §14).

The revision records `normalization_validation_version = 'baec-human-normalization-validation/v1'`. Execution refuses a revision whose recorded version is not the current contract version (`normalization_contract_mismatch`).

---

## 12. Grant design (D1, D5, D14, D18)

### 12.1 Binding

The grant row holds:
- `grant_id`;
- `action = 'CONFIRM_BAEC'`;
- `account_id`, `interaction_id`, `artifact_id`, `proposal_id`, `proposal_digest`;
- `review_revision_id`, `review_content_digest`;
- `decision_id`;
- `issue_sequence` and `supersedes_grant_id`;
- `baec_id`, assigned at issuance and bound as the authorization subject;
- `actor_label`;
- `issued_at`, `expires_at = issued_at + 15 minutes`, `ttl_seconds = 900`;
- `grant_format = 'baec-human-grant/v1'`, `issuing_surface = 'streamlit-review/v1'`;
- `grant_digest`.

`grant_digest = SHA-256(canonical JSON of every binding field above except grant_digest)`.

### 12.2 Identifier

`grant_id = "grant_" + secrets.token_hex(32)` (256 random bits). It is effectively a short-lived bearer capability. The tool argument pattern is `^grant_[0-9a-f]{64}$`. No MCP resource lists, searches, or reveals grants. The human hands the grant_id to the MCP client.

### 12.3 Lifecycle

```
            issue (human path, via gate)
                       │
                       ▼
                    ISSUED ──────────────► CONSUMED      (execution committed; terminal)
                       │
        ┌──────────────┼───────────────────┐
        ▼              ▼                   ▼
   SUPERSEDED      SUPERSEDED         EXPIRED (derived from time only)
   (new revision)  (rejection)             │
                                           ▼
                                      SUPERSEDED (re-issued: the successor grant names it)
```

- **Persisted facts.** Issuance (grants row), supersession (supersessions row, or a successor grant's `supersedes_grant_id`), and consumption (consumptions row). All are append-only and never deleted or rewritten.
- **ACTIVE** = issued, not consumed, not superseded, and `now < expires_at`.
- **Deterministic database invariant** (no wall-clock predicate in any index or trigger): for one (`review_revision_id`, `action`), at most one grant is **unretired**, meaning neither consumed nor superseded. Since every ACTIVE grant is unretired, at most one ACTIVE grant exists for that revision and action.
- **Expiry is evaluated only by application code** with an injected clock: at issuance, to decide re-issue, and at execution. Execution checks supersession before expiry, so **a superseded grant fails closed (`grant_superseded`) even if its 15 minutes have not elapsed.**

### 12.4 Supersession mechanism (persisted, deterministic)

| Event | What is persisted | How |
|---|---|---|
| Human edits (new revision n+1) | One `human_authorization_grant_supersessions` row with reason `REVISION_SUPERSEDED` and `superseding_revision_id` = the new revision, for every unretired grant on any earlier revision of the proposal | An `AFTER INSERT` trigger on `ai_proposal_review_revisions`, in the same statement transaction as the revision. It cannot be forgotten by application code. |
| Human rejects the proposal | A row with reason `PROPOSAL_REJECTED` and `rejection_decision_id`, for every unretired grant of the proposal | An `AFTER INSERT` trigger on `ai_proposal_review_decisions` when `decision = 'REJECTED'` |
| Re-issue after expiry (same revision, unchanged content) | The new grant row has `issue_sequence = n+1` and `supersedes_grant_id` = grant n | Application check that grant n is expired and unconsumed. The insert trigger requires grant n to be the only unretired grant for that revision and action. UNIQUE(`supersedes_grant_id`). |

- Re-issue while grant n is still ACTIVE is refused (`active_grant_exists`).
- The old grant row is never altered. Its retired status is a pure function of the append-only rows that reference it.

### 12.5 Issuance preconditions

Checked in the application, then inside the issuance transaction:
- the revision is the latest for its proposal;
- the proposal has no REJECTED decision and no confirmation;
- the revision passes §9 and §11 again;
- the locked classifier rates its candidate `CONFIRMED_BAEC`;
- no ACTIVE grant exists for the revision and action, and any unretired predecessor is expired;
- the redeemed gate approval's digest equals the request digest that bound (revision_id, review_content_digest).

### 12.6 Identity limitation (stated plainly)

`actor_label` is self-asserted text from the Streamlit session. Authentication is out of scope. A grant shows that the human-interaction code path created the authorization. It does not authenticate who the person is, and it does not stop another local process with write access to the database file from inserting rows. AppTest (§17) proves UI behavior, not real-world human identity.

### 12.7 Time

- Timestamps use `encode_datetime` (UTC ISO 8601 with microseconds and offset).
- Clocks are injected.
- Execution reads `now` once, inside the transaction.
- `now < issued_at` fails closed with `grant_clock_invalid`.
- Clock skew between the Streamlit and MCP processes on one machine is accepted as a prototype limitation.

---

## 13. Persistence model: schema version 7

### 13.1 `human_authorizations`: complemented, not reused or extended

`human_authorizations` stays exactly as it is: the domain authorization audit, one row per executed authorization, loaded and re-verified as `HumanAuthorization`, and referenced by `baec_records`, `dormancy_judgments` and `account_state_transitions`.

A grant is a different thing. It is a pre-execution permission that may expire or be superseded unused, it carries bindings the domain value has no fields for, and it must exist before its subject row does. Merging them would collapse four concepts that must stay distinct:

| Concept | Table |
|---|---|
| Human review | `ai_proposal_review_revisions`, `ai_proposal_review_decisions` |
| Authorization grant | `human_authorization_grants`, `human_authorization_grant_supersessions` |
| Grant consumption | `human_authorization_grant_consumptions` |
| Domain authorization audit | `human_authorizations` (unchanged). One row is inserted at execution: `authorized_by = grant.actor_label`, `authorized_at = grant.issued_at` (the human act), `action = CONFIRM_BAEC`, `subject_id = grant.baec_id`, `target_state = NULL`. |

### 13.2 New tables

**Schema v7 adds exactly seven tables:**
1. `ai_proposals`
2. `ai_proposal_review_revisions`
3. `ai_proposal_review_decisions`
4. `human_authorization_grants`
5. `human_authorization_grant_supersessions`
6. `ai_proposal_confirmations`
7. `human_authorization_grant_consumptions`

**Every existing table is unchanged.**

All tables are `STRICT`. All foreign keys are `ON DELETE RESTRICT`. Every new table receives `<table>_no_update` and `<table>_no_delete` triggers, plus a `<table>_no_replace` trigger over each UNIQUE key (in new `BRIDGE_APPEND_ONLY_TABLES` / `BRIDGE_REPLACE_GUARDED_KEYS`, kept apart from the Phase 3 and Phase 6 constants). `SCHEMA_VERSION = 7`, and the version is checked as today (synthetic data: rebuild from seed).

```sql
CREATE TABLE ai_proposals (
    proposal_id TEXT PRIMARY KEY CHECK (proposal_id GLOB 'aiprop_*'),
    origin TEXT NOT NULL CHECK (origin = 'AI_DRAFT'),
    mapping_version TEXT NOT NULL CHECK (mapping_version = 'baec-ai-proposal-mapping/v1'),
    content_version TEXT NOT NULL CHECK (content_version = 'baec-ai-proposal-content/v1'),
    artifact_id TEXT NOT NULL,
    artifact_digest TEXT NOT NULL CHECK (<sha256 hex>),
    account_id TEXT NOT NULL,
    interaction_id TEXT NOT NULL,
    content TEXT NOT NULL CHECK (json_valid(content)),
    proposal_digest TEXT NOT NULL CHECK (<sha256 hex>),
    created_by TEXT NOT NULL CHECK (trim(created_by) <> ''),  -- self-asserted actor label
    created_at TEXT NOT NULL,
    UNIQUE (artifact_id, mapping_version),                      -- one proposal per artifact per mapping
    UNIQUE (proposal_id, account_id, interaction_id, artifact_id, proposal_digest),  -- parent key
    FOREIGN KEY (artifact_id, interaction_id) REFERENCES ai_artifacts (artifact_id, interaction_id),
    FOREIGN KEY (interaction_id, account_id) REFERENCES interactions (interaction_id, account_id)
) STRICT;
-- trigger ai_proposals_artifact_binding: refuse unless artifact_digest equals ai_artifacts.artifact_digest,
--   the artifact's run result is status 'success', and the run's validation_version is
--   'baec-extraction-validation/v2' (the E3 compatibility allowlist).

CREATE TABLE ai_proposal_review_revisions (
    review_revision_id TEXT PRIMARY KEY CHECK (review_revision_id GLOB 'aireview_*'),
    proposal_id TEXT NOT NULL,
    proposal_digest TEXT NOT NULL,
    account_id TEXT NOT NULL, interaction_id TEXT NOT NULL, artifact_id TEXT NOT NULL,
    revision_number INTEGER NOT NULL CHECK (revision_number >= 1),
    previous_revision_id TEXT REFERENCES ai_proposal_review_revisions (review_revision_id),
    review_content_version TEXT NOT NULL CHECK (review_content_version = 'baec-ai-review-content/v1'),
    normalization_validation_version TEXT NOT NULL
        CHECK (normalization_validation_version = 'baec-human-normalization-validation/v1'),
    content TEXT NOT NULL CHECK (json_valid(content)),
    review_content_digest TEXT NOT NULL CHECK (<sha256 hex>),
    actor_label TEXT NOT NULL CHECK (trim(actor_label) <> ''),
    created_at TEXT NOT NULL,
    CHECK ((revision_number = 1) = (previous_revision_id IS NULL)),
    UNIQUE (proposal_id, revision_number),
    UNIQUE (review_revision_id, proposal_id, account_id, interaction_id, artifact_id, review_content_digest),
    FOREIGN KEY (proposal_id, account_id, interaction_id, artifact_id, proposal_digest)
        REFERENCES ai_proposals (proposal_id, account_id, interaction_id, artifact_id, proposal_digest)
) STRICT;
-- BEFORE INSERT ..._sequence: revision_number = 1 + max for the proposal; previous_revision_id is that latest.
-- BEFORE INSERT ..._open: refuse if the proposal has a REJECTED decision or a confirmation.
-- AFTER INSERT ..._supersede_grants: INSERT one supersessions row (REVISION_SUPERSEDED,
--   superseding_revision_id = NEW.review_revision_id, superseded_at = NEW.created_at,
--   actor_label = NEW.actor_label) for every unretired grant of NEW.proposal_id.

CREATE TABLE ai_proposal_review_decisions (
    decision_id TEXT PRIMARY KEY CHECK (decision_id GLOB 'aidecision_*'),
    proposal_id TEXT NOT NULL REFERENCES ai_proposals (proposal_id),
    review_revision_id TEXT REFERENCES ai_proposal_review_revisions (review_revision_id),
    account_id TEXT NOT NULL,
    decision TEXT NOT NULL CHECK (decision IN ('ACCEPTED', 'REJECTED')),
    actor_label TEXT NOT NULL CHECK (trim(actor_label) <> ''),
    decided_at TEXT NOT NULL,
    CHECK (decision <> 'ACCEPTED' OR review_revision_id IS NOT NULL),  -- acceptance is per revision
    UNIQUE (review_revision_id),                                      -- at most one decision per revision
    UNIQUE (decision_id, review_revision_id)
) STRICT;
CREATE UNIQUE INDEX ai_proposal_review_decisions_one_rejection
    ON ai_proposal_review_decisions (proposal_id) WHERE decision = 'REJECTED';   -- terminal, once
-- BEFORE INSERT ..._binding: proposal_id and account_id equal the revision's (when a revision is named)
--   and the proposal's.
-- BEFORE INSERT ..._latest: an ACCEPTED revision is the latest for its proposal.
-- BEFORE INSERT ..._terminal: refuse if the proposal already has a REJECTED decision or a confirmation.
-- AFTER INSERT ..._reject_supersedes (WHEN NEW.decision = 'REJECTED'): INSERT a supersessions row
--   (PROPOSAL_REJECTED, rejection_decision_id = NEW.decision_id) for every unretired grant of the proposal.

CREATE TABLE human_authorization_grants (
    grant_id TEXT PRIMARY KEY CHECK (grant_id GLOB 'grant_*' AND length(grant_id) = 70),
    grant_format TEXT NOT NULL CHECK (grant_format = 'baec-human-grant/v1'),
    action TEXT NOT NULL CHECK (action = 'CONFIRM_BAEC'),
    issuing_surface TEXT NOT NULL CHECK (issuing_surface = 'streamlit-review/v1'),
    account_id TEXT NOT NULL, interaction_id TEXT NOT NULL, artifact_id TEXT NOT NULL,
    proposal_id TEXT NOT NULL, proposal_digest TEXT NOT NULL,
    review_revision_id TEXT NOT NULL, review_content_digest TEXT NOT NULL,
    decision_id TEXT NOT NULL,
    issue_sequence INTEGER NOT NULL CHECK (issue_sequence >= 1),
    supersedes_grant_id TEXT UNIQUE REFERENCES human_authorization_grants (grant_id),
    baec_id TEXT NOT NULL UNIQUE CHECK (baec_id GLOB 'BAEC-*'),
    actor_label TEXT NOT NULL CHECK (trim(actor_label) <> ''),
    issued_at TEXT NOT NULL,
    ttl_seconds INTEGER NOT NULL CHECK (ttl_seconds = 900),
    expires_at TEXT NOT NULL,
    grant_digest TEXT NOT NULL CHECK (<sha256 hex>),
    CHECK ((issue_sequence = 1) = (supersedes_grant_id IS NULL)),
    CHECK (abs((julianday(expires_at) - julianday(issued_at)) * 86400.0 - 900.0) < 0.001),
    UNIQUE (review_revision_id, action, issue_sequence),
    UNIQUE (grant_id, baec_id, account_id, interaction_id, artifact_id, proposal_id, review_revision_id),
    FOREIGN KEY (review_revision_id, proposal_id, account_id, interaction_id, artifact_id, review_content_digest)
        REFERENCES ai_proposal_review_revisions
            (review_revision_id, proposal_id, account_id, interaction_id, artifact_id, review_content_digest),
    FOREIGN KEY (decision_id, review_revision_id)
        REFERENCES ai_proposal_review_decisions (decision_id, review_revision_id)
) STRICT;
-- BEFORE INSERT ..._accepted: the decision is ACCEPTED; the revision is the latest; no REJECTED decision and
--   no confirmation exist for the proposal.
-- BEFORE INSERT ..._proposal_digest: proposal_digest equals ai_proposals.proposal_digest.
-- BEFORE INSERT ..._one_unretired: refuse if any grant for the same (review_revision_id, action) is unretired
--   (no consumption row, no supersessions row, no successor grant) other than NEW.supersedes_grant_id.
--   When supersedes_grant_id is set, it must be the grant with issue_sequence = NEW.issue_sequence - 1 for
--   the same revision and action, and it must be unconsumed and have no supersessions row.
-- (Expiry of the predecessor, exact 15-minute equality, and grant_digest are verified by the application;
--  the julianday CHECK is defense in depth. No trigger or index evaluates the current time.)

CREATE TABLE human_authorization_grant_supersessions (
    grant_id TEXT PRIMARY KEY REFERENCES human_authorization_grants (grant_id),
    reason TEXT NOT NULL CHECK (reason IN ('REVISION_SUPERSEDED', 'PROPOSAL_REJECTED')),
    superseding_revision_id TEXT REFERENCES ai_proposal_review_revisions (review_revision_id),
    rejection_decision_id TEXT REFERENCES ai_proposal_review_decisions (decision_id),
    actor_label TEXT NOT NULL CHECK (trim(actor_label) <> ''),
    superseded_at TEXT NOT NULL,
    CHECK ((reason = 'REVISION_SUPERSEDED') = (superseding_revision_id IS NOT NULL)),
    CHECK ((reason = 'PROPOSAL_REJECTED') = (rejection_decision_id IS NOT NULL))
) STRICT;
-- BEFORE INSERT ..._binding: the superseding revision (or rejection) belongs to the grant's proposal; a
--   superseding revision has a higher revision_number than the grant's revision; the grant is unconsumed.
-- Re-issue supersession is recorded by the successor grant's supersedes_grant_id, not here.

CREATE TABLE ai_proposal_confirmations (
    baec_id TEXT PRIMARY KEY,
    artifact_id TEXT NOT NULL UNIQUE,                          -- D8: one BAEC per AI lineage
    proposal_id TEXT NOT NULL UNIQUE,
    review_revision_id TEXT NOT NULL UNIQUE,
    grant_id TEXT NOT NULL UNIQUE,
    authorization_id INTEGER NOT NULL UNIQUE REFERENCES human_authorizations (authorization_id),
    account_id TEXT NOT NULL, interaction_id TEXT NOT NULL,
    FOREIGN KEY (baec_id, account_id) REFERENCES baec_records (baec_id, account_id),
    FOREIGN KEY (baec_id, interaction_id) REFERENCES baec_records (baec_id, source_interaction_id),
    FOREIGN KEY (grant_id, baec_id, account_id, interaction_id, artifact_id, proposal_id, review_revision_id)
        REFERENCES human_authorization_grants
            (grant_id, baec_id, account_id, interaction_id, artifact_id, proposal_id, review_revision_id)
) STRICT;
-- BEFORE INSERT ..._authorization_binding: baec_records.confirmation_authorization_id = NEW.authorization_id,
--   and that human_authorizations row has action CONFIRM_BAEC, subject_id = NEW.baec_id, target_state NULL,
--   authorized_by = grant.actor_label, authorized_at = grant.issued_at.
-- BEFORE INSERT ..._unretired: the grant has no supersessions row and no successor grant; its revision is the
--   latest; no REJECTED decision exists.
-- BEFORE INSERT ..._classification: baec_records.classification = 'CONFIRMED_BAEC' and staleness 'CURRENT'.

CREATE TABLE human_authorization_grant_consumptions (
    grant_id TEXT PRIMARY KEY,                                 -- single use
    baec_id TEXT NOT NULL UNIQUE,
    executor_surface TEXT NOT NULL CHECK (executor_surface = 'mcp-write/confirm_baec/v1'),
    consumed_at TEXT NOT NULL,
    FOREIGN KEY (baec_id) REFERENCES ai_proposal_confirmations (baec_id),
    FOREIGN KEY (grant_id) REFERENCES ai_proposal_confirmations (grant_id)
) STRICT;
-- BEFORE INSERT ..._window: julianday(grant.issued_at) <= julianday(consumed_at) < julianday(grant.expires_at)
--   (this compares two stored values, not the current time).
-- BEFORE INSERT ..._pairing: the confirmations row for baec_id has the same grant_id.
```

**Insert order inside the execution transaction:**
1. `human_authorizations`
2. `interaction_evidence`
3. `baec_records`
4. `criterion_*` and `stringency_expressions`
5. `ai_proposal_confirmations`
6. `human_authorization_grant_consumptions`

The consumption references the link, so a consumption cannot exist without its link. A link without a consumption can arise only inside an uncommitted transaction. The load-time integrity check `verify_bridge_integrity()` asserts:
- a 1:1 pairing of links and consumptions;
- that every retired grant is retired by exactly one mechanism;
- that at most one unretired grant exists per (revision, action).

**Cross-account.** Every child row carries `account_id`, `interaction_id` and `artifact_id` through composite foreign keys back to `interactions (interaction_id, account_id)` and `ai_artifacts (artifact_id, interaction_id)`. A grant, revision, link, or BAEC can therefore never pair one account's lineage with another account's BAEC.

**Indexes.** UNIQUE constraints already index every lookup the execution path uses. Additional indexes:
- `ai_proposals(account_id)`
- `human_authorization_grants(proposal_id)`, used by the supersession triggers
- `ai_proposal_confirmations(account_id)`
- the partial unique index on rejections shown above

**Review content `baec-ai-review-content/v1`** (canonical JSON):
- `review_content_version`, `normalization_validation_version`
- `proposal_id`, `proposal_digest`, `account_id`, `interaction_id`, `artifact_id`
- `revision_number`, `previous_revision_id`
- `evidence_selections` [{selection_id, text, provenance, suggested_excerpt_id}]
- `source_selection_id`, `buyer_exact_statement`, `buyer_role: null`
- `findings` [{criterion, finding, evidence_selection_ids}] in C1–C4 order
- `articulation_origin`, `elicitation_mode`
- `stringency` (null or the full expression, with `numeric_value` as a decimal string)
- `normalization` {condition, evaluation_link: {disposition, ai_value, final_value}}
- `captured_at`

`captured_at` is the source interaction's `occurred_at`: the time the buyer's words were captured. It is deterministic, and neither the human nor the AI chooses it (IMPLEMENTATION). `review_content_digest = SHA-256(canonical JSON)`. Because the content embeds `proposal_digest` and `previous_revision_id`, revisions form a hash-linked chain.

---

## 14. Atomic execution: `confirm_baec(grant_id)` (D12)

The MCP tool calls `GrantExecutionFacade.confirm_baec(grant_id)`. Everything below runs in **one** `transaction(connection)` (`BEGIN IMMEDIATE`), on the single writable connection owned by the write composition.

```
 0. Validate the grant_id format (no I/O). Failure → grant_id_invalid.
 1. BEGIN IMMEDIATE.                         (concurrent executions serialize here)
 2. now = clock.now()                        (read once)
 3. Load the grant row.                      Missing → grant_not_found
 4. Consumption row exists?                  → grant_already_consumed
 5. Supersessions row or successor grant exists? → grant_superseded   (checked before expiry)
 6. Recompute grant_digest; check format, action = CONFIRM_BAEC,
    surface, ttl and the exact expires_at.   → grant_binding_mismatch
 7. now < issued_at → grant_clock_invalid;  now >= expires_at → grant_expired
 8. Load the revision; recompute review_content_digest; equal to the grant's;
    account / interaction / artifact / proposal equal to the grant's → else grant_binding_mismatch
 9. The revision is the latest for the proposal → else review_revision_superseded (defense in depth)
10. The decision is ACCEPTED, with no REJECTED decision → else proposal_rejected
11. Load the proposal; recompute proposal_digest; artifact_digest equals ai_artifacts → else lineage_integrity_failure
    No confirmation exists for the artifact or proposal → else lineage_already_confirmed
12. Recorded normalization_validation_version is current → else normalization_contract_mismatch.
    Re-run the §9 evidence checks and the §11 normalization contract on the revision content
    → else evidence_invalid / normalization_invalid.
    Rebuild the candidate from the revision content only (domain constructors).
    classify_candidate == CONFIRMED_BAEC → else not_confirmable
13. authorization = authority.authorize_grant(grant)   # HumanAuthorization(actor_label, issued_at,
                                                       #   CONFIRM_BAEC, baec_id)
14. record = create_confirmed_baec_record(candidate, baec_id=grant.baec_id,
                                          captured_at=…, confirmation=authorization)   # CURRENT
15. insert_confirmed_record(connection, record)        # the existing Repository insert logic, extracted
16. INSERT ai_proposal_confirmations
17. INSERT human_authorization_grant_consumptions (consumed_at = now)
18. COMMIT
```

- Any exception or refusal at steps 3–18 rolls back the whole transaction. No domain row, authorization row, link, or consumption remains.
- A failed attempt does not consume the grant. Retrying a transient failure (`SQLITE_BUSY`) is possible while the grant is ACTIVE. Deterministic refusals refuse again.
- There is no second transaction anywhere on the path.

**Structural changes this requires (minimum).**
- `Repository._insert_record` and its helpers are extracted unchanged into a narrow data-layer function, `insert_confirmed_record(connection, record)`. `Repository.save_confirmed_baec` keeps its behavior by calling it inside its own `_write()`. All existing repository tests must pass unchanged. The extraction exists so the execution store never holds a `Repository`, and the write composition therefore never reaches `persist_transition_*` (§16).
- Steps 3–17 are a data-layer method `GrantExecutionStore.execute(grant_id, now, build_record)`. `build_record` is an application callback that runs inside the transaction: step 12 uses the §9 and §11 validators, and steps 13–14 use `authority` and the domain factory. This keeps rule R1/R2 placement of `HumanAuthorization` construction. The store verifies that the returned record's `baec_id` and subject equal the grant before inserting.

**Result.**
- **Success:** `{baec_id, account_id, proposal_id, grant_id, classification: "CONFIRMED_BAEC"}`. No evidence text is echoed. The account state is unchanged and not reported as changed.
- **Failure:** an MCP tool error with exactly one closed code from the list above (plus `conflict` for a database refusal and `integrity_failure` for a verification failure on load). No source text, digest, or actor label appears.

**Repeated invocation after success.** Step 4 finds the consumption and returns `grant_already_consumed` with `isError: true`. Nothing is written and no BAEC is created. The original `baec_id` is not returned, so a repeat cannot be mistaken for a fresh confirmation.

**Failed-attempt audit: deferred.** Phase 7 writes no durable record of a refused execution. Writing one would need a second transaction or a write that survives rollback, which D12 forbids on this path, and it would add a write surface reachable from MCP. A refused attempt leaves the grant unconsumed, so it stays observable to the human surface. Durable attempt logging can be designed later as a separate, non-authoritative table written outside the authority transaction.

---

## 15. Duplicates and idempotency (D8, D18)

| Threat | Prevention (application check + database constraint) |
|---|---|
| Two BAECs from one lineage | Step 11, plus `ai_proposal_confirmations` UNIQUE(`artifact_id`) and UNIQUE(`proposal_id`). There is one proposal per artifact under mapping v1. A future mapping version would still be blocked at the artifact level. |
| Two executions of one grant | Step 4; consumptions PRIMARY KEY(`grant_id`); confirmations UNIQUE(`grant_id`). `BEGIN IMMEDIATE` serializes concurrent calls. |
| Two ACTIVE grants for one revision and action | The `_one_unretired` trigger, plus UNIQUE(`review_revision_id`, `action`, `issue_sequence`) and UNIQUE(`supersedes_grant_id`). No clock is involved. |
| Old grant used after an edit | The trigger-written REVISION_SUPERSEDED row → step 5 `grant_superseded` (before expiry); step 9 latest-revision check; the confirmations `_unretired` trigger. |
| Cross-account use | Composite foreign keys on `account_id`, plus the step 8 equality checks. The tool takes no account argument. |
| Confirming a different proposal than the one reviewed | The grant binds `proposal_id`, `proposal_digest`, `review_revision_id` and `review_content_digest` (step 8 and the foreign keys). The candidate is built only from the bound revision (step 12). |
| Global text or excerpt duplicates | **Not prevented, by design (D8).** A different artifact on the same interaction is a different lineage. |

---

## 16. MCP architecture (D13) and state-machine isolation (D3)

### 16.1 Read-only server: unchanged

The read-only server stays at `baec_app/mcp/`. It has the same 8 resources, 4 preview tools, 0 prompts and 0 write tools. Existing tests M1–M10, S1–S6 and the M9 counts stay byte-identical and must keep passing. Nothing in `baec_app/mcp/` imports any Phase 7 symbol.

### 16.2 Write composition: a new package `baec_app/mcp_write/`

It is a separate package so that the Phase 5 rules, which scan `baec_app/mcp/**`, stay untouched and its rules can be stricter.

| Module | Content |
|---|---|
| `composition.py` | `open_write_runtime(path)`. Opens one writable connection to an **existing** database: no create, `require_current_schema`, foreign keys on. It calls only `baec_app.application.build_grant_execution_facade(connection, clock=...)`. |
| `server.py` | `build_write_server(facade)`. It accepts only an exact `GrantExecutionFacade`, registers exactly one tool, and registers no resources and no prompts. |
| `tools.py` | `confirm_baec`. Its argument model is `{grant_id: str}` with `extra="forbid"`, `strict=True`, `hide_input_in_errors=True`, and pattern `^grant_[0-9a-f]{64}$`. Annotations: `readOnlyHint=False`, `destructiveHint=False`, `idempotentHint=False`, `openWorldHint=False`. Annotations are descriptive, not controls. |
| `__main__.py` | stdio entry point, mirroring Phase 5 S1–S6. |

`GrantExecutionFacade` has exactly one public method, `confirm_baec(grant_id)`. It holds a `GrantExecutionStore` and a clock. It holds no gate, no `Repository`, no review, issuance, or supersession service, no AI object, and no previews.

### 16.3 Static and runtime boundary rules (new `tests/test_mcp_write_boundary.py`)

| Rule | Assertion |
|---|---|
| W1 | Exactly one tool is registered, named `confirm_baec`, with exactly one parameter `grant_id: str`. The advertised input schema equals a pinned snapshot with `additionalProperties: false`. |
| W2 | Zero resources and zero prompts registered. |
| W3 | The only `baec_app` imports are `baec_app.application` package-level `build_grant_execution_facade` and `GrantExecutionFacade`. |
| W4 | No import of `baec_app.data`, `baec_app.domain`, `baec_app.ai`, `sqlite3`, or any model SDK. |
| W5 | No names or calls matching grant creation, re-issue, supersession or alteration (`issue`, `request_grant`, `supersede`, `save_revision`, `map_artifact`, `reject`), gate use (`gate`, `approve`, `redeem`, `HumanConfirmationGate`), command-side names (`build_command_facade`, `HumanCommandFacade`, `Repository`, `move_to_`, `persist_transition`, `record_dormancy`, `save_nonconfirmed`, `request_`), or AI (`ExtractionService`, `open_extraction_runtime`). |
| W6 | Literal static registration; async handlers; no `getattr` dispatch (mirrors M7/M8). |
| W7 (runtime) | An object-graph walk from the built write server reaches no instance of `Repository`, `HumanConfirmationGate`, `ProposalFacade`, `HumanCommandFacade`, `AccountStateService`, `DormancyJudgmentService`, any review, issuance or supersession service, any `AiProvenanceStore` writer, or any AI provider. It reaches no callable attribute named `persist_transition_*`, `record_dormancy_judgment`, `save_classification_record`, `issue_*`, `save_revision`, or `insert_grant`. |
| W8 | The read-only server and the M9 counts are unchanged (existing tests). |
| W9 | `GrantExecutionStore`'s only `INSERT` targets are `human_authorizations`, the BAEC record tables, `ai_proposal_confirmations` and `human_authorization_grant_consumptions`. This is checked statically over its SQL string literals. The supersession and revision triggers are never fired by the execution path, because it inserts no revision and no decision. |

**Prospective test edits** (made in the increment that needs them, never silently):
- The application-boundary rule "the MCP SDK may be imported only by `baec_app.mcp`" extends to `baec_app.mcp_write` (7G).
- The R5 authority-importer allow-list adds the grant issuance and execution services (7F-B).
- The Phase 4 origin test changes from "exactly two" to "exactly three, no AI_MODEL" (7C).

### 16.4 The write composition cannot invoke AI

Rules W4, W5 and W7 cover this. The execution path verifies lineage by digest equality against stored rows. It needs the §11 contract, which imports only pure `grounding` primitives, through `build_record` in the application layer. The MCP write package itself imports nothing from `baec_app.ai`.

### 16.5 State machine

- Confirming a BAEC causes no account-state transition. This is already true today: `save_confirmed_baec` inserts and never touches `accounts`. Phase 7 adds a test that `accounts.state` and `account_state_transitions` are byte-identical before and after a successful `confirm_baec`.
- **Entirely outside Phase 7:** Active Opportunity, Conditionally Dormant and No Plausible Path transitions; dormancy judgment creation; staleness or lifecycle changes; non-confirmed classification saves.

**Structural prevention.**
1. The MCP write process composes only `GrantExecutionFacade`, which has one method.
2. The execution store calls the extracted `insert_confirmed_record`, never a `Repository` (W7).
3. W9 limits the store's SQL insert targets.
4. The tool argument model cannot carry an account state, target state, or any domain value (W1).
5. `human_authorization_grants.action` is CHECKed to `CONFIRM_BAEC`, and confirmations require a `human_authorizations` row with `target_state IS NULL`.
6. The Streamlit review composition holds review and issuance services only. It does not compose `HumanCommandFacade` state commands (rule added in 7E).

---

## 17. Streamlit surface and AppTest (D2, D19, D20)

- **Dependency.** `streamlit==1.65.0`, pinned exactly in `requirements.txt`. This is an **IMPLEMENTATION dependency choice**, not a research or security claim. Installing it is a one-time package download needed before 7E runs. The suite itself makes no network requests.
- **Placement.** `baec_app/interfaces/streamlit/review_page.py`. It imports only the application package (R3: never `baec_app.data`). It renders `ProposalReview` and posts `ReviewDecisions` and gate actions. All logic lives in the application layer, which keeps its own Streamlit-free tests.
- **Rendering.** Interaction text, suggestions, and all AI text are rendered as plain text (`st.text` or equivalent; never markdown with HTML enabled). AI sections are visually and textually labeled AI_INFERENCE or AI SUGGESTION. Authoritative controls are separate widgets that start unset.
- **Grant hand-off.** After issuance, the page shows the grant_id and `expires_at`, labeled single-use, with a note that the human passes it to the MCP client.

**Mandatory AppTest coverage** (`streamlit.testing.v1.AppTest`, in the ordinary offline suite and the clean-export gate; each test builds an ephemeral test-only database per D15):

| # | Behavior |
|---|---|
| A1 | The page renders from persisted proposal and review state, in a fresh session and after reload. |
| A2 | AI suggestions are distinguishable from authoritative human values: separate labeled sections and different widgets. |
| A3 | Authoritative evidence provenance begins unset for every selection. |
| A4 | All four authoritative criterion decisions begin unset. |
| A5 | Rendering alone creates no grant and no revision, decision, or proposal row beyond those that exist. |
| A6 | An incomplete review cannot authorize: submit and authorize controls are refused, and no grant is stored. |
| A7 | Reject cannot authorize confirmation: after rejection, no grant can be issued, and any earlier grant is superseded. |
| A8 | An explicit accept/review action is required. No grant exists until the human uses the authorize action through the gate with the displayed digest. |
| A9 | A human edit is represented as a new immutable revision. Revision 1 is byte-identical afterwards. |
| A10 | Stale or superseded authorization cannot execute changed content. After an edit, the old grant passed to `GrantExecutionFacade.confirm_baec` (application level, no MCP) is refused with `grant_superseded`, and no BAEC is created. |
| A11 | Human-selected evidence that is not an exact substring is refused, and `buyer_exact_statement` is refused unless its selection is BUYER_FACT. |

AppTest proves UI behavior. It does not prove authenticated real-world human identity. Authentication remains out of scope.

---

## 18. Threat model → invariants and tests

| # | Threat | Severity | Design invariant | Planned tests (increment) |
|---|---|---|---|---|
| T1 | AI self-authorization | Critical | I2. Grants come only from `issue_confirmation_grant(approval)` on the human path. | W5 and W7; MCP write has no issuance path; a model-shaped tool call with extra fields is refused (7G). |
| T2 | Forged grant (fabricated row values) | Critical | Execution recomputes `grant_digest` and the revision digest, and checks the foreign-key bindings. | Tamper each binding column in a scratch copy → `grant_binding_mismatch` or `integrity_failure` (7F-B). |
| T3 | Model-created grant | Critical | There is no MCP path to grant creation. Grant ids are random and unlisted. | W5, W7, W9; no resource exposes grants (7G). |
| T4 | Replay | High | Single-use consumption; PRIMARY KEY; `BEGIN IMMEDIATE`. | Second call → `grant_already_consumed`, row counts unchanged; a concurrent double call succeeds exactly once (7G). |
| T5 | Expired grant | High | Step 7 plus the consumption window trigger. | Injected clocks at `expires_at` − 1 µs and exactly at `expires_at` (7F-B, 7G). |
| T6 | Grant extension | High | Append-only table and no update method. Re-issue creates a new row. | An UPDATE of `expires_at` is refused by the trigger; no extend symbol exists (7C, 7F-B). |
| T7 | Cross-account grant use | High | Composite foreign keys and step 8. | A cross-account fixture is refused at both the database and the application (7C, 7G). |
| T8 | Stale review revision / superseded grant | High | D18 trigger-written supersession; step 5 before step 7; step 9 as defense in depth. | An edit within the 15 minutes → `grant_superseded`; a rejection → `grant_superseded`; a missing supersession row (scratch copy) is still refused at step 9 (7C, 7F-B, AppTest A10). |
| T9 | Content changed after approval | High | Digest bound at gate approval and at the grant. The revision is immutable. | A displayed-digest mismatch is refused by the gate; a revision UPDATE is refused (7E, 7F-B). |
| T10 | Duplicate BAEC | High | §15. | A second grant on the same lineage after success → `lineage_already_confirmed` (7F-B, 7G). |
| T11 | AI speaker becomes buyer fact | Critical | §9.2. Provenance comes only from human selections. | Static check that the builder never reads `ai_attributed_speaker`; a mutation seeding speaker → provenance must fail (7E). |
| T12 | AI normalization replaces buyer evidence | Critical | §9.3 and §10. Normalization never flows to candidate fields; `baec_records.normalized_*` stays NULL. | Static check plus mutation; stored-row assertions (7E, 7G). |
| T13 | Prompt injection | High | Plain-text rendering. AI text has no control over decisions. The tool takes only an id. | Injection fixtures (instructions in the interaction and in explanations) render inertly and change no decision field (7E, AppTest). |
| T14 | MCP argument broadening | High | W1 strict model with one field. | Extra keys, a wrong type, or a non-matching pattern are refused without echoing input (7G). |
| T15 | Direct account-state change | High | §16.5 structural prevention. | Before/after equality of `accounts` and transitions; W7 (7G). |
| T16 | Partial transaction | Critical | §14: one transaction. | Fault injection at steps 15–17 and at COMMIT → zero new rows and the grant unconsumed (7F-B, 7G). |
| T17 | Audit omission | High | Trigger-enforced 1:1 link between `human_authorizations` and the confirmation, plus `verify_bridge_integrity`. | A link without a matching authorization is refused; an unpaired consumption is caught (7C, 7G). |
| T18 | Provenance loss | High | Foreign keys from proposal to artifact; snapshot digest; deleted live databases are never eligible. | Eligibility refusals E1–E8; the proposal digest is recomputed on every load (7D). |
| T19 | AI_DRAFT bypass via the Phase 4 in-memory path | Critical | §6.4. `build_request` refuses AI_DRAFT. | A test for each RequestKind (7C). |
| T20 | Rewritten or paraphrased evidence | Critical | §9.1. Selections must be exact substrings of the persisted interaction. | A one-character change, case change, whitespace change, or joined spans are each refused (7E). |
| T21 | Normalization changes a threshold | High | §11 contract. | One test per failure ending and per unresolved word (past, beyond, within, over); a retained expression that drops or paraphrases the word is refused; omitting the expression entirely passes (7F-A). |
| T22 | Fixture artifacts presented as real model output | Medium | D15. Fixtures are test-only and never committed as databases. | Static scan for committed database or seed files containing `ai_*` rows (7C). |

**Not fully solvable in a synthetic, unauthenticated, local prototype**

- **Real-world identity.** `actor_label` is self-asserted. No authentication, SSO, or signing exists. AppTest proves UI behavior only.
- **Local process with write access.** Any code or person able to open the SQLite file for writing can insert grant rows, run raw SQL, or drop triggers. Triggers and static rules are accident resistance and defense in depth, not a security boundary.
- **Same-process code.** Code running inside the Streamlit process can reach private names, including the Phase 4 gate.
- **Grant id disclosure.** The grant id passes through the human's MCP client and may sit in a chat log. Single use, supersession, and the 15-minute window limit the exposure; they do not remove it.
- **Automation bias and persuasive injected text.** The design keeps AI suggestions separate and requires explicit decisions. It cannot guarantee that a human evaluates them independently.
- **Clock manipulation** on the local machine.

---

## 19. Proposed Research Contract clarification — NOT APPLIED

`docs/RESEARCH_CONTRACT.md` is **not** modified by Phase 7B. The following wording is proposed for the owner's approval. All of it is **IMPLEMENTATION**. None of it is a manuscript or research claim.

**Proposed addition to RC-31 (IMPLEMENTATION):**
> For a BAEC confirmation proposed from a persisted AI artifact, application code must establish the human action through a persisted, single-use authorization grant created only by the human-interaction path and bound to the exact human-reviewed content revision, the AI lineage, the account, the subject, and the action. At most one such grant may be active for a given review revision and action. A grant expires 15 minutes after issuance; this interval is an implementation setting, not a research finding. Any later edit or rejection supersedes the grant through a persisted record, and a superseded grant is invalid even before it expires; superseded grants are retained unchanged. The grant records a self-asserted actor label and does not authenticate identity. An MCP or model call may execute a grant but can never create, alter, extend, or supersede one.

*Why:* RC-31 already requires proof of an actual human action beyond the domain value. This records the specific Phase 7 mechanism, including supersession, and its limits, so the grant is not later mistaken for authentication.

**Proposed addition to RC-32 (IMPLEMENTATION):**
> An AI attribution of who spoke is AI INFERENCE. It never establishes BUYER FACT or SELLER OBSERVATION. Excerpts proposed by AI are suggestions. Evidence for a BAEC confirmed from an AI proposal is selected by the human reviewer as exact verbatim text of the same source interaction, and the reviewer explicitly asserts the provenance of each selection.

*Why:* RC-32 forbids collapsing categories but does not say who selects evidence or assigns provenance when an AI artifact proposes excerpts with speaker labels. This is the D10 and D16 rule.

**Proposed addition to RC-33 (IMPLEMENTATION):**
> When a BAEC is confirmed from an AI proposal, the AI proposal and each human-reviewed revision are stored immutably and separately from source evidence. Source evidence is selected verbatim and is never invented, rewritten, or paraphrased. A buyer's exact statement requires its selected evidence to be asserted BUYER FACT. A human-reviewed normalization remains derived text, is kept with the AI value it started from, must not change any number, kind, currency, unit, or comparator it retains from the selected evidence, and never replaces or populates the buyer's exact statement or any evidence field. One AI artifact can yield at most one confirmed BAEC, and a new BAEC is never created as a substitute for an amendment.

*Why:* RC-33 covers AI normalization only in general terms. Phase 7 introduces human selection of evidence, human edits of AI text, and a new route to `buyer_exact_statement`, which need explicit rules. The final sentence prevents the "unrelated replacement BAEC" workaround that D9 forbids.

---

## 20. Non-goals (Phase 7)

The following are out of scope:

- live AI inference;
- a durable, public, or seeded demo database (deferred, D15);
- model qualification or a default model;
- prompt, validator, or `grounding.py` changes;
- any Phase 6 schema change;
- account-state transitions;
- dormancy judgments;
- staleness changes;
- non-confirmed classification saves from AI;
- evidence amendments;
- authentication;
- signed tokens;
- durable failed-attempt audit;
- read-side lineage resources;
- monitoring, signals, outreach or messaging;
- quality judgments and `buyer_role` (always None);
- evidence from any interaction other than the proposal's own.

---

## 21. Implementation map (7C–7H)

**Gates common to every increment:**
- the full `pytest` suite passes offline, including AppTest from 7E on, and the actual counts are reported;
- the increment's mutation set is fully caught (scratch copy for each mutation; the named test and the full suite must both fail);
- **clean export:** `git archive HEAD` into a temporary directory, and `pytest` passes there with no access to the outer repository, including AppTest;
- `ANTHROPIC_API_KEY` and `ANTHROPIC_AUTH_TOKEN` presence is checked, never printed;
- zero API calls;
- no live evaluation;
- no durable demo database.

**7F is split** into 7F-A and 7F-B, and **7F-A is implemented before 7E**:
- `save_revision` (7E) persists **immutable** revisions, and §11 must refuse an invalid normalization at the moment of storage. If 7E shipped first, either it would store unvalidated revisions forever, or validation would have to be retrofitted onto revisions that already exist.
- 7F-A is a pure module with no dependency on 7E. It needs only the 7C content format (selections and normalization) and the unchanged grounding primitives.
- 7F-B (grants, supersession, execution) needs 7E's revisions and decisions.

**Build order: 7C → 7D → 7F-A → 7E → 7F-B → 7G → 7H.**

### 7C: Persistence substrate + AI_DRAFT + immutable revisions

- **Objective.** Schema v7 with all seven tables, their triggers (including the supersession triggers) and indexes; stores for proposals, revisions and decisions; `ProposalOrigin.AI_DRAFT`; Phase 4 refusal of AI_DRAFT; the D15 fixture-scan rule.
- **Files.**
  - `baec_app/data/database.py`
  - new `baec_app/data/proposal_bridge.py` (`ProposalBridgeStore`: insert and get for proposals, revisions, decisions; grant-status reads; `verify_bridge_integrity`)
  - `baec_app/application/proposals.py`
  - `baec_app/application/requests.py`
  - `tests/test_proposal_bridge_schema.py`, `tests/test_proposal_bridge_store.py`
  - Phase 4 origin test edit
- **Schema.** v6 → v7 (rebuild-from-seed policy).
- **New public symbols.** `SCHEMA_VERSION = 7`, `ProposalBridgeStore`, `AiProposalRecord`, `ReviewRevisionRecord`, `ReviewDecisionRecord`, `GrantStatus` (ACTIVE, EXPIRED, SUPERSEDED, CONSUMED; derived), `ProposalOrigin.AI_DRAFT`.
- **Invariants.** I4 append-only; cross-account foreign keys; one proposal per (artifact, mapping); revision sequence; REJECTED is terminal; supersession rows are written by triggers; at most one unretired grant per (revision, action); Phase 4 cannot carry AI_DRAFT.
- **Tests.**
  - every CHECK, FK, UNIQUE, partial index and trigger, accepted and refused;
  - UPDATE, DELETE and REPLACE refused on every table;
  - supersession on a new revision or a rejection, with hand-built grant rows;
  - re-issue chain rules;
  - link and consumption constraints at the database level.
- **Mutations (≥ 24), for example:**
  - drop the no-update trigger on revisions;
  - drop the `_supersede_grants` trigger;
  - drop `_one_unretired`;
  - drop UNIQUE(`artifact_id`) on confirmations;
  - remove the cross-account FK;
  - allow a revision after REJECTED;
  - Phase 4 accepts AI_DRAFT.
- **Non-goals.** No mapper, no UI, no issuance or execution code.
- **Depends on.** 7B approval.

### 7D: Deterministic mapping

- **Objective.** `baec-ai-proposal-mapping/v1` and eligibility E1–E8, with E3 as a compatibility allowlist.
- **Files.** New `baec_app/application/ai_proposal_mapping.py`; `tests/test_ai_proposal_mapping.py`; ephemeral fixture artifacts via `FakeProvider` and the existing builders (D15).
- **Schema.** None.
- **New public symbols.** `MAPPING_VERSION`, `PROPOSAL_CONTENT_VERSION`, `map_artifact_to_proposal_content`, `ArtifactNotEligible` (closed codes).
- **Invariants.** Deterministic, byte-identical output; every AI value labeled AI_INFERENCE or suggestion; no selection, provenance, finding, origin, mode, statement or stringency produced; fail closed.
- **Tests.** E1–E8, one refusal each; a golden content digest; a static import rule.
- **Mutations (≥ 15), for example:**
  - map `supported` to MET;
  - copy the speaker into a provenance field;
  - accept validation `/v1`;
  - accept `no_clear_baec_language`;
  - skip re-validation;
  - reorder hypotheses.
- **Non-goals.** No review, no grant.
- **Depends on.** 7C.

### 7F-A: Human-normalization validation (built before 7E)

- **Objective.** `baec-human-normalization-validation/v1` (§11).
- **Files.** New `baec_app/application/human_normalization.py`; `tests/test_human_normalization.py`; an application-boundary rule for its narrow `grounding` import.
- **Schema.** None.
- **New public symbols.** `HUMAN_NORMALIZATION_VALIDATION_VERSION`, `validate_final_normalization(final_values, evidence_texts) -> tuple[str, ...]`, `HUMAN_NORMALIZATION_FAILURE_CODES`.
- **Invariants.** Deterministic; per-selection tokenization; unchanged `grounding.py` (digest pin passes); no AI validation identity or codes.
- **Tests.**
  - each ending for each field;
  - currency versus non-currency kind changes;
  - each of past, beyond, within and over retained, dropped, and paraphrased;
  - expression omitted entirely;
  - cross-selection non-concatenation;
  - a KEEP_AI value that was grounded only outside the selected evidence is refused.
- **Mutations (≥ 15), for example:**
  - concatenate selections;
  - treat `unresolved:over` as GREATER_THAN;
  - skip the currency distinction;
  - validate EDITED only;
  - import `validate_extraction`;
  - drop the unit check.
- **Non-goals.** No storage, no UI.
- **Depends on.** 7C (content format).

### 7E: Review application layer + Streamlit 1.65.0 + AppTest

- **Objective.** `ProposalReview`, `ReviewDecisions`, `map_artifact`, `save_revision` (with §9 and §11), rejection through the gate, and the Streamlit page with AppTest A1–A9 and A11. A10 is completed in 7F-B.
- **Files.**
  - new `baec_app/application/proposal_review.py`
  - `baec_app/application/composition.py` (`build_review_facade`)
  - `baec_app/application/requests.py` and `canonical.py` (new RequestKinds and field tables)
  - new `baec_app/interfaces/streamlit/review_page.py`
  - `requirements.txt` (`streamlit==1.65.0`)
  - `tests/test_proposal_review.py` (no Streamlit)
  - `tests/test_streamlit_review_page.py` (AppTest)
- **Schema.** None.
- **New public symbols.** `ProposalReview`, `ReviewDecisions`, `EvidenceSelection`, `NormalizationDisposition`, `ReviewFacade`, `build_review_facade`, `RequestKind.REJECT_AI_PROPOSAL`.
- **Invariants.** I1, I9; D6, D10, D16; plain-text rendering; R3; the review facade composes no state command.
- **Tests.**
  - every undecided field refused;
  - verbatim selection from any span, and its refusals;
  - provenance only from selections;
  - BUYER_FACT required for `buyer_exact_statement`;
  - stringency substring rules;
  - dispositions retained;
  - revision chain and digests;
  - injection fixtures;
  - AppTest A1–A9 and A11.
- **Mutations (≥ 22), for example:**
  - default a finding from `ai_status`;
  - default provenance from the speaker;
  - preselect AI suggestions;
  - accept a trimmed or case-folded selection;
  - allow `buyer_exact_statement` on SELLER_OBSERVATION;
  - default `KEEP_AI`;
  - render as markdown;
  - skip §11 at save.
- **Non-goals.** No grant, no execution, no MCP.
- **Depends on.** 7C, 7D, 7F-A.

### 7F-B: Persisted grant, supersession, and the atomic execution service

- **Objective.** `request_grant` and `issue_confirmation_grant(approval)` (re-issue via `supersedes_grant_id`); grant status reads; `GrantExecutionStore.execute` with `insert_confirmed_record` extracted; `authority.authorize_grant`; the Streamlit authorize action and grant display; AppTest A10.
- **Files.**
  - `baec_app/application/proposal_review.py` and a new `baec_app/application/grant_execution.py`
  - `baec_app/application/authority.py`
  - `baec_app/data/repository.py` (extraction only, no behavior change)
  - `baec_app/data/proposal_bridge.py` (`GrantExecutionStore`)
  - the review page
  - `tests/test_authorization_grants.py`, `tests/test_grant_execution.py`
  - R5 allow-list edit
- **Schema.** None (tables exist from 7C).
- **New public symbols.** `RequestKind.ISSUE_CONFIRMATION_GRANT`, `GrantView`, `GrantExecutionFacade`, `build_grant_execution_facade`, `GrantRefused` (closed codes), `GRANT_TTL_SECONDS = 900`.
- **Invariants.** I2, I5, I6; D5, D12, D14, D18; the `human_authorizations` row derived only from the grant.
- **Tests.**
  - every step 3–17 refusal;
  - a superseded grant refused before expiry;
  - re-issue only after expiry;
  - atomicity via fault injection;
  - expiry boundaries;
  - the authorization row equals the grant;
  - the BAEC is CURRENT;
  - account state unchanged;
  - the existing repository suite unchanged after the extraction;
  - AppTest A10.
- **Mutations (≥ 28), for example:**
  - consume before validating;
  - check expiry before supersession;
  - skip the supersession check;
  - `<=` at the expiry boundary;
  - skip the digest recompute;
  - authorized_at = now;
  - a second transaction for the consumption;
  - allow re-issue while ACTIVE;
  - skip re-running §11 at execution.
- **Non-goals.** No MCP.
- **Depends on.** 7E.

### 7G: Separate one-tool MCP write composition

- **Objective.** The `baec_app/mcp_write/` package (§16.2) and its stdio entry point.
- **Files.** New `baec_app/mcp_write/{__init__,composition,server,tools,__main__}.py`; `tests/test_mcp_write_boundary.py` (W1–W9); `tests/test_mcp_write_tool.py`; `tests/test_mcp_write_stdio.py`; application-boundary MCP SDK allow-list edit; `docs/MCP_USAGE.md` (append-only section).
- **Schema.** None.
- **New public symbols.** `open_write_runtime`, `build_write_server`.
- **Invariants.** I3, I7; D13; W1–W9; the read-only server's counts unchanged.
- **Tests.**
  - end-to-end through the in-memory MCP client;
  - a repeat call gives `grant_already_consumed`;
  - a superseded grant gives `grant_superseded`;
  - argument broadening refused;
  - a concurrent double call;
  - the stdio smoke test;
  - the object-graph walk;
  - error results leak no content.
- **Mutations (≥ 15), for example:**
  - add an `account_id` parameter;
  - register a resource;
  - import `baec_app.ai`;
  - hold a `Repository`;
  - expose issuance;
  - return the original `baec_id` on a repeat;
  - set `extra="allow"`.
- **Non-goals.** No other tools, prompts, or resources on the write server.
- **Depends on.** 7F-B.

### 7H: Adversarial and end-to-end verification + closeout

- **Objective.** An adversarial suite over T1–T22; a synthetic end-to-end scenario on an ephemeral test-only database (fixture artifact → map → review → grant → edit → supersession → new grant → MCP confirm → repeat refused); traceability and closeout documents; the RC clarification only if the owner approves it separately.
- **Files.** `tests/test_phase7_adversarial.py`, `tests/test_phase7_end_to_end.py`, `docs/PHASE7_TRACEABILITY.md`, `docs/PHASE7_FINAL_VERIFICATION.md`.
- **Schema.** None.
- **Invariants.** Everything above. Phase 5 and Phase 6 artifacts are byte-identical where locked.
- **Tests.** The threat table end to end; a re-run of every 7C–7G mutation set at the final commit.
- **Non-goals.** No live AI, no demo database, no new features.
- **Depends on.** 7C–7G.

---

## 22. Open implementation questions

These are narrow, and none reopens D1–D20.

None remain open.

**Resolved at 7B approval: Streamlit installation.** `streamlit==1.65.0` is recorded as a future dependency decision.
- A one-time network installation is authorized **only when Phase 7E begins**. It is not installed in 7B, 7C, 7D, or 7F-A.
- After installation in 7E, Streamlit AppTest becomes part of the ordinary offline suite and the clean-export gates.

**Locked build order:** 7C → 7D → 7F-A → 7E → 7F-B → 7G → 7H.

---

## Phase 7D implementation clarification

Recorded during Phase 7D. Approved by the project owner. It is an IMPLEMENTATION clarification; it adds no research rule and changes no locked Phase 6 design text.

**A9 remains in force, with exactly one verification-only import exception.**
- Phase 6 rule A9 (`domain`, `data`, `application`, `mcp`, and `scripts` never import `baec_app.ai`) still applies.
- There is one named exception: `baec_app.application.ai_proposal_mapping` may import exactly `baec_app.ai.verification`. No other application module may import the AI package, and the mapper may not import any other AI module (`contracts`, `canonical`, `validation`, `grounding`, `service`, `composition`, `provider`, `anthropic_provider`).
- `baec_app/ai/verification.py` is a pure façade. It strictly re-parses a persisted result as the locked `baec-extraction-output/v1` contract, checks its `baec-canonical-json/v1` form, and re-runs the locked `validate_extraction`. It reads no data layer, provider, service, composition, network, environment, clock, or randomness.
- **Why the verifier was not duplicated.** E4 requires the stored result to be re-checked against the locked contract and validator. A second implementation in the application layer would be a weaker, drifting copy of the Phase 6 rules. Re-using them through one pure façade keeps a single source of truth.
- The future 7F-A grounding exception is not part of this clarification.

**Proposal snapshot provenance.** The persisted `artifact_id` is the authoritative anchor for Phase 6 provenance. The §6.3 snapshot's `artifact` block holds only the stable source reference, `{artifact_id, artifact_digest}`. Requested and returned model, prompt version and digest, validator version, request digest, and output-schema version are reconstructed through the immutable artifact → run lineage and are not copied into the snapshot.

**Store surface.** `ProposalBridgeStore` gains exactly one read-only method, `confirmed_baec_for_artifact(artifact_id)`, which eligibility rule E8 requires. It writes nothing.

**Live-evaluation exclusion (§6.1) is structural.** Eligibility is evaluated only for an artifact reachable through the product database's durable provenance relationships. Temporary live-evaluation databases are not the product database and are deleted after evaluation. No heuristic field, path, or process history is inspected. Limitation: this structure does not cryptographically prove that rows were never copied by hand from another SQLite database.

---

## Phase 7F-B implementation clarification

Recorded during Phase 7F-B and approved by the project owner. This is IMPLEMENTATION architecture, not a manuscript finding; it changes no research rule and rewrites no earlier text of this design.

- **Issuance path.** Authorization is an explicit Streamlit action ("Authorize BAEC confirmation") → `ProposalAuthorizationService.authorize_confirmation` → one persisted grant. It does not route through the Phase 4 in-memory approval gate; the design's `request_grant` and gate-approval steps (§8.3, §12.5) are not used. The 7C refusal of AI_DRAFT on the Phase 4 path is unchanged.
- **ACCEPTED decision.** Phase 7E already persists the ACCEPTED review decision together with its revision. Issuance reuses that decision; it does not insert one.
- **Transaction ownership.** The application-layer `ConfirmationExecutionService` owns the single `BEGIN IMMEDIATE` execution transaction and calls narrow data-layer pieces inside it (`insert_confirmed_record`, `AuthorizationGrantStore.record_confirmation`), instead of a `GrantExecutionStore` callback (§14). The atomic scope is unchanged: the confirmed BAEC, its HumanAuthorization, the confirmation link, and the grant consumption commit together or not at all, and a failed execution leaves the grant unconsumed.
- **Audit chain.** `human_authorizations` is schema-unchanged. The exact reviewed-content digest stays in the immutable grant and is reconstructable, one to one, through HumanAuthorization → `ai_proposal_confirmations` → `human_authorization_grants` → proposal → review revision → reviewed-content digest, enforced by the schema's UNIQUE keys, composite foreign keys, and the authorization-binding trigger.
- **Authority construction.** `authority.authorize_grant` is the only construction path from a persisted grant to a HumanAuthorization. It refuses on its own any grant whose action is not `CONFIRM_BAEC` or whose binding digest no longer covers its binding. Expiry, supersession, consumption, latest revision, and classification remain executor checks.
- **Status precedence.** consumed > superseded > expired > active. A grant already exercised stays historically CONSUMED; for an unconsumed grant, SUPERSEDED wins over EXPIRED.
- **Timestamps.** `HumanAuthorization.authorized_at` is the grant's issuance time, when the human authorized; execution consumes that authority later.
- **Subject.** `baec_id` is preallocated at issuance and bound into the grant, so the exact future domain subject is part of what the human authorized. A replacement grant after expiry receives its own `baec_id`; the expired grant's subject remains historical grant material and creates no BAEC.
- **No state change.** Confirmation creates a confirmed BAEC record only. No account-state transition, dormancy judgment, or staleness change accompanies it.
- **Identity.** The actor label is self-asserted; there is no authentication.

---

## Phase 7G implementation clarification

Recorded during Phase 7G. This is IMPLEMENTATION architecture, not a manuscript finding; it changes no research rule and rewrites no earlier text of this design.

- **Phase 5 is unchanged.** The MCP Core in `baec_app/mcp/` keeps its 8 resources, 4 read-only preview tools, 0 prompts, its `mode=ro` connection, and `PRAGMA query_only`. No Phase 5 file changed; it imports nothing from Phase 7G.
- **Separate write composition.** `baec_app/mcp_write/` is its own package with its own stdio entry point (`python -m baec_app.mcp_write --database PATH`). It opens one writable connection to an existing current-schema database (`mode=rw`, foreign keys on). It never creates, seeds, or migrates a database. The production clock is `SystemClock` (aware UTC), composed in the entry point; tests inject fixed clocks.
- **Writable opening is isolated to `mcp_write/composition.py`.** No existing opener has the semantics required here: existing file only, writable, no create, no migrate, current schema required. So `composition.py`, and no other `mcp_write` module, imports `sqlite3`, `check_sqlite_version`, and `require_current_schema`.
- **Exactly one tool.** The server registers `confirm_baec(grant_id)` and no resources or prompts. The closed argument model accepts only `grant_id` matching `^grant_[0-9a-f]{64}$`. Any other field is rejected at the input schema, not ignored.
- **MCP cannot issue grants.** The package does not import or name `ProposalAuthorizationService`, `authorize_confirmation`, `add_grant`, `GrantRecord`, the review service, the mapper, or `authorize_grant`. Grants are issued only by the explicit human action on the review page. That page still cannot execute a grant.
- **The executor holds only an execution grant capability.** Grant persistence has two capabilities.
  - `GrantExecutionStore` has exactly `get_grant`, `lifecycle`, and `record_confirmation`. It cannot insert, re-issue, or supersede a grant.
  - `AuthorizationGrantStore` extends it with `add_grant`, `grants_for_revision`, and `grants_for_proposal`. Only `ProposalAuthorizationService` holds it.

  `ConfirmationExecutionService` holds a `GrantExecutionStore`, so the executor composed into the MCP server retains no issuance capability. A capability walk from the composed runtime finds no `add_grant`, `authorize_confirmation`, `GrantRecord.issue`, or `ProposalAuthorizationService`. A negative-control test shows the walk fails if the issuance-capable store is restored. Grant semantics and issuance behavior are unchanged.
- **No authoritative content from MCP.** The tool supplies no actor, subject, evidence, statement, criteria, stringency, normalization, or state. Everything authoritative comes from the persisted grant and the human-reviewed revision it binds.
- **The executor stays the authority boundary.** The tool calls `ConfirmationExecutionService.execute(grant_id)` exactly once and translates the outcome; it never retries. The executor revalidates the grant, lineage, review, normalization, and classification inside one transaction.
- **Results.** Success is structured metadata only: `{status: "confirmed", baec_id, proposal_id, review_revision_id, grant_id}`. A refusal is an MCP tool error (`isError`). Its text, after the SDK's `Error executing tool confirm_baec: ` prefix, is the canonical JSON `{"code", "grant_id", "status": "refused"}`, carrying the executor's closed code unchanged. Any other failure is the SDK's generic tool error, with details on the server's stderr only.
- **Differences from §16.2–§16.3 as written.** These follow the implemented 7F-B executor.
  - There is no `GrantExecutionFacade`; the tool calls `ConfirmationExecutionService` directly.
  - The tool is defined in `server.py`; there is no separate `tools.py`.
  - The composition imports `sqlite3` and `baec_app.data.database` (`check_sqlite_version`, `require_current_schema`) for construction only, as approved above.
  - The success result carries `review_revision_id` and `status`, not `account_id` and `classification`.
  - `idempotentHint` is intentionally left unset, instead of `false`. A repeat call after consumption is safely refused (`grant_already_consumed`, nothing written); it is not represented as an idempotent repeat-success operation. The other annotations are `readOnlyHint=false`, `destructiveHint=false`, and `openWorldHint=false`.
  - A malformed grant id fails MCP input-schema validation. It never reaches the executor's `grant_id_invalid` refusal.
- **Transitive pure-AI imports.** Pure deterministic Phase 6 verification and grounding modules (`ai.verification`, `ai.grounding`, and their pure helpers) may load transitively through the locked application revalidation path; this is approved. `mcp_write` imports no `baec_app.ai` module directly and invokes no model. Fresh-interpreter tests show that the write composition loads no provider module, no `anthropic`, no Streamlit, no `baec_app.interfaces`, and no Phase 5 `baec_app.mcp`.
- **Not a sandbox.** The capability claim covers what the composed MCP server holds. Python code with import access, or with local access to the process or database, could bypass these conventions. The model-visible surface is the one tool.
