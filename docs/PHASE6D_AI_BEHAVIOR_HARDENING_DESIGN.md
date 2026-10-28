# Phase 6D Design: AI Behavior Hardening

> **Historical design — implemented and verified.**
> Phase 6D is complete; final verification is recorded in `docs/PHASE6_FINAL_VERIFICATION.md`. The sections below are the design as approved in Phase 6D-A, with their marked clarifications.

**Project:** BAEC Trigger Intelligence
**Baseline:** commit `7a3bae8f38aa0c15c90fcbd9415dc914744fe24c` (Phase 6C closed), schema version 5, 3970 tests passing, 1 deselected.
**Authority:** The manuscript, then `docs/RESEARCH_CONTRACT.md`, then the approved Phase 4, Phase 5, and Phase 6 designs (as amended), then this design, then code. This design adds no research rule.
**Phase 6C:** closed and locked. Its corpus, results, and evaluation record (`docs/PHASE6C_LIVE_EVALUATION.md`) are historical evidence and are not edited by Phase 6D.

---

## 0. How rules are labeled

Every rule in this document carries one of four labels.

| Label | Meaning |
|---|---|
| **Paper-backed constraint** | Comes from the manuscript through the Research Contract. Phase 6D relies on these and changes none of them |
| **Implementation safeguard** | A production software rule chosen by this project. It decides only whether an AI artifact exists. It is not a BAEC research finding |
| **Evaluation rule** | A rule of the opt-in synthetic live evaluation (`tests/live/`). It never runs in production and never changes production acceptance |
| **Known limitation** | Something Phase 6D explicitly does not solve |

Phase 6C and Phase 6D evaluations measure this implementation and model behavior on synthetic cases. They do not validate BAEC theory, show prospective validity, predict purchases, or produce a BAEC quality score.

## 1. Background: the five Phase 6C findings

Recorded in `docs/PHASE6C_LIVE_EVALUATION.md` §9.5. This is implementation evidence only.

| # | Finding | Root cause in the code at `7a3bae8` |
|---|---|---|
| F1 | C09 / CORE-NUMBERS: an accepted artifact contained a normalization number absent from the source | `baec_app/ai/validation.py` has no numeric, unit, or comparator rule. Only the evaluation-time CORE-NUMBERS check caught it |
| F2 | Nine `semantic_validation_failure` outcomes had no retained reason | The codes **were** persisted in `ai_run_results.failure_codes`, but the harness never projected them, and the temporary database was deleted. The data layer also accepts any token matching `[a-z][a-z0-9_]*(:…)?`, not a closed vocabulary |
| F3 | C04 failed validation yet scored 6/6; C12 and C14 failed validation without a critical label | `terminal_success` was a per-case check with inconsistent kind and criticality |
| F4 | Observational results were not retained | `CheckResult` held them, but nothing emitted or serialized them |
| F5 | The comparison was applied by hand | `compare_models` accepts only in-memory reports, and no serialized report existed |

## 2. Paper-backed constraints relied on

These are unchanged. Phase 6D cites them; it does not reinterpret them.

- **BAEC definition and the four constitutive criteria** (Research Contract, definitional). An AI artifact never classifies a BAEC, and nothing in Phase 6D maps an artifact to a classification.
- **RC-17 Stringency (PROPOSED, manuscript §4.4).** Stringency is the magnitude of articulated change required before the buyer expects evaluation to become worthwhile. The system records what the buyer said and does not rate it.
- **RC-33 / immutable buyer evidence.** Exact buyer evidence stays immutable and separate from AI-authored normalization.
- **RC-31 / RC-32.** A model-supplied value is never human approval; AI interpretation is `AI_INFERENCE`.

**RC-18 (threshold preservation) is itself labeled IMPLEMENTATION in the Research Contract.** Phase 6D's grounding guard supports RC-18. Neither RC-18 nor the guard is a manuscript finding.

## 3. Locked decisions

| # | Decision | Label |
|---|---|---|
| 6D-1 | Production numeric and stringency grounding in semantic validation, before artifact creation (§4) | Implementation safeguard |
| 6D-2 | Field scope: all model-authored free text (§4.1) | Implementation safeguard |
| 6D-3 | One deterministic tokenizer and canonicalizer; approved equivalences only; no arithmetic (§4.3) | Implementation safeguard |
| 6D-4 | Currency: no inference from ambiguous symbols; no conversion (§4.4) | Implementation safeguard |
| 6D-5 | Compound numeric structures preserved exactly or canonicalized by an explicit rule, otherwise fail closed (§4.5) | Implementation safeguard |
| 6D-6 | Comparators: adding, dropping, or changing a grounded comparator is rejected; conservative lexicon (§4.6) | Implementation safeguard |
| 6D-7 | Multiple closed, status-specific, sorted, unique failure codes, enforced in three layers (§5) | Implementation safeguard |
| 6D-8 | Schema v5 → v6: `ai_runs.validation_version`; `baec-extraction-validation/v1` recorded prospectively from 6D-B1, bumped to `/v2` by the grounding rules in 6D-B2 (§6) | Implementation safeguard |
| 6D-9 | `CORE-TERMINAL-SUCCESS` in corpus v2 (§7.1) | Evaluation rule |
| 6D-10 | CORE-NUMBERS stays as an independent implementation (§7.2) | Evaluation rule |
| 6D-11 | Observational results retained, never ranking (§7.3) | Evaluation rule |
| 6D-12 | Serialized report `baec-live-evaluation-report/v1` (§8) | Evaluation rule |
| 6D-13 | Report persistence through `BAEC_LIVE_REPORT_DIR`, checked before any attempt (§9) | Evaluation rule |
| 6D-14 | `load_report` and `compare_reports`, with comparability first (§10) | Evaluation rule |
| 6D-15 | Corpus v1 preserved and pinned; corpus v2 adds C15–C17 (§11) | Evaluation rule |
| 6D-16 | Explicit accepted limits (§12) | Known limitation |
| 6D-17 | Build sequence 6D-A … 6D-E (§13) | — |
| 6D-18 | Testing doctrine (§14) | — |

---

## 4. Validation v2: numeric and stringency grounding

**Label: Implementation safeguard (supports RC-18).**

**Rule.** Model-authored artifact text may restate source-grounded numeric information, but it may not introduce unsupported numeric assertions, alter a grounded magnitude or unit, or change, drop, or add a buyer threshold comparator.

**Placement.** The rule is part of `validate_extraction` (stage 3), which already receives `source_text`. A violation adds closed failure codes, so `ExtractionService._conclude` records `semantic_validation_failure` and builds **no** `ArtifactRecord`. Raw provider output follows the existing provenance rules unchanged (Phase 6 design §10.4, D1).

**Why it cannot create or deny a BAEC.** As with D8 (Phase 6 design §8.1), the rule only decides whether an AI artifact exists. The worst case of over-strictness is a lost AI interpretation, recorded as a failure.

### 4.1 Field scope

