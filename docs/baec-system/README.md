# BAEC Trigger Intelligence: System Research and Technical Overview

**Research program:** Buyer-Articulated Evaluation Contingencies in B2B Sales Encounters
**System:** BAEC Trigger Intelligence
**Integrated workflow:** Remember → Monitor → Recognize → Prepare
**Status:** Public research prototype using synthetic data
**Author:** Bodhi Parks

## 1. Research problem

B2B sales processes often focus on buyers who are already evaluating alternatives, expressing dissatisfaction, or showing signals interpreted as purchase intent.

The BAEC research program begins from a different situation:

**What should a salesperson preserve when a qualified buyer is not evaluating today, but can articulate what would make evaluation worthwhile later?**

The conceptual framework is developed in the unpublished working paper:

*When the Buyer Is Not In-Market: Buyer-Articulated Evaluation Contingencies in B2B Sales Encounters.*

The software in this repository operationalizes selected constructs and managerial implications from that framework. Software behavior on synthetic data is not empirical validation of the theory.

## 2. Buyer-Articulated Evaluation Contingency

A **Buyer-Articulated Evaluation Contingency (BAEC)** is an explicit buyer-generated statement, made while the buyer is not currently evaluating relevant alternatives, that identifies a prospective condition the buyer expects would make initiating or reopening evaluation worthwhile.

The construct is intentionally narrower than a trigger, lead score, intent signal, objection, or current buying need.

A BAEC requires four elements:

1. **Present non-evaluation** — the buyer is not currently evaluating relevant alternatives.
2. **Prospective condition** — the relevant condition lies in the future rather than describing an already realized event.
3. **Buyer articulation** — the core condition comes from the buyer rather than being supplied by the seller.
4. **Evaluation linkage** — the buyer links the condition to initiating or reopening evaluation.

A confirmed BAEC does not establish purchase intent and does not automatically create an active opportunity.

## 3. Contingency Elicitation Exchange

A **Contingency Elicitation Exchange (CEE)** is a salesperson conversational behavior used after qualified present non-evaluation.

Rather than immediately supplying another seller-generated reason to change, the salesperson invites the buyer to identify a prospective condition that would make initiating or reopening evaluation worthwhile.

CEE is one possible route to a BAEC. A buyer may also articulate a contingency spontaneously.

The wording of a question is not itself the construct. The important boundary is that the buyer supplies the core prospective condition.

## 4. Account-management states

The BAEC framework distinguishes three account-management states:

| State | Meaning |
|---|---|
| **Active Opportunity** | The buyer is currently evaluating relevant alternatives. |
| **Conditionally Dormant** | The buyer is not presently evaluating and has articulated a plausible future condition under which evaluation may become worthwhile. |
| **No Plausible Path** | The buyer is not presently evaluating and available information does not support a plausible seller-relevant route to future evaluation. |

An account may also be **unclassified** before a human has made an account-state decision. Unclassified is the absence of a state, not a fourth state.

Account state remains deliberately separate from the engine workflow. A confirmed BAEC, monitored signal, correspondence result, or prepared draft does not automatically change account state.

## 5. System architecture

The integrated system follows four human-readable stages:

**Remember → Monitor → Recognize → Prepare**

### Engine 1 — Capture + Verification

**Stage:** Remember

Engine 1 preserves what the buyer actually said and separates buyer evidence from seller interpretation and AI inference.

Its responsibilities include:

- preserving exact interaction evidence;
- recording the four BAEC criteria;
- preserving buyer-defined thresholds, timing, recurrence, and other stringency information;
- distinguishing buyer-generated from seller-seeded conditions;
- requiring explicit human review;
- deterministically classifying the record;
- requiring a separate human authorization path before confirmation;
- maintaining account-state decisions as a separate human-authorized process.

Engine 1 does not predict who will buy.

### Engine 2 — Monitoring + Correspondence

**Stages:** Monitor and Recognize

Engine 2 begins only from a confirmed Engine 1 BAEC.

Its responsibilities include:

- constructing a human-authorized monitoring plan;
- limiting monitoring to authorized sources;
- preserving exact observations and provenance;
- preserving corrections, supersessions, and retractions instead of deleting history;
- deriving measurements from recorded evidence using deterministic arithmetic;
- generating correspondence candidates for human review;
- requiring explicit human findings on correspondence dimensions and evidence sufficiency;
- deriving the final correspondence outcome deterministically from those human findings.

