"""BAEC Engine 1 public demo: the recording/retention path, tested before any paid call.

The authentic recording does not exist yet. Every test here uses an explicit TEST-ONLY fixture package produced
offline by the real Phase 6 ExtractionService driven by the FakeProvider, so it carries FIXTURE- identities and a
fake SDK. That package is never written to public_demo_assets/ and is refused as the public recording. A few tests
also build an in-memory COUNTERFEIT variant (fixture identity replaced) to exercise each authenticity gate on its
own, and to pin the documented limit: content checks cannot detect a deliberate forgery.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

import scripts.record_public_demo_artifact as recorder
from baec_app.ai.composition import open_extraction_runtime
from baec_app.ai.provider import ProviderApiError
from baec_app.application import public_demo_recording as module
from baec_app.application.ai_proposal_mapping import AiProposalMappingService, canonical_text
from baec_app.application.public_demo_recording import (
    RECORDING_FAILURE_CODES,
    RECORDING_MODEL,
    RECORDING_PATH,
    RecordingRejected,
    approved_demo_source,
    load_public_demo_database,
    public_safety_findings,
    verify_public_demo_recording,
)
from baec_app.data.database import BRIDGE_TABLES, DATA_TABLES, connect
from baec_app.data.proposal_bridge import sha256_text
from tests.ai_builders import START, FakeProvider, Ids, as_text, hypotheses, output, response
from tests.application_builders import FixedClock

REPO = Path(__file__).resolve().parents[1]
FIXTURE_MODEL = "FIXTURE-model-not-a-real-invocation"
HARBOR_CONDITION = "If our supplier raises pricing by more than 10% when our agreement renews, we'd evaluate other options."
HARBOR_NOT_EVALUATING = "No. We're not looking at other suppliers right now."


def harbor_output(**changes) -> dict:
    """A valid baec-extraction-output/v1 object for the Harbor source (verbatim excerpts)."""
    values = dict(
        excerpts=[{"excerpt_id": "e1", "source_interaction_id": "INT-HARBOR-001", "text": HARBOR_CONDITION,
                   "attributed_speaker": "buyer"},
                  {"excerpt_id": "e2", "source_interaction_id": "INT-HARBOR-001", "text": HARBOR_NOT_EVALUATING,
                   "attributed_speaker": "buyer"}],
        normalized_condition="A price increase of more than 10% when the agreement renews.",
        normalized_evaluation_link="The buyer says such an increase would lead them to evaluate other options.",
        criterion_hypotheses=hypotheses(refs=("e1",)),
    )
    values.update(changes)
    return output(interaction_id="INT-HARBOR-001", **values)


def fixture_rows(tmp_path: Path, *, reply=None, model=FIXTURE_MODEL) -> dict:
    """The stored Phase 6 rows of one offline FakeProvider extraction of the Harbor source. TEST-ONLY."""
    database = tmp_path / "fixture-recording.sqlite3"
    recorder._seed_source(database)
    reply = reply or response(as_text(harbor_output()), model=model)
    runtime = open_extraction_runtime(database, provider=FakeProvider(reply), clock=FixedClock(START),
                                      run_ids=Ids("FIXTURE-RUN"), artifact_ids=Ids("FIXTURE-ART"))
    try:
        result = runtime.service.extract_interaction(account_id="ACC-HARBOR", interaction_id="INT-HARBOR-001",
                                                     model=model)
    finally:
        runtime.close()
    assert result.artifact_id is not None, result.status
    connection = connect(str(database))
    try:
        return module._export_rows(connection, result.artifact_id)
    finally:
        connection.close()


def package_text(rows: dict, **body_changes) -> str:
    body = {"recording_version": module.RECORDING_VERSION, "purpose": module.RECORDING_PURPOSE,
            "notice": module.RECORDING_NOTICE, "prerecorded": True, "synthetic_source": True,
            "source": approved_demo_source(), **rows}
    body.update(body_changes)
    return canonical_text({"package_digest": sha256_text(canonical_text(body)), "recording": body})


def counterfeit(rows: dict) -> dict:
    """An IN-MEMORY, TEST-ONLY copy with the fixture identity replaced. Never written anywhere."""
    text = json.dumps(rows)
    for old, new in (("FIXTURE-RUN-001", "COUNTERFEIT-RUN-001"), ("FIXTURE-ART-001", "COUNTERFEIT-ART-001"),
                     (FIXTURE_MODEL, RECORDING_MODEL), ("fake-sdk", "anthropic"),
                     ("0.0.0-test", "0.0.0-counterfeit"), ("msg_fake_01", "msg_counterfeit_test"),
                     ("req_fake_01", "req_counterfeit_test")):
        text = text.replace(old, new)
    return json.loads(text)


@pytest.fixture
def rows(tmp_path):
    return fixture_rows(tmp_path)


def rejected(code, function, *args):
    with pytest.raises(RecordingRejected) as raised:
        function(*args)
    assert raised.value.code == code
    return raised.value


# --- the packaged recording: valid before and after it is created -----------------------------------------------


def test_the_public_loader_fails_closed_when_the_recording_is_missing(tmp_path, monkeypatch):
    """Deterministic: a test-only substitution of the fixed path, whatever the real asset's state."""
    monkeypatch.setattr(module, "RECORDING_PATH", tmp_path / "absent" / module.RECORDING_FILE_NAME)
    rejected("recording_missing", load_public_demo_database)


