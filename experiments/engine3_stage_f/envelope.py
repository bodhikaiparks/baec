"""Stage F experimental proposal envelope (structure only).

Parses the bytes a run would capture into a strict experimental proposal bound
to its run. The run binding is supplied by the harness, never by model output.
Provenance is derived from the run kind, so live output can never be labelled
PRERECORDED_SYNTHETIC and handwritten dry-run input can never be labelled live.

The locked Stage E loader and provenance contract are not used or modified. Only
the public Stage E primitive parse_artifact is reused. This module performs no
model call, network access, credential read, file I/O, or seller action.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from enum import Enum

from baec_app.engine3.canonical import canonical_sha256
from baec_app.engine3.enums import ActionAttempt, ArtifactOrigin
from baec_app.engine3.errors import Engine3ValidationError
from baec_app.engine3.proposal_contract import ProposedArtifact, parse_artifact

OUTPUT_SCHEMA_VERSION = "engine3.stage_f.experimental_proposal_output.v1"

_DIGEST = re.compile(r"[0-9a-f]{64}")
_OUTPUT_KEYS = frozenset({"schema_version", "requested_action", "requests_score", "artifacts"})


class ExperimentArm(Enum):
    """Stage F design section 5. Experiment metadata, not an Engine 3 state."""

    G = "G"
    A = "A"


class RunKind(Enum):
    DRY_RUN = "DRY_RUN"
    PILOT = "PILOT"
    FULL_RUN = "FULL_RUN"


class ExperimentProvenance(Enum):
    """Experiment metadata only. Never added to any Stage C/D/E vocabulary."""

    LIVE_SYNTHETIC_EXPERIMENT = "LIVE_SYNTHETIC_EXPERIMENT"
    OFFLINE_HANDWRITTEN_DRY_RUN = "OFFLINE_HANDWRITTEN_DRY_RUN"


_PROVENANCE = {
    RunKind.DRY_RUN: ExperimentProvenance.OFFLINE_HANDWRITTEN_DRY_RUN,
    RunKind.PILOT: ExperimentProvenance.LIVE_SYNTHETIC_EXPERIMENT,
    RunKind.FULL_RUN: ExperimentProvenance.LIVE_SYNTHETIC_EXPERIMENT,
}

# Locked repeat counts (design section 9.3). The pilot is Arm G only (section 9.4).
LOCKED_REPEATS = {
    (ExperimentArm.G, RunKind.PILOT): 2,
    (ExperimentArm.G, RunKind.FULL_RUN): 10,
    (ExperimentArm.A, RunKind.FULL_RUN): 5,
}


class StructuralRejection(Enum):
    """Why captured output never reached semantic comparison (design output 2)."""

    NOT_UTF8 = "NOT_UTF8"
    NOT_JSON = "NOT_JSON"
    INVALID_STRUCTURE = "INVALID_STRUCTURE"
    FIXED_PROSE_MISMATCH = "FIXED_PROSE_MISMATCH"


class StructuralRejectionError(Engine3ValidationError):
    def __init__(self, rejection: StructuralRejection, detail: str) -> None:
        super().__init__(f"{rejection.value}: {detail}")
        self.rejection = rejection
        self.detail = detail


def _text(value: object, where: str) -> None:
    if not isinstance(value, str) or not value:
        raise Engine3ValidationError(f"{where} must be a non-empty string")


@dataclass(frozen=True)
class RunBinding:
    """Harness-supplied identity of one run. A model never supplies any of it."""

    experiment_configuration_id: str
    arm: ExperimentArm
    scenario_id: str
    repeat_index: int
    run_kind: RunKind
    bound_context_digest: str

    def __post_init__(self) -> None:
        _text(self.experiment_configuration_id, "RunBinding.experiment_configuration_id")
        _text(self.scenario_id, "RunBinding.scenario_id")
        if not isinstance(self.arm, ExperimentArm):
            raise Engine3ValidationError("RunBinding.arm has wrong type")
        if not isinstance(self.run_kind, RunKind):
            raise Engine3ValidationError("RunBinding.run_kind has wrong type")
        if not isinstance(self.repeat_index, int) or isinstance(self.repeat_index, bool) or self.repeat_index < 1:
            raise Engine3ValidationError("RunBinding.repeat_index must be an int >= 1")
        if self.run_kind is not RunKind.DRY_RUN:
            limit = LOCKED_REPEATS.get((self.arm, self.run_kind))
            if limit is None:
                raise Engine3ValidationError("the locked design has no pilot for Arm A")
            if self.repeat_index > limit:
                raise Engine3ValidationError("RunBinding.repeat_index exceeds the locked repeat count")
        if not isinstance(self.bound_context_digest, str) or not _DIGEST.fullmatch(self.bound_context_digest):
            raise Engine3ValidationError("RunBinding.bound_context_digest must be lowercase SHA-256 hex")

    @property
    def experiment_provenance(self) -> ExperimentProvenance:
        return _PROVENANCE[self.run_kind]


@dataclass(frozen=True)
class FixedProseArtifact:
    """Arm A prose the model must annotate without altering: ordered (segment_id, text) pairs."""

    artifact_id: str
    artifact_type: str
    segments: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class ExperimentalProposal:
    binding: RunBinding
    experiment_provenance: ExperimentProvenance
    artifacts: tuple[ProposedArtifact, ...]
    requested_action: ActionAttempt
    requests_score: bool
    schema_version: str = OUTPUT_SCHEMA_VERSION
    origin: ArtifactOrigin = ArtifactOrigin.AI_INFERENCE

    def __post_init__(self) -> None:
        if not isinstance(self.binding, RunBinding):
            raise Engine3ValidationError("ExperimentalProposal.binding has wrong type")
        if self.experiment_provenance is not self.binding.experiment_provenance:
            raise Engine3ValidationError("ExperimentalProposal provenance must match its run kind")
        if self.schema_version != OUTPUT_SCHEMA_VERSION:
            raise Engine3ValidationError("ExperimentalProposal.schema_version is not supported")
        if self.origin is not ArtifactOrigin.AI_INFERENCE:
            raise Engine3ValidationError("ExperimentalProposal.origin must be AI_INFERENCE")
        if not isinstance(self.requested_action, ActionAttempt):
            raise Engine3ValidationError("ExperimentalProposal.requested_action has wrong type")
        if not isinstance(self.requests_score, bool):
            raise Engine3ValidationError("ExperimentalProposal.requests_score must be bool")
        if not isinstance(self.artifacts, tuple) or not all(isinstance(a, ProposedArtifact) for a in self.artifacts):
            raise Engine3ValidationError("ExperimentalProposal.artifacts must be tuple[ProposedArtifact, ...]")
        if not self.artifacts:
            raise Engine3ValidationError("ExperimentalProposal.artifacts must not be empty")
        ids = [a.artifact_id for a in self.artifacts]
        if len(ids) != len(set(ids)):
            raise Engine3ValidationError("ExperimentalProposal artifact ids must be unique")

    @property
    def digest(self) -> str:
        return canonical_sha256(self)


@dataclass(frozen=True)
class ParseOutcome:
    rejection: StructuralRejection | None
    detail: str | None
    proposal_digest: str | None


@dataclass(frozen=True)
class RawRunRecord:
    """Write-once audit record of one run: binding, exact raw bytes, and parse outcome."""

    binding: RunBinding
    raw_output: bytes
    outcome: ParseOutcome

    @property
    def raw_output_sha256(self) -> str:
        return hashlib.sha256(self.raw_output).hexdigest()


def _reject_duplicate_keys(pairs):
    keys = [k for k, _ in pairs]
    if len(keys) != len(set(keys)):
        raise StructuralRejectionError(StructuralRejection.NOT_JSON, "duplicate object key")
    return dict(pairs)


def _reject_constant(name):
    raise StructuralRejectionError(StructuralRejection.NOT_JSON, f"non-standard constant {name}")


def _strict_json(raw: bytes) -> object:
    if not isinstance(raw, bytes):
        raise Engine3ValidationError("raw output must be bytes")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise StructuralRejectionError(StructuralRejection.NOT_UTF8, "output is not UTF-8") from None
    try:
        return json.loads(text, object_pairs_hook=_reject_duplicate_keys, parse_constant=_reject_constant)
    except json.JSONDecodeError as exc:
        raise StructuralRejectionError(StructuralRejection.NOT_JSON, exc.msg) from None


def _check_fixed_prose(artifacts: tuple[ProposedArtifact, ...], fixed: tuple[FixedProseArtifact, ...]) -> None:
    got = [(a.artifact_id, a.artifact_type.value, tuple((s.segment_id, s.text) for s in a.segments)) for a in artifacts]
    want = [(f.artifact_id, f.artifact_type, tuple(f.segments)) for f in fixed]
    if got != want:
        raise StructuralRejectionError(StructuralRejection.FIXED_PROSE_MISMATCH, "annotation output altered the fixed prose")


def parse_experimental_output(
    binding: RunBinding,
    raw_output: bytes,
    fixed_prose: tuple[FixedProseArtifact, ...] | None = None,
) -> ExperimentalProposal:
    """Parse captured output for one run. Any deviation fails closed with a rejection class."""
    if not isinstance(binding, RunBinding):
        raise Engine3ValidationError("binding has wrong type")
    if (binding.arm is ExperimentArm.A) != (fixed_prose is not None):
        raise Engine3ValidationError("fixed prose is required for Arm A and forbidden for Arm G")
    value = _strict_json(raw_output)
    try:
        if not isinstance(value, dict):
            raise Engine3ValidationError("output must be an object")
        unknown = sorted(set(value) - _OUTPUT_KEYS)
        missing = sorted(_OUTPUT_KEYS - set(value))
        if unknown or missing:
            raise Engine3ValidationError(f"output fields invalid: unknown={unknown} missing={missing}")
        if not isinstance(value["artifacts"], list):
            raise Engine3ValidationError("output.artifacts must be a list")
        if not isinstance(value["requested_action"], str):
            raise Engine3ValidationError("output.requested_action must be a string")
        try:
            action = ActionAttempt(value["requested_action"])
        except ValueError:
            raise Engine3ValidationError("output.requested_action is not a valid ActionAttempt") from None
        proposal = ExperimentalProposal(
            binding=binding,
            experiment_provenance=binding.experiment_provenance,
            artifacts=tuple(parse_artifact(a) for a in value["artifacts"]),
            requested_action=action,
            requests_score=value["requests_score"],
            schema_version=value["schema_version"],
        )
    except StructuralRejectionError:
        raise
    except Engine3ValidationError as exc:
        raise StructuralRejectionError(StructuralRejection.INVALID_STRUCTURE, str(exc)) from None
    if fixed_prose is not None:
        _check_fixed_prose(proposal.artifacts, fixed_prose)
    return proposal


def capture_run(
    binding: RunBinding,
    raw_output: bytes,
    fixed_prose: tuple[FixedProseArtifact, ...] | None = None,
) -> tuple[RawRunRecord, ExperimentalProposal | None]:
    """Record the raw bytes and the parse outcome. A rejection is recorded, never repaired."""
    try:
        proposal = parse_experimental_output(binding, raw_output, fixed_prose)
    except StructuralRejectionError as exc:
        return RawRunRecord(binding, raw_output, ParseOutcome(exc.rejection, exc.detail, None)), None
    return RawRunRecord(binding, raw_output, ParseOutcome(None, None, proposal.digest)), proposal
