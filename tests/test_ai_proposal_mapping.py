"""Phase 7D: persisted artifact -> AI_DRAFT proposal (eligibility E1-E8, mapping, persistence, boundaries).

docs/PHASE7_HUMAN_AUTHORIZED_AI_PROPOSAL_BRIDGE_DESIGN.md §6. All artifacts are test-only fixtures produced
offline by the real Phase 6 extraction service with the FakeProvider (tests/mapping_builders.py, design D15).
"""

from __future__ import annotations

import json
from datetime import timedelta

import pytest

from baec_app.ai.canonical import canonical_json, sha256_text
from baec_app.application.ai_proposal_mapping import (
    ArtifactNotEligible,
    map_artifact_to_proposal_content,
    proposal_id_for,
    verify_artifact,
)
from baec_app.application.composition import build_command_facade
from baec_app.application.errors import AiDraftNotPermitted, ProposalNotAuthoritative
from baec_app.application.proposals import ConfirmationProposal, ProposalOrigin
from baec_app.data.ai_provenance import AiProvenanceStore
from baec_app.data.database import AI_PROVENANCE_TABLES, BRIDGE_TABLES, DATA_TABLES
from baec_app.data.proposal_bridge import AiProposalRecord
from baec_app.data.repository import Repository
from baec_app.domain.baec_rules import create_confirmed_baec_record
from baec_app.domain.enums import AuthorizationAction, ProvenanceCategory
from baec_app.domain.models import EvidenceExcerpt, HumanAuthorization
from tests.ai_builders import THRESHOLD_TEXT, response
from tests.application_builders import FixedClock, SequentialIds
from tests.bridge_builders import decision_record, grant_values, insert_row, revision_record
from tests.builders import candidate
from tests.mapping_builders import (  # noqa: F401
    ACC,
    ACTOR,
    INT,
    INT_OTHER_ACCOUNT,
    INT_SAME_TEXT,
    MAPPED_AT,
    MODEL,
    OTHER_ACC,
    mapping,
)
from tests.persistence_builders import dump, tamper

EXCERPT = "If our supplier raises pricing by more than 10% at renewal, we would reopen the evaluation."


def refused(mapping, artifact_id, code):
    before = dump(mapping.connection)
    with pytest.raises(ArtifactNotEligible) as raised:
        mapping.map(artifact_id)
    assert raised.value.code == code and str(raised.value) == code  # a closed code; never artifact text
    assert dump(mapping.connection) == before  # a refusal writes nothing


def content_of(result) -> dict:
    return json.loads(result.proposal.content)


# --- a valid artifact maps -----------------------------------------------------------------


def test_an_eligible_artifact_maps_to_exactly_one_ai_draft_proposal_and_nothing_else(mapping):
    artifact_id = mapping.artifact()
    before = dump(mapping.connection)
    result = mapping.map(artifact_id)
    after = dump(mapping.connection)
    assert result.created is True
    assert {t for t in DATA_TABLES if after[t] != before[t]} == {"ai_proposals"}
    assert len(after["ai_proposals"]) == 1
    proposal = result.proposal
    assert proposal == mapping.bridge.get_proposal(proposal.proposal_id)
    assert (proposal.origin, proposal.mapping_version, proposal.content_version) == (
        "AI_DRAFT", "baec-ai-proposal-mapping/v1", "baec-ai-proposal-content/v1")
    assert (proposal.account_id, proposal.interaction_id, proposal.artifact_id) == (ACC, INT, artifact_id)
    assert proposal.proposal_id == proposal_id_for(artifact_id)
    assert (proposal.created_by, proposal.created_at) == (ACTOR, MAPPED_AT)
    assert proposal.proposal_digest == sha256_text(proposal.content)


def test_mapping_creates_no_review_grant_consumption_baec_authorization_or_state_change(mapping):
    artifact_id = mapping.artifact()
    before = dump(mapping.connection)
    mapping.map(artifact_id)
    after = dump(mapping.connection)
    for table in DATA_TABLES:
        if table != "ai_proposals":
            assert after[table] == before[table], table
    for table in ("baec_records", "human_authorizations", "account_state_transitions", "dormancy_judgments",
                  "interaction_evidence") + tuple(t for t in BRIDGE_TABLES if t != "ai_proposals"):
        assert mapping.count(table) == 0, table
    assert mapping.connection.execute("SELECT state FROM accounts").fetchall() == [(None,), (None,)]