def test_the_packaged_recording_is_either_absent_or_verified_for_publication():
    if not RECORDING_PATH.exists():
        rejected("recording_missing", load_public_demo_database)
        return
    package = verify_public_demo_recording(RECORDING_PATH.read_text(encoding="utf-8"))
    assert package.body["run"]["requested_model"] == RECORDING_MODEL
    connection = load_public_demo_database()
    try:
        assert connection.execute("SELECT COUNT(*) FROM ai_artifacts").fetchone()[0] == 1
    finally:
        connection.close()


def test_the_loader_reads_only_the_one_fixed_packaged_path():
    import inspect
    assert list(inspect.signature(load_public_demo_database).parameters) == []
    assert RECORDING_PATH == REPO / "public_demo_assets" / "baec-engine1-harbor-recording.json"


# --- the synthetic source and the model identity --------------------------------------------------------------------


def test_the_approved_source_is_the_canonical_synthetic_harbor_interaction():
    source = approved_demo_source()
    seed = json.loads((REPO / "data" / "demo" / "interactions.json").read_text(encoding="utf-8"))
    [harbor] = [i for i in seed["interactions"] if i["interaction_id"] == "INT-HARBOR-001"]
    assert (source["account_id"], source["interaction_id"], source["text"]) == (
        "ACC-HARBOR", "INT-HARBOR-001", harbor["text"])
    assert source["account_name"] == "Harbor Surgical Center"
    policy = json.loads((REPO / "data" / "demo" / "accounts.json").read_text(encoding="utf-8"))["data_policy"]
    assert "synthetic" in json.dumps(policy).lower()


def test_the_recording_model_is_the_phase6_live_path_model_and_only_historical():
    from tests.live.harness import COMPARISON_MODELS
    assert RECORDING_MODEL == "claude-sonnet-5-5" and RECORDING_MODEL in COMPARISON_MODELS
    assert "does not qualify a default model" in module.RECORDING_NOTICE


# --- the package format and digest ------------------------------------------------------------------------------------


