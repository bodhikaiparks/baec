"""Stage F blinded annotation packets and strict human reference records."""

import dataclasses
import hashlib
import inspect
import json
from dataclasses import replace

import pytest

from baec_app.engine3.enums import ClaimSemanticClass, ClaimType, PreparationChannel, PreparationGoal, ReferenceUseMode
from baec_app.engine3.errors import Engine3ValidationError
from baec_app.engine3.models import ClaimField
from baec_app.engine3.proposal_contract import parse_artifact
from experiments.engine3_stage_f import annotation
from experiments.engine3_stage_f.annotation import (
    ANNOTATION_GUIDE,
    LOCKED_PROTOCOL_SHA256,
    OPERATIONAL_GUIDE_SHA256,
    PROVENANCE_FIELDS,
    REFERENCE_FIELDS,
    AdjudicatedSegment,
    Adjudication,
    AnnotationStatus,
    PrimaryAnnotation,
    ReferenceFields,
    SegmentAnnotation,
    annotation_packet_sha256,
    annotator_order,
    build_annotation_packet,
    finalize_reference,
    primary_field_agreement,
    record_provenance,
)
from experiments.engine3_stage_f.comparison import compare_artifact, reference_segments
from tests.engine3 import stage_f_adapter as ad

PACKET_KEYS = {"packet_id", "context_label", "artifact_type", "audience", "rendered_text", "segments",
               "grounding_items", "preparation_goal", "channel_scope", "guide"}
GROUNDING_KEYS = {"grounding_item_id", "exact_value", "disposition", "source_identity_disclosure",
                  "is_buyer_statement", "approximate", "approved_specificity", "adversity_related",
                  "adversity_context_reviewed"}
COMPARISON_LABELS = ("CONCORDANT_PASS", "CONCORDANT_REFUSAL", "FALSE_PASS", "FALSE_REFUSAL")
# HARNESS_AMENDMENT_002: the only approved change to the packet guide. Each evaluation-derived example in the locked
# spec is replaced by a fictional value; the normative rule text around it is unchanged.
APPROVED_EXAMPLE_SUBSTITUTIONS = {"9.00% -> 9%": "$46.00 -> $46", "more than 8% -> above 8%": "over $40 -> higher than $40"}
# Evaluation values that must never reach an annotator through instructional text (case content may carry them).
EVALUATION_VALUES = ("9.00%", "9%", "above 8%", "more than 8%", "alder", "ridge", "cobalt", "july")
HIDDEN_KEYS = {"arm", "repeat_index", "scenario_id", "family", "run_id", "run_kind", "binding", "context_id",
               "experiment_configuration_id", "experiment_provenance", "model", "model_identifier", "prompt",
               "sampling_configuration", "requested_action", "requests_score", "d_model", "d_ref", "report",
               "reason_code", "primary_reason_code", "category", "expected", "segment_id", "artifact_id"}


def _keys(value) -> set[str]:
    if isinstance(value, dict):
        return set(value) | set().union(*(_keys(v) for v in value.values()))
    if isinstance(value, list):
        return set().union(*(_keys(v) for v in value)) if value else set()
    return set()


def _dry_run_packets():
    ctx = ad.contexts()
    found = []
    for case in ad.load_inputs()["cases"]:
        _, proposal = ad.run(case, ctx)
        if proposal is None:
            continue
        for artifact in proposal.artifacts:
            found.append((case, proposal, artifact, ad.packet(case, artifact, ctx)))
    return found


PACKETS = _dry_run_packets()


def _ids(item):
    return f"{item[0]['case_id']}/{item[2].artifact_id}"


@pytest.mark.parametrize("item", PACKETS, ids=[_ids(p) for p in PACKETS])
def test_packet_shows_only_the_protocol_section_4_1_content(item):
    _, _, artifact, packet = item
    data = json.loads(packet.to_json())
    assert set(data) == PACKET_KEYS
    assert all(set(s) == {"packet_segment_id", "ordinal", "text"} for s in data["segments"])
    assert all(set(g) == GROUNDING_KEYS for g in data["grounding_items"])
    assert [s["text"] for s in data["segments"]] == [s.text for s in artifact.segments]
    assert data["rendered_text"] == artifact.rendered_text
    assert data["guide"] == json.loads(json.dumps(ANNOTATION_GUIDE))