def test_provenance_is_reconstructed_through_the_artifact_lineage_not_copied(mapping):
    result = mapping.map(mapping.artifact())
    proposal, snapshot = result.proposal, content_of(result)["artifact"]
    assert snapshot == {"artifact_id": proposal.artifact_id, "artifact_digest": proposal.artifact_digest}
    # proposal -> artifact_id -> immutable Phase 6 provenance
    store = AiProvenanceStore(mapping.connection)
    artifact = store.get_artifact(proposal.artifact_id)
    run, outcome = store.get_run(artifact.ai_run_id), store.get_result(artifact.ai_run_id)
    assert artifact.artifact_digest == proposal.artifact_digest
    assert (artifact.account_id, artifact.interaction_id) == (proposal.account_id, proposal.interaction_id)
    assert (run.requested_model, outcome.response_model) == ("FIXTURE-model-not-a-real-invocation",) * 2
    assert (run.prompt_version, run.validation_version, run.output_schema_version) == (
        "baec-extraction-prompt/v2", "baec-extraction-validation/v2", "baec-extraction-output/v1")
    for copied in (run.prompt_digest, run.request_digest, run.requested_model, run.ai_run_id, run.prompt_version):
        assert copied not in proposal.content


# --- AI material stays AI material ---------------------------------------------------------


def test_excerpts_are_verbatim_suggestions_and_speakers_stay_inference(mapping):
    content = content_of(mapping.map(mapping.artifact()))
    excerpts = content["ai_suggested_excerpts"]
    assert [e["text"] for e in excerpts] == [EXCERPT, "We would not switch for anything under 10%."]
    assert all(e["text"] in THRESHOLD_TEXT for e in excerpts)
    assert all(set(e) == {"excerpt_id", "text", "ai_attributed_speaker"} for e in excerpts)
    assert all(e["ai_attributed_speaker"] == {"value": "buyer", "provenance": "AI_INFERENCE"} for e in excerpts)


def test_hypotheses_normalizations_and_uncertainties_stay_non_authoritative(mapping):
    content = content_of(mapping.map(mapping.artifact()))
    assert [h["criterion"] for h in content["ai_criterion_hypotheses"]] == [
        "PRESENT_NON_EVALUATION", "PROSPECTIVE_CONDITION", "BUYER_ARTICULATION", "EVALUATION_LINKAGE"]
    for hypothesis in content["ai_criterion_hypotheses"]:
        assert set(hypothesis) == {"criterion", "ai_status", "excerpt_refs", "explanation", "provenance"}
        assert hypothesis["ai_status"] == "supported" and hypothesis["provenance"] == "AI_INFERENCE"
    assert content["ai_normalized_condition"] == {
        "value": "A price increase of more than 10% at renewal.", "provenance": "AI_INFERENCE"}
    assert content["ai_normalized_evaluation_link"]["provenance"] == "AI_INFERENCE"
    assert content["ai_uncertainties"] == {
        "values": ["Whether the buyer's statement reflects the whole buying group is not stated."],
        "provenance": "AI_INFERENCE"}


def test_null_normalizations_and_empty_uncertainties_map_as_ai_inference(mapping):
    content = content_of(mapping.map(mapping.artifact(normalized_condition=None, normalized_evaluation_link=None,
                                                      uncertainties=[])))
    assert content["ai_normalized_condition"] == {"value": None, "provenance": "AI_INFERENCE"}
    assert content["ai_normalized_evaluation_link"] == {"value": None, "provenance": "AI_INFERENCE"}
    assert content["ai_uncertainties"] == {"values": [], "provenance": "AI_INFERENCE"}


