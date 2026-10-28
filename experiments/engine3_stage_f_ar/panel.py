"""Stage F-AR reviewer panel, family correlation, and autonomous-track readiness.

Validation only. Nothing here supplies, defaults, or invents a reviewer model, and
the repository contains no reviewer configuration or readiness record. Meeting
every precondition does not authorize execution: a separate explicit owner
authorization is always still required, and this package has no executor.

Autonomous readiness never reads O-3, and nothing here reads or writes any
human-track readiness state. O-3 is a human-reference blocker, and nothing in
this track resolves it. AR-PRE-4 stays unresolved until the owner freezes the
qualification governance (FROZEN_QUALIFICATION_RULES_SHA256); no thresholds are
chosen here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from baec_app.engine3.canonical import canonical_sha256
from baec_app.engine3.errors import Engine3ValidationError

REVIEWER_OUTPUT_SCHEMA_VERSION = "engine3.stage_f_ar.reviewer_output.v1"

# The frozen Stage F generator (O-5, ENGINE3_STAGE_F_O5_MODEL_CONFIGURATION_FROZEN.json). Read-only here.
GENERATOR_MODEL_FAMILY = "Claude Sonnet"
GENERATOR_MODEL_DEVELOPER = "Anthropic"
GENERATOR_CONFIGURATION_SHA256 = "5ec4f582d2a05e8fe2768e9030e8eadf1315c1cced89d5acc0de1916e28e7f21"
# Reviewer qualification binds to the frozen calibration corpus (diagnostic only; not human ground truth).
CALIBRATION_FREEZE_MANIFEST_SHA256 = "a88600bb392f9d853b07c8948ee41ce8af597f5a3025697c56dc5e6e0fefef97"
# SHA-256 of the owner-frozen qualification governance record (metrics, acceptance/refusal criteria, structural
# failure, semantic disagreement, disqualification, rerun rules). None: not frozen, so AR-PRE-4 stays unresolved.
FROZEN_QUALIFICATION_RULES_SHA256: str | None = None

_DIGEST = re.compile(r"[0-9a-f]{64}")


class ReviewerRole(Enum):
    """Stage F-AR panel roles. Experiment metadata, not Engine 3 states and not human annotator roles."""

    REVIEWER_A = "REVIEWER_A"
    REVIEWER_B = "REVIEWER_B"
    AUTONOMOUS_ADJUDICATOR = "AUTONOMOUS_ADJUDICATOR"


PANEL_ROLES = (ReviewerRole.REVIEWER_A, ReviewerRole.REVIEWER_B, ReviewerRole.AUTONOMOUS_ADJUDICATOR)


def _filled(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _key(value: str) -> str:
    """Family and developer names compare after strip and casefold. No fuzzy matching."""
    return value.strip().casefold()


@dataclass(frozen=True)
class ReviewerConfiguration:
    """One exact autonomous reviewer configuration version, identified by configuration_sha256.

    Any material change (model, version, prompt bundle carrying the annotation or adjudication instructions,
    output schema, sampling) changes that identity: it is a new configuration version, unbinds every record
    tied to the old panel, and needs a new qualification run.
    """

    role: ReviewerRole
    model_family: str
    model_developer: str
    model_id: str
    model_version: str
    sampling_configuration: tuple[tuple[str, object], ...]
    prompt_bundle_sha256: str
    output_schema_version: str
    frozen: bool

    def __post_init__(self) -> None:
        if not isinstance(self.role, ReviewerRole):
            raise Engine3ValidationError("ReviewerConfiguration.role has wrong type")
        for name in ("model_family", "model_developer", "model_id", "model_version"):
            if not isinstance(getattr(self, name), str):
                raise Engine3ValidationError(f"ReviewerConfiguration.{name} must be a string")
        if not isinstance(self.frozen, bool):
            raise Engine3ValidationError("ReviewerConfiguration.frozen must be bool")

    @property
    def configuration_sha256(self) -> str:
        return canonical_sha256(self)


def _configuration_problems(panel: tuple[ReviewerConfiguration, ...]) -> list[str]:
    problems = []
    for c in panel:
        for name in ("model_family", "model_developer", "model_id", "model_version"):
            if not _filled(getattr(c, name)):
                problems.append(f"{c.role.value}: {name} must be non-empty")
        sampling = c.sampling_configuration
        if (not isinstance(sampling, tuple) or not sampling
                or not all(isinstance(p, tuple) and len(p) == 2 and _filled(p[0])
                           and (p[1] is None or isinstance(p[1], (str, int, bool))) for p in sampling)
                or len({p[0] for p in sampling}) != len(sampling)):
            problems.append(f"{c.role.value}: sampling configuration must be a non-empty tuple of uniquely named "
                            "parameters with string, int, bool, or null values")
        if c.frozen is not True:
            problems.append(f"{c.role.value}: configuration is not frozen")
    a, b = panel[0], panel[1]
    if _filled(a.model_family) and _filled(b.model_family) and _key(a.model_family) == _key(b.model_family):
        problems.append("Reviewer A and Reviewer B must be different model families")
    return problems


def _prompt_problems(panel: tuple[ReviewerConfiguration, ...]) -> list[str]:
    problems = []
    for c in panel:
        if not isinstance(c.prompt_bundle_sha256, str) or not _DIGEST.fullmatch(c.prompt_bundle_sha256):
            problems.append(f"{c.role.value}: prompt_bundle_sha256 must be lowercase SHA-256 hex")
        if c.output_schema_version != REVIEWER_OUTPUT_SCHEMA_VERSION:
            problems.append(f"{c.role.value}: output schema version does not match the Stage F-AR schema")
    return problems


def panel_shape_problem(panel: object) -> str | None:
    if not isinstance(panel, tuple) or not all(isinstance(c, ReviewerConfiguration) for c in panel):
        return "reviewer panel has wrong type"
    if tuple(c.role for c in panel) != PANEL_ROLES:
        return "reviewer panel must be exactly Reviewer A, Reviewer B, and the Autonomous Adjudicator, in that order"
    return None


def panel_sha256(panel: tuple[ReviewerConfiguration, ...]) -> str:
    problem = panel_shape_problem(panel)
    if problem:
        raise Engine3ValidationError(problem)
    return canonical_sha256([c.configuration_sha256 for c in panel])


def family_correlations(panel: tuple[ReviewerConfiguration, ...]) -> tuple[tuple[str, str, str], ...]:
    """Every pair of panel members (and the frozen generator) sharing a model family or developer.

    A correlation is a disclosed limitation (rule AR-F), not a blocker, except that Reviewer A and
    Reviewer B must never share a model family.
    """
    problem = panel_shape_problem(panel)
    if problem:
        raise Engine3ValidationError(problem)
    parties = [("GENERATOR", GENERATOR_MODEL_FAMILY, GENERATOR_MODEL_DEVELOPER)]
    parties += [(c.role.value, c.model_family, c.model_developer) for c in panel]
    found = []
    for i, (left, left_family, left_developer) in enumerate(parties):
        for right, right_family, right_developer in parties[i + 1:]:
            if _key(left_family) == _key(right_family):
                found.append((left, right, "SAME_MODEL_FAMILY"))
            elif _key(left_developer) == _key(right_developer):
                found.append((left, right, "SAME_MODEL_DEVELOPER"))
    return tuple(found)


# --- readiness ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class CorrelationAssessment:
    """AR-PRE-3: the owner's recorded assessment of reviewer-family correlation for one exact panel."""

    panel_sha256: str
    disclosed_correlations: tuple[tuple[str, str, str], ...]
    owner_reviewed: bool


