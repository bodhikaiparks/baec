"""Account-state transitions (RC-20 to RC-27, RC-31).

The machine is stateless and deterministic. It knows nothing about history
or storage: the caller passes in everything it judges. It never modifies the
Account it is given; an allowed transition returns a new Account.

How outcomes are reported:

* A transition refused by a rule returns a rejected TransitionResult. A
  refusal is a normal, recordable outcome.
* A wrong object type raises DomainValidationError. That is a programming
  error, not a business outcome.
* A missing prerequisite (None) is a rule refusal, not a type error.

There is no function that accepts a signal. Active Opportunity requires
EvaluationEvidence, which itself refuses external evidence (RC-27).

HumanAuthorization is a domain requirement here, not proof that a human
acted. Later application, service, and MCP server code must establish that.

Not checked here, by design: time ordering of evidence (needs account
history), and whether a NoPlausiblePathGround agrees with any other judgment
(it is a human managerial declaration).
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from .enums import (
    AccountState,
    AuthorizationAction,
    BaecClassification,
    NoPlausiblePathGround,
    ReviewAnswer,
    StalenessStatus,
    TransitionRejectionKind,
    TransitionUnresolvedKind,
)
from .models import (
    Account,
    BaecRecord,
    DomainValidationError,
    DormancyJudgment,
    EvaluationEvidence,
    HumanAuthorization,
    NonEvaluationEvidence,
)

_K = TransitionRejectionKind

_REJECTION_PHRASES = {
    _K.SAME_STATE: "the account is already in the requested state",
    _K.AUTHORIZATION_MISSING: "no human authorization was provided",
    _K.AUTHORIZATION_WRONG_ACTION: (
        "the authorization is not for changing account state"
    ),
    _K.AUTHORIZATION_WRONG_SUBJECT: "the authorization is for a different account",
    _K.AUTHORIZATION_WRONG_TARGET: (
        "the authorization is for a different destination state"
    ),
    _K.NON_EVALUATION_EVIDENCE_MISSING: (
        "leaving Active Opportunity requires evidence that the buyer is no "
        "longer evaluating"
    ),
    _K.NON_EVALUATION_EVIDENCE_WRONG_ACCOUNT: (
        "the non-evaluation evidence is for a different account"
    ),
    _K.BAEC_MISSING: "no BAEC record was provided",
    _K.BAEC_NOT_CONFIRMED: "the BAEC record is not a confirmed BAEC",
    _K.BAEC_WRONG_ACCOUNT: "the BAEC record belongs to a different account",
    _K.BAEC_NOT_CURRENT: (
        "the BAEC is not CURRENT and must be reassessed first"
    ),
    _K.JUDGMENT_MISSING: "no dormancy judgment was provided",
    _K.JUDGMENT_WRONG_BAEC: "the dormancy judgment is for a different BAEC",
    _K.PLAUSIBILITY_NOT_YES: "a human has not judged the condition plausible",
    _K.ADDRESSABILITY_NO: (
        "a human judged the condition clearly seller-unaddressable"
    ),
    _K.ADDRESSABILITY_NOT_YET: "addressability has not yet been judged by a human",
    _K.EVALUATION_EVIDENCE_MISSING: (
        "no evidence that the buyer initiated or reopened evaluation was provided"
    ),
    _K.EVALUATION_EVIDENCE_WRONG_ACCOUNT: (
        "the evaluation evidence is for a different account"
    ),
    _K.GROUND_MISSING: "no structured ground for No Plausible Path was provided",
    _K.REASON_MISSING: "no human-written reason was provided",
}

_REJECTION_ORDER = {kind: index for index, kind in enumerate(_K)}


def _rejection_text(
    to_state: AccountState, rejections: tuple[TransitionRejectionKind, ...]
) -> str:
    parts = "; ".join(f"{k.value} ({_REJECTION_PHRASES[k]})" for k in rejections)
    return f"Transition to {to_state.value} rejected: {parts}."


def _is_blank(value: object) -> bool:
    return not isinstance(value, str) or not value.strip()


@dataclass(frozen=True)
class TransitionResult:
    """Outcome of a requested transition. Validates its own consistency.

    allowed      new_account present, same account_id, state == to_state;
                 no rejections; rejection_text None; ground and reason
                 present only when to_state is NO_PLAUSIBLE_PATH; unresolved
                 items only when to_state is CONDITIONALLY_DORMANT, with no
                 repeats.
    rejected     new_account None; one or more rejections in canonical
                 order; rejection_text exactly the canonical text; no
                 unresolved items, ground, or reason.
    """

    allowed: bool
    account_id: str
    from_state: AccountState | None
    to_state: AccountState
    new_account: Account | None = None
    rejections: tuple[TransitionRejectionKind, ...] = ()
    rejection_text: str | None = None
    unresolved: tuple[TransitionUnresolvedKind, ...] = ()
    ground: NoPlausiblePathGround | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        def fail(message: str) -> None:
            raise DomainValidationError(f"TransitionResult: {message}")

        if not isinstance(self.allowed, bool):
            fail("allowed must be a bool")
        if _is_blank(self.account_id):
            fail("account_id must be a non-empty string")
        if self.from_state is not None and not isinstance(
            self.from_state, AccountState
        ):
            fail("from_state must be an AccountState or None")
        if not isinstance(self.to_state, AccountState):
            fail("to_state must be an AccountState")
        if not isinstance(self.rejections, tuple) or not all(
            isinstance(k, TransitionRejectionKind) for k in self.rejections
        ):
            fail("rejections must be a tuple of TransitionRejectionKind")
        if not isinstance(self.unresolved, tuple) or not all(
            isinstance(k, TransitionUnresolvedKind) for k in self.unresolved
        ):
            fail("unresolved must be a tuple of TransitionUnresolvedKind")
        if len(set(self.unresolved)) != len(self.unresolved):
            fail("unresolved items must not repeat")
        if self.unresolved and self.to_state is not AccountState.CONDITIONALLY_DORMANT:
            fail("unresolved items apply only to CONDITIONALLY_DORMANT")

        if self.allowed:
            if self.from_state is self.to_state:
                fail("an allowed transition must change the state")
            if not isinstance(self.new_account, Account):
                fail("an allowed transition requires new_account")
            if self.new_account.account_id != self.account_id:
                fail("new_account is a different account")
            if self.new_account.state is not self.to_state:
                fail("new_account.state must equal to_state")
            if self.rejections or self.rejection_text is not None:
                fail("an allowed transition carries no rejections")
            if self.to_state is AccountState.NO_PLAUSIBLE_PATH:
                if not isinstance(self.ground, NoPlausiblePathGround):
                    fail("NO_PLAUSIBLE_PATH requires a ground")
                if _is_blank(self.reason):
                    fail("NO_PLAUSIBLE_PATH requires a non-empty reason")
            elif self.ground is not None or self.reason is not None:
                fail("ground and reason apply only to NO_PLAUSIBLE_PATH")
            return

        if self.new_account is not None:
            fail("a rejected transition must not carry new_account")
        if not self.rejections:
            fail("a rejected transition requires at least one rejection")
        if len(set(self.rejections)) != len(self.rejections):
            fail("rejections must not repeat")
        if list(self.rejections) != sorted(
            self.rejections, key=_REJECTION_ORDER.__getitem__
        ):
            fail("rejections must be in canonical order")
        same_state = _K.SAME_STATE in self.rejections
        if same_state != (self.from_state is self.to_state):
            fail("SAME_STATE must be reported exactly when from_state == to_state")
        if same_state and len(self.rejections) != 1:
            fail("SAME_STATE is terminal and must be the only rejection")
        if self.rejection_text != _rejection_text(self.to_state, self.rejections):
            fail("rejection_text must equal the canonical generated text")
        if self.unresolved:
            fail("unresolved may appear only on an allowed transition")
        if self.ground is not None or self.reason is not None:
            fail("a rejected transition does not preserve ground or reason")


# --- internal helpers -------------------------------------------------------


def _require_type(value: object, expected: type, name: str) -> None:
    if not isinstance(value, expected):
        raise DomainValidationError(
            f"{name} must be {expected.__name__}, got {type(value).__name__}"
        )


def _require_optional_type(value: object, expected: type, name: str) -> None:
    if value is not None:
        _require_type(value, expected, name)


def _rejected(
    account: Account, to_state: AccountState, kinds: list[TransitionRejectionKind]
) -> TransitionResult:
    ordered = tuple(sorted(set(kinds), key=_REJECTION_ORDER.__getitem__))
    return TransitionResult(
        allowed=False,
        account_id=account.account_id,
        from_state=account.state,
        to_state=to_state,
        rejections=ordered,
        rejection_text=_rejection_text(to_state, ordered),
    )


def _authorization_rejections(
    authorization: HumanAuthorization | None,
    account: Account,
    to_state: AccountState,
) -> list[TransitionRejectionKind]:
    if authorization is None:
        return [_K.AUTHORIZATION_MISSING]
    found = []
    if authorization.action is not AuthorizationAction.CHANGE_ACCOUNT_STATE:
        # Other actions carry no target_state, so a target check would say
        # nothing further.
        found.append(_K.AUTHORIZATION_WRONG_ACTION)
    elif authorization.target_state is not to_state:
        found.append(_K.AUTHORIZATION_WRONG_TARGET)
    if authorization.subject_id != account.account_id:
        found.append(_K.AUTHORIZATION_WRONG_SUBJECT)
    return found


def _non_evaluation_rejections(
    evidence: NonEvaluationEvidence | None, account: Account
) -> list[TransitionRejectionKind]:
    if evidence is None:
        if account.state is AccountState.ACTIVE_OPPORTUNITY:
            return [_K.NON_EVALUATION_EVIDENCE_MISSING]
        return []
    if evidence.account_id != account.account_id:
        return [_K.NON_EVALUATION_EVIDENCE_WRONG_ACCOUNT]
    return []


# --- public transitions -----------------------------------------------------


def transition_to_conditionally_dormant(
    account: Account,
    *,
    baec_record: BaecRecord | None,
    judgment: DormancyJudgment | None,
    authorization: HumanAuthorization | None,
    non_evaluation_evidence: NonEvaluationEvidence | None = None,
) -> TransitionResult:
    """Move an account to CONDITIONALLY_DORMANT (RC-22).

    Requires a confirmed, CURRENT BAEC for this account; a human judgment on
    that BAEC with plausibility YES and addressability YES or UNKNOWN; and an
    authorization for this destination. Leaving ACTIVE_OPPORTUNITY also
    requires non-evaluation evidence. Addressability UNKNOWN passes but is
    returned in unresolved.
    """
    to_state = AccountState.CONDITIONALLY_DORMANT
    _require_type(account, Account, "account")
    _require_optional_type(baec_record, BaecRecord, "baec_record")
    _require_optional_type(judgment, DormancyJudgment, "judgment")
    _require_optional_type(authorization, HumanAuthorization, "authorization")
    _require_optional_type(
        non_evaluation_evidence, NonEvaluationEvidence, "non_evaluation_evidence"
    )
    if account.state is to_state:
        return _rejected(account, to_state, [_K.SAME_STATE])

    found = _authorization_rejections(authorization, account, to_state)
    found += _non_evaluation_rejections(non_evaluation_evidence, account)

    if baec_record is None:
        found.append(_K.BAEC_MISSING)
    else:
        if baec_record.classification is not BaecClassification.CONFIRMED_BAEC:
            found.append(_K.BAEC_NOT_CONFIRMED)
        elif baec_record.staleness_status is not StalenessStatus.CURRENT:
            found.append(_K.BAEC_NOT_CURRENT)
        if baec_record.candidate.account_id != account.account_id:
            found.append(_K.BAEC_WRONG_ACCOUNT)

    unresolved: tuple[TransitionUnresolvedKind, ...] = ()
    if judgment is None:
        found.append(_K.JUDGMENT_MISSING)
    else:
        if baec_record is not None and judgment.baec_id != baec_record.baec_id:
            found.append(_K.JUDGMENT_WRONG_BAEC)
        if judgment.plausibility is not ReviewAnswer.YES:
            found.append(_K.PLAUSIBILITY_NOT_YES)
        if judgment.addressability is ReviewAnswer.NO:
            found.append(_K.ADDRESSABILITY_NO)
        elif judgment.addressability is ReviewAnswer.NOT_YET:
            found.append(_K.ADDRESSABILITY_NOT_YET)
        elif judgment.addressability is ReviewAnswer.UNKNOWN:
            unresolved = (TransitionUnresolvedKind.ADDRESSABILITY_UNKNOWN,)

    if found:
        return _rejected(account, to_state, found)
    return TransitionResult(
        allowed=True,
        account_id=account.account_id,
        from_state=account.state,
        to_state=to_state,
        new_account=replace(account, state=to_state),
        unresolved=unresolved,
    )


def transition_to_active_opportunity(
    account: Account,
    *,
    evaluation_evidence: EvaluationEvidence | None,
    authorization: HumanAuthorization | None,
) -> TransitionResult:
    """Move an account to ACTIVE_OPPORTUNITY (RC-27).

    Requires evidence that the buyer actually initiated or reopened
    evaluation, plus an authorization for this destination. A signal cannot
    be supplied: there is no parameter for one.
    """
    to_state = AccountState.ACTIVE_OPPORTUNITY
    _require_type(account, Account, "account")
    _require_optional_type(
        evaluation_evidence, EvaluationEvidence, "evaluation_evidence"
    )
    _require_optional_type(authorization, HumanAuthorization, "authorization")
    if account.state is to_state:
        return _rejected(account, to_state, [_K.SAME_STATE])

    found = _authorization_rejections(authorization, account, to_state)
    if evaluation_evidence is None:
        found.append(_K.EVALUATION_EVIDENCE_MISSING)
    elif evaluation_evidence.account_id != account.account_id:
        found.append(_K.EVALUATION_EVIDENCE_WRONG_ACCOUNT)

    if found:
        return _rejected(account, to_state, found)
    return TransitionResult(
        allowed=True,
        account_id=account.account_id,
        from_state=account.state,
        to_state=to_state,
        new_account=replace(account, state=to_state),
    )


def transition_to_no_plausible_path(
    account: Account,
    *,
    ground: NoPlausiblePathGround | None,
    reason: str | None,
    authorization: HumanAuthorization | None,
    non_evaluation_evidence: NonEvaluationEvidence | None = None,
) -> TransitionResult:
    """Move an account to NO_PLAUSIBLE_PATH (RC-20, RC-23).

    Requires a structured ground, a non-empty human-written reason, and an
    authorization for this destination. Leaving ACTIVE_OPPORTUNITY also
    requires non-evaluation evidence. The ground is a human declaration and
    is not cross-checked against other judgments.
    """
    to_state = AccountState.NO_PLAUSIBLE_PATH
    _require_type(account, Account, "account")
    _require_optional_type(ground, NoPlausiblePathGround, "ground")
    _require_optional_type(reason, str, "reason")
    _require_optional_type(authorization, HumanAuthorization, "authorization")
    _require_optional_type(
        non_evaluation_evidence, NonEvaluationEvidence, "non_evaluation_evidence"
    )
    if account.state is to_state:
        return _rejected(account, to_state, [_K.SAME_STATE])

    found = _authorization_rejections(authorization, account, to_state)
    found += _non_evaluation_rejections(non_evaluation_evidence, account)
    if ground is None:
        found.append(_K.GROUND_MISSING)
    if _is_blank(reason):
        found.append(_K.REASON_MISSING)

    if found:
        return _rejected(account, to_state, found)
    return TransitionResult(
        allowed=True,
        account_id=account.account_id,
        from_state=account.state,
        to_state=to_state,
        new_account=replace(account, state=to_state),
        ground=ground,
        reason=reason,
    )
