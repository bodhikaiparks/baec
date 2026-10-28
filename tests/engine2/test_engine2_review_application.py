"""Stage I: the human review application layer (baec_app/engine2/review.py).

Stores are seeded from the locked Stage B corpus through the locked Stage D writers. Oracle fields are read
only to build fixtures and to compare afterwards. AI proposals come from predetermined replay text; nothing
here calls a model, a provider, or the network.
"""

import inspect
import re
from collections import Counter
from contextlib import closing
from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from baec_app.domain.enums import StalenessStatus
from baec_app.engine2 import ai_persistence as a
from baec_app.engine2 import persistence as p
from baec_app.engine2 import review as r
from baec_app.engine2.ai import ModelResponse, build_correspondence_ai_input
from baec_app.engine2.classification import DimensionAssessment, EvidenceLedger
from baec_app.engine2.domain import (CorrespondenceCandidate, CorrespondenceDimension, CorrespondenceOutcome,
                                     DimensionFinding, Engine1CurrentnessReading, SignalCandidate, SufficiencyFinding)
from baec_app.engine2.errors import ReviewInconsistentError
from tests.engine2.ai_replay import ai_input_for, condition_input, faithful_output
from tests.engine2.corpus_adapter import (REPO, REVIEWED_AT, REVIEWER, authorized_sources, checks_from, currentness_for,
                                          findings_from, load_corpus, plan_for, run_case)

CORPUS = load_corpus()
CASES = {c["case_id"]: c for c in CORPUS["cases"]}
T = datetime.fromisoformat("2027-06-01T00:00:00+00:00")
ACTOR = "REVIEWER-B (synthetic human alias)"
ACTIVATION = "ACT-MP-HBR-001-1"
MODULE = REPO / "baec_app" / "engine2" / "review.py"


def seed(case_id):
    """Record everything admissible for one case except its review. Returns (connection, run)."""
    case = CASES[case_id]
    conn = p.open_engine2_database(":memory:")
    for source in authorized_sources(CORPUS).values():
        p.record_authorized_source(conn, source, recorded_at=T, recorded_by=ACTOR)
    plan = plan_for(CORPUS, case)
    p.record_monitoring_plan(conn, plan, recorded_at=T, recorded_by=ACTOR)
    run = run_case(CORPUS, case)
    try:
        p.record_plan_activation(conn, plan.monitoring_plan_id, plan.plan_version, currentness_for(CORPUS, case),
                                 activation_id=ACTIVATION, recorded_at=T, recorded_by=ACTOR)
    except Exception:
        return conn, run
    packets = {pk["packet_id"]: pk for pk in case["source_packets"]}
    for o in run.observations:
        p.record_observation(conn, o, activation_id=ACTIVATION, recorded_at=T, recorded_by=ACTOR)
    for item in run.rejected:
        p.record_source_refusal(conn, item, activation_id=ACTIVATION, source_reference=packets[item.item_id]["source_id"],
                                recorded_at=T, recorded_by=ACTOR)
    for s in run.ledger.supersessions:
        p.record_supersession(conn, s, recorded_at=T, recorded_by=ACTOR)
    for m in run.measurements:
        p.record_derived_measurement(conn, m, recorded_at=T, recorded_by=ACTOR)
    if run.candidate is not None:
        for s in run.candidate.signal_candidates:
            p.record_signal_candidate(conn, s, recorded_at=T, recorded_by=ACTOR)
        p.record_correspondence_candidate(conn, run.candidate, recorded_at=T, recorded_by=ACTOR)
    return conn, run


def ref(run):
    plan = run.active_plan.plan
    return dict(monitoring_plan_id=plan.monitoring_plan_id, plan_version=plan.plan_version, baec_id=plan.baec_id,
                candidate_id=run.candidate.candidate_id, activation_id=ACTIVATION)


def packet_for(conn, run, **overrides):
    return r.build_review_packet(conn, **{**ref(run), **overrides})