@pytest.mark.parametrize("item", PACKETS, ids=[_ids(p) for p in PACKETS])
def test_packet_is_blind_to_model_annotations_run_identity_and_stage_d(item):
    case, proposal, artifact, packet = item
    text = packet.to_json()
    hidden_values = [
        case["case_id"], case["scenario_id"], case["purpose"], case["generation_context_id"],
        case["evaluation_context_id"], ad.load_inputs()["experiment_configuration_id"],
        proposal.digest, proposal.binding.bound_context_digest, artifact.artifact_id, "E3C-",
        "LIVE_SYNTHETIC_EXPERIMENT", "OFFLINE_HANDWRITTEN_DRY_RUN", *COMPARISON_LABELS,
    ]
    spec_scenario = ad.scenario(case["scenario_id"])
    if spec_scenario is not None:
        hidden_values.append(spec_scenario["family"])
    for value in hidden_values:
        assert value not in text, value
    assert not _keys(json.loads(text)) & HIDDEN_KEYS
    data = json.loads(text)
    model_ids = {s.segment_id for s in artifact.segments}
    assert not model_ids & {s["packet_segment_id"] for s in data["segments"]}
    for segment in data["segments"]:  # no annotation field is carried per segment
        assert not set(segment) & set(REFERENCE_FIELDS)


def test_model_only_annotation_values_never_reach_a_packet():
    ctx = ad.contexts()["CTX-AR-CURRENT"]
    canary = parse_artifact({
        "artifact_id": "CANARY-ARTIFACT-ID", "artifact_type": "FOLLOW_UP_EMAIL",
        "rendered_text": "Your contract went up a little.",
        "segments": [{
            "segment_id": "CANARY-SEGMENT-ID", "text": "Your contract went up a little.",
            "claim_type": "FACTUAL_ASSERTION", "semantic_class": "OPPORTUNITY_STATUS",
            "grounding_refs": ["CANARY-REF"], "reference_use_mode": "PARAPHRASE",
            "source_identity_refs": ["CANARY-SOURCE"], "claim_field": "SELLER_CAPABILITY",
            "declared_value": "CANARY-DECLARED", "specificity": 77, "question_has_premise": True,
            "source_instruction_ref": "CANARY-INSTRUCTION", "prescribes_buyer_choice": True,
        }],
    })
    text = build_annotation_packet(canary, ctx, None, None).to_json()
    assert "CANARY" not in text
    without_guide = json.loads(text)
    without_guide.pop("guide")  # the guide lists every vocabulary value; the rest must carry none of the labels
    rest = json.dumps(without_guide)
    for value in ("OPPORTUNITY_STATUS", "SELLER_CAPABILITY", "FACTUAL_ASSERTION", "\"PARAPHRASE\"", "77"):
        assert value not in rest, value


def test_packet_builder_cannot_receive_run_identity_or_model_results():
    params = list(inspect.signature(build_annotation_packet).parameters)
    assert params == ["artifact", "context", "preparation_goal", "channel_scope"]
    assert list(inspect.signature(finalize_reference).parameters) == ["packet", "primary_a", "primary_b", "adjudication"]


def _with_approved_examples(text: str) -> str:
    for old, new in APPROVED_EXAMPLE_SUBSTITUTIONS.items():
        assert text.count(old) <= 1, old
        text = text.replace(old, new)
    return text


