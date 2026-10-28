# BAEC Engine 2: Technical and Research Brief

**Project:** BAEC Trigger Intelligence  
**Engine:** BAEC Engine 2: Monitoring + Correspondence  
**Status:** Version 1.0 released research prototype. Synthetic data only. The public Streamlit demo is a post-v1.0 presentation layer.  
**Release tag:** `baec-engine2-v1.0`  
**Release commit:** `b820778108f7c9432689755c4b255f9082d24350`  
**Public-demo commit:** `df77f38cbb64729b5e1579af136431d448dd9bc3`  
**Public demo:** https://baec-engine2.streamlit.app  
**Public PDF brief:** `docs/engine2/BAEC_Engine_2_Research_and_Technical_Brief.pdf`  
**Governing sources:** the manuscript, then `docs/RESEARCH_CONTRACT.md`, then the locked Stage A contract (`docs/engine2/ENGINE2_MONITORING_CORRESPONDENCE_DOMAIN_CONTRACT.md`), then code. If this brief conflicts with any of them, they win.

> **Correspondence narrows human attention. It does not establish that the buyer is currently evaluating, intends to purchase, or that an opportunity exists.**

---

## 1. Purpose

Two engines answer two different questions.

- **Engine 1:** What did the buyer say would make evaluation worthwhile, and can that condition be verified and confirmed?
- **Engine 2:** Has new evidence appeared that may correspond to that verified buyer-defined condition?

Engine 2 begins only after Engine 1 has confirmed a BAEC. It watches human-authorized sources for evidence, preserves the evidence record, prepares possible correspondence for a person, and records that person's judgment. It does not rank accounts, predict purchases, create opportunities, contact buyers, or change account state.

Every Engine 2 concept is an implementation of a managerial implication proposed in an unpublished conceptual working paper. Nothing here is presented as an empirical research finding.

## 2. Core architecture

| Concept | What it is | What it is not |
|---|---|---|
| Confirmed BAEC handoff | A read-only, point-in-time copy of one confirmed Engine 1 BAEC (Stage K) | A new BAEC, a re-confirmation, or a live link to Engine 1 |
| Monitoring Plan | A human-authorized plan for one BAEC: required and not-applicable dimensions, authorized sources, plan facts and rules. Each version is immutable | Evidence that the condition occurred |
| Activation | The plan becomes active only when Engine 1 reports the BAEC `CURRENT` | A judgment of currentness by Engine 2 |
| Authorized Source | A source a human approved for one plan, with its basis and limitations | Permission to contact anyone |
| Observation | Exact captured source text with provenance and a content hash | An interpretation; it is never edited |
| Supersession and retraction | A later Observation that corrects or withdraws an earlier one, on a recorded basis | Deletion: the earlier Observation stays in the record, no longer usable |
| Derived Measurement | A value computed with exact decimal arithmetic from Observations | A free-form inference or an editable number |
| Signal Candidate | Observations selected as possibly relevant | A finding |
| Correspondence Candidate | A hypothesis that signals may bear on the BAEC's condition and warrant human review | An opportunity, intent signal, or lead |
| Human Correspondence Review | A person's finding on each of 11 dimensions, four cross-cutting checks, sufficiency, and a BAEC revalidation flag | An account-state change or authorization to act |
| Correspondence classification | A deterministic rule that derives the outcome from the human review | Something a caller can choose |
| Advisory AI (optional) | A validated AI proposal, kept in a separate audit store | A human finding or an outcome |
| Persistence | Two separate append-only SQLite stores: `BAEC_ENGINE_2` (v1, 30 tables) and `BAEC_ENGINE_2_AI` (v1, 7 tables) | A place where records are edited or repaired |
| Operator workflow | A Streamlit research prototype over the review application layer (Stages I and J) | A production interface |
| Public demo | A separate post-v1.0 Streamlit presentation over the released scenario and Stage I review boundary | A replacement for the internal operator workflow or the v1.0 release |

## 3. Authority model

