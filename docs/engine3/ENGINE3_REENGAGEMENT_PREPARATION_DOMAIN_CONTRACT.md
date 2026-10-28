# BAEC Engine 3: Re-engagement Preparation Domain Contract

**Project:** BAEC Trigger Intelligence  
**Engine:** BAEC Engine 3: Re-engagement Preparation  
**Stage:** A (domain contract only)  
**Status:** Stage A LOCKED (October 14, 2026). Owner approved. Nothing in this document is implemented.  
**Depends on:** BAEC Engine 1 v1.0 (tag `baec-engine1-v1.0`, commit `da7cde96cf80e2cbc7fb7d27fc1713fae4f8b567`) and BAEC Engine 2 v1.0 (tag `baec-engine2-v1.0`, commit `b820778108f7c9432689755c4b255f9082d24350`). Both released engines remain frozen.

Source authority, in descending order: the manuscript; `docs/RESEARCH_CONTRACT.md`; locked Engine 1 semantics; locked Engine 2 semantics and `docs/engine2/ENGINE2_MONITORING_CORRESPONDENCE_DOMAIN_CONTRACT.md`; released Engine 1 and Engine 2 research briefs; this contract. If this contract conflicts with a higher source, this contract is wrong and must be corrected.

Engine 3 principle: **Verified correspondence can justify preparation for a human conversation. It does not establish that contact should occur, that the buyer is evaluating, or that the buyer intends to purchase.**

---

## 1. Purpose and scope

Engine 3 is a preparation layer. It may turn one eligible, human-reviewed Engine 2 correspondence record into evidence-grounded communication preparation after a separate human request to prepare.

Core question:

> Can a human-reviewed correspondence record be converted into useful, evidence-grounded re-engagement preparation without treating correspondence as buyer intent, exposing internal monitoring evidence, or authorizing contact?

In scope:
- read-only receipt of one eligible Engine 2 result;
- separate human Preparation Request;
- immutable Communication Grounding Bundle;
- explicit human disclosure decisions for buyer-facing facts;
- advisory AI preparation for a follow-up email, call opener, discovery questions, and internal why-now summary;
- claim-to-evidence grounding;
- deterministic refusal of buyer-facing claims that use non-permitted evidence;
- human accept/edit/reject review;
- hard stop before seller action.

Out of scope:
- deciding the buyer is evaluating;
- purchase/switching intent inference;
- deciding outreach is authorized;
- sending email or messages;
- placing calls;
- CRM writes, sequences, campaigns, tasks, meeting booking;
- opportunity creation;
- account-state changes;
- new monitoring or source collection;
- modifying Engine 1 or Engine 2;
- autonomous persuasion;
- personal/sensitive-data targeting.

Stage A creates this document only. No code, schema, migration, prompt, connector, model call, interface, or test is approved by Stage A.

---
## 2. Research and authority basis

Relevant governing rules include RC-04, RC-26, RC-27, RC-28, RC-29, RC-30, RC-31, RC-32 and RC-33.

Engine 3 is not a manuscript construct. The manuscript does not define AI-written follow-up emails, call scripts, grounding bundles, disclosure dispositions, claim ledgers, or preparation artifacts. Those are IMPLEMENTATION concepts.

Engine 3 therefore makes no claim that:
- a verified correspondence result means re-engagement should occur;
- a prepared draft means outreach is appropriate;
- AI-written preparation improves response, evaluation, conversion, revenue, or buyer outcomes;
- any Engine 3 software object is an empirical research finding.

Authority table:

| Concept | Authority |
|---|---|
| BAEC definition and C1-C4 | DEFINITIONAL |
| Evaluation activation distinct from purchase/switching | DEFINITIONAL |
| Buyer-specific monitoring | MANAGERIAL |
| Signal/correspondence is not opportunity | MANAGERIAL |
| Human reassessment before action | MANAGERIAL |
| BAEC staleness | MANAGERIAL |
| No automated outreach / governance | MANAGERIAL |
| Engine 2 correspondence outcomes | IMPLEMENTATION, locked Engine 2 |
| Engine 3 eligibility boundary | IMPLEMENTATION |
| Preparation Request | IMPLEMENTATION |
| Communication Grounding Bundle | IMPLEMENTATION |
| External Reference Disposition | IMPLEMENTATION |
| Claim Ledger | IMPLEMENTATION |
| AI Preparation Proposal | IMPLEMENTATION |
| Human Preparation Review | IMPLEMENTATION |
| Reviewed Preparation Artifact | IMPLEMENTATION |
| Outreach authorization | outside Engine 3 |

