"""Phase 6D-B1: schema v6, validator provenance, and the closed failure-code contract.

docs/PHASE6D_AI_BEHAVIOR_HARDENING_DESIGN.md §5-§6. Every rule here is an implementation
safeguard, not a research finding. Since Phase 6D-B2 the current validator is /v2, whose
vocabulary adds the 18 grounding codes; they remain illegal for any run recorded as /v1.
"""

import sqlite3
from dataclasses import replace
from datetime import timedelta

import pytest

from baec_app.ai.composition import _DataLayerProvenanceStore, open_extraction_runtime
from baec_app.ai.provenance import PARSE_FAILURE_CODES, RemoteOutcome, RunRecord, RunStatus, TerminalResult
from baec_app.ai.provider import ProviderApiError, ProviderTransportError
from baec_app.ai.validation import (
    SEMANTIC_FAILURE_CODES,
    SEMANTIC_FAILURE_CODES_BY_VALIDATION_VERSION,
    VALIDATION_VERSION,
)
from baec_app.application import open_read_connection
from baec_app.data import database
from baec_app.data.ai_provenance import (
    AiProvenanceStore,
    AiRemoteOutcome,
    AiRunResultRecord,
    AiRunStatus,
    open_ai_provenance_store,
)
from baec_app.data.database import (
    AI_PARSE_FAILURE_CODES,
    AI_SEMANTIC_FAILURE_CODES,
    AI_SEMANTIC_FAILURE_CODES_BY_VALIDATION_VERSION,
    DatabaseVersionError,
    PersistenceError,
    PersistenceIntegrityError,
    RepositoryVerificationError,
    open_database,
    schema_version,
    table_counts,
)
from baec_app.data.records import RecordValidationError
from baec_app.data.seed import build_canonical_seed_database
from tests.ai_builders import START, FakeProvider, as_text, output, response, world  # noqa: F401
from tests.ai_provenance_builders import (  # noqa: F401
    COMPLETED,
    ai_rows,
    db,
    digest,
    outcome_for,
    result_record,
    run_record,
)
from tests.persistence_builders import tamper

V1 = "baec-extraction-validation/v1"
V2 = "baec-extraction-validation/v2"
UNKNOWN_VERSION = "baec-extraction-validation/v9"  # no such validator exists
V1_SEMANTIC = (
    "criterion_set_invalid", "duplicate_excerpt_id", "duplicate_excerpt_reference", "duplicate_excerpt_text",
    "excerpt_blank", "excerpt_id_blank", "excerpt_not_verbatim", "explanation_blank", "explanation_too_long",
    "normalization_blank", "normalization_too_long", "possible_language_without_excerpt",
    "source_interaction_mismatch", "supported_without_excerpt", "too_many_excerpt_references", "too_many_excerpts",
    "too_many_uncertainties", "uncertainty_blank", "uncertainty_too_long", "unknown_excerpt_reference",
)
PARSE = ("invalid_json", "missing_stop_reason", "missing_text_block", "multiple_text_blocks",
         "structured_output_validation_failed")
# The validation-v2 grounding codes (6D-B2): legal under /v2 only, never for a run recorded as /v1.
RESERVED_V2_GROUNDING = tuple(
    f"{scope}_{ending}"
    for scope in ("normalization", "explanation", "uncertainty")
    for ending in ("number_unsupported", "numeric_kind_changed", "unit_changed", "compound_unsupported",
                   "comparator_changed", "comparator_unresolved")
)
# A validator identifier must hold non-whitespace content: each of these is refused, never trimmed.
WHITESPACE_ONLY = {"empty": "", "space": " ", "spaces": "   ", "tab": "\t", "newline": "\n"}
NO_CODE_STATUSES = ("success", "refusal", "max_tokens", "unexpected_stop", "api_error", "transport_failure",
                    "model_mismatch", "interrupted")

# --- the closed vocabularies -------------------------------------------------------------------------