| Guarded (model-authored free text) | Not guarded |
|---|---|
| `normalized_condition` | `source_excerpts[].text`: already validated as verbatim source text |
| `normalized_evaluation_link` | `excerpt_id`, `excerpt_refs`, `source_interaction_id`: identifiers |
| every `criterion_hypotheses[].explanation` | `analysis_status`, `criterion`, `status`, `attributed_speaker`: closed enums |
| every `uncertainties[]` entry | |

Fail-closed behavior applies even when an unsupported number appears only in explanatory or meta text. There is no exception for model-authored meta-numbers such as "4 criteria" or "2 excerpts".

### 4.2 Tokens and magnitudes

One deterministic tokenizer is applied identically to the source interaction text and to each guarded field. It produces:

- **Simple magnitudes:** `(value, kind, unit, comparator)`.
  - `value`: an exact decimal (Python `Decimal`), never a float.
  - `kind` (numeric kind), a closed set: `plain`, `percent`, `percentage_point`, `currency:unspecified_dollar`, `currency:USD`, `currency:EUR`, `currency:GBP` (§4.4). Numeric kind is distinct from unit: kind says what sort of quantity the number is (a bare count, a percentage, an amount of a currency); unit is a measurement or time unit from the closed unit lexicon.
  - `unit`: one of the closed unit lexicon (`day`, `business_day`, `week`, `month`, `quarter`, `year`, `hour`), case-insensitive, with singular and plural folded (`week` ≡ `weeks`); otherwise `none`. A grounded unit is part of the numeric meaning (§4.7).
  - `comparator`: §4.6.
- **Compound tokens:** §4.5. They are compared by exact echo.

Only ASCII digits form simple magnitudes. Any token containing a non-ASCII decimal digit (full-width, Arabic-Indic, superscript, and so on) is a compound token and must be echoed exactly.

### 4.3 Canonicalization

| Form | Treatment |
|---|---|
| Comma grouping: `12,500` ≡ `12500` | **Canonicalized.** A comma counts as grouping only in the form `d{1,3}(,ddd)+`; any other comma between digits makes the token compound |
| Decimal magnitude: `10` ≡ `10.0` ≡ `10.00` | **Canonicalized** to equal numeric magnitude. This is equality of value, not arithmetic |
| Percent: `10%` ≡ `10 %` ≡ `10 percent` ≡ `10 per cent` | **Canonicalized** to kind `percent` (one ASCII space or no-break space allowed before `%`; words case-insensitive) |
| `percentage point(s)` | Its own numeric kind, `percentage_point`. **`percent` ≠ `percentage_point`**: `10%` ≢ `10 percentage points`, `5 percent` ≢ `5 percentage points`. Changing between them is `*_numeric_kind_changed`, never `*_unit_changed` |
| Number words (closed grammar below): `ten` ≡ `10` | **Canonicalized** |
| Leading plus: `+5` ≡ `5` | **Canonicalized** |
| Negative: `-5`, `−5` (U+2212) | **Distinct** from `5`. A sign counts only at a token start (after start of text, whitespace, or `(`) |
| Leading zeros in a standalone magnitude: `05` ≡ `5`, `05.0` ≡ `5` | **Canonicalized** under decimal magnitude equality. Leading zeros inside compound structures (dates, ratios, identifiers, codes, version-like values) remain structural: §4.5 |
| Scale suffixes and words after digits (`12.5k`, `12.5K`, `12.5 thousand`, `3m`, `2bn`, `5x`) | Compound token: exact echo only. **Never** converted (`12.5k` ≢ `12500`) |
| Unit conversion (`4 weeks` → `28 days`), annualization, sums, differences, inferred percentages, currency conversion, any other derived value | **Rejected**: the derived magnitude is not in the source |

**Number-word grammar (closed, case-insensitive, whole words only).**

```
units   := zero | one | two | three | four | five | six | seven | eight | nine
teens   := ten | eleven | twelve | thirteen | fourteen | fifteen | sixteen | seventeen | eighteen | nineteen
tens    := twenty | thirty | forty | fifty | sixty | seventy | eighty | ninety
small   := units | teens | tens | tens "-" units          # "twenty-five"
hundreds:= small | units " hundred" [ " and"? " " small ]
number  := hundreds | hundreds " thousand" [ " and"? " " hundreds ]
```

- Words outside the grammar are not numbers: ordinals (`first`, `second`), `a`, `an`, `couple`, `few`, `several`, `million`, `billion`.
- **Exact-echo multiplier words:** `half`, `double`, `twice`, `triple`, `dozen`. They are accepted only if the identical word occurs in the source, and they are never converted.
- `quarter` is a unit word, never a fraction.
- A number word followed by a percent word or a unit takes that kind or unit (`ten percent` ≡ `10%`).

### 4.4 Currency

Closed lexicon. A currency marker may precede the magnitude (`$10`, `USD 10`, `US$10`, `€10`, `£10`) or follow it (`10 USD`, `10 euros`, `10 US dollars`, `10 pounds sterling`). The longest match wins (`US dollars` before `dollars`; `pounds sterling` before `pounds`).

| Numeric kind | Forms |
|---|---|
| `currency:EUR` | `€`, `EUR`, `euro`, `euros` |
| `currency:GBP` | `£`, `GBP`, `British pound`, `British pounds`, `pound sterling`, `pounds sterling` |
| `currency:USD` | `USD`, `US$`, `US dollar`, `US dollars`, `U.S. dollar`, `U.S. dollars`, `United States dollar`, `United States dollars` |
| `currency:unspecified_dollar` | `$`, `dollar`, `dollars` (unqualified) |
| **Unresolved** | bare `pound`, `pounds`: may denote currency or weight. A magnitude followed by bare `pound(s)` is a compound token (exact echo only, §4.5) |

- **Case.** Symbols (`$`, `€`, `£`, `US$`) match exactly. ISO codes (`USD`, `EUR`, `GBP`) and the qualifiers `US` and `U.S.` match in upper case only, so ordinary words such as `us` are never read as a currency qualifier. The words `dollar(s)`, `euro(s)`, `pound(s)`, `sterling`, `British`, `United States` match case-insensitively.
- **No inference.** `$` and unqualified `dollar(s)` are never treated as USD. A specific currency is recorded only when one of the explicit forms above establishes it.
- **No conversion.** Nothing is ever converted across currencies.
- **Kind comparison is exact.** `$12,500` ≡ `12,500 dollars` and `€40` ≡ `EUR 40` ≡ `40 euros`. Any other pairing is a numeric-kind change (§4.7): explicit currency specificity may not be added, weakened, or changed (`USD 10` → `$10`, `$10` → `USD 10`, `EUR 10` → `GBP 10` all fail).

### 4.5 Compound numeric structures

A token is compound when it contains a digit and is not wholly a simple magnitude. Compounds are **never** validated as independent digit runs, so a permutation such as `3/15` → `15/3` cannot pass merely because both components appear in the source.

