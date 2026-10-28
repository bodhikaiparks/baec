"""Builders for valid baseline domain objects used across the test suite.

These helpers only construct objects, with explicit overrides. They contain
no classification or transition decision logic: every expected outcome is
stated in the tests themselves.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from baec_app.domain.enums import (
    AccountState,
    ArticulationOrigin,
    AuthorizationAction,
    BaecClassification,
    BaecCriterion,
    CriterionFinding,
    ElicitationMode,
    NoPlausiblePathGround,
    ProvenanceCategory,
    ReviewAnswer,
    StalenessStatus,
)
from baec_app.domain.models import (
    Account,
    BaecCandidate,
    BaecRecord,
    CriterionAssessment,
    DormancyJudgment,
    EvaluationEvidence,
    EvidenceExcerpt,
    HumanAuthorization,
    NonEvaluationEvidence,
)
from baec_app.domain.state_machine import (
    transition_to_active_opportunity,
    transition_to_conditionally_dormant,
    transition_to_no_plausible_path,
)

# Fixed timestamp: tests never read the clock.
NOW = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)

# Synthetic statement from the fictional Harbor Surgical Center account.
HARBOR_QUOTE = (
    "If our supplier raises pricing by more than 10% when our agreement "
    "renews, we'd evaluate other options."
)

AO = AccountState.ACTIVE_OPPORTUNITY
CD = AccountState.CONDITIONALLY_DORMANT
NP = AccountState.NO_PLAUSIBLE_PATH


def cases(rows):
    """Turn (label, callable) rows into a parametrize decorator."""
    return pytest.mark.parametrize(
        "check", [fn for _, fn in rows], ids=[label for label, _ in rows]
    )


def excerpt(text=HARBOR_QUOTE, provenance=ProvenanceCategory.BUYER_FACT, source_id="INT-1"):
    return EvidenceExcerpt(text, provenance, source_id)


def external_excerpt():
    return excerpt("12% increase announced", ProvenanceCategory.EXTERNAL_EVIDENCE, "SIG-3")


def assessments(findings=None, criterion_evidence=None, reverse=False):
    """Four assessments, all MET unless overridden per criterion."""
    findings = findings or {}
    evidence = criterion_evidence or excerpt()
    items = []
    for criterion in BaecCriterion:
        finding = findings.get(criterion, CriterionFinding.MET)
        attached = () if finding is CriterionFinding.UNKNOWN else (evidence,)
        items.append(CriterionAssessment(criterion, finding, attached))
    if reverse:
        items.reverse()
    return tuple(items)


def candidate(
    origin=ArticulationOrigin.BUYER_GENERATED,
    findings=None,
    *,
    source_excerpt=None,
    criterion_evidence=None,
    source_interaction_id="INT-1",
    account_id="ACC-1",
    reverse=False,
    **overrides,
):
    return BaecCandidate(
        account_id=account_id,
        source_interaction_id=source_interaction_id,
        source_excerpt=source_excerpt or excerpt(),
        assessments=assessments(findings, criterion_evidence, reverse),
        articulation_origin=origin,
        elicitation_mode=ElicitationMode.CEE_ELICITED,
        **overrides,
    )


def authorization(action, subject_id, target_state=None):
    return HumanAuthorization("reviewer-1", NOW, action, subject_id, target_state)


def confirm_auth(baec_id="B-1"):
    return authorization(AuthorizationAction.CONFIRM_BAEC, baec_id)


def state_auth(target_state, account_id="ACC-1"):
    return authorization(AuthorizationAction.CHANGE_ACCOUNT_STATE, account_id, target_state)


def record(cand, classification, reason=None, *, baec_id="B-1", captured_at=NOW, **overrides):
    return BaecRecord(baec_id, captured_at, cand, classification, reason, **overrides)


def confirmed_record(account_id="ACC-1", baec_id="B-1", staleness=StalenessStatus.CURRENT):
    return record(
        candidate(account_id=account_id),
        BaecClassification.CONFIRMED_BAEC,
        baec_id=baec_id,
        confirmation=confirm_auth(baec_id),
        staleness_status=staleness,
    )


def seller_seeded_record():
    return record(
        candidate(ArticulationOrigin.SELLER_SEEDED),
        BaecClassification.NOT_BAEC,
        "seller supplied the condition",
    )


def judgment(plausibility=ReviewAnswer.YES, addressability=ReviewAnswer.YES, baec_id="B-1"):
    return DormancyJudgment(
        baec_id,
        plausibility,
        addressability,
        authorization(AuthorizationAction.RECORD_DORMANCY_JUDGMENT, baec_id),
    )


def evaluation_evidence(account_id="ACC-1"):
    return EvaluationEvidence(
        account_id, excerpt("We have opened a formal supplier review.", source_id="INT-2"), NOW
    )


def non_evaluation_evidence(account_id="ACC-1"):
    return NonEvaluationEvidence(
        account_id, excerpt("We closed the review and are staying put.", source_id="INT-3"), NOW
    )


def account(state=None):
    return Account("ACC-1", "Harbor Surgical Center", state)


def request_transition(from_state, to_state, **overrides):
    """Request a transition with every prerequisite valid, then apply overrides.

    Supplies non-evaluation evidence when the account starts in Active
    Opportunity, because a valid request from that state needs it.
    """
    leaving_active = non_evaluation_evidence() if from_state is AO else None
    if to_state is CD:
        kwargs = dict(
            baec_record=confirmed_record(),
            judgment=judgment(),
            authorization=state_auth(CD),
            non_evaluation_evidence=leaving_active,
        )
        kwargs.update(overrides)
        return transition_to_conditionally_dormant(account(from_state), **kwargs)
    if to_state is AO:
        kwargs = dict(evaluation_evidence=evaluation_evidence(), authorization=state_auth(AO))
        kwargs.update(overrides)
        return transition_to_active_opportunity(account(from_state), **kwargs)
    kwargs = dict(
        ground=NoPlausiblePathGround.NO_PLAUSIBLE_BAEC,
        reason="CEE produced no foreseeable condition.",
        authorization=state_auth(NP),
        non_evaluation_evidence=leaving_active,
    )
    kwargs.update(overrides)
    return transition_to_no_plausible_path(account(from_state), **kwargs)
