"""Engine 2 domain records: vocabularies, plan activation, sources, Observations, measurements."""

from dataclasses import FrozenInstanceError, replace
from datetime import datetime
from decimal import Decimal

import pytest

from baec_app.domain.enums import ProvenanceCategory, StalenessStatus
from baec_app.engine2.domain import (
    ALL_DIMENSIONS,
    BAEC_REVALIDATION_REQUIRED,
    ActiveMonitoringPlan,
    CorrespondenceDimension,
    CorrespondenceOutcome,
    CrossCuttingCheckName,
    DimensionFinding,
    Observation,
    Supersession,
    SupersessionBasis,
    SufficiencyFinding,
    activate_monitoring_plan,
)
from baec_app.engine2.errors import (
    DerivedMeasurementError,
    Engine2ValidationError,
    MonitoringPlanActivationError,
    SourceNotAuthorizedError,
)
from baec_app.engine2.measurement import MeasurementInput, Transformation, compute_derived_measurement
from tests.engine2 import builders as b

STAGE_A_DIMENSIONS = [
    "Entity match", "Relationship match", "Source relevance", "Event or condition type", "Threshold match",
    "Comparator match", "Numeric magnitude", "Unit match", "Timing match", "Renewal or effective-date match",
    "Causal or contextual relevance",
]
STAGE_A_CHECKS = ["Freshness", "Contradictory evidence", "Missing evidence", "Provenance quality"]


# --- closed vocabularies -------------------------------------------------------


def test_exactly_the_eleven_stage_a_dimensions_with_canonical_labels():
    assert [d.label for d in CorrespondenceDimension] == STAGE_A_DIMENSIONS
    assert all(CorrespondenceDimension.from_label(label).label == label for label in STAGE_A_DIMENSIONS)
    assert ALL_DIMENSIONS == frozenset(CorrespondenceDimension) and len(ALL_DIMENSIONS) == 11


@pytest.mark.parametrize("label", ["Freshness", "Entity", "entity match", "Price match", ""])
def test_unknown_dimension_label_is_refused(label):
    with pytest.raises(Engine2ValidationError):
        CorrespondenceDimension.from_label(label)


def test_finding_sufficiency_and_outcome_vocabularies_are_exact():
    assert [f.value for f in DimensionFinding] == ["SUPPORTED", "CONTRADICTED", "UNRESOLVED", "NOT_APPLICABLE"]
    assert [s.value for s in SufficiencyFinding] == ["YES", "NO", "UNKNOWN"]
    assert [o.value for o in CorrespondenceOutcome] == [
        "NO_CORRESPONDENCE", "POSSIBLE_CORRESPONDENCE", "HUMAN_VERIFIED_CORRESPONDENCE"]
    assert BAEC_REVALIDATION_REQUIRED not in {o.value for o in CorrespondenceOutcome}
    with pytest.raises(ValueError):
        SufficiencyFinding("UNRESOLVED")
    for legacy in ("VERIFIED_CORRESPONDENCE", "LIKELY_CORRESPONDENCE", "STRONG_CORRESPONDENCE"):
        with pytest.raises(ValueError):
            CorrespondenceOutcome(legacy)


def test_exactly_four_cross_cutting_checks_separate_from_dimensions():
    assert [c.label for c in CrossCuttingCheckName] == STAGE_A_CHECKS
    for label in STAGE_A_CHECKS:
        with pytest.raises(Engine2ValidationError):
            CorrespondenceDimension.from_label(label)


# --- Monitoring Plan activation (Stage A Section 4.3) ------------------------


def test_plan_activates_only_on_a_matching_current_reading():
    active = activate_monitoring_plan(b.plan(), b.current())
    assert isinstance(active, ActiveMonitoringPlan) and active.monitoring_plan_id == b.PLAN_ID


@pytest.mark.parametrize(
    "reading",
    [
        None,
        b.current(status=None),
        b.current(record=None),
        b.current(status=StalenessStatus.REVIEW_DUE),
        b.current(status=StalenessStatus.STALE),
        b.current(status=StalenessStatus.RETIRED),
        b.current(baec_id="BAEC-OTHER"),
        b.current(record="CONF-OTHER"),
    ],
    ids=["missing", "unreadable-status", "unreadable-record", "review-due", "stale", "retired", "other-baec", "other-record"],
)
def test_missing_or_non_current_engine1_reading_fails_closed(reading):
    with pytest.raises(MonitoringPlanActivationError):
        activate_monitoring_plan(b.plan(), reading)
    with pytest.raises(MonitoringPlanActivationError):
        ActiveMonitoringPlan(b.plan(), reading)


