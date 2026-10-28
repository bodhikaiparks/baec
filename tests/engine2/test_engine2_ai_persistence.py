"""Stage G: the separate AI audit store. Offline only; no model, provider, or network call."""

import copy
import inspect
import json
import re
import sqlite3
from contextlib import closing
from datetime import datetime

import pytest

from baec_app.engine2 import ai_persistence as a
from baec_app.engine2 import persistence as authoritative
from baec_app.engine2.ai import ModelResponse, OUTPUT_SCHEMA_VERSION, PROMPT_VERSION
from tests.engine2.ai_replay import ai_case, faithful_output, overreaching_output
from tests.engine2.corpus_adapter import REPO, load_corpus, run_case

CORPUS = load_corpus()
CASES = {c["case_id"]: c for c in CORPUS["cases"]}
T0 = datetime.fromisoformat("2027-06-02T00:00:00+00:00")
T1 = datetime.fromisoformat("2027-06-03T00:00:00+00:00")
ACTOR = "REVIEWER-AI-AUDIT"


@pytest.fixture
def conn():
    with closing(a.open_ai_audit_database(":memory:")) as connection:
        yield connection


def _case(case_id="HBR-CORR-003"):
    ai_input, ids = ai_case(CORPUS, run_case(CORPUS, CASES[case_id]))
    return ai_input, ids, CASES[case_id]


def _flow(conn, ai_input, raw, attempt_id="ATT-1", artifact_id="AIP-1", record_input=True):
    if record_input:
        a.record_ai_input(conn, ai_input, recorded_at=T0, recorded_by=ACTOR)
    a.record_ai_attempt(conn, attempt_id=attempt_id, input_digest=ai_input.input_digest, provider="replay",
                        model="fixture-replay/v1", requested_at=T0, requested_by=ACTOR, producer_commit="c6d2484")
    a.record_ai_response(conn, attempt_id, ModelResponse(raw, "replay", "fixture-replay/v1"), received_at=T0, recorded_by=ACTOR)
    return a.record_ai_validation(conn, attempt_id, artifact_id=artifact_id, created_at=T0, validated_at=T1, validated_by=ACTOR)


def _good(conn, case_id="HBR-CORR-003"):
    ai_input, ids, case = _case(case_id)
    return ai_input, _flow(conn, ai_input, faithful_output(ai_input, case, ids))


def _tamper(conn, sql, params=(), *, checks=True, fks=True):
    table = sql.split()[1] if sql.startswith("UPDATE") else sql.split()[2]
    for op in ("update", "delete", "replace"):
        conn.execute(f"DROP TRIGGER IF EXISTS {table}_no_{op}")
    if not checks:
        conn.execute("PRAGMA ignore_check_constraints = ON")
    if not fks:
        conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute(sql, params)
    conn.execute("PRAGMA ignore_check_constraints = OFF")
    conn.execute("PRAGMA foreign_keys = ON")


# --- schema and isolation ---------------------------------------------------------------------------


def test_ai_store_is_a_separate_versioned_database(conn):
    assert dict(conn.execute("SELECT key, value FROM engine2_ai_meta")) == {"engine": "BAEC_ENGINE_2_AI", "schema_version": "1"}
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert tables == set(a.TABLES) and len(tables) == 7
    assert not tables & set(authoritative.TABLES)


def test_each_store_refuses_the_other(tmp_path):
    auth_path, ai_path = str(tmp_path / "engine2.sqlite3"), str(tmp_path / "engine2_ai.sqlite3")
    authoritative.open_engine2_database(auth_path).close()
    a.open_ai_audit_database(ai_path).close()
    with pytest.raises(a.AIStoreSchemaError):
        a.open_ai_audit_database(auth_path)
    with pytest.raises(authoritative.Engine2SchemaError):
        authoritative.open_engine2_database(ai_path)
    with closing(authoritative.open_engine2_database(auth_path)) as c:
        assert len(authoritative.verify_database(c)) == 30


