# Research Contract

**Project:** BAEC Trigger Intelligence
**Status:** Approved by project owner for Phase 1
**Source of truth:** "When the Buyer Is Not In-Market: Buyer-Articulated Evaluation Contingencies in B2B Sales Encounters" (conceptual article, submission-ready manuscript). Section references below (§) point to that manuscript.

This document fixes the theory rules the software must obey. If code, prompts, UI text, or documentation conflict with this contract, they are wrong. If this contract conflicts with the manuscript, the manuscript wins and this contract must be corrected.

Read this before changing any BAEC logic.

---

## 1. How to read this contract

Every rule carries one of four labels. The label says where the rule's authority comes from.

| Label | Meaning |
|---|---|
| **DEFINITIONAL** | Part of how the manuscript defines a construct. Changing it changes the construct. |
| **PROPOSED** | A proposition or dimension the manuscript puts forward for future testing. Not empirically established. |
| **MANAGERIAL** | A managerial implication or architecture the manuscript proposes. Outside the core theoretical model. |
| **IMPLEMENTATION** | A choice made for this software. Not a claim of the manuscript. |

Never present a PROPOSED, MANAGERIAL, or IMPLEMENTATION rule as a research finding. The manuscript is conceptual: none of it has been empirically validated.

---

## 2. BAEC

### RC-01. Definition (DEFINITIONAL, §3.1)

> An explicit, buyer-generated statement communicated by an organizational buyer who is not currently evaluating relevant alternatives, identifying a prospective condition the buyer expects would make initiating or reopening such evaluation worthwhile.

### RC-02. Four constitutive criteria (DEFINITIONAL, §2.4, §3.1)

All four are required. Each is assessed independently, with its own supporting evidence.

| ID | Criterion | Requirement |
|---|---|---|
| C1 | Present non-evaluation | The buyer is presently outside an active evaluation of the relevant alternatives. |
| C2 | Prospective condition | The condition is prospective, not an event that has already occurred. |
| C3 | Buyer articulation | The condition is articulated by the buyer rather than supplied solely by the seller. |
| C4 | Evaluation linkage | The buyer links the condition to initiating or reopening evaluation. |

### RC-03. BAEC is information, not cognition or commitment (DEFINITIONAL, §3.1, §4.3)

A BAEC is what the buyer communicated. It is not a claim that a fully formed rule existed in the buyer's mind before the conversation, and it is not a guaranteed organizational commitment. The conversation may have retrieved, sharpened, or helped construct the contingency.

### RC-04. Endpoint is evaluation activation (DEFINITIONAL, §3.2, §3.4)

The response a BAEC points to is the opening or reopening of evaluation. It is not purchase, not switching, and not selection of the focal seller.

### RC-05. C1 requires state qualification (DEFINITIONAL, §2.2)

A statement such as "we are all set" may reflect true non-evaluation, a block, a stall, or avoidance of disclosure. C1 is not met merely because the buyer declined. The basis for the C1 finding must be recorded, and UNKNOWN is a permitted finding.

### RC-06. What a BAEC is not (DEFINITIONAL, §3.2–3.5, Table 1)

| Not this | Because |
|---|---|
| Conditional intention | Conditional form alone is not the construct; BAEC concerns a presently non-evaluating buyer and evaluation activation. |
| Implementation intention | Those enact an existing goal; a BAEC can arise with no current goal to change or search. |
| Current problem recognition | BAEC identifies a prospective condition, not a present deficiency. |
| Problem framing | BAEC concerns when evaluation becomes worthwhile, not how a current problem is understood. |
| Vendor-selection rule | Those operate inside an evaluation; BAEC concerns whether evaluation begins. |
| Supplier search | BAEC is articulated while relevant search is not active. |
| Switching intention | A BAEC can coexist with near-zero current switching intention. |
| Real option to switch | BAEC is explicit account information, not the economic option. |
| Realized trigger / compelling event | BAEC is stated before the event occurs. |
| Generic signal-based selling | Those signals are seller-defined or model-inferred; a BAEC condition comes from the buyer. |

