# BAEC Engine 2 Stage B: Synthetic Signal and Correspondence Corpus

**Project:** BAEC Trigger Intelligence
**Engine:** BAEC Engine 2: Monitoring + Correspondence
**Stage:** B (synthetic corpus design only)
**Status:** Stage B LOCKED (October 8, 2026).
**Machine-readable corpus:** `docs/engine2/corpus/HARBOR_CORRESPONDENCE_CORPUS.json`
**Governing contract:** `docs/engine2/ENGINE2_MONITORING_CORRESPONDENCE_DOMAIN_CONTRACT.md` (Stage A LOCKED, commit `e33306184237bdbdc253e9325d33713aef6b5ccb`)

> **Synthetic test oracles.** The expected findings and outcomes in this corpus are synthetic test oracles. They are not empirical statements about real buyers and they are not validated psychological labels. They exist so that future software behavior can be tested deterministically against deliberately constructed examples.

Correspondence narrows attention. It does not establish buyer evaluation, purchase intent, or an opportunity.

---

## 1. Purpose

This corpus gives future Engine 2 software a fixed, adversarial set of synthetic cases against which its behavior can be tested deterministically. Each case states what evidence arrives, what may become an Observation, what a human reviewer's findings would be under the locked Stage A rules, and what correspondence outcome the Stage A rule then produces.

The corpus is built to make easy answers fail. It tests whether software can:

- tell relevant evidence from irrelevant evidence, and applicability from generic similarity;
- tell supported from contradicted dimensions, and exact threshold satisfaction from near misses;
- tell the right entity, account, agreement, and timing from wrong ones;
- tell authorized evidence from unusable evidence, and raw Observations from Derived Measurements;
- tell current evidence from stale, corrected, or retracted evidence;
- keep correspondence separate from purchase intent, from action authorization, and from BAEC revalidation.

Only 9 of 64 cases reach `HUMAN_VERIFIED_CORRESPONDENCE`. Most of the corpus consists of near misses, partial matches, and evidence that must be refused.

## 2. Authority and Scope

Authority for this stage, in order: the locked Stage A contract; `docs/RESEARCH_CONTRACT.md`; locked Engine 1 semantics where a cross-engine reference is needed. The manuscript is not reinterpreted here; Stage A records its managerial basis.

Every concept in this corpus is IMPLEMENTATION. The corpus uses the Stage A dimension names, finding vocabulary, outcome names, and workflow condition exactly. It adds no domain state. Two corpus-level processing dispositions ("reject before Observation creation" and "Monitoring Plan not activated; processing stops before Observation creation") explain why a case never reaches classification; they are not Engine 2 states.

The JSON file is a Stage B corpus representation. It is not the Engine 2 persistence schema, and its field names are not production names. Stage B creates no code, schema, validator, prompt, connector, or interface.

Where Stage A leaves room for interpretation, the corpus follows the decided interpretations recorded in Section 4. Each is a corpus rule, recorded in the Monitoring Plan where it concerns the plan, and none is presented as a Stage A rule.

## 3. Canonical Harbor BAEC

### 3.1 Engine 1 fixture (`FIXTURE-HBR-BAEC-001`)

A synthetic representation of a confirmed, current Engine 1 BAEC as Engine 2 would receive it under Stage A Section 4.2. The Engine 1 v1.0 public demo stops at authorization and does not create this record; the fixture stands in for a BAEC confirmed through the separate executor.