@pytest.mark.parametrize("tamper", ["PRAGMA user_version = 2",
                                    "UPDATE engine2_ai_meta SET value = 'BAEC_ENGINE_2' WHERE key = 'engine'"])
def test_unsupported_ai_schema_fails_closed(tmp_path, tamper):
    path = str(tmp_path / "ai.sqlite3")
    with closing(a.open_ai_audit_database(path)) as c:
        c.execute("DROP TRIGGER engine2_ai_meta_no_update")
        c.execute(tamper)
    with pytest.raises(a.AIStoreSchemaError):
        a.open_ai_audit_database(path)


def test_ai_store_has_no_authority_path_or_oracle_access():
    code = re.sub(r'"""[\s\S]*?"""', "", (REPO / "baec_app/engine2/ai_persistence.py").read_text(encoding="utf-8"))
    for forbidden in ("HumanCorrespondenceReview", "DimensionAssessment", "CorrespondenceOutcome", "classify_correspondence",
                      "from .persistence", "from .classification", "baec_app.data", "baec_app.ai", "AccountState",
                      "import anthropic", "requests", "urllib", "socket", "datetime.now", "utcnow", "time.time",
                      "OR REPLACE", "ON CONFLICT", "pickle", "corpus", "HBR-CORR", "subprocess", "git "):
        assert forbidden not in code, forbidden
    names = [n for n, _ in inspect.getmembers(a, inspect.isfunction) if n.startswith(("record_", "load_", "open_"))]
    for name in names:
        assert not re.search(r"delete|update|accept_as|convert|apply|approve|opportunity|contact", name), name
        assert "status" not in inspect.signature(getattr(a, name)).parameters, name


# --- inputs, attempts, responses -----------------------------------------------------------------------------


def test_input_round_trips_exactly_and_duplicates_are_refused(conn):
    ai_input, _, _ = _case()
    a.record_ai_input(conn, ai_input, recorded_at=T0, recorded_by=ACTOR)
    assert a.load_ai_input(conn, ai_input.input_digest) == ai_input
    stored = conn.execute("SELECT canonical_json, data_authority FROM ai_inputs").fetchone()
    assert stored == (ai_input.canonical_json, "INPUT_SNAPSHOT")
    with pytest.raises(a.AIDuplicateError):
        a.record_ai_input(conn, ai_input, recorded_at=T1, recorded_by=ACTOR)


def test_attempt_carries_locked_versions_and_reproducibility_metadata(conn):
    ai_input, _, _ = _case()
    a.record_ai_input(conn, ai_input, recorded_at=T0, recorded_by=ACTOR)
    a.record_ai_attempt(conn, attempt_id="ATT-1", input_digest=ai_input.input_digest, provider="replay", model="m1",
                        requested_at=T0, requested_by=ACTOR, producer_commit="c6d2484b20124c8d630ef182841ea16e1b7d5d5f",
                        provider_request_ref="req-0001")
    attempt = a.load_ai_attempt(conn, "ATT-1")
    assert (attempt.prompt_version, attempt.output_schema_version) == (PROMPT_VERSION, OUTPUT_SCHEMA_VERSION)
    assert (attempt.monitoring_plan_id, attempt.plan_version, attempt.requested_at) == ("MP-HBR-001", 1, T0)
    with pytest.raises(a.AIDuplicateError):
        a.record_ai_attempt(conn, attempt_id="ATT-1", input_digest=ai_input.input_digest, provider="replay", model="m1",
                            requested_at=T0, requested_by=ACTOR)
    with pytest.raises(a.AIWriteRefused):
        a.record_ai_attempt(conn, attempt_id="ATT-2", input_digest=ai_input.input_digest, provider="replay", model="m1",
                            requested_at=T0, requested_by=ACTOR, producer_commit="not-a-commit")
    with pytest.raises(a.AINotFoundError):
        a.record_ai_attempt(conn, attempt_id="ATT-3", input_digest="0" * 64, provider="replay", model="m1",
                            requested_at=T0, requested_by=ACTOR)
    with pytest.raises(a.AIWriteRefused):
        a.record_ai_attempt(conn, attempt_id="ATT-4", input_digest=ai_input.input_digest, provider="replay", model="m1",
                            requested_at=datetime(2027, 6, 2), requested_by=ACTOR)
    assert "api_key" not in {c[1] for c in conn.execute("PRAGMA table_info(ai_attempts)")}