def test_guide_conventions_match_the_locked_spec_exactly():
    # Rule text must equal the locked spec; only the approved example substitution (HARNESS_AMENDMENT_002) may differ.
    spec = ad.load_spec()["measurement_conventions"]
    for key in ("0", "10", "90"):
        assert ANNOTATION_GUIDE["specificity_convention"][key] == _with_approved_examples(spec["specificity"]["values"][key])
    assert spec["specificity"]["values"]["90"].count("9.00% -> 9%") == 1  # the substitution replaced exactly one example
    assert ANNOTATION_GUIDE["specificity_convention"]["excluded_values"] == spec["specificity"]["excluded_values"]
    for mode in ("INTERNAL", "DIRECT", "PARAPHRASE"):
        assert ANNOTATION_GUIDE["reference_use_convention"][mode] == spec["reference_use_mode"][mode]
    assert ANNOTATION_GUIDE["declared_value_convention"] == spec["declared_value"]["rule"]
    assert ANNOTATION_GUIDE["vocabularies"]["specificity"] == [0, 10, 90]
    assert ANNOTATION_GUIDE["annotation_statuses"] == ad.load_spec()["human_reference"]["annotation_statuses"]["values"]
    assert ANNOTATION_GUIDE["fields"] == [f["field"] for f in ad.load_spec()["evaluated_annotation_fields"]]
    assert ANNOTATION_GUIDE["experiment_flags"] == ad.load_spec()["human_reference"]["experiment_only_flags"]
    assert ANNOTATION_GUIDE["policy_attestations"] == ad.load_spec()["human_reference"]["h_attestation_fields"]
    vocab = ANNOTATION_GUIDE["vocabularies"]
    assert set(vocab["semantic_class_in_precedence_order"]) == {c.value for c in ClaimSemanticClass}
    assert vocab["claim_type"] == [c.value for c in ClaimType]
    assert vocab["claim_field"] == [c.value for c in ClaimField]
    assert vocab["reference_use_mode"] == [m.value for m in ReferenceUseMode]


def test_packet_guide_examples_are_the_approved_decontaminated_values():
    spec = ad.load_spec()["measurement_conventions"]["reference_use_mode"]
    variant, rewording = spec["formatting_only_numeric_variant"], spec["semantically_equivalent_rewording"]
    convention = ANNOTATION_GUIDE["reference_use_convention"]
    assert convention["formatting_only_numeric_variant"] == (
        f"may be coded {variant['may_be_coded']} (example {APPROVED_EXAMPLE_SUBSTITUTIONS[variant['example']]}) "
        f"and is flagged {variant['flag']}")
    assert convention["semantically_equivalent_rewording"] == (
        f"coded {rewording['coded']} (example {APPROVED_EXAMPLE_SUBSTITUTIONS[rewording['example']]})")
    assert ANNOTATION_GUIDE["specificity_convention"]["90"].endswith("formatting-only variants such as $46.00 -> $46")
    assert convention["formatting_only_numeric_variant"] == "may be coded DIRECT (example $46.00 -> $46) and is flagged formatting_variant"
    assert convention["semantically_equivalent_rewording"] == "coded PARAPHRASE (example over $40 -> higher than $40)"


@pytest.mark.parametrize("item", PACKETS, ids=[_ids(p) for p in PACKETS])
def test_no_evaluation_value_reaches_a_packet_through_the_instructional_guide(item):
    # The guide is the only instructional text in a packet; every other key is blinded case content.
    data = json.loads(item[3].to_json())
    assert set(data) - {"guide"} == PACKET_KEYS - {"guide"}
    instructional = json.dumps(data["guide"], ensure_ascii=False).casefold()
    for value in EVALUATION_VALUES:
        assert value not in instructional, value


def test_evaluation_value_check_separates_the_guide_from_case_content():
    # Case content may legitimately carry evaluation values (here the grounded 9.00% measurement); the check above
    # must therefore look only at the instructional guide, and it is not passing merely because no value occurs.
    case_content = []
    for _, _, _, packet in PACKETS:
        data = json.loads(packet.to_json())
        data.pop("guide")
        case_content.append(json.dumps(data, ensure_ascii=False))
    assert any("9.00%" in text for text in case_content)
    assert any("more than 8%" in text for text in case_content)


