# Demo walkthrough: Riverside Outpatient Surgery Center

A complete, synthetic walk through BAEC, suitable for a recorded demo. Every company, person, statement, price, and
date below is fictional.

| | |
|---|---|
| Account | Riverside Outpatient Surgery Center (ambulatory surgery, synthetic) |
| Contact | Morgan Lee, Materials Manager |
| Incumbent supplier | Halvern Surgical Supply (fictional) |
| Buyer condition | Contracted pricing increases by **at least 7%** at the **April renewal** |

Start the demo with `./scripts/start_demo.sh`, open http://localhost:8501, and type your name in the sidebar. Every
human action is recorded under that name. The name is a label, not authentication.

`baec_console/tests/test_demo_walkthrough.py` runs this same path offline on every test run, with a stubbed Claude
response.

## 60–90 second video cut

Doing the whole walkthrough by hand, with typed forms, takes several minutes. For a 60–90 second video, record the
gates and cut the typing:

| Time | Screen | What to show |
|---|---|---|
| 0:00 | **Today** | What BAEC is: remember, monitor, recognize, prepare |
| 0:08 | **Account** (start from call notes) | Riverside Outpatient Surgery Center, Morgan Lee |
| 0:15 | Overview → Log interaction | Paste the call notes (step 1) |
| 0:22 | **Analyze with AI** | The possible BAEC condition and its exact quotes (steps 2–3) |
| 0:32 | **Engine 1** · Remember | Preview, then the human **Confirm** click (step 4) |
| 0:42 | **Monitor** | The authorized source, then the renewal notice and its correction: 9% → 7% (steps 5–6) |
| 0:55 | **Engine 2** · Recognize | The human checks, then **Verify correspondence** (step 7) |
| 1:05 | **Prepare** | Disclosure choices, then **Prepare drafts** (step 8) |
| 1:12 | **Engine 3 output** | Why Now, email, call opening, discovery questions, then **Accept** (steps 9–10) |
| 1:22 | **Timeline** | The full progression, every human action attributed (step 11) |

Fill the step 5, 6, and 8 forms before recording, or cut while you type. Every click on a gate (Save & review,
Confirm, Verify, Prepare, Accept) must be a real click in the recording. Do not substitute the *completed example*
for those gates. Use it only for the Today shot if you like. The video must show no API key (the field shows dots;
disconnect afterwards) and no real data.

---

## 1. CRM interaction

Sidebar → **Load demo: start from call notes**. The account opens with Morgan Lee as its only contact and with no
interactions. The account is unclassified, which is not a fourth state.

In **Overview → Log interaction**, choose contact **Morgan Lee**, type **Call**, and paste these notes:

```
Quarterly check-in call with Morgan Lee, Materials Manager (synthetic). Morgan said: "We're not evaluating other suppliers today. If our contracted pricing increases by at least 7% at the April renewal, we would compare other options." The current Halvern Surgical Supply supply agreement renews in April. No next meeting was scheduled.
```

## 2. Claude-assisted analysis (optional, your own key)

Paste your Anthropic API key into **AI Assist** in the sidebar. The key stays in this browser session only; BAEC
does not store it. Click **Analyze with AI**.

Claude proposes a summary, the exact buyer statement, and a finding with an exact-quote excerpt for each of the four
BAEC criteria. The application checks that every quoted passage is verbatim in your notes. If one is not, it refuses
the whole proposal and applies nothing.

*No key?* Use **Load demo: start from beginning** instead. It logs the same call for you, with no AI, and you begin
at step 4 by capturing the condition in **Remember** yourself.

## 3. Possible BAEC condition

If the proposal holds up, BAEC shows **Possible buyer-articulated condition**. Nothing is saved yet. You choose:

* **Save & review BAEC condition →** saves the CRM activity, records the Engine 1 interaction, and creates an
  unconfirmed condition draft.
* **Save to CRM only** keeps it as an ordinary activity.