def test_a_fixture_package_parses_and_seeds_an_isolated_in_memory_v7_database(rows):
    package = module._parse_package(package_text(rows))
    connection = module._seed_ephemeral_database(package)
    try:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 7
        assert [row[2] for row in connection.execute("PRAGMA database_list")] == [""]  # :memory:, no file
        counts = {t: connection.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in DATA_TABLES}
        assert (counts["accounts"], counts["interactions"], counts["ai_runs"], counts["ai_run_results"],
                counts["ai_run_outputs"], counts["ai_artifacts"]) == (1, 1, 1, 1, 1, 1)
        assert counts["ai_artifact_excerpts"] == 2
        assert all(counts[t] == 0 for t in BRIDGE_TABLES + ("baec_records", "human_authorizations",
                                                            "account_state_transitions", "dormancy_judgments"))
        assert connection.execute("SELECT account_id, state FROM accounts").fetchall() == [("ACC-HARBOR", None)]
        assert module._export_rows(connection, rows["artifact"]["artifact_id"]) == rows  # exact round trip
        # The Phase 7D path accepts the replayed lineage and maps it to an AI_DRAFT.
        proposal = AiProposalMappingService(connection, clock=FixedClock(START)).map_artifact(rows["artifact"]["artifact_id"],
                                                                      created_by="TEST-reviewer").proposal
        assert proposal.origin == "AI_DRAFT" and proposal.interaction_id == "INT-HARBOR-001"
    finally:
        connection.close()


def test_each_seeding_creates_a_new_independent_database(rows):
    package = module._parse_package(package_text(rows))
    first, second = module._seed_ephemeral_database(package), module._seed_ephemeral_database(package)
    try:
        assert first is not second
        AiProposalMappingService(first, clock=FixedClock(START)).map_artifact(rows["artifact"]["artifact_id"], created_by="TEST-reviewer")
        assert second.execute("SELECT COUNT(*) FROM ai_proposals").fetchone()[0] == 0
    finally:
        first.close()
        second.close()


def test_a_tampered_package_fails_its_digest(rows):
    text = package_text(rows)
    document = json.loads(text)
    document["recording"]["output"]["raw_output_text"] += " "
    rejected("digest_mismatch", module._parse_package, canonical_text(document))
    document = json.loads(text)
    document["package_digest"] = "0" * 64
    rejected("digest_mismatch", module._parse_package, canonical_text(document))


def test_a_non_canonical_or_malformed_package_is_refused(rows):
    text = package_text(rows)
    rejected("not_canonical", module._parse_package, json.dumps(json.loads(text), indent=1))
    rejected("malformed", module._parse_package, "not json")
    rejected("malformed", module._parse_package, canonical_text({"recording": {}}))
    without = dict(rows)
    without["run"] = {k: v for k, v in rows["run"].items() if k != "requested_model"}
    rejected("malformed", module._parse_package, package_text(without))  # a missing provenance column
    rejected("malformed", module._parse_package, package_text(rows, prerecorded=False))
    rejected("malformed", module._parse_package, package_text(rows, purpose="seed data"))


def test_an_unsupported_version_is_refused(rows):
    rejected("unsupported_version", module._parse_package,
             package_text(rows, recording_version="baec-public-demo-recording/v2"))


# --- authenticity: fixture and fake-provider output is never the public recording -----------------------------------------


def test_a_fixture_package_is_refused_as_the_public_recording(rows):
    rejected("fake_provenance", verify_public_demo_recording, package_text(rows))
    assert {"fixture_identity"} <= set(public_safety_findings(json.loads(package_text(rows))["recording"]))


@pytest.mark.parametrize("marker", module.FIXTURE_MARKERS)
def test_any_fixture_or_fake_provider_marker_anywhere_is_refused(rows, marker):
    fake = counterfeit(rows)
    fake["result"]["provider_request_id"] = f"req_{marker}"
    rejected("fake_provenance", verify_public_demo_recording, package_text(fake))


@pytest.mark.parametrize("field,value,code", [
    ("sdk_name", "fake-sdk", "fake_provenance"), ("sdk_name", "other-sdk", "fake_provenance"),
    ("requested_model", "claude-opus-5-5", "fake_provenance"),
], ids=["fake sdk", "other sdk", "other model"])
def test_the_provider_identity_must_be_the_real_anthropic_sdk_and_the_recording_model(rows, field, value, code):
    fake = counterfeit(rows)
    fake["run"][field] = value
    rejected(code, verify_public_demo_recording, package_text(fake))


