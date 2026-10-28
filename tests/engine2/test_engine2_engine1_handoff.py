"""Stage K: the read-only Engine 1 -> Engine 2 confirmed-BAEC handoff (baec_app/engine2/integration).

Engine 1 stores are real schema-v7 files built through Engine 1's own paths: the Phase 7F-B grant executor
(ConfirmationExecutionService) and the Phase 3 repository confirmation. The handoff reads them only through
Engine 1's query-only connection. Sockets are blocked; no model, provider, or network is reachable. These are
software tests on synthetic fixtures, not evidence for the theory.
"""

import hashlib
import re
import socket
from contextlib import closing
from dataclasses import fields, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from baec_app.application.composition import open_read_connection
from baec_app.application.proposal_authorization import ExecutionRefused
from baec_app.data.database import PersistenceIntegrityError, open_database, table_counts
from baec_app.data.records import SourceInteraction
from baec_app.data.repository import Repository
from baec_app.domain.baec_rules import create_confirmed_baec_record, create_nonconfirmed_classification_record
from baec_app.domain.enums import (BaecClassification, BaecCriterion, CriterionFinding, ProvenanceCategory, StalenessStatus,
                                   ThresholdComparator)
from baec_app.domain.models import Account, EvidenceExcerpt, StringencyExpression
from baec_app.engine2 import persistence as p
from baec_app.engine2 import review
from baec_app.engine2.ai import build_correspondence_ai_input
from baec_app.engine2.classification import CrossCuttingCheck, DimensionAssessment
from baec_app.engine2.domain import (AuthorizedSource, CorrespondenceCandidate, CorrespondenceDimension,
                                     CorrespondenceOutcome, CrossCuttingCheckName, DimensionFinding, SignalCandidate,
                                     SourceItem, SufficiencyFinding, activate_monitoring_plan, capture_observation,
                                     content_integrity_sha256)
from baec_app.engine2.errors import MonitoringPlanActivationError
from baec_app.engine2.integration import engine1_handoff as k
from baec_app.engine2.measurement import MeasurementInput, Transformation, compute_derived_measurement
from tests.builders import NOW, candidate, confirm_auth, excerpt
from tests.persistence_builders import INTERACTION_TEXTS, dump, tamper
from tests.review_builders import SUGGESTED
from tests.test_proposal_authorization import ISSUED, Authorized

REPO = Path(__file__).resolve().parents[2]
MODULE = REPO / "baec_app" / "engine2" / "integration" / "engine1_handoff.py"
D = CorrespondenceDimension
T = datetime(2027, 3, 1, 9, 0, tzinfo=timezone.utc)
WHITESPACE_TEXT = "Buyer:   If  pricing\trises above 10 %\n at renewal,  we re-run the review.  "
WHITESPACE_STATEMENT = "If  pricing\trises above 10 %\n at renewal,  we re-run the review.  "


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL"):
        monkeypatch.delenv(name, raising=False)

    def refuse(*args, **kwargs):
        raise AssertionError("a real socket connection was attempted")
    monkeypatch.setattr(socket.socket, "connect", refuse)


# --- Engine 1 stores ---------------------------------------------------------------------------------------------


@pytest.fixture
def granted(tmp_path):
    """The real Phase 7 path up to an issued grant: AI_DRAFT, ACCEPTED human review, grant. Not yet executed."""
    world = Authorized(str(tmp_path / "engine1.sqlite3"))
    world.path = Path(tmp_path / "engine1.sqlite3")
    yield world
    world.connection.close()


def execute(world):
    grant = world.grant()
    return world.execution.execute(grant.grant_id).baec_id


@pytest.fixture
def repository_store(tmp_path):
    """An Engine 1 store written through the Engine 1 repository (the Phase 3 confirmation persistence)."""
    path = tmp_path / "engine1_repository.sqlite3"
    connection = open_database(str(path))
    repo = Repository(connection)
    repo.add_account(Account("ACC-1", "Harbor Surgical Center"))
    for interaction_id, text in {**INTERACTION_TEXTS, "INT-W": WHITESPACE_TEXT}.items():
        repo.add_interaction(SourceInteraction(interaction_id, "ACC-1", NOW, text))
    yield path, connection, repo
    connection.close()


def confirm(repo, cand, baec_id):
    record = create_confirmed_baec_record(cand, baec_id=baec_id, captured_at=NOW, confirmation=confirm_auth(baec_id))
    repo.save_confirmed_baec(record)
    return record


