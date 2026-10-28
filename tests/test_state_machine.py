"""Account-state transitions (Step 6)."""

from itertools import product

import pytest

from baec_app.domain.enums import (
    AccountState,
    AuthorizationAction,
    NoPlausiblePathGround,
    ProvenanceCategory,
    ReviewAnswer,
    StalenessStatus,
    TransitionRejectionKind,
    TransitionUnresolvedKind,
)
from baec_app.domain.models import Account, DomainValidationError, EvaluationEvidence
from baec_app.domain.state_machine import (
    TransitionResult,
    transition_to_active_opportunity,
    transition_to_conditionally_dormant,
    transition_to_no_plausible_path,
)
from tests.builders import (
    AO,
    CD,
    NOW,
    NP,
    account,
    authorization,
    candidate,
    cases,
    confirmed_record,
    evaluation_evidence,
    excerpt,
    external_excerpt,
    judgment,
    non_evaluation_evidence,
    request_transition as go,
    seller_seeded_record,
    state_auth,
)

A = AuthorizationAction
G = NoPlausiblePathGround
K = TransitionRejectionKind
RA = ReviewAnswer
S = StalenessStatus
T = TransitionResult
U = TransitionUnresolvedKind.ADDRESSABILITY_UNKNOWN
STATES = (AO, CD, NP)


def only(result, *expected):
    """True when the result is a rejection carrying exactly these kinds, in order."""
    return (not result.allowed) and result.rejections == expected and result.new_account is None


def name(state):
    return state.value if state else "None"


# --- transition matrix (RC-20, RC-21) -----------------------------------------

MATRIX = [(frm, to) for frm in (None, AO, CD, NP) for to in STATES]


def _matrix_id(pair):
    frm, to = pair
    suffix = "rejected SAME_STATE only" if frm is to else "allowed"
    return f"{name(frm)} -> {to.value}: {suffix}"


@pytest.mark.parametrize("pair", MATRIX, ids=_matrix_id)
def test_rc20_transition_matrix(pair):
    """Any state or None may move to any different state; same-state is rejected."""
    frm, to = pair
    result = go(frm, to)
    if frm is to:
        assert only(result, K.SAME_STATE)
    else:
        assert result.allowed
        assert result.new_account.state is to
        assert result.from_state is frm
        assert result.to_state is to
        assert result.account_id == "ACC-1"
        assert result.rejections == () and result.rejection_text is None


# --- signal is not an opportunity; representative behaviors (RC-27, RC-22) -----


def _original_account_unchanged():
    original = account(CD)
    transition_to_active_opportunity(original, evaluation_evidence=evaluation_evidence(), authorization=state_auth(AO))
    return original.state is CD


def _np_preserves_ground_and_reason():
    result = go(None, NP, ground=G.CLEARLY_SELLER_UNADDRESSABLE, reason="Only a regulatory mandate; we have no offering.")
    return (
        result.allowed
        and result.ground is G.CLEARLY_SELLER_UNADDRESSABLE
        and result.reason == "Only a regulatory mandate; we have no offering."
    )


@cases(
    [
        (
            "signal-only: dormant -> active with no evaluation evidence rejected",
            lambda: only(go(CD, AO, evaluation_evidence=None), K.EVALUATION_EVIDENCE_MISSING),
        ),
        (
            "nothing supplied: both rejections, canonical order",
            lambda: only(
                go(CD, AO, evaluation_evidence=None, authorization=None),
                K.AUTHORIZATION_MISSING,
                K.EVALUATION_EVIDENCE_MISSING,
            ),
        ),
        (
            "addressability UNKNOWN passes and is returned unresolved",
            lambda: go(None, CD, judgment=judgment(addressability=RA.UNKNOWN)).allowed
            and go(None, CD, judgment=judgment(addressability=RA.UNKNOWN)).unresolved == (U,),
        ),
        ("addressability YES leaves nothing unresolved", lambda: go(None, CD).unresolved == ()),
        ("allowed No Plausible Path preserves ground and reason", _np_preserves_ground_and_reason),
        (
            "allowed dormant/active carry no ground or reason",
            lambda: go(None, CD).ground is None and go(None, AO).reason is None,
        ),
        ("original Account is not modified", _original_account_unchanged),
    ]
)
def test_rc27_rc22_representative_transition_behaviour(check):
    """RC-27: a signal alone never activates. RC-22: unknown addressability stays visible."""
    assert check()