def test_a_seller_attribution_or_unsupported_hypothesis_is_carried_only_as_inference(mapping):
    from tests.ai_builders import hypotheses
    artifact_id = mapping.artifact(
        excerpts=[{"excerpt_id": "e1", "source_interaction_id": INT, "text": EXCERPT, "attributed_speaker": "seller"}],
        criterion_hypotheses=hypotheses(status="not_supported"))
    content = content_of(mapping.map(artifact_id))
    assert content["ai_suggested_excerpts"][0]["ai_attributed_speaker"] == {"value": "seller",
                                                                              "provenance": "AI_INFERENCE"}
    assert {h["ai_status"] for h in content["ai_criterion_hypotheses"]} == {"not_supported"}
    text = result_text = mapping.bridge.get_proposal(proposal_id_for(artifact_id)).content
    for authoritative in ("SELLER_OBSERVATION", "BUYER_FACT", '"NOT_MET"', '"MET"'):
        assert authoritative not in result_text, authoritative
    assert text


# --- determinism ---------------------------------------------------------------------------


def test_pure_mapping_is_byte_identical_across_repeated_calls_and_reverification(mapping):
    artifact_id = mapping.artifact()
    store, repository = AiProvenanceStore(mapping.connection), Repository(mapping.connection)
    first = verify_artifact(artifact_id, provenance=store, repository=repository)
    second = verify_artifact(artifact_id, provenance=store, repository=repository)
    contents = {map_artifact_to_proposal_content(first) for _ in range(5)} | {map_artifact_to_proposal_content(second)}
    assert len(contents) == 1
    content = contents.pop()
    assert canonical_json(json.loads(content)) == content
    assert mapping.map(artifact_id).proposal.content == content


def test_the_same_artifact_in_an_independent_database_maps_to_the_same_content_and_digest(mapping, tmp_path):
    from tests.mapping_builders import Mapping
    other = Mapping(str(tmp_path / "independent.sqlite3"))
    try:
        mapping.clock.advance(days=3)  # creation time differs; content and digest must not
        a, b = mapping.map(mapping.artifact()).proposal, other.map(other.artifact()).proposal
        assert (a.proposal_id, a.content, a.proposal_digest) == (b.proposal_id, b.content, b.proposal_digest)
        assert a.created_at != b.created_at
    finally:
        other.connection.close()


# --- E1: the artifact exists and passes the Phase 6 integrity path -------------------------------


def test_e1_a_missing_artifact_is_refused(mapping):
    refused(mapping, "FIXTURE-ART-404", "artifact_not_found")


def test_e1_a_failed_run_has_no_artifact_and_never_maps(mapping):
    result = mapping.extract(reply=response("{not json", model="FIXTURE-model-not-a-real-invocation"))
    assert result.artifact_id is None and result.status.value == "parse_failure"
    refused(mapping, f"FIXTURE-ART-{1:03d}", "artifact_not_found")
    assert mapping.count("ai_proposals") == 0


@pytest.mark.parametrize("sql", [
    "UPDATE ai_artifacts SET artifact_digest = '" + "0" * 64 + "'",
    "UPDATE ai_artifacts SET account_id = 'FIXTURE-ACC-2'",
    "UPDATE ai_artifacts SET interaction_id = 'FIXTURE-INT-2'",
    "UPDATE ai_artifacts SET ai_run_id = 'FIXTURE-RUN-002' WHERE artifact_id = 'FIXTURE-ART-001'",
    "UPDATE ai_run_results SET status = 'semantic_validation_failure', failure_codes = '[\"excerpt_not_verbatim\"]'",
    "UPDATE ai_run_results SET failure_codes = '[\"excerpt_not_verbatim\"]'",
    "UPDATE ai_run_outputs SET raw_output_text = 'tampered'",
    "UPDATE ai_artifact_excerpts SET text = 'not in the interaction' WHERE artifact_id = 'FIXTURE-ART-001' "
    "AND excerpt_id = 'e1'",
    "UPDATE interactions SET text = 'Buyer: rewritten.' WHERE interaction_id = 'FIXTURE-INT-1'",
], ids=["artifact digest", "account", "interaction", "run binding", "failed status", "failure codes", "raw output",
        "excerpt text", "source text"])