def test_the_v1_vocabularies_are_exactly_the_approved_tokens():
    assert len(V1_SEMANTIC) == 20 and len(PARSE) == 5 and len(RESERVED_V2_GROUNDING) == 18
    assert tuple(AI_SEMANTIC_FAILURE_CODES_BY_VALIDATION_VERSION) == (V1, V2)
    assert AI_SEMANTIC_FAILURE_CODES_BY_VALIDATION_VERSION[V1] == V1_SEMANTIC
    assert AI_SEMANTIC_FAILURE_CODES_BY_VALIDATION_VERSION[V2] == tuple(sorted(V1_SEMANTIC + RESERVED_V2_GROUNDING))
    assert AI_SEMANTIC_FAILURE_CODES == AI_SEMANTIC_FAILURE_CODES_BY_VALIDATION_VERSION[V2]
    assert AI_PARSE_FAILURE_CODES == PARSE
    every = V1_SEMANTIC + PARSE + RESERVED_V2_GROUNDING
    assert len(set(every)) == 43  # the design's total, with no collisions
    for token in every:
        assert token.isascii() and token == token.lower() and ":" not in token


def test_ai_layer_and_data_layer_vocabularies_are_equal():
    assert VALIDATION_VERSION == V2 and VALIDATION_VERSION in AI_SEMANTIC_FAILURE_CODES_BY_VALIDATION_VERSION
    assert set(SEMANTIC_FAILURE_CODES_BY_VALIDATION_VERSION) == set(AI_SEMANTIC_FAILURE_CODES_BY_VALIDATION_VERSION)
    for version, codes in SEMANTIC_FAILURE_CODES_BY_VALIDATION_VERSION.items():
        assert set(codes) == set(AI_SEMANTIC_FAILURE_CODES_BY_VALIDATION_VERSION[version]), version
    assert set(SEMANTIC_FAILURE_CODES) == set(AI_SEMANTIC_FAILURE_CODES_BY_VALIDATION_VERSION[V1])
    assert len(SEMANTIC_FAILURE_CODES) == len(set(SEMANTIC_FAILURE_CODES))
    assert set(PARSE_FAILURE_CODES) == set(AI_PARSE_FAILURE_CODES) and len(PARSE_FAILURE_CODES) == 5


def test_the_reserved_v2_grounding_codes_are_legal_nowhere_and_never_emitted_by_validator_v1():
    """Name kept from 6D-B1 for ID continuity. Since 6D-B2 the grounding codes are legal under /v2 only."""
    grounding = set(RESERVED_V2_GROUNDING)
    for vocabulary in (AI_SEMANTIC_FAILURE_CODES_BY_VALIDATION_VERSION, SEMANTIC_FAILURE_CODES_BY_VALIDATION_VERSION):
        assert not grounding & set(vocabulary[V1])
        assert grounding <= set(vocabulary[V2])
    assert not grounding & (set(AI_PARSE_FAILURE_CODES) | set(SEMANTIC_FAILURE_CODES))


# --- the contract matrix, applied to every layer -------------------------------------------------------

VALID = {
    "semantic one": ("semantic_validation_failure", ("excerpt_not_verbatim",)),
    "semantic several sorted": ("semantic_validation_failure", ("criterion_set_invalid", "excerpt_not_verbatim",
                                                                 "uncertainty_blank")),
    "semantic every v1 code": ("semantic_validation_failure", V1_SEMANTIC),
    **{f"parse {code}": ("parse_failure", (code,)) for code in PARSE},
}
INVALID = {
    "semantic none": ("semantic_validation_failure", ()),
    "semantic unknown": ("semantic_validation_failure", ("excerpt_missing",)),
    "semantic suffixed id": ("semantic_validation_failure", ("excerpt_not_verbatim:e1",)),
    "semantic suffixed design form": ("semantic_validation_failure", ("excerpt_not_in_source:e2",)),
    "semantic unsorted": ("semantic_validation_failure", ("excerpt_not_verbatim", "criterion_set_invalid")),
    "semantic duplicate": ("semantic_validation_failure", ("excerpt_blank", "excerpt_blank")),
    "semantic with parse code": ("semantic_validation_failure", ("invalid_json",)),
    "semantic uppercase": ("semantic_validation_failure", ("EXCERPT_BLANK",)),
    "semantic padded": ("semantic_validation_failure", (" excerpt_blank",)),
    "parse none": ("parse_failure", ()),
    "parse two": ("parse_failure", ("invalid_json", "missing_text_block")),
    "parse two identical": ("parse_failure", ("invalid_json", "invalid_json")),
    "parse with semantic code": ("parse_failure", ("excerpt_blank",)),
    "parse unknown": ("parse_failure", ("no_parsed_output",)),
    "parse suffixed": ("parse_failure", ("invalid_json:line1",)),
    **{f"{status} with a parse code": (status, ("invalid_json",)) for status in NO_CODE_STATUSES},
    **{f"{status} with a semantic code": (status, ("excerpt_blank",)) for status in NO_CODE_STATUSES},
}
# Legal under /v2 since 6D-B2, so legal for record construction and the current AI validator,
# but still illegal for a run recorded as /v1: the store and SQL tests (all /v1 runs) refuse them.
GROUNDING_UNDER_V1 = {
    "semantic reserved v2 code": ("semantic_validation_failure", ("normalization_number_unsupported",)),
    "semantic v1 plus reserved v2": ("semantic_validation_failure", ("excerpt_blank", "explanation_comparator_changed")),
}


