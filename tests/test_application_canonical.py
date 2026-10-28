"""Phase 4A: context, canonical serialization, and approval-request content."""

import collections
import dataclasses
import hashlib
import subprocess
import sys
import unicodedata
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum, IntEnum
from pathlib import Path

import pytest

from baec_app.application import canonical
from baec_app.application.canonical import CanonicalizationError, canonical_json, digest_request
from baec_app.application.context import Actor, InteractionSession, SystemClock, UuidIdFactory
from baec_app.application.errors import ApplicationValidationError
from baec_app.application.proposals import ProposalOrigin
from baec_app.application import requests as requests_module
from baec_app.application.requests import (
    ApprovalRequest,
    ClassificationPreview,
    RequestKind,
    build_request,
)
from baec_app.domain.baec_rules import classify_candidate
from baec_app.domain.enums import (
    AccountState,
    ArticulationOrigin,
    AuthorizationAction,
    NoPlausiblePathGround,
    ReviewAnswer,
)
from baec_app.domain.models import EvaluationEvidence, EvidenceExcerpt, HumanAuthorization
from baec_app.domain.state_machine import transition_to_active_opportunity
from tests.application_builders import PAYLOAD_BUILDERS, active_payload, confirm_payload, request_for
from tests.builders import AO, NOW, account, candidate, evaluation_evidence, excerpt

K = RequestKind
REPO_ROOT = Path(__file__).resolve().parents[1]

# --- golden vectors -------------------------------------------------------------
# Hand-checked: the JSON below is the exact canonical text, and its SHA-256 was
# confirmed independently with `shasum -a 256`.

GOLDEN_ACTIVE_JSON = (
    '{"action":"CHANGE_ACCOUNT_STATE","format":"baec-approval-request/v1","kind":"MOVE_TO_ACTIVE_OPPORTUNITY",'
    '"origin":"HUMAN_DRAFT","payload":{"$type":"MoveToActivePayload","account_id":"ACC-1","evaluation_evidence":'
    '{"$type":"EvaluationEvidence","account_id":"ACC-1","evidence":{"$type":"EvidenceExcerpt","provenance":'
    '"BUYER_FACT","source_id":"INT-2","text":"We have opened a formal supplier review."},"observed_at":'
    '{"$datetime":"2026-01-15T12:00:00.000000+00:00"}}},"subject_id":"ACC-1","target_state":"ACTIVE_OPPORTUNITY"}'
)
GOLDEN_ACTIVE_DIGEST = "78a204e67272e0d4e889df033ffa93c796cd62da6292d61477b14f251b05e294"
GOLDEN_CONFIRM_DIGEST = "716d3836ce0e8fb46ada93267e9218d5058350ef035f22560a9dc7be5cb17a60"


def envelope_json(request):
    return canonical._dumps(
        canonical.request_envelope(
            kind=request.kind,
            action=request.action,
            subject_id=request.subject_id,
            target_state=request.target_state,
            origin=request.origin,
            payload=request.payload,
        )
    )


def test_golden_active_opportunity_request():
    request = request_for(K.MOVE_TO_ACTIVE_OPPORTUNITY)
    assert envelope_json(request) == GOLDEN_ACTIVE_JSON
    assert hashlib.sha256(GOLDEN_ACTIVE_JSON.encode("utf-8")).hexdigest() == GOLDEN_ACTIVE_DIGEST
    assert request.digest == GOLDEN_ACTIVE_DIGEST


def test_golden_confirmation_request_with_decimal_threshold():
    request = request_for(K.CONFIRM_BAEC)
    text = envelope_json(request)
    assert '"numeric_value":{"$decimal":"10"}' in text
    assert '"captured_at":{"$datetime":"2026-01-15T12:00:00.000000+00:00"}' in text
    assert request.digest == GOLDEN_CONFIRM_DIGEST