def test_e1_any_corruption_of_the_stored_provenance_is_refused_and_never_repaired(mapping, sql):
    artifact_id = mapping.artifact()
    mapping.extract(INT_SAME_TEXT, reply=response("{not json", model=MODEL))  # FIXTURE-RUN-002: failed, no artifact
    tamper(mapping.connection, sql)
    refused(mapping, artifact_id, "artifact_integrity_failure")


# --- E2: a successful terminal result ----------------------------------------------------------


class _Provenance:
    """The real store, except that get_result returns a substituted (still record-valid) result."""

    def __init__(self, store, result) -> None:
        self._store, self._result = store, result

    def __getattr__(self, name):
        return getattr(self._store, name)

    def get_result(self, ai_run_id):
        return self._result


def test_e2_a_stored_response_model_mismatch_is_already_refused_by_the_phase6_store(mapping):
    artifact_id = mapping.artifact()
    tamper(mapping.connection, "UPDATE ai_run_results SET response_model = 'FIXTURE-another-model'")
    refused(mapping, artifact_id, "artifact_integrity_failure")


@pytest.mark.parametrize("change", [
    {"response_model": "FIXTURE-another-model"},
    {"stop_reason": "end_turn ", "status": None},
], ids=["response model", "stop reason"])
def test_e2_the_mapper_independently_requires_a_clean_successful_result(mapping, change):
    import dataclasses
    from baec_app.data.ai_provenance import AiRunStatus
    artifact_id = mapping.artifact()
    store, repository = AiProvenanceStore(mapping.connection), Repository(mapping.connection)
    result = store.get_result(store.get_artifact(artifact_id).ai_run_id)
    if "status" in change:  # an unexpected stop: still a record-valid result, never a success
        substituted = dataclasses.replace(result, status=AiRunStatus.UNEXPECTED_STOP, stop_reason="pause_turn")
    else:
        substituted = dataclasses.replace(result, **change)
    with pytest.raises(ArtifactNotEligible) as raised:
        verify_artifact(artifact_id, provenance=_Provenance(store, substituted), repository=repository)
    assert raised.value.code == "run_not_successful"


# --- E3: the compatibility allowlist -------------------------------------------------------------


@pytest.mark.parametrize("sql", [
    "UPDATE ai_runs SET validation_version = 'baec-extraction-validation/v1'",
    "UPDATE ai_runs SET validation_version = 'baec-extraction-validation/v3'",
    "UPDATE ai_runs SET canonicalization_version = 'baec-canonical-json/v2'",
    "UPDATE ai_runs SET task_version = 'baec-extraction-task/v2'; "
    "UPDATE ai_artifacts SET task_version = 'baec-extraction-task/v2'",
    "UPDATE ai_runs SET task_type = 'other_task'; UPDATE ai_artifacts SET task_type = 'other_task'",
    "UPDATE ai_runs SET output_schema_version = 'baec-extraction-output/v2'; "
    "UPDATE ai_artifacts SET output_schema_version = 'baec-extraction-output/v2'",
], ids=["validation v1", "validation v3", "canonicalization", "task version", "task type", "output schema"])
def test_e3_an_identity_outside_the_compatibility_allowlist_is_refused(mapping, sql):
    artifact_id = mapping.artifact()
    for statement in sql.split("; "):
        tamper(mapping.connection, statement)
    refused(mapping, artifact_id, "incompatible_identity")


def test_e3_no_model_is_required_or_preferred(mapping, monkeypatch):
    import tests.mapping_builders as builders
    monkeypatch.setattr(builders, "MODEL", "FIXTURE-any-other-model")
    assert mapping.map(mapping.artifact()).created


def test_e3_a_runtime_validator_other_than_the_allowlisted_one_refuses(mapping, monkeypatch):
    from baec_app.ai import validation
    artifact_id = mapping.artifact()
    monkeypatch.setattr(validation, "VALIDATION_VERSION", "baec-extraction-validation/v3")
    refused(mapping, artifact_id, "validator_unavailable")


# --- E4: the stored output is exactly a valid output with its excerpts --------------------------------


def _rewrite_result(mapping, change) -> None:
    value = json.loads(mapping.connection.execute("SELECT canonical_result FROM ai_artifacts").fetchone()[0])
    text = change(value)
    tamper(mapping.connection, "UPDATE ai_artifacts SET canonical_result = ?, artifact_digest = ?",
           (text, sha256_text(text)))