| Element | Value |
|---|---|
| Buyer statement (BUYER_FACT, verbatim) | "If our supplier raises pricing by more than 10% when our agreement renews, we'd evaluate other options." |
| Classification and currentness | `CONFIRMED_BAEC`, staleness `CURRENT` (confirmation `CONF-HBR-001`) |
| Account | Harbor Surgical Center (`ACC-HARBOR`), synthetic |
| Buyer role | Materials Manager |
| Comparator | more than (strict; stored as `GREATER_THAN`) |
| Threshold | 10 |
| Unit | % |
| Timing context | agreement renewal ("when our agreement renews") |
| Evaluation consequence | "we'd evaluate other options" (the buyer's statement; Engine 2 neither predicts nor confirms it) |
| Human-reviewed normalization | "The current supplier raises pricing by more than 10% when the agreement renews." (derived, human-reviewed, never evidence) |
| "our supplier" | Resolved by a human reviewer to NorthStar (contracting entity NorthStar Surgical Supply Co., synthetic), on the basis of the buyer's words "NorthStar has been fine for us." Not resolved by AI. |

**Boundary rule.** 10% does NOT satisfy "more than 10%". The comparator is strict and is never applied as "at least".

The fixture contains no AI draft as authority. AI-derived normalization, AI_DRAFT proposals, unconfirmed review revisions, and unconsumed authorizations are listed as excluded.

### 3.2 Monitoring Plan `MP-HBR-001` version 1

The plan is human-authorized and active from 2026-03-16. It records:

- **Required dimensions:** all eleven primary Stage A dimensions. Every one bears on the buyer's condition, so none is marked `NOT_APPLICABLE`.
- **Cross-cutting checks:** Freshness, Contradictory evidence, Missing evidence, and Provenance quality. Stage A defines 11 correspondence dimensions plus 4 separate cross-cutting checks: the checks "do not stand in for a required dimension; they limit what may be concluded from it" (Stage A Section 8.2). Section 4 describes how the corpus records them.
- **Plan facts:**
  - the renewal date 2027-04-01, from an authorized CRM contract summary;
  - the product scope, procedure packs under agreement HNS-2025-01.
- **Plan rules:**
  - Harbor-specific contract notices govern Harbor's contracted pricing, and general announcements do not.
  - Duplicate copies of one notice are one evidence event.
  - No interpretation of approximate, bounded, ranged, or qualitative figures is authorized.
  - Supersession needs an authority basis; recency alone is not one.
  - The resolved supplier is the contracting legal entity, and its continuity needs authoritative evidence.
  - The plan does not define which price measure "pricing" means.
- **Authorized sources** (all synthetic):
  - `AUTH-SRC-01`: buyer-provided documents.
  - `AUTH-SRC-02`: NorthStar public customer notices.
  - `AUTH-SRC-03`: NorthStar Holdings public announcements.
  - `AUTH-SRC-04`: the seller's CRM record for Harbor.
  - `AUTH-SRC-05`: a licensed pricing bulletin.

Some cases vary a plan fact, for example by having no recorded renewal date. Each such variation is stated in the case's `monitoring_plan_context`.

### 3.3 The eight Stage A reference cases

| Case | Title | Findings (non-supported) | Oracle |
|---|---|---|---|
| 001 | Renewal increase of 4% | C: Threshold | `NO_CORRESPONDENCE` (sufficiency NO) |
| 002 | General 12% increase with no evidence it applies to Harbor | U: Relationship, Source relevance, Timing, Effective date | `POSSIBLE_CORRESPONDENCE` (sufficiency NO) |
| 003 | Harbor renewal notice: 12% effective at renewal | All S | `HUMAN_VERIFIED_CORRESPONDENCE` (sufficiency YES) |
| 004 | Competitor raises pricing by 15% | C: Entity, Relationship; U: Source relevance, Timing, Effective date, Context | `NO_CORRESPONDENCE` (sufficiency NO) |
| 005 | Renewal increase of exactly 10% | C: Threshold | `NO_CORRESPONDENCE` (sufficiency NO) |
| 006 | 12% increase effective six months after renewal | U: Timing, Effective date | `POSSIBLE_CORRESPONDENCE` (sufficiency NO) |
| 007 | Unsourced seller note: 'probably go up around 12%' | U: Entity, Relationship, Source relevance, Event type, Threshold, Comparator, Magnitude, Timing, Effective date, Context | `POSSIBLE_CORRESPONDENCE` (sufficiency NO) |
| 008 | 12% renewal notice corrected to 7% | C: Threshold | `NO_CORRESPONDENCE` (sufficiency NO) |

**Case 006, alternate reading.** The machine oracle records timing `UNRESOLVED` because the evidence does not show whether Harbor's renewed terms include the October increase. If a reviewer instead has evidence that renewed pricing is fixed and records timing `CONTRADICTED`, the rule yields `NO_CORRESPONDENCE`; HBR-CORR-033 is that version. In neither reading can the case reach `HUMAN_VERIFIED_CORRESPONDENCE`.

## 4. Corpus Oracle Rules

After authoritative human findings, per Stage A Section 11.2:

1. Any required dimension `CONTRADICTED` → `NO_CORRESPONDENCE`.
2. Otherwise, every required dimension `SUPPORTED` by accepted, sourced, current evidence, plus an explicit human sufficiency finding of `YES`, and no active `BAEC_REVALIDATION_REQUIRED` → `HUMAN_VERIFIED_CORRESPONDENCE`.
3. Otherwise → `POSSIBLE_CORRESPONDENCE`.

Further corpus rules, each traced to Stage A:

- A finding of `SUPPORTED` or `CONTRADICTED` cites at least one usable Observation, accepted Derived Measurement, or plan fact. Superseded or retracted Observations and invalidated Derived Measurements support nothing (Sections 7.1, 7.3, 14).
- `NOT_APPLICABLE` appears only where the plan establishes that a dimension is not required. In this corpus the plan marks none, so it never appears as an expected finding (Section 5, rule 6).
- `BAEC_REVALIDATION_REQUIRED` blocks only `HUMAN_VERIFIED_CORRESPONDENCE`. `NO_CORRESPONDENCE` and `POSSIBLE_CORRESPONDENCE` may still be recorded (Section 4.3).
- Packets from unauthorized sources never become Observations, and a case whose only evidence is unauthorized receives no outcome (Sections 6, 7.1).
- There is no `LIKELY_CORRESPONDENCE`, `STRONG_CORRESPONDENCE`, score, probability, confidence value, match score, or evidence count anywhere in the corpus.

### 4.1 Eleven dimensions and four cross-cutting checks

Stage A defines 11 correspondence dimensions plus 4 separate cross-cutting checks.

- **The 11 dimensions** are the only findings. Each takes `SUPPORTED`, `CONTRADICTED`, `UNRESOLVED`, or `NOT_APPLICABLE`, and only these enter the outcome rule.
- **The 4 checks** (Freshness, Contradictory evidence, Missing evidence, Provenance quality) are recorded per classified case as structured entries. Each entry has a note, the evidence it cites, and the dimensions it limits.
  - A check never takes the four-value vocabulary and never triggers an outcome on its own.
  - Its effect appears only through the dimension findings it explains. For example, inadequate provenance is recorded under Provenance quality, and the dimensions it affects are `UNRESOLVED`.
  - Missing evidence always lists exactly the dimensions that are `UNRESOLVED`.

### 4.2 Decided interpretations

These were reviewed and decided for this corpus. They are corpus rules, not amendments to Stage A.

- **Threshold and comparator.** The two findings answer different questions:
  - *Comparator match:* can the authoritative buyer-defined comparator be applied faithfully to the evidence value?
  - *Threshold match:* does the resulting evidence value satisfy the buyer-defined threshold under that comparator?

  Against "more than 10%", exactly 10% is Comparator `SUPPORTED`, Threshold `CONTRADICTED`; 10.01% is both `SUPPORTED`. Threshold can be `SUPPORTED` or `CONTRADICTED` only when Comparator, Numeric magnitude, and Unit match are all `SUPPORTED`. Linguistic similarity is never satisfaction.
- **Approximate figures.** "About", "roughly", "around", "up to", "double-digit", estimates, ranges, and qualitative terms do not yield a precise threshold result. The default is `UNRESOLVED` for Numeric magnitude, Comparator match, and Threshold match, unless the Monitoring Plan authorizes an interpretation; `MP-HBR-001` authorizes none. There are no tolerance bands, rounding assumptions, confidence values, fuzzy matches, or probabilistic threshold satisfaction.
- **Supersession.** A later item does not supersede an earlier one merely because it is newer. Supersession needs an authority basis:
  - explicit correction or retraction language;
  - version or revision metadata;
  - contractual amendment hierarchy;
  - effective-date logic;
  - a source rule already in the Monitoring Plan.

  Every superseded Observation records its basis and the superseding evidence. Without a basis, the affected dimensions stay `UNRESOLVED` (Case 049).
- **Rename and entity continuity.** A legal or brand name change preserves the entity only where authoritative evidence establishes that the contracting supplier is the same entity. Then Entity match may be `SUPPORTED`, and no revalidation is raised for a changed display name (Case 028). `BAEC_REVALIDATION_REQUIRED` is raised, and Entity match stays `UNRESOLVED`, when any of the following holds (Case 053):
  - the contracting legal entity changed;
  - the buyer changed supplier;
  - an acquisition assigned or transferred the agreement;
  - continuity cannot be established.

  Name similarity never resolves entity or currentness.
- **Human sufficiency.** Stage A Section 10 defines the sufficiency judgment as `YES`, `NO`, or `UNKNOWN`, and the corpus uses that exact vocabulary. Only `YES`, together with all required dimensions `SUPPORTED` and no active revalidation condition, can yield `HUMAN_VERIFIED_CORRESPONDENCE`. `NO` and `UNKNOWN` cannot.

**Oracle integrity cases.**

| Case | Title | Findings (non-supported) | Oracle |
|---|---|---|---|
| 063 | Reviewer attempts NOT_APPLICABLE on a required timing dimension | U: Timing, Effective date | `POSSIBLE_CORRESPONDENCE` (sufficiency NO) |
| 064 | All dimensions supported, but the reviewer's sufficiency finding is UNKNOWN (adversity context) | All S | `POSSIBLE_CORRESPONDENCE` (sufficiency UNKNOWN) |

## 5. Case Format

Each JSON case contains:

- **Identity:** `case_id`, `title`, `category`, `stage_a_reference_case`, `purpose`, `synthetic_only` (always true), and `coverage_tags`.
- **Context:** `authoritative_baec_reference` (always `FIXTURE-HBR-BAEC-001`), `monitoring_plan_context`, and `source_authorization_context` (one entry per packet).
- **Evidence:**
  - `source_packets` (full synthetic text and explicit ISO 8601 times) and `packets_not_admitted`;
  - `raw_observations_expected`, each with provenance category, content SHA-256, evidence event, supersession and its authority basis, and usability;
  - `derived_measurements_expected`, each with source Observation IDs, exact inputs and units, formula, rounding rule, output, calculation time, and transformation identifier and version.
- **Findings:**
  - `expected_dimension_findings`: all eleven dimensions, each with a finding, cited evidence, and a reason;
  - `cross_cutting_checks`: all four checks, each with a note, cited evidence, and the dimensions it limits, but no finding value;
  - `refused_review_entries` and `prior_review_history`.
- **Oracle:** `expected_baec_revalidation_required` with `revalidation_triggers`, `expected_adversity_context_flag`, `expected_correspondence_outcome` (null when the case stops before classification) with `expected_processing_disposition`, `expected_human_sufficiency_finding`, `alternate_human_interpretation`, and `expected_action_boundary`.
- **Explanation:** `rationale`, `prohibited_inferences`, and `authority_notes`.

The tables in this document show only dimensions that are not `SUPPORTED`. "C" lists contradicted dimensions and "U" lists unresolved ones.

## 6. Coverage Matrix

Primary categories:

| Primary category | Cases | IDs |
|---|---|---|
| Stage A Harbor reference | 8 | 001 to 008 |
| Threshold and comparator | 11 | 009 to 019 |
| Derived Measurement | 5 | 020 to 024 |
| Entity and applicability | 7 | 025 to 031 |
| Timing | 7 | 032 to 038 |
| Source authorization and provenance | 5 | 039 to 043 |
| Correction, retraction, and conflict | 7 | 044 to 050 |
| Multi-source composition | 2 | 051 to 052 |
| BAEC revalidation | 5 | 053 to 057 |
| Semantic ambiguity | 5 | 058 to 062 |
| Oracle integrity | 2 | 063 to 064 |

Coverage by tested boundary (a case can test several):

| Category | Count | Cases |
|---|---|---|
| Threshold | 31 | 001, 003, 005, 008, 009, 010, 011, 012, 013, 014, 015, 016, 018, 019, 020, 021, 022, 023, 024, 030, 033, 042, 044, 048, 051, 054, 058, 059, 060, 062, 064 |
| Comparator | 14 | 001, 003, 005, 009, 010, 011, 012, 013, 014, 015, 017, 020, 021, 022 |
| Unit | 8 | 003, 014, 015, 016, 017, 023, 042, 060 |
| Entity | 9 | 003, 004, 025, 026, 027, 028, 031, 051, 053 |
| Account applicability | 8 | 002, 025, 026, 027, 029, 030, 045, 052 |
| Source authorization | 11 | 002, 003, 007, 027, 039, 040, 041, 042, 043, 052, 057 |
| Provenance | 8 | 007, 031, 039, 040, 041, 042, 043, 052 |
| Timing | 16 | 002, 003, 006, 019, 032, 033, 034, 035, 036, 037, 038, 050, 051, 054, 063, 064 |
| Freshness | 10 | 008, 024, 035, 036, 037, 044, 046, 047, 049, 050 |
| Correction | 8 | 008, 024, 031, 038, 044, 046, 049, 050 |
| Retraction | 2 | 038, 047 |
| Conflicting evidence | 4 | 045, 046, 048, 049 |
| Derived Measurement | 10 | 014, 015, 020, 021, 022, 023, 024, 042, 060, 061 |
| Multi-source composition | 15 | 020, 021, 022, 024, 025, 026, 028, 029, 033, 035, 037, 045, 051, 052, 061 |
| BAEC revalidation | 6 | 036, 053, 054, 055, 056, 057 |
| Semantic ambiguity | 12 | 007, 011, 012, 017, 018, 019, 030, 058, 059, 060, 061, 062 |
| Approximate, bounded, ranged, or qualitative value | 8 | 007, 011, 012, 013, 017, 018, 058, 059 |
| Supplier entity continuity | 2 | 028, 053 |
| Action boundary | 61 | 001, 002, 003, 004, 005, 006, 007, 008, 009, 010, 011, 012, 013, 014, 015, 016, 017, 018, 019, 020, 021, 022, 023, 024, 025, 026, 027, 028, 029, 030, 031, 032, 033, 034, 035, 036, 037, 038, 039, 042, 043, 044, 045, 046, 047, 048, 049, 050, 051, 052, 053, 054, 055, 056, 058, 059, 060, 061, 062, 063, 064 |

## 7. Threshold and Comparator Cases

| Case | Title | Findings (non-supported) | Oracle |
|---|---|---|---|
| 009 | Renewal increase of 9.99% | C: Threshold | `NO_CORRESPONDENCE` (sufficiency NO) |
| 010 | Renewal increase of 10.01% | All S | `HUMAN_VERIFIED_CORRESPONDENCE` (sufficiency YES) |
| 011 | Approximate figures: 'about 12%' and 'roughly 12%' | U: Threshold, Comparator, Magnitude | `POSSIBLE_CORRESPONDENCE` (sufficiency NO) |
| 012 | Ceiling only: 'up to 12%' | U: Threshold, Comparator, Magnitude | `POSSIBLE_CORRESPONDENCE` (sufficiency NO) |
| 013 | Range: between 8% and 12% depending on pack | U: Threshold, Comparator, Magnitude | `POSSIBLE_CORRESPONDENCE` (sufficiency NO) |
| 014 | Renewal increase of 1,000 basis points | C: Threshold | `NO_CORRESPONDENCE` (sufficiency NO) |
| 015 | Renewal increase of 1,001 basis points | All S | `HUMAN_VERIFIED_CORRESPONDENCE` (sufficiency YES) |
| 016 | Missing unit: 'increase by 12' | U: Threshold, Comparator, Magnitude, Unit | `POSSIBLE_CORRESPONDENCE` (sufficiency NO) |
| 017 | Ambiguous unit: 'more than ten points' | U: Threshold, Comparator, Magnitude, Unit | `POSSIBLE_CORRESPONDENCE` (sufficiency NO) |
| 018 | Components: base price unchanged, fees +15%, landed cost incl. freight +12% | U: Event type, Threshold, Comparator, Magnitude, Context | `POSSIBLE_CORRESPONDENCE` (sufficiency NO) |
| 019 | Temporary 14% surcharge; base prices unchanged | U: Event type, Threshold, Context | `POSSIBLE_CORRESPONDENCE` (sufficiency NO) |

Notes:

- **Edge figures.** 9.99%, exactly 10% (Case 005), 10.01%, 1,000 basis points, and 1,001 basis points are evaluated without rounding. Basis points become percent only through a recorded Derived Measurement. 11% appears in Case 051, and 1,100 basis points in Case 042.
- **Approximate and partial figures.** "About 12%", "up to 12%", and a range across the threshold are not exact magnitudes, so they stay `UNRESOLVED` even though 12 is above 10.
- **Missing or different units.** A missing unit or "points" is never read as percent.
- **Mixed price changes.** Components moving differently (Case 018) and temporary surcharges (Case 019) leave the buyer's "pricing" basis to the human.

Also tested in other sections: threshold in 001, 003, 005, 008, 020, 021, 022, 023, 024, 030, 033, 042, 044, 048, 051, 054, 058, 059, 060, 062, 064.

## 8. Entity and Applicability Cases

| Case | Title | Findings (non-supported) | Oracle |
|---|---|---|---|
| 025 | Same supplier, different customer with a similar name | C: Relationship; U: Source relevance, Timing, Context | `NO_CORRESPONDENCE` (sufficiency NO) |
| 026 | Same customer, different supplier and agreement | C: Entity, Relationship, Context | `NO_CORRESPONDENCE` (sufficiency NO) |
| 027 | Parent company announcement; contracting subsidiary unclear | U: Entity, Relationship, Source relevance, Timing, Effective date | `POSSIBLE_CORRESPONDENCE` (sufficiency NO) |
| 028 | Supplier renamed after acquisition; legal entity and agreement unchanged | All S | `HUMAN_VERIFIED_CORRESPONDENCE` (sufficiency YES) |
| 029 | Different Harbor agreement and unrelated product line | C: Relationship, Context; U: Timing | `NO_CORRESPONDENCE` (sufficiency NO) |
| 030 | One covered SKU rises 14%; other packs unchanged | U: Threshold, Context | `POSSIBLE_CORRESPONDENCE` (sufficiency NO) |
| 031 | Ambiguous name shared by two organizations, then identity corrected | C: Entity; U: Relationship, Source relevance, Event type, Threshold, Comparator, Magnitude, Unit, Timing, Effective date, Context | `NO_CORRESPONDENCE` (sufficiency NO) |

Notes:

- **Wrong supplier and other customers.** The wrong supplier is Case 004. A different customer with a similar name, and the same customer with a different supplier, are both invalid compositions joined to Harbor's plan.
- **Parent companies and renames.** A parent company's statement does not bind the contracting subsidiary (Case 027; Entity match `UNRESOLVED`, because the evidence's entity is uncertain, not the BAEC's supplier). A share acquisition followed by a name change, with authoritative evidence that the legal entity and agreement are unchanged, preserves continuity (Case 028).
- **Other agreements and SKUs.** A different Harbor agreement or product line contradicts the relationship. A single-SKU increase leaves the "pricing" basis unresolved.
- **Ambiguous names.** An ambiguous name stays `UNRESOLVED` until a correction identifies the organization (Case 031).
- **Supplier change.** Harbor changing supplier is a continuity failure and a revalidation case (Case 053).

Also tested in other sections: entity in 003, 004, 051, 053; account applicability in 002, 045, 052.

## 9. Timing Cases

| Case | Title | Findings (non-supported) | Oracle |
|---|---|---|---|
| 032 | Increase effective before renewal (mid-term) | C: Timing, Effective date | `NO_CORRESPONDENCE` (sufficiency NO) |
| 033 | Renewed terms fix pricing; a later 12% increase applies only to later renewals | C: Threshold, Timing | `NO_CORRESPONDENCE` (sufficiency NO) |
| 034 | Renewal date unknown | U: Timing, Effective date | `POSSIBLE_CORRESPONDENCE` (sufficiency NO) |
| 035 | Renewal date moved by extension (auto-renewal postponed) | U: Timing, Effective date | `POSSIBLE_CORRESPONDENCE` (sufficiency NO) |
| 036 | Notice arrives after renewal; renewal occurred without evaluation | All S | `POSSIBLE_CORRESPONDENCE` (revalidation flag, sufficiency YES) |
| 037 | Stale historical renewal notice joined with the current renewal | C: Timing, Effective date | `NO_CORRESPONDENCE` (sufficiency NO) |
| 038 | Future increase announced, then cancelled | C: Event type, Threshold | `NO_CORRESPONDENCE` (sufficiency NO) |

Notes:

- **Effective at renewal.** Case 003 covers an increase announced four months before renewal and effective at renewal; timing is judged on the effective date.
- **Before and after renewal.** A mid-term increase is not "when our agreement renews"; Engine 2 does not broaden the condition (RC-26). An increase after renewal is `UNRESOLVED` in Case 006 and `CONTRADICTED` in Case 033, where evidence fixes renewed pricing.
- **Unknown or moved renewal dates.** These leave timing `UNRESOLVED`. They cover renewal date unknown, renewal date changed, contract extended, and auto-renewal postponed.
- **Notices that arrive late or describe the past.** A notice that arrives after the renewal has passed raises `BAEC_REVALIDATION_REQUIRED` (Case 036). A notice about a past renewal cannot satisfy a prospective condition (Case 037).
- **Cancelled announcements.** A cancellation replaces the earlier announcement (Case 038).

Also tested in other sections: timing in 002, 003, 006, 019, 050, 051, 054, 063, 064.

## 10. Source Authorization and Provenance Cases

| Case | Title | Findings (non-supported) | Oracle |
|---|---|---|---|
| 039 | Seller recollection of a supplier rep's remark, no source | U: Entity, Relationship, Source relevance, Event type, Threshold, Comparator, Magnitude, Timing, Effective date, Context | `POSSIBLE_CORRESPONDENCE` (sufficiency NO) |
| 040 | Unauthorized private portal using shared credentials | None (not classified) | Blocked: reject before Observation creation |
| 041 | Leaked private email | None (not classified) | Blocked: reject before Observation creation |
| 042 | Duplicate copies of one notice (1,100 basis points) | All S | `HUMAN_VERIFIED_CORRESPONDENCE` (sufficiency YES) |
| 043 | Document whose authenticity cannot be established | U: Entity, Relationship, Source relevance, Event type, Threshold, Comparator, Magnitude, Unit, Timing, Effective date, Context | `POSSIBLE_CORRESPONDENCE` (sufficiency NO) |

Notes:

- **Authorized sources** are exercised throughout the corpus:
  - buyer-provided renewal notices (Case 003 and most others);
  - a supplier announcement (Case 002);
  - a public corporate pricing notice (Case 027);
  - CRM contract documents (Cases 020 to 024 and 051).
- **Unauthorized material** stops before Observation creation and receives no outcome, however relevant its content: the private portal reached with credentials the seller may not use (Case 040), the leaked email (Case 041), and the copied screenshot with no provenance (Case 052).
- **Unsourced notes.** An unsourced seller note (Case 007) and a seller's recollection (Case 039) become SELLER_OBSERVATION Observations from an authorized CRM, but they cannot support external-evidence dimensions.
- **Duplicates.** Four copies of one notice (Case 042) are one evidence event.
- **Doubtful authenticity.** A document whose authenticity cannot be established (Case 043) is captured but not accepted.

Also tested in other sections: source authorization in 002, 003, 007, 027, 052, 057; provenance in 007, 031, 052.

## 11. Derived Measurement Cases

| Case | Title | Findings (non-supported) | Oracle |
|---|---|---|---|
| 020 | Derived Measurement A: USD 100.00 to USD 112.00 | All S | `HUMAN_VERIFIED_CORRESPONDENCE` (sufficiency YES) |
| 021 | Derived Measurement B: USD 100.00 to USD 110.00 | C: Threshold | `NO_CORRESPONDENCE` (sufficiency NO) |
| 022 | Derived Measurement C: USD 100.00 to USD 110.01 | All S | `HUMAN_VERIFIED_CORRESPONDENCE` (sufficiency YES) |
| 023 | Derived Measurement D: new price stated, baseline missing | U: Threshold, Comparator, Magnitude, Unit | `POSSIBLE_CORRESPONDENCE` (sufficiency NO) |
| 024 | Derived Measurement E: baseline corrected after calculation | C: Threshold | `NO_CORRESPONDENCE` (sufficiency NO) |

Every Derived Measurement records:

- its source Observation IDs;
- the exact inputs, with units;
- the formula and the rounding rule ("none: exact decimal arithmetic");
- the output and the calculation time;
- the transformation identifier and version.

The two transformations, `corpus.percent_change_from_two_prices` v1 and `corpus.basis_points_to_percent` v1, are corpus fixture identifiers, not implemented code. AI never performs the authoritative arithmetic. In Case 024 the baseline correction invalidates DM-024-01, which is retained as history, and DM-024-02 = 9.375% is computed from the controlling inputs.

Also tested in other sections: Derived Measurement in 014, 015, 042, 060, 061. Case 061 shows a correct Derived Measurement (25%) that still does not settle the event type.

## 12. Correction and Conflict Cases

| Case | Title | Findings (non-supported) | Oracle |
|---|---|---|---|
| 044 | 7% renewal notice corrected to 12% | All S | `HUMAN_VERIFIED_CORRESPONDENCE` (sufficiency YES) |
| 045 | Public 12% announcement versus Harbor-specific 7% renewal notice | C: Threshold | `NO_CORRESPONDENCE` (sufficiency NO) |
| 046 | Harbor-specific 12% later amended to 8% | C: Threshold | `NO_CORRESPONDENCE` (sufficiency NO) |
| 047 | Notice retracted with no replacement | U: Source relevance, Event type, Threshold, Comparator, Magnitude, Unit, Timing, Effective date, Context | `POSSIBLE_CORRESPONDENCE` (sufficiency NO) |
| 048 | Two current authorized sources conflict (12% and 9%) | U: Threshold, Comparator, Magnitude | `POSSIBLE_CORRESPONDENCE` (sufficiency NO) |
| 049 | Older 12% notice and newer 9% notice; newer does not reference older | U: Threshold, Comparator, Magnitude | `POSSIBLE_CORRESPONDENCE` (sufficiency NO) |
| 050 | Effective date corrected from renewal to six months later | U: Timing, Effective date | `POSSIBLE_CORRESPONDENCE` (sufficiency NO) |

**Observation immutability.** Cases 008, 024, 031, 038, 044, 046, 047, and 050 keep every original Observation with its content hash unchanged. The corrected or retracting item is a separate Observation that references the original. Current findings cite only usable evidence, and `prior_review_history` keeps the superseded review and its outcome.

**Conflicts.** Conflicts are not resolved by recency, by size, or by which document looks more formal.

- **Resolved by a stated basis.** Case 045 is resolved by the plan's authorized source rule. Case 046 is resolved by an amendment that states its own supersession.
- **Left unresolved.** Same-date conflicts (Case 048) and an unmarked newer notice (Case 049) stay `UNRESOLVED` because no supersession basis exists (Section 4.2).
- **Retraction without replacement.** A bare retraction (Case 047) removes support without establishing a new figure.

Also tested in other sections: correction in 008, 024, 031, 038.

## 13. Multi-Source Cases

| Case | Title | Findings (non-supported) | Oracle |
|---|---|---|---|
| 051 | Three sources jointly establish the condition (11%) | All S | `HUMAN_VERIFIED_CORRESPONDENCE` (sufficiency YES) |
| 052 | Authorized general notice plus unauthorized screenshot of Harbor's price | U: Relationship, Source relevance, Timing, Effective date | `POSSIBLE_CORRESPONDENCE` (sufficiency NO) |

Case 051 is the valid composition. No single source suffices, but three accepted, current, authorized Observations jointly support every dimension, and each dimension cites the Observations it rests on.

Invalid compositions:

- different accounts (Case 025);
- different suppliers (Case 026);
- different contracts (Case 029);
- different time periods with a stale source (Case 037);
- an unauthorized source (Case 052).

There is no evidence accumulation score: more sources never strengthen an outcome.

All cases tagged multi-source: 020, 021, 022, 024, 025, 026, 028, 029, 033, 035, 037, 045, 051, 052, 061.

## 14. BAEC Revalidation Cases

| Case | Title | Findings (non-supported) | Oracle |
|---|---|---|---|
| 053 | Harbor changed procedure pack supplier | U: Entity, Relationship, Source relevance, Timing, Effective date | `POSSIBLE_CORRESPONDENCE` (revalidation flag, sufficiency NO) |
| 054 | Agreement terminated early and replaced by a renegotiated agreement | C: Threshold; U: Relationship, Timing, Effective date | `NO_CORRESPONDENCE` (revalidation flag, sufficiency NO) |
| 055 | Buyer withdraws the condition; a matching 12% notice then arrives | All S | `POSSIBLE_CORRESPONDENCE` (revalidation flag, sufficiency YES) |
| 056 | Buyer replaces the threshold with 'more than 20%'; a 12% notice arrives | All S | `POSSIBLE_CORRESPONDENCE` (revalidation flag, sufficiency YES) |
| 057 | Engine 1 currentness reading missing at plan activation | None (not classified) | Blocked: Monitoring Plan not activated; processing stops before Observation creation |

These are forward-dependency cases. Engine 1 v1.0 does not implement a BAEC revalidation lifecycle (Stage A Section 4.3, known dependency). In every case:

- Engine 2 raises `BAEC_REVALIDATION_REQUIRED`, with the triggering evidence;
- it does not decide that the BAEC is invalid;
- it does not clear the flag;
- `HUMAN_VERIFIED_CORRESPONDENCE` stays blocked until Engine 1 reports the BAEC current again.

Cases 036, 055, and 056 show that perfect correspondence evidence and a sufficiency finding of `YES` still yield `POSSIBLE_CORRESPONDENCE` while the flag is active. Case 054 shows that `NO_CORRESPONDENCE` may still be recorded. Case 057 shows plan activation failing closed when Engine 1 currentness cannot be read.

Triggers covered:

- changed supplier (Case 053);
- renegotiated and terminated agreement (Case 054);
- renewal occurred without evaluation, with the timing window expired (Case 036);
- explicit withdrawal (Case 055);
- a replacement threshold (Case 056).

Also tested in other sections: BAEC revalidation in 036.

## 15. Semantic Ambiguity Cases

| Case | Title | Findings (non-supported) | Oracle |
|---|---|---|---|
| 058 | 'A double-digit increase, around 10%' | U: Threshold, Comparator, Magnitude | `POSSIBLE_CORRESPONDENCE` (sufficiency NO) |
| 059 | 'A significant increase' | U: Threshold, Comparator, Magnitude, Unit | `POSSIBLE_CORRESPONDENCE` (sufficiency NO) |
| 060 | 'Discount reduced by 12%' | U: Event type, Threshold, Comparator, Magnitude, Unit | `POSSIBLE_CORRESPONDENCE` (sufficiency NO) |
| 061 | Promotional pricing expires at renewal (USD 80.00 to USD 100.00) | U: Event type, Context | `POSSIBLE_CORRESPONDENCE` (sufficiency NO) |
| 062 | 'Our effective cost rises 12% due to taxes'; supplier prices unchanged | C: Event type, Threshold, Context | `NO_CORRESPONDENCE` (sufficiency NO) |

Ambiguous wording is not forced into threshold support. Where the evidence itself establishes that supplier pricing did not rise (Case 062, a tax increase), `CONTRADICTED` is evidence-based rather than a guess. Elsewhere the corpus prefers `UNRESOLVED`.

Where the other listed phrases appear:

- "roughly 12%": Case 011;
- "12% including freight" and "base price unchanged but fees rise 15%": Case 018;
- "more than ten points": Case 017.

Also tested in other sections: semantic ambiguity in 007, 011, 012, 017, 018, 019, 030.

## 16. Action Boundary

Every classified case, including every `HUMAN_VERIFIED_CORRESPONDENCE` case, has the same expected action boundary: **stop for human attention**. No case may automatically:

- contact the buyer;
- create an opportunity;
- change account state;
- send a message;
- trigger a campaign;
- create a sales task.

Cases that stop before classification have a stricter boundary: nothing is surfaced at all. The corpus wording is plain language, not a production enum. Action Review remains a future boundary that Stage A names but does not design. Case 064 also flags an adversity context (a facility shutdown) for contextual human review (RC-30).

## 17. Prohibited Inferences

Every case prohibits at least these inferences:

- the buyer is evaluating alternatives;
- the buyer intends to purchase;
- the buyer intends to switch suppliers;
- the account is an opportunity;
- the seller has permission to contact the buyer.

Cases with source ambiguity also prohibit treating captured content as true. Revalidation cases prohibit concluding that the BAEC is invalid. Several cases name the specific shortcut they are built to catch, such as "'more than 10%' means 'at least 10%'", "four copies are four independent confirmations", or "Engine 2 may apply the buyer's new 20% threshold".

## 18. Coverage Summary

| Measure | Count |
|---|---|
| Total cases | 64 |
| `NO_CORRESPONDENCE` | 20 |
| `POSSIBLE_CORRESPONDENCE` | 32 |
| `HUMAN_VERIFIED_CORRESPONDENCE` | 9 |
| Stopped before classification | 3 |
| `BAEC_REVALIDATION_REQUIRED` true | 5 |
| Cases with a Derived Measurement | 8 |
| Cases with an unauthorized packet | 3 |
| Correction or retraction cases | 9 |
| Multi-source cases | 15 |

All 64 cases are intentionally retained because each covers a distinct boundary. The minimum lists in the Stage B specification contain about 85 items. After merging items that test the same boundary (for example, wrong customer with different-account composition, or 1,100 basis points with duplicate evidence), 64 distinct boundaries remain, above the original 40 to 48 target. The count was approved at review.

## 19. Known Dependencies

- **Engine 1 revalidation lifecycle.** Engine 1 v1.0 does not implement BAEC revalidation. Cases 036 and 053 to 057 are forward-dependency cases. They test that Engine 2 raises the flag and fails closed; clearing the flag needs a future, separately approved Engine 1 capability.
- **No confirmed Harbor BAEC exists in Engine 1 today.** The public demo stops at authorization. `FIXTURE-HBR-BAEC-001` is a synthetic stand-in.
- **Interpretations decided in review.** The questions earlier listed as open are now decided:
  - the eleven-plus-four model (Section 4.1);
  - threshold and comparator, approximate figures, supersession, rename and entity continuity, and human sufficiency (Section 4.2).

  None remains open in this corpus. None is a Stage A amendment.
- **Pricing scope (deliberate domain boundary).** The corpus does not define whether the buyer's "pricing" means base price, surcharge, freight, landed cost, one SKU, multiple SKUs, or weighted portfolio cost (`PLAN-RULE-NO-PRICING-BASIS`). That interpretation requires authoritative buyer or Monitoring Plan context. Cases 018, 019, and 030 leave it to a human finding and `UNRESOLVED` rather than inventing one. This is a deliberate boundary, not a Stage B defect.

## 20. Stage B Acceptance Criteria

| # | Criterion | Status |
|---|---|---|
| 1 | At least 40 substantive cases | Met (64) |
| 2 | The eight Stage A Harbor cases are preserved as 001 to 008 | Met |
| 3 | Every case traces to the locked Stage A contract (authority notes) | Met |
| 4 | Threshold edge cases are represented | Met |
| 5 | Comparator precision is tested | Met |
| 6 | Entity errors are tested | Met |
| 7 | Timing errors are tested | Met |
| 8 | Authorized and unauthorized source cases exist | Met |
| 9 | Corrections and retractions exist | Met |
| 10 | Conflicting sources exist | Met |
| 11 | Derived Measurements exist | Met |
| 12 | Multi-source composition exists | Met |
| 13 | `BAEC_REVALIDATION_REQUIRED` exists | Met |
| 14 | Semantic ambiguity prefers `UNRESOLVED` over guessing | Met |
| 15 | No scores exist | Met |
| 16 | No purchase-intent inference exists | Met |
| 17 | No automatic action exists | Met |
| 18 | No Engine 1 file changed | Met |
| 19 | No production Engine 2 code exists | Met |
| 20 | JSON consistency checks pass | Met (temporary, untracked check; not part of the repository) |
| 21 | Eleven dimensions carry findings; the four cross-cutting checks are structured and carry none | Met |
| 22 | Threshold and comparator findings follow Section 4.2 | Met |
| 23 | Approximate figures make no precision claim | Met |
| 24 | Every supersession records an authority basis | Met |
| 25 | Entity continuity rests on authoritative evidence; a continuity failure raises `BAEC_REVALIDATION_REQUIRED` | Met |
| 26 | Sufficiency uses the Stage A vocabulary (`YES`, `NO`, `UNKNOWN`) | Met |

**Stage B is LOCKED (October 8, 2026). All acceptance criteria are satisfied.** The lock records that:

- all 64 cases are intentionally retained because they cover distinct boundaries;
- 11 correspondence dimensions plus 4 separate cross-cutting checks is the authoritative Stage A model, and only the 11 dimensions carry findings or enter the outcome rule;
- human sufficiency uses the Stage A vocabulary `YES`, `NO`, `UNKNOWN`, and only `YES` can support `HUMAN_VERIFIED_CORRESPONDENCE`;
- the pricing-scope interpretation (base price, surcharge, freight, landed cost, one or several SKUs, weighted cost) is a deliberate human and domain boundary that requires authoritative buyer or Monitoring Plan context, not an unresolved Stage B architecture question.

Locking Stage B approves no implementation. Stage C requires its own approved design.