def _result(status: str, codes: tuple[str, ...]) -> AiRunResultRecord:
    """A result valid in every respect but its failure codes."""
    if status == "interrupted":
        return AiRunResultRecord("RUN-1", AiRunStatus.INTERRUPTED, AiRemoteOutcome.UNKNOWN, COMPLETED,
                                 failure_codes=codes)
    return replace(result_record(AiRunStatus(status)), failure_codes=codes)


@pytest.mark.parametrize("case", VALID.values(), ids=VALID.keys())
def test_record_construction_accepts_every_legal_code_set(case):
    status, codes = case
    assert _result(status, codes).failure_codes == codes


@pytest.mark.parametrize("case", INVALID.values(), ids=INVALID.keys())
def test_record_construction_refuses_every_illegal_code_set(case):
    with pytest.raises(RecordValidationError):
        _result(*case)


@pytest.mark.parametrize("bad", [["excerpt_blank"], ("excerpt_blank", 1), (None,), "excerpt_blank"],
                         ids=["list", "non-text member", "none member", "bare string"])
def test_record_construction_refuses_anything_but_a_tuple_of_text(bad):
    with pytest.raises(RecordValidationError):
        replace(result_record(AiRunStatus.SEMANTIC_VALIDATION_FAILURE), failure_codes=bad)


def _terminal(status: str, codes: tuple[str, ...]) -> TerminalResult:
    return TerminalResult(status=RunStatus(status), remote_outcome=RemoteOutcome.RESPONSE_RECEIVED,
                          completed_at=COMPLETED, failure_codes=codes)


@pytest.mark.parametrize("case", VALID.values(), ids=VALID.keys())
def test_the_ai_terminal_result_accepts_every_legal_code_set(case):
    status, codes = case
    assert _terminal(status, codes).failure_codes == codes


AI_INVALID = {label: case for label, case in INVALID.items() if case[0] != "interrupted"}


@pytest.mark.parametrize("case", AI_INVALID.values(), ids=AI_INVALID.keys())
def test_the_ai_terminal_result_refuses_every_illegal_code_set(case):
    with pytest.raises(ValueError):
        _terminal(*case)


# --- the store: write against the run's validator version, read back fail-closed ------------------------


def _bypass(record: AiRunResultRecord, codes: tuple[str, ...]) -> AiRunResultRecord:
    """A record whose codes were changed after its own checks ran, as a defective caller could."""
    object.__setattr__(record, "failure_codes", codes)
    return record


STORE_INVALID = {label: case for label, case in {**INVALID, **GROUNDING_UNDER_V1}.items()
                 if case[0] not in ("interrupted", "success")}  # interrupted: mark_interrupted only


@pytest.mark.parametrize("case", STORE_INVALID.values(), ids=STORE_INVALID.keys())
def test_the_store_write_refuses_every_illegal_code_set_on_its_own_and_writes_nothing(db, case):
    _, connection, store = db
    status, codes = case
    store.record_run(run_record())
    outcome = outcome_for(AiRunStatus(status))
    _bypass(outcome.result, codes)
    with pytest.raises(RepositoryVerificationError):
        store.record_terminal_outcome(outcome)
    assert ai_rows(connection) == {"ai_runs": 1, "ai_run_results": 0, "ai_run_outputs": 0, "ai_artifacts": 0,
                                   "ai_artifact_excerpts": 0}