def read(path, baec_id):
    with closing(open_read_connection(path)) as connection:
        return k.read_confirmed_baec(connection, baec_id)


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


# --- eligibility -------------------------------------------------------------------------------------------------


def test_a_baec_confirmed_by_the_real_grant_executor_crosses(granted):
    baec_id = execute(granted)
    handoff = read(granted.path, baec_id)
    record = Repository(granted.connection).get_baec_record(baec_id)
    assert (handoff.baec_id, handoff.account_id, handoff.source_interaction_id) == (
        baec_id, record.candidate.account_id, record.candidate.source_interaction_id)
    assert handoff.staleness_status is StalenessStatus.CURRENT
    assert (handoff.confirmed_by, handoff.confirmed_at) == (record.confirmation.authorized_by, record.confirmation.authorized_at)
    (authorization_id,) = granted.connection.execute(
        "SELECT confirmation_authorization_id FROM baec_records WHERE baec_id = ?", (baec_id,)).fetchone()
    assert handoff.confirmation_authorization_id == authorization_id
    assert handoff.engine1_reference.startswith(f"engine1:baec/{baec_id}/confirmation/{authorization_id}/handoff/")


def test_an_accepted_review_alone_does_not_cross(granted):
    with pytest.raises(k.HandoffRefused, match="no BAEC record"):
        read(granted.path, granted.pid)  # the AI_DRAFT proposal id is not a BAEC
    assert granted.count("baec_records") == 0


def test_an_issued_but_unexecuted_grant_does_not_cross(granted):
    grant = granted.grants.get_grant(granted.grant().grant_id)
    with pytest.raises(k.HandoffRefused, match="no BAEC record"):
        read(granted.path, grant.baec_id)


def test_an_expired_unused_grant_does_not_cross(granted):
    grant = granted.grants.get_grant(granted.grant().grant_id)
    granted.clock.current = grant.expires_at + timedelta(seconds=1)
    with pytest.raises(ExecutionRefused):
        granted.execution.execute(grant.grant_id)
    with pytest.raises(k.HandoffRefused, match="no BAEC record"):
        read(granted.path, grant.baec_id)


@pytest.mark.parametrize("finding", [CriterionFinding.NOT_MET, CriterionFinding.UNKNOWN])
def test_an_unconfirmed_classification_record_does_not_cross(repository_store, finding):
    path, _, repo = repository_store
    cand = candidate(findings={next(iter(candidate().assessments)).criterion: finding})
    record = create_nonconfirmed_classification_record(cand, baec_id="B-UNCONFIRMED", captured_at=NOW)
    repo.save_classification_record(record)
    with pytest.raises(k.HandoffRefused, match=f"is {record.classification.value}, not CONFIRMED_BAEC"):
        read(path, "B-UNCONFIRMED")


def test_a_caller_cannot_fabricate_or_alter_a_handoff(granted):
    handoff = read(granted.path, execute(granted))
    values = {f.name: getattr(handoff, f.name) for f in fields(handoff) if f.name != "_source"}
    with pytest.raises(k.HandoffRefused, match="built only by reading Engine 1"):
        k.ConfirmedBaecHandoff(**values)
    with pytest.raises(k.HandoffRefused, match="digest does not match"):
        replace(handoff, staleness_status=StalenessStatus.CURRENT, buyer_exact_statement="We would switch.")
    with pytest.raises(k.HandoffRefused):
        k.monitoring_plan_from_handoff(object(), plan_choices())
    assert not {n for n in ("status", "classification", "confirmed", "is_confirmed")
                if n in __import__("inspect").signature(k.read_confirmed_baec).parameters}


def test_a_writable_engine1_connection_is_refused(granted):
    baec_id = execute(granted)
    with pytest.raises(k.HandoffRefused, match="query-only"):
        k.read_confirmed_baec(granted.connection, baec_id)


# --- exactness ---------------------------------------------------------------------------------------------------


