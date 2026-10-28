# BAEC Engine 2 Release Candidate Record

**Project:** BAEC Trigger Intelligence
**Engine:** BAEC Engine 2: Monitoring + Correspondence
**Status:** Release candidate of an offline research prototype, verified as software on synthetic data. Not tagged.
**Starting baseline:** commit `69175621d49a7c194a50810fcbe2c3a28867ecfc` (parent `3665b95fb0fca4eb5754509ccd7480541b036ffe`), plus the Stage L documentation, demonstration, verification script, and tests.
**Machine-readable record:** `docs/engine2/ENGINE2_RELEASE_CANDIDATE_REPRODUCIBILITY.json`
**Companion:** `docs/engine2/ENGINE2_TECHNICAL_RESEARCH_BRIEF.md`

> **Limitation.** These results demonstrate one software implementation of managerial implications proposed in an unpublished conceptual working paper. They do not validate the BAEC construct, establish prospective validity, or show any link between correspondence and purchases, revenue, or opportunity creation.

Each statement below is one of four kinds, as in the Engine 1 verification records: a **research claim** (from the manuscript, untested; Stage L makes none), an **implementation choice**, a **deterministic software verification**, or an **accepted prototype limitation**.

---

## 1. Stages in this release candidate (implementation)

| Stage | Content | Commit |
|---|---|---|
| A | Domain contract | `e333061` |
| B | Synthetic corpus, 64 cases | `1114c0b` |
| C | Deterministic domain core | `4711b00` |
| D | Append-only persistence, `BAEC_ENGINE_2` v1, 30 tables | `7b09cb7` |
| E | Plan-version identity | `ad1ef5d` |
| F | AI proposal boundary (advisory) | `c6d2484` |
| G | AI audit persistence, `BAEC_ENGINE_2_AI` v1, 7 tables | `b3a9bd9` |
| H-A | Controlled live harness, opt-in, offline-proven | `499df93` |
| I | Human review application layer | `5501bec` |
| J | Operator review workflow (Streamlit) | `3665b95` |
| K | Confirmed BAEC handoff from Engine 1 | `6917562` |
| L | Release hardening and reproducibility | this record |

Stage H-B, the single live model attempt, has **not** been executed.

## 2. Deterministic release demonstration (deterministic software verification)

`python3 scripts/engine2_release_demo.py` runs, in memory and offline:

```
Engine 1 buyer interaction (Repository)
-> human confirmation request, gate approval, confirm_baec (Phase 4 HumanCommandFacade)
-> persisted CONFIRMED_BAEC (BAEC-RC-001)
-> Stage K read-only handoff (Engine 1 query-only connection)
-> Engine 2 MonitoringPlan MP-RC-001 v1 and activation ACT-RC-001 (Stage D, locked activation rule)
-> five Observations from two authorized sources; one correction and one retraction; one refused item
-> two Derived Measurements (12.00 %, invalidated by the correction; 9.00 %, usable)
-> Signal Candidate SC-RC-001 and Correspondence Candidate CC-RC-001
-> Stage I review packet -> scripted human review REV-CC-RC-001-1 (Stage I)
-> HUMAN_VERIFIED_CORRESPONDENCE, derived by the locked classifier
```

- **Scenario (implementation choice).** The original synthetic Stage J scenario (Alder Ridge Clinics and Cobalt Imaging Supply), now beginning from a real Engine 1 confirmation. It is not taken from the research corpus. The Stage J page keeps its own fixed demo store unchanged.
- **Confirmation path (implementation choice).** The Phase 4 human confirmation gate, not the Phase 7 grant executor, because the grant path begins from an AI artifact and no production or seed source may introduce AI provenance. The Stage K tests separately cover a BAEC confirmed by the real Phase 7 grant executor.
- **Scripted human inputs (accepted prototype limitation).** The gate approval and the review judgments are fixed synthetic inputs in the script. They represent a person; they are not a person.
- **Determinism (verified).** Fixed clocks and explicit identifiers. Two runs produce identical summaries. Release demo digest `310f570874c1efb73eea33ff3830844375389fc8f08a5069554c06952cc4f45e`, handoff digest `168cdc8894cd67a6590f160f46a27af55392c2f2f855a693586216129e7de255`, review context digest `e78f571a170679fc37845d81f05ff146c69c849a5ac48dbc2eac5ad55e184c7d`. The digest covers the logical records; raw SQLite image hashes are recorded separately because their bytes can differ between SQLite builds.
- **Isolation (verified).** The Engine 1 store is byte-identical before and after the handoff. No account-state transition occurs and the account's state stays empty. Nothing is written to disk, and no AI, network, or credential is used.

## 3. Release verification procedure

`python3 scripts/verify_engine2_release.py` (add `--full` for the full suite). It checks, offline:

- a clean working tree, and that no live gate variable (`BAEC_LIVE_E2_STAGE_H`, `BAEC_LIVE_E2_RESEARCH_DIR`, `BAEC_LIVE_CLAUDE`) is set;
- the Research Contract, Stage A contract, and both Stage B corpus files against their recorded SHA-256 values;
- the Engine 1 schema version and both Engine 2 schema identities (engine, version, table count);
- the Engine 1 development database hash where that generated file is present;
- the release demonstration: two identical runs, the recorded digest, the outcome, and no account-state change;
- the composed existing suites: Stage K, Stage I, Stage J, Stage L, the 64-case distributions, the Engine 2 static audits, all Engine 2 tests, and the Engine 1 boundary audits.