def test_digest_is_identical_across_processes_and_hash_seeds():
    script = (
        "from tests.application_builders import request_for;"
        "from baec_app.application.requests import RequestKind as K;"
        "print(request_for(K.MOVE_TO_ACTIVE_OPPORTUNITY).digest, request_for(K.CONFIRM_BAEC).digest)"
    )
    for seed in ("0", "1", "12345", "random"):
        out = subprocess.run(
            [sys.executable, "-c", script],
            cwd=REPO_ROOT,
            env={"PYTHONHASHSEED": seed, "PATH": "/usr/bin:/bin"},
            capture_output=True,
            text=True,
            check=True,
        ).stdout.split()
        assert out == [GOLDEN_ACTIVE_DIGEST, GOLDEN_CONFIRM_DIGEST], seed


# --- explicit field tables ------------------------------------------------------


@pytest.mark.parametrize("cls", list(canonical._DOMAIN_FIELDS), ids=lambda c: c.__name__)
def test_domain_field_table_matches_the_ordered_dataclass_fields(cls):
    assert canonical._DOMAIN_FIELDS[cls] == tuple(f.name for f in dataclasses.fields(cls))


PAYLOAD_TYPES = [type(builder()) for builder in PAYLOAD_BUILDERS.values()]


@pytest.mark.parametrize("cls", PAYLOAD_TYPES, ids=lambda c: c.__name__)
def test_payload_field_table_matches_the_ordered_dataclass_fields(cls):
    assert canonical._payload_fields()[cls] == tuple(f.name for f in dataclasses.fields(cls))


def test_field_tables_cover_exactly_the_approved_types():
    from baec_app.domain import models

    assert set(canonical._DOMAIN_FIELDS) == {
        models.EvidenceExcerpt,
        models.CriterionAssessment,
        models.StringencyExpression,
        models.BaecCandidate,
        models.EvaluationEvidence,
        models.NonEvaluationEvidence,
    }
    assert set(canonical._payload_fields()) == set(PAYLOAD_TYPES)
    assert canonical._payload_fields() is canonical._PAYLOAD_FIELDS
    assert all(getattr(requests_module, cls.__name__) is cls for cls in PAYLOAD_TYPES)


# --- exact type identity ------------------------------------------------------------


@pytest.mark.parametrize("kind", list(K), ids=lambda k: k.value)
def test_exact_payload_types_are_encoded(kind):
    payload = PAYLOAD_BUILDERS[kind]()
    assert canonical_json(payload).startswith('{"$type":"%s"' % type(payload).__name__)


def _envelope_digest(payload, kind=K.MOVE_TO_ACTIVE_OPPORTUNITY):
    return digest_request(
        kind=kind,
        action=AuthorizationAction.CHANGE_ACCOUNT_STATE,
        subject_id="ACC-1",
        target_state=AO,
        origin=ProposalOrigin.HUMAN_DRAFT,
        payload=payload,
    )


def test_a_payload_subclass_is_refused():
    @dataclasses.dataclass(frozen=True)
    class Subclass(requests_module.MoveToActivePayload):
        pass

    sub = Subclass("ACC-1", evaluation_evidence())
    with pytest.raises(CanonicalizationError):
        canonical_json(sub)
    with pytest.raises(CanonicalizationError):
        _envelope_digest(sub)


def test_a_spoofed_class_with_the_same_name_module_and_fields_is_refused():
    @dataclasses.dataclass(frozen=True)
    class MoveToActivePayload:  # look-alike, not the real class
        account_id: str
        evaluation_evidence: object

    MoveToActivePayload.__module__ = requests_module.__name__
    MoveToActivePayload.__qualname__ = "MoveToActivePayload"
    spoof = MoveToActivePayload("ACC-1", evaluation_evidence())
    assert type(spoof).__module__ == requests_module.MoveToActivePayload.__module__
    assert type(spoof).__qualname__ == requests_module.MoveToActivePayload.__qualname__
    assert type(spoof) is not requests_module.MoveToActivePayload
    with pytest.raises(CanonicalizationError):
        canonical_json(spoof)
    with pytest.raises(CanonicalizationError):
        _envelope_digest(spoof)