def test_exact_text_survives_without_normalization(repository_store):
    path, _, repo = repository_store
    source = EvidenceExcerpt(WHITESPACE_TEXT, ProvenanceCategory.BUYER_FACT, "INT-W")
    stringency = StringencyExpression("above 10 %\n at renewal", ThresholdComparator.GREATER_THAN, Decimal("10.0"),
                                      " % ", recurrence_text=" each renewal ", timing_text="\n at renewal,  ")
    cand = candidate(source_excerpt=source, criterion_evidence=source, source_interaction_id="INT-W",
                     buyer_exact_statement=WHITESPACE_STATEMENT, buyer_role="  VP Operations ", stringency=stringency)
    record = confirm(repo, cand, "B-WS")
    handoff = read(path, "B-WS")
    assert handoff.source_excerpt == source and handoff.buyer_exact_statement == WHITESPACE_STATEMENT
    assert handoff.buyer_role == "  VP Operations "
    assert handoff.stringency == stringency and str(handoff.stringency.numeric_value) == "10.0"
    assert handoff.criteria == record.candidate.assessments  # every criterion, including evaluation linkage
    linkage = next(a for a in handoff.criteria if a.criterion is BaecCriterion.EVALUATION_LINKAGE)
    assert linkage == next(a for a in record.candidate.assessments if a.criterion is linkage.criterion)
    plan = k.monitoring_plan_from_handoff(handoff, plan_choices())
    assert plan.condition_reference == WHITESPACE_STATEMENT and plan.timing_context == "\n at renewal,  "
    assert (plan.threshold.comparator, plan.threshold.value, plan.threshold.unit) == (
        ThresholdComparator.GREATER_THAN, Decimal("10.0"), " % ")
    assert str(plan.threshold.value) == "10.0"
    condition = k.condition_input(handoff)
    assert condition.buyer_exact_statement == condition.condition_reference == WHITESPACE_STATEMENT


def test_the_ai_derived_normalized_condition_never_crosses(repository_store):
    from baec_app.domain.models import AiDerivedText
    path, connection, repo = repository_store
    record = create_confirmed_baec_record(candidate(buyer_exact_statement="pricing"), baec_id="B-NORM", captured_at=NOW,
                                          confirmation=confirm_auth("B-NORM"))
    repo.save_confirmed_baec(replace(record, normalized_condition=AiDerivedText("AI paraphrase of the condition", NOW)))
    handoff = read(path, "B-NORM")
    assert "AI paraphrase" not in repr(handoff) and k.condition_input(handoff).normalized_condition is None


@pytest.mark.parametrize("stringency, threshold", [
    (None, None),
    (StringencyExpression("a significant increase", ThresholdComparator.QUALITATIVE_ONLY,
                          qualitative_term="significant"), None),
    (StringencyExpression("whenever it changes", ThresholdComparator.NONE_STATED, recurrence_text="whenever"), None),
    (StringencyExpression("roughly the usual", None), None),
    (StringencyExpression("at least 5 points", ThresholdComparator.AT_LEAST, Decimal("5"), "points"),
     (ThresholdComparator.AT_LEAST, Decimal("5"), "points")),
])
def test_thresholds_cross_exactly_or_not_at_all(repository_store, stringency, threshold):
    path, _, repo = repository_store
    confirm(repo, candidate(buyer_exact_statement="pricing", stringency=stringency), "B-T")
    plan = k.monitoring_plan_from_handoff(read(path, "B-T"), plan_choices())
    found = None if plan.threshold is None else (plan.threshold.comparator, plan.threshold.value, plan.threshold.unit)
    assert found == threshold


def test_a_number_without_a_unit_is_refused_rather_than_invented(repository_store):
    path, _, repo = repository_store
    confirm(repo, candidate(buyer_exact_statement="pricing",
                            stringency=StringencyExpression("more than 10", ThresholdComparator.GREATER_THAN, Decimal("10"))),
            "B-NOUNIT")
    with pytest.raises(k.HandoffRefused, match="without a unit"):
        k.monitoring_plan_from_handoff(read(path, "B-NOUNIT"), plan_choices())


def test_no_exact_buyer_statement_is_refused_rather_than_paraphrased(repository_store):
    path, _, repo = repository_store
    confirm(repo, candidate(), "B-NOSTATEMENT")
    handoff = read(path, "B-NOSTATEMENT")
    assert handoff.buyer_exact_statement is None
    for build in (lambda: k.monitoring_plan_from_handoff(handoff, plan_choices()), lambda: k.condition_input(handoff)):
        with pytest.raises(k.HandoffRefused, match="no exact buyer statement"):
            build()


def test_the_handoff_digest_is_deterministic_and_content_bound(repository_store):
    path, _, repo = repository_store
    confirm(repo, candidate(buyer_exact_statement="pricing"), "B-D")
    first, second = read(path, "B-D"), read(path, "B-D")
    assert first == second and first.digest == second.digest and len(first.digest) == 64


# --- read-only isolation -----------------------------------------------------------------------------------------