def test_plan_must_partition_all_eleven_dimensions():
    with pytest.raises(Engine2ValidationError):
        b.plan(required_dimensions=ALL_DIMENSIONS - {CorrespondenceDimension.TIMING_MATCH})
    with pytest.raises(Engine2ValidationError):
        b.plan(not_applicable_dimensions=frozenset({CorrespondenceDimension.TIMING_MATCH}))
    with pytest.raises(Engine2ValidationError):
        b.plan(authorized_source_ids=frozenset())


# --- source authorization and Observations -----------------------------------


@pytest.mark.parametrize("source_id", [None, "SRC-UNKNOWN"])
def test_unauthorized_source_is_refused_before_any_observation_exists(source_id):
    with pytest.raises(SourceNotAuthorizedError):
        b.observe(b.active(), it=b.item(source_id=source_id))


def test_source_authorized_by_plan_but_without_record_is_refused():
    from baec_app.engine2.domain import capture_observation
    with pytest.raises(SourceNotAuthorizedError):
        capture_observation(b.active(), {}, b.item(), observation_id="OBS-X", provenance=ProvenanceCategory.EXTERNAL_EVIDENCE,
                            content_sha256="0" * 64, source_locator="x", evidence_event_id="EVT-X")


def test_observation_is_raw_immutable_and_holds_no_calculation():
    obs = b.observe(b.active())
    assert obs.exact_evidence_content == b.item().content
    with pytest.raises(FrozenInstanceError):
        obs.exact_evidence_content = "edited"
    with pytest.raises(TypeError):
        replace(obs, derived_value=Decimal("12"))


@pytest.mark.parametrize("provenance", [ProvenanceCategory.AI_INFERENCE, ProvenanceCategory.UNKNOWN])
def test_observation_cannot_be_ai_inference_or_unknown(provenance):
    obs = b.observe(b.active())
    with pytest.raises(Engine2ValidationError):
        replace(obs, provenance=provenance)


@pytest.mark.parametrize("digest", ["", "ABC", "0" * 63, "G" * 64, "A" * 64])
def test_content_hash_must_be_sha256_hex(digest):
    with pytest.raises(Engine2ValidationError):
        replace(b.observe(b.active()), content_sha256=digest)


def test_content_hash_is_sha256_of_exact_utf8_text():
    import hashlib
    from baec_app.engine2.domain import content_integrity_sha256
    text = "Prix augment\u00e9s de 12% \u2014 renouvellement\r\n"
    assert content_integrity_sha256(text) == hashlib.sha256(text.encode("utf-8")).hexdigest()
    obs = b.observe(b.active(), it=b.item(content=text))
    assert obs.content_sha256 == hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_content_hash_mismatch_fails_closed():
    obs = b.observe(b.active())
    with pytest.raises(Engine2ValidationError):
        replace(obs, exact_evidence_content=obs.exact_evidence_content + "!")
    with pytest.raises(Engine2ValidationError):
        replace(obs, content_sha256="0" * 64)


def test_content_hash_applies_no_unicode_normalization():
    import unicodedata
    from baec_app.engine2.domain import content_integrity_sha256
    composed = "R\u00e9sum\u00e9 notice"
    decomposed = unicodedata.normalize("NFD", composed)
    assert composed != decomposed and content_integrity_sha256(composed) != content_integrity_sha256(decomposed)
    obs = b.observe(b.active(), it=b.item(content=composed))
    with pytest.raises(Engine2ValidationError):
        replace(obs, exact_evidence_content=decomposed)


@pytest.mark.parametrize("variant", [" {}", "{} ", "{}\n", "{}".replace("{}", "{}\r\n"), "  ".join(["{}", ""])])
def test_content_hash_is_whitespace_and_case_sensitive(variant):
    from baec_app.engine2.domain import content_integrity_sha256
    base = "Pricing rises 12% at renewal."
    assert content_integrity_sha256(variant.format(base)) != content_integrity_sha256(base)
    assert content_integrity_sha256(base.upper()) != content_integrity_sha256(base)


def test_naive_timestamps_are_refused():
    with pytest.raises(Engine2ValidationError):
        replace(b.observe(b.active()), observed_at=datetime(2027, 1, 1))


