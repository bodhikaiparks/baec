# Phase 6 Final Verification

**Project:** BAEC Trigger Intelligence
**Status:** Phase 6 closed. A successful engineering closeout with a documented model-qualification limitation, **not** a successful model qualification.
**Scope:** Synthetic evaluation of this implementation only. Nothing here validates BAEC theory, predicts purchases, or measures any model's general quality. No model is designated as the product default on the basis of Phase 6.

---

## 1. Locked identities

| Item | Value |
|---|---|
| Model (both live runs) | `claude-sonnet-5-5` (no other model run) |
| Corpus | `baec-extraction-live-corpus/v2`, 17 cases C01–C17, SHA-256 `95a7bd5449a37f1ca47a5486432991aee37f60c80f040cfe3b1dd8e69daf17fc` |
| Corpus v1 (historical, unchanged) | `baec-extraction-live-corpus/v1`, SHA-256 `107fb99be29fb5e848cfae12b580b14e0290d50c07e8c0f4ad1ac92cff3e8684` |
| Validator | `baec-extraction-validation/v2` (unchanged between the runs) |
| Report format | `baec-live-evaluation-report/v2` |
| Unchanged labels | task `baec-extraction-task/v1`, input `baec-extraction-input/v1`, output schema `baec-extraction-output/v1`, request spec `baec-ai-request-spec/v1`, canonicalization `baec-canonical-json/v1`; `max_tokens` 4096; timeout 180 s; `max_retries` 0 |

| Run | Source commit | Prompt | Prompt SHA-256 | Request digest (golden) |
|---|---|---|---|---|
| **E2** | `277ab0f421d94acc69f860dd18a8dc95b1175df1` | `baec-extraction-prompt/v1` | `b1782f1ce0afdd96eb335cece03912b9aa53c3a5ac3c71a2017f1cbe9ba5eadd` | `298bf6aac27bb69df99412d63c9586c79697d7342f5bb1a00dbabdb56b9c20a8` |
| **E5** | `9ad1834bbef656acb359d2f8e9844f09b721e38f` | `baec-extraction-prompt/v2` | `d6296491be408c9a4b9b9561e6e207ff8890eb0f0460ce0c7300ab2cf0ed8845` | `e142cb5c71c9b173931f04901cc18b1352d97ed78a3eba916c86850e5e71cdde` |

Between the two source commits the only production change is the prompt (`baec_app/ai/prompts.py` and the three lines of `baec_app/ai/service.py` that select it). Validation, provider, composition, the live harness, the report format, and both corpora are byte-identical.

## 2. Evidence

The sanitized runtime reports are kept outside the repository and are **not committed**. Each holds only identifiers, version labels, closed codes, sanitized model IDs, counts, and timings.

| Run | Report file | SHA-256 |
|---|---|---|
| E2 | `baec-extraction-live-corpus-v2__claude-sonnet-5-5__277ab0f421d9__20261005T184056Z.json` | `166d0703b52c80571c16092d53ac45ccd84f1204f787cb5ddc730bebfbc14057` |
| E5 | `baec-extraction-live-corpus-v2__claude-sonnet-5-5__9ad1834bbef6__20261005T193027Z.json` | `47ea6cedb55fd5a183bd52bc87d777724c0746f734e1013df96504201f7164fc` |

**Reproducing the verification offline** (no API call): check the file's SHA-256, then strict-load it with the committed loader, which re-checks canonical bytes, schema, every aggregate, and the audit:

```python
from tests.live.compare import load_report
report = load_report("<path to the report file>").report
```

Both reports load without error under the committed loader at `9ad1834`.

## 3. Acceptance criteria (locked before each run)

- **Operational verification PASS:** `operationally_valid = true`; `calls_attempted = 17`; C01–C17 all represented; `authority_unchanged = true`; no `audit_failure`, `cleanup_failure`, or `report_failure`; the report reloads under the strict v2 loader.
- **Behavioral eligibility PASS:** zero behavioral critical failures. Every v2 case carries CORE-TERMINAL-SUCCESS, so any behavioral terminal failure is a `terminal_failure` critical and makes the model ineligible.
- Successful artifacts, hard and observational totals, terminal statuses, tokens, and time are descriptive and never override eligibility.

## 4. Results

