"""Phase 6C-B: open_ai_provenance_store, the extraction runtime's composition, and its lifecycle."""

import socket
import sqlite3
from pathlib import Path

import pytest

from baec_app.ai import composition
from baec_app.ai.anthropic_provider import AnthropicExtractionProvider
from baec_app.ai.composition import (
    OUTCOME_MAP,
    SPEAKER_MAP,
    STATUS_MAP,
    ExtractionRuntime,
    _DataLayerProvenanceStore,
    open_extraction_runtime,
)
from baec_app.ai.provenance import RemoteOutcome, RunStatus
from baec_app.ai.service import ExtractionService
from baec_app.application import (
    DatabaseVersionError,
    HumanApproval,
    HumanCommandFacade,
    ProposalFacade,
    ReadDatabaseUnavailable,
    ReadService,
)
from baec_app.application.account_state import AccountStateService
from baec_app.application.approval import HumanConfirmationGate
from baec_app.application.classification import ClassificationService
from baec_app.application.dormancy import DormancyJudgmentService
from baec_app.data.ai_provenance import (
    AiAttributedSpeaker,
    AiProvenanceStore,
    AiProvenanceStoreUnavailable,
    AiRemoteOutcome,
    AiRunStatus,
    open_ai_provenance_store,
)
from baec_app.data.database import open_database
from baec_app.data.repository import Repository
from baec_app.domain.models import HumanAuthorization
from tests.ai_builders import FakeProvider, as_text, output, response, world  # noqa: F401
from tests.test_mcp_server import _is_closed, _reachable

COMMAND_SIDE = (HumanCommandFacade, HumanConfirmationGate, HumanApproval, HumanAuthorization, ClassificationService,
                DormancyJudgmentService, AccountStateService, ProposalFacade)


def _old_schema(tmp_path):
    path = tmp_path / "v4.sqlite3"
    connection = open_database(str(path))
    connection.execute("PRAGMA user_version = 4")
    connection.close()
    return path


# --- open_ai_provenance_store -------------------------------------------------------------------


def test_the_store_opener_refuses_bad_paths_and_never_creates_a_database(tmp_path):
    missing = tmp_path / "absent.sqlite3"
    not_a_database = tmp_path / "notes.sqlite3"
    not_a_database.write_bytes(b"not sqlite" * 20)
    for bad in (missing, str(missing), "", "   ", ":memory:", "file:x.sqlite3", "FILE:x.sqlite3", tmp_path,
                not_a_database, b"bytes.sqlite3", None):
        with pytest.raises(AiProvenanceStoreUnavailable):
            open_ai_provenance_store(bad)
    assert not missing.exists() and sorted(p.name for p in tmp_path.iterdir()) == ["notes.sqlite3"]
    with pytest.raises(DatabaseVersionError):
        open_ai_provenance_store(_old_schema(tmp_path))


def test_the_opened_store_owns_a_foreign_key_enforcing_writable_connection(world):
    store = open_ai_provenance_store(Path(world.path))
    connection = store._db
    try:
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute("PRAGMA query_only").fetchone()[0] == 0
        assert not store.closed
    finally:
        store.close()
    assert store.closed and _is_closed(connection)
    store.close()  # idempotent


def test_a_directly_constructed_store_does_not_own_its_connection(world):
    connection = open_database(world.path)
    store = AiProvenanceStore(connection)
    store.close()
    assert store.closed and not _is_closed(connection)
    connection.close()


def test_the_opener_closes_its_connection_when_verification_fails(tmp_path, monkeypatch):
    import baec_app.data.ai_provenance as ai_provenance

    old = _old_schema(tmp_path)
    opened = []
    real_connect = sqlite3.connect
    monkeypatch.setattr(ai_provenance.sqlite3, "connect", lambda *a, **k: opened.append(real_connect(*a, **k)) or opened[-1])
    with pytest.raises(DatabaseVersionError):
        open_ai_provenance_store(old)
    assert len(opened) == 1 and _is_closed(opened[0])


# --- runtime composition --------------------------------------------------------------------------


def test_bad_databases_are_refused_and_nothing_is_created(tmp_path):
    missing = tmp_path / "absent.sqlite3"
    for bad in (missing, ":memory:", "file:absent.sqlite3"):
        with pytest.raises(ReadDatabaseUnavailable):
            open_extraction_runtime(bad, provider=FakeProvider())
    with pytest.raises(DatabaseVersionError):
        open_extraction_runtime(_old_schema(tmp_path), provider=FakeProvider())
    assert not missing.exists()


def _tracking(monkeypatch):
    opened = {}
    real_read, real_store = composition.open_read_connection, composition.open_ai_provenance_store
    monkeypatch.setattr(composition, "open_read_connection",
                        lambda p: opened.setdefault("read", real_read(p)))
    monkeypatch.setattr(composition, "open_ai_provenance_store",
                        lambda p: opened.setdefault("store", real_store(p)))
    return opened


