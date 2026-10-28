# BAEC Engine 2: Monitoring + Correspondence Domain Contract

**Project:** BAEC Trigger Intelligence
**Engine:** BAEC Engine 2: Monitoring + Correspondence
**Stage:** A (domain contract only)
**Status:** Stage A LOCKED (October 8, 2026). Nothing in this document is implemented.
**Depends on:** BAEC Engine 1 v1.0 (tag `baec-engine1-v1.0`, commit `da7cde96cf80e2cbc7fb7d27fc1713fae4f8b567`), which is released and frozen.

Source authority, in descending order: the manuscript "When the Buyer Is Not In-Market: Buyer-Articulated Evaluation Contingencies in B2B Sales Encounters"; `docs/RESEARCH_CONTRACT.md`; locked Engine 1 domain semantics; the Engine 1 Research and Technical Brief; this contract. If this contract conflicts with any higher source, this contract is wrong and must be corrected. Section references (§) point to the manuscript. Rule references (RC-nn) point to the Research Contract. The manuscript itself is not reproduced here.

Engine 2 principle: **Correspondence narrows attention. It does not establish buyer evaluation, purchase intent, or an opportunity.**

---

## 1. Purpose and Scope

This contract fixes the domain boundaries for BAEC Engine 2 before any Engine 2 code exists. It defines what Engine 2 may receive from Engine 1, which concepts it introduces, what each concept may and may not mean, where human authority is required, and where Engine 2 must stop.

Engine 2 operationalizes one managerial implication of the manuscript: once a buyer-specific condition has been preserved, legitimate and authorized sources may be watched for evidence that may correspond to that condition, and a human reviews that evidence before anything is done with it.

In scope for Engine 2, when later implemented:

- receiving an authoritative, human-confirmed BAEC from Engine 1;
- a human-authorized Monitoring Plan derived from that BAEC;
- capture of Observations from Authorized Sources, with provenance;
- selection of Signal Candidates and assembly of Correspondence Candidates;
- optional AI Correspondence Assessment, held as AI inference;
- Human Correspondence Review and a deterministic correspondence outcome;
- a hard stop before any seller action or account-state change.

Out of scope for Engine 2 entirely: BAEC capture, BAEC classification, BAEC confirmation, account-state transitions, seller outreach, and any form of purchase or intent prediction (Section 19).

Stage A produces this document only. It creates no code, schema, migration, prompt, connector, scheduler, interface, or test.

---

## 2. Research Basis

### 2.1 What the manuscript supports

The manuscript supports preserving the buyer-specific condition and monitoring legitimate or authorized sources for evidence that may correspond to it, followed by human revalidation.

The relevant manuscript content is:

- **§5.1.** The three-state qualification logic (active opportunity, conditionally dormant, no plausible path). Conditionally dormant is an account-management label, not a validated psychological construct, and it warrants "preservation and selective reassessment rather than repeated persuasion." If the stated condition appears to emerge later, the seller should not assume the earlier statement remains valid; a BAEC is "a reason to reassess, not a permanent permission slip."
- **§5.2.** Buyer-specific monitoring: the distinctive element is the origin of the signal specification (a condition the buyer articulated), not the existence of monitoring, for which the manuscript claims no novelty. The sketched architecture links the condition to legitimate and authorized evidence sources, monitors for evidence potentially consistent with the condition, and on a possible match does not create an opportunity but prompts human review of whether the evidence is accurate, whether the original contingency remains valid, and whether re-engagement is appropriate. Governance: public signals can be incomplete or misleading, internal data may carry access restrictions, statements go stale, and automated outreach based on inferred adversity could be harmful. The design principle is AI-assisted detection with human contextual judgment; BAEC should narrow attention, not automate persuasion.
- **Figure 2.** The architecture runs CEE conversation, BAEC captured, signal monitoring, potential match, human review. Its governance principle states that a detected signal is not proof that the BAEC has been satisfied and that monitoring should support human reassessment, not autonomous opportunity creation.
- **§6.5.** AI-enabled account-management tests are future research. The associated Future Research Proposition is explicitly outside the core theoretical model and is untested.
- **§6.6.** Monitoring should be constrained to legitimate business data, authorized internal sources, and public information relevant to the articulated condition; not unrelated personal or sensitive information. The boundary between relevance and surveillance, and between human-in-the-loop review and automated outreach, is an open ethical question.
- **§3.5.** A BAEC is articulated before the relevant event and is not itself a realized trigger. Evidence that the condition may have emerged is therefore a different kind of information from the BAEC.
- **§4.9, Proposition 10.** Whether buyers who articulated a condition are more likely to open evaluation when it occurs is a proposed, untested relationship. Engine 2 must not assume it is true.

These are carried into the Research Contract as RC-26 (buyer-specific monitoring), RC-27 (a signal is not an opportunity), RC-28 (human review questions), RC-29 (BAECs go stale), and RC-30 (governance), all MANAGERIAL.

### 2.2 What Engine 2 adds

BAEC Engine 2 operationalizes that managerial implication through software concepts such as Monitoring Plans, Observations, Derived Measurements, Signal Candidates and Correspondence Candidates. These implementation concepts are not presented as validated theoretical constructs.

"Correspondence" is a product and software term. The manuscript speaks of evidence "potentially consistent with" the condition and of a "potential match". It does not define correspondence, correspondence states, or correspondence dimensions. Everything in Sections 4 to 18 that is labeled IMPLEMENTATION is a design choice of this software and makes no claim about the BAEC theory.

---

## 3. Authority Classification

Labels follow Research Contract §1.

| Concept | Authority | Basis |
|---|---|---|
| BAEC definition | DEFINITIONAL | §3.1; RC-01 |
| Four constitutive criteria (C1 to C4) | DEFINITIONAL | §3.1; RC-02 |
| BAEC endpoint is evaluation activation, not purchase or switching | DEFINITIONAL | §3.2, §3.4; RC-04 |
| BAEC is not a realized trigger | DEFINITIONAL | §3.5; RC-06 |
| CEE definition | DEFINITIONAL | §3.6; RC-07. Engine 2 does not use CEE. |
| CEE propositions (P1, P2a, P2b, P8) | PROPOSED, untested | RC §14 |
| Quality dimensions (specificity, plausibility, addressability, representational authority) | PROPOSED | §4.2; RC-14 |
| Stringency | PROPOSED | §4.4; RC-17 |
| P10 prospective validity | PROPOSED, untested | §4.9 |
| Account-prioritization proposition for AI monitoring | PROPOSED, untested, outside the core model | §6.5 |
| Three account states | MANAGERIAL | §5.1; RC-20 |
| Preserve the buyer-specific condition | MANAGERIAL | §5.1, §5.2 |
| Monitor legitimate and authorized sources | MANAGERIAL | §5.2, §6.6; RC-26, RC-30 |
| Watch for evidence that may correspond | MANAGERIAL | §5.2, Figure 2 |
| Human revalidation before action | MANAGERIAL | §5.2, Figure 2; RC-27, RC-28 |
| A signal is not an opportunity | MANAGERIAL | §5.2, Figure 2; RC-27 |
| BAECs go stale | MANAGERIAL | §5.1; RC-29 |
| Engine 2 input boundary | IMPLEMENTATION | Section 4 |
| Monitoring Plan | IMPLEMENTATION | Section 5 |
| Authorized Source | IMPLEMENTATION (operationalizing RC-30) | Section 6 |
| Observation | IMPLEMENTATION | Section 7.1 |
| Derived Measurement | IMPLEMENTATION | Section 7.3 |
| Signal Candidate | IMPLEMENTATION | Section 7.2 |
| Correspondence dimensions | IMPLEMENTATION | Section 8 |
| Correspondence Candidate | IMPLEMENTATION | Section 8 |
| AI Correspondence Assessment | IMPLEMENTATION | Section 9 |
| Human Correspondence Review | IMPLEMENTATION (operationalizing RC-28 in part) | Section 10 |
| Engine 1 BAEC Revalidation | Engine 1 authority (MANAGERIAL basis RC-29; process is IMPLEMENTATION) | Sections 4.3, 10 |
| `BAEC_REVALIDATION_REQUIRED` workflow condition | IMPLEMENTATION | Section 4.3 |
| Correspondence outcomes and the outcome rule | IMPLEMENTATION | Section 11 |
| Action Review | Future IMPLEMENTATION boundary; not designed, not implemented | Section 12 |
| Provenance and audit structures | IMPLEMENTATION (extending RC-32, RC-33) | Section 14 |