| | E2 (prompt v1) | E5 (prompt v2) |
|---|---|---|
| Calls attempted | 17 | 17 |
| `operationally_valid` | true | true |
| Operational failures | none | none |
| Authority unchanged | true | true |
| Provenance verified | 17 / 17 | 17 / 17 |
| Temporary database cleaned | yes | yes |
| Strict reload | yes | yes |
| **Operational verification** | **PASS** | **PASS** |
| Terminal statuses | 9 success, 8 semantic_validation_failure | 15 success, 2 semantic_validation_failure |
| Successful artifacts | 9 / 17 | 15 / 17 |
| Hard checks | 92 / 110 | 108 / 110 |
| Observational checks | 11 / 24 | 22 / 24 |
| Behavioral critical cases (`terminal_failure`) | C01, C02, C04, C05, C07, C09, C16, C17 | C05, C07 |
| **Behavioral eligibility** | **FAIL** | **FAIL** |
| Input / output tokens | 39,077 / 10,014 | 48,002 / 8,675 |
| Elapsed | 102.8 s | 85.5 s |

**E5 failures.** C05 and C07 each ended in `semantic_validation_failure` with exactly one code, `explanation_number_unsupported`: production validation rejected a criterion explanation containing a number not grounded in the interaction, and **no artifact was created** for either case. Their only failed hard check is CORE-TERMINAL-SUCCESS (`terminal_failure`); their only failed observational checks are C05-O1 and C07-O1. By their corpus purposes, C05 is a satisfied buyer who names no contingency and C07 is an event that already happened with evaluation under way. The same code failed the same two cases in E2. The raw model text is deliberately not retained, and no wording is inferred here.

**`claude-sonnet-5-5` qualified as default model: NO.** The precommitted rule requires zero behavioral critical failures; 15 / 17 does not meet it, and the rule is not relaxed.

## 5. C15–C17 in E5

All three produced accepted artifacts, so the corpus-v2 adversarial checks were exercised by real live output, not only vacuously:

- **C15** (unsupported numeric invention): success; artifact present; every hard check passed, including `C15-H2` (`free_text_forbids_numeric_content`).
- **C16** (comparator mutation, LESS_THAN 95%): success; artifact present; CORE-TERMINAL-SUCCESS, `C16-H1`, `C16-H2`, `C16-H3`, and `C16-H4` all passed.
- **C17** (legitimate formatting): success; artifact present; CORE-NUMBERS, CORE-TERMINAL-SUCCESS, `C17-H1`, `C17-H2`, and `C17-H3` all passed.

This shows the checks ran on accepted artifacts and passed on this synthetic corpus. It says nothing beyond these cases.

## 6. E2 → E5 (descriptive only)

The prompt-v2 re-verification exhibited substantially fewer behavioral failures than the prior prompt-v1 verification (8 → 2 failing cases). The recurring E2 classes `uncertainty_comparator_changed`, `*_comparator_unresolved`, and `explanation_comparator_changed` did not occur in E5; `explanation_number_unsupported` recurred in C05 and C07.

This is an engineering before/after of one run each, not a controlled or randomized experiment, and no causal effect size is claimed. The two reports are **not comparable** under the locked report-comparison contract: the committed `compare_reports` returns `defer` / `not_comparable` with mismatches `same_requested_model`, `source_commit`, and `version_prompt`. They are not ranked against each other.

## 7. Limitations and future work

- **Remaining observed failure class:** `explanation_number_unsupported` in C05 and C07.
- Possible future work, none of it part of Phase 6: structurally constraining explanation prose; evolving the output schema; evaluating another model.
- Another paid run of the same configuration is not justified: it would repeat this evidence.
- All other limitations in `PHASE6D_AI_BEHAVIOR_HARDENING_DESIGN.md` §12 still apply.

## 8. Phase 6 conclusion

Phase 6 successfully implemented and verified:

- structured Claude extraction;
- immutable, persistent AI provenance;
- deterministic semantic grounding;
- fail-closed artifact creation;
- durable sanitized evaluation reports;
- strict independent report reload and recomputation;
- deterministic comparison machinery;
- adversarial corpus v2;
- prompt/validator contract alignment;
- live operational verification.

The final Sonnet verification remained behaviorally ineligible because two cases produced disallowed explanation-level numeric content. The validator rejected both, and no artifact was created for either case.

No model is designated as the product default on the basis of Phase 6. This is a successful engineering closeout with a documented model-qualification limitation, not a successful model qualification.