def test_a_spoofed_domain_class_with_the_same_name_and_module_is_refused():
    @dataclasses.dataclass(frozen=True)
    class EvidenceExcerpt:
        text: str
        provenance: object
        source_id: str

    EvidenceExcerpt.__module__ = "baec_app.domain.models"
    with pytest.raises(CanonicalizationError):
        canonical_json(EvidenceExcerpt("x", excerpt().provenance, "INT-1"))


def test_canonicalization_error_is_an_application_validation_error():
    assert issubclass(CanonicalizationError, ApplicationValidationError)


# --- value rules ----------------------------------------------------------------


def test_json_is_compact_sorted_and_not_ascii_escaped():
    text = canonical_json(excerpt("Café review", source_id="INT-1"))
    assert text == '{"$type":"EvidenceExcerpt","provenance":"BUYER_FACT","source_id":"INT-1","text":"Café review"}'


def test_decimal_text_is_preserved_so_10_and_10_0_differ():
    a = confirm_payload(candidate=candidate(stringency=_stringency(Decimal("10"))))
    b = confirm_payload(candidate=candidate(stringency=_stringency(Decimal("10.0"))))
    assert canonical_json(a) != canonical_json(b)
    assert request_for(K.CONFIRM_BAEC, payload=a).digest != request_for(K.CONFIRM_BAEC, payload=b).digest


def _stringency(value):
    from baec_app.domain.enums import ThresholdComparator
    from baec_app.domain.models import StringencyExpression

    return StringencyExpression("more than 10%", ThresholdComparator.GREATER_THAN, value)


def test_the_same_instant_in_different_time_zones_has_the_same_digest():
    plus_five = NOW.astimezone(timezone(timedelta(hours=5)))
    assert plus_five == NOW and plus_five.utcoffset() != NOW.utcoffset()
    a = request_for(K.CONFIRM_BAEC, payload=confirm_payload(captured_at=NOW))
    b = request_for(K.CONFIRM_BAEC, payload=confirm_payload(captured_at=plus_five))
    assert a.digest == b.digest


def test_different_instants_have_different_digests():
    later = NOW + timedelta(microseconds=1)
    assert request_for(K.CONFIRM_BAEC, payload=confirm_payload(captured_at=later)).digest != request_for(K.CONFIRM_BAEC).digest


def test_text_is_not_unicode_normalized():
    nfc = unicodedata.normalize("NFC", "Café")
    nfd = unicodedata.normalize("NFD", "Café")
    assert nfc != nfd
    assert canonical_json(nfc) != canonical_json(nfd)


class _Colour(Enum):
    RED = 1


class _Level(IntEnum):
    LOW = 1


_Pair = collections.namedtuple("_Pair", "a b")


class _ExcerptSubclass(EvidenceExcerpt):
    pass


class _Text(str):
    pass


REFUSED = {
    "float": 1.5,
    "bool": True,
    "list": ["a"],
    "dict": {"approved": True},
    "set": {"a"},
    "bytes": b"a",
    "date": date(2026, 1, 15),
    "naive datetime": datetime(2026, 1, 15, 12, 0),
    "non-finite decimal": Decimal("NaN"),
    "infinite decimal": Decimal("Infinity"),
    "int-valued enum": _Colour.RED,
    "IntEnum": _Level.LOW,
    "namedtuple": _Pair(1, 2),
    "str subclass": _Text("x"),
    "domain subclass": _ExcerptSubclass("x", excerpt().provenance, "INT-1"),
    "HumanAuthorization": HumanAuthorization("r", NOW, AuthorizationAction.CONFIRM_BAEC, "B-1"),
    "plain object": object(),
    "lone surrogate": "\ud800",
    "tuple containing a float": ("a", 1.5),
}


@pytest.mark.parametrize("value", REFUSED.values(), ids=REFUSED.keys())
def test_unsupported_values_are_refused(value):
    with pytest.raises(CanonicalizationError):
        canonical_json(value)


def test_envelope_refuses_a_non_payload_object():
    with pytest.raises(CanonicalizationError):
        digest_request(
            kind=K.MOVE_TO_ACTIVE_OPPORTUNITY,
            action=AuthorizationAction.CHANGE_ACCOUNT_STATE,
            subject_id="ACC-1",
            target_state=AO,
            origin=ProposalOrigin.HUMAN_DRAFT,
            payload=evaluation_evidence(),  # a domain object, not a request payload
        )