def test_attempt_may_exist_without_response(conn):
    ai_input, _, _ = _case()
    a.record_ai_input(conn, ai_input, recorded_at=T0, recorded_by=ACTOR)
    a.record_ai_attempt(conn, attempt_id="ATT-1", input_digest=ai_input.input_digest, provider="replay", model="m",
                        requested_at=T0, requested_by=ACTOR)
    with pytest.raises(a.AINotFoundError):
        a.record_ai_validation(conn, "ATT-1", artifact_id="AIP-1", created_at=T0, validated_at=T1, validated_by=ACTOR)
    assert a.verify_ai_database(conn)["ai_responses"] == 0


def test_response_text_is_stored_exactly_without_repair(conn):
    ai_input, ids, case = _case()
    raw = "  " + faithful_output(ai_input, case, ids).replace('"plan_version": 1', '"plan_version": 1, "plan_version": 1') + "\n"
    record = _flow(conn, ai_input, raw)
    text, canonical, authority = conn.execute("SELECT raw_text, canonical_json, data_authority FROM ai_responses").fetchone()
    assert text == raw and canonical is None and authority == "UNTRUSTED_MODEL_RESPONSE"
    assert record.status == "REJECTED"
    assert a.load_ai_response(conn, "ATT-1").raw_text == raw


# --- validation: Stage F decides ---------------------------------------------------------------------------------


def _mutated(case_id, fn):
    ai_input, ids, case = _case(case_id)
    data = json.loads(faithful_output(ai_input, case, ids))
    out = fn(data)
    return ai_input, json.dumps(data) if out is None else out


def _dim(d, label):
    return next(p for p in d["dimension_proposals"] if p["dimension"] == label)


REJECTED = {
    "malformed": lambda d: "{not json",
    "wrong_plan_version": lambda d: d.update(plan_version=2),
    "unknown_dimension": lambda d: _dim(d, "Entity match").update(dimension="Price match"),
    "duplicate_dimension": lambda d: _dim(d, "Unit match").update(dimension="Entity match"),
    "fabricated_excerpt": lambda d: _dim(d, "Entity match")["evidence"][0].update(excerpt="NorthStar will double prices."),
    "invalid_evidence_reference": lambda d: _dim(d, "Entity match")["evidence"][0].update(observation_id="OBS-99"),
    "required_not_applicable": lambda d: _dim(d, "Timing match").update(proposal="PROPOSED_NOT_APPLICABLE", evidence=[]),
    "outcome_field": lambda d: d.update(correspondence_outcome="HUMAN_VERIFIED_CORRESPONDENCE"),
    "purchase_probability_field": lambda d: d.update(purchase_probability=0.8),
    "prohibited_generated_text": lambda d: _dim(d, "Entity match").update(rationale="The buyer is evaluating alternatives."),
    "altered_acknowledgement": lambda d: d.update(prohibited_inference_acknowledgement="ok"),
}


