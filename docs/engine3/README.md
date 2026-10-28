# BAEC Engine 3: Re-engagement Preparation

Version 1.0

**Live demo:** https://baec-engine3.streamlit.app

BAEC Engine 3 begins after Engine 2 records a human-verified correspondence.

Its purpose is to convert an eligible, human-reviewed correspondence into evidence-grounded preparation for a salesperson without treating correspondence as buyer intent or authorizing contact.

## Core question

Can a human-reviewed correspondence record be converted into useful, evidence-grounded re-engagement preparation without treating correspondence as buyer intent, exposing internal monitoring evidence, or authorizing contact?

## Public research demo

The public demo uses the synthetic Alder Ridge Clinics / Cobalt Imaging Supply scenario.

The BAEC workflow is:

**Remember → Monitor → Recognize → Prepare**

Engine 1 preserves what the buyer said would make evaluation worthwhile.

Engine 2 monitors for evidence that may correspond to that condition and requires a human to verify the connection.

Engine 3 begins only after an eligible `HUMAN_VERIFIED_CORRESPONDENCE` and a separate human Preparation Request.

Engine 3 prepares:

- Internal Why-Now Summary
- Follow-up Email Draft
- Call Opening Draft
- Discovery Question Set

Verified correspondence can justify preparation for a human conversation. It does not establish that contact should occur, that the buyer is evaluating, or that the buyer intends to purchase.

**BAEC prepares the conversation. The salesperson decides whether to have it.**

## Research materials

[Research and Technical Brief PDF](./BAEC_Engine_3_Research_and_Technical_Brief.pdf)

[Re-engagement Preparation Domain Contract](./ENGINE3_REENGAGEMENT_PREPARATION_DOMAIN_CONTRACT.md)

[Stage D Deterministic Core Implementation Report](./ENGINE3_STAGE_D_IMPLEMENTATION_REPORT.md)

[Stage E Offline Evaluation Report](./ENGINE3_STAGE_E_OFFLINE_EVALUATION_REPORT.md)

[Stage E Implementation Manifest](./ENGINE3_STAGE_E_IMPLEMENTATION_MANIFEST.json)

[Stage F Semantic Reliability Experiment Design](./ENGINE3_STAGE_F_SEMANTIC_RELIABILITY_EXPERIMENT_DESIGN.md)

## Research status

Engine 3 Version 1.0 is a research prototype using synthetic data.

Stage D implements the deterministic Engine 3 validation boundary.

Stage E is locked under a limited claim. It demonstrates that prerecorded synthetic proposals can be constrained to the Engine 3 structural envelope and routed through the deterministic validator.

Stage E does not independently establish the semantic accuracy of model-supplied annotations. Human Preparation Review remains the semantic control over the prose.

No live-model execution is part of the public demo.

Engine 3 does not authorize outreach, send email, place calls, create opportunities, change account state, or infer buyer intent.

The system has not been empirically validated as a sales-performance system.