| Record | Who is authoritative | Authority it carries |
|---|---|---|
| Engine 1 buyer evidence | Engine 1 | Exact buyer text with provenance |
| Engine 1 confirmed BAEC | Engine 1, through a human confirmation path | That the condition meets the BAEC criteria, as confirmed by a human |
| Engine 2 Observation | Engine 2 capture under an active plan | That exact text was captured from an authorized source |
| Signal Candidate | Engine 2 selection | None beyond "possibly relevant" |
| Correspondence Candidate | Engine 2 | None beyond "warrants human review" |
| AI advisory proposal | None | Advisory only, origin `AI_INFERENCE` |
| Human Correspondence Review | The human reviewer | The human's findings on this evidence |
| Correspondence outcome | The locked classifier, from the human review | A narrow correspondence result |

**AI output is advisory and cannot create a human finding or authoritative correspondence outcome.**

## 4. Outcome vocabulary

The locked outcomes are:

- `NO_CORRESPONDENCE`: at least one required dimension was found contradicted.
- `POSSIBLE_CORRESPONDENCE`: at least one required dimension remains unresolved, the evidence was not judged sufficient, or BAEC revalidation is required.
- `HUMAN_VERIFIED_CORRESPONDENCE`: every required dimension was found supported, the evidence was judged sufficient, and no BAEC revalidation was flagged.

`BAEC_REVALIDATION_REQUIRED` is a flag, not an outcome; it blocks `HUMAN_VERIFIED_CORRESPONDENCE`.

None of these outcomes measures buyer intent, likelihood, readiness, or opportunity status, and none ranks an account.

## 5. Critical boundary

Correspondence narrows human attention. It does not establish that the buyer is currently evaluating, intends to purchase, or that an opportunity exists.

- A confirmed BAEC does not automatically create an active opportunity.
- A `HUMAN_VERIFIED_CORRESPONDENCE` outcome does not automatically create an active opportunity.
- No Engine 2 operation changes account state, contacts a buyer, writes to a CRM, or starts seller action.
- The public demo stops at a correspondence result and preserves the same boundary.

## 6. Engine 1 to Engine 2 handoff (Stage K)

- **Read-only.** The handoff reads Engine 1 only through Engine 1's query-only connection and locked repository. It writes nothing, consumes no grant, and reruns no confirmation.
- **Eligibility.** A persisted `CONFIRMED_BAEC` record carrying its human confirmation is required. An AI draft, accepted review, or unexecuted grant cannot cross.
- **Exact buyer statement.** The Engine 2 condition reference is the buyer's exact statement, never a paraphrase.
- **Threshold and timing.** A stated number with its unit becomes the plan threshold exactly. A number without a unit is refused rather than given an invented unit. Timing is the buyer's own timing text.
- **Currentness.** The stored Engine 1 staleness status is passed to Engine 2's locked activation rule, which activates only a `CURRENT` BAEC.
- **Point in time.** The handoff is a snapshot. There is no synchronization; a later Engine 1 change is seen only by a new read.
- **Not carried.** AI-derived normalized wording and the Phase 7 human-reviewed normalized wording in Engine 1's proposal-bridge review store do not cross.

## 7. AI boundary (Stages F, G, and H-A)

- **Stage F.** A model receives a curated, canonical input built from one review context: the buyer's exact statement, the plan, and the evidence with its usability. Its proposal vocabulary is limited to `PROPOSED_SUPPORTED`, `PROPOSED_CONTRADICTED`, `PROPOSED_UNRESOLVED`, and `PROPOSED_NOT_APPLICABLE`.
- **Stage G.** Inputs, attempts, exact responses, validations, and accepted proposals are stored in the separate `BAEC_ENGINE_2_AI` store, never in the authoritative store.
- **Stage I.** An accepted proposal can be shown only on explicit request and only beside the human workflow, never inside the human findings.
- **Stage H-A.** A controlled live harness exists behind explicit opt-in gates.
- **Stage H-B has not been executed.** It remains paused and is not part of Engine 2 v1.0 or the public demo.
- **Public demo.** No live AI call, provider credential, model selection, or AI suggestion is used.