def test_packets_are_deterministic_and_deduplicate_byte_identical_prose():
    ctx = ad.contexts()
    _, _, artifact, packet = PACKETS[0]
    again = build_annotation_packet(artifact, ctx["CTX-AR-CURRENT"], PreparationGoal.CHECK_CONDITION_STILL_RELEVANT,
                                    PreparationChannel.EMAIL)
    assert again.to_json() == packet.to_json()
    relabelled = replace(artifact, artifact_id="A-OTHER",
                         segments=tuple(replace(s, segment_id=f"X{n}", specificity=90)
                                        for n, s in enumerate(artifact.segments)))
    assert build_annotation_packet(relabelled, ctx["CTX-AR-CURRENT"], None, None).packet_id == packet.packet_id
    assert build_annotation_packet(artifact, ctx["CTX-AR-9PCT-DIRECT"], None, None).packet_id != packet.packet_id
    assert len({p[3].packet_id for p in PACKETS}) == ad.load_oracle()["expected_packet_count"]


def test_annotator_order_is_deterministic_seeded_and_complete():
    ids = tuple(p[3].packet_id for p in PACKETS)
    first = annotator_order(ids, "seed-a")
    assert first == annotator_order(ids, "seed-a") and sorted(first) == sorted(set(ids))
    assert first != annotator_order(ids, "seed-b")
    with pytest.raises(Engine3ValidationError):
        annotator_order(ids, "")


# --- reference records ------------------------------------------------------------------------------

COURTESY = dict(claim_type=ClaimType.COURTESY_OR_TRANSITION, semantic_class=ClaimSemanticClass.COURTESY,
                grounding_refs=(), reference_use_mode=ReferenceUseMode.DIRECT, source_identity_refs=(),
                claim_field=ClaimField.NONE, declared_value=None, specificity=0, question_has_premise=False,
                source_instruction_ref=None, prescribes_buyer_choice=False)


@pytest.mark.parametrize("changes", [
    {"specificity": 50}, {"specificity": 100}, {"specificity": True},
    {"claim_type": ClaimType.FACTUAL_ASSERTION},  # factual claim needs a claim_field
    {"claim_field": ClaimField.OTHER},  # courtesy has claim_field NONE
    {"declared_value": "9%"},  # a value needs a specific-value claim_field
    {"question_has_premise": True},  # premise only for questions
    {"grounding_refs": ("G-B", "G-A")}, {"grounding_refs": ("G-A", "G-A")}, {"source_identity_refs": ["G-A"]},
    {"declared_value": ""}, {"source_instruction_ref": ""}, {"semantic_class": "COURTESY"},
])
def test_reference_fields_follow_the_protocol(changes):
    with pytest.raises(Engine3ValidationError):
        ReferenceFields(**dict(COURTESY, **changes))


def test_segments_that_are_not_scorable_are_never_given_labels():
    fields = ReferenceFields(**COURTESY)
    with pytest.raises(Engine3ValidationError):
        SegmentAnnotation("PS-1", AnnotationStatus.SCORABLE)
    for status in (AnnotationStatus.UNSCORABLE_SEGMENT_STRUCTURE, AnnotationStatus.ADJUDICATION_REQUIRED):
        with pytest.raises(Engine3ValidationError):
            SegmentAnnotation("PS-1", status, fields, note="n")
        with pytest.raises(Engine3ValidationError):
            SegmentAnnotation("PS-1", status, None, formatting_variant=True, note="n")
    with pytest.raises(Engine3ValidationError):
        SegmentAnnotation("PS-1", AnnotationStatus.ADJUDICATION_REQUIRED)  # a flag needs a note
    with pytest.raises(Engine3ValidationError):
        SegmentAnnotation("PS-1", AnnotationStatus.SCORABLE, fields, mixed_reference_modes=True)  # DIRECT
    paraphrase = replace(fields, reference_use_mode=ReferenceUseMode.PARAPHRASE)
    with pytest.raises(Engine3ValidationError):
        SegmentAnnotation("PS-1", AnnotationStatus.SCORABLE, paraphrase, formatting_variant=True)