@pytest.mark.parametrize("name", sorted(REJECTED))
def test_rejected_response_is_audited_without_a_proposal(conn, name):
    ai_input, raw = _mutated("HBR-CORR-003", REJECTED[name])
    record = _flow(conn, ai_input, raw)
    assert record.status == "REJECTED" and record.proposal is None and record.error_category == "ProposalRejected"
    assert conn.execute("SELECT COUNT(*) FROM ai_proposals").fetchone()[0] == 0
    loaded = a.load_ai_validation(conn, "ATT-1")
    assert loaded.status == "REJECTED" and loaded.proposal is None
    assert a.load_ai_response(conn, "ATT-1").raw_text == raw
    assert [e.event_type for e in a.ai_audit_events(conn)][-1] == "AI_VALIDATION_REJECTED"
    a.verify_ai_database(conn)


def test_accepted_validation_stores_the_stage_f_artifact(conn):
    ai_input, record = _good(conn)
    assert record.status == "ACCEPTED"
    proposal = a.load_ai_proposal(conn, "AIP-1")
    assert proposal == record.proposal and proposal.origin.value == "AI_INFERENCE"
    row = conn.execute("SELECT origin, data_authority, proposal_sha256, canonical_json FROM ai_proposals").fetchone()
    text, digest = a.proposal_digest(proposal)
    assert row == ("AI_INFERENCE", "AI_INFERENCE_PROPOSAL", digest, text)
    assert [e.event_type for e in a.ai_audit_events(conn)][-2:] == ["AI_VALIDATION_ACCEPTED", "AI_PROPOSAL_RECORDED"]
    with pytest.raises(a.AIDuplicateError):
        a.record_ai_validation(conn, "ATT-1", artifact_id="AIP-2", created_at=T0, validated_at=T1, validated_by=ACTOR)
    columns = {c[1] for c in conn.execute("PRAGMA table_info(ai_proposals)")}
    assert not columns & {"outcome", "correspondence_outcome", "sufficiency", "opportunity", "lead", "contact"}


def test_audit_details_stay_minimal(conn):
    _good(conn)
    for event in a.ai_audit_events(conn):
        details = json.dumps(event.details)
        assert "NorthStar" not in details and "rationale" not in details and len(details) < 300


# --- multiple attempts and history ---------------------------------------------------------------------------------


def test_retry_is_a_new_attempt_and_rejected_history_is_untouched(conn):
    ai_input, ids, case = _case()
    bad = faithful_output(ai_input, case, ids).replace('"plan_version": 1', '"plan_version": 2')
    assert _flow(conn, ai_input, bad, "ATT-A", "AIP-A").status == "REJECTED"
    before = {t: conn.execute(f"SELECT * FROM {t} WHERE attempt_id = 'ATT-A'").fetchall()
              for t in ("ai_attempts", "ai_responses", "ai_validations")}
    good = _flow(conn, ai_input, faithful_output(ai_input, case, ids), "ATT-B", "AIP-B", record_input=False)
    assert good.status == "ACCEPTED"
    assert a.attempt_ids_for_input(conn, ai_input.input_digest) == ["ATT-A", "ATT-B"]
    assert {t: conn.execute(f"SELECT * FROM {t} WHERE attempt_id = 'ATT-A'").fetchall() for t in before} == before
    assert a.load_ai_validation(conn, "ATT-A").status == "REJECTED"
    proposal_row = conn.execute("SELECT * FROM ai_proposals WHERE artifact_id = 'AIP-B'").fetchall()
    _flow(conn, ai_input, faithful_output(ai_input, case, ids), "ATT-C", "AIP-C", record_input=False)
    assert conn.execute("SELECT * FROM ai_proposals WHERE artifact_id = 'AIP-B'").fetchall() == proposal_row
    assert a.load_ai_attempt(conn, "ATT-A").provider == "replay" and a.load_ai_attempt(conn, "ATT-A").requested_at == T0
    a.verify_ai_database(conn)


# --- append-only and transactions --------------------------------------------------------------------------------------


@pytest.mark.parametrize("table", ["ai_inputs", "ai_attempts", "ai_responses", "ai_validations", "ai_proposals",
                                   "ai_audit_events", "engine2_ai_meta"])