| Compound | Validation v2 treatment |
|---|---|
| Dates: `3/15`, `3/15/2027`, `2027-03-15`, month name plus day (`March 15`, `15 March`) | Exact echo |
| Times: `9:30` | Exact echo |
| Ratios: `3:1` | Exact echo |
| Ordinals: `2nd`, `3rd`, `21st` | Exact echo |
| Period labels: `Q3`, `H1`, `FY27` | Exact echo |
| `24/7`, version-like `1.2.3`, any other alphanumeric digit token | Exact echo |
| Compact numeric range: `10-15%`, `10–15 weeks` (hyphen or en dash between two simple magnitudes) | **Explicit canonical form** `(low, high, kind, unit)`, order-preserving. The kind and unit after the range apply to both ends. Grounded only by an identical canonical range in the source; `15-10` ≢ `10-15` |
| Hyphenated unit adjective: `4-week` | Simple magnitude with unit `week` (≡ `4 weeks`) |
| A magnitude followed by `pound` / `pounds` | Exact echo (currency or weight is unresolved, §4.4) |
| Anything containing a digit that matches none of the above | Exact echo, otherwise rejected (fail closed) |

**Exact echo** means the identical character sequence occurs as a token in the source. It is case-sensitive, with no Unicode normalization (Phase 3 doctrine). Leading zeros are structural inside compounds (`03/05` ≢ `3/5`), so standalone leading-zero canonicalization (§4.3) never applies to them. Validation v2 approves only one compound canonicalizer, compact ranges. Matching compound components digit by digit is forbidden.

### 4.6 Comparators

Classes reuse the domain vocabulary `ThresholdComparator` (`baec_app/domain/enums.py`): `GREATER_THAN`, `AT_LEAST`, `LESS_THAN`, `AT_MOST`, `EXACTLY`, `APPROXIMATELY`. A magnitude with no comparator expression has class `NONE`.

**Classified lexicon (validation v2).** An expression is classified only when it is syntactically bound to a numeric magnitude (see Binding below). Case-insensitive, whole words; the longest deterministic match wins (so `no less than` is never read as `less than`, and `not more than` never as `more than`).

| Class | Prefix expressions | Postfix expressions |
|---|---|---|
| `GREATER_THAN` | `>`, `more than`, `greater than`, `above`, `exceeds`, `exceeding`, `in excess of` | — |
| `AT_LEAST` | `>=`, `≥`, `at least`, `no less than`, `not less than`, `minimum of` | `or more`, `or greater`, `or higher`, `or above`, `or longer`, `and up`, postfix `+` (rule below) |
| `LESS_THAN` | `<`, `less than`, `fewer than`, `below`, `under` | — |
| `AT_MOST` | `<=`, `≤`, `at most`, `no more than`, `not more than`, `maximum of`, `up to` | `or less`, `or fewer`, `or lower`, `or below`, `or shorter` |
| `EXACTLY` | `=`, `exactly` | — |
| `APPROXIMATELY` | `about`, `around`, `roughly`, `approximately`, `~` | — |

**`over` is unresolved in all contexts (validation v2).** `over` is never mapped to `GREATER_THAN`, and no contextual heuristic (such as a temporal-noun lexicon) decides when it is a comparator. `over 10%`, `over $500`, `over 20 units`, and `over 4 weeks` all contain an unresolved comparator expression:
- exact preservation may pass (source `over 10%` → model `over 10%`);
- semantic rewriting does not (source `over 10%` → model `more than 10%` fails with `*_comparator_unresolved`), because validation v2 deliberately does not establish that equivalence.

This is a conservative, fail-closed implementation choice. Contextual classification of `over` may be considered only in a future validator version.

**Postfix `+` rule.** A `+` is the `AT_LEAST` comparator only when all of these hold:
- it directly follows a recognized standalone magnitude or that magnitude's bound unit (`5+`, `5+ weeks`, `5 weeks+`);
- it is not a prefix sign: `+5` remains the approved positive-sign canonicalization (§4.3);
- it is not part of a compound, date, version, or code structure: a `+` immediately followed by a digit (`5+3`) makes the token compound (exact echo only).

**Recognized but unclassified (unresolved) expressions.** These are detected when bound to a magnitude, so that dropping or changing them cannot pass silently, but no comparator meaning is inferred:

- `past`, `beyond`, `within`, `over`

Comparator semantics are never inferred from arbitrary prose. Promoting any unresolved expression to a class is a new validation version.

**Binding.** A prefix expression binds to a magnitude only when immediately adjacent: the only thing allowed between them is whitespace, plus the currency symbol that belongs to the magnitude. A postfix expression binds when it immediately follows the magnitude and its unit. An expression bound to no magnitude is ignored. Two or more expressions bound to one magnitude make its comparator **conflicting**.

### 4.7 The grounding decision

For each simple magnitude `m` in a guarded field:

1. Let `V` be the source magnitudes with the same `value`. `V` is empty → `<field>_number_unsupported`.
2. Let `S` be the members of `V` with the same numeric kind. `S` is empty → `<field>_numeric_kind_changed`. Examples:
   - `10` → `10%` and `10%` → `10` (plain ↔ percent);
   - `$10` → `EUR 10` (currency changed);
   - `USD 10` → `$10` (explicit currency weakened to unspecified dollar);
   - `EUR 10` → `GBP 10` (explicit currency changed).

   Numeric-kind changes never use `unit_changed`.
3. No member of `S` has exactly `m`'s unit, where `none` counts as a unit value → `<field>_unit_changed`. This code is used only for measurement and time units from the closed unit lexicon. A grounded unit is part of the numeric meaning, so all of these are rejected:
   - a changed or substituted unit (`4 weeks` → `4 days`);
   - a dropped grounded unit (`4 weeks` → `4`);
   - a unit introduced where the source magnitude has none (`4` → `4 weeks`).

   Only singular/plural folding within the closed unit lexicon is allowed (`week` ≡ `weeks`).
4. Restrict `S` to the members with exactly `m`'s unit. The comparator is accepted only when some member has the same comparator as `m`:
   - a classified class equal to `m`'s class; or
   - `NONE` equal to `NONE`; or
   - the identical unresolved expression (exact echo, case-insensitive).
5. Otherwise:
   - `m`'s comparator is unresolved or conflicting → `<field>_comparator_unresolved`;
   - else → `<field>_comparator_changed`. This covers adding a comparator (`NONE` in the source), dropping one (`NONE` in model text), and changing one (`more than` → `at least`; `less than` → `at most`).

For each compound token in a guarded field with no exact echo in the source → `<field>_compound_unsupported`. Multiplier words follow the exact-echo rule and fail with `<field>_number_unsupported`.

`<field>` is the closed token `normalization` (both normalizations), `explanation`, or `uncertainty`.

> **Phase 6D-B2 clarification (amendment; label: Implementation safeguard).**
>
> The generic §4.7 rule that a dropped comparator produces `*_comparator_changed` remains authoritative for classified comparators.
>
> For the deliberately unresolved comparator expressions `past`, `beyond`, `within`, and `over`, exact preservation is the only accepted comparator behavior. If a matching source occurrence contains one of these unresolved expressions, then:
>
> - exact preservation → allowed;
> - classified rewrite → `*_comparator_unresolved`;
> - unresolved-to-unresolved rewrite → `*_comparator_unresolved`;
> - dropped expression → `*_comparator_unresolved`.
>
> No semantic comparator class is assigned to these words. This is a conservative implementation refinement of the locked validation-v2 unresolved-comparator policy (§4.6), not a research claim.