def test_the_store_write_refuses_a_code_on_success(db):
    _, connection, store = db
    store.record_run(run_record())
    outcome = outcome_for(AiRunStatus.SUCCESS)
    _bypass(outcome.result, ("excerpt_blank",))
    with pytest.raises(RepositoryVerificationError):
        store.record_terminal_outcome(outcome)
    assert ai_rows(connection)["ai_run_results"] == 0


@pytest.mark.parametrize("version", [UNKNOWN_VERSION, "legacy", "baec-extraction-validation/v1 "])
def test_a_semantic_code_is_refused_under_a_validator_version_that_cannot_emit_it(db, version):
    _, connection, store = db
    store.record_run(run_record(validation_version=version))
    with pytest.raises(RepositoryVerificationError):
        store.record_terminal_outcome(outcome_for(AiRunStatus.SEMANTIC_VALIDATION_FAILURE))
    assert ai_rows(connection)["ai_run_results"] == 0


@pytest.mark.parametrize("status", [s for s in AiRunStatus if s not in (AiRunStatus.SEMANTIC_VALIDATION_FAILURE,
                                                                         AiRunStatus.INTERRUPTED)])
def test_statuses_without_semantic_codes_do_not_depend_on_a_known_validator_version(db, status):
    _, _, store = db
    store.record_run(run_record(validation_version="baec-extraction-validation/v9"))
    store.record_terminal_outcome(outcome_for(status))
    assert store.get_result("RUN-1").status is status


@pytest.fixture
def stored(db):
    """One stored run per code-bearing shape: semantic, parse, refusal, and success."""
    _, connection, store = db
    for run_id, status in (("RUN-S", AiRunStatus.SEMANTIC_VALIDATION_FAILURE), ("RUN-P", AiRunStatus.PARSE_FAILURE),
                           ("RUN-R", AiRunStatus.REFUSAL), ("RUN-1", AiRunStatus.SUCCESS)):
        store.record_run(run_record(run_id))
        store.record_terminal_outcome(outcome_for(status, run_id))
    return connection, store


TAMPERED = {
    "semantic unknown": ("RUN-S", "failure_codes = '[\"excerpt_missing\"]'"),
    "semantic suffixed": ("RUN-S", "failure_codes = '[\"excerpt_not_verbatim:e1\"]'"),
    "semantic unsorted": ("RUN-S", "failure_codes = '[\"excerpt_not_verbatim\",\"criterion_set_invalid\"]'"),
    "semantic duplicate": ("RUN-S", "failure_codes = '[\"excerpt_blank\",\"excerpt_blank\"]'"),
    "semantic reserved v2": ("RUN-S", "failure_codes = '[\"uncertainty_unit_changed\"]'"),
    "semantic parse code": ("RUN-S", "failure_codes = '[\"invalid_json\"]'"),
    "semantic empty array": ("RUN-S", "failure_codes = '[]'"),
    "semantic null": ("RUN-S", "failure_codes = NULL"),
    "semantic not json": ("RUN-S", "failure_codes = 'excerpt_blank'"),
    "semantic object": ("RUN-S", "failure_codes = '{\"excerpt_blank\":1}'"),
    "semantic number member": ("RUN-S", "failure_codes = '[1]'"),
    "semantic nested array": ("RUN-S", "failure_codes = '[[\"excerpt_blank\"]]'"),
    "semantic non-canonical spelling": ("RUN-S", "failure_codes = '[ \"excerpt_not_verbatim\" ]'"),
    "semantic escaped spelling": ("RUN-S", "failure_codes = '[\"excerpt_not_verb\\u0061tim\"]'"),
    "parse none": ("RUN-P", "failure_codes = NULL"),
    "parse two": ("RUN-P", "failure_codes = '[\"invalid_json\",\"missing_text_block\"]'"),
    "parse semantic code": ("RUN-P", "failure_codes = '[\"excerpt_blank\"]'"),
    "parse suffixed": ("RUN-P", "failure_codes = '[\"invalid_json:1\"]'"),
    "refusal with a code": ("RUN-R", "failure_codes = '[\"invalid_json\"]'"),
    "success with a code": ("RUN-1", "failure_codes = '[\"excerpt_blank\"]'"),
}