@pytest.mark.parametrize("change", [
    lambda v: json.dumps(v),  # not canonical
    lambda v: canonical_json(dict(v, unexpected="field")),  # not the output schema
    lambda v: canonical_json(dict(v, normalized_condition="A price increase of more than 15% at renewal.")),
    lambda v: canonical_json(dict(v, source_excerpts=v["source_excerpts"][:1])),  # differs from stored excerpts
    lambda v: canonical_json(dict(v, criterion_hypotheses=v["criterion_hypotheses"][:3])),  # fails validation
], ids=["non-canonical", "extra field", "ungrounded number", "excerpt mismatch", "criterion set"])
def test_e4_an_output_that_is_not_exactly_a_valid_stored_output_is_refused(mapping, change):
    artifact_id = mapping.artifact()
    _rewrite_result(mapping, change)
    refused(mapping, artifact_id, "artifact_output_invalid")


def test_e4_a_stored_speaker_that_differs_from_the_output_is_refused(mapping):
    artifact_id = mapping.artifact()
    tamper(mapping.connection, "UPDATE ai_artifact_excerpts SET attributed_speaker = 'seller'")
    refused(mapping, artifact_id, "artifact_output_invalid")


# --- E5: only possible BAEC language ------------------------------------------------------------


@pytest.mark.parametrize("status", ["no_clear_baec_language", "insufficient_context"])
def test_e5_only_possible_baec_language_maps(mapping, status):
    refused(mapping, mapping.artifact(analysis_status=status), "no_possible_baec_language")


# --- E6: the source exists and belongs to the account ------------------------------------------------


@pytest.mark.parametrize("sql", [
    "DELETE FROM accounts WHERE account_id = 'FIXTURE-ACC-1'",
    "UPDATE accounts SET state = 'ACTIVE_OPPORTUNITY' WHERE account_id = 'FIXTURE-ACC-1'",
], ids=["account missing", "account state contradicts its history"])
def test_e6_a_missing_or_corrupt_source_account_is_refused(mapping, sql):
    artifact_id = mapping.artifact()
    tamper(mapping.connection, sql)
    refused(mapping, artifact_id, "source_binding_invalid")


def test_e6_the_mapper_independently_requires_the_interaction_to_belong_to_the_account(mapping):
    from baec_app.data.records import SourceInteraction
    artifact_id = mapping.artifact()
    repository = Repository(mapping.connection)

    class _Moved:
        def get_account(self, account_id):
            return repository.get_account(account_id)

        def get_interaction(self, interaction_id):
            real = repository.get_interaction(interaction_id)
            return SourceInteraction(real.interaction_id, OTHER_ACC, real.occurred_at, real.text)

    with pytest.raises(ArtifactNotEligible) as raised:
        verify_artifact(artifact_id, provenance=AiProvenanceStore(mapping.connection), repository=_Moved())
    assert raised.value.code == "source_binding_invalid"


def test_e6_an_interaction_moved_to_another_account_is_refused(mapping):
    artifact_id = mapping.artifact()
    tamper(mapping.connection, "UPDATE interactions SET account_id = 'FIXTURE-ACC-2' WHERE interaction_id = 'FIXTURE-INT-1'")
    with pytest.raises(ArtifactNotEligible) as raised:
        mapping.map(artifact_id)
    assert raised.value.code in ("artifact_integrity_failure", "source_binding_invalid")
    assert mapping.count("ai_proposals") == 0


# --- E7: one proposal per artifact; a repeat returns it and creates nothing ---------------------------


def test_e7_mapping_the_same_artifact_again_returns_the_stored_proposal_and_creates_nothing(mapping):
    artifact_id = mapping.artifact()
    first = mapping.map(artifact_id)
    before = dump(mapping.connection)
    mapping.clock.advance(minutes=30)
    again = mapping.map(artifact_id)
    assert (again.created, again.proposal) == (False, first.proposal)
    assert dump(mapping.connection) == before and mapping.count("ai_proposals") == 1