### 4.8 How a C09-class output is stopped

A model-authored normalization containing a magnitude absent from the source produces `normalization_number_unsupported` in `validate_extraction`. `_conclude` then records `semantic_validation_failure` with the sorted codes and the raw output, and the artifact is never built. This is described generically; the Phase 6C C09 model output is not quoted or reconstructed.

---

## 5. Failure-code contract

**Label: Implementation safeguard.**

- **Multiple codes** per terminal result, because one output can violate several invariants.
- **Closed vocabulary**, status-specific. The complete vocabulary is in §5.1: 43 distinct `failure_codes` tokens (20 existing semantic + 18 new semantic grounding + 5 existing parse).
- **Lexicographically sorted and unique**, as stored. Duplicates are forbidden.
- **Free of variable content.** No excerpt ID, source text, model text, prompt text, exception text, or other variable value may enter a code. Field scope uses closed tokens in the code name, never a `:suffix`. The suffixed forms in the Phase 6 design §8 tables (`excerpt_not_in_source:<id>`, `text_too_long:<field>`, and so on) were never implemented and are superseded (see the note added to Phase 6 design §8).
- **Parse-failure detail codes** (mapping Pydantic errors) are deferred; 6D-B1 does not add them.

**Enforcement in three layers:**

| Layer | Where | Enforcement |
|---|---|---|
| AI layer | `ai/validation.py`, `ai/service.py`, `ai/provenance.py` `TerminalResult` | Codes come only from the closed constants; `TerminalResult` refuses codes outside the vocabulary for its status, or not sorted and unique |
| Data records | `data/ai_provenance.py` `AiRunResultRecord`, on construction and on readback in `get_result` | The same checks. A stored violation raises `PersistenceIntegrityError` (fail closed). The vocabulary is defined in `data/database.py` and duplicated in the AI layer with an equality test, following the existing `AI_API_ERROR_CATEGORIES` / `API_ERROR_CATEGORIES` precedent (the AI service never imports the data layer) |
| SQL backstop | schema v6 trigger `ai_run_results_failure_codes_closed` (`BEFORE INSERT`), plus `CHECK` constraints | Enforces the cardinality in §5.1. Rejects any `failure_codes` that is not a valid JSON array of text, contains a value outside the status's vocabulary, or is not strictly increasing |

### 5.1 Complete closed vocabulary

Every token is a fixed lowercase machine string with no variable part. Ordering is by code point (Python `sorted`, SQLite `BINARY`), which is identical for these ASCII tokens.

**A. Existing semantic-validation codes** (`baec_app/ai/validation.py` `SEMANTIC_FAILURE_CODES`, unchanged). **Legal only under `semantic_validation_failure`.** 20 tokens:

`criterion_set_invalid`, `duplicate_excerpt_id`, `duplicate_excerpt_reference`, `duplicate_excerpt_text`, `excerpt_blank`, `excerpt_id_blank`, `excerpt_not_verbatim`, `explanation_blank`, `explanation_too_long`, `normalization_blank`, `normalization_too_long`, `possible_language_without_excerpt`, `source_interaction_mismatch`, `supported_without_excerpt`, `too_many_excerpt_references`, `too_many_excerpts`, `too_many_uncertainties`, `uncertainty_blank`, `uncertainty_too_long`, `unknown_excerpt_reference`

**B. New validation-v2 grounding codes** (6D-B2). **Legal only under `semantic_validation_failure`.** 18 tokens, three closed field scopes × six endings:

| | `normalization_` (both normalizations) | `explanation_` | `uncertainty_` |
|---|---|---|---|
| unsupported magnitude or multiplier word | `normalization_number_unsupported` | `explanation_number_unsupported` | `uncertainty_number_unsupported` |
| grounded value, changed numeric kind | `normalization_numeric_kind_changed` | `explanation_numeric_kind_changed` | `uncertainty_numeric_kind_changed` |
| unit changed, substituted, dropped, or introduced | `normalization_unit_changed` | `explanation_unit_changed` | `uncertainty_unit_changed` |
| compound token without exact echo | `normalization_compound_unsupported` | `explanation_compound_unsupported` | `uncertainty_compound_unsupported` |
| classified comparator added, dropped, or changed | `normalization_comparator_changed` | `explanation_comparator_changed` | `uncertainty_comparator_changed` |
| unresolved or conflicting comparator without exact echo | `normalization_comparator_unresolved` | `explanation_comparator_unresolved` | `uncertainty_comparator_unresolved` |

None of these collide with group A or group C. Combined semantic vocabulary: 38 tokens.

**C. Parse-failure codes** (`baec_app/ai/service.py` `PARSE_FAILURE_CODES`, unchanged; detailed Pydantic codes deferred). **Legal only under `parse_failure`.** 5 tokens:

`invalid_json`, `missing_stop_reason`, `missing_text_block`, `multiple_text_blocks`, `structured_output_validation_failed`

**D. Other terminal statuses.** `success`, `refusal`, `max_tokens`, `unexpected_stop`, `api_error`, `transport_failure`, `model_mismatch`, and `interrupted` carry **no** `failure_codes` (NULL), as today. Two statuses carry a different, already closed, single-valued column, `failure_category`, which Phase 6D does not change:

| Status | `failure_category` vocabulary |
|---|---|
| `api_error` | `authentication`, `permission`, `rate_limited`, `overloaded`, `invalid_request`, `server_error`, `other` |
| `transport_failure` | `connection_not_established` (with `not_sent`), `timeout_or_disconnect` (with `unknown`) |
| every other status | none (NULL) |

**Cardinality (enforced in all three layers from schema v6).**

| Status | `failure_codes` |
|---|---|
| `parse_failure` | **exactly one** code from group C. This reflects the existing terminal precedence: the service stops at the first parse failure it meets. It tightens schema v5, which allowed NULL |
| `semantic_validation_failure` | **one or more** codes from groups A and B, sorted and unique |
| every other status | **zero** codes (NULL) |

**Totals:** 38 semantic + 5 parse = **43** distinct tokens, with no collisions. The separate `failure_category` behavior is unchanged.

## 6. Schema v6 and validator provenance

**Label: Implementation safeguard.**

**Why a schema change.** Stage-3 rules decide whether an artifact exists, but no version label records which rules applied. Validation v2 changes acceptance while the prompt, input, output schema, and request digest stay the same. Without a recorded validator version, provenance could not tell why identical requests were accepted under one rule set and rejected under another. `TASK_VERSION` is not overloaded for this.

**Labels and sequence.**

| Period | Validator | Persisted identifier |
|---|---|---|
| Through commit `7a3bae8`, including both Phase 6C live runs | **Legacy, pre-versioned Phase 6C semantic validator** (the D8 rules plus the stage-3 core invariants) | **None.** No identifier is assigned retroactively to Phase 6C runs or to any schema-v5 row |
| From 6D-B1 (schema v6) | The same pre-grounding rules, now labeled | `baec-extraction-validation/v1` |
| From 6D-B2 | The rules above plus numeric, numeric-kind, unit, compound, and comparator grounding (§4) | `baec-extraction-validation/v2` |