def test_reading_changes_nothing_in_engine1(granted):
    baec_id = execute(granted)
    before_bytes, before_rows = file_hash(granted.path), dump(granted.connection)
    grants_before = granted.connection.execute("SELECT * FROM human_authorization_grant_consumptions").fetchall()
    handoff = read(granted.path, baec_id)
    plan = k.monitoring_plan_from_handoff(handoff, plan_choices())
    k.currentness_reading(plan, handoff)
    k.condition_input(handoff)
    assert file_hash(granted.path) == before_bytes and dump(granted.connection) == before_rows
    assert granted.connection.execute("SELECT * FROM human_authorization_grant_consumptions").fetchall() == grants_before
    assert granted.count("account_state_transitions") == 0
    assert granted.connection.execute("SELECT state FROM accounts").fetchall() == [(None,)] * granted.count("accounts")


# --- Engine 2 construction ---------------------------------------------------------------------------------------


def plan_choices(**changes):
    values = dict(monitoring_plan_id="MP-K-001", plan_version=1, target_entities=("Supplier (synthetic)",),
                  required_dimensions=frozenset({D.ENTITY_MATCH, D.THRESHOLD_MATCH, D.COMPARATOR_MATCH,
                                                 D.NUMERIC_MAGNITUDE, D.UNIT_MATCH}),
                  not_applicable_dimensions=frozenset(set(D) - {D.ENTITY_MATCH, D.THRESHOLD_MATCH, D.COMPARATOR_MATCH,
                                                                 D.NUMERIC_MAGNITUDE, D.UNIT_MATCH}),
                  authorized_source_ids=frozenset({"SRC-K-NOTICES"}), authorized_by="PLANNER (synthetic)",
                  authorization_reference="PLAN-AUTH-K-001", authorized_at=T)
    values.update(changes)
    return k.PlanChoices(**values)


def test_engine1_facts_and_engine2_choices_stay_separate(granted):
    handoff = read(granted.path, execute(granted))
    plan = k.monitoring_plan_from_handoff(handoff, plan_choices())
    assert (plan.baec_id, plan.condition_reference, plan.engine1_currentness_reference) == (
        handoff.baec_id, SUGGESTED, handoff.engine1_reference)
    assert (plan.threshold.comparator, plan.threshold.value, plan.threshold.unit) == (
        ThresholdComparator.GREATER_THAN, Decimal("10"), "%")
    assert (plan.monitoring_plan_id, plan.authorized_by, plan.authorized_source_ids) == (
        "MP-K-001", "PLANNER (synthetic)", frozenset({"SRC-K-NOTICES"}))
    choice_fields = {f.name for f in fields(k.PlanChoices)}
    assert not choice_fields & {"baec_id", "condition_reference", "threshold", "timing_context",
                                "engine1_currentness_reference", "staleness_status", "buyer_exact_statement"}
    for override in ({"baec_id": "BAEC-OTHER"}, {"threshold": None}, {"condition_reference": "x"}):
        with pytest.raises(TypeError):
            plan_choices(**override)


@pytest.mark.parametrize("change", [
    {"baec_id": "BAEC-OTHER"}, {"condition_reference": "We would switch at any increase."}, {"threshold": None},
    {"timing_context": "next year"}, {"engine1_currentness_reference": "engine1:baec/other"},
])
def test_a_plan_disagreeing_with_the_handoff_is_refused(granted, change):
    handoff = read(granted.path, execute(granted))
    plan = replace(k.monitoring_plan_from_handoff(handoff, plan_choices()), **change)
    with pytest.raises(k.HandoffRefused, match="does not carry exactly the BAEC facts"):
        k.currentness_reading(plan, handoff)


def test_a_plan_built_from_another_confirmation_is_refused(repository_store):
    path, _, repo = repository_store
    confirm(repo, candidate(buyer_exact_statement="pricing"), "B-ONE")
    confirm(repo, candidate(buyer_exact_statement="pricing"), "B-TWO")
    one, two = read(path, "B-ONE"), read(path, "B-TWO")
    with pytest.raises(k.HandoffRefused):
        k.currentness_reading(k.monitoring_plan_from_handoff(one, plan_choices()), two)