def test_e7_a_stored_proposal_that_differs_from_the_mapping_is_refused(mapping):
    artifact_id = mapping.artifact()
    stored = mapping.map(artifact_id).proposal
    text = canonical_json(dict(json.loads(stored.content), ai_analysis_status="tampered"))
    tamper(mapping.connection, "UPDATE ai_proposals SET content = ?, proposal_digest = ?", (text, sha256_text(text)))
    refused(mapping, artifact_id, "stored_proposal_mismatch")


def test_e7_a_proposal_for_the_artifact_under_another_identifier_is_never_duplicated(mapping):
    artifact_id = mapping.artifact()
    store, repository = AiProvenanceStore(mapping.connection), Repository(mapping.connection)
    content = map_artifact_to_proposal_content(verify_artifact(artifact_id, provenance=store, repository=repository))
    artifact = store.get_artifact(artifact_id)
    mapping.bridge.add_proposal(AiProposalRecord(
        proposal_id="aiprop_inserted_elsewhere", artifact_id=artifact_id, artifact_digest=artifact.artifact_digest,
        account_id=ACC, interaction_id=INT, content=content, proposal_digest=sha256_text(content),
        created_by=ACTOR, created_at=MAPPED_AT))
    refused(mapping, artifact_id, "stored_proposal_mismatch")
    assert mapping.count("ai_proposals") == 1


def test_identical_text_in_different_artifacts_remains_distinct_lineages(mapping):
    results = [mapping.map(mapping.artifact(interaction_id, account_id))
               for interaction_id, account_id in ((INT, ACC), (INT_SAME_TEXT, ACC), (INT_OTHER_ACCOUNT, OTHER_ACC))]
    assert all(r.created for r in results) and mapping.count("ai_proposals") == 3
    assert len({r.proposal.proposal_id for r in results}) == len({r.proposal.proposal_digest for r in results}) == 3
    excerpts = {tuple(e["text"] for e in content_of(r)["ai_suggested_excerpts"]) for r in results}
    assert len(excerpts) == 1  # the same words, three lineages
    assert [r.proposal.account_id for r in results] == [ACC, ACC, OTHER_ACC]


def test_a_second_artifact_on_the_same_interaction_is_its_own_lineage(mapping):
    first, second = mapping.artifact(), mapping.artifact()
    assert first != second
    assert mapping.map(first).proposal.proposal_id != mapping.map(second).proposal.proposal_id


# --- E8: a confirmed lineage is never mapped again ---------------------------------------------------


def test_e8_an_artifact_whose_lineage_is_confirmed_is_refused(mapping):
    artifact_id = mapping.artifact()
    proposal = mapping.map(artifact_id).proposal
    connection = mapping.connection
    revision = revision_record(proposal)
    mapping.bridge.add_revision(revision)
    accept = decision_record(proposal, revision.review_revision_id)
    mapping.bridge.add_decision(accept)
    grant = grant_values(revision, accept.decision_id, issued_at=MAPPED_AT + timedelta(hours=1))
    insert_row(connection, "human_authorization_grants", grant)
    issued = MAPPED_AT + timedelta(hours=1)
    evidence = EvidenceExcerpt(EXCERPT, ProvenanceCategory.BUYER_FACT, INT)
    record = create_confirmed_baec_record(
        candidate(source_excerpt=evidence, criterion_evidence=evidence, source_interaction_id=INT, account_id=ACC),
        baec_id=grant["baec_id"], captured_at=MAPPED_AT,
        confirmation=HumanAuthorization(grant["actor_label"], issued, AuthorizationAction.CONFIRM_BAEC, grant["baec_id"]))
    Repository(connection).save_confirmed_baec(record)
    authorization_id = connection.execute("SELECT confirmation_authorization_id FROM baec_records").fetchone()[0]
    insert_row(connection, "ai_proposal_confirmations", dict(
        baec_id=grant["baec_id"], artifact_id=artifact_id, proposal_id=proposal.proposal_id,
        review_revision_id=revision.review_revision_id, grant_id=grant["grant_id"],
        authorization_id=authorization_id, account_id=ACC, interaction_id=INT))
    assert mapping.bridge.confirmed_baec_for_artifact(artifact_id) == grant["baec_id"]
    refused(mapping, artifact_id, "lineage_already_confirmed")