# --- digest sensitivity -----------------------------------------------------------

PAYLOAD_CHANGES = {
    K.CONFIRM_BAEC: {
        "baec_id": "B-2",
        "candidate": candidate(ArticulationOrigin.SELLER_SEEDED),
        "captured_at": NOW + timedelta(seconds=1),
    },
    K.RECORD_DORMANCY_JUDGMENT: {
        "baec_id": "B-2",
        "plausibility": ReviewAnswer.NO,
        "addressability": ReviewAnswer.YES,
        "notes": None,
    },
    K.MOVE_TO_CONDITIONALLY_DORMANT: {
        "account_id": "ACC-2",
        "baec_id": "B-2",
        "judgment_id": 2,
        "non_evaluation_evidence": None,
    },
    K.MOVE_TO_ACTIVE_OPPORTUNITY: {
        "account_id": "ACC-2",
        "evaluation_evidence": EvaluationEvidence("ACC-1", excerpt("formal supplier review", source_id="INT-2"), NOW),
    },
    K.MOVE_TO_NO_PLAUSIBLE_PATH: {
        "account_id": "ACC-2",
        "ground": NoPlausiblePathGround.OTHER,
        "reason": "Different reason.",
        "non_evaluation_evidence": None,
        "basis_interaction_id": None,
    },
}
PAYLOAD_CASES = [(kind, field, value) for kind, changes in PAYLOAD_CHANGES.items() for field, value in changes.items()]


@pytest.mark.parametrize("kind,field,value", PAYLOAD_CASES, ids=[f"{k.value}-{f}" for k, f, _ in PAYLOAD_CASES])
def test_changing_any_payload_field_changes_the_digest(kind, field, value):
    base = PAYLOAD_BUILDERS[kind]()
    assert getattr(base, field) != value
    assert request_for(kind, payload=replace(base, **{field: value})).digest != request_for(kind).digest


def test_every_payload_field_has_a_sensitivity_case():
    for kind, builder in PAYLOAD_BUILDERS.items():
        assert set(PAYLOAD_CHANGES[kind]) == {f.name for f in dataclasses.fields(builder())}, kind


@pytest.mark.parametrize("kind", list(K), ids=lambda k: k.value)
def test_origin_is_covered_by_the_digest(kind):
    human = request_for(kind, origin=ProposalOrigin.HUMAN_DRAFT)
    deterministic = request_for(kind, origin=ProposalOrigin.DETERMINISTIC)
    assert human.digest != deterministic.digest


def test_envelope_fields_are_each_covered_by_the_digest():
    base = dict(
        kind=K.MOVE_TO_ACTIVE_OPPORTUNITY,
        action=AuthorizationAction.CHANGE_ACCOUNT_STATE,
        subject_id="ACC-1",
        target_state=AO,
        origin=ProposalOrigin.HUMAN_DRAFT,
        payload=active_payload(),
    )
    reference = digest_request(**base)
    changes = {
        "kind": K.MOVE_TO_NO_PLAUSIBLE_PATH,
        "action": AuthorizationAction.CONFIRM_BAEC,
        "subject_id": "ACC-2",
        "target_state": AccountState.NO_PLAUSIBLE_PATH,
        "origin": ProposalOrigin.DETERMINISTIC,
        "payload": active_payload(account_id="ACC-2"),
    }
    assert set(changes) == set(base)
    for field, value in changes.items():
        assert digest_request(**{**base, field: value}) != reference, field


def test_identifiers_times_and_previews_are_not_in_the_digest():
    a = request_for(K.MOVE_TO_ACTIVE_OPPORTUNITY)
    preview = transition_to_active_opportunity(account(), evaluation_evidence=evaluation_evidence(), authorization=None)
    b = request_for(
        K.MOVE_TO_ACTIVE_OPPORTUNITY,
        request_id="REQ-2",
        session_id="S-2",
        opened_at=NOW + timedelta(days=1),
        preview=preview,
    )
    assert a.digest == b.digest


