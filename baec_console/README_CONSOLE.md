# BAEC Console V1 — Trigger Intelligence

A local, offline product shell over the released BAEC Engines 1–3. A salesperson remembers what a buyer said
would make evaluation worthwhile, monitors for related evidence, recognizes possible correspondence through a
human review, and prepares a bounded re-engagement conversation for human use.

**Status:** local development mode on synthetic data. Not deployed by this repository.

## Launch

The quickest start is `./scripts/start_demo.sh` from the repository root (see the root `README.md`). To run it by hand,
from the repository root:

```
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cd baec_console
BAEC_DEPLOYMENT_MODE=development ../.venv/bin/python -m streamlit run app.py
```

`BAEC_DEPLOYMENT_MODE` is required and has no default. `development` runs as the local-development principal;
`production` opens only for a signed-in user whose sign-in an operator linked to an active HUMAN BAEC user, on
PostgreSQL only (Phase 4, below). Unset, empty, or any other value shows a configuration error and creates or opens
nothing (no identity, no workspace data). There is no `.env` file.

Then open http://localhost:8501. The first run creates `baec_console/.data/` (git-ignored); no manual setup is
needed. Run it from the `baec_console` directory so `baec_console/.streamlit/config.toml` applies: it keeps the
server on `localhost`, turns off usage statistics, and stops Streamlit's startup banner from looking up the
machine's public IP address. Set `BAEC_CONSOLE_DATA_DIR` to keep data somewhere else.

To try it immediately, use one of the sidebar demos for the synthetic Riverside Outpatient Surgery Center account:
**Load demo: start from call notes** (account and contact only), **Load demo: start from beginning** (the call
already logged), or **Load demo: completed example**. The walkthrough is `docs/DEMO_WALKTHROUGH.md`.

## Tests

```
.venv/bin/python -m pytest -q baec_console/tests     # from the repository root, with requirements-dev.txt installed
```

These are software tests on synthetic data. They are not evidence for the BAEC theory.

## Workflow

| Step | What the person does | What decides |
|---|---|---|
| Remember | Records a buyer interaction, captures the condition in a structured form, previews it, confirms it | Engine 1 classifier and human confirmation gate |
| Monitor | Authorizes a monitoring plan and its sources, adds observations and optional exact measurements | Engine 2 activation rule (Engine 1 currentness) |
| Recognize | Answers each required correspondence check and the sufficiency question | Engine 2 outcome rule, derived from the human findings |
| Prepare | Requests preparation with disclosure decisions, reviews each draft: accept, edit, or reject | Engine 3 eligibility and validator |

Nothing contacts the buyer. There is no send, schedule, mailbox, or CRM action anywhere in the console.

## Architecture

```
baec_console/
  app.py                       Streamlit shell and navigation
  ui_pages.py                  Dashboard, Accounts, Review Queue, Preparation, Activity
  ui_workspace.py              Account Workspace (Remember, Monitor, Recognize, Prepare, Timeline, Account state)
  ui_common.py, styles.py      shared UI helpers and styling
  console_service.py           product operations for one workspace; no Streamlit dependency
  engine1_adapter.py           form -> Engine 1 objects; HumanCommandFacade request/approve/redeem
  engine2_adapter.py           Stage K handoff, plans, observations, measurements, human review
  engine3_adapter.py           eligibility from stored records, grounding, validate_case
  deterministic_preparation.py DETERMINISTIC_CONSOLE_TEMPLATE_V1
  console_store.py             console-owned SQLite store (append-only preparation records, ownership, attribution)
  crm_store.py                 CRM contacts, tasks, activities, interaction links
  identity.py                  organizations, users and their kinds, the Principal, the local-development principal
  deployment.py                development / production mode; how a process may obtain its principal
  demo_seed.py                 synthetic Riverside / Halvern seed
```

### Storage

