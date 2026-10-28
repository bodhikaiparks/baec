# CLAUDE.md

Rules for AI-assisted work in this repository. Keep this file short.

## Before changing anything

- Read `docs/RESEARCH_CONTRACT.md` before changing BAEC logic. It is the approved statement of the theory rules; the manuscript overrides it, and it overrides code.
- Explain large structural changes before making them. Do not silently rewrite working code.

## Theory rules

- Do not redefine BAEC or CEE.
- Do not add account states. There are exactly three; an unclassified account is `None`.
- Keep BAEC validity (the four criteria) separate from quality judgments (specificity, plausibility, addressability, and representational authority).
- Do not create scores, grades, probabilities, or confidence percentages for BAECs or signal matches.
- Do not invent or alter buyer statements, thresholds, dates, or counts. Store thresholds exactly as stated.
- Label rules by source: definitional, proposed, managerial, or implementation. Never present an implementation choice as a research finding.

## Safety rules

- Do not weaken human-confirmation rules.
- `HumanAuthorization` in the domain layer is a requirement, not the security boundary. Never treat a model-supplied value as proof of human approval.
- A signal alone never moves an account to Active Opportunity.
- Do not add automatic outreach of any kind.
- Do not treat model output as authoritative without application validation. Fail closed on invalid output.
- Do not introduce real customer, employer, or account data. Synthetic data only.
- Never put API keys or secrets in code, docs, commits, or logs.

## Engineering rules

- Every meaningful behavioral bug fix requires a regression test.
- Run the relevant tests before claiming a change is complete, and report the actual result.
- Clearly distinguish implemented, tested, experimental, and planned functionality.
- Do not create files or abstractions that have no current use.
- Do not build ahead of the approved phase.

## Claims

Never claim scientific validation, purchase prediction, sales lift, production security, or 100% accuracy. Tests are software tests and synthetic evaluation cases, not evidence for the theory.

## Environment

Mac, zsh. Quote terminal paths that contain spaces. Run tests with `pytest`.
