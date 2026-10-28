"""Deterministic canonical serialization of approval-request content.

digest = SHA-256(UTF-8(canonical JSON)), lowercase hex.

Every structured type is encoded from a handwritten, ordered field table.
Nothing here uses repr(), str() of objects, dataclasses.asdict/fields,
pickle, or any generic introspection. A value the encoder does not know is
refused (CanonicalizationError): the serialization fails closed.

Changing any rule or field table changes digests and requires a new FORMAT.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum

from baec_app.application.errors import CanonicalizationError
from baec_app.application.proposals import ProposalOrigin
from baec_app.domain.enums import AccountState, AuthorizationAction
from baec_app.domain.models import (
    BaecCandidate,
    CriterionAssessment,
    EvaluationEvidence,
    EvidenceExcerpt,
    NonEvaluationEvidence,
    StringencyExpression,
)

FORMAT = "baec-approval-request/v1"

# Handwritten, ordered field tables keyed by exact type. Dispatch is by type
# identity (type(value) is T), so subclasses and look-alike classes are refused.
_DOMAIN_FIELDS: dict[type, tuple[str, ...]] = {
    EvidenceExcerpt: ("text", "provenance", "source_id"),
    CriterionAssessment: ("criterion", "finding", "evidence", "rationale"),
    StringencyExpression: (
        "verbatim_text",
        "comparator",
        "numeric_value",
        "unit",
        "qualitative_term",
        "recurrence_text",
        "timing_text",
    ),
    BaecCandidate: (
        "account_id",
        "source_interaction_id",
        "source_excerpt",
        "assessments",
        "articulation_origin",
        "elicitation_mode",
        "buyer_exact_statement",
        "buyer_role",
        "stringency",
    ),
    EvaluationEvidence: ("account_id", "evidence", "observed_at"),
    NonEvaluationEvidence: ("account_id", "evidence", "observed_at"),
}

# Request payload types live in requests.py, which imports this module at
# load time. Their table is therefore filled on first use by a local import,
# and is still keyed by the exact payload classes.
_PAYLOAD_FIELDS: dict[type, tuple[str, ...]] = {}


def _payload_fields() -> dict[type, tuple[str, ...]]:
    if not _PAYLOAD_FIELDS:
        from baec_app.application.requests import (  # local import: requests imports this module
            ConfirmBaecPayload,
            DormancyJudgmentPayload,
            MoveToActivePayload,
            MoveToDormantPayload,
            MoveToNoPlausiblePathPayload,
        )

        _PAYLOAD_FIELDS.update(
            {
                ConfirmBaecPayload: ("baec_id", "candidate", "captured_at"),
                DormancyJudgmentPayload: ("baec_id", "plausibility", "addressability", "notes"),
                MoveToDormantPayload: ("account_id", "baec_id", "judgment_id", "non_evaluation_evidence"),
                MoveToActivePayload: ("account_id", "evaluation_evidence"),
                MoveToNoPlausiblePathPayload: (
                    "account_id",
                    "ground",
                    "reason",
                    "non_evaluation_evidence",
                    "basis_interaction_id",
                ),
            }
        )
    return _PAYLOAD_FIELDS


def _field_table(value: object) -> tuple[str, tuple[str, ...]] | None:
    cls = type(value)
    if cls in _DOMAIN_FIELDS:
        return cls.__name__, _DOMAIN_FIELDS[cls]
    payload_fields = _payload_fields()
    if cls in payload_fields:
        return cls.__name__, payload_fields[cls]
    return None


def _encode(value: object):
    """Encode one value into JSON-ready data."""
    if value is None:
        return None
    if isinstance(value, Enum):  # before str: str-valued enums are str subclasses
        if type(value.value) is not str:
            raise CanonicalizationError(f"enum {type(value).__name__} does not have a string value")
        return value.value
    # Exact type checks: bool (an int subclass), str subclasses, and other
    # subclasses fall through to the refusal at the end.
    if type(value) is str:
        return value
    if type(value) is int:
        return value
    if type(value) is Decimal:
        if not value.is_finite():
            raise CanonicalizationError(f"non-finite Decimal {value!s} is not a canonical value")
        return {"$decimal": str(value)}
    if type(value) is datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise CanonicalizationError("naive datetime is not a canonical value")
        return {"$datetime": value.astimezone(timezone.utc).isoformat(timespec="microseconds")}
    if type(value) is tuple:
        return [_encode(item) for item in value]
    table = _field_table(value)
    if table is not None:
        name, fields = table
        encoded = {"$type": name}
        for field in fields:
            encoded[field] = _encode(getattr(value, field))
        return encoded
    raise CanonicalizationError(f"{type(value).__qualname__} is not a canonical value")


def _dumps(data) -> str:
    text = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    try:
        text.encode("utf-8")
    except UnicodeEncodeError as error:
        raise CanonicalizationError(f"text cannot be encoded as UTF-8: {error}") from error
    return text


def canonical_json(value: object) -> str:
    """The canonical JSON text of a value."""
    return _dumps(_encode(value))


def request_envelope(
    *,
    kind: Enum,
    action: AuthorizationAction,
    subject_id: str,
    target_state: AccountState | None,
    origin: ProposalOrigin,
    payload: object,
) -> dict:
    """The exact content an approval binds to. Identifiers, times, and previews are excluded."""
    if not isinstance(kind, Enum):
        raise CanonicalizationError("kind must be an enum member")
    if not isinstance(action, AuthorizationAction):
        raise CanonicalizationError("action must be an AuthorizationAction")
    if type(subject_id) is not str:
        raise CanonicalizationError("subject_id must be a string")
    if target_state is not None and not isinstance(target_state, AccountState):
        raise CanonicalizationError("target_state must be an AccountState or None")
    if not isinstance(origin, ProposalOrigin):
        raise CanonicalizationError("origin must be a ProposalOrigin")
    if type(payload) not in _payload_fields():
        raise CanonicalizationError("payload must be exactly one of the request payload types")
    return {
        "format": FORMAT,
        "kind": _encode(kind),
        "action": _encode(action),
        "subject_id": subject_id,
        "target_state": _encode(target_state),
        "origin": _encode(origin),
        "payload": _encode(payload),
    }


def digest_request(
    *,
    kind: Enum,
    action: AuthorizationAction,
    subject_id: str,
    target_state: AccountState | None,
    origin: ProposalOrigin,
    payload: object,
) -> str:
    """SHA-256 hex digest of the canonical request envelope."""
    envelope = request_envelope(
        kind=kind,
        action=action,
        subject_id=subject_id,
        target_state=target_state,
        origin=origin,
        payload=payload,
    )
    return hashlib.sha256(_dumps(envelope).encode("utf-8")).hexdigest()