Also not a BAEC: current dissatisfaction, a purchase intention, or a prediction that the buyer will buy.

---

## 3. CEE

### RC-07. Definition (DEFINITIONAL, §3.6)

> A salesperson conversational behavior in which, following an indication that a buyer is not currently evaluating relevant alternatives, the salesperson invites the buyer to identify a prospective condition that would make initiating or reopening evaluation worthwhile before supplying an additional seller-generated problem, need, or value argument.

### RC-08. CEE boundaries (DEFINITIONAL, §2.2, §3.6)

- CEE is state-contingent. It applies to qualified present non-evaluation, not to every conversation.
- CEE is not generic discovery, not objection handling, and not another seller-generated reason to change.
- The wording is not the construct. What matters is that the buyer supplies the condition and links it to evaluation.
- A leading question that supplies the condition ("Would backorders make you reconsider?") does not meet the buyer-generated requirement in its pure form. It may be useful in practice, but it produces a different informational object.

### RC-09. CEE is not required for a BAEC (DEFINITIONAL, §6.2)

A buyer may articulate a contingency spontaneously. The manuscript distinguishes spontaneous from elicited articulation because asking about future behavior can itself influence it (the question-behavior effect).

---

## 4. Articulation origin

### RC-10. Origin values (IMPLEMENTATION, operationalizing C3)

Origin is determined by who supplied the **core prospective condition**, not by who spoke last or who added detail.

| Value | When it applies | Classification effect |
|---|---|---|
| `BUYER_GENERATED` | The buyer supplied the core condition. This includes a buyer who, after a seller prompt, independently supplies a materially different condition. | May be `CONFIRMED_BAEC` if C1–C4 are met. |
| `SELLER_SEEDED` | The seller supplied the core condition and the buyer only agreed, narrowed it, or attached a threshold to it. | `NOT_BAEC`. |
| `UNCERTAIN` | The available evidence does not establish who supplied the core condition. | `INSUFFICIENT_EVIDENCE`. |

Examples:

- Seller: "What would have to change?" Buyer: "If deliveries became a recurring monthly problem." → `BUYER_GENERATED`.
- Seller: "Would a price increase make you reconsider?" Buyer: "Over 15%, yes." → `SELLER_SEEDED`. The buyer's threshold is real buyer evidence and is preserved, but the condition was the seller's.
- Seller: "Would a price increase make you reconsider?" Buyer: "Price, no. If they dropped on-site implementation support, we'd look." → `BUYER_GENERATED` for the support condition.
- A note reads "buyer would reconsider if pricing rose" with no record of who raised pricing. → `UNCERTAIN`.

**Interpretive note.** The manuscript's wording is that the condition must not be supplied "solely" by the seller. Treating a seller-supplied condition with a buyer-supplied threshold as `SELLER_SEEDED` is a deliberately conservative reading chosen for this software. It is not a ruling by the manuscript on that case.

Seller-seeded and uncertain records are preserved with their reason. They are not discarded and never relabeled as BAECs.

### RC-11. Elicitation mode is recorded (IMPLEMENTATION, motivated by §6.2)

Each record notes whether the articulation was spontaneous, CEE-elicited, or unknown. This does not affect classification.

---

## 5. Classification

### RC-12. Allowed classifications (IMPLEMENTATION)

`CONFIRMED_BAEC`, `NOT_BAEC`, `INSUFFICIENT_EVIDENCE`. No others.

### RC-13. Classification rule (IMPLEMENTATION of RC-02 and RC-10)

Each criterion has a finding of `MET`, `NOT_MET`, or `UNKNOWN`.

1. Any criterion `NOT_MET`, or origin `SELLER_SEEDED` → `NOT_BAEC`, with the reason stated.
2. Otherwise, all four `MET` and origin `BUYER_GENERATED` → `CONFIRMED_BAEC`.
3. Otherwise → `INSUFFICIENT_EVIDENCE`, with what is missing stated.