@pytest.mark.parametrize(
    "pair", [p for p in MATRIX if p[0] is not p[1]], ids=lambda p: f"{name(p[0])}->{p[1].value}"
)
def test_original_account_is_never_modified(pair):
    frm, to = pair
    original = account(frm)
    before = (original.account_id, original.name, original.state)
    go(frm, to)
    assert (original.account_id, original.name, original.state) == before


# --- authorization (RC-31) ----------------------------------------------------


@cases(
    [
        ("missing authorization", lambda: only(go(None, CD, authorization=None), K.AUTHORIZATION_MISSING)),
        (
            "wrong action (CONFIRM_BAEC)",
            lambda: only(
                go(None, CD, authorization=authorization(A.CONFIRM_BAEC, "ACC-1")), K.AUTHORIZATION_WRONG_ACTION
            ),
        ),
        (
            "wrong subject",
            lambda: only(go(None, CD, authorization=state_auth(CD, "ACC-2")), K.AUTHORIZATION_WRONG_SUBJECT),
        ),
        (
            "dormancy authorization reused for Active",
            lambda: only(go(CD, AO, authorization=state_auth(CD)), K.AUTHORIZATION_WRONG_TARGET),
        ),
        (
            "Active authorization reused for No Plausible Path",
            lambda: only(go(None, NP, authorization=state_auth(AO)), K.AUTHORIZATION_WRONG_TARGET),
        ),
        (
            "wrong action and wrong subject both reported",
            lambda: only(
                go(None, AO, authorization=authorization(A.CONFIRM_BAEC, "B-1")),
                K.AUTHORIZATION_WRONG_ACTION,
                K.AUTHORIZATION_WRONG_SUBJECT,
            ),
        ),
    ]
)
def test_rc31_authorization_is_bound_to_action_account_and_destination(check):
    assert check()


@pytest.mark.parametrize("target", STATES, ids=lambda s: s.value)
@pytest.mark.parametrize("to", STATES, ids=lambda s: s.value)
def test_rc31_authorization_valid_only_for_its_own_destination(to, target):
    frm = None
    result = go(frm, to, authorization=state_auth(target))
    if target is to:
        assert result.allowed
    else:
        assert only(result, K.AUTHORIZATION_WRONG_TARGET)


@pytest.mark.parametrize("to", STATES, ids=lambda s: s.value)
@pytest.mark.parametrize("action", [A.CONFIRM_BAEC, A.RECORD_DORMANCY_JUDGMENT], ids=lambda a: a.value)
def test_rc31_other_action_authorizations_cannot_change_state(action, to):
    assert only(go(None, to, authorization=authorization(action, "ACC-1")), K.AUTHORIZATION_WRONG_ACTION)


# --- Conditionally Dormant gate (RC-22, RC-29) --------------------------------


def _staleness_rows():
    return [
        (
            f"BAEC {status.value} blocks",
            lambda status=status: only(go(None, CD, baec_record=confirmed_record(staleness=status)), K.BAEC_NOT_CURRENT),
        )
        for status in (S.REVIEW_DUE, S.STALE, S.RETIRED)
    ]


def _plausibility_rows():
    return [
        (
            f"plausibility {answer.value} blocks",
            lambda answer=answer: only(go(None, CD, judgment=judgment(plausibility=answer)), K.PLAUSIBILITY_NOT_YES),
        )
        for answer in (RA.NO, RA.UNKNOWN, RA.NOT_YET)
    ]