# --- request invariants -----------------------------------------------------------


@pytest.mark.parametrize("kind", list(K), ids=lambda k: k.value)
def test_build_request_derives_action_subject_and_target(kind):
    request = request_for(kind)
    expected_action = {
        K.CONFIRM_BAEC: AuthorizationAction.CONFIRM_BAEC,
        K.RECORD_DORMANCY_JUDGMENT: AuthorizationAction.RECORD_DORMANCY_JUDGMENT,
    }.get(kind, AuthorizationAction.CHANGE_ACCOUNT_STATE)
    expected_target = {
        K.MOVE_TO_CONDITIONALLY_DORMANT: AccountState.CONDITIONALLY_DORMANT,
        K.MOVE_TO_ACTIVE_OPPORTUNITY: AccountState.ACTIVE_OPPORTUNITY,
        K.MOVE_TO_NO_PLAUSIBLE_PATH: AccountState.NO_PLAUSIBLE_PATH,
    }.get(kind)
    expected_subject = request.payload.baec_id if expected_target is None else request.payload.account_id
    assert (request.action, request.subject_id, request.target_state) == (expected_action, expected_subject, expected_target)


def _fields(request):
    return {f.name: getattr(request, f.name) for f in dataclasses.fields(ApprovalRequest)}


INCONSISTENT = {
    "wrong digest": {"digest": "0" * 64},
    "wrong action": {"action": AuthorizationAction.CONFIRM_BAEC},
    "wrong subject": {"subject_id": "ACC-2"},
    "wrong target": {"target_state": AccountState.NO_PLAUSIBLE_PATH},
    "no target": {"target_state": None},
    "payload of another kind": {"payload": confirm_payload()},
    "origin as text": {"origin": "HUMAN_DRAFT"},
    "kind as text": {"kind": "MOVE_TO_ACTIVE_OPPORTUNITY"},
    "naive opened_at": {"opened_at": datetime(2026, 1, 15)},
    "blank request id": {"request_id": " "},
    "blank session id": {"session_id": ""},
}


@pytest.mark.parametrize("change", INCONSISTENT.values(), ids=INCONSISTENT.keys())
def test_inconsistent_requests_cannot_be_constructed(change):
    values = {**_fields(request_for(K.MOVE_TO_ACTIVE_OPPORTUNITY)), **change}
    with pytest.raises((ApplicationValidationError, CanonicalizationError)):
        ApprovalRequest(**values)


def test_changing_request_content_without_recomputing_the_digest_is_refused():
    original = request_for(K.MOVE_TO_ACTIVE_OPPORTUNITY)
    with pytest.raises(ApplicationValidationError, match="digest"):
        replace(original, payload=active_payload(account_id="ACC-2"), subject_id="ACC-2")


PREVIEW_MISMATCHES = {
    "confirmation preview on a transition": (K.MOVE_TO_ACTIVE_OPPORTUNITY, "classification"),
    "transition preview on a confirmation": (K.CONFIRM_BAEC, "transition"),
    "any preview on a judgment": (K.RECORD_DORMANCY_JUDGMENT, "transition"),
    "transition preview for another target": (K.MOVE_TO_NO_PLAUSIBLE_PATH, "transition"),
    "confirmation preview of another candidate": (K.CONFIRM_BAEC, "other-candidate"),
}


@pytest.mark.parametrize("case", PREVIEW_MISMATCHES.values(), ids=PREVIEW_MISMATCHES.keys())
def test_preview_must_match_the_request(case):
    kind, preview_kind = case
    if preview_kind == "classification":
        preview = ClassificationPreview(candidate(), classify_candidate(candidate()), True)
    elif preview_kind == "other-candidate":
        other = candidate(ArticulationOrigin.SELLER_SEEDED)
        preview = ClassificationPreview(other, classify_candidate(other), False)
    else:
        preview = transition_to_active_opportunity(account(), evaluation_evidence=evaluation_evidence(), authorization=None)
    with pytest.raises(ApplicationValidationError):
        request_for(kind, preview=preview)