def _case(case_id):
    return next(c for c in ad.load_inputs()["cases"] if c["case_id"] == case_id)


def _item(case_id, artifact_id):
    return next(p for p in PACKETS if p[0]["case_id"] == case_id and p[2].artifact_id == artifact_id)


def test_agreeing_primaries_finalize_without_adjudication():
    case, _, _, packet = _item("F-DRY-01", "A-EMAIL")
    reference = ad.reference(case, "A-EMAIL", packet)
    assert reference.reference_complete and reference.human_safe is True
    assert not any(s.adjudicated for s in reference.segments)


def test_disagreement_requires_adjudication_and_adjudication_decides():
    case, _, _, packet = _item("F-DRY-09", "A-CALL")
    a, b = ad.primaries(case, "A-CALL", packet)
    unresolved = finalize_reference(packet, a, b, None)
    assert not unresolved.reference_complete
    assert [s.status for s in unresolved.segments] == [AnnotationStatus.ADJUDICATION_REQUIRED, AnnotationStatus.SCORABLE]
    resolved = ad.reference(case, "A-CALL", packet)
    assert resolved.reference_complete and resolved.segments[0].adjudicated
    assert resolved.segments[0].fields.specificity == 90


def test_unscorable_segments_are_reported_and_never_forced_into_stage_d():
    case, proposal, artifact, packet = _item("F-DRY-07", "A-EMAIL")
    reference = ad.reference(case, "A-EMAIL", packet)
    assert not reference.reference_complete and reference.segments[0].fields is None
    with pytest.raises(Engine3ValidationError):
        reference_segments(artifact, reference)
    comparison = compare_artifact(proposal, "A-EMAIL", ad.contexts()["CTX-AR-9PCT-DIRECT"], reference)
    assert comparison.d_ref is None and comparison.category is None and comparison.unscorable_ordinals == (1,)
    assert comparison.d_model.allowed  # D_model is still reported for non-reference-complete artifacts


def test_flag_disagreement_alone_requires_adjudication():
    case, _, _, packet = _item("F-DRY-08", "A-EMAIL")
    a, b = ad.primaries(case, "A-EMAIL", packet)
    b = replace(b, segments=(replace(b.segments[0], formatting_variant=False),) + b.segments[1:])
    assert finalize_reference(packet, a, b, None).segments[0].status is AnnotationStatus.ADJUDICATION_REQUIRED


@pytest.mark.parametrize("breaker", [
    lambda a, b, j, p: (a, replace(b, alias=a.alias), j),
    lambda a, b, j, p: (a, b, replace(j, alias=a.alias)),
    lambda a, b, j, p: (replace(a, role=b.role), b, j),
    lambda a, b, j, p: (a, replace(b, packet_id="0" * 64), j),
    lambda a, b, j, p: (a, b, replace(j, annotation_packet_sha256="0" * 64)),
    lambda a, b, j, p: (a, replace(b, segments=b.segments[:1]), j),
    lambda a, b, j, p: (a, b, replace(j, segments=j.segments + (AdjudicatedSegment(b.segments[1], "r"),))),
    lambda a, b, j, p: (a, b, replace(j, attestation=a.attestation)),
])
def test_reference_records_fail_closed(breaker):
    case, _, _, packet = _item("F-DRY-09", "A-CALL")
    a, b = ad.primaries(case, "A-CALL", packet)
    j = ad.adjudication(case, "A-CALL", packet)
    with pytest.raises(Engine3ValidationError):
        finalize_reference(packet, *breaker(a, b, j, packet))


def test_references_may_cite_only_ids_shown_and_use_internal_only_for_internal_artifacts():
    case, _, _, packet = _item("F-DRY-01", "A-EMAIL")
    a, b = ad.primaries(case, "A-EMAIL", packet)
    fields = a.segments[1].fields
    for bad in (replace(fields, grounding_refs=("G-NOT-SHOWN",)),
                replace(fields, reference_use_mode=ReferenceUseMode.INTERNAL)):
        changed = replace(a, segments=(a.segments[0], replace(a.segments[1], fields=bad), a.segments[2]))
        with pytest.raises(Engine3ValidationError):
            finalize_reference(packet, changed, b, None)