@cases(
    [
        ("BAEC missing", lambda: only(go(None, CD, baec_record=None), K.BAEC_MISSING)),
        ("BAEC not confirmed", lambda: only(go(None, CD, baec_record=seller_seeded_record()), K.BAEC_NOT_CONFIRMED)),
        ("BAEC wrong account", lambda: only(go(None, CD, baec_record=confirmed_record("ACC-2")), K.BAEC_WRONG_ACCOUNT)),
        *_staleness_rows(),
        ("judgment missing", lambda: only(go(None, CD, judgment=None), K.JUDGMENT_MISSING)),
        (
            "judgment for a different BAEC",
            lambda: only(go(None, CD, judgment=judgment(baec_id="B-9")), K.JUDGMENT_WRONG_BAEC),
        ),
        *_plausibility_rows(),
        (
            "addressability NO blocks",
            lambda: only(go(None, CD, judgment=judgment(addressability=RA.NO)), K.ADDRESSABILITY_NO),
        ),
        (
            "addressability NOT_YET blocks",
            lambda: only(go(None, CD, judgment=judgment(addressability=RA.NOT_YET)), K.ADDRESSABILITY_NOT_YET),
        ),
        (
            "BAEC missing skips dependent JUDGMENT_WRONG_BAEC, keeps independent ones",
            lambda: only(
                go(None, CD, baec_record=None, judgment=judgment(RA.NO, baec_id="B-9")),
                K.BAEC_MISSING,
                K.PLAUSIBILITY_NOT_YES,
            ),
        ),
        (
            "many failures reported together in canonical order",
            lambda: only(
                go(
                    AO,
                    CD,
                    authorization=None,
                    non_evaluation_evidence=None,
                    baec_record=confirmed_record(staleness=S.STALE),
                    judgment=judgment(RA.NO, RA.NO),
                ),
                K.AUTHORIZATION_MISSING,
                K.NON_EVALUATION_EVIDENCE_MISSING,
                K.BAEC_NOT_CURRENT,
                K.PLAUSIBILITY_NOT_YES,
                K.ADDRESSABILITY_NO,
            ),
        ),
    ]
)
def test_rc22_rc29_conditionally_dormant_gate(check):
    """RC-22: confirmed BAEC plus human judgment. RC-29: the BAEC must be CURRENT."""
    assert check()


DORMANCY_GRID = list(product((None, AO, NP), list(RA), list(RA), list(S)))


def _grid_id(case):
    frm, plausibility, addressability, staleness = case
    return f"{name(frm)}|P={plausibility.value}|A={addressability.value}|{staleness.value}"


@pytest.mark.parametrize("case", DORMANCY_GRID, ids=_grid_id)
def test_rc22_exhaustive_dormancy_grid(case):
    """3 starting points x 4 plausibility x 4 addressability x 4 staleness = 192 cases.

    Expected outcome written directly from RC-22 (as clarified) and RC-29:
    plausibility must be YES; addressability NO and NOT_YET block, UNKNOWN
    passes but stays unresolved; the BAEC must be CURRENT.
    """
    frm, plausibility, addressability, staleness = case
    expected = []
    if staleness.name != "CURRENT":
        expected.append("BAEC_NOT_CURRENT")
    if plausibility.name != "YES":
        expected.append("PLAUSIBILITY_NOT_YES")
    if addressability.name == "NO":
        expected.append("ADDRESSABILITY_NO")
    if addressability.name == "NOT_YET":
        expected.append("ADDRESSABILITY_NOT_YET")

    result = go(
        frm,
        CD,
        baec_record=confirmed_record(staleness=staleness),
        judgment=judgment(plausibility, addressability),
    )

    assert [kind.name for kind in result.rejections] == expected
    if expected:
        assert not result.allowed
        assert result.new_account is None
        assert result.unresolved == ()
    else:
        assert result.allowed
        assert result.new_account.state is CD
        assert [u.name for u in result.unresolved] == (
            ["ADDRESSABILITY_UNKNOWN"] if addressability.name == "UNKNOWN" else []
        )


def test_rc22_dormancy_grid_size_and_allowed_count():
    assert len(DORMANCY_GRID) == 192
    assert len(set(DORMANCY_GRID)) == 192
    allowed = [
        case
        for case in DORMANCY_GRID
        if go(case[0], CD, baec_record=confirmed_record(staleness=case[3]), judgment=judgment(case[1], case[2])).allowed
    ]
    # Per starting point: plausibility YES x addressability {YES, UNKNOWN} x CURRENT = 2.
    assert len(allowed) == 6


