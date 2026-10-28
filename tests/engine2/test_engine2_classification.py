"""Human Correspondence Review consistency and the deterministic outcome rule (Stage A Section 11.2)."""

from dataclasses import FrozenInstanceError, replace
from itertools import product

import pytest

from baec_app.engine2.classification import (
    CrossCuttingCheck,
    DimensionAssessment,
    HumanCorrespondenceReview,
    classify_correspondence,
)
from baec_app.engine2.domain import (
    ALL_DIMENSIONS,
    CorrespondenceDimension,
    CorrespondenceOutcome,
    CrossCuttingCheckName,
    DimensionFinding,
    SufficiencyFinding,
    Supersession,
    SupersessionBasis,
)
from baec_app.engine2.errors import Engine2ValidationError, ReviewInconsistentError
from tests.engine2 import builders as b

D, F, O, Y = CorrespondenceDimension, DimensionFinding, CorrespondenceOutcome, SufficiencyFinding
EV = ("OBS-T-01",)


def test_all_supported_and_yes_is_human_verified():
    assert b.review().outcome is O.HUMAN_VERIFIED_CORRESPONDENCE


@pytest.mark.parametrize("sufficiency", [Y.NO, Y.UNKNOWN])
def test_all_supported_without_yes_is_possible(sufficiency):
    assert b.review(sufficiency=sufficiency).outcome is O.POSSIBLE_CORRESPONDENCE


@pytest.mark.parametrize("dimension", list(D))
def test_any_contradicted_required_dimension_is_no(dimension):
    if dimension in (D.COMPARATOR_MATCH, D.NUMERIC_MAGNITUDE, D.UNIT_MATCH):
        # a decided threshold needs these SUPPORTED; contradict them with the threshold left unresolved
        findings = b.findings(**{dimension.name: (F.CONTRADICTED, EV), "THRESHOLD_MATCH": (F.UNRESOLVED, ())})
        checks = b.checks(unresolved={D.THRESHOLD_MATCH})
    else:
        findings, checks = b.findings(**{dimension.name: (F.CONTRADICTED, EV)}), b.checks()
    assert b.review(findings=findings, checks=checks).outcome is O.NO_CORRESPONDENCE


def test_unresolved_required_dimension_is_possible():
    r = b.review(findings=b.findings(TIMING_MATCH=(F.UNRESOLVED, ())), checks=b.checks(unresolved={D.TIMING_MATCH}))
    assert r.outcome is O.POSSIBLE_CORRESPONDENCE


def test_contradiction_wins_over_unresolved_yes_and_revalidation():
    r = b.review(findings=b.findings(THRESHOLD_MATCH=(F.CONTRADICTED, EV), TIMING_MATCH=(F.UNRESOLVED, ())),
                 checks=b.checks(unresolved={D.TIMING_MATCH}), baec_revalidation_required=True,
                 revalidation_triggers=("timing context has expired",))
    assert r.outcome is O.NO_CORRESPONDENCE


def test_revalidation_flag_blocks_human_verified():
    r = b.review(baec_revalidation_required=True, revalidation_triggers=("the referenced entity has changed",))
    assert r.outcome is O.POSSIBLE_CORRESPONDENCE


def test_revalidation_flag_and_triggers_must_agree():
    with pytest.raises(ReviewInconsistentError):
        b.review(baec_revalidation_required=True)
    with pytest.raises(ReviewInconsistentError):
        b.review(revalidation_triggers=("dangling trigger",))


def test_exhaustive_rule_over_one_dimension_sufficiency_and_flag():
    for finding, suff, flag in product([F.SUPPORTED, F.CONTRADICTED, F.UNRESOLVED], list(Y), [False, True]):
        unresolved = {D.TIMING_MATCH} if finding is F.UNRESOLVED else set()
        r = b.review(findings=b.findings(TIMING_MATCH=(finding, () if finding is F.UNRESOLVED else EV)),
                     checks=b.checks(unresolved=unresolved), sufficiency=suff, baec_revalidation_required=flag,
                     revalidation_triggers=("trigger",) if flag else ())
        if finding is F.CONTRADICTED:
            expected = O.NO_CORRESPONDENCE
        elif finding is F.SUPPORTED and suff is Y.YES and not flag:
            expected = O.HUMAN_VERIFIED_CORRESPONDENCE
        else:
            expected = O.POSSIBLE_CORRESPONDENCE
        assert r.outcome is expected and classify_correspondence(r) is expected