def human_judgments(case_id, review_id="REV-I-1", **overrides):
    case = CASES[case_id]
    values = dict(review_id=review_id, findings=findings_from(case), checks=checks_from(case),
                  sufficiency=SufficiencyFinding(case["expected_human_sufficiency_finding"]), reviewer=REVIEWER,
                  reviewed_at=REVIEWED_AT, baec_revalidation_required=case["expected_baec_revalidation_required"],
                  revalidation_triggers=tuple(case["revalidation_triggers"]), recorded_at=T, recorded_by=ACTOR)
    values.update(overrides)
    return values


def submit(conn, run, packet, case_id, **overrides):
    return r.submit_human_review(conn, **ref(run), context_digest=packet.context_digest,
                                 **human_judgments(case_id, **overrides))


# --- the review packet -------------------------------------------------------------------------------------


@pytest.mark.parametrize("case_id", ["HBR-CORR-003", "HBR-CORR-008", "HBR-CORR-020", "HBR-CORR-024", "HBR-CORR-047",
                                     "HBR-CORR-051", "HBR-CORR-007"])
def test_the_packet_reconstructs_the_complete_authoritative_context(case_id):
    conn, run = seed(case_id)
    with closing(conn):
        packet = packet_for(conn, run)
        assert packet.ledger == run.ledger  # every Observation, Supersession, Measurement, Refusal, in order
        assert packet.candidate == run.candidate and packet.active_plan == run.active_plan
        assert (packet.monitoring_plan_id, packet.plan_version, packet.baec_id, packet.candidate_id) == \
            ("MP-HBR-001", 1, run.active_plan.plan.baec_id, f"CC-{case_id}")
        plan = packet.plan
        assert plan.threshold is not None and plan.timing_context and plan.required_dimensions
        assert plan.plan_fact_ids == run.active_plan.plan.plan_fact_ids
        assert plan.plan_rule_ids == run.active_plan.plan.plan_rule_ids
        assert packet.review_history == ()
        assert packet == packet_for(conn, run)  # deterministic


def test_evidence_text_round_trips_exactly():
    conn, run = seed("HBR-CORR-008")
    with closing(conn):
        packet = packet_for(conn, run)
        expected = {o["observation_id"]: o["exact_evidence_content"] for o in CASES["HBR-CORR-008"]["raw_observations_expected"]}
        assert {o.observation_id: o.exact_evidence_content for o in packet.ledger.observations} == expected


@pytest.mark.parametrize("case_id", ["HBR-CORR-008", "HBR-CORR-047", "HBR-CORR-024"])
def test_superseded_and_retracted_evidence_stays_visible_with_locked_usability(case_id):
    conn, run = seed(case_id)
    with closing(conn):
        packet = packet_for(conn, run)
        superseded = {s.superseded_observation_id for s in packet.ledger.supersessions}
        assert superseded and superseded <= packet.ledger.observation_ids  # history kept, not repaired
        assert packet.usable_observation_ids == run.ledger.usable_observation_ids == packet.ledger.observation_ids - superseded
        assert packet.usable_measurement_ids == run.ledger.usable_measurement_ids
        assert packet.ledger.supersessions == run.ledger.supersessions


def test_derived_measurement_identity_and_inputs_are_intact():
    conn, run = seed("HBR-CORR-020")
    with closing(conn):
        (m,) = packet_for(conn, run).ledger.measurements
        (expected,) = run.measurements
        assert m == expected and m.inputs == expected.inputs and m.output_value == expected.output_value
        assert [i.observation for i in m.inputs] == [i.observation for i in expected.inputs]


def test_refusals_are_part_of_the_context():
    case_id = next(c for c, case in CASES.items() if case["packets_not_admitted"] and case["raw_observations_expected"])
    conn, run = seed(case_id)
    with closing(conn):
        packet = packet_for(conn, run)
        assert packet.ledger.rejected_items == tuple(run.rejected) and packet.ledger.rejected_items


