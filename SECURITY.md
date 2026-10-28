# Security

## Supported release

Only the latest commit on the default branch of this public repository is supported. This repository is a local,
synthetic-data release. It is not a hosted service.

## Reporting a vulnerability

Please open a **private security advisory on GitHub** (Security tab → *Report a vulnerability*). Do not open a public
issue for a suspected vulnerability.

Include the affected file or component, the steps to reproduce, and what you expected to happen.

**Do not include secrets in a report.** That means no API keys, passwords, tokens, cookies, database URLs, private
keys, or real customer data. If a report needs a credential to demonstrate, describe it instead of pasting it.

## What this repository is

- **Synthetic data only.** Every company, person, statement, price, and document in the demo and test fixtures is
  fictional. Do not enter real customer, employer, health (PHI), or confidential data into the local demo.
- **Local development by default.** `./scripts/start_demo.sh` runs the Console in explicit development mode, on
  local SQLite files, listening on `127.0.0.1` only. Development mode has no authentication. It is not meant to be
  exposed to a network.
- **Production needs separate hardening and configuration.** Production mode requires OIDC sign-in, PostgreSQL with
  row-level security over verified TLS, current migrations, and least-privilege database roles. If any of these is
  missing or invalid, production mode fails closed: it shows a configuration error and opens nothing. The
  deployment files in `deploy/` and `infra/` are architecture with placeholders. Running them safely is the
  operator's responsibility (`docs/PRODUCTION_RUNBOOK.md`).

## Claude: bring your own key

- AI Assist works only with the visitor's own Anthropic API key, entered in the browser sidebar.
- The key is kept in that browser session's state only. It is not written to disk, a database, or logs, and
  **Disconnect Claude** or signing out clears it.
- The Console has no shared or server-funded key and no environment-variable fallback, and the demo script removes
  any `ANTHROPIC_*` variable from the server's environment. There is no other AI provider.
- AI output is validated by the application, and invalid output is refused. AI can propose; it cannot confirm a
  BAEC, verify correspondence, change account state, or authorize any action.

Separately, the opt-in research evaluation harnesses (`docs/PHASE6C_LIVE_EVALUATION.md`, deselected by default) read
`ANTHROPIC_API_KEY` from the environment of the person who runs them. The Console never uses them.

## Known dependency advisory: Pillow

Pillow is installed transitively by Streamlit (Streamlit 1.65 requires `pillow<13`), at version 12.3.0
(`deploy/requirements.lock`). Dependency scanners such as pip-audit report advisories against Pillow 12.3.0 that are
fixed in Pillow 13.0.0, mostly in image decoders. These are real advisories, and we do not suppress them.

BAEC does not accept, decode, or process image or file uploads. It has no `st.file_uploader`, no `st.image`, and no
code that opens an image, so the affected Pillow decoders are not reached by BAEC's own code paths. Clearing the
advisories requires a Streamlit upgrade, which can change the accepted sign-in (`st.login`, Authlib) and UI behavior,
so it will be handled as a separate, separately tested dependency upgrade. It is not part of this release.

## No autonomous sending

BAEC has no send, schedule, mailbox, calendar, or external CRM action anywhere in the code. Prepared drafts are
for human review. Accepting a draft does not send it. A signal or a verified correspondence never moves an
account to Active Opportunity.