def test_missing_provider_provenance_is_refused(rows):
    fake = counterfeit(rows)
    fake["result"]["provider_message_id"] = None
    rejected("provenance_missing", verify_public_demo_recording, package_text(fake))


def test_a_source_other_than_the_approved_synthetic_interaction_is_refused(rows):
    fake = counterfeit(rows)
    source = dict(approved_demo_source(), text=approved_demo_source()["text"] + " Call me.")
    rejected("source_not_approved", verify_public_demo_recording, package_text(fake, source=source))


def test_unsafe_content_is_refused(rows):
    fake = counterfeit(rows)
    fake["run"]["sdk_version"] = "/Users/someone/anthropic"
    rejected("unsafe_content", verify_public_demo_recording, package_text(fake))


def test_malformed_lineage_is_refused(rows):
    fake = counterfeit(rows)
    fake["result"]["status"] = "semantic_validation_failure"
    rejected("lineage_invalid", verify_public_demo_recording, package_text(fake))
    fake = counterfeit(rows)
    fake["excerpts"][0]["text"] = fake["excerpts"][0]["text"][:-1]
    rejected("lineage_invalid", verify_public_demo_recording, package_text(fake))
    fake = counterfeit(rows)
    fake["artifact"]["interaction_id"] = "INT-OTHER"
    rejected("source_not_approved", verify_public_demo_recording, package_text(fake))


def test_content_checks_cannot_detect_a_deliberate_forgery_which_is_a_documented_limit(rows):
    """A hand-edited copy with plausible identity passes: the digest is repository-asset integrity, not proof."""
    assert verify_public_demo_recording(package_text(counterfeit(rows))).package_digest
    assert "repository-asset integrity only" in module.__doc__.replace("\n", " ")


# --- public safety scan ------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("value,finding", [
    ("sk-ant-CANARYKEYQX0001", "credential"), ("Authorization: Bearer CANARYTOKENQX", "credential"),
    ("ANTHROPIC_API_KEY", "credential"), ("x-api-key", "credential"), ("/home/alice/notes", "local_path"),
    ("C:\\Users\\alice", "local_path"), ("/private/var/folders/x", "local_path"), ("alice@example.com", "email"),
    ("(555) 123-4567", "phone"), ("123-45-6789", "personal_record"), ("MRN 12345", "personal_record"),
    ("FIXTURE-ART-001", "fixture_identity"), ("fake-sdk", "fixture_identity"),
])
def test_the_public_safety_scan_finds_each_category(value, finding):
    assert finding in public_safety_findings({"value": value})


def test_the_approved_source_and_ordinary_provenance_values_scan_clean(rows):
    body = json.loads(package_text(counterfeit(rows)))["recording"]
    assert public_safety_findings(body) == ()


# --- the manual recording utility, exercised offline -----------------------------------------------------------------------


def test_the_utility_cannot_package_fake_provider_output_and_writes_nothing(tmp_path):
    target = tmp_path / "recording.json"
    provider = FakeProvider(response(as_text(harbor_output()), model=RECORDING_MODEL))
    with pytest.raises(recorder.RecordingFailed, match="recording_rejected code=fake_provenance"):
        recorder.record(provider=provider, output_path=target)
    assert len(provider.invoked) == 1 and not target.exists()


