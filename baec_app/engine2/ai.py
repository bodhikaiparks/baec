"""Engine 2 AI correspondence PROPOSAL boundary (Stage F). Advisory only.

Authority: the locked Stage A contract, Section 9 (AI Correspondence
Assessment, IMPLEMENTATION). Every claim an AI proposal makes is AI_INFERENCE.

    "The system will not predict who is going to buy."
    "Correspondence narrows attention. It does not establish buyer evaluation,
     purchase intent, or an opportunity."

An AI proposal does not establish that the buyer is evaluating or in market,
intends to purchase or switch, that the account is an opportunity, that the
BAEC was revalidated, that the seller should or may contact the buyer, or any
account-state transition.

What this module does:
* builds an immutable, exact-plan-version input snapshot (CorrespondenceAIInput)
  from validated Engine 2 objects, with a reproducible digest. It never contains
  human findings, human sufficiency, a correspondence outcome, or any oracle;
* defines a model protocol; nothing here calls a model, a network, or a provider;
* validates raw structured output strictly and fails closed, producing a distinct
  CorrespondenceAIProposal. Nothing is repaired, defaulted, or inferred.

What it never does: create a DimensionAssessment, a HumanCorrespondenceReview,
or a CorrespondenceOutcome; call the deterministic classifier; persist anything;
calculate an authoritative measurement; choose between conflicting sources.
Human Correspondence Review remains the only route to authoritative findings.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Protocol

from baec_app.domain.enums import ProvenanceCategory

from .classification import EvidenceLedger
from .domain import (
    CorrespondenceCandidate,
    CorrespondenceDimension,
    CrossCuttingCheckName,
    plan_key,
)
from .errors import Engine2Error, Engine2ValidationError

PROMPT_VERSION = "baec-e2-correspondence-proposal-prompt/v1"
INPUT_VERSION = "baec-e2-correspondence-ai-input/v1"
OUTPUT_SCHEMA_VERSION = "baec-e2-correspondence-proposal/v1"
CANONICAL_JSON_VERSION = "baec-e2-canonical-json/v1"

DOCTRINE_NO_PREDICTION = "The system will not predict who is going to buy."
DOCTRINE_NARROWS = ("Correspondence narrows attention. It does not establish buyer evaluation, "
                    "purchase intent, or an opportunity.")
PROHIBITED_INFERENCE_ACKNOWLEDGEMENT = (
    "This proposal is AI_INFERENCE for human review only. It does not establish that the buyer is evaluating or "
    "in market, intends to purchase or switch, that the account is an opportunity, that the BAEC was revalidated, "
    "that contact is permitted or advised, or any account-state change."
)
MAX_RATIONALE = 600
MAX_EXCERPT = 500
MIN_EXCERPT = 3


class ProposalRejected(Engine2Error):
    """Raw model output failed strict validation. No proposal exists; nothing is usable."""


class ProposedFinding(Enum):
    """AI proposal vocabulary. Deliberately distinct from the authoritative DimensionFinding."""

    PROPOSED_SUPPORTED = "PROPOSED_SUPPORTED"
    PROPOSED_CONTRADICTED = "PROPOSED_CONTRADICTED"
    PROPOSED_UNRESOLVED = "PROPOSED_UNRESOLVED"
    PROPOSED_NOT_APPLICABLE = "PROPOSED_NOT_APPLICABLE"


_DECISIVE = frozenset({ProposedFinding.PROPOSED_SUPPORTED, ProposedFinding.PROPOSED_CONTRADICTED})


class UnresolvedBasis(Enum):
    """Why a PROPOSED_UNRESOLVED proposal is unresolved. A closed reason, never a rating.

    MISSING_EVIDENCE is the only basis that may cite no observation or measurement.
    """

    MISSING_EVIDENCE = "MISSING_EVIDENCE"
    AMBIGUOUS_EVIDENCE = "AMBIGUOUS_EVIDENCE"
    CONFLICTING_EVIDENCE = "CONFLICTING_EVIDENCE"
    INSUFFICIENT_PROVENANCE = "INSUFFICIENT_PROVENANCE"
    OTHER_EVIDENCE_LIMITATION = "OTHER_EVIDENCE_LIMITATION"


# --- generated-text authority boundary ------------------------------------------------------
# Four text classes. (A) Authoritative source text (observation content, exact excerpts, the
# buyer condition, plan text) is never checked here; excerpts are only verified as exact
# quotations. (B) The fixed acknowledgement is the one place that states what a proposal does
# not establish, and must match exactly. (C) AI-generated interpretive text (rationale,
# cross-cutting concern, open question) may discuss only the evidence and its relation to a
# dimension, so any mention of a buyer-state or action topic below is refused, with no
# negation exception: "The buyer is not evaluating" is refused like "The buyer is evaluating".
# (D) Structured fields are fixed by exact key sets, so unknown fields are refused.
# These are deterministic topic patterns, not language understanding.
# Implementation limitation (approved at the Stage F lock): the patterns are deliberately blunt and can
# reject some otherwise harmless evidence-focused wording. Any relaxation requires separately approved
# work after real-model evaluation.
GENERATED_TEXT_TOPICS = (
    ("buyer evaluation", re.compile(r"\bevaluat\w*")),
    ("in market", re.compile(r"\bin[- ]market\b")),
    ("purchase", re.compile(r"\b(purchas\w*|buy|buys|buying|bought)\b")),
    ("intent", re.compile(r"\bintent\w*|\bintend\w*")),
    ("switching", re.compile(r"\bswitch\w*")),
    ("likelihood or propensity", re.compile(r"\b(likelihood|likely to|chance|odds|propensity|conversion|proba\w*)\b")),
    ("lead rating", re.compile(r"\blead\s+(sc\w+|rank\w*|rating|grade|quality|status)\b|\b(sales|qualified|hot|warm)\s+leads?\b")),
    ("opportunity status", re.compile(r"\bopportunit\w*")),
    ("contact or outreach", re.compile(r"\b(contact\w*|outreach|reach(ing)? out|re-?engag\w*|follow[- ]?up|call the buyer|"
                                       r"email the buyer)\b")),
    ("permission", re.compile(r"\b(permission|permitted)\b")),
    ("seller action", re.compile(r"\bseller action\b|\b(seller|sales|rep|team)\s+(should|must|needs? to|ought to)\b|"
                                 r"\brecommend\w*")),
    ("account state", re.compile(r"\baccount[- ]state\b|\b(active opportunity|conditionally dormant|no plausible path)\b")),
)


def generated_text_violation(text: str) -> str | None:
    """The first buyer-state or action topic in AI-generated interpretive text, or None. Never applied to source quotes."""
    lowered = text.lower()
    for label, pattern in GENERATED_TEXT_TOPICS:
        if pattern.search(lowered):
            return label
    return None


SYSTEM_PROMPT = f"""You propose, you do not decide. Prompt version: {PROMPT_VERSION}.

