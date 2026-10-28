# BAEC Trigger Intelligence: LinkedIn product demo

A standalone, 60–90 second product walkthrough showing what BAEC does for a salesperson:

**Remember → Monitor → Recognize → Prepare**

BAEC remembers conditions buyers themselves say would make them reconsider a purchase, monitors for evidence
that those conditions may have become relevant, and helps a salesperson prepare a grounded conversation without
assuming buyer intent.

## What this is and is not

- **Synthetic and prerecorded.** The account (Alder Ridge Clinics), seller (Cobalt Imaging Supply), buyer
  statement, renewal notice, and every prepared output are fictional and fixed in `demo_data.py`.
- **Offline.** No API, no API key, no AI model call, no database, no network request. Streamlit usage stats are
  off and the server binds to `localhost` only (`.streamlit/config.toml`).
- **Human-controlled.** The correspondence decision in step 3 is made by the person using the demo. Nothing is
  sent, no opportunity is created, and no buyer intent is inferred.
- **Not a research artifact.** It does not read or change any Engine 1, 2, or 3 file and is not evidence about
  BAEC. It is a UI demonstration only.

## Launch

From the repository root, using the repository's existing virtual environment (Streamlit 1.65.0 is already in
`requirements.txt`):

```zsh
cd demo_linkedin
../.venv/bin/streamlit run app.py
```

Run it from inside `demo_linkedin/` so Streamlit picks up this directory's `.streamlit/config.toml`
(light theme, localhost-only, usage stats off). The app opens at <http://localhost:8501>.

Without the repository's virtual environment:

```zsh
cd demo_linkedin
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

## Recording mode (for LinkedIn screen capture)

Open <http://localhost:8501/?recording=1>, or open the sidebar (arrow at top left) and switch on
**Recording mode**.

Recording mode hides the Streamlit header, menu, toolbar, sidebar, and the "How this works" drawer, and tightens
spacing so each step fits a 1600×900 (16:9) browser viewport without scrolling. To leave it, remove
`?recording=1` from the URL.

Suggested capture: a browser window around 1600×900, zoom 100%, recording mode on, click **Restart demo** before
each take.

## Walkthrough

1. **Remember.** The buyer's statement, verbatim, with the condition (price increase > 8%), timing (July
   renewal), and potential action (contract review) underlined in the quote and pulled out below.
   Click **Start monitoring**.
2. **Monitor.** "Something changed." A July renewal notice shows a +9% price change, compared against the
   buyer's > 8% threshold on a simple scale. The evidence *may* correspond. Click **Review correspondence**.
3. **Recognize.** What the buyer said and what changed, side by side. You decide:
   **Yes — verify correspondence** shows "Human-verified correspondence" (with the note that this does not
   establish intent or authorize outreach) and **Prepare re-engagement**. **Not enough evidence** records no
   correspondence, prepares nothing, and offers **Back to review**.
4. **Prepare.** Four tabs: Why now, Email draft (not sent), Call opening, Discovery questions, followed by the
   boundary strip: "BAEC prepares the conversation. The salesperson decides whether to have it."

**Restart demo** (top right) returns to step 1 from any point.

## Files

| File | Purpose |
| --- | --- |
| `app.py` | The Streamlit app: four steps driven by `st.session_state` |
| `demo_data.py` | All synthetic, prerecorded copy |
| `styles.py` | Page CSS (system fonts only) and recording-mode overrides |
| `.streamlit/config.toml` | Theme, localhost binding, usage stats off |
| `requirements.txt` | Streamlit pin, matching the repository |
| `test_demo_app.py` | Software tests: button paths, restart, recording mode, offline imports |

## Tests

From the repository root:

```zsh
.venv/bin/pytest -q demo_linkedin
```

These are UI software tests of a synthetic demo. They are not evidence for the theory.