- `/v1` is **prospective from 6D-B1 onward**. It labels the existing pre-grounding rules as recorded in schema-v6 runs. It does **not** describe or retroactively label the historical Phase 6C runs, even though those rules were the same.
- 6D-B2 changes acceptance semantics and therefore bumps `/v1` → `/v2`.
- Each increment's provenance is truthful on its own: there is no period in which `/v2` describes legacy behavior.

A version identifier is never reused with a different meaning (Phase 6 design §13.2).

**Schema changes (v5 → v6).**

| Change | Detail |
|---|---|
| `ai_runs.validation_version` | `TEXT NOT NULL CHECK (validation_version <> '')`. Fixed before the provider attempt and committed with the run, like the other version labels. It is covered automatically by the existing `ai_runs` append-only UPDATE/DELETE/REPLACE triggers |
| `ai_run_results_failure_codes_closed` | New `BEFORE INSERT` trigger (§5) |
| `SCHEMA_VERSION` | `6` |
| `MINIMUM_SQLITE_VERSION` | `3.37.0` → `3.38.0` (approved, conditional on 6D-B1 using SQLite JSON functions for the backstop). SQLite 3.38.0 is the project's minimum for the Phase 6D JSON-backed SQL integrity checks; it is the first release with JSON functions built in by default (§15 X1) |

**Migration.** There is no in-place migration mechanism, following the existing precedent (Phase 6 design §21 C2). `require_current_schema` refuses version 5 databases, and synthetic databases are rebuilt from the seed files. Phase 6C's schema-v5 history is preserved through Git and documentation, not through migrated live databases.

**Store and record API.**
- `RunRecord.validation_version` (AI layer), `AiRunRecord.validation_version` (data layer), the adapter mapping in `ai/composition.py`, `_RUN_COLUMNS`, and `get_run`.
- `ExtractionService` takes the constant `VALIDATION_VERSION` from `ai/validation.py`.
- The live harness `CaseAudit` and the report record it.

No change to `ai_run_outputs`, `ai_artifacts`, or `ai_artifact_excerpts`.

---

## 7. Evaluation rules

### 7.1 `CORE-TERMINAL-SUCCESS` (corpus v2 onward)

**Label: Evaluation rule.**

| Terminal status | Result | Critical class |
|---|---|---|
| `success` with the required verified artifact | pass | — |
| `refusal`, `max_tokens`, `unexpected_stop`, `parse_failure`, `semantic_validation_failure` | fail | `terminal_failure` |
| `api_error`, `transport_failure`, `model_mismatch`, `interrupted`, `incomplete`, `not_started` | fail | none (operational invalidity already governs comparison) |

A model may legitimately conclude `no_clear_baec_language` or `insufficient_context`, but it must deliver that conclusion as a valid structured artifact.

**Corpus v2:**
- Case-level `terminal_success` checks are retired, both hard and observational.
- The v2 corpus validator rejects the `terminal_success` check type.
- `clear_case_parse_failure` is retired from the v2 critical classes.
- Terminal failures are counted once.

Phase 6C (v1) totals stay historical and unchanged.

### 7.2 CORE-NUMBERS stays independent

**Label: Evaluation rule.** The existing CORE-NUMBERS check (digit runs in the two normalizations must appear in the source) is kept as it is: a separate, simpler implementation that provides defense in depth. It is not replaced by, and does not import, the production validator.

### 7.3 Observational retention

**Label: Evaluation rule.**
- **Retained** per case, in the console and in the report: `check_id`, `kind`, pass/fail, and a closed code or bounded categorical result.
- **Never influence** eligibility, hard score, artifact ranking, or the comparison outcome, unless a future precommitted evaluation version explicitly promotes the check.

---

## 8. Serialized report: `baec-live-evaluation-report/v1`

**Label: Evaluation rule.**

This is the first serialized format. It is written as canonical JSON (`baec-canonical-json/v1`), and integers replace floats wherever possible (elapsed time in milliseconds).

```
report_version          "baec-live-evaluation-report/v1"
generated_at            UTC ISO-8601
source                  { commit: 40-hex, working_tree_clean: bool }
corpus                  { version, sha256, case_count }
versions                { task, prompt, input, output_schema, request_spec, canonicalization, validation }
transport               { timeout_seconds, max_retries }
requested_model
returned_model_ids      [sanitized model IDs]
cases[]                 { case_id, ai_run_id | null, status, artifact_present,
                          failure_codes[],                    # closed vocabulary only
                          error_class | null,                 # exception class name only
                          elapsed_ms, input_tokens, output_tokens,
                          checks[]: { check_id, kind, critical_class | null, passed, code } }
audit                   { the sanitized RunAudit and per-case CaseAudit fields, plus validation_version }
authority_unchanged
aggregate               { calls_attempted, terminal_status_counts, successful_artifacts,
                          hard_passed, hard_total, critical_failures { class: [case_ids] },
                          observational_passed, observational_total,
                          operational_failures[], operationally_valid,
                          total_input_tokens, total_output_tokens, total_elapsed_ms }
```

**Never included:**
- raw model output, source interaction text, excerpts, prompt text, or the canonical artifact;
- per-run digest values;
- arbitrary exception messages;
- credentials;
- the temporary database path.

The builder uses an allowlist: every string is an identifier, version label, closed code, or sanitized model ID.

> **Phase 6D-C2 clarification (amendment; label: Evaluation rule).**
>
> The offline strict-loader implementation exposed one evidence-completeness gap in `baec-live-evaluation-report/v1`: `audit_failure` could include a disagreement between the service-returned terminal status and the persisted terminal status, but v1 serialized only the persisted status, so that part of audit validity could not be recomputed independently.
>
> Therefore `baec-live-evaluation-report/v1` is superseded before its first authorized live use, and `baec-live-evaluation-report/v2` adds, per case, the sanitized closed value `service_terminal_status` (the status the extraction service returned in memory, or `null` when it returned no terminal result) alongside `terminal_status` (read back from persisted provenance). The strict loader re-derives their agreement; a disagreement is an `audit_failure` and makes the run operationally invalid. A v1 file is refused by the loader: it is never migrated, upgraded, or compared. This is an evaluation-evidence integrity correction, not a BAEC research claim.

## 9. Report persistence and the live gate

**Label: Evaluation rule.**

- Live evaluation requires `BAEC_LIVE_REPORT_DIR`.
- **Before any provider attempt**, the gate checks:
  - the destination exists, is a directory, and is writable (a probe file is created and removed);
  - the source commit and the clean-tree flag can be determined.
- If any of these fails, the live gate closes and **zero provider attempts occur**.
- **Destination location:**
  - A directory **outside** the repository is acceptable.
  - A directory **inside** the repository must already be git-ignored before the live gate opens.
  - If an in-repository destination would create untracked runtime evidence (it is not ignored), the gate closes before any API invocation. This prevents a report from dirtying the tree or being committed by accident.
  - Implementation is in 6D-C1.
- **Atomic write:** a temporary file in the same directory, flushed and fsynced, then `os.replace`.
- **Console:** never prints the whole JSON report or an absolute local path. After a successful write it prints only the sanitized report filename and the report SHA-256.
- **Workflow:** live report → ignored working location → review → explicit promotion. Copying a reviewed sanitized report into a tracked evidence directory is a separate, explicitly approved step. Runtime reports are never committed automatically.