def test_a_runtime_has_a_query_only_read_side_and_a_separate_writable_provenance_side(world, monkeypatch):
    opened = _tracking(monkeypatch)
    with open_extraction_runtime(world.path, provider=FakeProvider()) as runtime:
        assert type(runtime) is ExtractionRuntime and type(runtime.service) is ExtractionService
        read, store = opened["read"], opened["store"]
        assert read.execute("PRAGMA query_only").fetchone()[0] == 1
        assert store._db.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        reachable = _reachable(runtime)
        connections = [o for o in reachable if type(o) is sqlite3.Connection]
        assert {id(c) for c in connections} == {id(read), id(store._db)}
        repositories = [o for o in reachable if type(o) is Repository]
        assert repositories and all(r._db is read for r in repositories)  # domain reads only on the read side
        assert [type(o) for o in reachable if type(o) is ReadService] == [ReadService]
        assert not [o for o in reachable if isinstance(o, COMMAND_SIDE)]
        assert not [o for o in reachable if type(o).__module__.startswith(("baec_app.mcp", "mcp"))]
    assert runtime.closed and _is_closed(read) and _is_closed(store._db)
    with pytest.raises(RuntimeError):
        runtime.service
    runtime.close()  # idempotent


def test_a_failure_opening_the_store_closes_the_read_connection(world, monkeypatch):
    opened = _tracking(monkeypatch)

    def fail(path):
        raise AiProvenanceStoreUnavailable("synthetic")

    monkeypatch.setattr(composition, "open_ai_provenance_store", fail)
    with pytest.raises(AiProvenanceStoreUnavailable):
        open_extraction_runtime(world.path, provider=FakeProvider())
    assert _is_closed(opened["read"])


def test_a_provider_construction_failure_closes_both_sides(world, monkeypatch):
    opened = _tracking(monkeypatch)

    def fail():
        raise RuntimeError("provider construction failed")

    monkeypatch.setattr(composition, "AnthropicExtractionProvider", fail)
    with pytest.raises(RuntimeError, match="provider construction failed"):
        open_extraction_runtime(world.path)
    assert _is_closed(opened["read"]) and opened["store"].closed and _is_closed(opened["store"]._db)


def test_the_default_provider_is_the_anthropic_provider_with_retries_disabled(world, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-only-not-a-real-key")
    monkeypatch.setattr(socket.socket, "connect", lambda *a, **k: (_ for _ in ()).throw(AssertionError("network")))
    with open_extraction_runtime(world.path) as runtime:
        provider = runtime.service._provider
        assert type(provider) is AnthropicExtractionProvider and provider._client.max_retries == 0


def test_default_ids_are_distinct_and_prefixed():
    run_ids = {composition.new_run_id() for _ in range(50)}
    artifact_ids = {composition.new_artifact_id() for _ in range(50)}
    assert len(run_ids) == len(artifact_ids) == 50
    assert all(i.startswith("airun_") for i in run_ids) and all(i.startswith("aiart_") for i in artifact_ids)


# --- the explicit adapter ------------------------------------------------------------------------------


def test_every_ai_status_outcome_and_speaker_maps_explicitly():
    assert set(STATUS_MAP) == set(RunStatus)
    assert {k.value: v.value for k, v in STATUS_MAP.items()} == {s.value: s.value for s in RunStatus}
    assert set(STATUS_MAP.values()) == set(AiRunStatus) - {AiRunStatus.INTERRUPTED}
    assert {k.value: v.value for k, v in OUTCOME_MAP.items()} == {o.value: o.value for o in RemoteOutcome}
    assert set(OUTCOME_MAP.values()) == set(AiRemoteOutcome)
    assert SPEAKER_MAP == {"buyer": AiAttributedSpeaker.BUYER, "seller": AiAttributedSpeaker.SELLER,
                           "unclear": AiAttributedSpeaker.UNCLEAR}


def test_the_adapter_accepts_only_exact_types(world):
    adapter = _DataLayerProvenanceStore(world.store)
    with pytest.raises(TypeError):
        _DataLayerProvenanceStore(object())
    with pytest.raises(TypeError):
        adapter.record_run(object())
    with pytest.raises(TypeError):
        adapter.record_terminal_outcome(object())


def test_every_speaker_reaches_the_store(world):
    excerpts = [{"excerpt_id": f"e{i}", "source_interaction_id": "INT-T", "text": text, "attributed_speaker": speaker}
                for i, (text, speaker) in enumerate((("Seller: Are you evaluating", "seller"),
                                                     ("We would not switch for anything under 10%.", "buyer"),
                                                     ("at the moment", "unclear")), 1)]
    result = world.run(FakeProvider(response(as_text(output(excerpts=excerpts)))))
    stored = world.store.list_artifact_excerpts(result.artifact_id)
    assert [e.attributed_speaker for e in stored] == [AiAttributedSpeaker.SELLER, AiAttributedSpeaker.BUYER,
                                                      AiAttributedSpeaker.UNCLEAR]
