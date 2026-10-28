"""Governance: explicit correspondence binding, deterministic provenance, and the human preparation block."""

from __future__ import annotations

import json
import sqlite3

import pytest

from baec_app.engine2.domain import DimensionFinding, SufficiencyFinding
from baec_app.engine3.enums import HumanReviewDecision
from baec_app.engine3.reason_codes import ReasonCode
from baec_console import console_store
from baec_console.console_service import ConsoleError, PreparationRefused

from .conftest import ACTOR, ATTEST, monitored, observe, permitted_request, review, verified


def _two_verified(console):
    """Two human-verified correspondences on one account, from two different observations."""
    account_id, baec_id, first, first_review = verified(console)
    second = observe(console, account_id, baec_id, new_price="44000",
                     content="Second notice: the annual fee changes from USD 40,000 to USD 44,000.")
    second_review = review(console, second.candidate_id).review.review_id
    return account_id, (first, first_review), (second, second_review)


def _request(console, result, review_id, **overrides):
    return console.local.request_preparation(review_id, permitted_request(result, **overrides))


# --- explicit correspondence binding ---------------------------------------------------------------------------


def test_two_reviews_exist_and_the_newest_is_not_silently_authoritative(console):
    account_id, (first, first_review), (second, second_review) = _two_verified(console)
    view = console.local.account_view(account_id)
    assert [v["review"].review_id for v in view.verified_correspondences()] == [first_review, second_review]
    assert view.next_action == "Request preparation"  # nothing was chosen for the human
    request_id = _request(console, first, first_review)
    stored = console.local.account_view(account_id).requests[0]
    assert stored["review_id"] == first_review and stored["candidate_id"] == first.candidate_id
    assert [e["text"] for e in stored["snapshot"]["evidence"]] == [
        "Renewal notice: the annual fee changes from USD 40,000 to USD 43600."]
    preparation_id = console.local.generate_preparation(request_id)
    assert console.local.preparation_view(preparation_id)["provenance"]["bound_review_id"] == first_review


def test_preparation_stores_the_full_engine2_binding(console):
    account_id, baec_id, result, review_id = verified(console)
    request_id = _request(console, result, review_id)
    snapshot = console.local.account_view(account_id).requests[0]["snapshot"]
    plan = console.local.account_view(account_id).plan
    assert snapshot["review_id"] == review_id and snapshot["candidate_id"] == result.candidate_id
    assert (snapshot["monitoring_plan_id"], snapshot["plan_version"], snapshot["activation_id"]) == (
        plan.monitoring_plan_id, plan.plan_version, plan.activation_id)
    assert snapshot["outcome"] == "HUMAN_VERIFIED_CORRESPONDENCE"
    preparation_id = console.local.generate_preparation(request_id)
    provenance = console.local.preparation_view(preparation_id)["provenance"]
    assert (provenance["bound_review_id"], provenance["bound_candidate_id"]) == (review_id, result.candidate_id)


def test_changing_the_selected_review_changes_the_binding(console):
    account_id, (first, first_review), (second, second_review) = _two_verified(console)
    _request(console, first, first_review)
    _request(console, second, second_review)
    requests = console.local.account_view(account_id).requests
    assert [(r["review_id"], r["candidate_id"]) for r in requests] == [
        (first_review, first.candidate_id), (second_review, second.candidate_id)]
    assert requests[0]["context_digest"] != requests[1]["context_digest"]


@pytest.mark.parametrize("outcome", ["POSSIBLE_CORRESPONDENCE", "NO_CORRESPONDENCE"])
def test_an_unverified_review_can_never_be_selected(console, outcome):
    account_id, baec_id = monitored(console)
    result = observe(console, account_id, baec_id)
    if outcome == "POSSIBLE_CORRESPONDENCE":
        recorded = review(console, result.candidate_id, DimensionFinding.UNRESOLVED, SufficiencyFinding.NO)
    else:
        packet = console.local.review_packet(result.candidate_id)
        findings = {d: ("CONTRADICTED" if d.value == "THRESHOLD_MATCH" else "SUPPORTED")
                    for d in packet.plan.required_dimensions}
        recorded = console.local.submit_correspondence_review(
            result.candidate_id, displayed_context_digest=packet.context_digest, findings=findings, notes={},
            sufficiency="YES")
    assert recorded.outcome.value == outcome
    review_id = recorded.review.review_id
    assert console.local.account_view(account_id).verified_correspondences() == []
    with pytest.raises(ConsoleError, match="Only human-verified correspondence"):
        _request(console, result, review_id)
    with pytest.raises(ConsoleError, match="Only human-verified correspondence"):
        console.local.block_preparation(review_id, reason="")
    assert console.local.account_view(account_id).requests == []