---
## 3. Engine 3 flow

```text
Confirmed BAEC (Engine 1)
  |
  v
Human-reviewed correspondence (Engine 2)
  |
  | eligible only when:
  | HUMAN_VERIFIED_CORRESPONDENCE
  | BAEC current
  | no BAEC_REVALIDATION_REQUIRED
  | review/evidence not superseded or retracted
  v
Read-only Engine 3 handoff
  |
  v
Preparation Request
(explicit human action; drafting only)
  |
  v
Communication Grounding Bundle
  |
  v
AI Preparation Proposal (AI_INFERENCE)
  |
  +--> Internal Why-Now Summary
  +--> Follow-up Email Draft
  +--> Call Opening Draft
  +--> Discovery Question Set
  |
  v
Claim + Disclosure Validation
  |
  v
Human Preparation Review
  |
  v
Reviewed Preparation Artifact
  |
  v
STOP

Reviewed preparation != outreach authorization
Reviewed preparation != buyer evaluating
Reviewed preparation != opportunity
Reviewed preparation != message sent
```

---
## 4. Input eligibility and read-only handoff

Engine 3 may begin only when every condition below is true:

1. Engine 2 outcome is `HUMAN_VERIFIED_CORRESPONDENCE`.
2. The review is authoritative under the locked Engine 2 review/persistence boundary.
3. The referenced Engine 1 BAEC is still current.
4. `BAEC_REVALIDATION_REQUIRED` is not active.
5. The Engine 2 review has not been superseded.
6. Required evidence has not been retracted, invalidated, or superseded in a way that makes the result stale.
7. Exact upstream lineage can be reconstructed fail closed.
8. A human explicitly requests preparation.

For V1, `POSSIBLE_CORRESPONDENCE` and `NO_CORRESPONDENCE` are ineligible for buyer-facing preparation.

Engine 3 reads only:
- BAEC id/account reference;
- exact buyer statement;
- authoritative structured condition fields;
- currentness reference;
- Engine 2 review id/outcome;
- exact human findings;
- accepted Observation and Derived Measurement references;
- exact usable evidence snapshot;
- source authorization metadata;
- supersession/retraction state;
- integrity/context digests.

Engine 3 never:
- reclassifies a BAEC;
- recomputes the correspondence outcome;
- upgrades POSSIBLE to VERIFIED;
- edits upstream records;
- queries arbitrary new sources;
- turns seller opinion into external evidence;
- broadens the buyer-defined condition.

Any upstream change invalidates the current Engine 3 context digest. A stale packet cannot be accepted as current preparation.

---
## 5. Preparation Request

A Preparation Request is a distinct human action authorizing drafting only.

Conceptual fields:
- `preparation_request_id`
- `engine2_review_id`
- `baec_id`
- `requested_by`
- `requested_at`
- `channel_scope`: EMAIL, CALL, or EMAIL_AND_CALL
- `preparation_goal`
- `audience_context`
- optional human tone preference
- `context_digest`
- status: REQUESTED, CANCELLED, SUPERSEDED

Allowed goals:
- check whether the previously stated condition is still relevant;
- ask whether the buyer wants to revisit the earlier discussion;
- ask whether anything changed;
- prepare to learn whether evaluation has actually reopened.

Disallowed goals:
- convince the buyer to switch because correspondence occurred;
- pressure the buyer;
- exploit adversity;
- tell the buyer they are now in-market;
- invent urgency;
- assume purchase intent.

A Preparation Request is not an Action Review, outreach authorization, send authorization, call authorization, campaign authorization, opportunity authorization, or account-state authorization.

---
## 6. Communication Grounding Bundle

The Communication Grounding Bundle is an immutable canonical snapshot for one Preparation Request.