> **Phase 6D-C1 clarification (amendment; label: Evaluation rule).**
>
> A live evaluation requires a clean source working tree before any provider attempt. This prevents spending API calls on evidence that would already fail the Phase 6D-C2 comparability precondition. Git-ignored files do not make the tree dirty. `source.working_tree_clean` stays in the report schema, and is therefore `true` in every live report that passes the gate. This is an evaluation-integrity and cost-control safeguard, not a research claim.

## 10. Comparison from reports

**Label: Evaluation rule.**

1. **`load_report(path)`** performs strict loading:
   - report version, closed fields, and patterns;
   - **every aggregate recomputed** from `cases` and `audit`, failing closed on any disagreement;
   - the stored `operationally_valid` is cross-checked, never trusted.
2. **`compare_reports(a, b)`.**
   - **Comparability first.** Both reports must match on report version, source commit, `working_tree_clean = true`, corpus version, corpus SHA-256, all version labels, timeout, max retries, and the case set and count. The requested models must be distinct. Otherwise: `defer` / `not_comparable`.
   - **Then the Phase 6C ordering, unchanged:** operational validity → eligibility (critical failures) → hard checks passed → successful artifacts → tie.
3. **Reason codes** (machine tokens): `not_comparable`, `operationally_inconclusive`, `none_eligible`, `only_eligible_model`, `more_hard_checks_passed`, `more_successful_artifacts`, `behaviorally_tied`.
4. **Never consulted:** tokens, latency, observational results. The comparison is argument-order invariant. It informs a human decision and never sets a default or production model.

---

## 11. Corpus history and corpus v2

**Label: Evaluation rule.**

- **v1 preserved.** `tests/live/data/baec_extraction_live_v1.json` (`baec-extraction-live-corpus/v1`) stays byte-for-byte unchanged. Its SHA-256 is pinned by a test: `107fb99be29fb5e848cfae12b580b14e0290d50c07e8c0f4ad1ac92cff3e8684`. This is identical at `09dba15` and `7a3bae8`. Phase 6C remains reproducible at `09dba15`.
- **v2** (`baec-extraction-live-corpus/v2`, a new file):
  - C01–C14 semantics are copied prospectively, with the retained check IDs unchanged.
  - Case-level `terminal_success` checks are removed (§7.1).
  - C15–C17 are added.
  - The v2 validator adds one check type, `free_text_forbids_numeric_content`: a generic, parameterless check that fails if any of the four model-authored free-text fields (both normalizations, every explanation, every uncertainty) of an existing artifact contains a digit or a number-grammar word. It is a separate, simpler implementation, independent of the production tokenizer. CORE-NUMBERS stays unchanged over its existing fields.

The new cases test system rules, not any particular observed model output. All interactions are synthetic.

| | C15: unsupported numeric invention (critical) | C16: comparator mutation, LESS_THAN (critical) | C17: legitimate formatting (standard) |
|---|---|---|---|
| Synthetic interaction | Seller asks whether the buyer is reviewing other cleaning-service suppliers. Buyer: not reviewing now. Buyer: "If the renewal price goes up significantly, we would start looking at other suppliers." **No digits and no number-grammar words anywhere** | Seller asks whether the buyer is comparing logistics providers. Buyer: not comparing now. Buyer: "If on-time delivery is less than 95% for a full quarter, we would reopen the provider evaluation." | Seller asks about other maintenance providers. Buyer: not looking now. Buyer: "If the annual service fee is more than $12,500, or it rises by more than 8.5 percent, we would ask other providers for quotes." |
| Expected extraction | `possible_baec_language`; a qualitative threshold only; no numeric content in any model-authored field (RC-18: "significant" → any number never) | `possible_baec_language`; `LESS_THAN` 95 percent preserved | `possible_baec_language`; `GREATER_THAN` preserved on both magnitudes; approved formatting equivalents accepted |
| Hard checks | `excerpt_contains` "goes up significantly"; `free_text_forbids_numeric_content` | `excerpt_contains` "less than 95%"; `excerpt_fragment_requires` "95%" → "less than 95%"; `normalization_forbids` ["95% or less", "95 percent or less", "at most 95", "no more than 95", "up to 95", "≤", "<="] | `excerpt_contains` "more than $12,500"; `excerpt_contains` "more than 8.5 percent"; `normalization_forbids` ["at least", "no less than", "not less than", "or more", "or greater", "or higher", "≥", ">="] |
| Critical | `free_text_forbids_numeric_content` → `threshold_corruption`; core CORE-NUMBERS and CORE-TERMINAL-SUCCESS | `excerpt_fragment_requires` and `normalization_forbids` → `threshold_corruption` | `normalization_forbids` → `threshold_corruption`; CORE-TERMINAL-SUCCESS catches over-strict guard rejections |
| Observational | `analysis_status_in` ["possible_baec_language"] | `normalization_contains_any` ["less than 95", "fewer than 95", "below 95", "under 95", "<95", "< 95"] | `normalization_contains_any` ["12,500", "12500"]; `normalization_contains_any` ["8.5"] |
| Regression detected | A number invented from qualitative language, in any free-text field | LESS_THAN → AT_MOST, or a dropped comparator. C01 and C02 cover only the GREATER_THAN / AT_LEAST boundary | Guard false positives on comma grouping, decimal equivalence (`8.5` ≡ `8.50`), dollar form (`$12,500` ≡ `12,500 dollars`), and percent spelling (`8.5 percent` ≡ `8.5%`) |

The guard's acceptance of every approved equivalence is **proven deterministically offline** (§14), using these interactions with synthetic model outputs through a fake provider. The live case only observes whether model and guard together produce an artifact. Under §4.4, a model that rewrites `$12,500` as `USD 12,500` is correctly rejected.

> **Phase 6D-D clarification (amendment; label: Evaluation rule).** Two findings from the offline corpus-v2 implementation, before any live v2 execution:
>
> 1. **C16 normalization comparator deletion is independently hard-failing.** The table above detects a comparator dropped from an excerpt (`excerpt_fragment_requires`) and a LESS_THAN → AT_MOST rewrite (`normalization_forbids`), but a normalization that merely drops "less than" was caught only by production grounding. Corpus v2 adds the hard check `C16-H4` of the v2-only type `normalization_fragment_requires_any` (fragment `95`; allowed `less than 95`, `fewer than 95`, `below 95`, `under 95`, `<95`, `< 95`; critical `threshold_corruption`): every normalization that mentions the magnitude must carry a LESS_THAN expression. Like the other prohibitions it passes when no artifact exists. `C16-O1` stays observational.
> 2. **CORE-NUMBERS literal-digit false positives.** The legacy literal-digit CORE-NUMBERS flags approved representation-only formatting (`$12,500` → `$12500`, `8.5%` → `8.50%`) as `threshold_corruption`. Corpus v2 therefore keeps the `CORE-NUMBERS` check identity but compares simple ASCII numeric literals (comma-grouped `d{1,3}(,ddd)+` or plain, with optional decimals) by exact decimal value, so `12,500` ≡ `12500`, `8.5` ≡ `8.50`, `05` ≡ `5`. It adds nothing else: no number words, scale suffixes or words, arithmetic, unit or currency conversion, comparator inference, or compounds, and it stays independent of the production implementation. Corpus v1 keeps its historical literal-digit CORE-NUMBERS unchanged.
>
> These are evaluation-methodology corrections, not BAEC research findings. Item 2 intentionally supersedes the statements in §7.2 and above that CORE-NUMBERS stays completely unchanged, for corpus v2 only.

