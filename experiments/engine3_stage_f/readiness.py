"""Pre-run readiness validation for O-3, O-5, and O-6 (design section 15).

Validation only. Nothing here supplies, defaults, or invents a value, and the
repository contains no readiness record. Meeting every precondition does not
authorize or start a pilot: a live run still requires a separate owner approval
naming the configuration (design section 13), and this harness has no executor.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .envelope import OUTPUT_SCHEMA_VERSION

ARM_A_SUPPLEMENT_COUNT = 16
_DIGEST = re.compile(r"[0-9a-f]{64}")


def _filled(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _alias_key(alias: str) -> str:
    """HARNESS_AMENDMENT_001: exact match after strip and casefold. No substring or fuzzy matching."""
    return alias.strip().casefold()


@dataclass(frozen=True)
class AnnotatorStaffing:
    """O-3: two independent primary annotators plus a separate adjudicator."""

    primary_a_alias: str
    primary_b_alias: str
    adjudicator_alias: str
    frozen: bool


@dataclass(frozen=True)
class FrozenRunConfiguration:
    """O-5: one exact configuration. Any change is a new configuration and needs a new pilot."""

    experiment_configuration_id: str
    model_identifier: str
    model_version: str
    prompt_sha256: str
    sampling_configuration: tuple[tuple[str, object], ...]
    output_schema_version: str
    frozen: bool


@dataclass(frozen=True)
class SupplementArtifact:
    """O-6: one Arm A fixed-prose supplement artifact (owner decision D-9)."""

    artifact_id: str
    artifact_sha256: str
    author_aliases: tuple[str, ...]
    human_approved: bool
    frozen: bool


@dataclass(frozen=True)
class PilotReadiness:
    unresolved: tuple[tuple[str, str], ...]

    @property
    def preconditions_met(self) -> bool:
        return not self.unresolved

    @property
    def owner_approval_still_required(self) -> bool:
        return True


def _o3(staffing: AnnotatorStaffing | None) -> list[str]:
    if staffing is None:
        return ["annotator staffing not supplied"]
    if not isinstance(staffing, AnnotatorStaffing):
        return ["annotator staffing has wrong type"]
    problems = []
    aliases = (staffing.primary_a_alias, staffing.primary_b_alias, staffing.adjudicator_alias)
    if not all(_filled(a) for a in aliases):
        problems.append("every annotator alias must be non-empty")
    elif len({_alias_key(a) for a in aliases}) != 3:  # HARNESS_AMENDMENT_001: distinct after strip and casefold
        problems.append("two distinct primary annotators and a separate adjudicator are required")
    if staffing.frozen is not True:
        problems.append("annotator staffing is not frozen")
    return problems


def _o5(configuration: FrozenRunConfiguration | None) -> list[str]:
    if configuration is None:
        return ["run configuration not supplied"]
    if not isinstance(configuration, FrozenRunConfiguration):
        return ["run configuration has wrong type"]
    problems = []
    for name in ("experiment_configuration_id", "model_identifier", "model_version"):
        if not _filled(getattr(configuration, name)):
            problems.append(f"{name} must be non-empty")
    if not isinstance(configuration.prompt_sha256, str) or not _DIGEST.fullmatch(configuration.prompt_sha256):
        problems.append("prompt_sha256 must be lowercase SHA-256 hex")
    sampling = configuration.sampling_configuration
    if (not isinstance(sampling, tuple) or not sampling
            or not all(isinstance(p, tuple) and len(p) == 2 and _filled(p[0]) for p in sampling)
            or len({p[0] for p in sampling}) != len(sampling)):
        problems.append("sampling configuration must be a non-empty tuple of uniquely named parameters")
    if configuration.output_schema_version != OUTPUT_SCHEMA_VERSION:
        problems.append("output schema version does not match the harness envelope")
    if configuration.frozen is not True:
        problems.append("run configuration is not frozen")
    return problems


def _o6(supplement: tuple[SupplementArtifact, ...] | None, staffing: AnnotatorStaffing | None) -> list[str]:
    if supplement is None:
        return ["Arm A supplement not supplied"]
    if not isinstance(supplement, tuple) or not all(isinstance(a, SupplementArtifact) for a in supplement):
        return ["Arm A supplement has wrong type"]
    problems = []
    if len(supplement) != ARM_A_SUPPLEMENT_COUNT:
        problems.append(f"exactly {ARM_A_SUPPLEMENT_COUNT} supplement artifacts are required")
    if len({a.artifact_id for a in supplement}) != len(supplement):
        problems.append("supplement artifact ids must be unique")
    if len({a.artifact_sha256 for a in supplement}) != len(supplement):
        problems.append("supplement artifact hashes must be unique")
    # HARNESS_AMENDMENT_001: either primary being an author fails. The adjudicator is not checked here.
    primaries = None
    if isinstance(staffing, AnnotatorStaffing) and _filled(staffing.primary_a_alias) and _filled(staffing.primary_b_alias):
        primaries = {_alias_key(staffing.primary_a_alias), _alias_key(staffing.primary_b_alias)}
    for a in supplement:
        if not _filled(a.artifact_id):
            problems.append("a supplement artifact id is empty")
        if not isinstance(a.artifact_sha256, str) or not _DIGEST.fullmatch(a.artifact_sha256):
            problems.append(f"{a.artifact_id}: artifact_sha256 must be lowercase SHA-256 hex")
        if a.human_approved is not True:
            problems.append(f"{a.artifact_id}: not human-approved")
        if a.frozen is not True:
            problems.append(f"{a.artifact_id}: not frozen")
        if not isinstance(a.author_aliases, tuple) or not a.author_aliases or not all(_filled(x) for x in a.author_aliases):
            problems.append(f"{a.artifact_id}: authors must be recorded")
        elif primaries is None:
            problems.append(f"{a.artifact_id}: author independence cannot be checked without O-3")
        elif primaries & {_alias_key(x) for x in a.author_aliases}:
            problems.append(f"{a.artifact_id}: a primary annotator is one of its authors")
    return problems


def check_pilot_readiness(
    staffing: AnnotatorStaffing | None = None,
    configuration: FrozenRunConfiguration | None = None,
    supplement: tuple[SupplementArtifact, ...] | None = None,
) -> PilotReadiness:
    unresolved = [("O-3", p) for p in _o3(staffing)]
    unresolved += [("O-5", p) for p in _o5(configuration)]
    unresolved += [("O-6", p) for p in _o6(supplement, staffing)]
    return PilotReadiness(tuple(unresolved))
