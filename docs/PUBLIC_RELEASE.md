# Public release

This repository is a clean public export of the BAEC source. It starts from a single sanitized snapshot, and no
earlier development history is included.

## License

BAEC is source-available under the Business Source License 1.1 (`LICENSE`). You may inspect, modify, and use BAEC
for non-production evaluation and development under the license terms. Production use requires separate
authorization until the applicable Change Date.

| Field | Value |
|---|---|
| Licensor | Bodhi Parks |
| Licensed Work | BAEC: the original source code and documentation in this repository |
| Additional Use Grant | None |
| Change Date | 2030-10-28 |
| Change License | GPL-2.0-or-later |

The source is publicly available, but this is not an open-source release. Third-party dependencies are not part
of the Licensed Work. They are installed from their own distributions under their own licenses (MIT, BSD,
Apache-2.0, ISC, PSF, MPL-2.0 for certifi, LGPL-3.0 for psycopg), and none of their code is copied into this
repository.

## What is public

The complete, working local system:

- the Console: CRM (accounts, contacts, tasks, activity), Today, Accounts, Review Queue, Preparation, Activity,
  Dashboard, and the account workspace (Overview, Remember, Monitor, Recognize, Prepare, Timeline, Account state);
- Engine 1 (capture + verification), Engine 2 (monitoring + correspondence), and Engine 3 (re-engagement
  preparation), with their human review gates;
- Claude-assisted interaction intake, with bring-your-own-key;
- SQLite storage for local development;
- the PostgreSQL production architecture: schema, migrations, row-level security, provisioning, and the SQLite
  import tool, plus their tests;
- production authentication code (OIDC sign-in linked to provisioned BAEC users) and its tests;
- deployment architecture: container images, a compose file, nginx, backup/restore tooling, a runbook, and
  controlled-AWS Terraform, all with placeholder values;
- engine contracts, synthetic corpora, research harnesses, phase reports, and release verifiers.

## What is synthetic

Everything. The demo account (Riverside Outpatient Surgery Center, contact Morgan Lee), the supplier (Halvern
Surgical Supply), the buyer statement, the renewal notices and prices, and every company, person, interaction,
email, document, and monitoring source in fixtures, corpora, and tests is fictional. None of it comes from a real
customer, employer, or buyer conversation, and none of it is health data.

## What is not included

- the earlier private development history, branches, and tags;
- local databases, `.env` files, secrets, keys, certificates, Terraform state or plans, backups, and dumps;
- operator-specific infrastructure values (account IDs, hostnames, IP addresses, identity-provider credentials);
- the unpublished conceptual working paper;
- any hosted service, shared API key, or managed database.

Some phase reports cite commit IDs and tags from that earlier history. Those references are records of how the work
was verified. They do not resolve in this repository.

## Local demo

```bash
./scripts/start_demo.sh     # Python 3.12+, macOS or Linux; opens on http://localhost:8501
./scripts/stop_demo.sh      # add --reset to delete .demo/
```

The demo runs in explicit development mode on local SQLite files under `.demo/`, bound to `127.0.0.1`. It does not
use AWS, PostgreSQL, email, a calendar, or an external CRM, and it contacts no one. The walkthrough is
[`DEMO_WALKTHROUGH.md`](DEMO_WALKTHROUGH.md).

## AI and bring-your-own-key

AI features require your own Anthropic API key. The key is used only for the current session and is not stored by
BAEC. There is no shared key, no environment fallback in the Console, and no other provider. Without a key, every
workflow step still works manually.

In the Console, AI structures interaction notes and proposes a possible condition. The application checks every
quoted passage against the notes and refuses the whole proposal if one is not verbatim. Prepared drafts come from
a deterministic template, and the Engine 3 validator checks each one.

## Cost

Running the local synthetic demo uses your own machine. Optional AI analysis uses your own Anthropic API key.

The repository has no CI workflows, no deployment automation, no server-funded key, and no dependency on
operator infrastructure, so publishing it creates no ongoing cost for the maintainer.

## Production versus this release

| | This public release | Production BAEC |
|---|---|---|
| Mode | `development` | `production` |
| Storage | Local SQLite | PostgreSQL with row-level security, verified TLS |
| Sign-in | None (a local name label) | OIDC, linked by an operator to a provisioned user |
| Data | Synthetic | The customer's own, under controlled onboarding |
| Hosting | Your machine | Deployed and operated separately |

Production mode fails closed: if configuration is missing or invalid, it shows an error and opens nothing. The
deployment files here are architecture. Using them requires separate hardening, configuration, and review
(`docs/PRODUCTION_RUNBOOK.md`).

## Known limitations

- A demo video has not been captured yet.
- Tested on macOS (Python 3.14, SQLite 3.50) and on Debian Linux (Python 3.12 and 3.14, SQLite 3.46). The MCP stdio
  tests need `ps` (procps), which minimal Linux images leave out. The demo scripts are bash and do not run natively
  on Windows.
- Pillow 12.3.0, installed transitively by Streamlit, has published advisories that are fixed in Pillow 13. BAEC does
  not accept or decode images. The fix needs a separately tested Streamlit upgrade (see `SECURITY.md`).
- The hosted research demos on Streamlit Community Cloud sleep when inactive, and a visitor has to wake them.
- Development mode has no authentication, and its tenancy is enforced only in application code. Do not expose it
  to a network.
- The Console prepares drafts from a deterministic template. Engine 3's AI-proposal path is research code and is
  not connected to the Console.
- The PostgreSQL and deployment test suites need Docker and are not run by plain `pytest`.
- A few release-verifier tests compare against commits or tags from the earlier history. In this repository they
  skip, or report that history as unavailable.
- BAEC is research software. Its tests are software tests on synthetic data, not evidence for the BAEC theory,
  purchase prediction, or sales outcomes.

## Operator gate

Do not create or push the public GitHub repository until `./scripts/check_publication_ready.sh` reports
"Ready to publish". It checks:

1. **Contact link.** `README.md` ("Production BAEC") and `LICENSE` point to https://www.linkedin.com/in/bodhiparks. The gate checks that no
   contact placeholder remains.
2. **Screenshots.** Capture the six images in `docs/SCREENSHOTS.md`, then replace the README's placeholder table with
   the images.
3. **Commit author.** Every commit must use the GitHub noreply address, never a personal email. This repository's
   local Git identity is already set that way. For any new commit, check it, and re-author if needed:

   ```bash
   git config user.email "284195038+bodhikaiparks@users.noreply.github.com"
   git config user.name "Bodhi Parks"
   git commit --amend --reset-author --no-edit
   git log -1 --format=fuller          # Author and Commit both show the noreply address
   git log --all --format='%ae %ce' | sort -u   # nothing but the noreply address
   ```

   Run this after the README and screenshot commits as well, or squash them into the single commit first, so that
   every commit in the new history uses the noreply address.
4. **Clean tree.** Everything is committed.