# --- the Phase 4 gate refuses a real mapped AI_DRAFT -------------------------------------------------


def test_a_mapped_ai_draft_can_never_enter_the_phase4_request_or_approval_path(mapping):
    mapped = mapping.map(mapping.artifact()).proposal
    command = build_command_facade(Repository(mapping.connection), clock=FixedClock(MAPPED_AT), ids=SequentialIds())
    session = command.gate.open_session("FIXTURE-reviewer")
    evidence = EvidenceExcerpt(EXCERPT, ProvenanceCategory.BUYER_FACT, mapped.interaction_id)
    drafted = candidate(source_excerpt=evidence, criterion_evidence=evidence,
                        source_interaction_id=mapped.interaction_id, account_id=mapped.account_id)
    before = dump(mapping.connection)
    with pytest.raises(ProposalNotAuthoritative):
        command.request_from_proposal(session, mapped)  # the persisted AI_DRAFT itself is not a Phase 4 proposal
    with pytest.raises(AiDraftNotPermitted):
        ConfirmationProposal(drafted, MAPPED_AT, ProposalOrigin(mapped.origin))
    with pytest.raises(AiDraftNotPermitted):
        command.request_baec_confirmation(session, drafted, captured_at=MAPPED_AT, origin=ProposalOrigin(mapped.origin))
    forged = ConfirmationProposal(drafted, MAPPED_AT, ProposalOrigin.HUMAN_DRAFT)
    object.__setattr__(forged, "origin", ProposalOrigin(mapped.origin))
    with pytest.raises(AiDraftNotPermitted):
        command.request_from_proposal(session, forged)
    assert command.gate._requests == {} and dump(mapping.connection) == before
    assert mapping.count("baec_records") == 0


def test_mapping_never_touches_ai_provenance(mapping):
    artifact_id = mapping.artifact()
    before = {t: dump(mapping.connection)[t] for t in AI_PROVENANCE_TABLES}
    mapping.map(artifact_id)
    assert {t: dump(mapping.connection)[t] for t in AI_PROVENANCE_TABLES} == before


# --- live-evaluation exclusion is structural (design §6.1; Phase 7D clarification) -----------------------


def test_an_artifact_in_another_database_is_never_reachable(mapping, tmp_path):
    from tests.mapping_builders import Mapping
    temporary = Mapping(str(tmp_path / "temporary-evaluation.sqlite3"))
    try:
        elsewhere = temporary.artifact()
        assert temporary.map(elsewhere).created  # eligible in its own database
    finally:
        temporary.connection.close()
    refused(mapping, elsewhere, "artifact_not_found")  # not reachable from the product database
    assert mapping.count("ai_artifacts") == 0


def test_the_structural_limitation_is_stated_plainly():
    from baec_app.application import ai_proposal_mapping
    doc = " ".join(ai_proposal_mapping.__doc__.split())
    assert "reachable through this product database's durable provenance relationships" in doc
    assert "does not cryptographically prove that rows were never copied by hand from another SQLite database" in doc


@pytest.mark.parametrize("speaker", ["buyer", "seller", "unclear"])
def test_each_attributed_speaker_value_survives_separately_from_its_ai_inference_status(mapping, speaker):
    artifact_id = mapping.artifact(excerpts=[{"excerpt_id": "e1", "source_interaction_id": INT, "text": EXCERPT,
                                              "attributed_speaker": speaker}])
    proposal = mapping.map(artifact_id).proposal
    (excerpt,) = json.loads(proposal.content)["ai_suggested_excerpts"]
    assert excerpt == {"excerpt_id": "e1", "text": EXCERPT,
                       "ai_attributed_speaker": {"value": speaker, "provenance": "AI_INFERENCE"}}
    assert excerpt["ai_attributed_speaker"]["value"] != "AI_INFERENCE"  # the speaker is never collapsed away
    for authoritative in ("BUYER_FACT", "SELLER_OBSERVATION"):
        assert authoritative not in proposal.content