def test_attestation_disagreement_needs_adjudication():
    case, _, _, packet = _item("F-DRY-01", "A-EMAIL")
    a, b = ad.primaries(case, "A-EMAIL", packet)
    b = replace(b, attestation=replace(b.attestation, factual_accuracy=False))
    reference = finalize_reference(packet, a, b, None)
    assert reference.attestation is None and reference.human_safe is None and reference.reference_complete
    decided = Adjudication("ADJ", packet.packet_id, **record_provenance(packet), recorded_at="2026-10-15T09:00:00Z",
                           segments=(), attestation=a.attestation)
    assert finalize_reference(packet, a, b, decided).human_safe is True


def test_records_require_rfc3339_utc_and_digest_bindings():
    case, _, _, packet = _item("F-DRY-01", "A-EMAIL")
    a, _ = ad.primaries(case, "A-EMAIL", packet)
    for changes in ({"recorded_at": "2026-10-14 12:00"}, {"annotation_packet_sha256": "x"}, {"alias": ""}):
        with pytest.raises(Engine3ValidationError):
            replace(a, **changes)


def test_pre_adjudication_primary_agreement_is_per_field_over_both_scorable_segments():
    case, _, _, packet = _item("F-DRY-09", "A-CALL")
    counts = primary_field_agreement([ad.primaries(case, "A-CALL", packet)])
    assert set(counts) == set(REFERENCE_FIELDS)
    assert counts["specificity"] == (1, 2) and counts["claim_type"] == (2, 2)
    case, _, _, packet = _item("F-DRY-07", "A-EMAIL")
    assert primary_field_agreement([ad.primaries(case, "A-EMAIL", packet)])["claim_type"] == (1, 1)


def test_statuses_and_flags_are_experiment_metadata_outside_engine3_vocabularies():
    import baec_app.engine3.enums as engine3_enums
    vocab = {m.value for e in vars(engine3_enums).values()
             if isinstance(e, type) and issubclass(e, engine3_enums.Enum) for m in e}
    assert not {s.value for s in AnnotationStatus} & vocab
    assert "formatting_variant" not in {f for f in annotation.ReferenceFields.__dataclass_fields__}


# --- HARNESS_AMENDMENT_003: annotation record provenance hashes ------------------------------------

REFERENCE_ITEMS = [p for p in PACKETS if p[2].artifact_id in p[0]["human_reference"]]


def _records(item):
    case, _, artifact, packet = item
    a, b = ad.primaries(case, artifact.artifact_id, packet)
    return a, b, ad.adjudication(case, artifact.artifact_id, packet)


def _expected_provenance(packet):
    return {"locked_protocol_sha256": ad.protocol_sha256(), "operational_guide_sha256": ad.guide_sha256(),
            "annotation_packet_sha256": hashlib.sha256(packet.to_json().encode("utf-8")).hexdigest()}


def test_provenance_constants_are_the_exact_frozen_artifact_hashes():
    assert PROVENANCE_FIELDS == ("locked_protocol_sha256", "operational_guide_sha256", "annotation_packet_sha256")
    assert LOCKED_PROTOCOL_SHA256 == ad.protocol_sha256() == (
        "cba979c91cdcc96f419b0f3758cadd651f3427064a7f66ebebd23929c623b5a4")
    assert OPERATIONAL_GUIDE_SHA256 == ad.guide_sha256() == (
        "fb4f0510254649dadfc68c06bb76bd1dfab73e2d33bc0db325faf791e61b9011")
    freeze = json.loads((ad.REPO / "docs" / "engine3" / "ENGINE3_STAGE_F_HUMAN_REFERENCE_OPERATIONAL_GUIDE_FREEZE_MANIFEST.json")
                        .read_text(encoding="utf-8"))
    assert freeze["operational_guide_sha256"] == OPERATIONAL_GUIDE_SHA256
    assert freeze["locked_protocol_sha256"] == LOCKED_PROTOCOL_SHA256