## 8. Human review workflow

The review application layer (Stage I) creates a deterministic review packet carrying a context digest. A submitted review is refused if the stored context changed after the packet was opened.

The human reviewer answers 11 correspondence dimensions:

1. Entity match
2. Relationship match
3. Source relevance
4. Event or condition type
5. Threshold match
6. Comparator match
7. Numeric magnitude
8. Unit match
9. Timing match
10. Renewal or effective-date match
11. Causal or contextual relevance

The reviewer also records four cross-cutting checks:

- Freshness
- Contradictory evidence
- Missing evidence
- Provenance quality

Finally, the reviewer records evidence sufficiency and whether the original BAEC requires revalidation. No control submits an outcome; the locked classifier derives it.

## 9. Public demo scenario

The public demo at https://baec-engine2.streamlit.app uses a fixed synthetic Alder Ridge Clinics / Cobalt Imaging Supply scenario.

The buyer previously said:

> “If Cobalt raises the price of our imaging service contract by more than 8% at the July renewal, we would put the contract out for review.”

The decision context is:

- **Buyer threshold:** more than 8%
- **Relevant timing:** July renewal
- **Initial measurement:** 12.00%, derived from the first renewal notice
- **Correction:** the supplier later corrected the contract-specific notice
- **Current usable measurement:** 9.00%
- **Historical treatment:** 12.00% remains in the ledger as invalidated and is not used
- **Unauthorized forum item:** refused and never becomes evidence

The public page presents the sequence:

`Buyer-defined condition -> Authorized monitoring -> New evidence -> Possible correspondence -> Human review -> correspondence result`

Each visitor receives isolated in-memory state. Restarting the demo clears the session. No database file is written to disk.

## 10. Successful public-demo walkthrough

The public PDF brief contains the formatted walkthrough. The successful path is reproduced below for technical reference.

### 10.1 Landing through evidence

1. Open https://baec-engine2.streamlit.app and click **Start Demo**.
2. Step 1: verify the confirmed buyer statement, **More than 8%**, and **July renewal**.
3. Step 2: review the human-authorized monitoring plan and authorized sources.
4. Step 3: verify **Current usable measurement: 9.00%** and the correction sequence **12.00% initially reported -> corrected -> 9.00% current**.
5. Click **Review Possible Correspondence**.

### 10.2 Step 4A - Supplier and event

| Field | Successful selection |
|---|---|
| Entity match | Supported |
| Relationship match | Not applicable |
| Source relevance | Supported |
| Event or condition type | Supported |
| Cited evidence | `OBS-DEMO-02` |

Use a short reviewer reason explaining that the corrected renewal notice applies to the contract and comes from the authorized supplier-notice source.

### 10.3 Step 4B - Buyer's threshold

| Field | Successful selection |
|---|---|
| Threshold match | Supported |
| Comparator match | Supported |
| Numeric magnitude | Supported |
| Unit match | Supported |
| Cited evidence | `DM-DEMO-02` |

The current usable measurement is 9.00%, which is strictly greater than the buyer's 8% threshold.

### 10.4 Step 4C - Timing and context

| Field | Successful selection |
|---|---|
| Timing match | Not applicable |
| Renewal or effective-date match | Supported |
| Causal or contextual relevance | Not applicable |
| Cited evidence | `OBS-DEMO-02` |

The corrected notice takes effect on 1 July 2027, matching the July renewal context.

**Important:** in this locked plan, `TIMING_MATCH` is not required. Marking it Supported is inconsistent with the plan and is refused rather than classified.

### 10.5 Step 4D - Evidence quality and conclusion

| Field | Successful selection |
|---|---|
| Freshness | No concern |
| Contradictory evidence | No concern |
| Missing evidence | No concern |
| Provenance quality | No concern |
| Evidence sufficient | Yes, sufficient |
| BAEC revalidation | No |
| Reviewer alias | `BP` or another non-identifying alias |

Submit the human review.

Expected public headline: **Verified correspondence**  
Expected technical outcome: `HUMAN_VERIFIED_CORRESPONDENCE`

