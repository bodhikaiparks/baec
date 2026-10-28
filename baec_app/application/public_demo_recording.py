"""The BAEC Engine 1 public-demo recording: one authentic, prerecorded Phase 6 artifact for a synthetic source.

PURPOSE. Authentic prerecorded synthetic AI output for public demonstration. The packaged file
public_demo_assets/baec-engine1-harbor-recording.json holds the exact persisted Phase 6 rows of one successful
extraction (run, terminal result, raw returned text, artifact, excerpts) for the canonical synthetic interaction
INT-HARBOR-001. A public demo replays those rows into a fresh, per-session, in-memory schema-v7 database, so a
visitor sees the same artifact that passed Phase 6. No model is called while the demo runs.

WHAT THE RECORDING IS NOT. It is one successful synthetic demonstration artifact. It does not change Phase 6
behavioral eligibility (FAIL), does not qualify a default model (NO), and does not validate the BAEC theory. The
requested model is shown only as historical provenance, never as a recommendation.

FORMAT (baec-public-demo-recording/v1). A package file whose bytes are exactly the baec-canonical-json/v1 text of
{"package_digest": <hex>, "recording": <body>}, where package_digest is the SHA-256 of the canonical text of the
body. The body records the purpose and notice, the synthetic source (bound byte for byte to the canonical seed),
and the five Phase 6 rows as stored, column by column. Nothing is summarized. The digest gives repository-asset
integrity only: it does not prove that a file was never copied between systems.

LOADING. load_public_demo_database() reads only the fixed packaged path; no caller supplies a path. It verifies the
package digest and format, requires authentic provider provenance (no fixture or fake-provider identity), runs the
public-safety scan, then writes the rows through the Phase 6 store's own record types and writers into a new
:memory: database, re-applies the Phase 7D eligibility checks E1-E6 (which re-run the locked Phase 6 validator
through the pure verification facade), and requires the stored rows to round-trip byte for byte. Anything else
fails closed with one closed code. It imports no provider, makes no model or network call, and reads no
environment variable or credential.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from baec_app.application.ai_proposal_mapping import (
    AiProposalMappingService,
    ArtifactNotEligible,
    canonical_text,
    verify_artifact,
)
from baec_app.application.context import SystemClock
from baec_app.application.errors import ApplicationError
from baec_app.data.ai_provenance import (
    AiArtifactExcerptRecord,
    AiArtifactRecord,
    AiAttributedSpeaker,
    AiProvenanceStore,
    AiRemoteOutcome,
    AiRunOutputRecord,
    AiRunRecord,
    AiRunResultRecord,
    AiRunStatus,
    AiTerminalOutcome,
    sha256_text,
)
from baec_app.data.database import PersistenceError, connect, open_database, require_current_schema
from baec_app.data.records import RecordValidationError, SourceInteraction
from baec_app.data.repository import Repository, decode_datetime, encode_datetime
from baec_app.data.seed import load_seed_inputs
from baec_app.domain.models import Account

RECORDING_VERSION = "baec-public-demo-recording/v1"
RECORDING_PURPOSE = "authentic prerecorded synthetic AI output for public demonstration"
RECORDING_NOTICE = (
    "This recording is one successful synthetic demonstration artifact. It does not change Phase 6 behavioral "
    "eligibility, does not qualify a default model, and does not validate the BAEC theory."
)
ASSET_DIRECTORY = Path(__file__).resolve().parents[2] / "public_demo_assets"
RECORDING_FILE_NAME = "baec-engine1-harbor-recording.json"
RECORDING_PATH = ASSET_DIRECTORY / RECORDING_FILE_NAME

# The one approved synthetic source: the canonical seed's Harbor interaction (data/demo).
DEMO_ACCOUNT_ID = "ACC-HARBOR"
DEMO_INTERACTION_ID = "INT-HARBOR-001"
# The model used by the Phase 6 live verification runs (tests/live/harness.py COMPARISON_MODELS; both runs used
# it). Historical provenance only: Phase 6 qualified no default model.
RECORDING_MODEL = "claude-sonnet-5-5"
PROVIDER = "anthropic"
PROVIDER_SDK = "anthropic"

RUN_COLUMNS = (
    "ai_run_id", "provider", "task_type", "task_version", "account_id", "interaction_id", "requested_model",
    "sdk_name", "sdk_version", "prompt_version", "prompt_digest", "input_version", "input_digest",
    "output_schema_version", "output_schema_digest", "canonicalization_version", "request_spec_version",
    "request_digest", "validation_version", "requested_at", "retry_of_ai_run_id",
)
RESULT_COLUMNS = (
    "ai_run_id", "status", "remote_outcome", "provider_message_id", "response_model", "stop_reason",
    "provider_request_id", "input_tokens", "output_tokens", "cache_creation_input_tokens",
    "cache_read_input_tokens", "failure_category", "failure_codes", "output_digest", "completed_at",
)
OUTPUT_COLUMNS = ("ai_run_id", "raw_output_text", "output_digest")
ARTIFACT_COLUMNS = (
    "artifact_id", "ai_run_id", "task_type", "task_version", "output_schema_version", "account_id",
    "interaction_id", "canonical_result", "artifact_digest", "created_at",
)
EXCERPT_COLUMNS = ("artifact_id", "excerpt_id", "interaction_id", "text", "attributed_speaker")
BODY_KEYS = ("recording_version", "purpose", "notice", "prerecorded", "synthetic_source", "source", "run", "result",
             "output", "artifact", "excerpts")
SOURCE_KEYS = ("account_id", "account_name", "interaction_id", "occurred_at", "text")

# Identity markers of the offline test fixtures and fake provider (tests/ai_builders.py, tests/mapping_builders.py).
# A recording carrying any of them is refused: fixture output is never presented as recorded model output.
FIXTURE_MARKERS = ("FIXTURE-", "not-a-real-invocation", "fake-sdk", "0.0.0-test", "msg_fake", "req_fake")

RECORDING_FAILURE_CODES = (
    "recording_missing",
    "not_canonical",
    "digest_mismatch",
    "unsupported_version",
    "malformed",
    "source_not_approved",
    "fake_provenance",
    "provenance_missing",
    "unsafe_content",
    "lineage_invalid",
    "not_mappable",
)


class RecordingRejected(ApplicationError):
    """The public-demo recording was refused. Carries one closed code and no recorded text."""

    def __init__(self, code: str) -> None:
        if code not in RECORDING_FAILURE_CODES:
            raise ValueError("unknown recording failure code")
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class RecordingPackage:
    """A recording whose canonical form, digest, version, and shape were verified. Not yet authenticated."""

    package_digest: str
    body: dict


# --- the approved synthetic source -----------------------------------------------------------------------------


def approved_demo_source() -> dict:
    """The one approved source, read from the canonical synthetic seed and encoded exactly as it is stored."""
    seed = load_seed_inputs()
    [account] = [a for a in seed["accounts"]["accounts"] if a["account_id"] == DEMO_ACCOUNT_ID]
    [interaction] = [i for i in seed["interactions"]["interactions"] if i["interaction_id"] == DEMO_INTERACTION_ID]
    if interaction["account_id"] != DEMO_ACCOUNT_ID:
        raise RecordingRejected("source_not_approved")
    return {
        "account_id": DEMO_ACCOUNT_ID,
        "account_name": account["name"],
        "interaction_id": DEMO_INTERACTION_ID,
        "occurred_at": encode_datetime(datetime.fromisoformat(interaction["occurred_at"])),
        "text": interaction["text"],
    }


# --- export (used by the manual recording utility) ---------------------------------------------------------------


def _rows(connection: sqlite3.Connection, table: str, columns: tuple[str, ...], key: str, value: str) -> list[dict]:
    cursor = connection.execute(f"SELECT {', '.join(columns)} FROM {table} WHERE {key} = ? ORDER BY rowid", (value,))
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def _export_rows(connection: sqlite3.Connection, artifact_id: str) -> dict:
    [artifact] = _rows(connection, "ai_artifacts", ARTIFACT_COLUMNS, "artifact_id", artifact_id)
    run_id = artifact["ai_run_id"]
    [run] = _rows(connection, "ai_runs", RUN_COLUMNS, "ai_run_id", run_id)
    [result] = _rows(connection, "ai_run_results", RESULT_COLUMNS, "ai_run_id", run_id)
    [output] = _rows(connection, "ai_run_outputs", OUTPUT_COLUMNS, "ai_run_id", run_id)
    excerpts = _rows(connection, "ai_artifact_excerpts", EXCERPT_COLUMNS, "artifact_id", artifact_id)
    return {"run": run, "result": result, "output": output, "artifact": artifact, "excerpts": excerpts}


def build_recording_document(connection: sqlite3.Connection, artifact_id: str) -> str:
    """The package text for one stored artifact, exactly as persisted. Verified before it is returned."""
    body = {"recording_version": RECORDING_VERSION, "purpose": RECORDING_PURPOSE, "notice": RECORDING_NOTICE,
            "prerecorded": True, "synthetic_source": True, "source": approved_demo_source(),
            **_export_rows(connection, artifact_id)}
    text = canonical_text({"package_digest": sha256_text(canonical_text(body)), "recording": body})
    verify_public_demo_recording(text)  # fails closed: nothing unverified is ever returned for export
    return text


# --- verification ---------------------------------------------------------------------------------------------------


def _parse_package(text: str) -> RecordingPackage:
    """Canonical form, package digest, version, and exact shape. Says nothing about authenticity."""
    if type(text) is not str:
        raise RecordingRejected("malformed")
    try:
        document = json.loads(text)
    except ValueError:
        raise RecordingRejected("malformed") from None
    if type(document) is not dict or set(document) != {"package_digest", "recording"}:
        raise RecordingRejected("malformed")
    if canonical_text(document) != text:
        raise RecordingRejected("not_canonical")
    body = document["recording"]
    if type(body) is not dict or sha256_text(canonical_text(body)) != document["package_digest"]:
        raise RecordingRejected("digest_mismatch")
    if body.get("recording_version") != RECORDING_VERSION:
        raise RecordingRejected("unsupported_version")
    if set(body) != set(BODY_KEYS):
        raise RecordingRejected("malformed")
    shapes = (("source", SOURCE_KEYS), ("run", RUN_COLUMNS), ("result", RESULT_COLUMNS), ("output", OUTPUT_COLUMNS),
              ("artifact", ARTIFACT_COLUMNS))
    if any(type(body[name]) is not dict or tuple(sorted(body[name])) != tuple(sorted(keys)) for name, keys in shapes):
        raise RecordingRejected("malformed")
    if type(body["excerpts"]) is not list or not body["excerpts"] or any(
            type(e) is not dict or tuple(sorted(e)) != tuple(sorted(EXCERPT_COLUMNS)) for e in body["excerpts"]):
        raise RecordingRejected("malformed")
    if (body["purpose"], body["notice"], body["prerecorded"], body["synthetic_source"]) != (
            RECORDING_PURPOSE, RECORDING_NOTICE, True, True):
        raise RecordingRejected("malformed")
    return RecordingPackage(document["package_digest"], body)


def _strings(value: object):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


_CREDENTIAL = re.compile(r"sk-ant-|(?i:bearer\s+[a-z0-9._~+/-]{8,})|(?i:x-api-key)|(?i:authorization\s*:)|"
                         r"ANTHROPIC_API_KEY|ANTHROPIC_AUTH_TOKEN|-----BEGIN [A-Z ]*PRIVATE KEY-----")
_LOCAL_PATH = re.compile(r"(?<![A-Za-z0-9._-])/(?:Users|home|private|tmp|var/folders|root|Volumes)/|"
                         r"(?<![A-Za-z])[A-Za-z]:\\")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE = re.compile(r"(?<!\d)(?:\+?\d{1,2}[\s.-]?)?\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}(?!\d)")
_PERSONAL_RECORD = re.compile(r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)|\b(?:MRN|SSN|DOB|date of birth)\b",
                              re.IGNORECASE)


def public_safety_findings(body: dict) -> tuple[str, ...]:
    """Deterministic public-safety findings for a recording body, as closed category names."""
    findings = set()
    for text in _strings(body):
        if _CREDENTIAL.search(text):
            findings.add("credential")
        if _LOCAL_PATH.search(text):
            findings.add("local_path")
        if _EMAIL.search(text):
            findings.add("email")
        if _PHONE.search(text):
            findings.add("phone")
        if _PERSONAL_RECORD.search(text):
            findings.add("personal_record")
        if any(marker.lower() in text.lower() for marker in FIXTURE_MARKERS):
            findings.add("fixture_identity")
    return tuple(sorted(findings))


def _require_authentic(body: dict) -> None:
    """Authentic provider provenance for the one approved synthetic source; fixture identity is refused."""
    if any(marker.lower() in text.lower() for text in _strings(body) for marker in FIXTURE_MARKERS):
        raise RecordingRejected("fake_provenance")
    run, result = body["run"], body["result"]
    if run["provider"] != PROVIDER or run["sdk_name"] != PROVIDER_SDK:
        raise RecordingRejected("fake_provenance")
    if run["requested_model"] != RECORDING_MODEL or result["response_model"] != RECORDING_MODEL:
        raise RecordingRejected("fake_provenance")
    for value in (run["sdk_version"], result["provider_message_id"], result["completed_at"], run["requested_at"]):
        if type(value) is not str or not value.strip():
            raise RecordingRejected("provenance_missing")
    if body["source"] != approved_demo_source():
        raise RecordingRejected("source_not_approved")
    for record in (run, body["artifact"], *body["excerpts"]):
        if (record.get("account_id", DEMO_ACCOUNT_ID), record["interaction_id"]) != (DEMO_ACCOUNT_ID,
                                                                                     DEMO_INTERACTION_ID):
            raise RecordingRejected("source_not_approved")
    if public_safety_findings(body):
        raise RecordingRejected("unsafe_content")


class _AtArtifactCreation:
    """A fixed clock for the throwaway publication check: the recorded artifact's own creation time."""

    def __init__(self, at: datetime) -> None:
        self._at = at

    def now(self) -> datetime:
        return self._at