@dataclass(frozen=True)
class QualificationRecord:
    """AR-PRE-4: one calibration qualification attempt on one exact panel, under frozen qualification rules.

    A reviewer qualification diagnostic, not an evaluation result and not a tuning set. Every prior attempt is
    preserved and named by hash; a rerun is a new attempt, never an overwrite.
    """

    panel_sha256: str
    calibration_freeze_manifest_sha256: str
    qualification_rules_sha256: str
    attempt_number: int
    prior_attempt_sha256s: tuple[str, ...]
    completed: bool

    @property
    def attempt_sha256(self) -> str:
        return canonical_sha256(self)


@dataclass(frozen=True)
class AutonomousRunOrder:
    """AR-PRE-5: the frozen order in which review slots are executed for one exact panel."""

    panel_sha256: str
    run_order_sha256: str
    frozen: bool


@dataclass(frozen=True)
class AutonomousReferenceReadiness:
    unresolved: tuple[tuple[str, str], ...]

    @property
    def preconditions_met(self) -> bool:
        return not self.unresolved

    @property
    def owner_execution_authorization_still_required(self) -> bool:
        """AR-PRE-6. Never satisfied by code or by any model-supplied value."""
        return True


def _bound(record: object, cls: type, name: str, panel_hash: str | None) -> list[str]:
    if record is None:
        return [f"{name} not supplied"]
    if not isinstance(record, cls):
        return [f"{name} has wrong type"]
    if panel_hash is None:
        return [f"{name} cannot be checked without a valid reviewer panel"]
    if record.panel_sha256 != panel_hash:
        return [f"{name} is not bound to this exact reviewer panel"]
    return []