# --- leaving Active Opportunity (RC-34) ---------------------------------------


@cases(
    [
        (
            "Active -> dormant without non-evaluation evidence",
            lambda: only(go(AO, CD, non_evaluation_evidence=None), K.NON_EVALUATION_EVIDENCE_MISSING),
        ),
        (
            "Active -> No Plausible Path without non-evaluation evidence",
            lambda: only(go(AO, NP, non_evaluation_evidence=None), K.NON_EVALUATION_EVIDENCE_MISSING),
        ),
        (
            "non-evaluation evidence for another account",
            lambda: only(
                go(AO, NP, non_evaluation_evidence=non_evaluation_evidence("ACC-2")),
                K.NON_EVALUATION_EVIDENCE_WRONG_ACCOUNT,
            ),
        ),
        (
            "not required when not leaving Active",
            lambda: go(NP, CD, non_evaluation_evidence=None).allowed
            and go(None, NP, non_evaluation_evidence=None).allowed,
        ),
        (
            "if supplied when not required, still checked for account",
            lambda: only(
                go(None, NP, non_evaluation_evidence=non_evaluation_evidence("ACC-2")),
                K.NON_EVALUATION_EVIDENCE_WRONG_ACCOUNT,
            ),
        ),
    ]
)
def test_rc34_leaving_active_opportunity_requires_non_evaluation_evidence(check):
    """RC-34: Active Opportunity is not overwritten without evidence evaluation ended."""
    assert check()


# --- Active Opportunity and No Plausible Path prerequisites (RC-27, RC-20) -----


@cases(
    [
        (
            "evaluation evidence for another account",
            lambda: only(
                go(CD, AO, evaluation_evidence=evaluation_evidence("ACC-2")), K.EVALUATION_EVIDENCE_WRONG_ACCOUNT
            ),
        ),
        ("ground missing", lambda: only(go(None, NP, ground=None), K.GROUND_MISSING)),
        ("reason missing (None)", lambda: only(go(None, NP, reason=None), K.REASON_MISSING)),
        ("reason missing (blank)", lambda: only(go(None, NP, reason="   "), K.REASON_MISSING)),
        (
            "ground and reason both missing",
            lambda: only(go(None, NP, ground=None, reason=None), K.GROUND_MISSING, K.REASON_MISSING),
        ),
        (
            "rejected No Plausible Path does not preserve ground/reason",
            lambda: go(None, NP, authorization=None).ground is None
            and go(None, NP, authorization=None).reason is None,
        ),
    ]
)
def test_rc27_rc20_active_and_no_plausible_path_prerequisites(check):
    assert check()


@pytest.mark.parametrize("ground", list(G), ids=lambda g: g.value)
def test_rc20_every_no_plausible_path_ground_is_accepted_with_a_reason(ground):
    result = go(CD, NP, ground=ground, reason="Recorded basis.")
    assert result.allowed and result.ground is ground and result.reason == "Recorded basis."


# --- SAME_STATE is terminal ---------------------------------------------------


@cases(
    [
        (
            "same state with nothing supplied reports only SAME_STATE",
            lambda: only(
                transition_to_conditionally_dormant(account(CD), baec_record=None, judgment=None, authorization=None),
                K.SAME_STATE,
            ),
        ),
        (
            "same state (Active) with no auth or evidence",
            lambda: only(
                transition_to_active_opportunity(account(AO), evaluation_evidence=None, authorization=None),
                K.SAME_STATE,
            ),
        ),
        (
            "same state (No Plausible Path) with no ground/reason",
            lambda: only(
                transition_to_no_plausible_path(account(NP), ground=None, reason=None, authorization=None),
                K.SAME_STATE,
            ),
        ),
    ]
)
def test_same_state_is_terminal(check):
    """A no-op request reports SAME_STATE alone; nothing else is evaluated."""
    assert check()