def _require_mappable(package: RecordingPackage) -> None:
    """Phase 7D must map the replayed artifact to a new AI_DRAFT, in a throwaway database that is then discarded."""
    artifact = package.body["artifact"]
    connection = _seed_ephemeral_database(package)  # replay, E1-E6, exact round trip
    try:
        mapper = AiProposalMappingService(connection, clock=_AtArtifactCreation(decode_datetime(artifact["created_at"])))
        mapped = mapper.map_artifact(artifact["artifact_id"], created_by="public-demo-publication-check")
        proposal = mapped.proposal
        # The stored proposal record type admits only the AI_DRAFT origin; this checks it is new and is ours.
        if not mapped.created or (proposal.artifact_id, proposal.account_id, proposal.interaction_id) != (
                artifact["artifact_id"], DEMO_ACCOUNT_ID, DEMO_INTERACTION_ID):
            raise RecordingRejected("not_mappable")
    except (ArtifactNotEligible, PersistenceError, RecordValidationError, ValueError, sqlite3.Error):
        raise RecordingRejected("not_mappable") from None
    finally:
        connection.close()


def verify_public_demo_recording(text: str) -> RecordingPackage:
    """Verify a package for publication: canonical form and digest, authenticity, public safety, a replay that
    passes E1-E6 (the locked Phase 6 validator re-run; possible_baec_language required) with an exact round trip,
    and a Phase 7D mapping to a new AI_DRAFT."""
    package = _parse_package(text)
    _require_authentic(package.body)
    _require_mappable(package)
    return package