@pytest.mark.parametrize("case", TAMPERED.values(), ids=TAMPERED.keys())
def test_a_tampered_stored_code_set_fails_closed_on_readback(stored, case):
    connection, store = stored
    run_id, assignment = case
    tamper(connection, f"UPDATE ai_run_results SET {assignment} WHERE ai_run_id = '{run_id}'")
    with pytest.raises(PersistenceIntegrityError):
        store.get_result(run_id)


@pytest.mark.parametrize("version", [UNKNOWN_VERSION, "legacy"])
def test_a_tampered_run_validator_version_makes_its_semantic_codes_fail_closed(stored, version):
    connection, store = stored
    tamper(connection, f"UPDATE ai_runs SET validation_version = '{version}' WHERE ai_run_id = 'RUN-S'")
    assert store.get_run("RUN-S").validation_version == version  # the run itself is still well-formed
    with pytest.raises(PersistenceIntegrityError):
        store.get_result("RUN-S")


@pytest.mark.parametrize("value", WHITESPACE_ONLY.values(), ids=WHITESPACE_ONLY.keys())
def test_a_tampered_blank_validator_version_fails_closed(stored, value):
    """NULL is unreachable even by tampering: NOT NULL holds with every other guard off."""
    connection, store = stored
    tamper(connection, "UPDATE ai_runs SET validation_version = ? WHERE ai_run_id = 'RUN-R'", (value,))
    with pytest.raises(PersistenceIntegrityError):
        store.get_run("RUN-R")
    with pytest.raises(PersistenceIntegrityError):
        store.get_result("RUN-R")


def test_untampered_code_sets_read_back_exactly(stored):
    _, store = stored
    assert store.get_result("RUN-S").failure_codes == ("excerpt_not_verbatim",)
    assert store.get_result("RUN-P").failure_codes == ("invalid_json",)
    assert store.get_result("RUN-R").failure_codes == store.get_result("RUN-1").failure_codes == ()


# --- the SQL backstop: raw inserts ----------------------------------------------------------------------

RAW_RUN = (
    "INSERT INTO ai_runs (ai_run_id, provider, task_type, task_version, account_id, interaction_id, requested_model, "
    "sdk_name, sdk_version, prompt_version, prompt_digest, input_version, input_digest, output_schema_version, "
    "output_schema_digest, canonicalization_version, request_spec_version, request_digest, validation_version, "
    "requested_at) VALUES ('RAW-1', 'anthropic', 't', 'v1', 'ACC-1', 'INT-1', 'm', 'anthropic', '1', 'p', ?, 'i', ?, "
    "'o', ?, 'c', 's', ?, ?, '2026-04-01T09:00:00.000000+00:00')"
)
RAW_RESULT = (
    "INSERT INTO ai_run_results (ai_run_id, status, remote_outcome, provider_message_id, response_model, stop_reason, "
    "failure_category, failure_codes, output_digest, completed_at) "
    "VALUES ('RAW-1', ?, ?, ?, ?, ?, ?, ?, ?, '2026-04-01T09:00:20.000000+00:00')"
)
OUT = digest("out")
SHAPES = {  # status -> (remote_outcome, message id, response model, stop reason, category, output digest)
    "success": ("response_received", "msg", "m", "end_turn", None, OUT),
    "refusal": ("response_received", None, None, "refusal", None, None),
    "max_tokens": ("response_received", None, None, "max_tokens", None, None),
    "unexpected_stop": ("response_received", None, None, "pause_turn", None, None),
    "api_error": ("response_received", None, None, None, "overloaded", None),
    "transport_failure": ("unknown", None, None, None, "timeout_or_disconnect", None),
    "parse_failure": ("response_received", None, None, "end_turn", None, None),
    "semantic_validation_failure": ("response_received", None, None, "end_turn", None, OUT),
    "model_mismatch": ("response_received", None, "other", "end_turn", None, None),
    "interrupted": ("unknown", None, None, None, None, None),
}