@pytest.mark.parametrize("reply,status", [
    (ProviderApiError("rate_limited", provider_request_id=None), "api_error"),
    (response(as_text(harbor_output(normalized_condition="A price increase of more than 25%.")),
              model=RECORDING_MODEL), "semantic_validation_failure"),
    (response("not json", model=RECORDING_MODEL), "parse_failure"),
    (response(as_text(harbor_output()), model="claude-opus-5-5"), "model_mismatch"),
], ids=["api error", "semantic failure", "parse failure", "model mismatch"])
def test_a_failed_call_produces_no_recording_and_is_never_retried(tmp_path, reply, status):
    target = tmp_path / "recording.json"
    provider = FakeProvider(reply)
    with pytest.raises(recorder.RecordingFailed, match=f"run_not_successful status={status}"):
        recorder.record(provider=provider, output_path=target)
    assert len(provider.invoked) == 1 and not target.exists()


def test_the_one_request_guard_refuses_a_second_request():
    provider = FakeProvider(response(as_text(harbor_output()), model=RECORDING_MODEL))
    guarded = recorder.OneRequestProvider(provider)
    guarded.invoke(None)
    with pytest.raises(recorder.RecordingFailed, match="second_provider_request_refused"):
        guarded.invoke(None)
    assert len(provider.invoked) == 1 and recorder.MAX_PROVIDER_REQUESTS == 1


def test_the_utility_refuses_another_model_and_never_overwrites(tmp_path):
    provider = FakeProvider(response(as_text(harbor_output()), model=RECORDING_MODEL))
    with pytest.raises(recorder.RecordingFailed, match="model_not_approved"):
        recorder.record(provider=provider, output_path=tmp_path / "a.json", model="claude-opus-5-5")
    existing = tmp_path / "b.json"
    existing.write_text("keep", encoding="utf-8")
    with pytest.raises(recorder.RecordingFailed, match="recording_exists"):
        recorder.record(provider=provider, output_path=existing)
    assert provider.invoked == [] and existing.read_text(encoding="utf-8") == "keep"