# --- recording reviews --------------------------------------------------------------------------------------


@pytest.mark.parametrize("case_id", sorted(CASES))
def test_every_corpus_review_through_the_application_layer_gets_the_locked_outcome(case_id):
    conn, run = seed(case_id)
    with closing(conn):
        if run.candidate is None:
            with pytest.raises(r.ReviewContextError):
                r.build_review_packet(conn, monitoring_plan_id="MP-HBR-001", plan_version=1,
                                      baec_id=run.active_plan.plan.baec_id if run.active_plan else "BAEC-HBR-001",
                                      candidate_id=f"CC-{case_id}", activation_id=ACTIVATION)
            assert CASES[case_id]["expected_correspondence_outcome"] is None
            return
        packet = packet_for(conn, run)
        recorded = submit(conn, run, packet, case_id, review_id=f"REV-{case_id}")
        assert recorded.review == run.review
        assert recorded.outcome is CorrespondenceOutcome(CASES[case_id]["expected_correspondence_outcome"])
        p.verify_database(conn)


def test_the_application_layer_preserves_the_locked_64_case_distribution():
    derived = Counter()
    for case_id in sorted(CASES):
        conn, run = seed(case_id)
        with closing(conn):
            if run.candidate is None:
                derived[None] += 1
                continue
            derived[submit(conn, run, packet_for(conn, run), case_id).outcome.value] += 1
    assert derived == {"NO_CORRESPONDENCE": 20, "POSSIBLE_CORRESPONDENCE": 32, "HUMAN_VERIFIED_CORRESPONDENCE": 9, None: 3}


def test_reviews_are_append_only_history_in_recording_order():
    conn, run = seed("HBR-CORR-003")
    with closing(conn):
        packet = packet_for(conn, run)
        first = submit(conn, run, packet, "HBR-CORR-003", review_id="REV-I-1")
        assert first.outcome is CorrespondenceOutcome.HUMAN_VERIFIED_CORRESPONDENCE
        second = submit(conn, run, packet_for(conn, run), "HBR-CORR-003", review_id="REV-I-2",
                        sufficiency=SufficiencyFinding.UNKNOWN, reviewed_at=REVIEWED_AT + timedelta(days=1))
        assert second.outcome is CorrespondenceOutcome.POSSIBLE_CORRESPONDENCE
        history = packet_for(conn, run).review_history
        assert [h.review.review_id for h in history] == ["REV-I-1", "REV-I-2"]
        assert history[0] == first and history[1] == second  # the earlier review is unchanged
        assert p.load_review(conn, "REV-I-1") == first.review
        with pytest.raises(p.DuplicateRecordError):
            submit(conn, run, packet_for(conn, run), "HBR-CORR-003", review_id="REV-I-1")
        assert [h.review.review_id for h in packet_for(conn, run).review_history] == ["REV-I-1", "REV-I-2"]


def test_the_caller_cannot_supply_an_outcome_or_classification():
    params = inspect.signature(r.submit_human_review).parameters
    assert not {n for n in params if re.search(r"outcome|classif|result|state|verdict", n)}
    assert all(p_.kind is inspect.Parameter.KEYWORD_ONLY for name, p_ in params.items() if name != "connection")
    conn, run = seed("HBR-CORR-003")
    with closing(conn):
        packet = packet_for(conn, run)
        with pytest.raises(TypeError):
            submit(conn, run, packet, "HBR-CORR-003", outcome=CorrespondenceOutcome.HUMAN_VERIFIED_CORRESPONDENCE)
        assert p.review_ids_for_candidate(conn, run.candidate.candidate_id) == []


def test_every_human_judgment_is_required():
    params = inspect.signature(r.submit_human_review).parameters
    for name in ("findings", "checks", "sufficiency", "reviewer", "reviewed_at", "baec_revalidation_required",
                 "revalidation_triggers", "context_digest", "review_id", "recorded_at", "recorded_by"):
        assert params[name].default is inspect.Parameter.empty, name