def _raw_run(connection, version=V1):
    connection.execute(RAW_RUN, (*[digest(x) for x in "pior"], version))


def _raw_result(connection, status, codes):
    outcome, message, model, stop, category, out = SHAPES[status]
    connection.execute(RAW_RESULT, (status, outcome, message, model, stop, category, codes, out))


def _json(codes):
    return None if not codes else "[" + ",".join(f'"{code}"' for code in codes) + "]"


@pytest.mark.parametrize("case", VALID.values(), ids=VALID.keys())
def test_sql_accepts_every_legal_code_set(db, case):
    _, connection, _ = db
    _raw_run(connection)
    status, codes = case
    _raw_result(connection, status, _json(codes))
    assert ai_rows(connection)["ai_run_results"] == 1


@pytest.mark.parametrize("status", NO_CODE_STATUSES)
def test_sql_accepts_null_codes_on_every_other_status(db, status):
    _, connection, _ = db
    _raw_run(connection)
    _raw_result(connection, status, None)


SQL_INVALID = {
    **{label: (status, _json(codes)) for label, (status, codes) in {**INVALID, **GROUNDING_UNDER_V1}.items()},
    "semantic not json": ("semantic_validation_failure", "excerpt_blank"),
    "semantic object": ("semantic_validation_failure", '{"excerpt_blank":1}'),
    "semantic json string": ("semantic_validation_failure", '"excerpt_blank"'),
    "semantic number member": ("semantic_validation_failure", "[1]"),
    "semantic null member": ("semantic_validation_failure", "[null]"),
    "semantic nested array": ("semantic_validation_failure", '[["excerpt_blank"]]'),
    "semantic empty array": ("semantic_validation_failure", "[]"),
    "semantic empty text": ("semantic_validation_failure", ""),
    "parse not json": ("parse_failure", "invalid_json"),
    "parse empty array": ("parse_failure", "[]"),
    "parse nested array": ("parse_failure", '[["invalid_json"]]'),
    "parse object": ("parse_failure", '{"invalid_json":1}'),
    "refusal empty array": ("refusal", "[]"),
}


@pytest.mark.parametrize("case", SQL_INVALID.values(), ids=SQL_INVALID.keys())
def test_sql_refuses_every_illegal_code_set_in_a_raw_insert(db, case):
    _, connection, _ = db
    _raw_run(connection)
    with pytest.raises(sqlite3.IntegrityError):
        _raw_result(connection, *case)
    assert ai_rows(connection)["ai_run_results"] == 0


@pytest.mark.parametrize("version", [UNKNOWN_VERSION, "legacy"])
def test_sql_refuses_a_semantic_code_under_a_validator_version_that_cannot_emit_it(db, version):
    _, connection, _ = db
    _raw_run(connection, version)
    with pytest.raises(sqlite3.IntegrityError, match="closed vocabulary"):
        _raw_result(connection, "semantic_validation_failure", '["excerpt_not_verbatim"]')
    _raw_result(connection, "parse_failure", '["invalid_json"]')  # parse codes do not depend on the validator


@pytest.mark.parametrize("value", WHITESPACE_ONLY.values(), ids=WHITESPACE_ONLY.keys())
def test_sql_refuses_a_whitespace_only_validator_version(db, value):
    _, connection, _ = db
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        _raw_run(connection, value)
    assert ai_rows(connection)["ai_runs"] == 0


@pytest.mark.parametrize("value", WHITESPACE_ONLY.values(), ids=WHITESPACE_ONLY.keys())
def test_the_store_write_refuses_a_whitespace_only_validator_version(db, value):
    """A run whose version was blanked after its own checks ran is still refused, and nothing is written."""
    _, connection, store = db
    run = run_record()
    object.__setattr__(run, "validation_version", value)
    with pytest.raises(PersistenceError):
        store.record_run(run)
    assert ai_rows(connection)["ai_runs"] == 0


def test_a_validator_version_is_stored_exactly_never_trimmed(db):
    _, connection, store = db
    store.record_run(run_record(validation_version=" baec-extraction-validation/v1\t"))
    assert store.get_run("RUN-1").validation_version == " baec-extraction-validation/v1\t"
    assert connection.execute("SELECT validation_version FROM ai_runs").fetchone() == (" baec-extraction-validation/v1\t",)