def test_matching_previews_are_accepted():
    cand = confirm_payload().candidate
    assert request_for(K.CONFIRM_BAEC, preview=ClassificationPreview(cand, classify_candidate(cand), True)).preview
    preview = transition_to_active_opportunity(account(), evaluation_evidence=evaluation_evidence(), authorization=None)
    assert request_for(K.MOVE_TO_ACTIVE_OPPORTUNITY, preview=preview).preview is preview


def test_classification_preview_must_come_from_the_locked_classifier():
    seller_seeded = candidate(ArticulationOrigin.SELLER_SEEDED)
    with pytest.raises(ApplicationValidationError):
        ClassificationPreview(seller_seeded, classify_candidate(candidate()), True)  # result of another candidate
    with pytest.raises(ApplicationValidationError):
        ClassificationPreview(candidate(), classify_candidate(candidate()), False)  # confirmable flag wrong


# --- closed payload schema --------------------------------------------------------

AUTHORITY_OBJECTS = {
    "HumanAuthorization": HumanAuthorization("r", NOW, AuthorizationAction.CONFIRM_BAEC, "B-1"),
    "ApprovalRequest": request_for(K.MOVE_TO_ACTIVE_OPPORTUNITY),
    "mapping": {"approved": True},
    "bool": True,
}
FIELD_CASES = [
    (kind, f.name, label)
    for kind, builder in PAYLOAD_BUILDERS.items()
    for f in dataclasses.fields(builder())
    for label in AUTHORITY_OBJECTS
]


@pytest.mark.parametrize("kind,field,label", FIELD_CASES, ids=[f"{k.value}-{f}-{l}" for k, f, l in FIELD_CASES])
def test_no_payload_field_can_hold_an_authority_object_or_mapping(kind, field, label):
    with pytest.raises(ApplicationValidationError):
        PAYLOAD_BUILDERS[kind](**{field: AUTHORITY_OBJECTS[label]})


def test_payload_values_that_are_the_state_machines_to_judge_are_not_prejudged():
    """An empty reason is a REASON_MISSING decision for the locked state machine, not for the payload."""
    assert PAYLOAD_BUILDERS[K.MOVE_TO_NO_PLAUSIBLE_PATH](reason="").reason == ""


def test_judgment_id_must_be_a_positive_int_not_a_bool():
    for value in (0, -1, True, "1", 1.0):
        with pytest.raises(ApplicationValidationError):
            PAYLOAD_BUILDERS[K.MOVE_TO_CONDITIONALLY_DORMANT](judgment_id=value)


def test_build_request_refuses_a_payload_of_the_wrong_kind_and_text_enums():
    with pytest.raises(ApplicationValidationError):
        build_request(
            request_id="R", session_id="S", opened_at=NOW, kind=K.CONFIRM_BAEC, origin=ProposalOrigin.HUMAN_DRAFT,
            payload=active_payload(),
        )
    with pytest.raises(ApplicationValidationError):
        request_for(K.CONFIRM_BAEC, origin="DETERMINISTIC")


# --- context ----------------------------------------------------------------------


def test_actor_and_session_validate_themselves():
    actor = Actor("reviewer-1")
    assert InteractionSession("S-1", actor, NOW).actor is actor
    for bad in ("", "  ", None, 7):
        with pytest.raises(ApplicationValidationError):
            Actor(bad)
    with pytest.raises(ApplicationValidationError):
        InteractionSession("S-1", "reviewer-1", NOW)
    with pytest.raises(ApplicationValidationError):
        InteractionSession("S-1", actor, datetime(2026, 1, 15))


def test_system_clock_is_aware_utc_and_ids_are_unique():
    now = SystemClock().now()
    assert now.tzinfo is not None and now.utcoffset() == timedelta(0)
    ids = UuidIdFactory()
    tokens = {ids.new_token() for _ in range(100)}
    baec_ids = {ids.new_baec_id() for _ in range(100)}
    assert len(tokens) == 100 and len(baec_ids) == 100
    assert all(b.startswith("BAEC-") for b in baec_ids)