def test_engine1_staleness_is_surfaced_and_engine2_activation_refuses_a_stale_baec(granted):
    baec_id = execute(granted)
    granted.connection.execute("UPDATE baec_records SET staleness_status = 'STALE' WHERE baec_id = ?", (baec_id,))
    handoff = read(granted.path, baec_id)
    assert handoff.staleness_status is StalenessStatus.STALE
    plan = k.monitoring_plan_from_handoff(handoff, plan_choices())
    reading = k.currentness_reading(plan, handoff)
    assert reading.staleness_status is StalenessStatus.STALE
    with closing(p.open_engine2_database(":memory:")) as e2:
        p.record_authorized_source(e2, source(), recorded_at=T, recorded_by="FIXTURE")
        p.record_monitoring_plan(e2, plan, recorded_at=T, recorded_by="FIXTURE")
        with pytest.raises(MonitoringPlanActivationError, match="STALE"):
            p.record_plan_activation(e2, plan.monitoring_plan_id, 1, reading, activation_id="ACT-K", recorded_at=T,
                                     recorded_by="FIXTURE")
        assert e2.execute("SELECT result FROM plan_activations").fetchall() == [("REFUSED",)]


def test_a_corrupted_engine1_record_fails_closed(granted):
    baec_id = execute(granted)
    tamper(granted.connection, "UPDATE criterion_assessments SET finding = 'NOT_MET' WHERE baec_id = ? AND position = 0",
           (baec_id,))
    with pytest.raises(PersistenceIntegrityError):
        read(granted.path, baec_id)


def test_a_duplicate_plan_identity_is_refused_by_stage_d(granted):
    handoff = read(granted.path, execute(granted))
    plan = k.monitoring_plan_from_handoff(handoff, plan_choices())
    with closing(p.open_engine2_database(":memory:")) as e2:
        p.record_authorized_source(e2, source(), recorded_at=T, recorded_by="FIXTURE")
        p.record_monitoring_plan(e2, plan, recorded_at=T, recorded_by="FIXTURE")
        with pytest.raises(p.DuplicateRecordError):
            p.record_monitoring_plan(e2, plan, recorded_at=T, recorded_by="FIXTURE")


# --- end to end ---------------------------------------------------------------------------------------------------


def source():
    return AuthorizedSource("SRC-K-NOTICES", "supplier renewal notice", "Supplier (synthetic)",
                            "forwarded by the buyer", "email forward", "notice text", "PLANNER (synthetic)", T)