## 12. Known limitations

**Label: Known limitation.** Validation v2 does not claim to solve:

- **Seller/buyer numeric rebinding.** Grounding is per interaction, not per speaker.
- **Condition rebinding** when identical magnitudes appear in several contexts. A comparator is accepted if any source occurrence of the same magnitude carries it.
- **Semantic direction or polarity** beyond the closed lexical rules (for example, increase vs decrease).
- **Relative time** without a supported numeric structure (`at renewal` → `next year`).
- **Broader negation distortion.**

These remain evaluation and governance limitations. Further limits of the v2 lexicon and grammar:

- **Unresolved expressions are exact-echo only.** `past`, `beyond`, `within`, and `over` pass only when repeated identically; restating `past 4 weeks` or `over 10%` as `more than …` is rejected. This is fail-closed by design.
- **`pound(s)`** is unresolved (currency or weight), so a magnitude in pounds passes only by exact echo.
- **Fail-closed meta-numbers.** The grammar word `one` is a number, so model prose such as "one condition" fails when the source has no 1.
- **Language and notation.** English number words only; US digit grouping only; ordinal words and scale words are not parsed as numbers.
- **Expected effect on artifact rates.** Stricter validation will lower artifact rates. Prompt v1 is unchanged; Phase 6D is not a prompt-tuning exercise.

---

## 13. Build sequence

| Increment | Scope |
|---|---|
| **6D-A** | Design documentation (this document and the Phase 6 design §9 amendment) |
| **6D-B1** | Provenance foundation: schema v6, `validation_version` recording `baec-extraction-validation/v1` prospectively, closed failure-code vocabulary and cardinality enforced in all three layers, SQLite minimum 3.38.0. Acceptance rules are unchanged |
| **6D-B2** | Deterministic semantic grounding: the numeric, numeric-kind, unit, compound, and comparator guard; validation constant bumped `/v1` → `/v2`; the 18 new codes |
| **6D-C1** | Evaluation evidence: CORE-TERMINAL-SUCCESS, failure-code projection, observational retention, report-v1 builder and atomic writer, report-directory gate |
| **6D-C2** | Durable comparison: strict `load_report` and `compare_reports` |
| **6D-D** | Corpus v2: v1 preserved and pinned; C01–C14 carried forward; C15–C17; deterministic adversarial tests |
| **6D-E** | Optional, separately authorized live verification. No live-call count or model choice is approved yet |

Each increment requires the regression suite to pass, with the actual result reported, and is reviewed before the next begins.

## 14. Testing doctrine

Deterministic, offline, and part of the default suite unless marked live:

- **Numeric canonicalization:** table-driven tests covering every row of §4.3–§4.5, including number words, the full currency lexicon, Unicode digits, and no-break spaces.
- **Numeric-kind matrix:** for every ordered pair of numeric kinds, a grounded value under kind X restated as kind Y is rejected with `*_numeric_kind_changed` iff X ≠ Y, never with `*_unit_changed`. Includes `USD 10` → `$10`, `$10` → `USD 10`, and `EUR 10` → `GBP 10`.
- **`over` and postfix `+`:** `over` is unresolved in every context (exact echo passes; `over 10%` → `more than 10%` fails with `*_comparator_unresolved`); `5+` is `AT_LEAST`; `+5` stays a sign; `5+3` is compound.
- **Percent vs percentage point:** `10%` ↔ `10 percentage points` fails with `*_numeric_kind_changed`.
- **Comparator mutation matrix:** for every ordered pair of classified classes, plus `NONE`, a source in class X and model text in class Y is rejected iff X ≠ Y. Unresolved expressions pass only on exact echo; conflicting markers are rejected.
- **Compound fail-closed tests:** permutations (`3/15` → `15/3`), reordered ranges, scale suffixes, unknown digit structures.
- **Field independence:** each guarded field is tested on its own, and numbers in identifiers and refs never trigger.
- **Production service test:** a synthetic number-invention fixture gives `semantic_validation_failure`, no artifact, the code persisted, and the raw output retained per the existing rules.
- **Failure codes:** closed-vocabulary and cardinality tests on write, on a tampered readback, and through raw-SQL inserts against the trigger: unknown code, wrong status, unsorted, duplicate, suffixed, non-array, `parse_failure` with zero or two codes, `semantic_validation_failure` with zero codes, any other status with a code.
- **`validation_version` provenance:** recorded before the attempt, `NOT NULL`, append-only, pinned constant, schema-version assertions.
- **CORE-TERMINAL-SUCCESS:** every status row of §7.1; the v2 validator rejects case-level `terminal_success`.
- **v1 digest pin:** the SHA-256 recorded in §11.
- **Observational non-ranking:** flipping every observational outcome never changes `compare_reports`.
- **Report sanitization canaries:** fake-provider outputs carry canary strings in every model field; the canaries and all interaction text are absent from the report and the console.
- **Report round-trip and aggregate recomputation:** each tampered aggregate, unknown field, or bad pattern fails closed.
- **Report directory:** a missing or unwritable directory, or an un-ignored in-tree destination, gives zero provider attempts.
- **`compare_reports`:** every reason code reachable; argument-order invariance; tokens, latency, and observations are inert.
- **Phase 6C branch reproduction:** the documented Phase 6C aggregates, encoded as a **synthetic fixture only** (not a rerun and not retained live data), reproduce the `only_eligible_model` branch.
- **Mutation checks:** disabling each guard sub-rule makes a named test fail.

## 15. Conflicts with the current architecture