def test_every_ai_table_refuses_update_delete_and_replace(conn, table):
    _good(conn)
    column = conn.execute(f"SELECT name FROM pragma_table_info('{table}') WHERE pk = 0 LIMIT 1").fetchone()[0]
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        conn.execute(f"UPDATE {table} SET {column} = {column}")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        conn.execute(f"DELETE FROM {table}")
    with pytest.raises(sqlite3.DatabaseError, match="cannot be replaced"):
        conn.execute(f"INSERT OR REPLACE INTO {table} SELECT * FROM {table} LIMIT 1")


def _fail_on(event_type, original):
    def wrapper(connection, kind, *args):
        if kind == event_type:
            raise RuntimeError("injected failure")
        return original(connection, kind, *args)
    return wrapper


@pytest.mark.parametrize("event,tables", [
    ("AI_INPUT_RECORDED", ("ai_inputs",)),
    ("AI_ATTEMPT_RECORDED", ("ai_attempts",)),
    ("AI_RESPONSE_RECORDED", ("ai_responses",)),
    ("AI_VALIDATION_REJECTED", ("ai_validations",)),
    ("AI_VALIDATION_ACCEPTED", ("ai_validations", "ai_proposals")),
    ("AI_PROPOSAL_RECORDED", ("ai_validations", "ai_proposals")),
])
def test_each_write_and_its_audit_event_are_atomic(conn, monkeypatch, event, tables):
    ai_input, ids, case = _case()
    raw = faithful_output(ai_input, case, ids)
    if event == "AI_VALIDATION_REJECTED":
        raw = raw.replace('"plan_version": 1', '"plan_version": 2')
    monkeypatch.setattr(a, "_insert_audit_event", _fail_on(event, a._insert_audit_event))
    with pytest.raises(RuntimeError):
        _flow(conn, ai_input, raw)
    for table in tables:
        assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0, table
    assert not conn.in_transaction


# --- tampering fails closed ----------------------------------------------------------------------------------------------

TAMPER = {
    "input_json": ("UPDATE ai_inputs SET canonical_json = replace(canonical_json, '12%', '13%')", {}),
    "input_digest_column": ("UPDATE ai_inputs SET input_digest = ?", {"params": ("f" * 64,), "fks": False}),
    "input_plan_version_column": ("UPDATE ai_inputs SET plan_version = 2", {"fks": False}),
    "response_payload": ("UPDATE ai_responses SET raw_text = raw_text || ' '", {}),
    "response_digest": ("UPDATE ai_responses SET response_sha256 = ?", {"params": ("0" * 64,), "fks": False}),
    "validation_status": ("UPDATE ai_validations SET status = 'REJECTED', error_category = 'X', error_summary = 'X'",
                          {"fks": False}),
    "accepted_missing_proposal": ("DELETE FROM ai_proposals", {}),
    "proposal_json": ("UPDATE ai_proposals SET canonical_json = replace(canonical_json, 'PROPOSED_SUPPORTED', 'PROPOSED_UNRESOLVED')", {}),
    "proposal_digest": ("UPDATE ai_proposals SET proposal_sha256 = ?", {"params": ("0" * 64,)}),
    "proposal_origin": ("UPDATE ai_proposals SET origin = 'BUYER_FACT'", {"checks": False}),
    "proposal_candidate": ("UPDATE ai_proposals SET candidate_id = 'CANDIDATE-9'", {}),
    "orphan_attempt": ("DELETE FROM ai_inputs", {"fks": False}),
    "orphan_response": ("DELETE FROM ai_attempts", {"fks": False}),
    "audit_relationship": ("DELETE FROM ai_audit_events WHERE event_type = 'AI_PROPOSAL_RECORDED'", {}),
}