A finding of `MET` or `NOT_MET` requires a supporting evidence excerpt. The system never forces a yes/no answer when evidence is incomplete.

---

## 6. Validity is separate from informational quality

### RC-14. Four quality dimensions (PROPOSED, §4.2)

| Dimension | Question |
|---|---|
| Specificity | Is the contingency concrete enough to interpret or monitor? |
| Plausibility | Could the condition realistically occur within a commercially meaningful horizon? |
| Addressability | Could the focal seller credibly respond if the condition emerged? |
| Representational authority | Does the person articulating it have sufficient involvement or influence for the statement to inform expectations about organizational evaluation behavior? |

The manuscript states these "should not be treated as a validated scale" and that it is unknown whether they form an index, a profile, or independent moderators (§6.1).

### RC-15. No scoring (IMPLEMENTATION, enforcing RC-14)

Quality is recorded as descriptive, evidence-based notes. UNKNOWN is allowed. The system produces no numeric scores, grades, probabilities, or confidence percentages for BAEC quality.

### RC-16. A valid BAEC can be commercially useless (PROPOSED, §4.2, §7)

Meeting C1–C4 says nothing about usefulness. Quality judgments never alter the constitutive classification, and the classification never implies quality.

---

## 7. Stringency and threshold fidelity

### RC-17. Stringency (PROPOSED, §4.4)

Stringency is used narrowly: the magnitude or consequentiality of the articulated change required before the buyer expects evaluation to become worthwhile. It is not a general psychological threshold construct. The system records what the buyer said; it does not rate stringency as high or low.

### RC-18. Threshold preservation (IMPLEMENTATION)

The buyer's threshold is stored exactly as stated.

| Buyer said | Stored | Never stored as |
|---|---|---|
| "more than 15%" | > 15% | ≥ 15% |
| "around 10%" | approximately 10% | ≥ 10% |
| "a significant increase" | qualitative: "significant"; number: unknown | any number |

Recurrence ("recurring, not a single late delivery") and timing ("at renewal") conditions are preserved the same way. No threshold, date, or count is ever invented.

---

## 8. Buying-center authority

### RC-19. One contact is not the account (PROPOSED, §4.3, §6.4)

A BAEC records what one person articulated. Buyer role, decision relevance, known and unknown authority, and corroboration are recorded. A lower-authority contact's BAEC is still useful account information, but it is never presented as account-wide intent. Corroboration may strengthen the information; it does not create a commitment.

---

## 9. Account states

### RC-20. Exactly three states (MANAGERIAL, §5.1)

| State | Meaning |
|---|---|
| `ACTIVE_OPPORTUNITY` | The buyer is currently evaluating relevant alternatives. |
| `CONDITIONALLY_DORMANT` | Not presently evaluating, and the buyer has articulated a plausible future condition under which evaluation may become worthwhile. Warrants preservation and selective reassessment, not repeated persuasion. |
| `NO_PLAUSIBLE_PATH` | The account is not presently evaluating, and available information does not support a plausible seller-relevant path to future evaluation. This may include no plausible BAEC, a condition judged too implausible to warrant continued attention, or only clearly seller-unaddressable contingencies. Disengagement may be appropriate. |

"Conditionally dormant" is an account-management label. The manuscript states it should not be treated as a validated psychological construct.

No additional states may be added. Workflow statuses (monitoring active, signal detected, review pending) are not account states.

### RC-21. Unclassified accounts (IMPLEMENTATION)

An account that has not been qualified has `account_state = None`. This is the absence of a classification, not a fourth state.

### RC-22. Conditionally Dormant gate (IMPLEMENTATION of §4.8, P9, §5.1)

An account may enter `CONDITIONALLY_DORMANT` only when all of these hold:

1. A `CONFIRMED_BAEC` exists for the account.
2. A human has judged plausibility: **YES**.
3. A human has **not** judged addressability **NO**.

Addressability `UNKNOWN` does not block dormancy, but it remains visibly unresolved on the account. Addressability `NOT_YET` blocks dormancy because the required human judgment has not yet occurred. This is an implementation clarification.