# --- seeding ------------------------------------------------------------------------------------------------------


def _records(body: dict):
    try:
        run = dict(body["run"])
        run["requested_at"] = decode_datetime(run["requested_at"])
        result = dict(body["result"])
        if result["failure_codes"] is not None or result["failure_category"] is not None:
            raise RecordingRejected("lineage_invalid")  # a public recording is a success with nothing left over
        result.pop("failure_codes")
        result.update(status=AiRunStatus(result["status"]), remote_outcome=AiRemoteOutcome(result["remote_outcome"]),
                      completed_at=decode_datetime(result["completed_at"]))
        artifact = dict(body["artifact"])
        artifact["created_at"] = decode_datetime(artifact["created_at"])
        output = body["output"]
        return (
            AiRunRecord(**run),
            AiTerminalOutcome(
                result=AiRunResultRecord(**result),
                output=AiRunOutputRecord(output["ai_run_id"], output["raw_output_text"], output["output_digest"]),
                artifact=AiArtifactRecord(**artifact),
                excerpts=tuple(AiArtifactExcerptRecord(e["artifact_id"], e["excerpt_id"], e["interaction_id"], e["text"],
                                                       AiAttributedSpeaker(e["attributed_speaker"]))
                               for e in body["excerpts"]),
            ),
        )
    except (RecordValidationError, ValueError, TypeError, KeyError):
        raise RecordingRejected("lineage_invalid") from None