{DOCTRINE_NO_PREDICTION}
{DOCTRINE_NARROWS}

You receive one exact evidence snapshot for one Monitoring Plan version of one confirmed buyer-articulated
evaluation contingency (BAEC). For each of the 11 correspondence dimensions, propose whether the supplied
evidence supports, contradicts, or leaves unresolved that dimension of the buyer's own condition. A human
reviewer decides; your output is AI_INFERENCE and is never authoritative.

Rules:
1. Use only the supplied evidence. Preserve the buyer's exact condition, threshold, comparator, unit, and timing.
2. Do not predict purchase or switching intent. Do not infer that the buyer is evaluating or in market.
   Do not recommend contact or outreach. Do not create or mention opportunity or account state.
3. Every PROPOSED_SUPPORTED or PROPOSED_CONTRADICTED proposal must cite current, usable evidence. Quote
   observation excerpts exactly as they appear; never paraphrase inside an excerpt. Cite a derived measurement
   by its id together with exactly its input observation ids.
4. Do not calculate. Use only supplied derived measurements for any computed value. If a calculation would be
   needed and no derived measurement exists, say so and propose PROPOSED_UNRESOLVED.
5. Prefer PROPOSED_UNRESOLVED over unsupported precision: approximate or bounded figures, unclear timing,
   uncertain entity continuity, ambiguous pricing scope, missing provenance, or unknown applicability.
   Every PROPOSED_UNRESOLVED gives an unresolved_basis. Cite the evidence that is ambiguous, conflicting, or
   provenance-limited; only MISSING_EVIDENCE may cite no observation or measurement. Never invent a citation.
6. If current sources conflict and no authoritative resolution is supplied, propose PROPOSED_UNRESOLVED and
   cite both sides. Never prefer a source because it is newer.
7. Use PROPOSED_NOT_APPLICABLE only for a dimension the Monitoring Plan already marks not applicable, and cite
   that plan dimension as a plan_dimension citation.
8. Report possible staleness, contradiction, missing evidence, and provenance limits as cross-cutting
   observations, separately from the 11 dimensions.