Internal grounding may include:
- exact buyer statement;
- verified correspondence outcome;
- accepted human findings;
- accepted Observations;
- usable Derived Measurements;
- source identity/provenance;
- correction/supersession history;
- human-selected goal.

Buyer-facing grounding is a strict subset of internal grounding.

No fact enters buyer-facing grounding unless a human assigns an External Reference Disposition that permits it.

The bundle is canonicalized and hashed. The digest binds:
- exact BAEC;
- exact Engine 2 review;
- exact evidence snapshot;
- disclosure decisions;
- Preparation Request;
- any later approved seller-provided source material.

Every AI proposal and human review must reference the exact bundle digest.

---
## 7. External Reference Disposition

**Monitoring authorization and external communication permission are separate.**

Every fact or prior-buyer-statement reference that could appear in buyer-facing language receives one human-controlled disposition:

| Disposition | Meaning |
|---|---|
| `DIRECT_REFERENCE_ALLOWED` | Fact may be stated externally at the approved specificity. |
| `PARAPHRASE_ALLOWED` | Underlying fact may inform language, but exact wording/detail/source identity may not be exposed. |
| `INTERNAL_ONLY` | Seller-facing preparation only. |
| `UNKNOWN` | Permission not established; treated as internal-only for generation. |

Default: `UNKNOWN`.

Source identity permission is separate from fact permission. A seller may be permitted to mention that a condition appears relevant without being permitted to reveal how it was learned.

The buyer's prior statement also requires a human disposition before external reuse. Direct quote, paraphrase, internal-only, and unknown must remain distinguishable.

Adversity/sensitive events require explicit contextual human review before external reference. Missing review means no external use.

---
## 8. Version 1 outputs

### Internal Why-Now Summary
Seller-only. May use internal grounding. Must state:

> Verified correspondence does not establish that the buyer is currently evaluating, intends to purchase, or that an opportunity exists.

### Follow-up Email Draft
Buyer-facing. Must:
- use only buyer-facing permitted facts;
- reopen a conversation rather than declare buyer state;
- ask when current status is unknown;
- avoid purchase/switching claims;
- avoid revealing monitoring/source identity unless explicitly permitted.

### Call Opening Draft
Buyer-facing. Short, natural, evidence-grounded, question-led, and non-assumptive.

### Discovery Question Set
No more than five questions in V1. Questions test:
- whether the original condition is still relevant;
- what changed;
- whether evaluation actually reopened;
- whether reviewing alternatives would be useful.

A question may not smuggle in an unsupported premise.

---
## 9. AI Preparation Proposal

Every model-generated artifact has origin `AI_INFERENCE`.

The AI receives only the curated Grounding Bundle.

AI may:
- propose wording;
- create the why-now summary;
- draft email/call language;
- propose discovery questions;
- identify the exact permitted evidence it used;
- express uncertainty.

AI may not:
- decide outreach is appropriate;
- infer buyer evaluation or purchase intent;
- invent facts, dates, thresholds, amounts, relationships, events, product claims, or competitor claims;
- broaden the BAEC;
- change disclosure permissions;
- authorize contact;
- choose an unsupplied contact;
- use unrelated personal or sensitive information.

No AI output becomes authoritative merely because it validates structurally.

---
## 10. Communication Claim Ledger and deterministic refusal

Every AI-generated buyer-facing draft includes a structured Claim Ledger.

Claim types:

| Type | Rule |
|---|---|
| `FACTUAL_ASSERTION` | Must cite exact buyer-facing permitted grounding. |
| `PRIOR_BUYER_STATEMENT_REFERENCE` | Must cite authoritative buyer statement plus permitted disposition. |
| `QUESTION` | No citation only if there is no factual premise. Embedded premises must be grounded. |
| `COURTESY_OR_TRANSITION` | No citation required, but cannot imply buyer state, urgency, or event. |

Refuse before human review if:
- a factual claim lacks grounding;
- cited evidence is INTERNAL_ONLY or UNKNOWN;
- direct quotation is used where only paraphrase is allowed;
- source identity is exposed without permission;
- draft asserts current evaluation without independent buyer evidence;
- draft asserts purchase/switching intent;
- draft treats correspondence as opportunity;
- draft invents date, threshold, amount, role, event, product claim, or competitor claim;
- draft implies outreach was authorized;
- bundle digest is stale/mismatched.