def test_end_to_end_from_engine1_confirmation_to_an_engine2_human_review(granted):
    # 1-3. Engine 1: interaction, AI_DRAFT, ACCEPTED human review, grant, and the real executor.
    baec_id = execute(granted)
    engine1_before = (file_hash(granted.path), dump(granted.connection), table_counts(granted.connection))
    # 4. Stage K: read the confirmed BAEC through Engine 1's query-only connection.
    handoff = read(granted.path, baec_id)
    # 5-6. Engine 2: plan from handoff facts plus explicit choices; activate under the locked rule.
    plan = k.monitoring_plan_from_handoff(handoff, plan_choices())
    e2 = p.open_engine2_database(":memory:")
    with closing(e2):
        p.record_authorized_source(e2, source(), recorded_at=T, recorded_by="FIXTURE")
        p.record_monitoring_plan(e2, plan, recorded_at=T, recorded_by="FIXTURE")
        active = p.record_plan_activation(e2, plan.monitoring_plan_id, 1, k.currentness_reading(plan, handoff),
                                          activation_id="ACT-K-001", recorded_at=T, recorded_by="FIXTURE")
        assert active == activate_monitoring_plan(plan, k.currentness_reading(plan, handoff))
        # 7-9. Evidence, a measurement, and a candidate, through Stage D.
        text = "Renewal notice: from 1 March 2027 the unit price changes from USD 200 to USD 224."
        item = SourceItem("ITEM-K-1", "SRC-K-NOTICES", "supplier renewal notice", "email forward", T, text)
        observation = capture_observation(active, {"SRC-K-NOTICES": source()}, item, observation_id="OBS-K-1",
                                          provenance=ProvenanceCategory.BUYER_FACT,
                                          content_sha256=content_integrity_sha256(text), source_locator="notice-1",
                                          evidence_event_id="EVENT-K-1")
        p.record_observation(e2, observation, activation_id="ACT-K-001", recorded_at=T, recorded_by="FIXTURE")
        measurement = compute_derived_measurement(
            "DM-K-1", Transformation.PERCENT_CHANGE_FROM_TWO_PRICES,
            (MeasurementInput(observation, "baseline_price", Decimal("200"), "USD"),
             MeasurementInput(observation, "new_price", Decimal("224"), "USD")), T)
        p.record_derived_measurement(e2, measurement, recorded_at=T, recorded_by="FIXTURE")
        signal = SignalCandidate("SC-K-1", plan.monitoring_plan_id, 1, (observation,), "ANALYST (synthetic)")
        p.record_signal_candidate(e2, signal, recorded_at=T, recorded_by="FIXTURE")
        p.record_correspondence_candidate(e2, CorrespondenceCandidate("CC-K-1", baec_id, plan.monitoring_plan_id, 1,
                                                                      (signal,)), recorded_at=T, recorded_by="FIXTURE")
        # 10. Stage I review packet.
        context = dict(monitoring_plan_id=plan.monitoring_plan_id, plan_version=1, baec_id=baec_id,
                       candidate_id="CC-K-1", activation_id="ACT-K-001")
        packet = review.build_review_packet(e2, **context)
        assert packet.plan == plan and packet.ledger.observations == (observation,)
        # Stage F input from the handoff: builds without any model call.
        ai_input = build_correspondence_ai_input(k.condition_input(handoff), packet.candidate, packet.ledger)
        assert ai_input.payload["baec"]["buyer_exact_statement"] == SUGGESTED
        # 11. A human review through Stage I.
        evidence = {D.NUMERIC_MAGNITUDE: ("DM-K-1",), D.THRESHOLD_MATCH: ("DM-K-1",)}
        findings = tuple(
            DimensionAssessment(d, DimensionFinding.SUPPORTED, evidence.get(d, ("OBS-K-1",)), f"Reviewer reason, {d.label}.")
            if d in plan.required_dimensions else DimensionAssessment(d, DimensionFinding.NOT_APPLICABLE, (), "Not in plan.")
            for d in D)
        checks = tuple(CrossCuttingCheck(c, "No concern noted.") for c in CrossCuttingCheckName)
        recorded = review.submit_human_review(
            e2, **context, context_digest=packet.context_digest, review_id="REV-K-1", findings=findings, checks=checks,
            sufficiency=SufficiencyFinding.YES, reviewer="REVIEWER (synthetic)", reviewed_at=T + timedelta(days=1),
            baec_revalidation_required=False, revalidation_triggers=(), recorded_at=T + timedelta(days=1),
            recorded_by="REVIEWER (synthetic)")
        # 12. The authoritative correspondence result, derived by the locked classifier.
        assert recorded.outcome is CorrespondenceOutcome.HUMAN_VERIFIED_CORRESPONDENCE
        assert p.load_review(e2, "REV-K-1").outcome is recorded.outcome
        p.verify_database(e2)
    # Engine 1 is byte-for-byte unchanged by the whole Engine 2 path: no account state, no opportunity, no contact.
    assert (file_hash(granted.path), dump(granted.connection), table_counts(granted.connection)) == engine1_before
    assert granted.count("account_state_transitions") == 0


# --- static boundary -----------------------------------------------------------------------------------------


def _code():
    return re.sub(r'"""[\s\S]*?"""', "", MODULE.read_text(encoding="utf-8"))


def test_the_handoff_module_reads_only_and_imports_only_what_it_needs():
    code = _code()
    imports = set(re.findall(r"^from ([\w.]+) import", code, re.M))
    assert {i for i in imports if i.startswith("baec_app")} == {
        "baec_app.data.database", "baec_app.data.repository", "baec_app.domain.enums", "baec_app.domain.models"}
    assert re.findall(r'execute\(\s*"([^"]*)"', code) == ["PRAGMA query_only",
                                                          "SELECT confirmation_authorization_id FROM baec_records WHERE baec_id = ?"]
    assert not re.search(r"\b(INSERT|UPDATE|DELETE|REPLACE|DROP|ALTER|CREATE)\b", code)
    for forbidden in ("save_", "add_", "record_", "persist_", "execute(grant", "authorize_", "authority.",
                      "ConfirmationExecutionService",
                      "proposal_bridge", "authorization_grants", "baec_app.ai", "baec_app.application", "anthropic",
                      "socket", "requests", "urllib", "http", "os.environ", "getenv", "API_KEY", "AccountState",
                      "account_state", "transition", "opportunit", "outreach", "contact", "crm", "latest",
                      "datetime.now", "utcnow", ".strip(", ".lower(", ".upper(", "casefold", "normalize(",
                      ".replace(", "uuid", "random"):
        assert forbidden not in code, forbidden


def test_engine1_production_code_still_never_names_engine2():
    for path in (REPO / "baec_app").rglob("*.py"):
        if (REPO / "baec_app" / "engine2") not in path.parents:
            assert "engine2" not in path.read_text(encoding="utf-8"), path