def test_the_original_binding_does_not_change_when_a_later_review_is_added(console):
    account_id, baec_id, result, review_id = verified(console)
    request_id = _request(console, result, review_id)
    preparation_id = console.local.generate_preparation(request_id)
    later = review(console, result.candidate_id).review.review_id  # a second, later verified review
    view = console.local.account_view(account_id)
    assert view.requests[0]["review_id"] == review_id
    assert console.local.preparation_view(preparation_id)["provenance"]["bound_review_id"] == review_id
    assert later != review_id
    # The bound preparation is still current for its own review and can be accepted.
    console.local.review_artifact(preparation_id, "EMAIL", HumanReviewDecision.ACCEPT_AS_DRAFTED,
                                  attestations=ATTEST)


def test_the_timeline_traces_a_request_to_its_correspondence(console):
    account_id, baec_id, result, review_id = verified(console)
    _request(console, result, review_id)
    titles = [i.title for i in console.local.timeline(account_id)]
    assert ("Preparation requested from the correspondence verified 4 September 2026 "
            "(drafting only; not outreach authorization)") in titles


# --- deterministic provenance -----------------------------------------------------------------------------------


def test_deterministic_provenance_is_stored_separately_from_the_validator_origin(console):
    account_id, baec_id, result, review_id = verified(console)
    preparation_id = console.local.generate_preparation(_request(console, result, review_id))
    provenance = console.local.preparation_view(preparation_id)["provenance"]
    assert provenance["console_generator_origin"] == "DETERMINISTIC_CONSOLE_TEMPLATE_V1"
    assert provenance["engine3_validator_origin"] == "AI_INFERENCE"
    assert provenance["origin_compatibility_note"] == "E3-ORIGIN-COMPAT-V1"
    assert provenance["model_used"] is False
    assert "No model was used" in console_store.ORIGIN_COMPATIBILITY_TEXT


@pytest.mark.parametrize("values", [
    ("MODEL_GENERATED", "AI_INFERENCE", "E3-ORIGIN-COMPAT-V1", 0),
    ("DETERMINISTIC_CONSOLE_TEMPLATE_V1", "HUMAN_EDITED", "E3-ORIGIN-COMPAT-V1", 0),
    ("DETERMINISTIC_CONSOLE_TEMPLATE_V1", "AI_INFERENCE", "generated by a model", 0),
    ("DETERMINISTIC_CONSOLE_TEMPLATE_V1", "AI_INFERENCE", "E3-ORIGIN-COMPAT-V1", 1),
])
def test_a_record_cannot_claim_model_generation(console, values):
    account_id, baec_id, result, review_id = verified(console)
    preparation_id = console.local.generate_preparation(_request(console, result, review_id))
    connection = sqlite3.connect(console.local.console_path)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("INSERT INTO preparation_provenance VALUES (?, ?, ?, ?, ?, ?, ?)",
                               ("PREP-OTHER", review_id, result.candidate_id, *values))
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("UPDATE preparation_provenance SET model_used = 1 WHERE preparation_id = ?",
                               (preparation_id,))
    finally:
        connection.close()


def test_edit_provenance_is_distinct_from_the_deterministic_preparation(console):
    account_id, baec_id, result, review_id = verified(console)
    preparation_id = console.local.generate_preparation(_request(console, result, review_id))
    original = next(o["artifact"] for o in console.local.preparation_view(preparation_id)["preparation"]["artifacts"]
                    if o["artifact"]["artifact_id"] == "EMAIL")
    edit = console.local.edit_artifact(preparation_id, "EMAIL", original["segments"][:2])
    assert edit["provenance"] == {"console_origin": "HUMAN_EDIT", "engine3_validator_origin": "HUMAN_EDITED",
                                  "edited_by": ACTOR, "parent_preparation_id": preparation_id,
                                  "parent_artifact_id": "EMAIL"}
    provenance = console.local.preparation_view(preparation_id)["provenance"]
    assert provenance["console_generator_origin"] == "DETERMINISTIC_CONSOLE_TEMPLATE_V1"  # unchanged by the edit