def test_outcome_is_derived_and_cannot_be_set_or_overridden():
    r = b.review()
    with pytest.raises(TypeError):
        b.review(outcome=O.HUMAN_VERIFIED_CORRESPONDENCE)
    with pytest.raises((FrozenInstanceError, AttributeError)):
        r.outcome = O.NO_CORRESPONDENCE
    assert "outcome" not in {f.name for f in r.__dataclass_fields__.values()}


def test_review_needs_exactly_eleven_unique_findings():
    with pytest.raises(ReviewInconsistentError):
        b.review(findings=b.findings()[:-1])
    with pytest.raises(ReviewInconsistentError):
        b.review(findings=b.findings() + (b.findings()[0],))


def test_required_dimension_cannot_be_not_applicable():
    with pytest.raises(ReviewInconsistentError):
        b.review(findings=b.findings(TIMING_MATCH=(F.NOT_APPLICABLE, ())))


def test_non_required_dimension_must_be_not_applicable_and_is_excluded_from_the_rule():
    plan = b.plan(required_dimensions=ALL_DIMENSIONS - {D.RENEWAL_OR_EFFECTIVE_DATE_MATCH},
                  not_applicable_dimensions=frozenset({D.RENEWAL_OR_EFFECTIVE_DATE_MATCH}))
    led = b.ledger(b.active(plan))
    r = b.review(led, findings=b.findings(RENEWAL_OR_EFFECTIVE_DATE_MATCH=(F.NOT_APPLICABLE, ())))
    assert r.outcome is O.HUMAN_VERIFIED_CORRESPONDENCE
    with pytest.raises(ReviewInconsistentError):
        b.review(led, findings=b.findings(RENEWAL_OR_EFFECTIVE_DATE_MATCH=(F.CONTRADICTED, EV)))


@pytest.mark.parametrize("finding", [F.SUPPORTED, F.CONTRADICTED])
def test_decided_finding_needs_accepted_evidence(finding):
    with pytest.raises(ReviewInconsistentError):
        b.review(findings=b.findings(ENTITY_MATCH=(finding, ())))
    with pytest.raises(ReviewInconsistentError):
        b.review(findings=b.findings(ENTITY_MATCH=(finding, ("PLAN-RULE-TEST",))))
    with pytest.raises(ReviewInconsistentError):
        b.review(findings=b.findings(ENTITY_MATCH=(finding, ("OBS-NOT-IN-LEDGER",))))


def test_superseded_observation_cannot_support_a_current_finding():
    a = b.active()
    old = b.observe(a, "OBS-T-01")
    new = b.observe(a, "OBS-T-02", b.item("PKT-T-02", content="CORRECTION: 7%."))
    led = b.ledger(a, [old, new], supersessions=(
        Supersession("OBS-T-01", "OBS-T-02", SupersessionBasis.EXPLICIT_CORRECTION_OR_RETRACTION, retraction=False),))
    with pytest.raises(ReviewInconsistentError):
        b.review(led)  # default findings cite OBS-T-01
    assert b.review(led, findings=b.findings(ev=("OBS-T-02",))).outcome is O.HUMAN_VERIFIED_CORRESPONDENCE


def _with_check(name, note="Noted.", refs=(), affected=frozenset(), base=None):
    return tuple(
        CrossCuttingCheck(c.check, note, tuple(refs), frozenset(affected)) if c.check is name else c
        for c in (base or b.checks())
    )


def test_harbor_style_plan_requires_numeric_prerequisites_for_a_threshold_result():
    for prerequisite in ("COMPARATOR_MATCH", "NUMERIC_MAGNITUDE", "UNIT_MATCH"):
        with pytest.raises(ReviewInconsistentError):
            b.review(findings=b.findings(**{prerequisite: (F.UNRESOLVED, ())}))


def test_threshold_prerequisites_follow_the_plan_not_a_global_numeric_structure():
    not_required = frozenset({D.COMPARATOR_MATCH, D.NUMERIC_MAGNITUDE, D.UNIT_MATCH})
    plan = b.plan(required_dimensions=ALL_DIMENSIONS - not_required, not_applicable_dimensions=not_required)
    led = b.ledger(b.active(plan))
    na = {d.name: (F.NOT_APPLICABLE, ()) for d in not_required}
    r = b.review(led, findings=b.findings(THRESHOLD_MATCH=(F.CONTRADICTED, EV), **na))
    assert r.outcome is O.NO_CORRESPONDENCE
    r = b.review(led, findings=b.findings(**na))
    assert r.outcome is O.HUMAN_VERIFIED_CORRESPONDENCE