The BAEC used to enter `CONDITIONALLY_DORMANT` must have staleness `CURRENT`. `REVIEW_DUE`, `STALE`, and `RETIRED` block entry until the BAEC has been reassessed. This is an implementation clarification (see RC-29).

These judgments belong to the account-state decision. They are not part of BAEC validity (RC-16).

### RC-23. Rational disengagement (PROPOSED, §4.8, P9)

CEE may legitimately end in disengagement: no plausible condition, an implausible one, or one the seller clearly cannot address. The system must make this outcome as easy to record as any other. It must not convert every "no" into a follow-up.

### RC-24. No Plausible Path is reversible (IMPLEMENTATION)

New buyer information may later change the state.

### RC-25. Category scope (IMPLEMENTATION, V0.1 simplification)

The manuscript defines non-evaluation relative to "relevant alternatives," which is category-specific. V0.1 assumes one product category per account, so state is held at the account level. This is a simplification, not a claim of the manuscript.

### RC-34. Leaving Active Opportunity (IMPLEMENTATION)

`ACTIVE_OPPORTUNITY` means the buyer is currently evaluating. V0.1 therefore requires `BUYER_FACT` or `SELLER_OBSERVATION` evidence that the buyer is no longer evaluating before an account moves from `ACTIVE_OPPORTUNITY` to `CONDITIONALLY_DORMANT` or `NO_PLAUSIBLE_PATH`. This evidence-form requirement is a software guard that preserves the meaning of the three states. It is not a requirement of the manuscript.

(Numbered RC-34 because it was added after RC-01 to RC-33 were approved; it is placed here with the other account-state rules.)

---

## 10. Signals and reactivation

### RC-26. Buyer-specific monitoring (MANAGERIAL, §5.2)

The monitoring specification originates from the buyer's articulated condition. The system may operationalize that condition into observable evidence. It may not broaden what matters beyond what the buyer said.

The manuscript claims no novelty for AI monitoring and states that the AI extension sits outside the core theoretical model.

### RC-27. A signal is not an opportunity (MANAGERIAL, §5.2)

A possible match never creates an opportunity automatically. It prompts human review. `CONDITIONALLY_DORMANT` → `ACTIVE_OPPORTUNITY` requires evidence that the buyer has actually initiated or reopened evaluation, plus human authorization. A signal alone is never sufficient.

### RC-28. Human review questions (MANAGERIAL, §5.2)

On a possible match, a human verifies: whether the evidence is accurate, whether the original contingency remains valid, and whether re-engagement is appropriate.

### RC-29. BAECs go stale (MANAGERIAL, §5.1, §6.3)

A BAEC is "a reason to reassess, not a permanent permission slip." Personnel, priorities, contracts, and authority change. The manuscript establishes no validated expiration period; any review interval in the software is an IMPLEMENTATION setting.

### RC-30. Governance (MANAGERIAL, §5.2, §6.6)

- Monitoring is limited to legitimate business data, authorized sources, and information relevant to the articulated condition. Not unrelated personal or sensitive information.
- Design principle: AI-assisted detection, human contextual judgment.
- Signals involving adversity (shortages, layoffs, service failures, regulatory events) require human contextual review. No automated outreach.

---

## 11. Human authorization

### RC-31. Authoritative changes require human authorization (IMPLEMENTATION)

Confirming a BAEC, approving monitoring, recording a review, and changing account state all require human authorization.

In the Phase 1 domain layer, `HumanAuthorization` expresses this as a domain requirement only. It is **not** the security boundary. Later application, service, and MCP server code must establish that the authorization originated from an actual human action. A value produced by a model is never proof of human approval.

A state-change authorization names its destination state and is valid only for that destination. It is not bound to the originating state. This is an implementation clarification.

---

## 12. Provenance

### RC-32. Five categories, never collapsed (IMPLEMENTATION)