def test_type_validation_occurs_before_same_state_check():
    """A malformed request raises even when it would otherwise be a SAME_STATE no-op."""
    # Well-formed no-op requests: recordable SAME_STATE rejections.
    assert only(
        transition_to_active_opportunity(account(AO), evaluation_evidence=None, authorization=None), K.SAME_STATE
    )
    # Same no-op requests with one wrong-typed argument each: raise instead.
    with pytest.raises(DomainValidationError):
        transition_to_active_opportunity(account(AO), evaluation_evidence="x", authorization=None)
    with pytest.raises(DomainValidationError):
        transition_to_active_opportunity(account(AO), evaluation_evidence=None, authorization=True)
    with pytest.raises(DomainValidationError):
        transition_to_conditionally_dormant(
            account(CD), baec_record="B-1", judgment=None, authorization=None
        )
    with pytest.raises(DomainValidationError):
        transition_to_conditionally_dormant(
            account(CD), baec_record=None, judgment=None, authorization=None, non_evaluation_evidence="x"
        )
    with pytest.raises(DomainValidationError):
        transition_to_no_plausible_path(account(NP), ground="OTHER", reason=None, authorization=None)
    with pytest.raises(DomainValidationError):
        transition_to_no_plausible_path(account(NP), ground=None, reason=5, authorization=None)


# --- wrong types raise (RC-27, RC-31) -----------------------------------------


@cases(
    [
        (
            "account as string",
            lambda: transition_to_active_opportunity(
                "ACC-1", evaluation_evidence=evaluation_evidence(), authorization=state_auth(AO)
            ),
        ),
        (
            "signal-like excerpt passed as evaluation evidence",
            lambda: go(CD, AO, evaluation_evidence=external_excerpt()),
        ),
        (
            "EvaluationEvidence built from external evidence",
            lambda: EvaluationEvidence("ACC-1", external_excerpt(), NOW),
        ),
        ("authorization as True", lambda: go(None, AO, authorization=True)),
        ("authorization as dict", lambda: go(None, AO, authorization={"human_confirmed": True})),
        ("baec_record as candidate", lambda: go(None, CD, baec_record=candidate())),
        ("ground as raw string", lambda: go(None, NP, ground="OTHER")),
        ("reason as number", lambda: go(None, NP, reason=5)),
        (
            "type error raised even on same-state request",
            lambda: transition_to_active_opportunity(account(AO), evaluation_evidence="x", authorization=None),
        ),
    ]
)
def test_rc27_rc31_wrong_types_raise(check):
    """RC-27: a signal cannot be passed as evaluation evidence. RC-31: a model-supplied
    value such as True or {"human_confirmed": True} is never an authorization."""
    with pytest.raises(DomainValidationError):
        check()


@pytest.mark.parametrize("to", STATES, ids=lambda s: s.value)
@pytest.mark.parametrize(
    "fake", [True, "approved", 1, {"human_confirmed": True}, ("reviewer-1",)], ids=repr
)
def test_rc31_fake_authorization_values_never_pass(fake, to):
    with pytest.raises(DomainValidationError):
        go(None, to, authorization=fake)


# --- every rejection kind, in isolation ---------------------------------------

ISOLATED = {
    K.SAME_STATE: lambda: go(AO, AO),
    K.AUTHORIZATION_MISSING: lambda: go(None, AO, authorization=None),
    K.AUTHORIZATION_WRONG_ACTION: lambda: go(None, AO, authorization=authorization(A.CONFIRM_BAEC, "ACC-1")),
    K.AUTHORIZATION_WRONG_SUBJECT: lambda: go(None, AO, authorization=state_auth(AO, "ACC-2")),
    K.AUTHORIZATION_WRONG_TARGET: lambda: go(None, AO, authorization=state_auth(NP)),
    K.NON_EVALUATION_EVIDENCE_MISSING: lambda: go(AO, NP, non_evaluation_evidence=None),
    K.NON_EVALUATION_EVIDENCE_WRONG_ACCOUNT: lambda: go(
        AO, NP, non_evaluation_evidence=non_evaluation_evidence("ACC-2")
    ),
    K.BAEC_MISSING: lambda: go(None, CD, baec_record=None),
    K.BAEC_NOT_CONFIRMED: lambda: go(None, CD, baec_record=seller_seeded_record()),
    K.BAEC_WRONG_ACCOUNT: lambda: go(None, CD, baec_record=confirmed_record("ACC-2")),
    K.BAEC_NOT_CURRENT: lambda: go(None, CD, baec_record=confirmed_record(staleness=S.STALE)),
    K.JUDGMENT_MISSING: lambda: go(None, CD, judgment=None),
    K.JUDGMENT_WRONG_BAEC: lambda: go(None, CD, judgment=judgment(baec_id="B-9")),
    K.PLAUSIBILITY_NOT_YES: lambda: go(None, CD, judgment=judgment(plausibility=RA.NO)),
    K.ADDRESSABILITY_NO: lambda: go(None, CD, judgment=judgment(addressability=RA.NO)),
    K.ADDRESSABILITY_NOT_YET: lambda: go(None, CD, judgment=judgment(addressability=RA.NOT_YET)),
    K.EVALUATION_EVIDENCE_MISSING: lambda: go(CD, AO, evaluation_evidence=None),
    K.EVALUATION_EVIDENCE_WRONG_ACCOUNT: lambda: go(CD, AO, evaluation_evidence=evaluation_evidence("ACC-2")),
    K.GROUND_MISSING: lambda: go(None, NP, ground=None),
    K.REASON_MISSING: lambda: go(None, NP, reason=None),
}


