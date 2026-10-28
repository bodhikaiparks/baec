# Phase 1 smoke-check migration checklist

Every exploratory smoke check run during Steps 4 to 6, with its pytest equivalent. Unless noted, the pytest case ID is the smoke-check label, shown in square brackets after the test function name when pytest lists the case. The smoke scripts were throwaway and are not in the repository.

Total smoke checks: 216. With a pytest equivalent: 216. Unaccounted: 0.

| # | Suite | Smoke check | pytest equivalent |
|---|---|---|---|
| 1 | Step 4 | AI inference as evidence | `tests/test_models.py::test_rc32_evidence_excerpt_rejects` |
| 2 | Step 4 | unknown provenance as evidence | `tests/test_models.py::test_rc32_evidence_excerpt_rejects` |
| 3 | Step 4 | raw string instead of enum | `tests/test_models.py::test_rc32_evidence_excerpt_rejects` |
| 4 | Step 4 | MET without evidence | `tests/test_models.py::test_rc02_criterion_assessment_rejects` |
| 5 | Step 4 | UNKNOWN without evidence | `tests/test_models.py::test_rc05_criterion_assessment_accepts` |
| 6 | Step 4 | 'more than 10%' as GREATER_THAN 10 | `tests/test_provenance.py::test_rc18_threshold_wording_is_accepted_as_stated` |
| 7 | Step 4 | 'around 10%' as APPROXIMATELY | `tests/test_provenance.py::test_rc18_threshold_wording_is_accepted_as_stated` |
| 8 | Step 4 | 'significant' qualitative | `tests/test_provenance.py::test_rc18_threshold_wording_is_accepted_as_stated` |
| 9 | Step 4 | number invented for 'significant' | `tests/test_provenance.py::test_rc18_no_threshold_may_be_invented` |
| 10 | Step 4 | numeric comparator without number | `tests/test_provenance.py::test_rc18_no_threshold_may_be_invented` |
| 11 | Step 4 | float instead of Decimal | `tests/test_provenance.py::test_rc18_no_threshold_may_be_invented` |
| 12 | Step 4 | NONE_STATED with recurrence only | `tests/test_provenance.py::test_rc18_threshold_wording_is_accepted_as_stated` |
| 13 | Step 4 | unnormalizable wording, comparator None | `tests/test_provenance.py::test_rc18_threshold_wording_is_accepted_as_stated` |
| 14 | Step 4 | number with comparator None | `tests/test_provenance.py::test_rc18_no_threshold_may_be_invented` |
| 15 | Step 4 | candidate with no verbatim quote | `tests/test_models.py::test_candidate_accepts` |
| 16 | Step 4 | candidate with verbatim quote | `tests/test_provenance.py::test_rc33_buyer_statement_accepted_when_verbatim_or_absent` |
| 17 | Step 4 | empty-string buyer quote | `tests/test_provenance.py::test_rc33_buyer_statement_is_never_fabricated` |
| 18 | Step 4 | candidate with 3 criteria | `tests/test_models.py::test_rc02_candidate_rejects` |
| 19 | Step 4 | candidate with duplicate criterion | `tests/test_models.py::test_rc02_candidate_rejects` |
| 20 | Step 4 | confirmed record | `tests/test_models.py::test_rc13_record_accepts_consistent_classification` |
| 21 | Step 4 | confirmed without confirmation | `tests/test_models.py::test_rc13_rc31_record_rejects_contradictory_classification` |
| 22 | Step 4 | confirmed, seller-seeded | `tests/test_models.py::test_rc13_rc31_record_rejects_contradictory_classification` |
| 23 | Step 4 | confirmed, uncertain origin | `tests/test_models.py::test_rc13_rc31_record_rejects_contradictory_classification` |
| 24 | Step 4 | confirmed, one criterion UNKNOWN | `tests/test_models.py::test_rc13_rc31_record_rejects_contradictory_classification` |
| 25 | Step 4 | confirmation for wrong action | `tests/test_models.py::test_rc13_rc31_record_rejects_contradictory_classification` |
| 26 | Step 4 | confirmation for wrong subject | `tests/test_models.py::test_rc13_rc31_record_rejects_contradictory_classification` |
| 27 | Step 4 | NOT_BAEC (seller-seeded) with reason | `tests/test_models.py::test_rc13_record_accepts_consistent_classification` |
| 28 | Step 4 | NOT_BAEC without reason | `tests/test_models.py::test_rc13_rc31_record_rejects_contradictory_classification` |
| 29 | Step 4 | INSUFFICIENT_EVIDENCE with confirmation | `tests/test_models.py::test_rc13_rc31_record_rejects_contradictory_classification` |
| 30 | Step 4 | naive timestamp | `tests/test_models.py::test_rc13_rc31_record_rejects_contradictory_classification` |
| 31 | Step 4 | dormancy judgment | `tests/test_models.py::test_rc22_dormancy_judgment_accepts` |
| 32 | Step 4 | dormancy judgment with wrong-action auth | `tests/test_models.py::test_rc22_dormancy_judgment_rejects` |
| 33 | Step 4 | evaluation evidence, buyer fact | `tests/test_models.py::test_rc27_rc34_evaluation_state_evidence_accepts` |
| 34 | Step 4 | evaluation evidence, seller observation | `tests/test_models.py::test_rc27_rc34_evaluation_state_evidence_accepts` |
| 35 | Step 4 | evaluation evidence from external signal | `tests/test_models.py::test_rc27_rc34_evaluation_state_evidence_rejects` |
| 36 | Step 4 | account with state None | `tests/test_models.py::test_rc21_account_may_be_unclassified` |
| 37 | Step 4 | account with raw-string state | `tests/test_models.py::test_rc20_account_rejects_unsupported_state` |
| 38 | Step 4 | overwrite buyer statement | `tests/test_provenance.py::test_rc33_saved_buyer_statement_cannot_be_overwritten` |
| 39 | Step 4 | buyer_role None | `tests/test_models.py::test_candidate_accepts` |
| 40 | Step 4 | buyer_role given | `tests/test_models.py::test_candidate_accepts` |
| 41 | Step 4 | buyer_role empty string | `tests/test_models.py::test_rc02_candidate_rejects` |
| 42 | Step 4 | buyer_role non-string | `tests/test_models.py::test_rc02_candidate_rejects` |
| 43 | Step 4 | EvidenceExcerpt still allows EXTERNAL_EVIDENCE | `tests/test_models.py::test_rc32_evidence_excerpt_accepts` |
| 44 | Step 4 | criterion MET on external evidence | `tests/test_models.py::test_rc02_criterion_assessment_rejects` |
| 45 | Step 4 | criterion NOT_MET on external evidence | `tests/test_models.py::test_rc02_criterion_assessment_rejects` |
| 46 | Step 4 | external evidence mixed with buyer fact | `tests/test_models.py::test_rc02_criterion_assessment_rejects` |
| 47 | Step 4 | criterion on seller observation | `tests/test_models.py::test_rc05_criterion_assessment_accepts` |
| 48 | Step 4 | NOT_BAEC with one NOT_MET | `tests/test_models.py::test_rc13_record_accepts_consistent_classification` |
| 49 | Step 4 | NOT_BAEC but all MET and buyer-generated | `tests/test_models.py::test_rc13_rc31_record_rejects_contradictory_classification` |
| 50 | Step 4 | NOT_BAEC but only UNKNOWN / uncertain | `tests/test_models.py::test_rc13_rc31_record_rejects_contradictory_classification` |
| 51 | Step 4 | INSUFFICIENT with one UNKNOWN | `tests/test_models.py::test_rc13_record_accepts_consistent_classification` |
| 52 | Step 4 | INSUFFICIENT with all MET, origin UNCERTAIN | `tests/test_models.py::test_rc13_record_accepts_consistent_classification` |
| 53 | Step 4 | INSUFFICIENT but a criterion NOT_MET | `tests/test_models.py::test_rc13_rc31_record_rejects_contradictory_classification` |
| 54 | Step 4 | INSUFFICIENT but seller-seeded | `tests/test_models.py::test_rc13_rc31_record_rejects_contradictory_classification` |
| 55 | Step 4 | INSUFFICIENT but all MET and buyer-generated | `tests/test_models.py::test_rc13_rc31_record_rejects_contradictory_classification` |
| 56 | Step 4 | CONFIRMED but one NOT_MET | `tests/test_models.py::test_rc13_rc31_record_rejects_contradictory_classification` |
| 57 | Step 4 | CONFIRMED with staleness None | `tests/test_models.py::test_rc29_record_staleness_rules` |
| 58 | Step 4 | CONFIRMED with REVIEW_DUE | `tests/test_models.py::test_rc13_record_accepts_consistent_classification` |
| 59 | Step 4 | NOT_BAEC with staleness | `tests/test_models.py::test_rc29_record_staleness_rules` |
| 60 | Step 4 | INSUFFICIENT with staleness | `tests/test_models.py::test_rc29_record_staleness_rules` |
| 61 | Step 4 | staleness raw string | `tests/test_models.py::test_rc29_record_staleness_rules` |
| 62 | Step 4 | assessment_for valid criterion | `tests/test_models.py::test_candidate_accepts` |
| 63 | Step 4 | assessment_for raw string | `tests/test_models.py::test_rc02_candidate_rejects` |
| 64 | Step 4 | assessment_for None | `tests/test_models.py::test_rc02_candidate_rejects` |
| 65 | Step 4 | EvidenceExcerpt exposes source_id | `tests/test_models.py::test_rc32_evidence_excerpt_accepts` |
| 66 | Step 4 | old field name source_interaction_id | `tests/test_models.py::test_evidence_excerpt_old_field_name_is_gone` |
| 67 | Step 4 | source_excerpt as seller observation, same interaction | `tests/test_provenance.py::test_rc32_candidate_source_excerpt_accepts` |
| 68 | Step 4 | source_excerpt as plain string | `tests/test_provenance.py::test_rc32_candidate_evidence_comes_from_its_own_interaction` |
| 69 | Step 4 | source_excerpt is external evidence | `tests/test_provenance.py::test_rc32_candidate_evidence_comes_from_its_own_interaction` |
| 70 | Step 4 | source_excerpt from a different interaction | `tests/test_provenance.py::test_rc32_candidate_evidence_comes_from_its_own_interaction` |
| 71 | Step 4 | criterion evidence from a later interaction | `tests/test_provenance.py::test_rc32_candidate_evidence_comes_from_its_own_interaction` |
| 72 | Step 4 | candidate interaction id differs from all its evidence | `tests/test_provenance.py::test_rc32_candidate_evidence_comes_from_its_own_interaction` |
| 73 | Step 4 | one later-interaction excerpt among valid ones | `tests/test_provenance.py::test_rc32_candidate_evidence_comes_from_its_own_interaction` |
| 74 | Step 4 | exact statement present in buyer-fact excerpt | `tests/test_provenance.py::test_rc33_buyer_statement_accepted_when_verbatim_or_absent` |
| 75 | Step 4 | purported exact statement absent from excerpt | `tests/test_provenance.py::test_rc33_buyer_statement_is_never_fabricated` |
| 76 | Step 4 | near match differing only in case | `tests/test_provenance.py::test_rc33_buyer_statement_is_never_fabricated` |
| 77 | Step 4 | near match differing only in spacing | `tests/test_provenance.py::test_rc33_buyer_statement_is_never_fabricated` |
| 78 | Step 4 | seller-observation excerpt containing exact statement | `tests/test_provenance.py::test_rc33_buyer_statement_accepted_when_verbatim_or_absent` |
| 79 | Step 4 | seller paraphrase with buyer_exact_statement=None | `tests/test_provenance.py::test_rc33_buyer_statement_accepted_when_verbatim_or_absent` |
| 80 | Step 4 | seller paraphrase passed off as a quote | `tests/test_provenance.py::test_rc33_buyer_statement_is_never_fabricated` |
| 81 | Step 4 | state-change authorization with target | `tests/test_models.py::test_rc31_authorization_accepts` |
| 82 | Step 4 | state-change authorization without target | `tests/test_models.py::test_rc31_authorization_target_state_rules` |
| 83 | Step 4 | state-change authorization with raw-string target | `tests/test_models.py::test_rc31_authorization_target_state_rules` |
| 84 | Step 4 | CONFIRM_BAEC authorization carrying a target | `tests/test_models.py::test_rc31_authorization_target_state_rules` |
| 85 | Step 4 | RECORD_DORMANCY_JUDGMENT authorization carrying a target | `tests/test_models.py::test_rc31_authorization_target_state_rules` |
| 86 | Step 4 | non-evaluation evidence, buyer fact | `tests/test_models.py::test_rc27_rc34_evaluation_state_evidence_accepts` |
| 87 | Step 4 | non-evaluation evidence, seller observation | `tests/test_models.py::test_rc27_rc34_evaluation_state_evidence_accepts` |
| 88 | Step 4 | non-evaluation evidence from external signal | `tests/test_models.py::test_rc27_rc34_evaluation_state_evidence_rejects` |
| 89 | Step 4 | non-evaluation evidence as plain string | `tests/test_models.py::test_rc27_rc34_evaluation_state_evidence_rejects` |
| 90 | Step 4 | non-evaluation evidence naive timestamp | `tests/test_models.py::test_rc27_rc34_evaluation_state_evidence_rejects` |
| 91 | Step 5 | Harbor candidate -> CONFIRMED_BAEC, reasons=(), reason_text=None | `tests/test_baec_rules.py::test_rc13_classification_precedence_and_reasons` |
| 92 | Step 5 | seller-seeded -> NOT_BAEC | `tests/test_baec_rules.py::test_rc13_classification_precedence_and_reasons` |
| 93 | Step 5 | uncertain origin -> INSUFFICIENT_EVIDENCE | `tests/test_baec_rules.py::test_rc13_classification_precedence_and_reasons` |
| 94 | Step 5 | C1 not met (active evaluation) -> NOT_BAEC | `tests/test_baec_rules.py::test_rc13_classification_precedence_and_reasons` |
| 95 | Step 5 | C4 unknown (missing linkage) -> INSUFFICIENT_EVIDENCE | `tests/test_baec_rules.py::test_rc13_classification_precedence_and_reasons` |
| 96 | Step 5 | NOT_MET outranks UNKNOWN; all disqualifiers listed C1,C4,origin; unknown C2 omitted | `tests/test_baec_rules.py::test_rc13_classification_precedence_and_reasons` |
| 97 | Step 5 | multiple unknowns listed in order, origin last | `tests/test_baec_rules.py::test_rc13_classification_precedence_and_reasons` |
| 98 | Step 5 | NOT_MET + UNCERTAIN origin -> NOT_BAEC, uncertain not listed | `tests/test_baec_rules.py::test_rc13_classification_precedence_and_reasons` |
| 99 | Step 5 | reason order independent of assessment order | `tests/test_baec_rules.py::test_rc13_classification_precedence_and_reasons` |
| 100 | Step 5 | same input twice gives identical result | `tests/test_baec_rules.py::test_rc13_classification_precedence_and_reasons` |
| 101 | Step 5 | classify a non-candidate | `tests/test_baec_rules.py::test_classify_rejects_non_candidate` |
| 102 | Step 5 | all 243 combinations: rules and model agree; each saves only through the correct factory | `tests/test_baec_rules.py::test_rc13_exhaustive_classification_matches_oracle (243 cases) and ::test_rc13_exhaustive_totals` |
| 103 | Step 5 | confirmed record created as CURRENT with confirmation, no reason | `tests/test_baec_rules.py::test_rc29_new_confirmed_record_is_current` |
| 104 | Step 5 | valid authorization cannot upgrade seller-seeded candidate | `tests/test_baec_rules.py::test_rc31_authorization_never_changes_classification` |
| 105 | Step 5 | valid authorization cannot upgrade insufficient candidate | `tests/test_baec_rules.py::test_rc31_authorization_never_changes_classification` |
| 106 | Step 5 | confirmed record with no confirmation | `tests/test_baec_rules.py::test_rc31_authorization_never_changes_classification` |
| 107 | Step 5 | confirmed record with wrong-action authorization | `tests/test_baec_rules.py::test_rc31_authorization_never_changes_classification` |
| 108 | Step 5 | confirmed record with authorization for another BAEC | `tests/test_baec_rules.py::test_rc31_authorization_never_changes_classification` |
| 109 | Step 5 | qualifying candidate saved as non-confirmed | `tests/test_baec_rules.py::test_rc31_authorization_never_changes_classification` |
| 110 | Step 5 | non-confirmed factory has no confirmation parameter | `tests/test_baec_rules.py::test_rc31_nonconfirmed_factory_cannot_accept_confirmation` |
| 111 | Step 5 | criterion reason without criterion | `tests/test_baec_rules.py::test_rc13_classification_result_rejects_contradictions` |
| 112 | Step 5 | origin reason with criterion | `tests/test_baec_rules.py::test_rc13_classification_result_rejects_contradictions` |
| 113 | Step 5 | CONFIRMED result with reason_text | `tests/test_baec_rules.py::test_rc13_classification_result_rejects_contradictions` |
| 114 | Step 5 | NOT_BAEC result with no reasons | `tests/test_baec_rules.py::test_rc13_classification_result_rejects_contradictions` |
| 115 | Step 5 | INSUFFICIENT result with no reason_text | `tests/test_baec_rules.py::test_rc13_classification_result_rejects_contradictions` |
| 116 | Step 5 | canonical NOT_BAEC result rebuilds | `tests/test_baec_rules.py::test_classification_result_accepts_canonical` |
| 117 | Step 5 | canonical INSUFFICIENT result rebuilds | `tests/test_baec_rules.py::test_classification_result_accepts_canonical` |
| 118 | Step 5 | NOT_BAEC carrying CRITERION_UNKNOWN | `tests/test_baec_rules.py::test_rc13_classification_result_rejects_contradictions` |
| 119 | Step 5 | NOT_BAEC carrying ORIGIN_UNCERTAIN | `tests/test_baec_rules.py::test_rc13_classification_result_rejects_contradictions` |
| 120 | Step 5 | NOT_BAEC with one valid and one mismatched kind | `tests/test_baec_rules.py::test_rc13_classification_result_rejects_contradictions` |
| 121 | Step 5 | INSUFFICIENT carrying CRITERION_NOT_MET | `tests/test_baec_rules.py::test_rc13_classification_result_rejects_contradictions` |
| 122 | Step 5 | INSUFFICIENT carrying ORIGIN_SELLER_SEEDED | `tests/test_baec_rules.py::test_rc13_classification_result_rejects_contradictions` |
| 123 | Step 5 | tampered text: arbitrary sentence | `tests/test_baec_rules.py::test_rc13_classification_result_rejects_contradictions` |
| 124 | Step 5 | tampered text: one character changed | `tests/test_baec_rules.py::test_rc13_classification_result_rejects_contradictions` |
| 125 | Step 5 | tampered text: trailing space | `tests/test_baec_rules.py::test_rc13_classification_result_rejects_contradictions` |
| 126 | Step 5 | tampered text: text from the other classification | `tests/test_baec_rules.py::test_rc13_classification_result_rejects_contradictions` |
| 127 | Step 5 | tampered text: text omits one of the reasons | `tests/test_baec_rules.py::test_rc13_classification_result_rejects_contradictions` |
| 128 | Step 5 | raw string not equal to reason kind | `tests/test_enums.py::test_raw_string_does_not_equal_enum_member` |
| 129 | Step 6 | None -> ACTIVE_OPPORTUNITY: allowed | `tests/test_state_machine.py::test_rc20_transition_matrix` |
| 130 | Step 6 | None -> CONDITIONALLY_DORMANT: allowed | `tests/test_state_machine.py::test_rc20_transition_matrix` |
| 131 | Step 6 | None -> NO_PLAUSIBLE_PATH: allowed | `tests/test_state_machine.py::test_rc20_transition_matrix` |
| 132 | Step 6 | ACTIVE_OPPORTUNITY -> ACTIVE_OPPORTUNITY: rejected SAME_STATE only | `tests/test_state_machine.py::test_rc20_transition_matrix` |
| 133 | Step 6 | ACTIVE_OPPORTUNITY -> CONDITIONALLY_DORMANT: allowed | `tests/test_state_machine.py::test_rc20_transition_matrix` |
| 134 | Step 6 | ACTIVE_OPPORTUNITY -> NO_PLAUSIBLE_PATH: allowed | `tests/test_state_machine.py::test_rc20_transition_matrix` |
| 135 | Step 6 | CONDITIONALLY_DORMANT -> ACTIVE_OPPORTUNITY: allowed | `tests/test_state_machine.py::test_rc20_transition_matrix` |
| 136 | Step 6 | CONDITIONALLY_DORMANT -> CONDITIONALLY_DORMANT: rejected SAME_STATE only | `tests/test_state_machine.py::test_rc20_transition_matrix` |
| 137 | Step 6 | CONDITIONALLY_DORMANT -> NO_PLAUSIBLE_PATH: allowed | `tests/test_state_machine.py::test_rc20_transition_matrix` |
| 138 | Step 6 | NO_PLAUSIBLE_PATH -> ACTIVE_OPPORTUNITY: allowed | `tests/test_state_machine.py::test_rc20_transition_matrix` |
| 139 | Step 6 | NO_PLAUSIBLE_PATH -> CONDITIONALLY_DORMANT: allowed | `tests/test_state_machine.py::test_rc20_transition_matrix` |
| 140 | Step 6 | NO_PLAUSIBLE_PATH -> NO_PLAUSIBLE_PATH: rejected SAME_STATE only | `tests/test_state_machine.py::test_rc20_transition_matrix` |
| 141 | Step 6 | signal-only: dormant -> active with no evaluation evidence rejected | `tests/test_state_machine.py::test_rc27_rc22_representative_transition_behaviour` |
| 142 | Step 6 | nothing supplied: both rejections, canonical order | `tests/test_state_machine.py::test_rc27_rc22_representative_transition_behaviour` |
| 143 | Step 6 | addressability UNKNOWN passes and is returned unresolved | `tests/test_state_machine.py::test_rc27_rc22_representative_transition_behaviour` |
| 144 | Step 6 | addressability YES leaves nothing unresolved | `tests/test_state_machine.py::test_rc27_rc22_representative_transition_behaviour` |
| 145 | Step 6 | allowed No Plausible Path preserves ground and reason | `tests/test_state_machine.py::test_rc27_rc22_representative_transition_behaviour` |
| 146 | Step 6 | allowed dormant/active carry no ground or reason | `tests/test_state_machine.py::test_rc27_rc22_representative_transition_behaviour` |
| 147 | Step 6 | original Account is not modified | `tests/test_state_machine.py::test_rc27_rc22_representative_transition_behaviour` |
| 148 | Step 6 | missing authorization | `tests/test_state_machine.py::test_rc31_authorization_is_bound_to_action_account_and_destination` |
| 149 | Step 6 | wrong action (CONFIRM_BAEC) | `tests/test_state_machine.py::test_rc31_authorization_is_bound_to_action_account_and_destination` |
| 150 | Step 6 | wrong subject | `tests/test_state_machine.py::test_rc31_authorization_is_bound_to_action_account_and_destination` |
| 151 | Step 6 | dormancy authorization reused for Active | `tests/test_state_machine.py::test_rc31_authorization_is_bound_to_action_account_and_destination` |
| 152 | Step 6 | Active authorization reused for No Plausible Path | `tests/test_state_machine.py::test_rc31_authorization_is_bound_to_action_account_and_destination` |
| 153 | Step 6 | wrong action and wrong subject both reported | `tests/test_state_machine.py::test_rc31_authorization_is_bound_to_action_account_and_destination` |
| 154 | Step 6 | BAEC missing | `tests/test_state_machine.py::test_rc22_rc29_conditionally_dormant_gate` |
| 155 | Step 6 | BAEC not confirmed | `tests/test_state_machine.py::test_rc22_rc29_conditionally_dormant_gate` |
| 156 | Step 6 | BAEC wrong account | `tests/test_state_machine.py::test_rc22_rc29_conditionally_dormant_gate` |
| 157 | Step 6 | BAEC REVIEW_DUE blocks | `tests/test_state_machine.py::test_rc22_rc29_conditionally_dormant_gate` |
| 158 | Step 6 | BAEC STALE blocks | `tests/test_state_machine.py::test_rc22_rc29_conditionally_dormant_gate` |
| 159 | Step 6 | BAEC RETIRED blocks | `tests/test_state_machine.py::test_rc22_rc29_conditionally_dormant_gate` |
| 160 | Step 6 | judgment missing | `tests/test_state_machine.py::test_rc22_rc29_conditionally_dormant_gate` |
| 161 | Step 6 | judgment for a different BAEC | `tests/test_state_machine.py::test_rc22_rc29_conditionally_dormant_gate` |
| 162 | Step 6 | plausibility NO blocks | `tests/test_state_machine.py::test_rc22_rc29_conditionally_dormant_gate` |
| 163 | Step 6 | plausibility UNKNOWN blocks | `tests/test_state_machine.py::test_rc22_rc29_conditionally_dormant_gate` |
| 164 | Step 6 | plausibility NOT_YET blocks | `tests/test_state_machine.py::test_rc22_rc29_conditionally_dormant_gate` |
| 165 | Step 6 | addressability NO blocks | `tests/test_state_machine.py::test_rc22_rc29_conditionally_dormant_gate` |
| 166 | Step 6 | addressability NOT_YET blocks | `tests/test_state_machine.py::test_rc22_rc29_conditionally_dormant_gate` |
| 167 | Step 6 | BAEC missing skips dependent JUDGMENT_WRONG_BAEC, keeps independent ones | `tests/test_state_machine.py::test_rc22_rc29_conditionally_dormant_gate` |
| 168 | Step 6 | many failures reported together in canonical order | `tests/test_state_machine.py::test_rc22_rc29_conditionally_dormant_gate` |
| 169 | Step 6 | Active -> dormant without non-evaluation evidence | `tests/test_state_machine.py::test_rc34_leaving_active_opportunity_requires_non_evaluation_evidence` |
| 170 | Step 6 | Active -> No Plausible Path without non-evaluation evidence | `tests/test_state_machine.py::test_rc34_leaving_active_opportunity_requires_non_evaluation_evidence` |
| 171 | Step 6 | non-evaluation evidence for another account | `tests/test_state_machine.py::test_rc34_leaving_active_opportunity_requires_non_evaluation_evidence` |
| 172 | Step 6 | not required when not leaving Active | `tests/test_state_machine.py::test_rc34_leaving_active_opportunity_requires_non_evaluation_evidence` |
| 173 | Step 6 | if supplied when not required, still checked for account | `tests/test_state_machine.py::test_rc34_leaving_active_opportunity_requires_non_evaluation_evidence` |
| 174 | Step 6 | evaluation evidence for another account | `tests/test_state_machine.py::test_rc27_rc20_active_and_no_plausible_path_prerequisites` |
| 175 | Step 6 | ground missing | `tests/test_state_machine.py::test_rc27_rc20_active_and_no_plausible_path_prerequisites` |
| 176 | Step 6 | reason missing (None) | `tests/test_state_machine.py::test_rc27_rc20_active_and_no_plausible_path_prerequisites` |
| 177 | Step 6 | reason missing (blank) | `tests/test_state_machine.py::test_rc27_rc20_active_and_no_plausible_path_prerequisites` |
| 178 | Step 6 | ground and reason both missing | `tests/test_state_machine.py::test_rc27_rc20_active_and_no_plausible_path_prerequisites` |
| 179 | Step 6 | rejected No Plausible Path does not preserve ground/reason | `tests/test_state_machine.py::test_rc27_rc20_active_and_no_plausible_path_prerequisites` |
| 180 | Step 6 | same state with nothing supplied reports only SAME_STATE | `tests/test_state_machine.py::test_same_state_is_terminal` |
| 181 | Step 6 | same state (Active) with no auth or evidence | `tests/test_state_machine.py::test_same_state_is_terminal` |
| 182 | Step 6 | same state (No Plausible Path) with no ground/reason | `tests/test_state_machine.py::test_same_state_is_terminal` |
| 183 | Step 6 | account as string | `tests/test_state_machine.py::test_rc27_rc31_wrong_types_raise` |
| 184 | Step 6 | signal-like excerpt passed as evaluation evidence | `tests/test_state_machine.py::test_rc27_rc31_wrong_types_raise` |
| 185 | Step 6 | EvaluationEvidence built from external evidence | `tests/test_state_machine.py::test_rc27_rc31_wrong_types_raise` |
| 186 | Step 6 | authorization as True | `tests/test_state_machine.py::test_rc27_rc31_wrong_types_raise` |
| 187 | Step 6 | authorization as dict | `tests/test_state_machine.py::test_rc27_rc31_wrong_types_raise` |
| 188 | Step 6 | baec_record as candidate | `tests/test_state_machine.py::test_rc27_rc31_wrong_types_raise` |
| 189 | Step 6 | ground as raw string | `tests/test_state_machine.py::test_rc27_rc31_wrong_types_raise` |
| 190 | Step 6 | reason as number | `tests/test_state_machine.py::test_rc27_rc31_wrong_types_raise` |
| 191 | Step 6 | type error raised even on same-state request | `tests/test_state_machine.py::test_rc27_rc31_wrong_types_raise` |
| 192 | Step 6 | canonical rejected result rebuilds | `tests/test_state_machine.py::test_transition_result_accepts_consistent_results` |
| 193 | Step 6 | allowed without new_account | `tests/test_state_machine.py::test_transition_result_rejects_contradictory_results` |
| 194 | Step 6 | allowed with new_account in wrong state | `tests/test_state_machine.py::test_transition_result_rejects_contradictory_results` |
| 195 | Step 6 | allowed with new_account for another account | `tests/test_state_machine.py::test_transition_result_rejects_contradictory_results` |
| 196 | Step 6 | allowed with rejections | `tests/test_state_machine.py::test_transition_result_rejects_contradictory_results` |
| 197 | Step 6 | allowed with from_state == to_state | `tests/test_state_machine.py::test_transition_result_rejects_contradictory_results` |
| 198 | Step 6 | allowed No Plausible Path without ground | `tests/test_state_machine.py::test_transition_result_rejects_contradictory_results` |
| 199 | Step 6 | allowed No Plausible Path without reason | `tests/test_state_machine.py::test_transition_result_rejects_contradictory_results` |
| 200 | Step 6 | allowed Active carrying ground/reason | `tests/test_state_machine.py::test_transition_result_rejects_contradictory_results` |
| 201 | Step 6 | rejected with new_account | `tests/test_state_machine.py::test_transition_result_rejects_contradictory_results` |
| 202 | Step 6 | rejected with no rejections | `tests/test_state_machine.py::test_transition_result_rejects_contradictory_results` |
| 203 | Step 6 | rejected with tampered text | `tests/test_state_machine.py::test_transition_result_rejects_contradictory_results` |
| 204 | Step 6 | rejected with text for another destination | `tests/test_state_machine.py::test_transition_result_rejects_contradictory_results` |
| 205 | Step 6 | rejected with unresolved | `tests/test_state_machine.py::test_transition_result_rejects_contradictory_results` |
| 206 | Step 6 | rejected preserving ground/reason | `tests/test_state_machine.py::test_transition_result_rejects_contradictory_results` |
| 207 | Step 6 | rejections out of canonical order | `tests/test_state_machine.py::test_transition_result_rejects_contradictory_results` |
| 208 | Step 6 | SAME_STATE combined with another rejection | `tests/test_state_machine.py::test_transition_result_rejects_contradictory_results` |
| 209 | Step 6 | SAME_STATE reported when states differ | `tests/test_state_machine.py::test_transition_result_rejects_contradictory_results` |
| 210 | Step 6 | raw-string rejection kind | `tests/test_state_machine.py::test_transition_result_rejects_contradictory_results` |
| 211 | Step 6 | one ADDRESSABILITY_UNKNOWN on allowed dormancy accepted | `tests/test_state_machine.py::test_transition_result_accepts_consistent_results` |
| 212 | Step 6 | unresolved on allowed Active Opportunity | `tests/test_state_machine.py::test_transition_result_rejects_contradictory_results` |
| 213 | Step 6 | unresolved on allowed No Plausible Path | `tests/test_state_machine.py::test_transition_result_rejects_contradictory_results` |
| 214 | Step 6 | duplicate ADDRESSABILITY_UNKNOWN on dormancy | `tests/test_state_machine.py::test_transition_result_rejects_contradictory_results` |
| 215 | Step 6 | unresolved on rejected dormancy | `tests/test_state_machine.py::test_transition_result_rejects_contradictory_results` |
| 216 | Step 6 | every rejection kind has a phrase and was exercised or is constructible | `tests/test_state_machine.py::test_every_rejection_kind_is_covered` |