No IMPLEMENTATION concept in this table may be described, in code, interface text, or documentation, as a research finding or a manuscript construct.

---

## 4. Engine 2 Domain Model

### 4.1 Flow

```
Confirmed BAEC (Engine 1, authoritative)
  |
  v
Monitoring Plan (human-authorized)
  |
  v
Observation (raw evidence captured from an Authorized Source, with provenance)
  |
  +--> Derived Measurement (optional; deterministic arithmetic on Observations)
  |
  v
Signal Candidate (an Observation selected as possibly relevant)
  |
  v
Correspondence Candidate (+ optional AI Correspondence Assessment, AI_INFERENCE)
  |
  v
Human Correspondence Review
  |
  v
Deterministic Correspondence Outcome
(NO_CORRESPONDENCE | POSSIBLE_CORRESPONDENCE | HUMAN_VERIFIED_CORRESPONDENCE)
  |
  v
STOP
Human attention only. Any action needs a separate, future,
separately authorized Action Review (Section 12).

At any point: evidence that the BAEC itself may no longer apply
raises BAEC_REVALIDATION_REQUIRED and routes to Engine 1 (Section 4.3).
```

### 4.2 Input boundary

Engine 2 may begin only from a BAEC that Engine 1 has made authoritative. Concretely, the input must be a stored BAEC record with classification `CONFIRMED_BAEC` whose confirmation passed the Engine 1 human authority boundary (human review, human authorization, and confirmation through the separate executor). The Engine 1 public browser demo stops at authorization and produces no confirmed BAEC, so it is never an Engine 2 input.

Engine 2 receives, read-only:

| Input | Engine 1 source | Provenance |
|---|---|---|
| BAEC identifier | `baec_id` | ID |
| Account reference | `account_id` | ID |
| Exact buyer statement | `buyer_exact_statement` | BUYER_FACT |
| Exact buyer evidence references | source excerpt and criterion evidence, with source interaction id | BUYER_FACT or SELLER_OBSERVATION, as recorded |
| Confirmed buyer-defined condition | the verbatim text above; never a paraphrase | BUYER_FACT |
| Authoritative normalized condition | the human-reviewed final normalization linked to the confirmation, where one exists | DERIVED (human-reviewed). Never evidence. |
| Threshold, comparator, unit | the human-decided structured stringency (`verbatim_text`, `comparator`, `numeric_value`, `unit`, `qualitative_term`), where one exists | DERIVED from BUYER_FACT; verbatim part is SOURCE |
| Timing and renewal context | stringency `timing_text`, where verified | SOURCE |
| Recurrence context | stringency `recurrence_text`, where verified | SOURCE |
| Buyer role and authority as recorded | `buyer_role` and any representational-authority notes | as recorded; UNKNOWN allowed |
| Currentness | `staleness_status` and the Engine 1 record that established it | DERIVED (Engine 1 authority) |
| Confirmation and audit reference | the confirmation authorization and its audit lineage | ID |
| Human authority metadata | who authorized and when | ID |

Rules for the input boundary:

1. **Structured stringency is authoritative for threshold, comparator, and unit.** The normalized condition is human-reviewed derived text. Where the two differ, the structured stringency and the verbatim buyer statement control, and the discrepancy is surfaced for human attention. Engine 2 never re-derives a threshold from prose.
2. **AI normalization is not an input.** An AI-derived normalized condition (`AiDerivedText`) is AI inference and may be shown only as such. It never populates a Monitoring Plan dimension.
3. **No AI_DRAFT.** Engine 2 never reads an AI_DRAFT proposal, a review revision that has not been confirmed, or a granted but unconsumed authorization as a BAEC. A BAEC whose content originated in an AI_DRAFT is acceptable only after it has completed Engine 1 confirmation.
4. **No BAEC creation.** Engine 2 never creates, edits, reclassifies, re-normalizes, or confirms a BAEC, and never creates one from raw conversation data, an Observation, or a seller note. If an Observation suggests a new buyer condition, that is input for a new Engine 1 capture, not an Engine 2 record.
5. **No substitution for Engine 1.** Engine 2 does not reassess C1 to C4, articulation origin, or quality dimensions.
6. **Currentness is Engine 1's.** Engine 2 reads currentness; it never determines, sets, or infers it (Section 4.3).

### 4.3 BAEC currentness and revalidation

**Authority:** currentness and revalidation belong to Engine 1 (RC-29, MANAGERIAL basis). The workflow condition below is IMPLEMENTATION.

**Activation rule.** A Monitoring Plan may become `ACTIVE` only when Engine 1 reports the confirmed BAEC as current (`staleness_status` `CURRENT`). The plan retains a reference to the Engine 1 record that establishes that currentness: the confirmation record, or a later Engine 1 revalidation record if one exists. If Engine 1 reports `REVIEW_DUE`, `STALE`, or `RETIRED`, the plan cannot be activated, and an active plan becomes `INACTIVE` with that reason. A missing, unreadable, or contradictory currentness reading is treated as not current (fail closed).

**`BAEC_REVALIDATION_REQUIRED`.** Engine 2 raises this workflow condition on a BAEC, with the triggering evidence attached, when it encounters evidence suggesting that:

- the BAEC may be stale;
- the buyer's condition may no longer apply;
- the referenced entity has changed (for example, the buyer may have changed suppliers);
- the timing context has expired (for example, the renewal named in the condition has passed);
- the condition may have been superseded by a later buyer statement.

It may be raised by a human reviewer, by a deterministic plan rule, or proposed by AI (as AI_INFERENCE, which a human must accept before it takes effect).

`BAEC_REVALIDATION_REQUIRED` is not a correspondence outcome, not a fourth outcome, not an account state, and not a change to BAEC validity. While it is active:

- Engine 2 must not produce a new `HUMAN_VERIFIED_CORRESPONDENCE` outcome for any candidate under that BAEC (Section 11.2);
- Engine 2 must not change the BAEC's classification, staleness, or any other Engine 1 field;
- the question is routed to Engine 1 BAEC Revalidation, a human decision under Engine 1 authority.

`NO_CORRESPONDENCE` and `POSSIBLE_CORRESPONDENCE` outcomes may still be recorded, and existing outcomes are not deleted. The condition clears only when Engine 1 reports the BAEC current again through a new Engine 1 record, which the plan then references.

**Known dependency.** Engine 1 v1.0 does not currently implement a BAEC revalidation lifecycle. Engine 2 may raise `BAEC_REVALIDATION_REQUIRED` and must fail closed while it is active, but clearing that condition requires a future, separately approved Engine 1 lifecycle capability. This dependency does not block Engine 2 domain design or synthetic corpus work.

### 4.4 Concept summary

| Concept | One-line meaning | Is not |
|---|---|---|
| Monitoring Plan | What evidence would be relevant to a confirmed BAEC, and which sources may be watched | a sales trigger, a lead rule, a score |
| Authorized Source | A source that a human has approved for a plan, with a stated legitimate basis | any reachable source |
| Observation | A captured piece of raw source evidence with provenance | a relevant piece of evidence; a calculation |
| Derived Measurement | A deterministic value calculated from Observations for comparison with the structured condition | an Observation; an AI estimate |
| Signal Candidate | An Observation selected as possibly relevant to a condition dimension | proof the condition occurred |
| Correspondence Candidate | A structured hypothesis that one or more Signal Candidates may correspond to the BAEC condition | a finding |
| AI Correspondence Assessment | AI-proposed dimension findings, excerpts, contradictions, and rationale | authoritative |
| Human Correspondence Review | The human decision on which evidence is accepted and what each dimension shows | BAEC revalidation; seller action authorization |
| `BAEC_REVALIDATION_REQUIRED` | A workflow condition routing a question about the BAEC itself to Engine 1 | a correspondence outcome; a change to BAEC validity |
| Correspondence Outcome | `NO_CORRESPONDENCE`, `POSSIBLE_CORRESPONDENCE`, or `HUMAN_VERIFIED_CORRESPONDENCE` | an account state, evaluation, intent |

---

## 5. Monitoring Plan

**Authority:** IMPLEMENTATION.

**Definition.** A structured, human-authorized representation of what evidence would be relevant to observe for a confirmed BAEC and which sources may legitimately be monitored.

A Monitoring Plan preserves the buyer-defined condition. It does not rewrite it into a generic sales trigger, widen it, or add conditions the buyer did not state (RC-26). A plan that would be equally sensible for any account in the category is evidence that the condition has been genericized.

Conceptual fields (not a schema):