def _seed_ephemeral_database(package: RecordingPackage) -> sqlite3.Connection:
    """A new :memory: schema-v7 database holding only the synthetic source and the recorded Phase 6 lineage."""
    if type(package) is not RecordingPackage:
        raise TypeError("seeding requires a verified RecordingPackage")
    body = package.body
    run, outcome = _records(body)
    connection = open_database(":memory:")
    try:
        source = body["source"]
        repository = Repository(connection)
        repository.add_account(Account(source["account_id"], source["account_name"]))
        repository.add_interaction(SourceInteraction(source["interaction_id"], source["account_id"],
                                                     decode_datetime(source["occurred_at"]), source["text"]))
        store = AiProvenanceStore(connection)
        store.record_run(run)
        store.record_terminal_outcome(outcome)
        verify_artifact(body["artifact"]["artifact_id"], provenance=store, repository=repository)  # E1-E6
        replayed = _export_rows(connection, body["artifact"]["artifact_id"])
        if replayed != {name: body[name] for name in ("run", "result", "output", "artifact", "excerpts")}:
            raise RecordingRejected("lineage_invalid")  # every stored column round-trips exactly
    except (ArtifactNotEligible, PersistenceError, RecordValidationError, ValueError, sqlite3.Error):
        connection.close()
        raise RecordingRejected("lineage_invalid") from None
    except BaseException:
        connection.close()
        raise
    return connection