def test_missing_evidence_names_only_unresolved_dimensions():
    with pytest.raises(ReviewInconsistentError):
        b.review(checks=_with_check(CrossCuttingCheckName.MISSING_EVIDENCE, affected={D.TIMING_MATCH}))
    r = b.review(findings=b.findings(TIMING_MATCH=(F.UNRESOLVED, ())),
                 checks=_with_check(CrossCuttingCheckName.MISSING_EVIDENCE, affected={D.TIMING_MATCH}))
    assert r.outcome is O.POSSIBLE_CORRESPONDENCE


def test_unresolved_for_another_reason_need_not_appear_in_missing_evidence():
    r = b.review(findings=b.findings(EVENT_OR_CONDITION_TYPE=(F.UNRESOLVED, ())), checks=b.checks())
    assert r.outcome is O.POSSIBLE_CORRESPONDENCE  # semantic ambiguity; Missing evidence names nothing


def test_provenance_limited_dimension_must_be_unresolved():
    with pytest.raises(ReviewInconsistentError):
        b.review(checks=_with_check(CrossCuttingCheckName.PROVENANCE_QUALITY, affected={D.ENTITY_MATCH}))
    r = b.review(findings=b.findings(ENTITY_MATCH=(F.UNRESOLVED, ())),
                 checks=_with_check(CrossCuttingCheckName.PROVENANCE_QUALITY, affected={D.ENTITY_MATCH}))
    assert r.outcome is O.POSSIBLE_CORRESPONDENCE


def _conflict_ledger():
    a = b.active()
    first = b.observe(a, "OBS-T-01")
    second = b.observe(a, "OBS-T-02", b.item("PKT-T-02", content="Later notice: pricing rises 9% at renewal."))
    return a, first, second


def test_conflict_without_authoritative_resolution_stays_unresolved():
    a, first, second = _conflict_ledger()
    led = b.ledger(a, [first, second])
    conflict = _with_check(CrossCuttingCheckName.CONTRADICTORY_EVIDENCE, "Two current notices disagree.",
                           ("OBS-T-01", "OBS-T-02"), {D.THRESHOLD_MATCH})
    with pytest.raises(ReviewInconsistentError):  # choosing the newer notice is not a basis
        b.review(led, findings=b.findings(THRESHOLD_MATCH=(F.CONTRADICTED, ("OBS-T-02",))), checks=conflict)
    r = b.review(led, findings=b.findings(THRESHOLD_MATCH=(F.UNRESOLVED, ())), checks=conflict)
    assert r.outcome is O.POSSIBLE_CORRESPONDENCE


def test_conflict_resolved_by_a_supersession_basis_can_be_decided():
    a, first, second = _conflict_ledger()
    led = b.ledger(a, [first, second], supersessions=(
        Supersession("OBS-T-01", "OBS-T-02", SupersessionBasis.EXPLICIT_CORRECTION_OR_RETRACTION),))
    conflict = _with_check(CrossCuttingCheckName.CONTRADICTORY_EVIDENCE, "Resolved by explicit correction.",
                           ("OBS-T-01", "OBS-T-02"), {D.THRESHOLD_MATCH})
    r = b.review(led, findings=b.findings(ev=("OBS-T-02",), THRESHOLD_MATCH=(F.CONTRADICTED, ("OBS-T-02",))), checks=conflict)
    assert r.outcome is O.NO_CORRESPONDENCE


def test_conflict_resolved_by_a_plan_rule_can_be_decided():
    conflict = _with_check(CrossCuttingCheckName.CONTRADICTORY_EVIDENCE, "Resolved by the plan's source rule.",
                           ("OBS-T-01",), {D.THRESHOLD_MATCH})
    resolved = b.findings(THRESHOLD_MATCH=(F.SUPPORTED, ("OBS-T-01", "PLAN-RULE-TEST")))
    assert b.review(findings=resolved, checks=conflict).outcome is O.HUMAN_VERIFIED_CORRESPONDENCE


