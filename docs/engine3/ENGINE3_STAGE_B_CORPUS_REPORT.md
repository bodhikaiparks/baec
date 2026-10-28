# BAEC Engine 3: Stage B Corpus Report

**Status:** Stage B LOCKED (October 14, 2026)  
**Stage A contract SHA-256:** `890870e0af12bbbc35d26ef825544f12f8589198fddf6507d680036d21212b3a`  
**Corpus Markdown SHA-256:** `6f64b037b25165cc2512d9d2342954f8ba316fc2176f594c8a8542dfabf574d2`  
**Canonical JSON SHA-256:** `362dc5df456acd03246cdf4a44b1b16306d9901ad82774e33f52e2cb22ef087a`  

## Result

Stage B is complete as a synthetic adversarial domain corpus.

- Total fixed cases: **64**
- Positive controls: **19**
- All data synthetic: **Yes**
- Live model calls used: **No**
- Production code added: **No**
- Engine 1 modified: **No**
- Engine 2 modified: **No**
- Outreach authorization created: **No**
- Stage A acceptance criteria represented in the corpus/report: **28/28**

## Family distribution

| Family | Cases |
|---|---:|
| A. Upstream eligibility / handoff | 10 |
| B. Disclosure and source identity | 14 |
| C. Claim grounding / hallucination | 14 |
| D. Buyer-state and intent overclaim | 10 |
| E. Corrections / staleness / context drift | 8 |
| F. Human edits / action boundary | 8 |
| **Total** | **64** |

## Expected verdict distribution

| Verdict | Cases |
|---|---:|
| `ALLOW_TO_HUMAN_REVIEW` | 19 |
| `REFUSE_PREPARATION_INPUT` | 9 |
| `REFUSE_BUYER_FACING_DRAFT` | 30 |
| `REBUILD_STALE_CONTEXT` | 6 |
| **Total** | **64** |

These are Stage B conformance verdicts, not Engine 2 outcomes and not sales/account states.

## Kill-test findings

The corpus found no reason to collapse Engine 3 into a generic AI email generator. The defensible distinction remains the authority chain:

`buyer statement -> confirmed BAEC -> human-reviewed correspondence -> human Preparation Request -> disclosure-controlled grounding -> AI_INFERENCE draft -> claim validation -> Human Preparation Review -> STOP`

The highest-risk boundary is **disclosure**, not wording. A source being authorized for Engine 2 monitoring does not establish that the source, fact, exact figure, or acquisition method may be mentioned to the buyer. Stage B therefore preserves disclosure as a separate human-controlled decision.

The second highest-risk boundary is **state overclaim**. Many superficially natural sales phrases ("now that you're evaluating", "since you're ready to switch", "this is an opportunity") are invalid unless independently established through authoritative buyer evidence. Correspondence alone never supplies that authority.

The third highest-risk boundary is **action leakage**. A Reviewed Preparation Artifact remains preparation only. Automatic send, call initiation, CRM task creation, sequence enrollment, meeting scheduling, opportunity creation, or account-state transition remain outside Engine 3.

## Required adversarial controls present

The locked corpus includes:
- POSSIBLE correspondence that looks persuasive but remains ineligible;
- NO correspondence plus seller enthusiasm;
- stale BAEC and active revalidation blockers;
- corrected/retracted/superseded evidence;
- monitoring-authorized but internal-only facts;
- fact-use permission separated from source-identity permission;
- buyer-statement direct/paraphrase/internal controls;
- unsupported evaluation and switching claims;
- invented urgency, pricing, clinical benefit, competitor weakness, dates, roles, and numeric thresholds;
- questions with hidden unsupported premises;
- prompt-injection-like text inside an Observation treated strictly as evidence;
- human edits that add unsupported claims;
- stale context digests;
- attempted automatic send/CRM/state transitions;
- an explicit refusal to describe synthetic test success as empirical sales validation.

## Stage B lock conclusion

Stage B does **not** implement Engine 3. It freezes the adversarial specification against which later domain models, validators, application services, prompts, persistence, and UI can be tested.

No Stage A amendment was required by the corpus design.

The next stage should be **Stage C: deterministic domain model + grounding/disclosure validator design**, still before any live AI integration. Stage C should translate the locked Stage A rules and these 64 expected cases into exact enums, immutable records, validation precedence, and refusal reasons.