def load_public_demo_database() -> sqlite3.Connection:
    """The packaged recording, verified, replayed into a fresh per-call :memory: database. The path is fixed."""
    try:
        text = RECORDING_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise RecordingRejected("recording_missing") from None
    package = _parse_package(text)
    _require_authentic(package.body)
    return _seed_ephemeral_database(package)


# --- per-visitor demo sessions -------------------------------------------------------------------------------------
#
# A Streamlit session runs each rerun on a new script thread, and a sqlite3 connection may only be used on the thread
# that created it. A session therefore keeps no connection: it keeps the serialized image of its own in-memory
# database (bytes, in that visitor's session state only). Each rerun opens a fresh :memory: connection on its own
# thread, deserializes the image, uses the unchanged application services, snapshots, and closes. Nothing is
# written to disk, nothing is shared between visitors, and no connection outlives one script run.

DEMO_MAPPER_LABEL = "public-demo-session"
SESSION_UNSUPPORTED = ("This Python and SQLite build cannot serialize an in-memory database (Python 3.11 or later "
                       "with SQLite serialization support is required). The demo does not fall back to files.")


def session_support_problem(connection_type: type = sqlite3.Connection) -> str | None:
    """None when per-session in-memory images work here; otherwise a plain explanation. Never touches the disk."""
    if not all(callable(getattr(connection_type, name, None)) for name in ("serialize", "deserialize")):
        return SESSION_UNSUPPORTED
    try:
        probe = connection_type(":memory:")
        try:
            probe.execute("CREATE TABLE probe (value INTEGER)")
            probe.execute("INSERT INTO probe VALUES (1)")
            image = probe.serialize()
        finally:
            probe.close()
        check = connection_type(":memory:")
        try:
            check.deserialize(image)
            if check.execute("SELECT value FROM probe").fetchall() != [(1,)]:
                return SESSION_UNSUPPORTED
        finally:
            check.close()
    except (sqlite3.Error, AttributeError, TypeError, ValueError, OverflowError):
        return SESSION_UNSUPPORTED
    return None


@dataclass(frozen=True)
class DemoSession:
    """One visitor's demo: the serialized image of their private in-memory database, and the AI_DRAFT under review."""

    image: bytes
    proposal_id: str


def start_demo_session() -> DemoSession:
    """A fresh session: the verified recording replayed into a new :memory: database, mapped to its AI_DRAFT by the
    unchanged Phase 7D mapper, then serialized. Nothing else is created: no review, grant, or BAEC."""
    connection = load_public_demo_database()
    try:
        [(artifact_id,)] = connection.execute("SELECT artifact_id FROM ai_artifacts").fetchall()
        mapped = AiProposalMappingService(connection, clock=SystemClock()).map_artifact(artifact_id,
                                                                                        created_by=DEMO_MAPPER_LABEL)
        return DemoSession(connection.serialize(), mapped.proposal.proposal_id)
    finally:
        connection.close()


def open_demo_database(image: bytes) -> sqlite3.Connection:
    """A new :memory: connection on the calling thread holding exactly this session image. The caller closes it."""
    if type(image) is not bytes or not image:
        raise RecordingRejected("malformed")
    connection = connect(":memory:")  # the data layer's connection: foreign keys enforced
    try:
        connection.deserialize(image)
        require_current_schema(connection)
        if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1 or \
                connection.execute("PRAGMA database_list").fetchone()[2] != "":
            raise RecordingRejected("malformed")
    except (PersistenceError, sqlite3.Error):
        connection.close()
        raise RecordingRejected("malformed") from None
    except BaseException:
        connection.close()
        raise
    return connection


def snapshot_demo_database(connection: sqlite3.Connection) -> bytes:
    """The serialized image of a session database, to keep in that visitor's session state."""
    return connection.serialize()
