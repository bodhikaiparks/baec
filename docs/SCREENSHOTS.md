# Screenshot capture checklist

Six screenshots for the README, all taken from the local synthetic demo. Every value shown must come from the
Riverside Outpatient Surgery Center scenario. No real data.

## Before you start

- [ ] Run `./scripts/stop_demo.sh --reset`, then `./scripts/start_demo.sh`, so the demo data is fresh.
- [ ] Use a clean browser window or profile with no bookmarks bar, no other tabs, and no extensions showing.
- [ ] Use a window about 1440 × 900, at 100% zoom, with the light theme.
- [ ] Make sure the address bar shows only `localhost:8501` (or hide it). No local filesystem path may appear
      anywhere, including terminal windows behind the browser.
- [ ] In the sidebar, set **Your name** to a neutral label such as `Demo salesperson`, not your real name.
- [ ] **No API key visible.** Paste your Claude key, then let the password field show dots only. Never take a
      screenshot with the field revealed or while you are typing it. After screenshot 2, click **Disconnect Claude**.
- [ ] Save each image as PNG, at exactly the path listed below.

## The six screenshots

| # | File | Setup | Must be visible |
|---|---|---|---|
| 1 | `docs/images/01-account-overview.png` | Load **completed example**, then open the Riverside account's **Overview** | The account state, the Remember → Monitor → Recognize → Prepare progress, the buyer condition, and the sidebar navigation |
| 2 | `docs/images/02-ai-intake.png` | Load **start from call notes**. In Overview → Log interaction, choose Morgan Lee and Call, paste the notes from `DEMO_WALKTHROUGH.md` step 1, then click **Analyze with AI** | The **Possible buyer-articulated condition** notice; the exact buyer statement; **Save & review BAEC condition →** and **Save to CRM only**. The key field shows dots only |
| 3 | `docs/images/03-engine1.png` | Click **Save & review BAEC condition →** (Remember opens with the preview) | The four criteria with their evidence; threshold *by at least 7%*; timing *at the April renewal*; the **Confirm buyer-articulated condition** button, not yet clicked |
| 4 | `docs/images/04-engine2.png` | Confirm, start monitoring, and add both observations (steps 5–6). Open **Recognize** and answer the checks without submitting yet | The corrected notice (USD 128,400) as the evidence under review, and the correspondence checks with their answers. If the superseded 9% notice should also appear, take this from **Monitor** instead |
| 5 | `docs/images/05-prepare.png` | Verify the correspondence, then **Prepare** with the step 8 choices | The Why Now summary and the email, call opening, and discovery-question drafts, with their Accept / Edit / Reject controls |
| 6 | `docs/images/06-timeline.png` | Accept the four drafts, then open **Timeline** | The full progression in order: account, call, condition captured and confirmed, monitoring, both observations, correspondence verified, preparation, draft reviews |

Screenshots 1 and 6 can also come from **Load demo: completed example**, which records every step as the
synthetic-seed user. Screenshots 2 to 5 should come from your own click-through, so the human gates on screen are
the ones you clicked.

## After capture

- [ ] Open each PNG and check it for a key, your real name or email, other tabs, bookmarks, notifications, or a
      filesystem path.
- [ ] Add the files under `docs/images/`, then in `README.md` replace the "Screenshots have not been captured yet"
      table with the images (for example `![Account and workflow overview](docs/images/01-account-overview.png)`).
- [ ] Run `.venv/bin/python -m pytest -q tests/test_public_demo.py` (it checks the README).