@pytest.mark.parametrize("change", ["missing dimension", "duplicate dimension", "unknown evidence", "flag without trigger",
                                    "naive time", "required as not applicable"])
def test_a_malformed_human_review_is_refused_and_nothing_is_recorded(change):
    conn, run = seed("HBR-CORR-003")
    with closing(conn):
        packet = packet_for(conn, run)
        findings = findings_from(CASES["HBR-CORR-003"])
        overrides = {
            "missing dimension": {"findings": findings[:-1]},
            "duplicate dimension": {"findings": findings[:-1] + (findings[0],)},
            "unknown evidence": {"findings": tuple(replace(f, evidence_refs=f.evidence_refs + ("OBS-NOWHERE",))
                                                   for f in findings)},
            "flag without trigger": {"baec_revalidation_required": True, "revalidation_triggers": ()},
            "naive time": {"reviewed_at": datetime(2027, 6, 1)},
            "required as not applicable": {"findings": tuple(
                replace(f, finding=DimensionFinding.NOT_APPLICABLE, evidence_refs=()) if i == 0 else f
                for i, f in enumerate(findings))},
        }[change]
        with pytest.raises(ReviewInconsistentError):
            submit(conn, run, packet, "HBR-CORR-003", **overrides)
        assert p.review_ids_for_candidate(conn, run.candidate.candidate_id) == []


def test_a_changed_context_after_the_packet_is_refused():
    conn, run = seed("HBR-CORR-008")
    with closing(conn):
        packet = packet_for(conn, run)
        o = run.observations[0]
        extra = replace(o, observation_id="OBS-I-LATE", evidence_event_id="EVT-I-LATE")
        p.record_observation(conn, extra, activation_id=ACTIVATION, recorded_at=T, recorded_by=ACTOR)
        with pytest.raises(r.StaleReviewContextError):
            submit(conn, run, packet, "HBR-CORR-008")
        assert p.review_ids_for_candidate(conn, run.candidate.candidate_id) == []
        assert packet_for(conn, run).context_digest != packet.context_digest
        with pytest.raises(r.StaleReviewContextError):
            submit(conn, run, replace(packet, context_digest="0" * 64), "HBR-CORR-008")


# --- fail closed ------------------------------------------------------------------------------------------


def add_second_plan_version(conn, run):
    """Plan v2 of the same plan, activated, with its own copy of the evidence and a candidate."""
    plan2 = replace(run.active_plan.plan, plan_version=2)
    p.record_monitoring_plan(conn, plan2, recorded_at=T, recorded_by=ACTOR)
    reading = run.active_plan.currentness
    p.record_plan_activation(conn, "MP-HBR-001", 2, reading, activation_id="ACT-V2", recorded_at=T, recorded_by=ACTOR)
    obs2 = tuple(replace(o, observation_id=f"{o.observation_id}-V2", plan_version=2) for o in run.observations)
    for o in obs2:
        p.record_observation(conn, o, activation_id="ACT-V2", recorded_at=T, recorded_by=ACTOR)
    signal = SignalCandidate("SC-V2", "MP-HBR-001", 2, obs2, REVIEWER)
    p.record_signal_candidate(conn, signal, recorded_at=T, recorded_by=ACTOR)
    cand = CorrespondenceCandidate("CC-V2", plan2.baec_id, "MP-HBR-001", 2, (signal,))
    p.record_correspondence_candidate(conn, cand, recorded_at=T, recorded_by=ACTOR)
    return obs2