def test_stale_evidence_cannot_support_a_decisive_finding():
    a, first, second = _conflict_ledger()
    led = b.ledger(a, [first, second], supersessions=(
        Supersession("OBS-T-01", "OBS-T-02", SupersessionBasis.EXPLICIT_CORRECTION_OR_RETRACTION),))
    fresh = _with_check(CrossCuttingCheckName.FRESHNESS, "OBS-T-01 is superseded.", ("OBS-T-01",), {D.THRESHOLD_MATCH})
    with pytest.raises(ReviewInconsistentError):
        b.review(led, findings=b.findings(ev=("OBS-T-02",), THRESHOLD_MATCH=(F.SUPPORTED, ("OBS-T-01",))), checks=fresh)
    r = b.review(led, findings=b.findings(ev=("OBS-T-02",)), checks=fresh)
    assert r.outcome is O.HUMAN_VERIFIED_CORRESPONDENCE  # current evidence independently supports it


def test_past_dated_but_authoritative_evidence_can_support_a_contradiction():
    a = b.active()
    old = b.observe(a, "OBS-T-01", b.item(content="Archived notice: pricing rose 12% at the 2025 renewal."))
    fresh = _with_check(CrossCuttingCheckName.FRESHNESS, "Describes a 2025 event; still authoritative for that fact.",
                        ("OBS-T-01",), {D.TIMING_MATCH, D.RENEWAL_OR_EFFECTIVE_DATE_MATCH})
    r = b.review(b.ledger(a, [old]), checks=fresh, findings=b.findings(
        TIMING_MATCH=(F.CONTRADICTED, ("OBS-T-01",)), RENEWAL_OR_EFFECTIVE_DATE_MATCH=(F.CONTRADICTED, ("OBS-T-01",))))
    assert "OBS-T-01" in r.ledger.usable_observation_ids
    assert r.outcome is O.NO_CORRESPONDENCE  # the contradiction decides it, not Freshness


def test_superseded_evidence_cannot_support_a_current_contradiction_but_stays_in_history():
    a, first, second = _conflict_ledger()
    led = b.ledger(a, [first, second], supersessions=(
        Supersession("OBS-T-01", "OBS-T-02", SupersessionBasis.EXPLICIT_CORRECTION_OR_RETRACTION, retraction=True),))
    fresh = _with_check(CrossCuttingCheckName.FRESHNESS, "OBS-T-01 was retracted.", ("OBS-T-01",), {D.TIMING_MATCH})
    with pytest.raises(ReviewInconsistentError):
        b.review(led, checks=fresh, findings=b.findings(ev=("OBS-T-02",), TIMING_MATCH=(F.CONTRADICTED, ("OBS-T-01",))))
    assert "OBS-T-01" in led.observation_ids and "OBS-T-01" not in led.usable_observation_ids


def test_freshness_alone_never_produces_no_correspondence():
    fresh = _with_check(CrossCuttingCheckName.FRESHNESS, "Evidence is old.", ("OBS-T-01",), {D.TIMING_MATCH})
    assert b.review(checks=fresh).outcome is O.HUMAN_VERIFIED_CORRESPONDENCE


def test_checks_are_exactly_four_and_cannot_masquerade_as_findings():
    with pytest.raises(ReviewInconsistentError):
        b.review(checks=b.checks()[:-1])
    for word in ("SUPPORTED", "CONTRADICTED", "UNRESOLVED", "NOT_APPLICABLE"):
        with pytest.raises(Engine2ValidationError):
            CrossCuttingCheck(CrossCuttingCheckName.FRESHNESS, word)
    with pytest.raises(TypeError):
        CrossCuttingCheck(CrossCuttingCheckName.FRESHNESS, "note", finding=F.SUPPORTED)
    with pytest.raises(Engine2ValidationError):
        DimensionAssessment(CrossCuttingCheckName.FRESHNESS, F.SUPPORTED, EV, "a check is not a dimension")


def test_a_check_alone_never_produces_no_correspondence():
    noisy = tuple(CrossCuttingCheck(c.check, "Serious concern noted.", ("OBS-T-01",), frozenset()) for c in b.checks())
    assert b.review(checks=noisy).outcome is O.HUMAN_VERIFIED_CORRESPONDENCE


def test_sufficiency_must_be_the_stage_a_enum():
    with pytest.raises(ReviewInconsistentError):
        b.review(sufficiency="YES")


def test_review_is_immutable_and_has_no_action_or_buyer_state_fields():
    r = b.review()
    with pytest.raises(FrozenInstanceError):
        r.sufficiency = Y.NO
    names = {f for f in HumanCorrespondenceReview.__dataclass_fields__}
    for forbidden in ("permission_to_contact", "account_state", "opportunity", "purchase_intent", "score", "probability"):
        assert not any(forbidden in n for n in names)
    with pytest.raises(TypeError):
        replace(r, permission_to_contact=True)