Engine 2 has three outcomes:

- `NO_CORRESPONDENCE`
- `POSSIBLE_CORRESPONDENCE`
- `HUMAN_VERIFIED_CORRESPONDENCE`

**Correspondence narrows human attention. It does not establish that the buyer is currently evaluating, intends to purchase, or that an opportunity exists.**

### Engine 3 — Re-engagement Preparation

**Stage:** Prepare

Engine 3 begins only from eligible human-verified correspondence and a separate human Preparation Request.

Its responsibilities include:

- reconstructing the exact upstream Engine 1 and Engine 2 lineage;
- refusing preparation when evidence, currentness, correspondence, or request context has changed;
- enforcing human disclosure choices;
- distinguishing internal evidence from facts that may be referenced externally;
- validating prepared statements against recorded grounding;
- rejecting invented thresholds, dates, urgency, intent, opportunity status, unsupported claims, or unauthorized source disclosure;
- producing preparation only for human review.

The integrated Console currently uses a deterministic template rather than a live language model to create the prepared text. Every generated artifact is still submitted to the released Engine 3 validator.

Engine 3 can prepare:

- Internal Why-Now Summary
- Follow-up Email Draft
- Call Opening Draft
- Discovery Question Set

**BAEC prepares the conversation. The salesperson decides whether to have it.**

## 6. Integrated synthetic demonstration

The unified public Console uses the fictional **Alder Ridge Clinics / Cobalt Imaging Supply** scenario.

The buyer states:

> “If Cobalt raises the price of our imaging service contract by more than 8% at the July renewal, we would put the contract out for review.”

The completed scenario demonstrates the complete system chain.

### Remember

The buyer is recorded as not currently evaluating other providers.

The buyer-defined contingency is captured and human-confirmed:

- condition: Cobalt raises the service-contract price;
- threshold: **more than 8%**;
- timing: **July renewal**;
- evaluation linkage: the contract would be put out for review.

A separate human dormancy judgment determines that the condition is plausible and addressable, and the account is moved to **Conditionally Dormant**.

### Monitor

A human-authorized monitoring plan watches an authorized supplier renewal-notice source.

The first notice reports:

- previous annual fee: USD 40,000;
- reported new fee: USD 44,800;
- derived increase: **12.00%**.

A later correction states that the original amount was incorrect and changes the new annual fee to USD 43,600.

The system preserves the original 12% observation historically but marks it superseded.

The current usable measurement becomes:

**9.00%**

### Recognize

The corrected 9% measurement remains above the buyer's stated threshold of more than 8%.

A human reviewer evaluates the required correspondence dimensions and evidence sufficiency.

The deterministic Engine 2 outcome is:

`HUMAN_VERIFIED_CORRESPONDENCE`

That result does not change the account from Conditionally Dormant and does not establish buyer intent.

### Prepare

A separate human Preparation Request authorizes drafting only.

Engine 3 receives the human-verified correspondence, current evidence, buyer condition, threshold, timing, and disclosure decisions.

The superseded 12% evidence is not used as current grounding.

Four prepared artifacts are created and validated for human use.

Nothing is sent to the buyer.

## 7. Human authority model

The system deliberately separates evidence, machine processing, and human authority.

| Layer | Authority |
|---|---|
| Recorded buyer evidence | Establishes what was captured from the interaction |
| AI or machine proposal | Advisory only |
| Human BAEC review | Records the human's findings |
| Deterministic classification | Applies the locked rule to those findings |
| Human confirmation | Authorizes creation of a confirmed BAEC |
| Monitoring evidence | Records what an authorized source contained |
| Correspondence candidate | Indicates that evidence warrants review |
| Human correspondence review | Records the human's findings on correspondence |
| Correspondence classifier | Derives the narrow correspondence outcome |
| Preparation Request | Authorizes drafting only |
| Engine 3 validation | Determines whether prepared material respects the recorded evidence and boundaries |
| Human artifact review | Determines whether prepared text is acceptable for human use |

No engine independently authorizes seller outreach.

## 8. Evidence and provenance

Evidence preservation is a central design principle across all three engines.

The system distinguishes:

- buyer facts;
- seller observations;
- AI inference;
- external evidence;
- derived measurements;
- human findings;
- deterministic classifications;
- prepared artifacts;
- human edits and reviews.

Corrections do not erase earlier evidence.