The internal Why-Now Summary may use INTERNAL_ONLY evidence, but factual assertions still require provenance.

V1 does not permit invented seller product, pricing, clinical, regulatory, comparative, legal, or performance claims. Any later seller capability content must be separately human-approved source material.

---
## 11. Human Preparation Review

For each output, human choices are:
- `ACCEPT_AS_DRAFTED`
- `EDIT`
- `REJECT`

The human records:
- condition fidelity;
- factual accuracy;
- disclosure compliance;
- whether buyer-state/intent claims were avoided;
- contextual appropriateness;
- optional reason;
- reviewer identity/alias;
- review time;
- exact bundle/proposal digests.

Human-edited language is a new artifact with origin `HUMAN_EDITED`.

A final human review must attest:
- factual assertions are supported;
- externally stated facts are permitted;
- no unsupported evaluation/purchase/opportunity claim exists;
- no internal-only source or monitoring method is exposed.

A Reviewed Preparation Artifact means only:

> A human reviewed this preparation material against the recorded grounding and disclosure context.

It does not mean the buyer should be contacted, was contacted, is evaluating, intends to purchase, or that an opportunity exists.

---
## 12. Action and account-state boundary

Engine 3 stops before seller action.

No Engine 3 operation may:
- send email/message;
- create an external mailbox draft;
- place/initiate a call;
- create CRM task/activity;
- enroll a contact in a sequence/campaign;
- create/change opportunity;
- change account state;
- schedule a meeting;
- notify the buyer.

A Reviewed Preparation Artifact is not outreach authorization.

A future Outreach/Action Authorization capability, if ever built, requires a separate approved domain contract and its own explicit human authority. It must remain separate from Engine 2 correspondence review, Engine 3 Preparation Request, and Engine 3 Human Preparation Review.

Engine 3 performs no account-state transitions.

`HUMAN_VERIFIED_CORRESPONDENCE` does not make an account `ACTIVE_OPPORTUNITY`. A Reviewed Preparation Artifact does not make an account `ACTIVE_OPPORTUNITY`. Existing Engine 1 rules remain the only authoritative path for account state.

---
## 13. Provenance, prohibited claims, and fail-closed cases

Engine 3 later persistence must preserve:
- upstream BAEC/review references;
- evidence snapshot;
- disclosure decisions and human actor;
- Preparation Request;
- canonical bundle and digest;
- exact AI request/response and provider/model provenance;
- Claim Ledger;
- validation refusals;
- Human Preparation Reviews;
- human edits as distinct artifacts;
- supersessions;
- Reviewed Preparation Artifact.

Prohibited claims:
- correspondence means buyer is evaluating/in-market;
- buyer intends to buy/switch;
- preparation equals opportunity;
- preparation authorizes contact;
- AI knows best contact timing;
- a monitored source may be mentioned merely because monitoring was authorized;
- matching evidence proves the old BAEC is still valid;
- Engine 3 improves sales outcomes without empirical evidence;
- software/synthetic tests validate BAEC theory.

No propensity score, intent score, urgency score, lead score, purchase probability, or confidence percentage.

Fail closed:
- POSSIBLE/NO correspondence -> ineligible;
- stale/revalidation-required BAEC -> ineligible;
- superseded review/evidence -> rebuild;
- missing disclosure -> UNKNOWN -> no external use;
- AI reveals monitoring method -> refuse unless permitted;
- AI says buyer is evaluating/ready to switch -> refuse;
- AI invents urgency/product claim -> refuse;
- unsupported premise in a question -> refuse/rewrite;
- upstream context changes -> artifact historical/stale for current use.

---
## 14. Alder Ridge / Cobalt Stage A reference

Synthetic buyer statement:

> "If Cobalt raises the price of our imaging service contract by more than 8% at the July renewal, we would put the contract out for review."

Engine 2 context:
- current usable measurement 9.00%;
- corrected contract-specific July renewal evidence;
- earlier 12.00% retained as invalidated history;
- `HUMAN_VERIFIED_CORRESPONDENCE`.