def test_the_command_refuses_without_confirmation_or_a_credential(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("no provider may be built")

    monkeypatch.setattr(recorder, "AnthropicExtractionProvider", forbidden)
    with pytest.raises(SystemExit):
        recorder.main(["--model", RECORDING_MODEL])  # the confirmation flag is required
    for name in recorder.AUTH_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    assert recorder.main(["--model", RECORDING_MODEL, recorder.CONFIRM_FLAG]) == 2


def test_the_utility_uses_the_production_provider_with_retries_disabled_and_no_dev_database():
    source = (REPO / "scripts" / "record_public_demo_artifact.py").read_text(encoding="utf-8").split('"""', 2)[2]
    assert "record(provider=AnthropicExtractionProvider()" in source  # its constructor enforces max_retries=0
    for forbidden in ("baec_dev", "var/", "max_retries=", "api_key=", "sk-ant-", "print(os.environ"):
        assert forbidden not in source, forbidden


# --- offline loading -----------------------------------------------------------------------------------------------------------


def test_loading_makes_no_network_model_or_credential_access(tmp_path, rows):
    package = tmp_path / "package.json"
    package.write_text(package_text(rows), encoding="utf-8")
    code = f"""
import json, os, socket, sys
def blocked(*a, **k): raise AssertionError("network access attempted")
socket.socket = blocked
socket.create_connection = blocked
class Environ(dict):
    def __getitem__(self, key):
        if key.startswith("ANTHROPIC"): raise AssertionError("credential read")
        return super().__getitem__(key)
    def get(self, key, default=None):
        if key.startswith("ANTHROPIC"): raise AssertionError("credential read")
        return super().get(key, default)
os.environ = Environ(os.environ)
from baec_app.application import public_demo_recording as m
connection = m._seed_ephemeral_database(m._parse_package(open({str(package)!r}).read()))
connection.close()
loaded = [n for n in sys.modules if n == "anthropic" or n.startswith(("anthropic.", "baec_app.ai.anthropic_provider",
          "baec_app.ai.provider", "baec_app.ai.service", "baec_app.ai.composition", "httpx", "streamlit"))]
print(json.dumps(loaded))
"""
    done = subprocess.run([sys.executable, "-c", code], cwd=REPO, capture_output=True, text=True, timeout=60,
                          env={"PATH": "/usr/bin:/bin", "ANTHROPIC_API_KEY": "canary-not-a-key"})
    assert done.returncode == 0, done.stderr[-2000:]
    assert json.loads(done.stdout) == []


def test_the_failure_codes_are_closed():
    assert RECORDING_FAILURE_CODES == ("recording_missing", "not_canonical", "digest_mismatch", "unsupported_version",
                                       "malformed", "source_not_approved", "fake_provenance", "provenance_missing",
                                       "unsafe_content", "lineage_invalid", "not_mappable")
    with pytest.raises(ValueError):
        RecordingRejected("something_else")


# --- the guard exception stays narrow ------------------------------------------------------------------------------------------

PROVENANCE_KEYS = ("canonical_result", "raw_output_text", "artifact_digest", "ai_run_id")


def provenance_json_files(root: Path) -> list[str]:
    """Repository JSON outside tests/ that carries Phase 6 provenance keys."""
    found = []
    for path in sorted(root.rglob("*.json")):
        parts = path.relative_to(root).parts
        if parts[0] in ("tests", ".venv", ".git", "var") or "__pycache__" in parts:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if any(f'"{key}"' in text for key in PROVENANCE_KEYS):
            found.append(path.relative_to(root).as_posix())
    return found


ALLOWED_ASSETS = {"README.md", "baec-engine1-harbor-recording.json", "BAEC_Engine_1_Research_and_Technical_Brief.pdf"}


def asset_violations(directory: Path) -> list[str]:
    """Anything in the public asset directory other than the three approved files (no pattern is allowed)."""
    return sorted(p.name for p in directory.iterdir() if p.name not in ALLOWED_ASSETS or not p.is_file())


def test_the_asset_directory_holds_only_its_readme_and_the_one_approved_recording(tmp_path):
    assert ALLOWED_ASSETS == {"README.md", module.RECORDING_FILE_NAME, "BAEC_Engine_1_Research_and_Technical_Brief.pdf"}
    assert asset_violations(REPO / "public_demo_assets") == []
    # Negative control: no other PDF, JSON, or file is allowed, whatever its name looks like.
    for name in ("BAEC_Engine_1_Demo_Guide.pdf", "notes.pdf", "other-recording.json", "BAEC_Engine_1_Final.pdf",
                 "baec-engine1-harbor-recording_v2.json"):
        (tmp_path / name).write_bytes(b"x")
    (tmp_path / "README.md").write_text("ok", encoding="utf-8")
    assert asset_violations(tmp_path) == sorted(["BAEC_Engine_1_Demo_Guide.pdf", "notes.pdf", "other-recording.json",
                                                 "BAEC_Engine_1_Final.pdf", "baec-engine1-harbor-recording_v2.json"])
    readme = (REPO / "public_demo_assets" / "README.md").read_text(encoding="utf-8")
    assert module.RECORDING_PURPOSE in readme and "does not qualify a default model" in readme


def test_ai_provenance_json_may_exist_only_at_the_approved_recording_path(tmp_path):
    assert set(provenance_json_files(REPO)) <= {RECORDING_PATH.relative_to(REPO).as_posix()}
    # Negative control: provenance in an ordinary data file anywhere else is detected.
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "demo.json").write_text('{"canonical_result": "{}"}', encoding="utf-8")
    assert provenance_json_files(tmp_path) == ["data/demo.json"]


def test_no_production_or_seed_code_imports_the_recorder_or_seeds_from_the_recording():
    for path in sorted((REPO / "baec_app").rglob("*.py")):
        assert "record_public_demo_artifact" not in path.read_text(encoding="utf-8"), path.name
    for path in (REPO / "baec_app" / "data" / "seed.py", REPO / "scripts" / "seed_demo.py"):
        text = path.read_text(encoding="utf-8")
        assert "public_demo_recording" not in text and "public_demo_assets" not in text, path.name