@cases(
    [
        (
            "every rejection kind has a phrase and was exercised or is constructible",
            lambda: set(ISOLATED) == set(K) and len(K) == 20,
        )
    ]
)
def test_every_rejection_kind_is_covered(check):
    assert check()


@pytest.mark.parametrize("kind", list(K), ids=lambda k: k.value)
def test_each_rejection_kind_in_isolation(kind):
    result = ISOLATED[kind]()
    assert only(result, kind)
    assert result.rejection_text.startswith(f"Transition to {result.to_state.value} rejected: {kind.value} (")
    assert result.rejection_text.endswith(").")


EXPECTED_REJECTION_TEXT = [
    (
        lambda: go(CD, AO, evaluation_evidence=None),
        "Transition to ACTIVE_OPPORTUNITY rejected: EVALUATION_EVIDENCE_MISSING (no evidence that "
        "the buyer initiated or reopened evaluation was provided).",
    ),
    (
        lambda: go(CD, AO, authorization=state_auth(CD)),
        "Transition to ACTIVE_OPPORTUNITY rejected: AUTHORIZATION_WRONG_TARGET (the authorization "
        "is for a different destination state).",
    ),
    (
        lambda: go(AO, NP, non_evaluation_evidence=None),
        "Transition to NO_PLAUSIBLE_PATH rejected: NON_EVALUATION_EVIDENCE_MISSING (leaving Active "
        "Opportunity requires evidence that the buyer is no longer evaluating).",
    ),
    (
        lambda: go(CD, AO, evaluation_evidence=None, authorization=None),
        "Transition to ACTIVE_OPPORTUNITY rejected: AUTHORIZATION_MISSING (no human authorization "
        "was provided); EVALUATION_EVIDENCE_MISSING (no evidence that the buyer initiated or "
        "reopened evaluation was provided).",
    ),
]


@pytest.mark.parametrize("request_fn,text", EXPECTED_REJECTION_TEXT, ids=["signal-only", "wrong-target", "leaving-active", "two-reasons"])
def test_rejection_text_is_exact_and_deterministic(request_fn, text):
    assert request_fn().rejection_text == text
    assert request_fn() == request_fn()


# --- TransitionResult integrity -----------------------------------------------


def _rejected_ao():
    return go(CD, AO, evaluation_evidence=None)


def _rej(**overrides):
    base = _rejected_ao()
    kwargs = dict(rejections=base.rejections, rejection_text=base.rejection_text)
    kwargs.update(overrides)
    return T(False, "ACC-1", CD, AO, **kwargs)


@cases(
    [
        ("canonical rejected result rebuilds", lambda: _rej() == _rejected_ao()),
        (
            "one ADDRESSABILITY_UNKNOWN on allowed dormancy accepted",
            lambda: T(True, "ACC-1", None, CD, new_account=account(CD), unresolved=(U,)).unresolved == (U,),
        ),
    ]
)
def test_transition_result_accepts_consistent_results(check):
    assert check()