This still does not establish current evaluation.

Example disclosure choices:
- buyer statement: PARAPHRASE_ALLOWED
- >8% threshold: DIRECT_REFERENCE_ALLOWED
- July renewal context: DIRECT_REFERENCE_ALLOWED
- exact 9.00% measurement: PARAPHRASE_ALLOWED
- exact forwarded notice/source path: INTERNAL_ONLY
- invalidated 12.00% figure: INTERNAL_ONLY

Allowed internal summary:

> The buyer previously said that a price increase above 8% at the July renewal would make putting the contract out for review worthwhile. Engine 2 later recorded human-verified correspondence using corrected renewal evidence. The current usable measurement is 9.00%. This does not establish that the buyer is currently evaluating. The next conversation should test whether the earlier condition is still relevant and whether evaluation has actually reopened.

Allowed buyer-facing pattern:

> When we last spoke, you mentioned that a price increase above 8% around the July renewal could make it worth revisiting the contract. I wanted to reconnect because that condition may now be relevant. Has anything changed on your side, and would it be useful to revisit the conversation?

Disallowed:

> Your contract went up 9%, so I know you're now looking to switch. Let's schedule a demo this week.

The disallowed version asserts buyer state, converts correspondence into intent, and invents urgency.

---
## 15. Engine boundaries

| Responsibility | Engine 1 | Engine 2 | Engine 3 |
|---|---|---|---|
| Capture buyer interaction | Yes | No | No |
| Verify/classify/confirm BAEC | Yes | No | No |
| Own BAEC currentness | Yes | Reads / raises revalidation need | Reads |
| Monitor authorized sources | No | Yes | No |
| Capture external evidence | No | Yes | No |
| Human correspondence review | No | Yes | No |
| Deterministic correspondence outcome | No | Yes | No |
| Human request to prepare communication | No | No | Yes |
| Human disclosure decision | No | No | Yes |
| Build grounding bundle | No | No | Yes |
| AI draft email/call/questions | No | Optional correspondence AI only | Yes, advisory |
| Human review of preparation | No | No | Yes |
| Send email / place call | No | No | No |
| Create opportunity | Existing locked path only | No | No |
| Change account state | Yes, under locked rules | No | No |
| Autonomous outreach | No | No | No |

Released Engine 1 and Engine 2 remain frozen. Engine 3 integrates through read-only handoffs only.

---
## 16. Stage A acceptance criteria

Stage A is locked by owner approval. The acceptance criteria at lock are:

1. V1 starts only from `HUMAN_VERIFIED_CORRESPONDENCE`.
2. Engine 1 remains authority for BAEC/currentness.
3. Engine 2 remains authority for correspondence.
4. Engine 1 and Engine 2 released code remain unchanged.
5. Preparation requires a separate human action.
6. Preparation authorization is not outreach authorization.
7. Monitoring permission is separate from disclosure permission.
8. Unknown disclosure fails closed.
9. AI cannot alter disclosure status.
10. AI remains `AI_INFERENCE`.
11. Every AI factual claim is grounded.
12. Buyer-facing facts require external-use permission.
13. Internal-only evidence cannot leak externally.
14. Source identity requires separate permission.
15. Buyer-statement reuse requires human permission.
16. Drafts cannot claim current evaluation.
17. Drafts cannot claim purchase/switching intent.
18. Drafts cannot create opportunity status.
19. Human edits remain distinct from AI output.
20. Context changes stale the packet.
21. No automatic send/call/CRM action.
22. No account-state transition.
23. No scores/probabilities/intent values.
24. Sensitive/adversity cases require contextual human review.
25. No implementation term is presented as a manuscript construct.
26. Stage A changes documentation only.
27. Later stages require separate approval.
28. Synthetic/software tests are never described as empirical validation.

Approval of Stage A does not approve implementation.

## 17. Changing this contract

Changes require explicit project-owner approval.

Any proposal to allow automatic outreach, infer intent from correspondence, merge preparation review with outreach authorization, weaken disclosure controls, permit ungrounded factual claims, or write into Engine 1/Engine 2 authoritative records requires a new domain review and cannot be introduced as an implementation detail.