The result remains narrow: it does not establish current buyer evaluation, purchase intent, an opportunity, outreach permission, or an account-state change.

## 11. Reproducing the other outcomes

### 11.1 `POSSIBLE_CORRESPONDENCE`

Starting from the successful walkthrough, any of the following is sufficient to prevent verified correspondence:

- set evidence sufficiency to **Cannot tell**;
- mark a required dimension such as Threshold match or Unit match **Unresolved**; or
- require BAEC revalidation and provide the reason.

### 11.2 `NO_CORRESPONDENCE`

Starting from the successful walkthrough, mark a required dimension such as **Entity match = Contradicted**.

Under the locked rule, any contradicted required dimension derives `NO_CORRESPONDENCE`.

### 11.3 Refusal is not a fourth outcome

A review inconsistent with the Monitoring Plan is refused rather than classified. For example, marking `TIMING_MATCH` Supported in this scenario conflicts with the plan's not-applicable setting. The visitor's answers remain available for correction.

## 12. Verification and release state

Engine 2 v1.0 is frozen at:

- tag `baec-engine2-v1.0`
- commit `b820778108f7c9432689755c4b255f9082d24350`

The public demo is a separate post-v1.0 presentation layer introduced at commit:

- `df77f38cbb64729b5e1579af136431d448dd9bc3`

At the v1.0 release snapshot:

- Stage I: 112 tests
- Stage J: 45 tests
- Stage K: 32 tests
- Stage L: 17 tests
- all Engine 2: 945 tests
- full repository: 6,763 passed, 2 opt-in live tests deselected

After the public-demo commit:

- public-demo tests: 34 passed
- all Engine 2: 979 passed
- full repository: 6,797 passed, 2 opt-in live tests deselected, 0 skipped
- synthetic corpus distribution remained 20 `NO_CORRESPONDENCE`, 32 `POSSIBLE_CORRESPONDENCE`, 9 `HUMAN_VERIFIED_CORRESPONDENCE`, and 3 blocked before classification

These counts are software verification on synthetic data, not empirical validation.

The historical files `ENGINE2_RELEASE_CANDIDATE_RECORD.md` and `ENGINE2_RELEASE_CANDIDATE_REPRODUCIBILITY.json` intentionally preserve the release-candidate state observed when they were created and should be read as historical records.

## 13. Reproduction commands

From the repository root:

```bash
python3 scripts/engine2_release_demo.py
```

Runs the deterministic Engine 1 -> Engine 2 release demonstration and prints its canonical summary.

```bash
python3 scripts/verify_engine2_release.py
```

Verifies identity, integrity, schemas, the demonstration, and the focused release suites.

```bash
python3 scripts/verify_engine2_release.py --full
```

Adds the full repository suite.

```bash
streamlit run streamlit_engine2_demo.py
```

Runs the public Engine 2 demo locally.

No Anthropic API key or other AI-provider credential is required for the public demo.

## 14. Limitations

- The conceptual basis is an unpublished working paper that has not been peer reviewed.
- All demonstration and test data is synthetic.
- Corpus results and test counts are software verification, not empirical validation of the BAEC construct or Engine 2.
- There is no evidence that correspondence predicts revenue, purchases, conversion, or buyer outcomes.
- Buyer conditions can become stale; Engine 2 relies on Engine 1's stored currentness status and does not judge currentness itself.
- The Engine 1 to Engine 2 handoff is point in time and not continuously synchronized.
- There is no automatic outreach, CRM automation, or account-state transition.
- Advisory AI has not been qualified by a Stage H-B live run.
- Reviewer identity is self-asserted in the research prototype.
- This is a research prototype. No claim of production readiness or production security is made.

## 15. Public brief

The formatted public companion, designed to match the Engine 1 brief and including the full demo walkthrough, is packaged at:

`docs/engine2/BAEC_Engine_2_Research_and_Technical_Brief.pdf`

Use that PDF for public sharing and LinkedIn. This Markdown file remains the repository-native technical reference.