def test_dry_run_has_records_for_every_role():
    assert REFERENCE_ITEMS
    assert any(_records(item)[2] is not None for item in REFERENCE_ITEMS)


@pytest.mark.parametrize("item", REFERENCE_ITEMS, ids=[_ids(p) for p in REFERENCE_ITEMS])
def test_every_primary_a_primary_b_and_adjudication_record_carries_the_three_hashes(item):
    packet = item[3]
    expected = _expected_provenance(packet)
    for record in (r for r in _records(item) if r is not None):
        assert {name: getattr(record, name) for name in PROVENANCE_FIELDS} == expected, type(record).__name__
        assert record.locked_protocol_sha256 == LOCKED_PROTOCOL_SHA256
        assert record.operational_guide_sha256 == OPERATIONAL_GUIDE_SHA256
        assert record.annotation_packet_sha256 == annotation_packet_sha256(packet)


def test_records_have_no_protocol_version_fallback_field():
    for cls in (PrimaryAnnotation, Adjudication):
        names = {f.name for f in dataclasses.fields(cls)}
        assert set(PROVENANCE_FIELDS) <= names
        assert "protocol_version_sha256" not in names


def test_a_record_with_only_a_protocol_version_hash_cannot_be_built():
    case, _, _, packet = _item("F-DRY-01", "A-EMAIL")
    a, _ = ad.primaries(case, "A-EMAIL", packet)
    header = dict(alias=a.alias, role=a.role, packet_id=a.packet_id, recorded_at=a.recorded_at,
                  attestation=a.attestation, segments=a.segments)
    with pytest.raises(TypeError):
        PrimaryAnnotation(**header, protocol_version_sha256=LOCKED_PROTOCOL_SHA256)
    # Putting the protocol hash in every provenance slot does not pass either.
    with pytest.raises(Engine3ValidationError):
        PrimaryAnnotation(**header, locked_protocol_sha256=LOCKED_PROTOCOL_SHA256,
                          operational_guide_sha256=LOCKED_PROTOCOL_SHA256,
                          annotation_packet_sha256=LOCKED_PROTOCOL_SHA256)
    b_like = PrimaryAnnotation(**dict(header, alias="other"), locked_protocol_sha256=LOCKED_PROTOCOL_SHA256,
                               operational_guide_sha256=OPERATIONAL_GUIDE_SHA256,
                               annotation_packet_sha256=LOCKED_PROTOCOL_SHA256)
    with pytest.raises(Engine3ValidationError):
        finalize_reference(packet, a, replace(b_like, role=annotation.AnnotatorRole.PRIMARY_B), None)


@pytest.mark.parametrize("field", PROVENANCE_FIELDS)
@pytest.mark.parametrize("value", [None, "", "x", "A" * 64, "0" * 63, "0" * 64])
def test_missing_or_wrong_provenance_hashes_fail_closed(field, value):
    case, _, _, packet = _item("F-DRY-09", "A-CALL")
    a, b = ad.primaries(case, "A-CALL", packet)
    j = ad.adjudication(case, "A-CALL", packet)
    for record in (a, b, j):
        if field == "annotation_packet_sha256" and value == "0" * 64:
            # Well-formed but not this packet: caught where the record is bound to its packet.
            broken = replace(record, **{field: value})
            records = [a, b, j]
            records[records.index(record)] = broken
            with pytest.raises(Engine3ValidationError):
                finalize_reference(packet, *records)
        else:
            with pytest.raises(Engine3ValidationError):
                replace(record, **{field: value})


def test_records_missing_a_provenance_field_cannot_be_built():
    case, _, _, packet = _item("F-DRY-01", "A-EMAIL")
    a, _ = ad.primaries(case, "A-EMAIL", packet)
    full = {f.name: getattr(a, f.name) for f in dataclasses.fields(PrimaryAnnotation)}
    for name in PROVENANCE_FIELDS:
        partial = {k: v for k, v in full.items() if k != name}
        with pytest.raises(TypeError):
            PrimaryAnnotation(**partial)