Each workspace is a directory with four SQLite files: the released Engine 1 schema, the released Engine 2
store, a console store, and a CRM store. Two workspaces exist: `local` (accounts you create) and `synthetic` (demo
data). Each organization has its own synthetic directory (the `local` organization keeps the V0.3 `synthetic/`
location; others use `synthetic-organizations/<digest>/`). One `identity.sqlite3` per data root holds
organizations and users (no passwords or credentials).
Engine 1 and Engine 2 are append-only with no delete path, so synthetic data lives in separate files and
**Reset synthetic data** removes only those files. Your accounts are never touched.

The console store holds only what the engines do not: account metadata, the structured breakdown of each
interaction, unconfirmed condition drafts, console events, and Engine 3 preparation records (Engine 3 has no
persistence of its own). Preparation records and events are append-only, enforced by SQLite triggers.

### Organizations and users (V0.4, implementation)

Every account belongs to exactly one organization, recorded once in the console store's append-only
`account_ownership` table. Engine records carry no organization: the console resolves every id it is given
(draft, condition, candidate, review, request, preparation, contact, task, activity, interaction) to its account
and checks that the current principal's organization owns it before reading or writing. An id that does not exist
and an id owned by another organization are refused with the same `AccessDenied` message. Users in one
organization share its accounts; each human action records the acting user's display name, which is unique within
the organization.

In development mode the app runs as the deterministic local-development principal (organization `local`, user
`local-user`), only when `BAEC_DEPLOYMENT_MODE=development` is set explicitly; there is no login. Production mode
requires sign-in (Phase 4) and never falls back to the local-development principal. With the mode unset or invalid,
the app refuses.
On first V0.4 start, existing V0.3 accounts in both workspaces are assigned to that organization
once (`basis = V03_MIGRATION`), and the V0.3 "Your name" becomes the local user's display name; no engine record
is rewritten. After that, an account with no ownership row is visible to no one. Each organization loads and resets
only its own synthetic workspace. Demo data is seeded by each organization's synthetic-seed user, never attributed
to the person who loaded it.

Phase 2 (implementation): a user's `user_id` is its identity; the display name is a label. Every human action also
writes an append-only attribution (user id, organization id, and the display name at that moment), so renaming a
user never changes who did what. Each user has a fixed kind (`HUMAN`, `LOCAL_DEVELOPMENT`, `SYSTEM_SEED`,
`PUBLIC_DEMO_SESSION`, `LEGACY_UNCLASSIFIED`); only active `HUMAN` users are eligible for a future sign-in. Every
console operation re-checks the principal against the identity store, so a deactivated user loses access without a
restart. Tenancy is enforced in application code: opening the SQLite files directly bypasses it. See
`docs/V04_PHASE2_IDENTITY_SECURITY_REPORT.md`.

Phase 3 (implementation; the console still runs on SQLite): product data (identity, console store, CRM) has a
PostgreSQL schema with row-level security, versioned migrations, and an explicit SQLite import tool
(`baec_console/postgres/`). Production mode requires `BAEC_DATABASE_URL` and refuses to start on SQLite; it also
stays closed while an engine store has no PostgreSQL store. Development mode refuses if `BAEC_DATABASE_URL`
is set. Integration tests run against a disposable local container: `scripts/postgres_test_db.sh test`. See
`docs/V04_PHASE3_POSTGRES_RLS_REPORT.md`.

Phase 3B (implementation; development still runs on SQLite): ConsoleService reaches its stores only through a
storage backend (`baec_console/backends.py`). `PostgresBackend` (PostgreSQL test mode) runs the same service code on
PostgreSQL identity, console, CRM, and Engine 2 stores (`baec_engine2`, migration 0003, the unchanged Engine 2 code
over `postgres/engine2.py`), with Engine 1 still in SQLite. Each service action is one PostgreSQL transaction.
Production stays closed while Engine 1 has no PostgreSQL store, and synthetic reset is refused on PostgreSQL. See
`docs/V04_PHASE3B_PRODUCTION_PERSISTENCE_REPORT.md` and `docs/V04_ENGINE1_POSTGRES_MIGRATION_PLAN.md`.