def check_autonomous_reference_readiness(
    panel: tuple[ReviewerConfiguration, ...] | None = None,
    correlation: CorrelationAssessment | None = None,
    qualification: QualificationRecord | None = None,
    run_order: AutonomousRunOrder | None = None,
) -> AutonomousReferenceReadiness:
    """Stage F-AR preconditions AR-PRE-1 to AR-PRE-5. AR-PRE-6 (owner authorization) always remains."""
    unresolved: list[tuple[str, str]] = []
    panel_hash = None
    if panel is None:
        unresolved += [("AR-PRE-1", "reviewer configurations not supplied"),
                       ("AR-PRE-2", "reviewer prompt and schema not supplied")]
    elif panel_shape_problem(panel):
        unresolved += [("AR-PRE-1", panel_shape_problem(panel)), ("AR-PRE-2", panel_shape_problem(panel))]
    else:
        unresolved += [("AR-PRE-1", p) for p in _configuration_problems(panel)]
        unresolved += [("AR-PRE-2", p) for p in _prompt_problems(panel)]
        panel_hash = panel_sha256(panel)

    problems = _bound(correlation, CorrelationAssessment, "correlation assessment", panel_hash)
    if not problems:
        if correlation.disclosed_correlations != family_correlations(panel):
            problems.append("correlation assessment must disclose exactly the panel's family correlations")
        if correlation.owner_reviewed is not True:
            problems.append("correlation assessment is not owner-reviewed")
    unresolved += [("AR-PRE-3", p) for p in problems]

    problems = []
    if FROZEN_QUALIFICATION_RULES_SHA256 is None:
        problems.append("qualification governance is not frozen (metrics, acceptance/refusal criteria, structural "
                        "failure, semantic disagreement, disqualification, rerun rules)")
    bound = _bound(qualification, QualificationRecord, "reviewer qualification", panel_hash)
    problems += bound
    if not bound:
        if qualification.calibration_freeze_manifest_sha256 != CALIBRATION_FREEZE_MANIFEST_SHA256:
            problems.append("reviewer qualification is not bound to the frozen calibration corpus")
        if (FROZEN_QUALIFICATION_RULES_SHA256 is not None
                and qualification.qualification_rules_sha256 != FROZEN_QUALIFICATION_RULES_SHA256):
            problems.append("reviewer qualification was not run under the frozen qualification rules")
        attempt, prior = qualification.attempt_number, qualification.prior_attempt_sha256s
        if (not isinstance(attempt, int) or isinstance(attempt, bool) or attempt < 1 or not isinstance(prior, tuple)
                or len(prior) != attempt - 1 or not all(isinstance(h, str) and _DIGEST.fullmatch(h) for h in prior)):
            problems.append("every prior qualification attempt must be preserved and named by hash")
        if qualification.completed is not True:
            problems.append("reviewer qualification is not completed")
    unresolved += [("AR-PRE-4", p) for p in problems]

    problems = _bound(run_order, AutonomousRunOrder, "autonomous run order", panel_hash)
    if not problems:
        if not isinstance(run_order.run_order_sha256, str) or not _DIGEST.fullmatch(run_order.run_order_sha256):
            problems.append("run_order_sha256 must be lowercase SHA-256 hex")
        if run_order.frozen is not True:
            problems.append("autonomous run order is not frozen")
    unresolved += [("AR-PRE-5", p) for p in problems]
    return AutonomousReferenceReadiness(tuple(unresolved))