| Category | Meaning |
|---|---|
| BUYER FACT | What the buyer actually said. |
| SELLER OBSERVATION | What the human seller documented. |
| EXTERNAL EVIDENCE | What a later authorized source reports. |
| AI INFERENCE | How AI interprets relationships among the above. |
| UNKNOWN | What has not been established. |

### RC-33. Source evidence is immutable (IMPLEMENTATION)

The buyer's exact statement, source excerpt, source interaction, and capture time are never silently overwritten. AI-derived normalization is stored separately and never replaces them. Corrections use an auditable amendment.

---

## 13. Claims this project must never make

The manuscript is conceptual. The software demonstrates the framework; it cannot validate it. Passing tests are software tests and synthetic evaluation cases, not scientific evidence.

Never claim that:

- BAEC or CEE is scientifically or empirically validated.
- BAEC has been empirically shown to predict purchase, switching, or evaluation. P10's proposed prospective relationship remains untested.
- AI monitoring based on BAEC improves sales performance.
- The quality dimensions are a validated scale.
- Any staleness interval is research-derived.
- The software's tests validate the theory.

---

## 14. Propositions (reference only)

All PROPOSED and untested. The software does not assume any of them is true.

| Prop. | Summary |
|---|---|
| P1 | CEE is more likely than immediate seller value communication to produce a BAEC among non-evaluating buyers. |
| P2a / P2b | CEE's advantage over current-state discovery grows as present dissatisfaction falls; discovery's advantage grows as unarticulated present problems grow. |
| P3 | Willingness to consider the seller rises with addressability. |
| P4 | The BAEC–evaluation relationship strengthens with decision relevance and corroboration. |
| P5 | Search and evaluation costs raise stringency. |
| P6 | Incumbent dependence raises stringency. |
| P7 | Higher consequences of prospective failure lower stringency. |
| P8 | CEE produces less perceived pressure than immediate persuasion. |
| P9 | No plausible or only clearly unaddressable BAECs → lower later evaluation engagement. |
| P10 | When the condition occurs, buyers who articulated it are more likely to open evaluation than comparable buyers who had not. |

---

## 15. Traceability

Phase 1 rules, the code that enforces them, and the tests that check them. Paths are relative to the repository root; code is under `baec_app/domain/`. Rules marked "later phases" are not yet enforced in code.

Phase 3 storage-layer enforcement of these rules is traced separately in `docs/PHASE3_PERSISTENCE_TRACEABILITY.md`; that addendum adds no rules.