Superseded observations remain in the ledger while becoming unavailable for current downstream reasoning.

This allows the system to preserve both **what was known at the time** and **what is currently usable**.

## 9. Research and product boundaries

The BAEC system does not:

- predict purchase likelihood;
- infer hidden buyer intent;
- equate a future condition with current demand;
- treat a signal as an opportunity;
- allow AI output to become a human finding;
- automatically move an account into Active Opportunity;
- authorize outreach from a correspondence result;
- send email;
- place calls;
- schedule buyer contact;
- write to a CRM;
- make autonomous commercial decisions.

The public Console is read-only and uses synthetic data.

## 10. AI research boundary

The broader repository contains controlled AI research and evaluation infrastructure developed during the individual engine programs.

Those experiments are deliberately separated from the integrated public Console.

The Console makes no live model call.

Engine 2's optional Stage H-B live-model experiment has not been executed and is not part of Engine 2 Version 1.0.

Engine 3 includes offline proposal and semantic-reliability research artifacts, but those experimental tracks are not presented as validated product capabilities.

The distinction is intentional:

**research experimentation does not silently become authoritative product behavior.**

## 11. Verification and reproducibility

The integrated Console is built over the released engine boundaries rather than replacing them.

At the current public-system release gate:

- Engine 1 schema version: **7**
- Engine 2 authoritative schema: **Version 1, 30 tables**
- Engine 2 AI audit schema: **Version 1, 7 tables**
- Engine 2 reproducibility demo digest:
  `310f570874c1efb73eea33ff3830844375389fc8f08a5069554c06952cc4f45e`
- Engine 2 expected release outcome:
  `HUMAN_VERIFIED_CORRESPONDENCE`
- full repository suite: **7,576 passed, 2 live tests deselected**
- release verification: **23 of 23 checks passed**

These results establish software behavior against the repository's tests and frozen synthetic artifacts.

They do not establish empirical validity of BAEC or CEE.

## 12. Individual public engine releases

### Engine 1 — Capture + Verification

Public demo:

https://baec-engine1.streamlit.app

Research and Technical Brief:

`public_demo_assets/BAEC_Engine_1_Research_and_Technical_Brief.pdf`

### Engine 2 — Monitoring + Correspondence

Public demo:

https://baec-engine2.streamlit.app

Research and Technical Brief:

`docs/engine2/BAEC_Engine_2_Research_and_Technical_Brief.pdf`

Technical research materials:

`docs/engine2/`

### Engine 3 — Re-engagement Preparation

Public demo:

https://baec-engine3.streamlit.app

Research and Technical Brief:

`docs/engine3-public/BAEC_Engine_3_Research_and_Technical_Brief.pdf`

Full research materials:

`docs/engine3/`

## 13. Integrated BAEC Console

The BAEC Console is the public integrated demonstration of the three-engine system.

It provides:

- Research & System Overview
- Dashboard
- Accounts
- Review Queue
- Preparation
- Activity
- full account workspace
- Remember
- Monitor
- Recognize
- Prepare
- Timeline
- Account state

The public deployment exposes the full workflow and forms but locks record-creating and state-changing actions.

Each visitor receives an isolated synthetic runtime.

## 14. Research status

BAEC and CEE are conceptual constructs originating in an unpublished working paper.

The three engines and the integrated Console are research software prototypes.

The software demonstrates that the proposed information and authority boundaries can be implemented and tested on synthetic data.

It does **not** demonstrate that:

- BAEC predicts future evaluation;
- CEE improves sales conversations;
- BAEC improves conversion or revenue;
- BAEC improves forecasting accuracy;
- buyers respond better to BAEC-informed outreach;
- the system improves commercial outcomes.

Those are empirical questions for future research.

## 15. Research direction

The next stage of the program is not another engine.

The priority is empirical evaluation of the underlying constructs and managerial implications.

Potential research directions include:

- construct-validity work around BAEC and CEE;
- expert review of construct boundaries;
- observational studies of buyer-articulated contingencies;
- reliability studies of human classification;
- field research on conditionally dormant accounts;
- controlled evaluation of BAEC-informed follow-up;
- investigation of when preserving buyer-defined contingencies improves account management.

The public software should be understood as a reproducible research apparatus and conceptual demonstration supporting that future work, not as evidence that those future empirical claims are already true.

---

**BAEC Trigger Intelligence**
*Remember what the buyer said mattered, then watch for it.*