def test_the_loader_only_ever_opens_an_in_memory_database():
    import ast
    tree = ast.parse((REPO / "baec_app" / "application" / "public_demo_recording.py").read_text(encoding="utf-8"))
    opens = [ast.unparse(n) for n in ast.walk(tree) if isinstance(n, ast.Call)
             and getattr(n.func, "id", getattr(n.func, "attr", "")) in ("open_database", "connect")]
    assert sorted(opens) == ["connect(':memory:')", "open_database(':memory:')"]  # in-memory only
    names = {getattr(n, "id", getattr(n, "attr", None)) for n in ast.walk(tree)}
    assert not names & {"environ", "getenv", "urlopen", "socket", "AnthropicExtractionProvider",
                        "open_extraction_runtime", "seed_database"}


# --- publication requires a successful Phase 7D mapping ---------------------------------------------------------------------


def test_publication_maps_the_replayed_artifact_to_a_new_ai_draft(rows, monkeypatch):
    calls = []
    original = AiProposalMappingService.map_artifact

    def spy(self, artifact_id, *, created_by):
        mapped = original(self, artifact_id, created_by=created_by)
        calls.append((artifact_id, mapped.created, mapped.proposal.origin))
        return mapped

    monkeypatch.setattr(AiProposalMappingService, "map_artifact", spy)
    fake = counterfeit(rows)
    verify_public_demo_recording(package_text(fake))
    assert calls == [(fake["artifact"]["artifact_id"], True, "AI_DRAFT")]


def test_an_artifact_without_possible_baec_language_is_refused_for_publication(tmp_path):
    for status in ("no_clear_baec_language", "insufficient_context"):
        directory = tmp_path / status
        directory.mkdir()
        fake = counterfeit(fixture_rows(directory, reply=response(as_text(harbor_output(analysis_status=status)),
                                                                   model=FIXTURE_MODEL)))
        rejected("lineage_invalid", verify_public_demo_recording, package_text(fake))  # E5, before any mapping


def test_a_mapping_refusal_blocks_publication(rows, monkeypatch):
    from baec_app.application.ai_proposal_mapping import ArtifactNotEligible

    def refuse(self, artifact_id, *, created_by):
        raise ArtifactNotEligible("stored_proposal_mismatch")

    monkeypatch.setattr(AiProposalMappingService, "map_artifact", refuse)
    rejected("not_mappable", verify_public_demo_recording, package_text(counterfeit(rows)))


def test_the_utility_writes_nothing_when_the_mapping_gate_refuses(tmp_path, monkeypatch):
    """With authenticity bypassed for this test only, the mapping gate alone still stops publication."""
    monkeypatch.setattr(module, "_require_authentic", lambda body: None)
    monkeypatch.setattr(module, "_require_mappable",
                        lambda package: (_ for _ in ()).throw(RecordingRejected("not_mappable")))
    target = tmp_path / "recording.json"
    provider = FakeProvider(response(as_text(harbor_output()), model=RECORDING_MODEL))
    with pytest.raises(recorder.RecordingFailed, match="recording_rejected code=not_mappable"):
        recorder.record(provider=provider, output_path=target)
    assert len(provider.invoked) == 1 and not target.exists()


def test_the_publication_path_is_build_then_full_verification_before_any_write():
    import ast
    tree = ast.parse((REPO / "baec_app" / "application" / "public_demo_recording.py").read_text(encoding="utf-8"))
    [build] = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "build_recording_document"]
    assert "verify_public_demo_recording(text)" in ast.unparse(build)
    [verify] = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "verify_public_demo_recording"]
    assert [ast.unparse(s) for s in verify.body[1:]] == [
        "package = _parse_package(text)", "_require_authentic(package.body)", "_require_mappable(package)",
        "return package"]
    script = (REPO / "scripts" / "record_public_demo_artifact.py").read_text(encoding="utf-8")
    assert script.index("build_recording_document(connection, result.artifact_id)") < script.index(
        'open(output_path, "x"')