def test_sql_refuses_a_blank_validator_version_and_a_missing_one(db):
    _, connection, _ = db
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        _raw_run(connection, "")
    with pytest.raises(sqlite3.IntegrityError, match="NOT NULL"):
        _raw_run(connection, None)
    assert ai_rows(connection)["ai_runs"] == 0


def test_the_validator_version_is_append_only_like_every_run_column(db):
    _, connection, store = db
    store.record_run(run_record())
    with pytest.raises(sqlite3.IntegrityError, match="ai_runs is append-only"):
        connection.execute("UPDATE ai_runs SET validation_version = 'baec-extraction-validation/v2'")
    assert store.get_run("RUN-1").validation_version == V1


# --- schema v6 and the validator version on runs ----------------------------------------------------------


def test_schema_v6_adds_a_required_validation_version_to_ai_runs_only():
    """Schema version 7 since Phase 7C (name kept for ID continuity); version 7 changed no ai_* table."""
    connection = open_database()
    try:
        assert database.SCHEMA_VERSION == schema_version(connection) == 7
        assert database.MINIMUM_SQLITE_VERSION == (3, 38, 0)
        columns = {row[1]: row for row in connection.execute("PRAGMA table_info(ai_runs)")}
        assert columns["validation_version"][2:4] == ("TEXT", 1)  # type, NOT NULL
        for table in ("ai_run_results", "ai_run_outputs", "ai_artifacts", "ai_artifact_excerpts"):
            assert "validation_version" not in {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
    finally:
        connection.close()


@pytest.mark.parametrize("value", [*WHITESPACE_ONLY.values(), None, 1], ids=[*WHITESPACE_ONLY, "none", "int"])
def test_a_blank_validator_version_is_refused_by_both_run_records(value):
    with pytest.raises(RecordValidationError):
        run_record(validation_version=value)
    fields = {name: getattr(run_record(), name) for name in RunRecord.__dataclass_fields__}
    with pytest.raises(ValueError):
        RunRecord(**dict(fields, validation_version=value))


def test_a_run_round_trips_its_validator_version_exactly(db):
    _, _, store = db
    store.record_run(run_record(validation_version="baec-extraction-validation/v1"))
    store.record_run(run_record("RUN-2", validation_version="synthetic-validator/v7"))
    assert store.get_run("RUN-1") == run_record()
    assert store.get_run("RUN-2").validation_version == "synthetic-validator/v7"


def test_the_adapter_carries_the_runs_own_validator_version_into_the_data_layer(db):
    _, _, store = db
    fields = {name: getattr(run_record(), name) for name in RunRecord.__dataclass_fields__}
    _DataLayerProvenanceStore(store).record_run(RunRecord(**dict(fields, validation_version="synthetic-validator/v7")))
    assert store.get_run("RUN-1").validation_version == "synthetic-validator/v7"


class RecordingProvider(FakeProvider):
    """Reads the persisted run inside the attempt, as the provider would see the database."""

    def __init__(self, reply, path):
        super().__init__(reply)
        self.path, self.seen = path, []

    def invoke(self, spec):
        connection = sqlite3.connect(self.path)
        try:
            self.seen.append(connection.execute("SELECT validation_version FROM ai_runs").fetchall())
        finally:
            connection.close()
        return super().invoke(spec)


def test_the_validator_version_is_committed_before_the_provider_attempt(world):
    provider = RecordingProvider(response(as_text(output())), world.path)
    world.run(provider)
    assert provider.seen == [[(V2,)]]


TERMINAL_CASES = {
    "success": (response(as_text(output())), RunStatus.SUCCESS),
    "refusal": (response("No.", stop_reason="refusal"), RunStatus.REFUSAL),
    "max_tokens": (response('{"analysis', stop_reason="max_tokens"), RunStatus.MAX_TOKENS),
    "unexpected_stop": (response(stop_reason="pause_turn"), RunStatus.UNEXPECTED_STOP),
    "api_error": (ProviderApiError("overloaded", "req_1"), RunStatus.API_ERROR),
    "transport_failure": (ProviderTransportError("connection_not_established", "not_sent"), RunStatus.TRANSPORT_FAILURE),
    "parse_failure": (response("not json"), RunStatus.PARSE_FAILURE),
    "semantic_validation_failure": (response(as_text(output(uncertainties=["  "]))),
                                    RunStatus.SEMANTIC_VALIDATION_FAILURE),
    "model_mismatch": (response(as_text(output()), model="claude-other"), RunStatus.MODEL_MISMATCH),
}


@pytest.mark.parametrize("case", TERMINAL_CASES.values(), ids=TERMINAL_CASES.keys())
def test_every_terminal_status_keeps_the_runs_validator_version(world, case):
    reply, status = case
    result = world.run(FakeProvider(reply))
    assert result.status is status
    assert world.store.get_run(result.ai_run_id).validation_version == V2
    stored = world.store.get_result(result.ai_run_id)
    assert stored.status.value == status.value
    if status is RunStatus.PARSE_FAILURE:
        assert stored.failure_codes == ("invalid_json",)
    elif status is RunStatus.SEMANTIC_VALIDATION_FAILURE:
        assert stored.failure_codes == ("uncertainty_blank",)
    else:
        assert stored.failure_codes == ()


def test_an_interrupted_run_keeps_its_validator_version(world):
    class Stop(Exception):
        pass

    with pytest.raises(Stop):
        world.run(FakeProvider(Stop()))
    (run,) = world.store.list_incomplete_runs()
    world.store.mark_interrupted(run.ai_run_id, START + timedelta(hours=2), minimum_age=timedelta(hours=1))
    assert world.store.get_result(run.ai_run_id).status is AiRunStatus.INTERRUPTED
    assert world.store.get_run(run.ai_run_id).validation_version == V2


def test_the_validator_version_is_not_part_of_the_request(world):
    """Recording the validator version changes no request, prompt, input, or schema digest."""
    provider = FakeProvider(response(as_text(output())))
    result = world.run(provider)
    run = world.store.get_run(result.ai_run_id)
    assert run.request_digest == "e142cb5c71c9b173931f04901cc18b1352d97ed78a3eba916c86850e5e71cdde"
    assert "validation" not in provider.invoked[0].to_json_object()


# --- schema v5 is refused; the seed is v6 ----------------------------------------------------------------


def _v5_file(tmp_path):
    path = tmp_path / "v5.sqlite3"
    connection = open_database(str(path))
    connection.execute("PRAGMA user_version = 5")
    connection.close()
    return path


def test_a_schema_v5_database_is_refused_by_every_opener_and_left_unmodified(tmp_path):
    path = _v5_file(tmp_path)
    before = path.read_bytes()
    with pytest.raises(DatabaseVersionError):
        open_database(str(path))
    with pytest.raises(DatabaseVersionError):
        open_read_connection(path)
    with pytest.raises(DatabaseVersionError):
        open_ai_provenance_store(path)
    provider = FakeProvider(response(as_text(output())))
    with pytest.raises(DatabaseVersionError):
        open_extraction_runtime(path, provider=provider)
    assert provider.prepared == provider.invoked == []
    assert path.read_bytes() == before


def test_the_store_refuses_a_connection_still_at_schema_v5():
    connection = open_database()
    try:
        connection.execute("PRAGMA user_version = 5")
        with pytest.raises(DatabaseVersionError):
            AiProvenanceStore(connection)
    finally:
        connection.close()


def test_the_canonical_seed_is_schema_v6_with_the_column_and_empty_ai_tables():
    """Schema version 7 since Phase 7C (name kept for ID continuity)."""
    canonical = build_canonical_seed_database()
    try:
        assert schema_version(canonical) == 7
        assert "validation_version" in {row[1] for row in canonical.execute("PRAGMA table_info(ai_runs)")}
        counts = table_counts(canonical)
        assert all(counts[table] == 0 for table in database.AI_PROVENANCE_TABLES)
        trigger = canonical.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'trigger' AND name = 'ai_run_results_failure_codes_closed'"
        ).fetchone()
        assert trigger == (1,)
    finally:
        canonical.close()