Choose **Save & review BAEC condition →**.

## 4. Engine 1: human confirmation

In **Remember**, the draft is marked *Captured · not yet confirmed*. Click **Preview condition** to see exactly what
will be recorded: the four criteria with their evidence, who supplied the condition (buyer-generated), the
threshold stored verbatim as *by at least 7%* (comparator *at least*, value 7, unit %), and the timing *at the April
renewal*.

Click **Confirm buyer-articulated condition**. The confirmation is bound to the preview you saw; if anything changed,
it is refused.

Optional: in **Account state**, record a dormancy judgment (plausible, addressable), then move the account to
**Conditionally Dormant**. That decision is separate from confirming the condition, and nothing makes it
automatically.

## 5. Monitoring

In **Monitor**, authorize a monitoring plan:

* target entities: `Halvern Surgical Supply (synthetic)` and `Riverside Outpatient Surgery Center (synthetic)`;
* the checks a reviewer must make (keep the defaults, which include threshold, comparator, magnitude, unit, and
  renewal date);
* one source: type `Supplier renewal notice`, owner `Halvern Surgical Supply (synthetic)`, legitimate basis
  `Forwarded by the buyer under the account agreement`, access `Buyer forwards notices by email`, captured
  `Notice text and dates`.

Click **Start monitoring**.

## 6. Synthetic renewal and pricing observation

Click **Add observation**, with effective date **1 April 2027**, measurement **Percent change from two prices**,
earlier value `120000`, new value `130800`, unit `USD per year`, and this evidence:

```
Renewal notice for supply agreement RSC-4410: from 1 April 2027 the annual contracted price changes from USD 120,000 to USD 130,800.
```

The measurement is derived exactly: **9.00%**. Then a correction arrives. Add a second observation that **corrects**
the first one, with new value `128400`:

```
Correction to renewal notice for supply agreement RSC-4410: the previously reported amount was incorrect. From 1 April 2027 the annual contracted price changes from USD 120,000 to USD 128,400.
```

The current measurement becomes **7.00%**. That meets the buyer's *at least 7%* exactly. The 9% notice stays in the
history, marked superseded, and is no longer used.

## 7. Engine 2: correspondence verification

In **Recognize**, BAEC shows a correspondence candidate built from the corrected evidence. Answer each required check
and the sufficiency question, then click **Verify correspondence**. BAEC derives the outcome,
`HUMAN_VERIFIED_CORRESPONDENCE`, from your answers.

Verified correspondence narrows attention. It is not buyer intent and not an opportunity, and the account state does
not change.

## 8. Prepare

Click **Continue to Prepare →**, then **Prepare from this correspondence**. Make the disclosure choices:

* prepare for **email and call**, with the goal **ask whether to revisit the prior discussion**;
* refer to the buyer's statement **in my own words**:
  `a contracted pricing increase of at least 7% at the April renewal could make it worth comparing options`;
* the buyer statement may be paraphrased; the threshold and timing may be referenced directly; the renewal notice
  stays **internal only**; the measurement may be paraphrased.

Click **Prepare drafts**.

## 9. Engine 3 drafts

Engine 3 checks eligibility, prepares the four artifacts, and runs every sentence through its validator:

* **Why Now** (internal summary)
* **Email draft**
* **Call opening**
* **Discovery questions**

A sentence that would invent a threshold, date, urgency, or intent, or reveal an internal-only source, is refused,
and BAEC shows the reason.

## 10. Human review

Review each draft: **Accept as drafted**, **Edit**, or **Reject**, after confirming the attestations. Accepting a
draft means it is fit for human use. It does not send it. BAEC has no send, schedule, or outreach action.

## 11. Timeline and history

Open **Timeline**. It shows every step in order, with each human action under your name and the superseded
observation kept alongside its correction. **Activity** and **Today** show the same work across accounts.

To start over, click **Reset synthetic data** in the sidebar, or run `./scripts/stop_demo.sh --reset`.