| # | Conflict | Resolution |
|---|---|---|
| X1 | The SQL backstop needs JSON functions (`json_valid`, `json_each`). `MINIMUM_SQLITE_VERSION` is 3.37.0, where they are optional; they are built in by default from 3.38.0 | Approved: raise the documented and tested minimum to 3.38.0 in 6D-B1, if the backstop uses JSON functions. SQLite 3.38.0 is the project's minimum for the Phase 6D JSON-backed SQL integrity checks (the local SQLite is 3.50.4) |
| X2 | The Phase 6 design §8 tables specify suffixed codes (`:<id>`, `:<field>`, `:<criterion>`). The implementation uses unsuffixed codes, and the data layer's `_CODE` pattern still permits a suffix | The implemented unsuffixed codes are authoritative; suffixes are prohibited (§5); the data-layer pattern is replaced by the closed vocabulary in 6D-B1 |
| X3 | No `.gitignore` entry ignores a report location. Only `*.sqlite3` files under `var/` are ignored, so a JSON report there would be untracked and would dirty the tree | 6D-C1 adds an explicit ignored report location (a `.gitignore` change, part of that increment's review), or the destination is outside the working tree; the gate enforces §9 |
| X4 | `live_gate` is a pure environment check today. The new gate needs filesystem and `git` checks | The checks stay in `tests/live/` only; production gains no `git` or filesystem dependency. If `git` is unavailable, the gate closes |
| X5 | The validator has never had a version label | Phase 6C used the legacy, pre-versioned semantic validator, with no identifier assigned retroactively. `baec-extraction-validation/v1` is recorded prospectively from 6D-B1 and bumped to `/v2` in 6D-B2 (§6) |
| X6 | Phase 6C comparison reasons were free-text strings | v1 report comparisons use machine reason codes (§10). The Phase 6C record keeps its original wording |
| X7 | Some bound threshold forms were in neither approved comparator list | **RESOLVED IN 6D-A:** `in excess of` → `GREATER_THAN`; `or above`, `or longer`, `and up`, postfix `+` → `AT_LEAST`; `or below`, `or shorter` → `AT_MOST` (§4.6) |
| X8 | `parse_failure` permitted zero `failure_codes` in schema v5 | **RESOLVED IN 6D-A:** exactly one code from schema v6 (§5.1) |
| X9 | The validator label in schema-v6 runs between 6D-B1 and 6D-B2 | **RESOLVED IN 6D-A:** `/v1` from 6D-B1, `/v2` from 6D-B2 (§6) |
| X10 | What counts as explicitly qualified USD, and the currency word forms | **RESOLVED IN 6D-A:** the closed currency lexicon in §4.4 |

## 16. Rules settled in the 6D-A review

Rules derived during design and then decided explicitly in the 6D-A review. The approved draft is superseded where noted.

| # | Rule | Outcome |
|---|---|---|
| R1 | Comparator vocabulary | The classified lexicon in §4.6 is approved, including `above`, `exceeds`, `exceeding`, `not less than`, `minimum of`, `below`, `under`, `not more than`, `maximum of`, `around`, `~`, and the postfix `or more` / `or greater` / `or higher` / `or less` / `or fewer` / `or lower`. `past`, `beyond`, `within`, and `over` stay unclassified. Supersedes the draft that made most synonyms exact-echo only |
| R2 | Currency | `€` → EUR and `£` → GBP as deterministic lexical mappings; `$` and unqualified `dollars` stay unspecified-dollar; `pounds` is unresolved; no conversion (§4.4). Supersedes the draft symbol-only kinds |
| R3 | Units | A grounded unit is part of the numeric meaning. Changing, substituting, dropping, or introducing a unit is rejected; only singular/plural folding is allowed (§4.7). Supersedes the draft that allowed `4 weeks` → `4` |
| R4 | Leading zeros | Standalone magnitudes canonicalize (`05` ≡ `5`, `05.0` ≡ `5`). Leading zeros stay structural inside compounds; component-digit matching is forbidden (§4.3, §4.5). Supersedes the draft exact-echo rule for standalone leading zeros |
| R5 | C16 and C17 wording | Use classified comparator expressions (`less than`, `more than`). Approved |
| R6 | Generic free-text numeric check | `free_text_forbids_numeric_content` over all four model-authored free-text scopes, for C15 and any later case (§11). CORE-NUMBERS stays unchanged. Approved |
| R7 | The number word `one` | Part of the closed grammar, with fail-closed consequences in meta text. Approved |
| R8 | Report destination | External directories are acceptable; in-repository destinations must already be git-ignored; otherwise the gate closes before any API invocation (§9). Approved |
| R9 | Numeric kind | A grounded value with a changed numeric kind fails with `*_numeric_kind_changed`, the sixth grounding ending; 18 grounding codes; 38 semantic, 43 total (§4.7, §5.1). Supersedes the draft 15-code vocabulary |
| R10 | Final comparator additions | `in excess of`; postfix `or above`, `or longer`, `and up`, `+` (`AT_LEAST`); `or below`, `or shorter` (`AT_MOST`); `over` unresolved in all contexts (§4.6). Supersedes the draft that left those forms unresolved, and the draft context-sensitive `over` rule with its temporal-frame lexicon |
| R11 | Failure-code cardinality | `parse_failure` exactly one; `semantic_validation_failure` one or more; all other statuses zero (§5.1) |
| R12 | Validator versioning | Legacy, pre-versioned for Phase 6C; `/v1` prospective from 6D-B1; `/v2` from 6D-B2 (§6). Supersedes the draft that made `/v2` the first persisted identifier |
| R13 | Currency lexicon | The closed lexicon in §4.4. Supersedes R2's narrower list |

## 17. Unchanged invariants

Phase 6D does not weaken any of the following:
- the synthetic-only public prototype;
- the human authorization boundary;
- immutable original buyer evidence;
- amendment rather than overwrite;
- no automatic opportunity activation;
- no automatic buyer contact;
- no AI authority to redefine the BAEC;
- no broadening of monitoring scope beyond the buyer-defined condition;
- no secret persistence;
- no default model introduced because of Phase 6C;
- the read-only MCP Core.

AI remains unexposed through MCP. The prompt, input, output schema, request specification, timeout, and retry policy are unchanged.

> **Phase 6D-E4 clarification (amendment; label: Implementation safeguard).**
>
> - **E2 (one authorized live verification of `claude-sonnet-5-5` on `baec-extraction-live-corpus/v2` at `277ab0f`, 17 calls):** operational verification PASS; behavioral eligibility FAIL (8 `terminal_failure` cases). Sanitized report SHA-256 `166d0703b52c80571c16092d53ac45ccd84f1204f787cb5ddc730bebfbc14057`; the runtime report itself is not committed.
> - **E3 (offline diagnosis):** no deterministic validator false positive was found under the locked rules. 10 of the 11 grounding codes arose in explanation and uncertainty text, which `baec-extraction-prompt/v1` did not ground: v1 required exact threshold fidelity only for the two normalizations.
> - **Decision:** validation v2 is preserved unchanged, and `baec-extraction-prompt/v2` states the grounding rules for every model-authored free-text field (both normalizations, every explanation, every uncertainty): no unsupported number, number word, percentage, currency amount, unit-bearing quantity, or count; magnitude, numeric kind, unit, and comparator preserved when a supported value is mentioned; `past`, `beyond`, `within`, and `over` repeated exactly; and unnecessary numeric restatement avoided. The task, input, output schema, request specification, canonicalization, timeout, and retry policy are unchanged; the prompt digest and the request digest change. Prompt v1 stays frozen as the historical identity of the Phase 6C and E2 runs.
>
> Prompt v2 is an alignment correction discovered through live verification, not a BAEC research finding. This supersedes, for prompt identity only, the statements in §12 and above that the prompt is unchanged.

---

## Phase 6D closeout

**Label: Status note.** Phase 6D implementation and live verification are complete; Phase 6 is closed. Final results, evidence hashes, and the conclusion are in `docs/PHASE6_FINAL_VERIFICATION.md`: operational verification PASS; behavioral eligibility FAIL for `claude-sonnet-5-5` on corpus v2 (C05 and C07, `explanation_number_unsupported`); no model is designated as the product default.