| Rule | Code | Tests |
|---|---|---|
| RC-02 | `models.py` (`CriterionAssessment`, `BaecCandidate`) | `tests/test_models.py::test_rc02_criterion_assessment_rejects`, `::test_rc02_candidate_rejects` |
| RC-05 | `models.py` (`CriterionAssessment`) | `tests/test_models.py::test_rc05_criterion_assessment_accepts` |
| RC-10, RC-13 | `baec_rules.py` (`classify_candidate`, `ClassificationResult`) | `tests/test_baec_rules.py::test_rc13_classification_precedence_and_reasons`, `::test_rc13_reason_text_is_exact_and_deterministic`, `::test_rc13_exhaustive_classification_matches_oracle`, `::test_rc13_exhaustive_totals`, `::test_rc13_classification_result_rejects_contradictions` |
| RC-13 (record consistency) | `models.py` (`BaecRecord`) | `tests/test_models.py::test_rc13_record_accepts_consistent_classification`, `::test_rc13_rc31_record_rejects_contradictory_classification` |
| RC-12, RC-20, RC-21 | `enums.py`; `models.py` (`Account`) | `tests/test_enums.py::test_enum_members_are_exactly_as_approved`, `::test_rc20_rc21_exactly_three_account_states_and_no_unclassified_member`; `tests/test_models.py::test_rc21_account_may_be_unclassified`, `::test_rc20_account_rejects_unsupported_state`; `tests/test_state_machine.py::test_rc20_transition_matrix` |
| RC-18 | `models.py` (`StringencyExpression`) | `tests/test_provenance.py::test_rc18_threshold_wording_is_accepted_as_stated`, `::test_rc18_no_threshold_may_be_invented`, `::test_rc18_more_than_is_not_stored_as_at_least`, `::test_rc18_qualitative_threshold_has_no_number` |
| RC-32 | `models.py` (`EvidenceExcerpt`, `BaecCandidate`) | `tests/test_models.py::test_rc32_evidence_excerpt_rejects`; `tests/test_provenance.py::test_rc32_candidate_evidence_comes_from_its_own_interaction` |
| RC-22 | `state_machine.py` (`transition_to_conditionally_dormant`); `models.py` (`DormancyJudgment`) | `tests/test_state_machine.py::test_rc22_rc29_conditionally_dormant_gate`, `::test_rc22_exhaustive_dormancy_grid`, `::test_rc22_dormancy_grid_size_and_allowed_count` |
| RC-20, RC-23 (No Plausible Path) | `state_machine.py` (`transition_to_no_plausible_path`) | `tests/test_state_machine.py::test_rc27_rc20_active_and_no_plausible_path_prerequisites`, `::test_rc20_every_no_plausible_path_ground_is_accepted_with_a_reason` |
| RC-27 | `state_machine.py` (`transition_to_active_opportunity`); `models.py` (`EvaluationEvidence`) | `tests/test_state_machine.py::test_rc27_rc22_representative_transition_behaviour`, `::test_rc27_rc31_wrong_types_raise`; `tests/test_models.py::test_rc27_rc34_evaluation_state_evidence_rejects` |
| RC-29 | `models.py` (`BaecRecord`); `baec_rules.py` (`create_confirmed_baec_record`); `state_machine.py` | `tests/test_models.py::test_rc29_record_staleness_rules`; `tests/test_baec_rules.py::test_rc29_new_confirmed_record_is_current`; `tests/test_state_machine.py::test_rc22_rc29_conditionally_dormant_gate` |
| RC-31 (Phase 1 subset) | `models.py` (`HumanAuthorization`); `baec_rules.py` (factories); `state_machine.py` | `tests/test_models.py::test_rc31_authorization_target_state_rules`; `tests/test_baec_rules.py::test_rc31_authorization_never_changes_classification`, `::test_rc31_nonconfirmed_factory_cannot_accept_confirmation`; `tests/test_state_machine.py::test_rc31_authorization_is_bound_to_action_account_and_destination`, `::test_rc31_authorization_valid_only_for_its_own_destination`, `::test_rc31_fake_authorization_values_never_pass` |
| RC-33 | `models.py` (frozen dataclasses; `BaecCandidate` verbatim rule) | `tests/test_provenance.py::test_rc33_buyer_statement_is_never_fabricated`, `::test_rc33_saved_buyer_statement_cannot_be_overwritten`, `::test_rc33_every_domain_dataclass_is_frozen`, `::test_rc33_source_fields_on_a_saved_record_cannot_be_reassigned` |
| RC-34 | `models.py` (`NonEvaluationEvidence`); `state_machine.py` | `tests/test_state_machine.py::test_rc34_leaving_active_opportunity_requires_non_evaluation_evidence`; `tests/test_models.py::test_rc27_rc34_evaluation_state_evidence_rejects` |
| RC-15, RC-19, RC-26, RC-28, RC-30 | Later phases | Later phases |

RC-31 (Phase 1 subset): Phase 1 enforces human authorization for confirming a BAEC, recording a dormancy judgment, and changing account state, as a domain requirement only. Authorization for approving a monitoring specification and for recording a signal review remains a later-phase requirement, as does proof that any authorization originated from an actual human action.

RC-19: representational authority and buying-center corroboration are deferred. Phase 1 only ensures, under RC-32, that a single candidate's criteria rest on evidence from its own source interaction.

---

## 16. Changing this contract

Changes require the project owner's explicit approval. A change to a DEFINITIONAL rule is only permitted to correct a mismatch with the manuscript.