It reads git locally and never fetches, reads no credential, and calls no model.

## 4. Reproducibility snapshot

| Item | Value |
|---|---|
| Python | 3.14.3 |
| SQLite library | 3.50.4 |
| Packages | streamlit 1.65.0, pydantic 2.13.5, pytest 9.1.1, mcp 2.2.0, anthropic 1.9.0, httpx2 2.13.1 |
| Engine 1 schema | version 7 |
| `BAEC_ENGINE_2` | version 1, 30 tables |
| `BAEC_ENGINE_2_AI` | version 1, 7 tables |
| Research Contract | `c66929ff07dd19913d12420b6ba4f59f56541e25e602da80ee0a399ab2e756c9` |
| Stage A contract | `c4bcf1d0673faf266a70793f7c5693a45a4828999bf63d4b54aed8a5154016b7` |
| Stage B corpus (Markdown) | `177848a45ccfb7ad0716c01f7f350bdb813c90e6b0265eb79544b0007a93d801` |
| Stage B corpus (JSON) | `fb96e3d17ba82b3b8877d1c1f7499919c549af05eed8902a754ad31c76ff7bf3` |
| Engine 1 development database | `add117f8452adbcf96368ae5fd57bc71be3b83493fb4136d96aea84834e1ef06` (generated, git-ignored) |
| Corpus distribution | 20 `NO_CORRESPONDENCE`, 32 `POSSIBLE_CORRESPONDENCE`, 9 `HUMAN_VERIFIED_CORRESPONDENCE`, 3 blocked |
| Stage H-B | not executed (paused) |

## 5. Test inventory (actual collected counts)

| Suite | Passed |
|---|---|
| Stage C (domain, classification, corpus conformance) | 246 |
| Stage D (persistence, persistence conformance, plan-version identity) | 150 |
| Stage F (AI boundary, curated evaluation) | 153 |
| Stage G (AI persistence, replay persistence) | 106 |
| Stage H-A (offline harness) | 84 |
| Stage I (review application) | 112 |
| Stage J (operator workflow) | 45 |
| Stage K (confirmed BAEC handoff) | 32 |
| Stage L (release) | 17 |
| All Engine 2 tests | 945 |
| Full repository suite | 6,763 passed, 2 deselected |

The two deselected tests are the opt-in live tests `tests/live/test_live_extraction.py` (Engine 1) and `tests/live_engine2/test_live_engine2_stage_h.py` (Engine 2 Stage H). No test was skipped. The corpus distribution is identical through the domain core, persistence, and the Stage I application layer.

## 6. Final authority and boundary audit (deterministic software verification)

Each line is enforced by a test in the repository; the Stage L audit tests scan every Engine 2 production module.

| No path exists for | Enforced by |
|---|---|
| AI creating a `HumanCorrespondenceReview` or an outcome | AI modules never name review, classifier, or outcome types (Stage F, G, L audits); proposals are refused as findings (Stage I, J) |
| A caller choosing the outcome | No argument accepts one; only the locked classifier derives it (Stages C, D, I, J, L) |
| Correspondence creating an opportunity or changing account state | No Engine 2 module names account state, transitions, or opportunity creation (Stage C, L audits); the demonstration and handoff tests show zero transitions |
| The UI writing review rows or classifying | The page reads and writes only through Stage I (Stage J) |
| Stage K writing Engine 1, consuming a grant, or changing staleness | Query-only connection required; byte-identical Engine 1 store; no grant, bridge, or staleness writer (Stage K, L) |
| An unauthorized source becoming evidence | Capture refuses it; it is recorded as a refusal only (Stages C, D) |
| A superseded Observation silently becoming current | Usability is the ledger's own; superseded and retracted evidence stays visible and unusable (Stages C, I, J) |
| Silent repair of a corrupted record | Every load rebuilds through the domain and raises on corruption (Stages D, G, I, J, K) |
| Implicit model selection | One pinned model literal in the opt-in harness; no `latest` token in Engine 2 production code (Stages E, H-A, L) |
| Stage H running without explicit gates | The live gate and default deselection (Stage H-A, L) |

## 7. Limitations register (accepted prototype limitations)

- The conceptual basis is an unpublished working paper that has not been peer reviewed.
- All data is synthetic. Test counts and corpus results are software verification, not empirical validation.
- There is no evidence that correspondence predicts revenue, purchases, or conversion.
- Buyer conditions can become stale. Engine 2 relies on Engine 1's stored staleness status, and no Engine 1 code changes it yet.
- The handoff is point in time; there is no synchronization with Engine 1.
- There is no automatic outreach, no CRM automation, and no automatic account-state transition.
- The Phase 7 human-reviewed normalized wording does not cross the handoff.
- Advisory AI has not been qualified by a live run (Stage H-B).
- Human approvals and reviewer identities are self-asserted; the release demonstration scripts them.
- No claim of production readiness or production security is made.

## 8. Stage H-B decision point

Stage H-B is a single controlled research run of one pinned model on one synthetic input. It tests nothing that Engine 2's authority depends on: AI output is advisory, optional, stored separately, and never part of the human workflow or the outcome. The decision whether to run it before a v1.0 designation is recorded in the Stage L report and is a human decision.
