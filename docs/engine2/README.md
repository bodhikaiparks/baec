# BAEC Engine 2: Monitoring + Correspondence

Version 1.0

**Live demo:** https://baec-engine2.streamlit.app

BAEC Engine 2 begins after Engine 1 has confirmed a Buyer-Articulated Evaluation Contingency.

Its purpose is to preserve that buyer-defined condition after the original conversation and monitor human-authorized sources for new evidence that may correspond to it.

## Core question

Has new evidence appeared that may correspond to what the buyer previously said would make evaluation worthwhile?

## Public research demo

The public demo uses a fixed synthetic Alder Ridge Clinics / Cobalt Imaging Supply scenario.

A buyer previously states that a price increase above 8% at the July renewal would cause the contract to be put out for review.

Engine 2 preserves that condition, monitors authorized evidence, and brings potentially corresponding evidence to a human reviewer.

The human review can derive one of three locked outcomes:

- `NO_CORRESPONDENCE`
- `POSSIBLE_CORRESPONDENCE`
- `HUMAN_VERIFIED_CORRESPONDENCE`

Correspondence narrows human attention. It does not establish that the buyer is currently evaluating, intends to purchase, or that an opportunity exists.

## Research materials

[Research and Technical Brief PDF](./BAEC_Engine_2_Research_and_Technical_Brief.pdf)

[Technical Research Brief](./ENGINE2_TECHNICAL_RESEARCH_BRIEF.md)

[Release Candidate Record](./ENGINE2_RELEASE_CANDIDATE_RECORD.md)

[Reproducibility Record](./ENGINE2_RELEASE_CANDIDATE_REPRODUCIBILITY.json)

## Research status

Engine 2 v1.0 is a research prototype using synthetic data.

It has not been empirically validated as a sales-performance system and is not production software.

Stage H-B has not been executed. The optional live-model experiment remains paused and is not part of Engine 2 v1.0 or the public demo.