Phase 3C (implementation; development still runs on SQLite): Engine 1 has a PostgreSQL store too (`baec_engine1`,
migration 0004: the 25 tables, one PostgreSQL trigger for each of the 92 SQLite triggers, tenancy and RLS), reached by
the unchanged Engine 1 repository through `postgres/engine1.py`. On PostgreSQL a service action's Engine 1 writes share
its transaction. The production gate validates the live product, Engine 1, and Engine 2 schema against a generated
catalog manifest (`postgres/catalog.py`, `postgres/schema_manifest.json`). The import tool also copies Engine 1 and
Engine 2 records. See `docs/V04_PHASE3C_ENGINE1_POSTGRES_REPORT.md`.

Phase 4 (implementation; not deployed): production sign-in. Streamlit's built-in OpenID Connect sign-in (`st.login`,
Authlib) verifies the provider session; BAEC takes only the issuer and subject from it. An operator links that
subject to an existing active HUMAN user with the provisioning tool; there is no self-registration, invitation, or
email-domain joining. The link lives in `baec_auth.auth_identities` (migration 0005), readable at sign-in only by the
narrow role `baec_auth_lookup`, one exact subject at a time. Every console operation re-checks the link, the user,
and the session age, so revocation or deactivation takes effect on the next request. Production needs
`BAEC_DATABASE_URL` (role `baec_app`), `BAEC_AUTH_DATABASE_URL` (role `baec_auth_lookup`), `BAEC_AUTH_ISSUER`, and
Streamlit's `[auth]` secrets (https URLs, one provider); the startup gate checks all of it. Provisioning
(`BAEC_PROVISIONING_DATABASE_URL`, role `baec_provisioner`, never set for the app):

```
python -m baec_console.postgres.provision create-organization --organization ID --name NAME
python -m baec_console.postgres.provision create-user --organization ID --user ID --name NAME
python -m baec_console.postgres.provision link --organization ID --user ID --issuer URL --subject SUB --operator WHO
python -m baec_console.postgres.provision revoke --organization ID --issuer URL --subject SUB --operator WHO
python -m baec_console.postgres.provision deactivate|reactivate --organization ID --user ID
```

See `docs/V04_PHASE4_PRODUCTION_AUTH_REPORT.md`.

Phase 5 (implementation; tested locally, not deployed): operations. Every PostgreSQL connection requires verified
TLS (`sslmode=verify-full`); pools are bounded, with statement, lock, and idle-in-transaction timeouts, and a request
that hits a limit shows one fixed "unavailable" message. The web process refuses to start if it holds a migration,
provisioning, backup, or restore credential or any `ANTHROPIC_` variable; database URLs may be mounted secret files
(`NAME_FILE`). Logs are redacted. `python -m baec_console.readiness` runs the whole production startup and prints one
code. The production image, HTTPS proxy, compose configuration, and backup tool are in `deploy/`; operating it is
`docs/PRODUCTION_RUNBOOK.md`; the evidence is `docs/V04_PHASE5_DEPLOYMENT_HARDENING_REPORT.md`.

## Deterministic preparation

V1 has no live model. `DETERMINISTIC_CONSOLE_TEMPLATE_V1` fills fixed sentence patterns from recorded values and
the salesperson's own paraphrase, declares each sentence's claim type and grounding, and submits every output
unchanged to `baec_app.engine3.validate_case`. A refusal is shown with its reasons and is never repaired. The
template is a local product convenience, not a research result and not an AI generator. The console labels its
output "Prepared by BAEC · Deterministic preparation".

### Provenance (V1 compatibility limitation)

