"""Deterministic verification of one persisted Phase 6 extraction result, for downstream consumers (Phase 7D).

A pure façade over the locked Phase 6 contract and validator. It re-applies them; it
implements no rule of its own:

* strict parsing of the stored text as baec-extraction-output/v1 (contracts.BaecExtractionOutput);
* the exact baec-canonical-json/v1 form of the parsed output (canonical.canonical_json);
* the locked semantic validation against the stored source text (validation.validate_extraction).

It is the only part of baec_app.ai that application code may import (one named
exception to Phase 6 rule A9, for baec_app.application.ai_proposal_mapping; see
docs/PHASE7_HUMAN_AUTHORIZED_AI_PROPOSAL_BRIDGE_DESIGN.md, Phase 7D implementation
clarification). It reads no data layer, provider, service, composition, network,
environment, clock, or randomness, and it never repairs a result: any failure
raises ExtractionVerificationError with one closed code and no text.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import ValidationError

from baec_app.ai import validation
from baec_app.ai.canonical import canonical_json
from baec_app.ai.contracts import BaecExtractionOutput

VERIFICATION_CODES = ("output_not_parseable", "output_not_canonical", "output_semantically_invalid")


class ExtractionVerificationError(ValueError):
    """The persisted result is not exactly a valid locked extraction output. Carries one closed code."""

    def __init__(self, code: str) -> None:
        if code not in VERIFICATION_CODES:
            raise ValueError("unknown verification code")
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class VerifiedExtraction:
    """A persisted result that parsed strictly, was canonical, and passed the named validator unchanged."""

    output: BaecExtractionOutput
    validation_version: str


def verifier_validation_version() -> str:
    """The identity of the locked validator this façade re-runs."""
    return validation.VALIDATION_VERSION


def verify_persisted_extraction(canonical_result: str, *, source_interaction_id: str,
                                source_text: str) -> VerifiedExtraction:
    """Strictly re-parse and re-validate one stored extraction result against its stored source text."""
    if type(canonical_result) is not str or type(source_interaction_id) is not str or type(source_text) is not str:
        raise TypeError("verify_persisted_extraction requires text")
    try:
        output = BaecExtractionOutput.model_validate_json(canonical_result, strict=True)
    except ValidationError:
        raise ExtractionVerificationError("output_not_parseable") from None
    if canonical_json(output.model_dump(mode="json")) != canonical_result:
        raise ExtractionVerificationError("output_not_canonical")
    if validation.validate_extraction(output, source_interaction_id=source_interaction_id,
                                      source_text=source_text) != ():
        raise ExtractionVerificationError("output_semantically_invalid")
    return VerifiedExtraction(output, verifier_validation_version())