@pytest.mark.parametrize("name", sorted(TAMPER))
def test_tampered_ai_history_fails_closed(conn, name):
    _good(conn)
    sql, opts = TAMPER[name]
    _tamper(conn, sql, opts.get("params", ()), checks=opts.get("checks", True), fks=opts.get("fks", True))
    with pytest.raises((a.AIStoreIntegrityError, a.AINotFoundError)):
        a.verify_ai_database(conn)


def test_rejected_validation_linked_to_a_proposal_fails_closed(conn):
    ai_input, ids, case = _case()
    good = faithful_output(ai_input, case, ids)
    _flow(conn, ai_input, good, "ATT-A", "AIP-A")
    _flow(conn, ai_input, good.replace('"plan_version": 1', '"plan_version": 2'), "ATT-B", "AIP-B", record_input=False)
    _tamper(conn, "INSERT INTO ai_proposals SELECT 'AIP-X', 'ATT-B', validation_status, data_authority, origin, input_digest, "
                  "monitoring_plan_id, plan_version, baec_id, candidate_id, prompt_version, output_schema_version, provider, "
                  "model, created_at, canonical_json, proposal_sha256, recorded_at, recorded_by FROM ai_proposals "
                  "WHERE artifact_id = 'AIP-A'", fks=False)
    with pytest.raises(a.AIStoreIntegrityError):
        a.verify_ai_database(conn)


# --- provider/model identity: one attempt, one exact (provider, model) pair -----------------------------------------

def _attempt_only(conn, provider="P1", model="M1", attempt_id="ATT-1", record_input=True):
    ai_input, ids, case = _case()
    if record_input:
        a.record_ai_input(conn, ai_input, recorded_at=T0, recorded_by=ACTOR)
    a.record_ai_attempt(conn, attempt_id=attempt_id, input_digest=ai_input.input_digest, provider=provider, model=model,
                        requested_at=T0, requested_by=ACTOR)
    return ai_input, faithful_output(ai_input, case, ids)


@pytest.mark.parametrize("provider,model", [("P2", "M1"), ("P1", "M2"), ("p1", "M1"), ("P1", "M1 ")])
def test_response_naming_another_provider_or_model_is_refused_at_write(conn, provider, model):
    _, raw = _attempt_only(conn)
    with pytest.raises(a.AIWriteRefused, match="exact equality"):
        a.record_ai_response(conn, "ATT-1", ModelResponse(raw, provider, model), received_at=T0, recorded_by=ACTOR)
    assert conn.execute("SELECT COUNT(*) FROM ai_responses").fetchone()[0] == 0
    assert "AI_RESPONSE_RECORDED" not in [e.event_type for e in a.ai_audit_events(conn)]


def test_exact_provider_model_pair_flows_through_to_the_proposal(conn):
    _, raw = _attempt_only(conn)
    a.record_ai_response(conn, "ATT-1", ModelResponse(raw, "P1", "M1"), received_at=T0, recorded_by=ACTOR)
    record = a.record_ai_validation(conn, "ATT-1", artifact_id="AIP-1", created_at=T0, validated_at=T1, validated_by=ACTOR)
    assert (record.proposal.provider, record.proposal.model) == ("P1", "M1")
    loaded = a.load_ai_proposal(conn, "AIP-1")
    assert (loaded.provider, loaded.model) == (a.load_ai_attempt(conn, "ATT-1").provider, a.load_ai_attempt(conn, "ATT-1").model)


def test_different_attempts_on_one_input_may_use_different_models(conn):
    ai_input, raw = _attempt_only(conn, "P1", "M1", "ATT-A")
    _attempt_only(conn, "P1", "M2", "ATT-B", record_input=False)
    a.record_ai_response(conn, "ATT-A", ModelResponse(raw, "P1", "M1"), received_at=T0, recorded_by=ACTOR)
    a.record_ai_response(conn, "ATT-B", ModelResponse(raw, "P1", "M2"), received_at=T0, recorded_by=ACTOR)
    a.record_ai_validation(conn, "ATT-A", artifact_id="AIP-A", created_at=T0, validated_at=T1, validated_by=ACTOR)
    a.record_ai_validation(conn, "ATT-B", artifact_id="AIP-B", created_at=T0, validated_at=T1, validated_by=ACTOR)
    assert [a.load_ai_proposal(conn, x).model for x in ("AIP-A", "AIP-B")] == ["M1", "M2"]
    assert a.attempt_ids_for_input(conn, ai_input.input_digest) == ["ATT-A", "ATT-B"]
    a.verify_ai_database(conn)