@pytest.mark.parametrize("override", [
    {"monitoring_plan_id": "MP-UNKNOWN"}, {"plan_version": 3}, {"baec_id": "BAEC-OTHER"},
    {"candidate_id": "CC-UNKNOWN"}, {"activation_id": "ACT-UNKNOWN"}, {"candidate_id": "CC-V2"},
    {"activation_id": "ACT-V2"}, {"plan_version": 2}, {"activation_id": "ACT-REFUSED"},
])
def test_wrong_plan_candidate_baec_or_activation_fails_closed(override):
    conn, run = seed("HBR-CORR-003")
    with closing(conn):
        add_second_plan_version(conn, run)
        stale = Engine1CurrentnessReading(run.active_plan.plan.baec_id, StalenessStatus.STALE,
                                          run.active_plan.currentness.record_reference)
        with pytest.raises(Exception):
            p.record_plan_activation(conn, "MP-HBR-001", 1, stale, activation_id="ACT-REFUSED", recorded_at=T,
                                     recorded_by=ACTOR)
        with pytest.raises(r.ReviewContextError):
            packet_for(conn, run, **override)
        with pytest.raises(r.ReviewContextError):
            r.submit_human_review(conn, **{**ref(run), **override}, context_digest="0" * 64,
                                  **human_judgments("HBR-CORR-003"))
        assert p.verify_database(conn)["reviews"] == 0


def test_cross_plan_evidence_in_a_review_is_refused():
    conn, run = seed("HBR-CORR-003")
    with closing(conn):
        obs2 = add_second_plan_version(conn, run)
        packet = packet_for(conn, run)
        findings = tuple(replace(f, evidence_refs=(obs2[0].observation_id,)) if f.finding is DimensionFinding.SUPPORTED
                         else f for f in findings_from(CASES["HBR-CORR-003"]))
        with pytest.raises(ReviewInconsistentError, match="unknown evidence"):
            submit(conn, run, packet, "HBR-CORR-003", findings=findings)
        assert p.verify_database(conn)["reviews"] == 0


def test_corrupted_persistence_raises_and_is_never_classified():
    conn, run = seed("HBR-CORR-003")
    with closing(conn):
        packet = packet_for(conn, run)
        for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'trigger' AND tbl_name = 'observations'"
                                    ).fetchall():
            conn.execute(f"DROP TRIGGER {name}")
        conn.execute("UPDATE observations SET exact_evidence_content = exact_evidence_content || ' ' WHERE observation_id = ?",
                     (run.observations[0].observation_id,))
        with pytest.raises(p.Engine2IntegrityError):
            packet_for(conn, run)
        with pytest.raises(p.Engine2IntegrityError):
            submit(conn, run, packet, "HBR-CORR-003")
        assert conn.execute("SELECT COUNT(*) FROM reviews").fetchone()[0] == 0


def test_a_tampered_review_history_raises_instead_of_being_skipped():
    conn, run = seed("HBR-CORR-003")
    with closing(conn):
        submit(conn, run, packet_for(conn, run), "HBR-CORR-003")
        for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'trigger' AND tbl_name = 'reviews'").fetchall():
            conn.execute(f"DROP TRIGGER {name}")
        conn.execute("UPDATE reviews SET outcome_snapshot = 'NO_CORRESPONDENCE'")
        with pytest.raises(p.Engine2IntegrityError):
            packet_for(conn, run)


# --- AI separation -------------------------------------------------------------------------------------------

CONDITION = condition_input(CORPUS)


def ai_store_with(packet, case_id, raw=None, *, ai_input=None, attempt_id="ATT-1", artifact_id="AIP-1"):
    conn = a.open_ai_audit_database(":memory:")
    ai_input = ai_input or build_correspondence_ai_input(CONDITION, packet.candidate, packet.ledger)
    raw = faithful_output(ai_input, CASES[case_id], {}) if raw is None else raw
    a.record_ai_input(conn, ai_input, recorded_at=T, recorded_by=ACTOR)
    a.record_ai_attempt(conn, attempt_id=attempt_id, input_digest=ai_input.input_digest, provider="replay",
                        model="fixture-replay/v1", requested_at=T, requested_by=ACTOR)
    a.record_ai_response(conn, attempt_id, ModelResponse(raw, "replay", "fixture-replay/v1"), received_at=T,
                         recorded_by=ACTOR)
    validation = a.record_ai_validation(conn, attempt_id, artifact_id=artifact_id, created_at=T, validated_at=T,
                                        validated_by=ACTOR)
    return conn, validation