| Field | Meaning |
|---|---|
| `monitoring_plan_id` | Plan identifier |
| `baec_id` | The confirmed BAEC this plan serves; exactly one |
| Authoritative condition reference | Pointer to the verbatim buyer statement, the structured stringency, and the human-reviewed normalization, as received under Section 4.2. Copied by reference, never re-typed. |
| Target entity or entities | The entities the condition concerns (for Harbor: the incumbent supplier and Harbor's own agreement). Each entity records its basis in recorded evidence. |
| Condition dimensions | The dimensions from Section 8.2 that the condition actually specifies, each marked required or not applicable, each with a reference to the buyer evidence it comes from |
| Threshold, comparator, unit | Exactly as in the structured stringency (RC-18). Never tightened, loosened, rounded, or converted. |
| Timing context | Exactly as verified (for Harbor: "when our agreement renews"), plus any separately evidenced date the human records, with its own provenance |
| Authorized source set | The Authorized Sources this plan may observe (Section 6) |
| Source-specific observation rules | For each source, what is captured and what is ignored, written to exclude irrelevant and personal information |
| `created_by` | Who drafted the plan (human, or AI as AI_INFERENCE draft) |
| `authorized_by` | The human who authorized the plan; never a model-supplied value |
| `created_at` | Creation time |
| Authorization provenance | The authorization record, bound to this plan and this BAEC (RC-31) |
| Engine 1 currentness reference | The Engine 1 record establishing that the BAEC was current when the plan was activated (Section 4.3) |
| Status | `ACTIVE` or `INACTIVE`, with the reason for every change |

Rules:

1. Approving monitoring requires human authorization (RC-31). The authorization names the plan and the BAEC. A plan drafted with AI assistance is AI_INFERENCE until authorized, and authorization does not convert AI text into buyer evidence.
2. Every required dimension traces to the buyer's recorded evidence. A dimension whose only basis is a seller's or model's belief about what matters is refused. This is how the plan enforces that monitoring may operationalize the condition but may not broaden what matters (RC-26).
3. Entity resolution is a recorded human decision. Where the buyer used a relative reference ("our supplier", "our agreement"), the plan records which entity the human resolved it to and the evidence for that resolution. If the resolution is uncertain, the entity dimension cannot later be marked supported (Section 16).
4. Editing an authorized plan creates a new plan version that requires a new authorization. Prior versions are retained.
5. A plan may become `ACTIVE` only under the activation rule of Section 4.3.
6. A dimension may be marked not applicable only where the plan establishes, with reference to the buyer's evidence, that the buyer's condition genuinely does not depend on it. Not applicable is never a way to set aside a dimension that is hard to evidence.
7. A plan is not a workflow status of the account and is not an account state (RC-20).

---

## 6. Authorized Sources

**Authority:** IMPLEMENTATION, operationalizing RC-30 (§5.2, §6.6).

**Definition.** An Authorized Source is a specific source that a human has approved for a specific Monitoring Plan, with a stated legitimate basis for observing it, a defined scope of what may be captured, and an access method that respects the source's own terms and access controls.

Examples of source types that can be legitimate when authorized and scoped to the condition:

| Source type | Typical legitimate basis | Typical provenance category |
|---|---|---|
| Buyer-provided documents | Supplied by the buyer to the seller | BUYER_FACT (for the buyer's own statements) or EXTERNAL EVIDENCE |
| Supplier notices | Published or provided by the supplier | EXTERNAL EVIDENCE |
| Contract-renewal notices | Lawfully held by the seller or provided by the buyer | EXTERNAL EVIDENCE |
| Public corporate announcements | Published by the organization | EXTERNAL EVIDENCE |
| Authorized CRM and account records | Internal records the seller is entitled to use | SELLER_OBSERVATION or EXTERNAL EVIDENCE, as recorded |
| Approved pricing feeds | Licensed or contracted data | EXTERNAL EVIDENCE |
| Approved public web sources | Specific pages approved for this plan, used within their terms | EXTERNAL EVIDENCE |

Prohibited, regardless of technical feasibility:

- access to private data without authorization;
- use of credentials outside their granted purpose, or any credential misuse;
- bypassing paywalls, logins, rate limits, robots directives, or other access controls;
- covert surveillance of people or organizations;
- scraping that violates a source's terms or the plan's authorization;
- collecting personal information that is not relevant to the articulated condition;
- using protected or sensitive data without a legitimate basis.

A source being publicly accessible does not make every use appropriate. Public availability is necessary for some source types and never sufficient. Each Authorized Source must still be relevant to the articulated condition and within the plan's scope (§6.6).

Source provenance requirements. Every Authorized Source records: a stable source identifier; source type; owner or publisher; the legitimate basis for use; the access method; the scope of permitted capture; who authorized it, when, and for which plan; and any known limitations (for example, that a public supplier notice may not state which customers it applies to). A source without these is not authorized, and evidence from it cannot support a correspondence dimension.

---

## 7. Observation and Signal Capture

### 7.1 Observation

**Authority:** IMPLEMENTATION.

**Definition.** An Observation is a captured piece of raw source evidence with provenance. It implies nothing about relevance, and it never contains a calculation made after capture.

Conceptual properties:

| Property | Meaning |
|---|---|
| `observation_id` | Identifier |
| `monitoring_plan_id` | The plan under which it was captured |
| `source_id` | The Authorized Source |
| Source type | From the source record |
| `observed_at` | When the system captured it |
| Publication or effective time | When the source says it was published or takes effect, where stated. Recorded as stated; never inferred. Absent means UNKNOWN. |
| Exact evidence content | The captured text, verbatim, immutable (RC-33) |
| Source locator | Where in the source the content was found |
| Provenance | RC-32 category, normally EXTERNAL EVIDENCE |
| Integrity metadata | A content digest and capture context sufficient to show the content was not altered after capture |
| Acquisition method | How it was obtained (manual entry, approved feed, approved retrieval) |
| Authorization reference | The Authorized Source authorization under which it was captured |

Rules:

1. An Observation is immutable once captured. A correction or retraction published by the source is a new Observation that references the earlier one; it never overwrites it (RC-33).
2. An Observation captured outside an authorized plan or source has no standing in Engine 2 and cannot support any dimension.
3. Observation, Derived Measurement, Signal Candidate, Correspondence Candidate, and a `HUMAN_VERIFIED_CORRESPONDENCE` outcome are distinct things. No record is ever promoted from one to the next by relabeling.

### 7.2 Signal Candidate

**Authority:** IMPLEMENTATION.

**Definition.** An Observation selected for further evaluation because it may be relevant to one or more dimensions of the buyer-defined BAEC condition.

A Signal Candidate is not:

- proof the condition occurred;
- proof the buyer is evaluating;
- purchase intent;
- opportunity status;
- authorization to contact the buyer.

Rules:

1. Every Signal Candidate references exactly one Observation and the exact excerpt within it that prompted selection.
2. It names the plan dimensions it may bear on. It asserts no finding on any of them.
3. Selection may be made by a deterministic plan rule, by a human, or proposed by AI. The method is recorded. AI selection is AI_INFERENCE.
4. Selection is a filter for attention, not a relevance judgment with authority. A non-selected Observation is retained and remains available for review.

### 7.3 Derived Measurement

**Authority:** IMPLEMENTATION.

**Definition.** A reproducible deterministic value calculated from one or more authoritative Observations for comparison against a structured BAEC condition.

Examples:

- a percentage price increase derived from an old and a new price stated in Observations;
- elapsed time derived from dated source events.

A Derived Measurement preserves:

- the source Observation IDs;
- the exact input values, as they appear in the Observations;
- the units of each input and of the output;
- the formula;
- the rounding rule (including "no rounding");
- the output value;
- the calculation timestamp;
- a deterministic transformation identifier and version.

Rules:

1. Raw Observation content is never overwritten. A Derived Measurement is a separate record that references its Observations.
2. AI must not perform the authoritative calculation. AI may point out that a calculation would be relevant; the value used for a finding comes only from deterministic software arithmetic.
3. Deterministic software arithmetic is permitted. The same inputs, formula, rounding rule, and transformation version always produce the same output, so any Derived Measurement can be recomputed and checked.
4. A human may verify the inputs and the result. A Derived Measurement can support a dimension only after a human accepts it, and only if every input Observation is itself accepted, current, and not superseded or retracted.
5. The derived value remains auditable back to source evidence. If an input Observation is corrected or retracted, the Derived Measurement no longer supports any finding and a new one must be computed from the controlling Observations.
6. A Derived Measurement never changes the buyer's threshold, comparator, or unit. It produces a value that is compared with them exactly as stated.
7. The scope is limited to calculations needed to compare evidence with a structured BAEC condition. Derived Measurement is not a general analytics layer, and it never produces scores, trends, forecasts, or aggregates across accounts.

---

## 8. Correspondence Candidate

### 8.1 Definition

**Authority:** IMPLEMENTATION.

**Definition.** A BAEC Correspondence Candidate is a structured hypothesis that one or more Signal Candidates may correspond to the buyer-defined condition preserved in a confirmed BAEC.

Correspondence is a relation between:

- A. the authoritative buyer-defined BAEC condition; and
- B. observed external or account evidence.

It is not a relation between a signal and a purchase, a signal and evaluation, or a signal and the buyer's intent. The question correspondence asks is "does this evidence show the condition the buyer described?" It never asks "will this buyer act?"

A Correspondence Candidate holds: the `baec_id` and plan version; the Signal Candidates it groups; the dimensions required by the plan; and, once they exist, any AI Correspondence Assessment and any Human Correspondence Reviews. It has a workflow status (`OPEN`, `REVIEWED`) that is neither an outcome nor an account state.

### 8.2 Correspondence dimensions

Each required dimension is assessed separately, with its own evidence. Dimensions that the buyer's condition does not specify are marked not applicable, never silently assumed satisfied.

| Dimension | Question |
|---|---|
| Entity match | Is the evidence about the entity the condition concerns (for Harbor: Harbor's incumbent supplier, not another supplier)? |
| Relationship match | Does it concern the buyer's own relationship or agreement, rather than the entity's dealings generally? |
| Source relevance | Is the source capable of establishing this dimension (for example, a general announcement versus a notice addressed to the buyer)? |
| Event or condition type | Is the reported event the kind of change the buyer named (for Harbor: a pricing increase)? |
| Threshold match | Does the reported magnitude meet the buyer's threshold under the buyer's comparator? |
| Comparator match | Is the comparison applied exactly as stated ("more than" is strict)? |
| Numeric magnitude | What magnitude does the evidence state, exactly as stated? |
| Unit match | Is the evidence in the buyer's unit (for Harbor: percent change)? |
| Timing match | Does the evidence satisfy the buyer's timing context? |
| Renewal or effective-date match | Does the effective date align with the renewal or other stated time? |
| Causal or contextual relevance | Is the evidence about the condition the buyer described, rather than a coincidental match on words or numbers? |
| Freshness | Is the evidence current, and not superseded, retracted, or outdated? |
| Contradictory evidence | Does any accepted evidence contradict it? |
| Missing evidence | What would be needed to resolve each unresolved dimension? |
| Provenance quality | Does the evidence come from an Authorized Source with complete provenance? |

The last four are cross-cutting checks. They do not stand in for a required dimension; they limit what may be concluded from it.

### 8.3 Dimension findings

A dimension finding takes one of four values, mirroring the MET / NOT_MET / UNKNOWN pattern of RC-13:

| Finding | Meaning |
|---|---|
| `SUPPORTED` | Accepted evidence establishes the dimension. Requires at least one accepted evidence excerpt or accepted Derived Measurement. |
| `CONTRADICTED` | Accepted evidence establishes that the dimension is not met. Requires at least one accepted evidence excerpt or accepted Derived Measurement. |
| `UNRESOLVED` | Evidence is missing, ambiguous, stale, conflicting, or insufficient. A stated reason is required. |
| `NOT_APPLICABLE` | Permitted only where the authorized Monitoring Plan establishes that the dimension is genuinely not required (Section 5, rule 6). |

These findings are categorical. There are no scores, weights, grades, probabilities, confidence percentages, or partial-match numbers for a dimension, a candidate, or an outcome (CLAUDE.md; RC-15 by extension).

---

## 9. AI Correspondence Assessment

**Authority:** IMPLEMENTATION.

AI may assist with correspondence assessment. Its output is always `AI_INFERENCE` (RC-32), is stored separately from evidence and from human decisions, and is never authoritative.

AI may propose:

- which condition dimensions appear supported, contradicted, or unresolved;
- which exact Observation excerpts appear to bear on each dimension;
- which dimensions remain unresolved, and what evidence would resolve them;
- apparent contradictions between Observations;
- a possible correspondence outcome;
- a rationale for each of the above.

AI may not establish:

- authoritative correspondence, or any authoritative dimension finding;
- that the buyer is evaluating or has entered the market;
- intent to buy or to switch;
- opportunity status;
- that the seller should act, or how;
- any account-state transition;
- any change to the BAEC, its threshold, its timing, or its currentness;
- that the BAEC has been revalidated.

Rules:

1. **Every AI claim traces to exact evidence.** Each proposed finding cites verbatim excerpts from Observations in the candidate. An excerpt that is not an exact substring of a captured Observation is refused, as in Engine 1 (fail closed).
2. **No authoritative computation.** AI does not convert units, compute percentages from prices, round, or combine figures across sources for any value used in a finding. If a figure would require calculation, AI may say so; the value comes only from a Derived Measurement (Section 7.3). Until one exists and is accepted, the dimension is proposed as `UNRESOLVED`.
3. **No number invention.** AI never introduces a threshold, magnitude, date, or count absent from the BAEC or the cited evidence (RC-18).
4. **No authority by presentation.** AI proposals are visibly labeled as AI inference in every interface. Formatting, ordering, default selections, or pre-filled controls must not turn a proposal into a decision. Human review controls start empty, as in Engine 1 Phase 7.
5. **Fail closed.** Invalid, incomplete, or unverifiable AI output is discarded and recorded as a failed assessment. Review proceeds without it. Absence of an AI assessment never blocks human review.

---

## 10. Human Correspondence Review

**Authority:** IMPLEMENTATION, operationalizing part of RC-28 (§5.2).

**Definition.** The act by which an identified human decides which observed evidence becomes authoritative for correspondence and records a finding for each required dimension.

The reviewer can:

- accept or reject each evidence excerpt, with a reason for rejection;
- record that evidence concerns the wrong entity or the wrong relationship;
- record wrong timing or wrong effective date;
- record a magnitude that does not meet the threshold under the stated comparator;
- record stale, superseded, or retracted evidence;
- record insufficient evidence;
- record contradictory evidence and which item controls;
- preserve exact numeric and stringency information as stated in evidence, without rounding;
- record a finding for each required dimension (Section 8.3);
- record a sufficiency judgment: whether the accepted evidence corresponds sufficiently to the BAEC condition to warrant human attention (`YES`, `NO`, `UNKNOWN`);
- raise `BAEC_REVALIDATION_REQUIRED` (Section 4.3).

### 10.1 Three separate human authorities

The manuscript (§5.2, RC-28) describes one human review that asks three questions: whether the evidence is accurate, whether the original contingency remains valid, and whether re-engagement is appropriate. This software decomposes those questions into three separate authorities. The decomposition is an IMPLEMENTATION choice that operationalizes the manuscript's review questions; the manuscript does not itself define three separate processes.

| | Authority | Purpose | Status |
|---|---|---|---|
| A. Human Correspondence Review | IMPLEMENTATION (Engine 2) | Determine whether observed evidence corresponds sufficiently to the authoritative BAEC condition | Defined by this contract |
| B. Engine 1 BAEC Revalidation | Engine 1 | Determine whether the confirmed BAEC itself remains current and valid | Owned by Engine 1. Engine 2 may raise `BAEC_REVALIDATION_REQUIRED` but must not alter BAEC validity or currentness. |
| C. Action Review | Future IMPLEMENTATION boundary | Determine whether any seller action or re-engagement is appropriate | Not designed and not implemented (Section 12) |

The three are never combined. A decision under one is not a decision under another: a correspondence review does not revalidate the BAEC, a revalidation does not establish correspondence, and neither authorizes any action. Each requires its own human decision and its own record.

Rules:

1. Review requires human authorization bound to the candidate (RC-31). The reviewer's identity or alias and the decision time are recorded. A model-supplied value is never proof of human review.
2. Human Correspondence Review is neither BAEC revalidation nor seller action authorization. A completed review, including one that yields `HUMAN_VERIFIED_CORRESPONDENCE`, revalidates nothing and authorizes nothing.
3. Reviews are append-only. A later review of the same candidate supersedes the earlier one and references it; nothing is overwritten.
4. Signals involving adversity (shortages, layoffs, service failures, regulatory events) are flagged for contextual review (RC-30). Engine 2 records the flag; it does not decide what to do about it.

---

## 11. Deterministic States and Outcomes

**Authority:** IMPLEMENTATION.

### 11.1 Outcomes

| Outcome | Definition |
|---|---|
| `NO_CORRESPONDENCE` | Human-reviewed evidence does not meaningfully correspond to the authoritative BAEC condition: at least one required dimension is contradicted by accepted evidence. |
| `POSSIBLE_CORRESPONDENCE` | Evidence may be relevant, but one or more required condition dimensions remain unresolved, ambiguous, or insufficient, the reviewer has not judged sufficiency `YES`, or `BAEC_REVALIDATION_REQUIRED` is active. |
| `HUMAN_VERIFIED_CORRESPONDENCE` | A human reviewer has determined that accepted, sourced and current evidence corresponds sufficiently to the authoritative buyer-defined BAEC condition to warrant human attention. |

`HUMAN_VERIFIED_CORRESPONDENCE` does not mean, and must never be presented as meaning:

- the buyer is evaluating;
- the buyer is in market;
- the BAEC itself has been revalidated;
- purchase intent;
- switching intent;
- opportunity creation;
- a sales-qualified lead;
- permission to contact the buyer;
- an account-state transition.

"Human-verified" describes who established the correspondence and on what evidence. It says nothing about the buyer's state of mind and nothing about whether the BAEC is still valid.

### 11.2 Outcome rule

The outcome is derived deterministically from the human's recorded findings, in this order:

1. Any required dimension `CONTRADICTED` → `NO_CORRESPONDENCE`, with the contradicted dimensions stated.
2. Otherwise, all of the following → `HUMAN_VERIFIED_CORRESPONDENCE`:
   - every required dimension `SUPPORTED`;
   - every supporting excerpt and Derived Measurement accepted by the human, from an Authorized Source with complete provenance, current, and not superseded or retracted;
   - an explicit human sufficiency finding of `YES`;
   - Engine 1 reports the BAEC current, and no `BAEC_REVALIDATION_REQUIRED` condition is active on it.
3. Otherwise → `POSSIBLE_CORRESPONDENCE`, with the unresolved dimensions, missing evidence, or active revalidation condition stated.

`NOT_APPLICABLE` dimensions are excluded from rules 1 and 2 only where the Monitoring Plan establishes that they are genuinely not required.

The outcome is never set directly. It is computed from human findings, so it cannot be reached by AI output, and `HUMAN_VERIFIED_CORRESPONDENCE` cannot be reached without an explicit human sufficiency judgment. A human who believes the rule produces the wrong outcome changes a finding, with a reason; there is no override field.

A candidate with no completed human review has no outcome. "Awaiting review" is a workflow status, not a fourth outcome.

### 11.3 Sufficiency of three outcomes

Three outcomes are sufficient for the decision Engine 2 exists to support: whether a human should give the case attention. The cases that might seem to need more states are handled without expanding the model:

| Case | Handling | Why not a new outcome |
|---|---|---|
| Not yet reviewed | Workflow status `OPEN`; no outcome | An outcome is a human result; there is none yet |
| AI proposal only | AI_INFERENCE attached to an `OPEN` candidate | AI cannot produce an outcome |
| Insufficient provenance | `UNRESOLVED` on provenance-dependent dimensions → `POSSIBLE` at most | It is a reason, not a different conclusion |
| Superseded or retracted evidence | New Observation, new review, new outcome that references the old one | History is preserved by append-only review, not by a state |
| BAEC may no longer be valid | `BAEC_REVALIDATION_REQUIRED` workflow condition, routed to Engine 1 (Section 4.3) | It concerns the BAEC, not the evidence |

Adding outcomes such as "likely correspondence" or "strong correspondence", or any strength measure, would introduce an ordinal scale that functions as a score. That is prohibited.

### 11.4 What these are not

Correspondence outcomes are not account states and not BAEC classifications. They do not appear in the account-state field and do not alter `CONFIRMED_BAEC`, `NOT_BAEC`, or `INSUFFICIENT_EVIDENCE`. RC-20 already states that workflow statuses such as "signal detected" or "review pending" are not account states; correspondence outcomes are treated the same way.

---

## 12. Action Boundary

**Authority:** IMPLEMENTATION, enforcing §5.2, §6.6, RC-27, RC-30.

Engine 2 stops before seller action. Its last act is recording a correspondence outcome.

`HUMAN_VERIFIED_CORRESPONDENCE` may make a case eligible for human attention: it may be listed for a person to look at. It must not automatically:

- send email or any message;
- call or otherwise contact the buyer;
- create a task, reminder, or sequence step;
- change an opportunity stage or create an opportunity;
- change account state;
- alert or write to an external system;
- trigger campaign enrollment or marketing automation.

A future **Action Review** is named here as a boundary only. It is not an existing capability: it is not designed, not implemented, and not approved by this contract. If it is ever built, it must be a separate stage with its own approved design, and it must:

- answer RC-28's third question (whether re-engagement is appropriate) as an explicit human decision;
- require its own human authorization, bound to the specific action, distinct from any correspondence review;
- record the action, who authorized it, and when, with a reference to the correspondence outcome that prompted attention;
- give adversity-related cases contextual human review (RC-30);
- never execute outreach automatically (CLAUDE.md).

---

## 13. Account-State Boundary

**Authority:** MANAGERIAL states (§5.1, RC-20); IMPLEMENTATION boundary.

The three account states remain exactly `ACTIVE_OPPORTUNITY`, `CONDITIONALLY_DORMANT`, and `NO_PLAUSIBLE_PATH`, with `None` for an unclassified account (RC-20, RC-21). They are managerial labels, not validated psychological constructs. Engine 2 adds no state.

Engine 2 performs no account-state transitions, automatic or otherwise. In particular, a correspondence outcome never transforms `CONDITIONALLY_DORMANT` → `ACTIVE_OPPORTUNITY`, nor any other transition.

The reason is definitional, not only procedural. `ACTIVE_OPPORTUNITY` means the buyer is currently evaluating (RC-20). Correspondence evidence is evidence about the condition, not about the buyer's evaluation. Moving to `ACTIVE_OPPORTUNITY` requires evidence that the buyer has actually initiated or reopened evaluation, in BUYER_FACT or SELLER_OBSERVATION form, plus human authorization through Engine 1's state machine (RC-27). Evidence that a supplier raised prices by more than the buyer's threshold is not that evidence, and P10, which would link the two, is untested.

Correspondence may inform a human's separate review of account state. That review, if any, happens outside Engine 2 under the existing Engine 1 rules.

---

## 14. Provenance and Auditability

**Authority:** IMPLEMENTATION, extending RC-32 and RC-33.

Engine 2 preserves, append-only:

- the exact source and its authorization;
- the Monitoring Plan version and its authorization;
- observation capture time, and publication or effective time where stated;
- the exact evidence content and its integrity digest;
- every transformation applied to evidence (none is the expected case; any extraction or excerpting is recorded);
- every AI hypothesis, with model and artifact provenance, labeled AI_INFERENCE;
- every link between evidence and a dimension;
- every human decision, with reviewer identity or alias and decision time;
- every workflow status change and every outcome, with the review that produced it;
- references to any later Action Review, if one is ever implemented.

The five provenance categories of RC-32 remain separate. Observations are normally EXTERNAL EVIDENCE; authorized CRM entries keep the category they were recorded under; AI output is AI_INFERENCE; what has not been established is UNKNOWN.

No correspondence conclusion may exist without evidence provenance. A dimension finding of `SUPPORTED` or `CONTRADICTED` without an accepted, provenanced excerpt is refused (fail closed).

---

## 15. Prohibited Inferences and Claims

Engine 2 software, interfaces, outputs, and documentation must never state or imply that:

- correspondence means the buyer is evaluating or has entered the market;
- correspondence indicates purchase intent or switching intent;
- correspondence predicts that the buyer will evaluate, buy, or switch;
- a correspondence outcome is an opportunity, a lead, or a qualification;
- a correspondence outcome authorizes contact;
- an account is more or less likely to buy because of a correspondence outcome;
- monitoring or correspondence improves conversion, revenue, forecasting, or buyer outcomes;
- BAEC, CEE, P10, or the §6.5 proposition has been validated;
- an AI assessment is a finding;
- an Observation from a public source is appropriate to use simply because it is public;
- the evidence establishes the BAEC is still valid.

Engine 2 produces no scores, grades, rankings by likelihood, probabilities, or confidence percentages for BAECs, accounts, observations, signals, or correspondence.

Passing tests, when they exist, are software tests on synthetic data. They are not evidence for the theory (RC §13).

---

## 16. Failure and Uncertainty Cases

General rule: when in doubt, the dimension is `UNRESOLVED` and the outcome is no higher than `POSSIBLE_CORRESPONDENCE`. The system prefers an unresolved status to an overclaim. Nothing is computed, rounded, or inferred to close a gap.

| Case | Handling |
|---|---|
| Missing threshold in the BAEC | If the condition is qualitative ("a significant increase"), the threshold dimension can be judged only by a human against the qualitative term, with a written reason. No number is ever assigned (RC-18). If the BAEC states no threshold at all, the dimension is `NOT_APPLICABLE` only if the plan records that the buyer's condition does not depend on magnitude. |
| Ambiguous percentage in evidence | "Up to 12%", "an average of 12%", "12% on selected lines", or a percentage without a stated base → threshold `UNRESOLVED` unless the evidence shows the figure applies to the buyer's relationship as the buyer meant it. |
| Percent versus percentage points, or absolute amounts | Not converted by AI. Where old and new prices are both stated in accepted Observations, a deterministic Derived Measurement (Section 7.3) may compute the percentage change for human verification. Percentage points are never treated as percent. |
| Wrong supplier | Entity `CONTRADICTED` → `NO_CORRESPONDENCE`. |
| Wrong account | Relationship `CONTRADICTED` → `NO_CORRESPONDENCE`. Evidence about another customer's agreement never supports this buyer's condition. |
| Future event not yet effective | The announcement is recorded with its effective date. Timing is judged against the buyer's timing context; where the condition requires occurrence at a time not yet reached, timing is `UNRESOLVED` until the effective date, and the case is reviewed again then. |
| Timing context expired | If the renewal or other time the buyer named has passed without the condition being met, `BAEC_REVALIDATION_REQUIRED` is raised. Engine 2 does not decide whether the BAEC still applies to the next renewal. |
| Stale source | Freshness limits the finding; stale evidence cannot support `HUMAN_VERIFIED_CORRESPONDENCE`. A stale source is about the evidence; a stale BAEC is handled by `BAEC_REVALIDATION_REQUIRED`. |
| Conflicting sources | Each source's claim is retained. The reviewer records which controls and why. Without a reasoned resolution, affected dimensions are `UNRESOLVED`. |
| Insufficient timing information | Timing `UNRESOLVED`. |
| Multiple thresholds in the BAEC | Each is a separate required dimension with its own comparator; all must be supported. Thresholds are never merged or averaged. |
| Magnitude exactly at the comparator boundary | Applied exactly as stated. Under "more than 10%", exactly 10% is `CONTRADICTED`. Under "at least 10%", exactly 10% is `SUPPORTED`. If the evidence's figure is itself stated as approximate ("about 10%"), threshold is `UNRESOLVED`. |
| Source retraction | The retraction is a new Observation. Retracted content cannot support any dimension. Any earlier outcome that relied on it is superseded by a new review. |
| Corrected source | The correction is a new Observation referencing the original. The latest authoritative statement from the source controls; the earlier one is retained as history. |
| Condition partially satisfied | Satisfied dimensions are `SUPPORTED`, others `UNRESOLVED` or `CONTRADICTED`; the rule in Section 11.2 decides. Partial satisfaction never rounds up to `HUMAN_VERIFIED_CORRESPONDENCE`. |
| Seller-generated interpretation not in the buyer statement | A plan dimension, threshold, entity, or timing element that is not grounded in the buyer's recorded statement is refused at plan authorization. A seller note asserting that the condition has occurred is at most SELLER_OBSERVATION and cannot alone support a dimension that requires external evidence. |
| Relative entity reference has changed | If the buyer said "our supplier" and the buyer may have changed suppliers since, entity `UNRESOLVED` and `BAEC_REVALIDATION_REQUIRED` is raised. |
| Condition superseded by a later buyer statement | `BAEC_REVALIDATION_REQUIRED` is raised. Any new condition is a new Engine 1 capture. |
| BAEC becomes not current during review | Fails closed. The plan becomes `INACTIVE`, no new `HUMAN_VERIFIED_CORRESPONDENCE` outcome can be produced, and the review may record only `NO_CORRESPONDENCE` or `POSSIBLE_CORRESPONDENCE` until Engine 1 reports the BAEC current again (Section 4.3). |

---

## 17. Harbor Reference Scenarios

These are domain-level examples, not a test corpus. They use the existing synthetic Harbor BAEC. No corpus is implemented in Stage A.

**Authoritative condition (BUYER_FACT, verbatim):** "If our supplier raises pricing by more than 10% when our agreement renews, we'd evaluate other options."

| Element | Preserved value |
|---|---|
| Comparator | more than (strict) |
| Threshold | 10 |
| Unit | % (percentage increase in pricing) |
| Timing context | agreement renewal ("when our agreement renews") |
| Entity | Harbor's incumbent supplier; in the synthetic interaction the buyer names it as NorthStar. The plan records that resolution and its evidence. |
| Evaluation consequence | evaluate other options. This is the buyer's statement, not something Engine 2 predicts or confirms. |

Required dimensions for a Harbor plan: entity, relationship, event type (pricing increase), threshold with comparator and unit, timing (renewal). Freshness, contradiction, and provenance apply throughout.

| Case | Evidence | Key findings | Outcome |
|---|---|---|---|
| A | Authorized notice: supplier increase of 4% at Harbor's renewal | Entity, relationship, event, timing `SUPPORTED`; threshold `CONTRADICTED` (4 is not more than 10) | `NO_CORRESPONDENCE` |
| B | Supplier publicly announces a 12% general price increase; nothing shows it applies to Harbor's renewal | Entity, event `SUPPORTED`; threshold figure exceeds 10 but its application to Harbor is unknown; relationship and timing `UNRESOLVED` | `POSSIBLE_CORRESPONDENCE` |
| C | Authorized Harbor renewal notice shows a 12% increase effective at renewal | All required dimensions can be `SUPPORTED` by one provenanced excerpt | `HUMAN_VERIFIED_CORRESPONDENCE` only if the human accepts the evidence, records every required finding as `SUPPORTED`, judges sufficiency `YES`, and the BAEC is current with no revalidation condition active. Otherwise `POSSIBLE_CORRESPONDENCE`. |
| D | A competitor of the supplier raises pricing by 15% | Entity `CONTRADICTED` | `NO_CORRESPONDENCE` |
| E | Supplier raises Harbor's renewal pricing by exactly 10% | Threshold `CONTRADICTED`: the buyer said "more than 10%", and exactly 10% is not more than 10% | `NO_CORRESPONDENCE` |
| F | Supplier raises pricing 12%, effective six months after Harbor's renewal | Threshold and entity `SUPPORTED`; timing is the human's finding | Timing `CONTRADICTED` → `NO_CORRESPONDENCE`. Timing `UNRESOLVED` → `POSSIBLE_CORRESPONDENCE`. Never `HUMAN_VERIFIED_CORRESPONDENCE` while timing is unresolved or contradicted. |
| G | Seller note: supplier is "probably going up around 12%", no source | No Authorized Source; figure stated as approximate and speculative; at most SELLER_OBSERVATION | Cannot support any external-evidence dimension. `POSSIBLE_CORRESPONDENCE` at most; cannot be `HUMAN_VERIFIED_CORRESPONDENCE` |
| H | An earlier 12% renewal notice is superseded by a corrected 7% renewal notice | Correction is a new Observation and controls; the 12% notice is retained as history and cannot support a finding; threshold `CONTRADICTED` | `NO_CORRESPONDENCE`. If an earlier review had reached `HUMAN_VERIFIED_CORRESPONDENCE` on the 12% notice, a new review supersedes it and the history shows both. |

**Case F explained.** The buyer tied the condition to "when our agreement renews". An increase that takes effect six months after renewal may mean the renewed terms already lock pricing for that period (the increase does not apply at renewal, so timing is `CONTRADICTED` and the outcome is `NO_CORRESPONDENCE`), or it may mean the renewed agreement includes the scheduled increase (timing `UNRESOLVED` or, with evidence, `SUPPORTED`). Which reading applies depends on facts about Harbor's agreement and on how the buyer meant "when our agreement renews". Neither the software nor AI may choose that reading; interpretation of the buyer's timing language stays with the human. Once the human records the timing finding, with evidence and a reason, the outcome is deterministic under Section 11.2:

- timing `CONTRADICTED` → `NO_CORRESPONDENCE`;
- timing `UNRESOLVED` → `POSSIBLE_CORRESPONDENCE`;
- `HUMAN_VERIFIED_CORRESPONDENCE` is unreachable while timing is `UNRESOLVED` or `CONTRADICTED`. It would require timing `SUPPORTED` by accepted evidence that the increase applies at Harbor's renewal.

**Case G explained.** A seller's unsourced belief is not external evidence. It may be a reason to look for an Authorized Source; it is not a substitute for one.

**Revalidation variant (any case).** If, while reviewing any Harbor case, evidence shows Harbor's agreement has already renewed without the condition being met, or that Harbor no longer uses the supplier, `BAEC_REVALIDATION_REQUIRED` is raised. No new `HUMAN_VERIFIED_CORRESPONDENCE` outcome is possible until Engine 1 reports the BAEC current again.

---

## 18. Engine 1 / Engine 2 Boundary

| Responsibility | Engine 1 (released, frozen) | Engine 2 (this contract) |
|---|---|---|
| Interaction capture | Yes | No |
| AI extraction proposal | Yes | No |
| Human evidence verification for a BAEC | Yes | No |
| BAEC criteria review (C1 to C4) | Yes | No |
| Normalized condition | Yes (human-reviewed) | Reads only |
| Deterministic BAEC classification | Yes | No |
| Authorization and confirmation through the separate executor | Yes | No |
| BAEC currentness and revalidation | Yes (sole authority) | Reads currentness; raises `BAEC_REVALIDATION_REQUIRED`; never alters validity |
| Action Review | No | No. Future, separate boundary (Section 12) |
| Account-state transitions | Yes, under existing human-authorized rules | No |
| Accept an authoritative confirmed BAEC | | Yes |
| Create a Monitoring Plan | | Yes (human-authorized) |
| Observe Authorized Sources and capture Observations | | Yes |
| Identify Signal Candidates | | Yes |
| Propose correspondence (AI_INFERENCE) | | Yes |
| Human Correspondence Review | | Yes |
| Record a deterministic correspondence outcome | | Yes |
| Seller action | No | No. Stops before it. |

Engine 2 never reinterprets an unconfirmed Engine 1 draft as authoritative, never writes to Engine 1 records, and never changes Engine 1 behavior. Engine 1 remains the sole authority for BAEC confirmation.

---

## 19. Non-Goals

Engine 2 does not and will not provide:

- purchase prediction;
- propensity scoring;
- generic intent scoring;
- lead scoring;
- automatic opportunity creation;
- automatic account-state movement;
- autonomous seller outreach;
- autonomous persuasion;
- scraping of arbitrary personal data;
- monitoring of unauthorized sources;
- a replacement for revalidating the condition with the buyer;
- any claim of causal sales impact;
- empirical validation of BAEC theory.

---

## 20. Stage A Acceptance Criteria

Stage A is complete when this contract is approved and each statement below is true of it. The audit column records the state at lock (October 8, 2026).

| # | Criterion | Where (sections of this contract) | Audit at lock |
|---|---|---|---|
| 1 | No claim that correspondence equals evaluation | §8.1, §11.1, §13, §15 | Met |
| 2 | No claim that correspondence equals purchase intent | §8.1, §11.1, §15 | Met |
| 3 | No automatic opportunity transition | §11.4, §12, §13 | Met |
| 4 | No automatic outreach | §12 | Met |
| 5 | AI remains inference | §9, §11.2 | Met |
| 6 | Human authority is explicit | §5, §10, §11.2, §12 | Met |
| 7 | Source authorization is explicit | §6, §7.1 | Met |
| 8 | Provenance is mandatory | §6, §7.1, §14 | Met |
| 9 | Threshold precision is preserved | §4.2, §5, §9, §16 | Met |
| 10 | Comparator precision is preserved | §8.2, §16, §17 (Case E) | Met |
| 11 | Timing precision is preserved | §5, §16, §17 (Case F) | Met |
| 12 | Wrong-entity evidence cannot satisfy correspondence | §8.2, §11.2, §16, §17 (Case D) | Met |
| 13 | Stale and retracted evidence are handled | §7.1, §16, §17 (Case H) | Met |
| 14 | Engine 1 remains authoritative for BAEC confirmation | §4.2, §18 | Met |
| 15 | Engine 1 code remains unchanged | Stage A changes documentation only | Met |
| 16 | No scores, probabilities, or confidence values | §8.3, §11.3, §15 | Met |
| 17 | Every IMPLEMENTATION concept is labeled and not presented as a manuscript construct | §2.2, §3 | Met |
| 18 | BAEC currentness is owned by Engine 1 | §4.3, §10.1, §18 | Met |
| 19 | Stale BAEC handling fails closed | §4.3, §11.2, §16 | Met |
| 20 | Raw source evidence remains distinct from derived arithmetic | §7.1, §7.3 | Met |
| 21 | Deterministic calculations are reproducible | §7.3 | Met |
| 22 | `HUMAN_VERIFIED_CORRESPONDENCE` cannot be confused with BAEC revalidation | §10.1, §11.1 | Met |
| 23 | Action Review is separate and future | §10.1, §12, §18 | Met |
| 24 | Case F outcome is deterministic after the human timing finding | §17 | Met |
| 25 | CEE definition is DEFINITIONAL and CEE propositions are PROPOSED, per the Research Contract | §3 | Met |

Terminology audit. Correspondence, Monitoring Plan, Authorized Source, Observation, Derived Measurement, Signal Candidate, Correspondence Candidate, AI Correspondence Assessment, Human Correspondence Review, the three correspondence outcomes, and `BAEC_REVALIDATION_REQUIRED` are IMPLEMENTATION terms. Action Review is a future IMPLEMENTATION boundary. Manuscript-supported monitoring (preserving the condition, watching legitimate and authorized sources for possibly consistent evidence, and human revalidation) is MANAGERIAL. No Engine 2 term is promoted into theory.

Approval of Stage A does not approve any implementation. Each later Engine 2 stage requires its own approved design before code is written (CLAUDE.md: do not build ahead of the approved phase).
