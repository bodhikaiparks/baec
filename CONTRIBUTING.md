# Contributing

Thanks for your interest in BAEC. Please keep changes small and focused, and open an issue first for anything
structural.

## Setup

Python 3.12 or newer.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt -r requirements-dev.txt
./scripts/start_demo.sh          # optional: the local synthetic demo on http://localhost:8501
```

## Tests

```bash
.venv/bin/python -m pytest                     # engines, domain, AI boundaries, research harness
.venv/bin/python -m pytest baec_console/tests  # Console (SQLite)
```

The PostgreSQL suites (`baec_console/tests_postgres`) and the deployment suites (`deploy/tests`) need Docker and
`requirements-postgres.txt`. See `scripts/postgres_test_db.sh`. Live Claude evaluations are opt-in and never needed
for a contribution.

Run the relevant tests before you open a pull request, and paste the actual result. Every behavioral bug fix needs
a regression test.

## Coding expectations

- Read [`docs/RESEARCH_CONTRACT.md`](docs/RESEARCH_CONTRACT.md) before you change any BAEC logic. Do not redefine
  BAEC or CEE, and do not add account states: there are exactly three, and an unclassified account is `None`.
- Keep BAEC validity (the four criteria) separate from quality judgments. Do not add scores, grades,
  probabilities, or confidence percentages.
- Store buyer statements, thresholds, dates, and counts exactly as stated. Never invent or alter them.
- Treat model output as a proposal. Validate it in the application, and fail closed on invalid output.
- Match the style of the surrounding code. Do not add files or abstractions that have no current use.
- Never claim scientific validation, purchase prediction, sales lift, or production security. Tests are software
  tests on synthetic data.

## Data and secrets

- **Synthetic data only.** Fixtures, examples, screenshots, and issues must use fictional companies, people,
  statements, and prices.
- **No real customer data**, employer data, confidential pricing, real buyer conversations, or health (PHI) data.
- **No secrets.** Never commit or paste API keys, passwords, tokens, cookies, certificates, `.env` files, Terraform
  state, or database URLs with credentials.

## Contribution Licensing

By submitting a contribution to BAEC, you agree that your contribution will be licensed under the same Business
Source License 1.1 terms that apply to the BAEC repository, unless a separate written agreement is made with the
project owner.

Do not submit code, data, documentation, or other material that you do not have the right to contribute.

Contributions must not contain real customer data, confidential employer information, credentials, API keys, PHI,
or other private information.

## Human-control boundaries

Changes that weaken a human confirmation gate or an outreach boundary need an explicit design review before any code
is written. Open an issue that describes the change and why it is needed. This includes:

- confirming a BAEC, changing account state, verifying correspondence, or accepting a draft without a human action;
- treating a model-supplied value as proof of human approval;
- moving an account to Active Opportunity from a signal;
- adding any send, schedule, outreach, or external CRM write capability.

Pull requests that do these things without that review will not be merged.