def test_a_record_made_on_another_packet_is_rejected_even_with_the_same_packet_id():
    case, _, _, packet = _item("F-DRY-01", "A-EMAIL")
    a, b = ad.primaries(case, "A-EMAIL", packet)
    shown = replace(packet, guide={**packet.guide, "fields": ["claim_type"]})  # same packet_id, different content
    assert shown.packet_id == packet.packet_id
    b_other = replace(b, annotation_packet_sha256=annotation_packet_sha256(shown))
    with pytest.raises(Engine3ValidationError):
        finalize_reference(packet, a, b_other, None)
    assert finalize_reference(packet, a, b, None).reference_complete


def test_packet_hash_is_the_sha256_of_the_exact_visible_packet_json():
    for _, _, _, packet in PACKETS:
        assert annotation_packet_sha256(packet) == hashlib.sha256(packet.to_json().encode("utf-8")).hexdigest()
    assert len({annotation_packet_sha256(p[3]) for p in PACKETS}) == len({p[3].to_json() for p in PACKETS})
    with pytest.raises(Engine3ValidationError):
        annotation_packet_sha256(json.loads(PACKETS[0][3].to_json()))


def _visible_changes(packet):
    segment = packet.segments[0]
    item = packet.grounding_items[0]
    return {
        "rendered_text": replace(packet, rendered_text=packet.rendered_text + " "),
        "segment_text": replace(packet, segments=(replace(segment, text=segment.text + "!"),) + packet.segments[1:]),
        "grounding_value": replace(packet, grounding_items=(replace(item, exact_value=item.exact_value + "0"),)
                                   + packet.grounding_items[1:]),
        "grounding_disclosure": replace(packet, grounding_items=(replace(item, approved_specificity=0 if
                                        item.approved_specificity else 90),) + packet.grounding_items[1:]),
        "guide": replace(packet, guide={**packet.guide, "experiment_flags": ["formatting_variant"]}),
        "context_label": replace(packet, context_label="context-000000000000"),
        "preparation_goal": replace(packet, preparation_goal=None),
    }


@pytest.mark.parametrize("change", sorted(_visible_changes(_item("F-DRY-01", "A-EMAIL")[3])))
def test_packet_hash_changes_when_visible_packet_content_changes(change):
    packet = _item("F-DRY-01", "A-EMAIL")[3]
    changed = _visible_changes(packet)[change]
    assert changed.to_json() != packet.to_json()
    assert annotation_packet_sha256(changed) != annotation_packet_sha256(packet)


def test_packet_hash_ignores_hidden_model_annotations_and_run_metadata():
    ctx = ad.contexts()
    case, proposal, artifact, packet = _item("F-DRY-01", "A-EMAIL")
    # Different model annotations, model segment ids, and artifact id: nothing a reviewer sees changes.
    relabelled = replace(artifact, artifact_id="A-HIDDEN",
                         segments=tuple(replace(s, segment_id=f"HIDDEN-{n}", specificity=90, grounding_refs=(),
                                                prescribes_buyer_choice=not s.prescribes_buyer_choice)
                                        for n, s in enumerate(artifact.segments)))
    rebuilt = ad.packet(case, relabelled, ctx)
    assert annotation_packet_sha256(rebuilt) == annotation_packet_sha256(packet)
    # A different run (another configuration id, arm, repeat, and run kind) of the same prose: same hash.
    other_run = dict(case, repeat_index=case["repeat_index"] + 1)
    assert annotation_packet_sha256(ad.packet(other_run, artifact, ctx)) == annotation_packet_sha256(packet)
    assert proposal.binding.repeat_index == case["repeat_index"]
    # The provenance builder takes only the packet; no run, proposal, or model value can reach it.
    assert list(inspect.signature(record_provenance).parameters) == ["packet"]
    assert list(inspect.signature(annotation_packet_sha256).parameters) == ["packet"]