9. Keep every rationale, cross-cutting concern, and open question concise and limited to the evidence and its
   relationship to the dimension: ambiguity, contradiction, missing evidence, provenance, timing, entity,
   threshold, comparator, unit, or applicability. The fixed acknowledgement already states what this proposal
   does not establish, so do not repeat or discuss buyer evaluation, purchase or switching intent, opportunity
   status, contact permission, outreach, or seller action anywhere else, not even to deny them.
   Acceptable: "The renewal notice states a 12% increase." "The effective date in the notice is six months after
   the recorded renewal date." "The two current notices state different pricing changes." "No accepted evidence
   establishes the renewal date."
   Exact quotations of source text inside evidence excerpts are not your words and are never restricted.
   Do not output hidden reasoning or chain-of-thought.
   Never express certainty or likelihood as a percentage, numeric rating, or likelihood statement. Figures that
   appear in the evidence or the buyer's condition, such as a quoted price change, may be restated exactly.
10. Copy this acknowledgement exactly: {PROHIBITED_INFERENCE_ACKNOWLEDGEMENT}
11. Return only JSON that matches the supplied output schema, with no extra fields.
"""

# --- canonical JSON and digest ----------------------------------------------------


def _check_canonical(value: object) -> None:
    if value is None or type(value) in (str, bool, int):
        return
    if type(value) is dict:
        for key, item in value.items():
            if type(key) is not str:
                raise Engine2ValidationError("canonical JSON object keys must be strings")
            _check_canonical(item)
        return
    if type(value) in (list, tuple):
        for item in value:
            _check_canonical(item)
        return
    raise Engine2ValidationError(f"{type(value).__name__} is not allowed in canonical JSON")


def canonical_json(value: object) -> str:
    """baec-e2-canonical-json/v1: sorted keys at every depth, compact separators, UTF-8 text kept as given,
    only str, int, bool, None, list, and dict. No floats, NaN, or Infinity; nothing is converted."""
    _check_canonical(value)
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def input_digest(canonical: str) -> str:
    """SHA-256 of the canonical JSON text encoded as UTF-8, as lowercase hex."""
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# --- AI input snapshot -------------------------------------------------------------


@dataclass(frozen=True)
class BaecConditionInput:
    """The confirmed BAEC's authoritative condition, supplied from Engine 1 as values. Never fetched here."""

    baec_id: str
    condition_reference: str
    buyer_exact_statement: str
    normalized_condition: str | None = None

    def __post_init__(self) -> None:
        for name in ("baec_id", "condition_reference", "buyer_exact_statement"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise Engine2ValidationError(f"BaecConditionInput.{name} must be a non-empty string")
        if self.normalized_condition is not None and (
                not isinstance(self.normalized_condition, str) or not self.normalized_condition.strip()):
            raise Engine2ValidationError("BaecConditionInput.normalized_condition must be a non-empty string or None")


@dataclass(frozen=True)
class CorrespondenceAIInput:
    """Immutable, reproducible model input for one exact (monitoring_plan_id, plan_version).

    canonical_json is the complete payload; input_digest = SHA-256(canonical_json UTF-8).
    It contains evidence and plan context only: no human findings, sufficiency, or outcome.
    """

    monitoring_plan_id: str
    plan_version: int
    baec_id: str
    candidate_id: str
    canonical_json: str
    input_digest: str
    input_version: str = INPUT_VERSION

    def __post_init__(self) -> None:
        if self.input_version != INPUT_VERSION:
            raise Engine2ValidationError("unsupported AI input version")
        if not isinstance(self.canonical_json, str) or input_digest(self.canonical_json) != self.input_digest:
            raise Engine2ValidationError("AI input digest does not match its canonical payload")
        payload = json.loads(self.canonical_json)
        if canonical_json(payload) != self.canonical_json:
            raise Engine2ValidationError("AI input payload is not in canonical form")
        plan = payload["monitoring_plan"]
        if (plan["monitoring_plan_id"], plan["plan_version"]) != (self.monitoring_plan_id, self.plan_version):
            raise Engine2ValidationError("AI input plan identity disagrees with its payload")
        if payload["baec"]["baec_id"] != self.baec_id or payload["candidate"]["candidate_id"] != self.candidate_id:
            raise Engine2ValidationError("AI input references disagree with its payload")

    @property
    def payload(self) -> dict:
        return json.loads(self.canonical_json)


def _ts(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def build_correspondence_ai_input(
    condition: BaecConditionInput, candidate: CorrespondenceCandidate, ledger: EvidenceLedger,
) -> CorrespondenceAIInput:
    """Build the snapshot from validated Engine 2 objects of one exact plan version, or fail before any model call."""
    if not isinstance(condition, BaecConditionInput) or not isinstance(candidate, CorrespondenceCandidate) \
            or not isinstance(ledger, EvidenceLedger):
        raise Engine2ValidationError("AI input needs a BaecConditionInput, a CorrespondenceCandidate, and an EvidenceLedger")
    plan = ledger.plan
    if plan_key(candidate) != plan_key(plan):
        raise Engine2ValidationError("candidate and evidence belong to different plan versions; plan versions never mix")
    if candidate.baec_id != plan.baec_id or condition.baec_id != plan.baec_id:
        raise Engine2ValidationError("BAEC reference disagrees with the Monitoring Plan")
    if condition.condition_reference != plan.condition_reference:
        raise Engine2ValidationError("condition reference disagrees with the Monitoring Plan")
    by_id = {o.observation_id: o for o in ledger.observations}
    if any(by_id.get(o.observation_id) != o for o in candidate.observations):
        raise Engine2ValidationError("candidate cites Observations outside the evidence snapshot")
    usable_obs, usable_dm = ledger.usable_observation_ids, ledger.usable_measurement_ids
    superseded = {s.superseded_observation_id: s for s in ledger.supersessions}
    order = list(CorrespondenceDimension)
    t = plan.threshold
    payload = {
        "input_version": INPUT_VERSION,
        "doctrine": [DOCTRINE_NO_PREDICTION, DOCTRINE_NARROWS],
        "baec": {"baec_id": condition.baec_id, "condition_reference": condition.condition_reference,
                 "buyer_exact_statement": condition.buyer_exact_statement,
                 "normalized_condition": condition.normalized_condition},
        "monitoring_plan": {
            "monitoring_plan_id": plan.monitoring_plan_id, "plan_version": plan.plan_version,
            "target_entities": list(plan.target_entities),
            "required_dimensions": [d.label for d in order if d in plan.required_dimensions],
            "not_applicable_dimensions": [d.label for d in order if d in plan.not_applicable_dimensions],
            "threshold": None if t is None else {"comparator": t.comparator.value, "value": str(t.value), "unit": t.unit},
            "timing_context": plan.timing_context,
            "plan_fact_ids": sorted(plan.plan_fact_ids), "plan_rule_ids": sorted(plan.plan_rule_ids),
        },
        "candidate": {"candidate_id": candidate.candidate_id, "signal_candidates": [
            {"signal_candidate_id": s.signal_candidate_id, "observation_ids": list(s.observation_ids)}
            for s in candidate.signal_candidates]},
        "observations": [
            {"observation_id": o.observation_id, "source_id": o.source_id, "source_type": o.source_type,
             "provenance": o.provenance.value, "observed_at": _ts(o.observed_at), "published_at": _ts(o.published_at),
             "effective_at": _ts(o.effective_at), "evidence_event_id": o.evidence_event_id,
             "content": o.exact_evidence_content, "usable_for_current_support": o.observation_id in usable_obs,
             "superseded_by": superseded[o.observation_id].superseding_observation_id if o.observation_id in superseded else None,
             "superseded_basis": superseded[o.observation_id].basis.value if o.observation_id in superseded else None,
             "retracted": superseded[o.observation_id].retraction if o.observation_id in superseded else False}
            for o in ledger.observations],
        "derived_measurements": [
            {"measurement_id": m.measurement_id, "transformation": m.transformation.value,
             "transformation_version": m.transformation_version, "formula": m.formula, "rounding_rule": m.rounding_rule,
             "inputs": [{"observation_id": i.observation_id, "quantity": i.quantity, "value": str(i.value), "unit": i.unit}
                        for i in m.inputs],
             "output_value": str(m.output_value), "output_unit": m.output_unit,
             "usable_for_current_support": m.measurement_id in usable_dm}
            for m in ledger.measurements],
        "source_refusals": [{"item_id": r.item_id, "reason": r.reason} for r in ledger.rejected_items],
        "dimensions": [d.label for d in order],
        "cross_cutting_checks": [c.label for c in CrossCuttingCheckName],
        "proposal_vocabulary": [p.value for p in ProposedFinding],
    }
    text = canonical_json(payload)
    return CorrespondenceAIInput(plan.monitoring_plan_id, plan.plan_version, plan.baec_id, candidate.candidate_id,
                                 text, input_digest(text))


# --- output contract -------------------------------------------------------------------

OUTPUT_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["schema_version", "monitoring_plan_id", "plan_version", "baec_id", "candidate_id", "input_digest",
                 "dimension_proposals", "cross_cutting_observations", "unresolved_questions",
                 "prohibited_inference_acknowledgement"],
    "properties": {
        "schema_version": {"const": OUTPUT_SCHEMA_VERSION},
        "monitoring_plan_id": {"type": "string"}, "plan_version": {"type": "integer", "minimum": 1},
        "baec_id": {"type": "string"}, "candidate_id": {"type": "string"}, "input_digest": {"type": "string"},
        "dimension_proposals": {"type": "array", "minItems": 11, "maxItems": 11, "items": {
            "type": "object", "additionalProperties": False,
            "required": ["dimension", "proposal", "unresolved_basis", "evidence", "rationale"],
            "properties": {"dimension": {"enum": [d.label for d in CorrespondenceDimension]},
                           "proposal": {"enum": [p.value for p in ProposedFinding]},
                           "unresolved_basis": {"enum": [b.value for b in UnresolvedBasis] + [None]},
                           "evidence": {"type": "array", "items": {"$ref": "#/$defs/citation"}},
                           "rationale": {"type": "string", "maxLength": MAX_RATIONALE}}}},
        "cross_cutting_observations": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["check", "concern", "evidence", "affected_dimensions"],
            "properties": {"check": {"enum": [c.label for c in CrossCuttingCheckName]},
                           "concern": {"type": "string", "maxLength": MAX_RATIONALE},
                           "evidence": {"type": "array", "items": {"$ref": "#/$defs/citation"}},
                           "affected_dimensions": {"type": "array", "items": {"enum": [d.label for d in CorrespondenceDimension]}}}}},
        "unresolved_questions": {"type": "array", "items": {"type": "string", "maxLength": MAX_RATIONALE}},
        "prohibited_inference_acknowledgement": {"const": PROHIBITED_INFERENCE_ACKNOWLEDGEMENT},
    },
    "$defs": {"citation": {"oneOf": [
        {"type": "object", "additionalProperties": False, "required": ["kind", "observation_id", "excerpt"],
         "properties": {"kind": {"const": "observation"}, "observation_id": {"type": "string"},
                        "excerpt": {"type": "string", "minLength": MIN_EXCERPT, "maxLength": MAX_EXCERPT}}},
        {"type": "object", "additionalProperties": False, "required": ["kind", "measurement_id", "observation_ids"],
         "properties": {"kind": {"const": "measurement"}, "measurement_id": {"type": "string"},
                        "observation_ids": {"type": "array", "items": {"type": "string"}}}},
        {"type": "object", "additionalProperties": False, "required": ["kind", "reference_id"],
         "properties": {"kind": {"const": "plan_reference"}, "reference_id": {"type": "string"}}},
        {"type": "object", "additionalProperties": False, "required": ["kind", "item_id"],
         "properties": {"kind": {"const": "refusal"}, "item_id": {"type": "string"}}},
        {"type": "object", "additionalProperties": False, "required": ["kind", "dimension"],
         "properties": {"kind": {"const": "plan_dimension"}, "dimension": {"enum": [d.label for d in CorrespondenceDimension]}}},
    ]}},
}