def _rehash_proposal(conn, old, new):
    """Edit the proposal JSON and recompute its digest, so only the cross-record identity check can catch it."""
    import hashlib
    text = conn.execute("SELECT canonical_json FROM ai_proposals").fetchone()[0].replace(old, new)
    _tamper(conn, "UPDATE ai_proposals SET canonical_json = ?, proposal_sha256 = ?",
            (text, hashlib.sha256(text.encode("utf-8")).hexdigest()))


IDENTITY_TAMPER = {
    "A_response_provider": (lambda c: _tamper(c, "UPDATE ai_responses SET provider = 'P9'"), ("response", "validation")),
    "B_response_model": (lambda c: _tamper(c, "UPDATE ai_responses SET model = 'M9'"), ("response", "validation")),
    "C_attempt_provider": (lambda c: _tamper(c, "UPDATE ai_attempts SET provider = 'P9'"), ("response", "validation")),
    "D_attempt_model": (lambda c: _tamper(c, "UPDATE ai_attempts SET model = 'M9'"), ("response", "validation")),
    "E_proposal_provider_column": (lambda c: _tamper(c, "UPDATE ai_proposals SET provider = 'P9'"), ("proposal",)),
    "F_proposal_model_column": (lambda c: _tamper(c, "UPDATE ai_proposals SET model = 'M9'"), ("proposal",)),
    "E_proposal_provider_json": (lambda c: _rehash_proposal(c, '"provider":"P1"', '"provider":"P9"'), ("proposal",)),
    "F_proposal_model_json": (lambda c: _rehash_proposal(c, '"model":"M1"', '"model":"M9"'), ("proposal",)),
}


@pytest.mark.parametrize("name", sorted(IDENTITY_TAMPER))
def test_provider_model_disagreement_fails_closed(conn, name):
    _, raw = _attempt_only(conn)
    a.record_ai_response(conn, "ATT-1", ModelResponse(raw, "P1", "M1"), received_at=T0, recorded_by=ACTOR)
    a.record_ai_validation(conn, "ATT-1", artifact_id="AIP-1", created_at=T0, validated_at=T1, validated_by=ACTOR)
    tamper, loaders = IDENTITY_TAMPER[name]
    tamper(conn)
    direct = {"response": lambda: a.load_ai_response(conn, "ATT-1"),
              "validation": lambda: a.load_ai_validation(conn, "ATT-1"),
              "proposal": lambda: a.load_ai_proposal(conn, "AIP-1")}
    for loader in loaders:
        with pytest.raises(a.AIStoreIntegrityError):
            direct[loader]()
    with pytest.raises(a.AIStoreIntegrityError):
        a.verify_ai_database(conn)


def test_identity_mismatch_is_an_integrity_failure_not_a_rejection(conn):
    _, raw = _attempt_only(conn)
    a.record_ai_response(conn, "ATT-1", ModelResponse(raw, "P1", "M1"), received_at=T0, recorded_by=ACTOR)
    _tamper(conn, "UPDATE ai_responses SET model = 'M9'")
    with pytest.raises(a.AIStoreIntegrityError, match="provider/model"):
        a.record_ai_validation(conn, "ATT-1", artifact_id="AIP-1", created_at=T0, validated_at=T1, validated_by=ACTOR)
    assert conn.execute("SELECT COUNT(*) FROM ai_validations").fetchone()[0] == 0
