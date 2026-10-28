"""MANUAL, PAID, ONE-SHOT: record the BAEC Engine 1 public-demo artifact. Never run by tests, CI, or the app.

    ANTHROPIC_API_KEY=... python scripts/record_public_demo_artifact.py \\
        --model claude-sonnet-5-5 --confirm-one-paid-anthropic-call

Supply the credential only in an ordinary shell for this one command. This file holds no credential and never
prints, logs, or stores one; it checks only that a credential variable is present.

What it does, in order, and nothing else:
1. Refuses unless --model is the approved recording model and the confirmation flag is given, unless a credential
   variable is present, and if the packaged recording already exists (a recording is never overwritten).
2. Creates a temporary schema-v7 database in a fresh system temporary directory (never var/baec_dev.sqlite3 or
   any repository path) holding only the canonical synthetic Harbor source (INT-HARBOR-001).
3. Runs the unchanged Phase 6 ExtractionService once, through the production Anthropic provider (max_retries=0,
   locked timeout), wrapped in a guard that refuses any second provider request.
4. If the run is not a success (error, timeout, refusal, parse or semantic validation failure), reports the closed
   status and codes and exits without exporting anything. It never makes a second call.
5. Otherwise exports the stored rows with build_recording_document, which re-verifies the package: canonical form
   and digest, authentic provider provenance, the public-safety scan, and a replay into a fresh :memory: database
   that passes the Phase 7D eligibility checks E1-E6 (the locked Phase 6 validator re-run) and round-trips every
   stored column. Only then is the file created (exclusive create).
6. Deletes the temporary directory on every path.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[1]
if str(REPOSITORY) not in sys.path:
    sys.path.insert(0, str(REPOSITORY))

from baec_app.ai.anthropic_provider import AnthropicExtractionProvider  # noqa: E402
from baec_app.ai.composition import open_extraction_runtime  # noqa: E402
from baec_app.application.public_demo_recording import (  # noqa: E402
    RECORDING_MODEL,
    RECORDING_PATH,
    RecordingRejected,
    approved_demo_source,
    build_recording_document,
)
from baec_app.data.ai_provenance import AiProvenanceStore  # noqa: E402
from baec_app.data.database import connect, open_database  # noqa: E402
from baec_app.data.records import SourceInteraction  # noqa: E402
from baec_app.data.repository import Repository, decode_datetime  # noqa: E402
from baec_app.domain.models import Account  # noqa: E402

MAX_PROVIDER_REQUESTS = 1
AUTH_VARIABLES = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")  # presence only; values are never read out
CONFIRM_FLAG = "--confirm-one-paid-anthropic-call"


class RecordingFailed(Exception):
    """No recording was produced. The message carries closed codes only."""


class OneRequestProvider:
    """Delegates to one provider and refuses any provider request after the first."""

    def __init__(self, inner) -> None:
        self._inner = inner
        self.provider_name = inner.provider_name
        self.sdk_name = inner.sdk_name
        self.sdk_version = inner.sdk_version
        self.requests = 0

    def prepare_request(self, **arguments):
        return self._inner.prepare_request(**arguments)

    def invoke(self, spec):
        if self.requests >= MAX_PROVIDER_REQUESTS:
            raise RecordingFailed("second_provider_request_refused")
        self.requests += 1
        return self._inner.invoke(spec)


def _seed_source(database_path: Path) -> None:
    source = approved_demo_source()
    connection = open_database(str(database_path))
    try:
        repository = Repository(connection)
        repository.add_account(Account(source["account_id"], source["account_name"]))
        repository.add_interaction(SourceInteraction(source["interaction_id"], source["account_id"],
                                                     decode_datetime(source["occurred_at"]), source["text"]))
    finally:
        connection.close()


def record(*, provider, output_path: Path = RECORDING_PATH, model: str = RECORDING_MODEL) -> str:
    """Make at most one provider request and write the verified package. Returns the package digest."""
    if model != RECORDING_MODEL:
        raise RecordingFailed("model_not_approved")
    output_path = Path(output_path)
    if output_path.exists():
        raise RecordingFailed("recording_exists")
    guarded = OneRequestProvider(provider)
    directory = Path(tempfile.mkdtemp(prefix="baec-public-demo-recording-"))
    try:
        if REPOSITORY in directory.resolve().parents:
            raise RecordingFailed("temporary_directory_inside_repository")
        database = directory / "recording.sqlite3"
        _seed_source(database)
        source = approved_demo_source()
        runtime = open_extraction_runtime(database, provider=guarded)
        try:
            result = runtime.service.extract_interaction(account_id=source["account_id"],
                                                         interaction_id=source["interaction_id"], model=model)
        finally:
            runtime.close()
        if guarded.requests != MAX_PROVIDER_REQUESTS:
            raise RecordingFailed("provider_request_count_invalid")
        connection = connect(str(database))
        try:
            if result.artifact_id is None:
                stored = AiProvenanceStore(connection).get_result(result.ai_run_id)
                codes = ",".join(stored.failure_codes) or "-"
                raise RecordingFailed(f"run_not_successful status={stored.status.value} codes={codes}")
            try:
                text = build_recording_document(connection, result.artifact_id)
            except RecordingRejected as rejected:
                raise RecordingFailed(f"recording_rejected code={rejected.code}") from None
        finally:
            connection.close()
        output_path.parent.mkdir(exist_ok=True)
        with open(output_path, "x", encoding="utf-8") as handle:  # exclusive create: never overwrites
            handle.write(text)
        return json.loads(text)["package_digest"]
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python scripts/record_public_demo_artifact.py",
                                     description="Make ONE paid Anthropic call to record the public-demo artifact.")
    parser.add_argument("--model", required=True)
    parser.add_argument(CONFIRM_FLAG, dest="confirmed", action="store_true", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    if not arguments.confirmed:
        print("refused: the confirmation flag is required", file=sys.stderr)
        return 2
    if not any(os.environ.get(name) for name in AUTH_VARIABLES):
        print("refused: no Anthropic credential variable is present in this shell", file=sys.stderr)
        return 2
    try:
        digest = record(provider=AnthropicExtractionProvider(), model=arguments.model)
    except RecordingFailed as failure:
        print(f"no recording produced: {failure}", file=sys.stderr)
        return 1
    print(f"recorded {RECORDING_PATH.relative_to(REPOSITORY)} package_digest={digest}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