# --- AI artifact (distinct from every authoritative type) -------------------------------------


@dataclass(frozen=True)
class AIEvidenceCitation:
    """One citation inside an AI proposal. kind: observation | measurement | plan_reference | refusal | plan_dimension."""

    kind: str
    reference_id: str
    excerpt: str | None = None
    observation_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class AIDimensionProposal:
    """An AI hypothesis about one dimension. Not a DimensionAssessment and never authoritative."""

    dimension: CorrespondenceDimension
    proposal: ProposedFinding
    unresolved_basis: UnresolvedBasis | None
    evidence: tuple[AIEvidenceCitation, ...]
    rationale: str


@dataclass(frozen=True)
class AICheckObservation:
    """An AI note about a possible cross-cutting concern. Not a CrossCuttingCheck and never authoritative."""

    check: CrossCuttingCheckName
    concern: str
    evidence: tuple[AIEvidenceCitation, ...]
    affected_dimensions: frozenset[CorrespondenceDimension]


@dataclass(frozen=True)
class CorrespondenceAIProposal:
    """A validated AI correspondence proposal. Origin AI_INFERENCE; advisory input to a human reviewer only.

    It has no outcome, no sufficiency, and no authoritative finding, and nothing converts it into one.
    """

    artifact_id: str
    monitoring_plan_id: str
    plan_version: int
    baec_id: str
    candidate_id: str
    input_digest: str
    prompt_version: str
    output_schema_version: str
    provider: str
    model: str
    created_at: datetime
    dimension_proposals: tuple[AIDimensionProposal, ...]
    cross_cutting_observations: tuple[AICheckObservation, ...]
    unresolved_questions: tuple[str, ...]
    prohibited_inference_acknowledgement: str
    origin: ProvenanceCategory = ProvenanceCategory.AI_INFERENCE

    def __post_init__(self) -> None:
        if self.origin is not ProvenanceCategory.AI_INFERENCE:
            raise Engine2ValidationError("an AI correspondence proposal is always AI_INFERENCE")
        if [p.dimension for p in self.dimension_proposals] != list(CorrespondenceDimension):
            raise Engine2ValidationError("an AI proposal holds exactly the 11 dimensions in canonical order")
        if self.prohibited_inference_acknowledgement != PROHIBITED_INFERENCE_ACKNOWLEDGEMENT:
            raise Engine2ValidationError("the prohibited-inference acknowledgement must be exact")
        if not isinstance(self.created_at, datetime) or self.created_at.utcoffset() is None:
            raise Engine2ValidationError("created_at must be a timezone-aware datetime supplied by the caller")
        for name in ("artifact_id", "provider", "model"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise Engine2ValidationError(f"CorrespondenceAIProposal.{name} must be a non-empty string")


# --- model protocol --------------------------------------------------------------------------


@dataclass(frozen=True)
class ModelResponse:
    """Raw structured text returned by a model, with provider metadata. Untrusted until validated."""

    raw_text: str
    provider: str
    model: str


class CorrespondenceProposalModel(Protocol):
    """Anything that can propose. Stage F ships no implementation that calls a live model."""

    def propose(self, ai_input: CorrespondenceAIInput, *, system_prompt: str, output_schema: dict) -> ModelResponse:
        ...


def propose_correspondence(model: CorrespondenceProposalModel, ai_input: CorrespondenceAIInput, *,
                           artifact_id: str, created_at: datetime) -> CorrespondenceAIProposal:
    """Ask a model for a proposal and validate it strictly. Raises ProposalRejected on any defect."""
    if not isinstance(ai_input, CorrespondenceAIInput):
        raise Engine2ValidationError("propose_correspondence requires a CorrespondenceAIInput")
    response = model.propose(ai_input, system_prompt=SYSTEM_PROMPT, output_schema=OUTPUT_SCHEMA)
    if not isinstance(response, ModelResponse):
        raise ProposalRejected("the model did not return a ModelResponse")
    return validate_proposal_output(response, ai_input, artifact_id=artifact_id, created_at=created_at)


# --- strict validation (fail closed) ---------------------------------------------------------


def _reject(message: str):
    raise ProposalRejected(message)


def _no_duplicate_keys(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            _reject(f"duplicate key {key!r}")
        out[key] = value
    return out


def _bad_constant(name):
    _reject(f"non-finite number {name} is not allowed")


def _keys(obj: object, required: set[str], where: str) -> dict:
    if type(obj) is not dict:
        _reject(f"{where} must be an object")
    if set(obj) != required:
        extra, missing = sorted(set(obj) - required), sorted(required - set(obj))
        _reject(f"{where} has unexpected fields {extra} or missing fields {missing}")
    return obj


def _string(value: object, where: str, *, limit: int = MAX_RATIONALE) -> str:
    if type(value) is not str or not value.strip():
        _reject(f"{where} must be a non-empty string")
    if len(value) > limit:
        _reject(f"{where} is longer than {limit} characters")
    return value


def _free_text(value: object, where: str) -> str:
    text = _string(value, where)
    topic = generated_text_violation(text)
    if topic is not None:
        _reject(f"{where} is AI-generated text and may not discuss {topic}; the fixed acknowledgement owns that limit")
    return text


def _list(value: object, where: str) -> list:
    if type(value) is not list:
        _reject(f"{where} must be an array")
    return value


class _Index:
    """What the snapshot makes citable, derived from the AI input only."""

    def __init__(self, ai_input: CorrespondenceAIInput) -> None:
        payload = ai_input.payload
        plan = payload["monitoring_plan"]
        self.required = {CorrespondenceDimension.from_label(d) for d in plan["required_dimensions"]}
        self.not_applicable = {CorrespondenceDimension.from_label(d) for d in plan["not_applicable_dimensions"]}
        self.content = {o["observation_id"]: o["content"] for o in payload["observations"]}
        self.usable_obs = {o["observation_id"] for o in payload["observations"] if o["usable_for_current_support"]}
        self.measurements = {m["measurement_id"]: tuple(i["observation_id"] for i in m["inputs"])
                             for m in payload["derived_measurements"]}
        self.usable_dm = {m["measurement_id"] for m in payload["derived_measurements"] if m["usable_for_current_support"]}
        self.plan_refs = set(plan["plan_fact_ids"]) | set(plan["plan_rule_ids"])
        self.refusals = {r["item_id"] for r in payload["source_refusals"]}


def _citation(raw: object, index: _Index, where: str, *, allow_refusal: bool,
              allow_plan_dimension: bool = False) -> AIEvidenceCitation:
    if type(raw) is not dict:
        _reject(f"{where} must be an object")
    kind = raw.get("kind")
    if kind == "observation":
        _keys(raw, {"kind", "observation_id", "excerpt"}, where)
        oid = _string(raw["observation_id"], f"{where}.observation_id")
        if oid not in index.content:
            _reject(f"{where} cites unknown or out-of-version observation {oid!r}")
        excerpt = raw["excerpt"]
        if type(excerpt) is not str or len(excerpt) < MIN_EXCERPT or len(excerpt) > MAX_EXCERPT or not excerpt.strip():
            _reject(f"{where} excerpt must be {MIN_EXCERPT} to {MAX_EXCERPT} characters")
        if excerpt not in index.content[oid]:
            _reject(f"{where} excerpt is not an exact quotation of observation {oid!r}")
        return AIEvidenceCitation("observation", oid, excerpt=excerpt)
    if kind == "measurement":
        _keys(raw, {"kind", "measurement_id", "observation_ids"}, where)
        mid = _string(raw["measurement_id"], f"{where}.measurement_id")
        if mid not in index.measurements:
            _reject(f"{where} cites unknown or out-of-version measurement {mid!r}")
        ids = _list(raw["observation_ids"], f"{where}.observation_ids")
        if tuple(ids) != index.measurements[mid]:
            _reject(f"{where} must list exactly the input observations of {mid!r}")
        return AIEvidenceCitation("measurement", mid, observation_ids=tuple(ids))
    if kind == "plan_reference":
        _keys(raw, {"kind", "reference_id"}, where)
        ref = _string(raw["reference_id"], f"{where}.reference_id")
        if ref not in index.plan_refs:
            _reject(f"{where} cites unknown plan reference {ref!r}")
        return AIEvidenceCitation("plan_reference", ref)
    if kind == "plan_dimension" and allow_plan_dimension:
        _keys(raw, {"kind", "dimension"}, where)
        try:
            dimension = CorrespondenceDimension.from_label(raw["dimension"])
        except (Engine2ValidationError, TypeError) as error:
            raise ProposalRejected(f"{where}: {error}") from error
        if dimension not in index.not_applicable:
            _reject(f"{where} cites {dimension.label} as not applicable, but the Monitoring Plan requires it")
        return AIEvidenceCitation("plan_dimension", dimension.label)
    if kind == "refusal" and allow_refusal:
        _keys(raw, {"kind", "item_id"}, where)
        item = _string(raw["item_id"], f"{where}.item_id")
        if item not in index.refusals:
            _reject(f"{where} cites unknown refused item {item!r}")
        return AIEvidenceCitation("refusal", item)
    _reject(f"{where} has an unsupported citation kind {kind!r}")


def _current(citation: AIEvidenceCitation, index: _Index) -> bool:
    if citation.kind == "observation":
        return citation.reference_id in index.usable_obs
    if citation.kind == "measurement":
        return citation.reference_id in index.usable_dm
    return False


def validate_proposal_output(response: ModelResponse, ai_input: CorrespondenceAIInput, *, artifact_id: str,
                             created_at: datetime) -> CorrespondenceAIProposal:
    """Strictly validate raw output against the input snapshot. Any defect raises ProposalRejected."""
    if not isinstance(response, ModelResponse) or type(response.raw_text) is not str:
        _reject("the model response must carry raw text")
    try:
        data = json.loads(response.raw_text, object_pairs_hook=_no_duplicate_keys, parse_constant=_bad_constant)
    except json.JSONDecodeError as error:
        raise ProposalRejected(f"output is not valid JSON: {error}") from error
    top = _keys(data, {"schema_version", "monitoring_plan_id", "plan_version", "baec_id", "candidate_id",
                       "input_digest", "dimension_proposals", "cross_cutting_observations", "unresolved_questions",
                       "prohibited_inference_acknowledgement"}, "output")
    if top["schema_version"] != OUTPUT_SCHEMA_VERSION:
        _reject("unsupported output schema version")
    if type(top["plan_version"]) is not int or (top["monitoring_plan_id"], top["plan_version"]) != (
            ai_input.monitoring_plan_id, ai_input.plan_version):
        _reject("output plan identity differs from the input's exact plan version")
    if top["baec_id"] != ai_input.baec_id or top["candidate_id"] != ai_input.candidate_id:
        _reject("output BAEC or candidate reference differs from the input")
    if top["input_digest"] != ai_input.input_digest:
        _reject("output was produced for a different input snapshot")
    if top["prohibited_inference_acknowledgement"] != PROHIBITED_INFERENCE_ACKNOWLEDGEMENT:
        _reject("the prohibited-inference acknowledgement is missing or altered")
    index = _Index(ai_input)

    proposals: dict[CorrespondenceDimension, AIDimensionProposal] = {}
    for n, raw in enumerate(_list(top["dimension_proposals"], "dimension_proposals")):
        where = f"dimension_proposals[{n}]"
        item = _keys(raw, {"dimension", "proposal", "unresolved_basis", "evidence", "rationale"}, where)
        try:
            dimension = CorrespondenceDimension.from_label(item["dimension"])
            proposal = ProposedFinding(item["proposal"])
            basis = None if item["unresolved_basis"] is None else UnresolvedBasis(item["unresolved_basis"])
        except (Engine2ValidationError, ValueError, TypeError) as error:
            raise ProposalRejected(f"{where}: {error}") from error
        if dimension in proposals:
            _reject(f"{where} repeats dimension {dimension.label!r}")
        citations = tuple(_citation(c, index, f"{where}.evidence[{i}]", allow_refusal=False,
                                    allow_plan_dimension=proposal is ProposedFinding.PROPOSED_NOT_APPLICABLE)
                          for i, c in enumerate(_list(item["evidence"], f"{where}.evidence")))
        observed = [c for c in citations if c.kind in ("observation", "measurement")]
        if (proposal is ProposedFinding.PROPOSED_UNRESOLVED) != (basis is not None):
            _reject(f"{where}: unresolved_basis is required for PROPOSED_UNRESOLVED and forbidden otherwise")
        if basis is UnresolvedBasis.MISSING_EVIDENCE and observed:
            _reject(f"{where}: MISSING_EVIDENCE cannot cite observations or measurements")
        if basis is not None and basis is not UnresolvedBasis.MISSING_EVIDENCE and not observed:
            _reject(f"{where}: {basis.value} must cite the evidence that leaves the dimension unresolved")
        if proposal is ProposedFinding.PROPOSED_NOT_APPLICABLE and [
                (c.kind, c.reference_id) for c in citations] != [("plan_dimension", dimension.label)]:
            _reject(f"{where}: PROPOSED_NOT_APPLICABLE must cite exactly its own plan_dimension and nothing else")
        if proposal is ProposedFinding.PROPOSED_NOT_APPLICABLE and dimension not in index.not_applicable:
            _reject(f"{where}: {dimension.label} is required by the Monitoring Plan and cannot be PROPOSED_NOT_APPLICABLE")
        if dimension in index.not_applicable and proposal is not ProposedFinding.PROPOSED_NOT_APPLICABLE:
            _reject(f"{where}: {dimension.label} is not applicable under the Monitoring Plan")
        if proposal in _DECISIVE:
            if not any(_current(c, index) for c in citations):
                _reject(f"{where}: a decisive proposal needs current, usable observation or measurement evidence; "
                        "plan facts or rules alone are not enough")
            stale = [c.reference_id for c in citations if c.kind in ("observation", "measurement") and not _current(c, index)]
            if stale:
                _reject(f"{where}: superseded, retracted, or unusable evidence {stale} cannot support a decisive proposal")
        proposals[dimension] = AIDimensionProposal(dimension, proposal, basis, citations,
                                                   _free_text(item["rationale"], f"{where}.rationale"))
    if set(proposals) != set(CorrespondenceDimension):
        missing = [d.label for d in CorrespondenceDimension if d not in proposals]
        _reject(f"dimension_proposals must cover exactly the 11 dimensions; missing {missing}")

    checks = []
    for n, raw in enumerate(_list(top["cross_cutting_observations"], "cross_cutting_observations")):
        where = f"cross_cutting_observations[{n}]"
        item = _keys(raw, {"check", "concern", "evidence", "affected_dimensions"}, where)
        try:
            check = CrossCuttingCheckName.from_label(item["check"])
            affected = frozenset(CorrespondenceDimension.from_label(d)
                                 for d in _list(item["affected_dimensions"], f"{where}.affected_dimensions"))
        except (Engine2ValidationError, TypeError) as error:
            raise ProposalRejected(f"{where}: {error}") from error
        citations = tuple(_citation(c, index, f"{where}.evidence[{i}]", allow_refusal=True)
                          for i, c in enumerate(_list(item["evidence"], f"{where}.evidence")))
        checks.append(AICheckObservation(check, _free_text(item["concern"], f"{where}.concern"), citations, affected))
    questions = tuple(_free_text(q, f"unresolved_questions[{n}]")
                      for n, q in enumerate(_list(top["unresolved_questions"], "unresolved_questions")))
    try:
        return CorrespondenceAIProposal(
            artifact_id=artifact_id, monitoring_plan_id=ai_input.monitoring_plan_id, plan_version=ai_input.plan_version,
            baec_id=ai_input.baec_id, candidate_id=ai_input.candidate_id, input_digest=ai_input.input_digest,
            prompt_version=PROMPT_VERSION, output_schema_version=OUTPUT_SCHEMA_VERSION,
            provider=response.provider, model=response.model, created_at=created_at,
            dimension_proposals=tuple(proposals[d] for d in CorrespondenceDimension),
            cross_cutting_observations=tuple(checks), unresolved_questions=questions,
            prohibited_inference_acknowledgement=top["prohibited_inference_acknowledgement"],
        )
    except Engine2ValidationError as error:
        raise ProposalRejected(str(error)) from error