Engine 3 V1 knows only two artifact origins, `AI_INFERENCE` and `HUMAN_EDITED`, and the console does not change
that locked enum. Template output is machine-produced and not human-written, so it is submitted to the validator
under `AI_INFERENCE`, the existing validator-compatible value. That value does not mean a model generated it.
Each preparation stores its provenance separately, in a table whose constraints refuse any other value:

| Field | Value |
|---|---|
| `console_generator_origin` | `DETERMINISTIC_CONSOLE_TEMPLATE_V1` |
| `engine3_validator_origin` | `AI_INFERENCE` (compatibility value) |
| `origin_compatibility_note` | `E3-ORIGIN-COMPAT-V1`: AI_INFERENCE is the existing Engine 3 validator-compatible origin value for machine-produced drafts. No model was used to generate this preparation. |
| `model_used` | always `0` |

A human edit carries its own provenance (`HUMAN_EDIT`, validated as `HUMAN_EDITED`, with the editor and the
parent draft) and never changes the original preparation's record. The main UI says "Prepared by BAEC ·
Deterministic preparation"; these fields appear under Technical details.

## Preparing from a specific correspondence

Preparation is bound to one human-verified correspondence, chosen explicitly. The Prepare section lists every
verified correspondence (newest shown first, for display only); the salesperson prepares from one with
**Prepare from this correspondence**. The Preparation Request stores the exact Engine 2 records it is bound to:
the review id (`HCR-...`), its correspondence candidate, the monitoring plan and version, and the activation. Each
preparation keeps that review id permanently in its provenance record, and the timeline names the correspondence
a request came from.

The console never chooses a review by recency. Engine 2 records no supersession of a review, so a later review of
the same evidence does not replace the bound one; it is shown to the salesperson as a warning instead. If the
bound review stops satisfying Engine 3 eligibility (its evidence is corrected, the condition is no longer current,
revalidation is flagged, or the records change), Engine 3 refuses and nothing is generated. Possible and no
correspondence can never be selected.

## Blocking preparation

**Block preparation for this correspondence** records a human decision that nothing may be drafted from that
correspondence. It uses Engine 3's existing `human_preparation_block` input, so Engine 3 itself refuses
(`E3C-IN-007 HUMAN_PREPARATION_BLOCK`; if a request already existed, Engine 3 refuses it as a changed context).
Decisions are append-only: lifting a block is a new, separate decision, and both stay in the timeline. A block
applies only to the correspondence it was placed on.

## Boundaries the console keeps visible

- Correspondence is not intent. Monitoring is not buyer evaluation.
- A buyer condition is not an active opportunity; account state changes only through Engine 1's separate,
  human-authorized transitions.
- A Preparation Request authorizes drafting only, never outreach.
- Engine output is not a human decision; every human step records who acted.
- BAEC prepares the conversation. The salesperson decides whether to have it.

The name in the sidebar is the local-development user's display name. It is recorded on every human action; it
is not authentication.

## Implementation choices (not research findings)

- Evidence excerpts must be copied exactly from the interaction; text inside the buyer's statement is recorded
  as a buyer fact, text in the seller notes as a seller observation.
- When Engine 3 refuses preparation for a request (eligibility, a human block, or a changed context), nothing is
  generated; the refusal is recorded as an event.
- The console maps the human action "Not enough evidence" to the complete Engine 2 review vocabulary required by
  the existing engine. Unanswered checks are recorded as Unresolved and an unanswered sufficiency decision as No.
  This is a console V1 mapping rule, not an Engine 2 research claim.
- The arithmetic check shown during review is display-only; the human's findings decide.
- One monitoring plan per confirmed condition in V1.

## V1 limitations (kept, not fixed by changing the engines)

- Engine 1 has no product path to make a confirmed condition stale, so confirmed conditions stay current.
- Engine 3 has no native deterministic-template origin (see Provenance above).
- Engine 3 has no native persistence; preparation records live in the console store.
- There is no authentication. The local-development principal is not a security boundary, and the domain
  layer's HumanAuthorization is a requirement, not proof that a human approved anything.
