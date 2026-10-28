"""Deterministic mapping of one persisted AI artifact to one immutable AI_DRAFT proposal (Phase 7D).

docs/PHASE7_HUMAN_AUTHORIZED_AI_PROPOSAL_BRIDGE_DESIGN.md §6. Three separate parts:

* verify_artifact: eligibility E1-E6. It loads the artifact through the Phase 6
  provenance store, which re-verifies the digest, the run binding, the
  successful result with its output, and the verbatim excerpts; re-applies the
  locked output contract and validator through baec_app.ai.verification; and
  re-checks the result, the compatibility identities, and the source binding.
  Any failure raises ArtifactNotEligible with a closed code.
* map_artifact_to_proposal_content: baec-ai-proposal-mapping/v1. A pure
  function of one VerifiedArtifact: no I/O, clock, randomness, environment,
  provider, or database. The same input always yields byte-identical
  canonical content.
* AiProposalMappingService: persistence orchestration. E7 (an existing
  proposal for the artifact is returned, verified, and nothing is created)
  and E8 (an artifact whose lineage is already confirmed is refused), then
  one plain insert through ProposalBridgeStore.

The mapping states only that this immutable artifact is represented by this
immutable AI_DRAFT proposal. It selects no authoritative evidence, sets no
evidence provenance, finding, origin, elicitation mode, buyer statement, buyer
role, or stringency, and never approves, authorizes, confirms, or changes any
account state. Every AI-authored value is labelled AI_INFERENCE; excerpts are
verbatim source text offered as suggestions only.

The compatibility identities below are an IMPLEMENTATION compatibility
allowlist for mapping v1, not a BAEC research proposition. No model is
required or preferred: eligibility follows persisted provenance and contract.

The proposal snapshot carries the mapped AI material and the stable source
reference (artifact_id and artifact_digest). Run, model, prompt, validator, and
request provenance are reconstructed through the immutable artifact_id lineage.

Source of artifacts (design §6.1): eligibility is evaluated only for an artifact
reachable through this product database's durable provenance relationships, on
the connection the service was built over. Temporary live-evaluation databases
are not the product database and are deleted after evaluation, so their
artifacts are never reachable. Limitation, stated plainly: this structure does
not cryptographically prove that rows were never copied by hand from another
SQLite database.

The AI package is reached only through baec_app.ai.verification, the one named
Phase 7D exception to Phase 6 rule A9.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass

from baec_app.ai.verification import (
    ExtractionVerificationError,
    VerifiedExtraction,
    verifier_validation_version,
    verify_persisted_extraction,
)
from baec_app.application.context import Clock
from baec_app.application.errors import ApplicationError
from baec_app.data.ai_provenance import (
    AiArtifactExcerptRecord,
    AiArtifactRecord,
    AiProvenanceStore,
    AiRemoteOutcome,
    AiRunRecord,
    AiRunResultRecord,
    AiRunStatus,
)
from baec_app.data.database import (
    BRIDGE_PROPOSAL_ORIGIN,
    PersistenceIntegrityError,
    RepositoryConflictError,
    RepositoryNotFoundError,
)
from baec_app.data.proposal_bridge import AiProposalRecord, ProposalBridgeStore, sha256_text
from baec_app.data.records import SourceInteraction
from baec_app.data.repository import Repository

MAPPING_VERSION = "baec-ai-proposal-mapping/v1"
PROPOSAL_CONTENT_VERSION = "baec-ai-proposal-content/v1"
AI_INFERENCE = "AI_INFERENCE"

# E3: the IMPLEMENTATION compatibility allowlist of mapping v1.
COMPATIBLE_IDENTITY = {
    "task_type": "baec_extraction",
    "task_version": "baec-extraction-task/v1",
    "output_schema_version": "baec-extraction-output/v1",
    "canonicalization_version": "baec-canonical-json/v1",
    "validation_version": "baec-extraction-validation/v2",
}
ELIGIBLE_ANALYSIS_STATUS = "possible_baec_language"

# AI criterion tokens, in the fixed C1-C4 order, and the BaecCriterion names they align with for display only.
# An AI hypothesis status is never translated into a criterion finding.
CRITERION_ORDER = (
    ("present_non_evaluation", "PRESENT_NON_EVALUATION"),
    ("prospective_condition", "PROSPECTIVE_CONDITION"),
    ("buyer_articulation", "BUYER_ARTICULATION"),
    ("evaluation_linkage", "EVALUATION_LINKAGE"),
)

ELIGIBILITY_CODES = (
    "artifact_not_found",          # E1
    "artifact_integrity_failure",  # E1
    "run_not_successful",          # E2
    "incompatible_identity",       # E3
    "validator_unavailable",       # E3/E4: the allowlisted validator is not the one this code runs
    "artifact_output_invalid",     # E4
    "no_possible_baec_language",   # E5
    "source_binding_invalid",      # E6
    "lineage_already_confirmed",   # E8
    "stored_proposal_mismatch",    # E7: a stored proposal for the artifact differs from its mapping
)


class ArtifactNotEligible(ApplicationError):
    """The artifact cannot be represented as an AI_DRAFT proposal. Carries one closed code and no artifact text."""

    def __init__(self, code: str) -> None:
        if code not in ELIGIBILITY_CODES:
            raise ValueError("unknown eligibility code")
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class VerifiedArtifact:
    """An artifact that passed E1-E6, with the provenance it was verified against. Built only by verify_artifact."""

    artifact: AiArtifactRecord
    run: AiRunRecord
    result: AiRunResultRecord
    excerpts: tuple[AiArtifactExcerptRecord, ...]
    extraction: VerifiedExtraction
    interaction: SourceInteraction


@dataclass(frozen=True)
class MappedProposal:
    """The persisted AI_DRAFT proposal for an artifact; created is False when E7 returned an existing one."""

    proposal: AiProposalRecord
    created: bool


# --- eligibility (E1-E6) --------------------------------------------------------------------


def verify_artifact(artifact_id: str, *, provenance: AiProvenanceStore, repository: Repository) -> VerifiedArtifact:
    """Apply E1-E6 to one stored artifact, reusing the Phase 6 integrity path. Reads only."""
    # E1: the Phase 6 store re-verifies the artifact digest, its binding to its run, the successful result and its
    # output, and every excerpt's verbatim fidelity. Corruption is never repaired here.
    try:
        artifact = provenance.get_artifact(artifact_id)
        run = provenance.get_run(artifact.ai_run_id)
        result = provenance.get_result(artifact.ai_run_id)
        excerpts = provenance.list_artifact_excerpts(artifact_id)
    except RepositoryNotFoundError:
        raise ArtifactNotEligible("artifact_not_found") from None
    except PersistenceIntegrityError:
        raise ArtifactNotEligible("artifact_integrity_failure") from None

    # E2: a successful terminal result with nothing left over.
    if (result.status is not AiRunStatus.SUCCESS or result.remote_outcome is not AiRemoteOutcome.RESPONSE_RECEIVED
            or result.failure_codes != () or result.stop_reason != "end_turn"
            or result.response_model != run.requested_model):
        raise ArtifactNotEligible("run_not_successful")

    # E3: the compatibility allowlist, on the run and (where it records them) the artifact.
    for field, expected in COMPATIBLE_IDENTITY.items():
        if getattr(run, field) != expected:
            raise ArtifactNotEligible("incompatible_identity")
    for field in ("task_type", "task_version", "output_schema_version"):
        if getattr(artifact, field) != COMPATIBLE_IDENTITY[field]:
            raise ArtifactNotEligible("incompatible_identity")
    if verifier_validation_version() != COMPATIBLE_IDENTITY["validation_version"]:
        raise ArtifactNotEligible("validator_unavailable")

    # E6: the source exists and belongs to the artifact's account.
    try:
        repository.get_account(artifact.account_id)
        interaction = repository.get_interaction(artifact.interaction_id)
    except (RepositoryNotFoundError, PersistenceIntegrityError):
        raise ArtifactNotEligible("source_binding_invalid") from None
    if interaction.account_id != artifact.account_id or run.account_id != artifact.account_id:
        raise ArtifactNotEligible("source_binding_invalid")

    # E4: the stored result is exactly a valid output, still valid against the stored source, with its excerpts.
    # The locked Phase 6 contract and validator are re-applied through the pure verification façade.
    try:
        extraction = verify_persisted_extraction(artifact.canonical_result,
                                                 source_interaction_id=interaction.interaction_id,
                                                 source_text=interaction.text)
    except ExtractionVerificationError:
        raise ArtifactNotEligible("artifact_output_invalid") from None
    output = extraction.output
    stored = tuple((e.excerpt_id, e.interaction_id, e.text, e.attributed_speaker.value) for e in excerpts)
    parsed = tuple((e.excerpt_id, e.source_interaction_id, e.text, e.attributed_speaker) for e in output.source_excerpts)
    if stored != parsed:
        raise ArtifactNotEligible("artifact_output_invalid")

    # E5: only a possible-BAEC reading can become a draft, because the only Phase 7 write is a confirmation.
    if output.analysis_status != ELIGIBLE_ANALYSIS_STATUS:
        raise ArtifactNotEligible("no_possible_baec_language")

    return VerifiedArtifact(artifact, run, result, excerpts, extraction, interaction)


# --- mapping (pure) ------------------------------------------------------------------------


def canonical_text(value: object) -> str:
    """baec-canonical-json/v1 text: sorted keys, compact separators, UTF-8, no NaN; content holds no floats.

    The same rules as the Phase 6 canonical form, which the proposal store re-checks on every write and load.
    """
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _inference(value: object) -> dict:
    return {"value": value, "provenance": AI_INFERENCE}


def map_artifact_to_proposal_content(verified: VerifiedArtifact) -> str:
    """baec-ai-proposal-mapping/v1: the canonical baec-ai-proposal-content/v1 text of one verified artifact. Pure."""
    if type(verified) is not VerifiedArtifact:
        raise TypeError("map_artifact_to_proposal_content requires a VerifiedArtifact")
    artifact, output = verified.artifact, verified.extraction.output
    hypotheses = {h.criterion: h for h in output.criterion_hypotheses}
    content = {
        "content_version": PROPOSAL_CONTENT_VERSION,
        "mapping_version": MAPPING_VERSION,
        "origin": BRIDGE_PROPOSAL_ORIGIN,
        "account_id": artifact.account_id,
        "interaction_id": artifact.interaction_id,
        # The stable source reference. Run, model, prompt, validator, and request provenance are reconstructed
        # through the immutable artifact_id lineage, never copied here.
        "artifact": {"artifact_id": artifact.artifact_id, "artifact_digest": artifact.artifact_digest},
        "ai_analysis_status": output.analysis_status,
        # Verbatim source text, offered as suggestions only: no provenance until a human asserts one.
        "ai_suggested_excerpts": [
            {"excerpt_id": e.excerpt_id, "text": e.text, "ai_attributed_speaker": _inference(e.attributed_speaker)}
            for e in output.source_excerpts
        ],
        "ai_normalized_condition": _inference(output.normalized_condition),
        "ai_normalized_evaluation_link": _inference(output.normalized_evaluation_link),
        "ai_criterion_hypotheses": [
            {"criterion": name, "ai_status": hypotheses[token].status, "excerpt_refs": list(hypotheses[token].excerpt_refs),
             "explanation": hypotheses[token].explanation, "provenance": AI_INFERENCE}
            for token, name in CRITERION_ORDER
        ],
        "ai_uncertainties": {"values": list(output.uncertainties), "provenance": AI_INFERENCE},
    }
    return canonical_text(content)


def proposal_id_for(artifact_id: str) -> str:
    """The deterministic proposal identifier of an artifact under mapping v1 (one proposal per artifact)."""
    seed = canonical_text({"artifact_id": artifact_id, "mapping_version": MAPPING_VERSION})
    return "aiprop_" + hashlib.sha256(seed.encode("utf-8")).hexdigest()


# --- persistence orchestration (E7, E8) --------------------------------------------------------


class AiProposalMappingService:
    """Represents one persisted artifact as one persisted AI_DRAFT proposal. Writes ai_proposals and nothing else."""

    def __init__(self, connection: sqlite3.Connection, *, clock: Clock) -> None:
        self._provenance = AiProvenanceStore(connection)  # each store verifies the schema and foreign keys
        self._repository = Repository(connection)
        self._bridge = ProposalBridgeStore(connection)
        self._clock = clock

    def map_artifact(self, artifact_id: str, *, created_by: str) -> MappedProposal:
        verified = verify_artifact(artifact_id, provenance=self._provenance, repository=self._repository)
        if self._bridge.confirmed_baec_for_artifact(artifact_id) is not None:  # E8
            raise ArtifactNotEligible("lineage_already_confirmed")
        content = map_artifact_to_proposal_content(verified)
        proposal_id = proposal_id_for(artifact_id)
        existing = self._existing(proposal_id, content)  # E7
        if existing is not None:
            return MappedProposal(existing, created=False)
        record = AiProposalRecord(
            proposal_id=proposal_id, artifact_id=artifact_id, artifact_digest=verified.artifact.artifact_digest,
            account_id=verified.artifact.account_id, interaction_id=verified.artifact.interaction_id,
            content=content, proposal_digest=sha256_text(content), created_by=created_by,
            created_at=self._clock.now(),
        )
        try:
            self._bridge.add_proposal(record)
        except RepositoryConflictError:
            existing = self._existing(proposal_id, content)  # a concurrent mapping of the same artifact
            if existing is None:
                raise ArtifactNotEligible("stored_proposal_mismatch") from None
            return MappedProposal(existing, created=False)
        return MappedProposal(self._bridge.get_proposal(proposal_id), created=True)

    def _existing(self, proposal_id: str, content: str) -> AiProposalRecord | None:
        try:
            stored = self._bridge.get_proposal(proposal_id)
        except RepositoryNotFoundError:
            return None
        if stored.content != content or stored.proposal_digest != sha256_text(content):
            raise ArtifactNotEligible("stored_proposal_mismatch")
        return stored