@cases(
    [
        ("allowed without new_account", lambda: T(True, "ACC-1", CD, AO)),
        ("allowed with new_account in wrong state", lambda: T(True, "ACC-1", CD, AO, new_account=account(NP))),
        (
            "allowed with new_account for another account",
            lambda: T(True, "ACC-1", CD, AO, new_account=Account("ACC-2", "Other", AO)),
        ),
        (
            "allowed with rejections",
            lambda: T(
                True,
                "ACC-1",
                CD,
                AO,
                new_account=account(AO),
                rejections=_rejected_ao().rejections,
                rejection_text=_rejected_ao().rejection_text,
            ),
        ),
        ("allowed with from_state == to_state", lambda: T(True, "ACC-1", AO, AO, new_account=account(AO))),
        (
            "allowed No Plausible Path without ground",
            lambda: T(True, "ACC-1", None, NP, new_account=account(NP), reason="r"),
        ),
        (
            "allowed No Plausible Path without reason",
            lambda: T(True, "ACC-1", None, NP, new_account=account(NP), ground=G.OTHER),
        ),
        (
            "allowed Active carrying ground/reason",
            lambda: T(True, "ACC-1", CD, AO, new_account=account(AO), ground=G.OTHER, reason="r"),
        ),
        ("rejected with new_account", lambda: _rej(new_account=account(AO))),
        ("rejected with no rejections", lambda: _rej(rejections=(), rejection_text="x")),
        ("rejected with tampered text", lambda: _rej(rejection_text="Buyer is ready to switch.")),
        (
            "rejected with text for another destination",
            lambda: _rej(
                rejection_text=_rejected_ao().rejection_text.replace("ACTIVE_OPPORTUNITY", "NO_PLAUSIBLE_PATH", 1)
            ),
        ),
        ("rejected with unresolved", lambda: _rej(unresolved=(U,))),
        (
            "rejected preserving ground/reason",
            lambda: T(
                False,
                "ACC-1",
                None,
                NP,
                rejections=(K.AUTHORIZATION_MISSING,),
                rejection_text=go(None, NP, authorization=None).rejection_text,
                ground=G.OTHER,
                reason="r",
            ),
        ),
        (
            "rejections out of canonical order",
            lambda: _rej(rejections=(K.EVALUATION_EVIDENCE_MISSING, K.AUTHORIZATION_MISSING), rejection_text="x"),
        ),
        (
            "SAME_STATE combined with another rejection",
            lambda: T(False, "ACC-1", AO, AO, rejections=(K.SAME_STATE, K.AUTHORIZATION_MISSING), rejection_text="x"),
        ),
        ("SAME_STATE reported when states differ", lambda: _rej(rejections=(K.SAME_STATE,), rejection_text="x")),
        ("raw-string rejection kind", lambda: _rej(rejections=("AUTHORIZATION_MISSING",), rejection_text="x")),
        (
            "unresolved on allowed Active Opportunity",
            lambda: T(True, "ACC-1", CD, AO, new_account=account(AO), unresolved=(U,)),
        ),
        (
            "unresolved on allowed No Plausible Path",
            lambda: T(True, "ACC-1", None, NP, new_account=account(NP), ground=G.OTHER, reason="r", unresolved=(U,)),
        ),
        (
            "duplicate ADDRESSABILITY_UNKNOWN on dormancy",
            lambda: T(True, "ACC-1", None, CD, new_account=account(CD), unresolved=(U, U)),
        ),
        (
            "unresolved on rejected dormancy",
            lambda: T(
                False,
                "ACC-1",
                None,
                CD,
                rejections=(K.AUTHORIZATION_MISSING,),
                rejection_text=go(None, CD, authorization=None).rejection_text,
                unresolved=(U,),
            ),
        ),
    ]
)
def test_transition_result_rejects_contradictory_results(check):
    """A hand-built or tampered result that contradicts itself cannot exist."""
    with pytest.raises(DomainValidationError):
        check()


def test_rejected_result_with_duplicate_rejections_is_refused():
    with pytest.raises(DomainValidationError):
        _rej(rejections=(K.AUTHORIZATION_MISSING, K.AUTHORIZATION_MISSING), rejection_text="x")
