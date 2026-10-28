"""Stage F context variants: apply one locked spec delta to a base context.

A delta adds exactly one grounding item or changes exactly one disposition
(design section 9.1). It never alters an existing value, and it never comes from
model output. Contexts use the Stage E EvaluationContext and digest unchanged.
"""

from __future__ import annotations

from dataclasses import replace

from baec_app.engine3.enums import ExternalReferenceDisposition, SourceIdentityDisclosure
from baec_app.engine3.errors import Engine3ValidationError
from baec_app.engine3.models import GroundingItem
from baec_app.engine3.offline_evaluation import EvaluationContext

_GROUNDING_KEYS = frozenset({
    "grounding_item_id", "exact_value", "disposition", "source_identity_disclosure", "is_buyer_statement",
    "approximate", "approved_specificity", "adversity_related", "adversity_context_reviewed",
})


def _bool(value: object, where: str) -> bool:
    if not isinstance(value, bool):
        raise Engine3ValidationError(f"{where} must be bool")
    return value


def grounding_item_from_dict(value: object) -> GroundingItem:
    if not isinstance(value, dict) or set(value) != _GROUNDING_KEYS:
        raise Engine3ValidationError("grounding item fields invalid")
    try:
        disposition = ExternalReferenceDisposition(value["disposition"])
        source = SourceIdentityDisclosure(value["source_identity_disclosure"])
    except ValueError:
        raise Engine3ValidationError("grounding item vocabulary invalid") from None
    return GroundingItem(
        grounding_item_id=value["grounding_item_id"],
        exact_value=value["exact_value"],
        disposition=disposition,
        source_identity_disclosure=source,
        is_buyer_statement=_bool(value["is_buyer_statement"], "is_buyer_statement"),
        adversity_related=_bool(value["adversity_related"], "adversity_related"),
        adversity_context_reviewed=_bool(value["adversity_context_reviewed"], "adversity_context_reviewed"),
        approximate=_bool(value["approximate"], "approximate"),
        approved_specificity=value["approved_specificity"],
    )


def apply_context_delta(base: EvaluationContext, delta: object) -> EvaluationContext:
    if not isinstance(base, EvaluationContext):
        raise Engine3ValidationError("base context has wrong type")
    if not isinstance(delta, dict) or len(delta) != 1:
        raise Engine3ValidationError("a context delta must contain exactly one operation")
    [(operation, body)] = delta.items()
    items = list(base.grounding_items)
    ids = [g.grounding_item_id for g in items]
    if operation == "add_grounding_item":
        item = grounding_item_from_dict(body)
        if item.grounding_item_id in ids:
            raise Engine3ValidationError("a delta may not replace an existing grounding item")
        items.append(item)
    elif operation == "set_disposition":
        if not isinstance(body, dict) or set(body) != {"grounding_item_id", "disposition"}:
            raise Engine3ValidationError("set_disposition fields invalid")
        if body["grounding_item_id"] not in ids:
            raise Engine3ValidationError("set_disposition target does not exist")
        try:
            disposition = ExternalReferenceDisposition(body["disposition"])
        except ValueError:
            raise Engine3ValidationError("set_disposition vocabulary invalid") from None
        index = ids.index(body["grounding_item_id"])
        items[index] = replace(items[index], disposition=disposition)
    else:
        raise Engine3ValidationError("unknown context delta operation")
    return EvaluationContext(base.upstream, base.context_state, tuple(items))