def test_supersession_requires_a_recorded_basis_and_never_edits_the_original():
    a = b.active()
    first = b.observe(a, "OBS-T-01")
    second = b.observe(a, "OBS-T-02", b.item("PKT-T-02", content="CORRECTION: pricing rises 7% at renewal."))
    with pytest.raises(Engine2ValidationError):
        Supersession("OBS-T-01", "OBS-T-02", "explicit correction or retraction language")
    led = b.ledger(a, [first, second],
                   supersessions=(Supersession("OBS-T-01", "OBS-T-02", SupersessionBasis.EXPLICIT_CORRECTION_OR_RETRACTION),))
    assert led.usable_observation_ids == {"OBS-T-02"}
    assert led.observations[0].exact_evidence_content == b.item().content


def test_duplicate_copies_of_one_event_are_one_underlying_event():
    a = b.active()
    copies = [b.observe(a, f"OBS-T-0{i}", b.item(f"PKT-T-0{i}"), event="EVT-ONE") for i in range(1, 5)]
    led = b.ledger(a, copies)
    assert led.evidence_events([o.observation_id for o in copies]) == {"EVT-ONE"}


# --- Derived Measurements (Stage A Section 7.3) -------------------------------


def _obs(oid: str, content: str | None = None):
    return b.observe(b.active(), oid, b.item(f"PKT-{oid}", content=content or f"Synthetic notice {oid}."))


def _pct(base: str, new: str, base_obs=None, new_obs=None):
    return compute_derived_measurement(
        "DM-T", Transformation.PERCENT_CHANGE_FROM_TWO_PRICES,
        (MeasurementInput(base_obs or _obs("OBS-A"), "baseline_price", Decimal(base), "USD per pack"),
         MeasurementInput(new_obs or _obs("OBS-B"), "new_price", Decimal(new), "USD per pack")), b.T0)


@pytest.mark.parametrize("base,new,expected", [("100.00", "110.00", "10"), ("100.00", "110.01", "10.01"),
                                               ("100.00", "112.00", "12"), ("102.40", "112.00", "9.375")])
def test_percent_change_is_exact_decimal(base, new, expected):
    m = _pct(base, new)
    assert m.output_value == Decimal(expected) and isinstance(m.output_value, Decimal)


def test_exactly_ten_and_ten_point_zero_one_stay_distinct():
    assert _pct("100.00", "110.00").output_value == Decimal("10")
    assert _pct("100.00", "110.01").output_value > Decimal("10")


def test_non_terminating_result_is_refused_not_rounded():
    with pytest.raises(DerivedMeasurementError):
        _pct("3.00", "4.00")  # 33.333...%: rounding rule "none" refuses it


@pytest.mark.parametrize("bps,expected", [("1000", "10"), ("1001", "10.01"), ("1100", "11")])
def test_basis_points_conversion(bps, expected):
    m = compute_derived_measurement("DM-T", Transformation.BASIS_POINTS_TO_PERCENT,
                                    (MeasurementInput(_obs("OBS-A"), "basis_points", Decimal(bps), "basis points"),), b.T0)
    assert m.output_value == Decimal(expected)


def test_measurement_cannot_carry_a_wrong_output_or_unknown_transformation():
    m = _pct("100.00", "110.00")
    with pytest.raises(Engine2ValidationError):
        replace(m, output_value=Decimal("10.01"))
    with pytest.raises(ValueError):
        Transformation("corpus.arbitrary_expression")
    with pytest.raises(DerivedMeasurementError):
        compute_derived_measurement("DM-T", Transformation.PERCENT_CHANGE_FROM_TWO_PRICES,
                                    (MeasurementInput(_obs("OBS-A"), "baseline_price", Decimal("100"), "USD per pack"),), b.T0)


def test_measurement_with_corrected_input_is_no_longer_usable():
    a = b.active()
    base = b.observe(a, "OBS-A", b.item("PKT-A", content="Baseline USD 100.00 per pack."))
    corrected = b.observe(a, "OBS-C", b.item("PKT-C", content="CORRECTION: baseline USD 102.40 per pack."))
    new = b.observe(a, "OBS-B", b.item("PKT-B", content="New price USD 112.00 per pack."))
    old_dm = _pct("100.00", "112.00", base, new)
    led = b.ledger(a, [base, corrected, new], measurements=(old_dm,),
                   supersessions=(Supersession("OBS-A", "OBS-C", SupersessionBasis.EXPLICIT_CORRECTION_OR_RETRACTION),))
    assert "DM-T" in led.measurement_ids and "DM-T" not in led.usable_measurement_ids
    assert base.exact_evidence_content == "Baseline USD 100.00 per pack."