def test_a_version_one_store_is_migrated_with_truthful_provenance(tmp_path):
    path = str(tmp_path / "console.sqlite3")
    old = sqlite3.connect(path, isolation_level=None)
    v1 = tuple(t for t in console_store._TABLES
               if t not in console_store._V2_TABLES + console_store._V3_TABLES + console_store._V4_TABLES)
    for statement in console_store._schema(v1):
        old.execute(statement)
    old.execute("INSERT INTO console_meta VALUES ('engine', 'BAEC_CONSOLE'), ('schema_version', '1')")
    old.execute("INSERT INTO preparation_requests VALUES ('PREQ-1', 'ACC', 'BAEC', 'HCR-1', 'CC-1', '{}', '{}', 'd', "
                "'2026-10-15T00:00:00+00:00', 'x')")
    old.execute("INSERT INTO preparations VALUES ('PREP-1', 'PREQ-1', 'DETERMINISTIC_CONSOLE_TEMPLATE_V1', ?, "
                "'2026-10-15T00:00:00+00:00', 'x')", (json.dumps([]),))
    old.close()
    store = console_store.ConsoleStore(console_store.open_console_store(path))
    try:
        provenance = store.provenance("PREP-1")
        assert (provenance["bound_review_id"], provenance["engine3_validator_origin"]) == ("HCR-1", "AI_INFERENCE")
        # The chain continues to V4: ownership and attribution are added, and both backfills are left for the
        # service (see test_tenancy and test_identity_freshness).
        assert store.db.execute("SELECT value FROM console_meta WHERE key = 'schema_version'").fetchone()[0] == "4"
        assert store.ownership_backfill() == console_store.BACKFILL_PENDING
        assert store.attribution_backfill() == console_store.BACKFILL_PENDING
    finally:
        store.db.close()


# --- human preparation block ------------------------------------------------------------------------------------


def test_block_prevents_preparation_through_engine3(console):
    account_id, baec_id, result, review_id = verified(console)
    console.local.block_preparation(review_id, reason="Buyer is dealing with a facility closure")
    snap, report = console.local.preparation_eligibility(review_id)
    assert snap["human_preparation_block"] is True
    assert [i.reason_code for i in report.issues] == [ReasonCode.HUMAN_PREPARATION_BLOCK]
    with pytest.raises(PreparationRefused) as refused:
        _request(console, result, review_id)
    assert refused.value.report.issues[0].reason_code is ReasonCode.HUMAN_PREPARATION_BLOCK
    view = console.local.account_view(account_id)
    assert view.requests == [] and view.preparations == []
    assert view.next_action == "Preparation blocked by human decision"


def test_block_after_a_request_stops_generation_and_produces_no_output(console):
    account_id, baec_id, result, review_id = verified(console)
    request_id = _request(console, result, review_id)
    console.local.block_preparation(review_id, reason="")
    with pytest.raises(PreparationRefused) as refused:
        console.local.generate_preparation(request_id)
    # Engine 3's locked precedence checks context first: the block changed the upstream the request was bound to.
    assert [i.reason_code for i in refused.value.report.issues] == [ReasonCode.CONTEXT_DIGEST_MISMATCH]
    _, report = console.local.preparation_eligibility(review_id)
    assert [i.reason_code for i in report.issues] == [ReasonCode.HUMAN_PREPARATION_BLOCK]
    assert console.local.account_view(account_id).preparations == []
    assert console.review_queue()["pending"] == []


def test_block_is_stored_with_human_provenance_and_is_append_only(console):
    account_id, baec_id, result, review_id = verified(console)
    console.local.block_preparation(review_id, reason="Sensitive timing")
    with pytest.raises(ConsoleError):
        console.local.block_preparation(review_id, reason="")
    (decision,) = console.local.account_view(account_id).blocks[review_id]
    assert (decision["action"], decision["decided_by"], decision["reason"]) == ("BLOCK", ACTOR, "Sensitive timing")
    connection = sqlite3.connect(console.local.console_path)
    try:
        for sql in ("UPDATE preparation_blocks SET action = 'UNBLOCK'", "DELETE FROM preparation_blocks"):
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(sql)
    finally:
        connection.close()


def test_unblocking_is_a_new_decision_and_history_stays_in_the_timeline(console):
    account_id, baec_id, result, review_id = verified(console)
    console.local.block_preparation(review_id, reason="Wait for leadership change")
    console.local.unblock_preparation(review_id, reason="Leadership settled")
    decisions = console.local.account_view(account_id).blocks[review_id]
    assert [d["action"] for d in decisions] == ["BLOCK", "UNBLOCK"]
    request_id = _request(console, result, review_id)  # eligible correspondence proceeds normally
    preparation_id = console.local.generate_preparation(request_id)
    assert console.local.preparation_view(preparation_id)["status"] == "Needs review"
    timeline = console.local.timeline(account_id)
    titles = [i.title for i in timeline]
    blocked = titles.index("Preparation blocked by human decision (correspondence verified 4 September 2026)")
    lifted = titles.index("Preparation block lifted by human decision (correspondence verified 4 September 2026)")
    assert blocked < lifted
    assert {i.actor_kind for i in timeline if "block" in i.title} == {"Human"}


def test_a_block_on_one_correspondence_does_not_block_another(console):
    account_id, (first, first_review), (second, second_review) = _two_verified(console)
    console.local.block_preparation(first_review, reason="")
    with pytest.raises(PreparationRefused):
        _request(console, first, first_review)
    assert _request(console, second, second_review)