def test_the_workflow_runs_with_no_ai_store_at_all(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("the human review workflow touched the AI store")
    for name in ("open_ai_audit_database", "load_ai_validation", "load_ai_input", "load_ai_attempt", "load_ai_proposal"):
        monkeypatch.setattr(a, name, refuse)
    conn, run = seed("HBR-CORR-011")
    with closing(conn):
        recorded = submit(conn, run, packet_for(conn, run), "HBR-CORR-011")
        assert recorded.outcome is CorrespondenceOutcome(CASES["HBR-CORR-011"]["expected_correspondence_outcome"])
    for name in ("build_review_packet", "submit_human_review"):
        assert not {n for n in inspect.signature(getattr(r, name)).parameters if "ai" in n.split("_")}


def test_an_accepted_proposal_attaches_explicitly_and_separately():
    conn, run = seed("HBR-CORR-011")
    with closing(conn):
        packet = packet_for(conn, run)
        ai_conn, validation = ai_store_with(packet, "HBR-CORR-011")
        with closing(ai_conn):
            assert validation.status == "ACCEPTED"
            advice = r.attach_advisory_proposal(packet, ai_conn, condition=CONDITION, attempt_id="ATT-1",
                                                artifact_id="AIP-1")
        assert advice.proposal == validation.proposal and advice.proposal.origin.value == "AI_INFERENCE"
        assert (advice.attempt_id, advice.artifact_id) == ("ATT-1", "AIP-1")
        assert packet == packet_for(conn, run)  # the packet holds no AI output and is unchanged
        assert "proposal" not in {f for f in r.ReviewPacket.__dataclass_fields__}
        assert p.verify_database(conn)["reviews"] == 0  # attaching advice records nothing authoritative


def test_a_rejected_validation_is_never_surfaced():
    conn, run = seed("HBR-CORR-011")
    with closing(conn):
        packet = packet_for(conn, run)
        ai_conn, validation = ai_store_with(packet, "HBR-CORR-011", raw="{}")
        with closing(ai_conn):
            assert validation.status == "REJECTED"
            with pytest.raises(r.AdvisoryProposalRefused, match="not ACCEPTED"):
                r.attach_advisory_proposal(packet, ai_conn, condition=CONDITION, attempt_id="ATT-1", artifact_id="AIP-1")
            with pytest.raises(r.AdvisoryProposalRefused):
                r.attach_advisory_proposal(packet, ai_conn, condition=CONDITION, attempt_id="ATT-NONE",
                                           artifact_id="AIP-1")


def test_ai_output_cannot_populate_human_findings():
    conn, run = seed("HBR-CORR-011")
    with closing(conn):
        packet = packet_for(conn, run)
        ai_conn, _ = ai_store_with(packet, "HBR-CORR-011")
        with closing(ai_conn):
            advice = r.attach_advisory_proposal(packet, ai_conn, condition=CONDITION, attempt_id="ATT-1",
                                                artifact_id="AIP-1")
        for findings in (advice.proposal.dimension_proposals, advice.proposal):
            with pytest.raises(ReviewInconsistentError):
                submit(conn, run, packet, "HBR-CORR-011", findings=findings)
        assert p.review_ids_for_candidate(conn, run.candidate.candidate_id) == []
        recorded = submit(conn, run, packet, "HBR-CORR-011")  # the human's own findings, recorded as given
        assert recorded.review.findings == findings_from(CASES["HBR-CORR-011"])
        assert all(isinstance(f, DimensionAssessment) for f in recorded.review.findings)


@pytest.mark.parametrize("mismatch", ["neutral input", "other case input", "wrong artifact", "wrong condition",
                                      "other plan packet"])
def test_a_proposal_for_another_context_is_refused(mismatch):
    conn, run = seed("HBR-CORR-011")
    with closing(conn):
        packet = packet_for(conn, run)
        kwargs = dict(condition=CONDITION, attempt_id="ATT-1", artifact_id="AIP-1")
        if mismatch == "neutral input":
            other = ai_input_for(CORPUS, run)  # same case, different model-visible identifiers: a different input
            ai_conn, _ = ai_store_with(packet, "HBR-CORR-011", ai_input=other)
        elif mismatch == "other case input":
            other_conn, other_run = seed("HBR-CORR-005")
            with closing(other_conn):
                other_packet = packet_for(other_conn, other_run)
            ai_conn, _ = ai_store_with(other_packet, "HBR-CORR-005")
        else:
            ai_conn, _ = ai_store_with(packet, "HBR-CORR-011")
            if mismatch == "wrong artifact":
                kwargs["artifact_id"] = "AIP-OTHER"
            elif mismatch == "wrong condition":
                kwargs["condition"] = replace(CONDITION, baec_id="BAEC-OTHER")
            else:
                add_second_plan_version(conn, run)
                packet = r.build_review_packet(conn, monitoring_plan_id="MP-HBR-001", plan_version=2,
                                               baec_id=packet.baec_id, candidate_id="CC-V2", activation_id="ACT-V2")
        with closing(ai_conn):
            with pytest.raises(r.AdvisoryProposalRefused):
                r.attach_advisory_proposal(packet, ai_conn, **kwargs)


# --- static boundaries ---------------------------------------------------------------------------------------


def _code():
    return re.sub(r'"""[\s\S]*?"""', "", MODULE.read_text(encoding="utf-8"))


def test_no_provider_network_credential_account_state_or_outreach_path():
    code = _code()
    for forbidden in ("anthropic", "import requests", "urllib", "socket", "http", "subprocess", "os.environ", "getenv",
                      "API_KEY", "AUTH_TOKEN", "AccountState", "account_state", "outreach", "contact", "opportunit",
                      "email", "send", "datetime.now", "utcnow", "time.time", "latest", "baec_app.ai", "baec_app.data",
                      "open_engine2_database", "open_ai_audit_database", "sqlite3.connect", "propose_correspondence"):
        assert forbidden not in code, forbidden
    public = sorted(n for n, f in inspect.getmembers(r, inspect.isfunction) if f.__module__ == r.__name__
                    and not n.startswith("_"))
    assert public == ["attach_advisory_proposal", "build_review_packet", "submit_human_review"]


def test_the_application_layer_writes_only_through_stage_d():
    code = _code()
    assert not re.search(r"\b(INSERT|UPDATE|DELETE|REPLACE|DROP|ALTER|CREATE)\b", code)
    selects = re.findall(r"SELECT [a-z_]+ FROM [a-z_]+", code)
    assert sorted(selects) == sorted(["SELECT observation_id FROM observations",
                                      "SELECT superseded_observation_id FROM supersessions",
                                      "SELECT measurement_id FROM derived_measurements",
                                      "SELECT item_id FROM source_refusals"])
    assert code.count("store.record_") == 1 and "store.record_review(" in code
    assert "ai_store.record_" not in code


def test_no_classifier_or_domain_rule_is_restated():
    code = _code()
    for restated in ("DimensionFinding.", "CONTRADICTED", "SUPPORTED", "UNRESOLVED", "NOT_APPLICABLE",
                     "SufficiencyFinding.", "CorrespondenceOutcome.", "classify_correspondence", "required_dimensions",
                     "usable_observation_ids -", "superseded_observation_id not in", "PROPOSED_"):
        assert restated not in code, restated
    assert "ledger.usable_observation_ids" in code and "ledger.usable_measurement_ids" in code
