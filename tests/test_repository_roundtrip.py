"""Every persistable Phase 3 domain object survives a save and load unchanged."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from itertools import product

import pytest

from baec_app.data.database import (
    RepositoryConflictError,
    RepositoryNotFoundError,
    RepositoryVerificationError,
)
from baec_app.data.records import PersistedDormancyJudgment, RecordValidationError, SourceInteraction
from baec_app.data.repository import decode_datetime, decode_decimal, encode_datetime, encode_decimal
from baec_app.domain.baec_rules import (
    classify_candidate,
    create_confirmed_baec_record,
    create_nonconfirmed_classification_record,
)
from baec_app.domain.enums import (
    ArticulationOrigin,
    BaecClassification,
    BaecCriterion,
    CriterionFinding,
    ElicitationMode,
    ProvenanceCategory,
    ReviewAnswer,
    StalenessStatus,
    ThresholdComparator,
)
from baec_app.domain.models import Account, AiDerivedText, CriterionAssessment, StringencyExpression
from tests.builders import (
    AO,
    HARBOR_QUOTE,
    NOW,
    candidate,
    confirm_auth,
    confirmed_record,
    excerpt,
    judgment,
    record,
)
from tests.persistence_builders import (  # noqa: F401
    connection,
    counts,
    no_leaked_connections,
    repo,
    save_confirmed,
)

C = BaecClassification
F = CriterionFinding
O = ArticulationOrigin
T = ThresholdComparator
CRITERIA = list(BaecCriterion)
INDIA = timezone(timedelta(hours=5, minutes=30))


def confirmed_from(cand, baec_id="B-1", captured_at=NOW):
    return create_confirmed_baec_record(
        cand, baec_id=baec_id, captured_at=captured_at, confirmation=confirm_auth(baec_id)
    )


def save_any(repo, saved):
    if saved.classification is C.CONFIRMED_BAEC:
        repo.save_confirmed_baec(saved)
    else:
        repo.save_classification_record(saved)


# --- accounts and interactions ------------------------------------------------


def test_account_round_trip_and_listing_order(repo):
    assert repo.get_account("ACC-1") == Account("ACC-1", "Harbor Surgical Center", None)
    assert [a.account_id for a in repo.list_accounts()] == ["ACC-1", "ACC-2"]


def test_rc21_accounts_can_only_be_inserted_unclassified(repo):
    """State is set only by a persisted transition, so history is always complete."""
    with pytest.raises(RepositoryVerificationError):
        repo.add_account(Account("ACC-3", "Synthetic", AO))
    assert [a.account_id for a in repo.list_accounts()] == ["ACC-1", "ACC-2"]


def test_account_duplicate_and_unknown_identifiers(repo):
    with pytest.raises(RepositoryConflictError):
        repo.add_account(Account("ACC-1", "Again"))
    with pytest.raises(RepositoryNotFoundError):
        repo.get_account("ACC-404")


def test_interaction_round_trip(repo):
    interaction = SourceInteraction("INT-50", "ACC-1", NOW, "Seller: Hello.\nBuyer: Hello.")
    repo.add_interaction(interaction)
    assert repo.get_interaction("INT-50") == interaction
    assert [i.interaction_id for i in repo.list_interactions("ACC-1")] == ["INT-1", "INT-2", "INT-3", "INT-50"]
    assert [i.interaction_id for i in repo.list_interactions("ACC-2")] == ["INT-9"]


def test_interaction_errors(repo):
    with pytest.raises(RepositoryNotFoundError):
        repo.add_interaction(SourceInteraction("INT-51", "ACC-404", NOW, "x"))
    with pytest.raises(RepositoryConflictError):
        repo.add_interaction(SourceInteraction("INT-1", "ACC-1", NOW, "x"))
    with pytest.raises(RepositoryNotFoundError):
        repo.get_interaction("INT-404")
    with pytest.raises(RecordValidationError):
        SourceInteraction("INT-52", "ACC-1", datetime(2026, 1, 1), "x")


# --- BAEC records -------------------------------------------------------------


def _with_rationale():
    base = candidate()
    first = base.assessments[0]
    changed = (CriterionAssessment(first.criterion, first.finding, first.evidence, "Buyer said so."),)
    return replace(base, assessments=changed + base.assessments[1:])


RECORD_CASES = {
    "confirmed, minimal": lambda: confirmed_from(candidate()),
    "confirmed, exact statement and role": lambda: confirmed_from(
        candidate(buyer_exact_statement=HARBOR_QUOTE, buyer_role="Materials Manager")
    ),
    "confirmed, assessments in reverse order": lambda: confirmed_from(candidate(reverse=True)),
    "confirmed, with a rationale": lambda: confirmed_from(_with_rationale()),
    "confirmed, two excerpts on one criterion": lambda: confirmed_from(
        replace(
            candidate(),
            assessments=candidate().assessments[:3]
            + (
                CriterionAssessment(
                    CRITERIA[3],
                    F.MET,
                    (excerpt(), excerpt("Rep note: buyer tied it to renewal.", ProvenanceCategory.SELLER_OBSERVATION)),
                ),
            ),
        )
    ),
    "confirmed, source excerpt is a seller observation": lambda: confirmed_from(
        candidate(source_excerpt=excerpt("Rep note: pricing at renewal.", ProvenanceCategory.SELLER_OBSERVATION))
    ),
    "not BAEC, seller-seeded": lambda: create_nonconfirmed_classification_record(
        candidate(O.SELLER_SEEDED), baec_id="B-1", captured_at=NOW
    ),
    "not BAEC, two criteria not met": lambda: create_nonconfirmed_classification_record(
        candidate(findings={CRITERIA[0]: F.NOT_MET, CRITERIA[3]: F.NOT_MET}), baec_id="B-1", captured_at=NOW
    ),
    "insufficient, unknown criterion with no evidence": lambda: create_nonconfirmed_classification_record(
        candidate(findings={CRITERIA[1]: F.UNKNOWN}), baec_id="B-1", captured_at=NOW
    ),
    "insufficient, uncertain origin": lambda: create_nonconfirmed_classification_record(
        candidate(O.UNCERTAIN), baec_id="B-1", captured_at=NOW
    ),
    "confirmed, AI-derived text with model": lambda: replace(
        confirmed_from(candidate()),
        normalized_condition=AiDerivedText("Incumbent price increase above 10% at renewal.", NOW, "example-model"),
    ),
    "confirmed, AI-derived text without model": lambda: replace(
        confirmed_from(candidate()), normalized_condition=AiDerivedText("Normalized wording.", NOW)
    ),
}


@pytest.mark.parametrize("build", RECORD_CASES.values(), ids=RECORD_CASES.keys())
def test_rc33_baec_record_round_trip(repo, build):
    """RC-33: source evidence and every other field come back exactly as saved."""
    saved = build()
    save_any(repo, saved)
    assert repo.get_baec_record("B-1") == saved
    assert repo.list_baec_records("ACC-1") == (saved,)


ALL_COMBINATIONS = [(f, o) for f in product(list(F), repeat=4) for o in O]


@pytest.mark.parametrize(
    "combo", ALL_COMBINATIONS, ids=lambda c: "-".join(f.name for f in c[0]) + "+" + c[1].name
)
def test_rc13_every_classification_combination_round_trips(repo, combo):
    """All 243 finding/origin combinations: every finding, origin and classification value."""
    findings, origin = combo
    cand = candidate(origin, dict(zip(CRITERIA, findings)))
    if classify_candidate(cand).classification is C.CONFIRMED_BAEC:
        saved = confirmed_from(cand)
    else:
        saved = create_nonconfirmed_classification_record(cand, baec_id="B-1", captured_at=NOW)
    save_any(repo, saved)
    assert repo.get_baec_record("B-1") == saved


@pytest.mark.parametrize("mode", list(ElicitationMode), ids=lambda m: m.value)
def test_every_elicitation_mode_round_trips(repo, mode):
    saved = confirmed_from(replace(candidate(), elicitation_mode=mode))
    repo.save_confirmed_baec(saved)
    assert repo.get_baec_record("B-1").candidate.elicitation_mode is mode


def test_optional_values_round_trip_as_none(repo):
    saved = confirmed_from(candidate())
    repo.save_confirmed_baec(saved)
    loaded = repo.get_baec_record("B-1")
    assert loaded.candidate.buyer_role is None
    assert loaded.candidate.buyer_exact_statement is None
    assert loaded.candidate.stringency is None
    assert loaded.classification_reason is None
    assert loaded.normalized_condition is None
    assert all(a.rationale is None for a in loaded.candidate.assessments)


def test_list_baec_records_keeps_insertion_order(repo):
    for baec_id in ("B-2", "B-1", "B-3"):
        repo.save_confirmed_baec(confirmed_from(candidate(), baec_id=baec_id))
    assert [r.baec_id for r in repo.list_baec_records("ACC-1")] == ["B-2", "B-1", "B-3"]
    assert repo.list_baec_records("ACC-2") == ()


# --- stringency, Decimal ------------------------------------------------------

STRINGENCY_CASES = {
    "GREATER_THAN": StringencyExpression("more than 10%", T.GREATER_THAN, Decimal("10"), "%", timing_text="at renewal"),
    "AT_LEAST": StringencyExpression("at least 5", T.AT_LEAST, Decimal("5")),
    "LESS_THAN": StringencyExpression("under 2 days", T.LESS_THAN, Decimal("2"), "days"),
    "AT_MOST": StringencyExpression("no more than 3", T.AT_MOST, Decimal("3")),
    "EXACTLY": StringencyExpression("exactly 12", T.EXACTLY, Decimal("12")),
    "APPROXIMATELY": StringencyExpression("around 10%", T.APPROXIMATELY, Decimal("10"), "%"),
    "QUALITATIVE_ONLY": StringencyExpression("a significant increase", T.QUALITATIVE_ONLY, qualitative_term="significant"),
    "NONE_STATED": StringencyExpression("recurring monthly", T.NONE_STATED, recurrence_text="recurring monthly"),
    "not normalized": StringencyExpression("somewhere in the double digits, depending", None),
}


def test_stringency_cases_cover_every_comparator():
    assert {s.comparator for s in STRINGENCY_CASES.values()} == set(T) | {None}


@pytest.mark.parametrize("stringency", STRINGENCY_CASES.values(), ids=STRINGENCY_CASES.keys())
def test_rc18_stringency_round_trip(repo, stringency):
    """RC-18: the buyer's threshold wording and comparator come back exactly."""
    saved = confirmed_from(candidate(stringency=stringency))
    repo.save_confirmed_baec(saved)
    assert repo.get_baec_record("B-1").candidate.stringency == stringency


DECIMALS = ["10", "10.0", "10.00", "0.1", "0.10", "12.3456789012345678901234567890", "1E+2", "-5", "0"]


@pytest.mark.parametrize("text", DECIMALS)
def test_rc18_decimal_thresholds_are_stored_as_text_without_float_conversion(repo, connection, text):
    original = Decimal(text)
    saved = confirmed_from(candidate(stringency=StringencyExpression("stated", T.GREATER_THAN, original)))
    repo.save_confirmed_baec(saved)
    stored, storage_type = connection.execute(
        "SELECT numeric_value, typeof(numeric_value) FROM stringency_expressions"
    ).fetchone()
    assert storage_type == "text"
    assert stored == text
    loaded = repo.get_baec_record("B-1").candidate.stringency.numeric_value
    assert isinstance(loaded, Decimal)
    assert loaded.as_tuple() == original.as_tuple()  # same digits and exponent: 10 is not 10.0
    assert str(loaded) == text


def test_decimal_and_datetime_codecs_reject_what_they_cannot_represent():
    assert decode_decimal(encode_decimal(None)) is None
    with pytest.raises(RecordValidationError):
        encode_decimal(10.0)
    with pytest.raises(ValueError):
        decode_decimal("ten")
    with pytest.raises(RecordValidationError):
        encode_datetime(datetime(2026, 1, 1))
    with pytest.raises(ValueError):
        decode_datetime("2026-01-01T00:00:00")


# --- datetimes ----------------------------------------------------------------


def test_datetime_is_stored_in_utc_and_returns_as_the_same_instant(repo, connection):
    local = datetime(2026, 3, 10, 20, 45, 12, 345678, tzinfo=INDIA)
    saved = confirmed_from(candidate(), captured_at=local)
    repo.save_confirmed_baec(saved)
    stored = connection.execute("SELECT captured_at FROM baec_records").fetchone()[0]
    assert stored == "2026-03-10T15:15:12.345678+00:00"
    loaded = repo.get_baec_record("B-1")
    assert loaded.captured_at == local  # same instant, microseconds kept
    assert loaded.captured_at.utcoffset() == timedelta(0)
    assert loaded == saved


def test_every_stored_timestamp_column_is_utc_text(repo, connection):
    save_confirmed(repo)
    repo.record_dormancy_judgment(judgment())
    for table, column in (
        ("interactions", "occurred_at"),
        ("baec_records", "captured_at"),
        ("human_authorizations", "authorized_at"),
    ):
        for (value,) in connection.execute(f"SELECT {column} FROM {table}"):
            assert value.endswith("+00:00") and len(value) == 32


# --- evidence row identity and reuse ------------------------------------------


def test_an_excerpt_shared_by_source_and_all_criteria_is_stored_once(repo, connection):
    save_confirmed(repo)
    assert counts(connection)["interaction_evidence"] == 1
    assert counts(connection)["criterion_evidence"] == 4
    ids = {row[0] for row in connection.execute("SELECT evidence_id FROM criterion_evidence")}
    source = connection.execute("SELECT source_excerpt_id FROM baec_records").fetchone()[0]
    assert ids == {source}


def test_the_same_excerpt_is_reused_across_records_of_one_interaction(repo, connection):
    save_confirmed(repo, "B-1")
    save_confirmed(repo, "B-2")
    assert counts(connection)["interaction_evidence"] == 1
    assert repo.get_baec_record("B-1").candidate.source_excerpt == repo.get_baec_record("B-2").candidate.source_excerpt


def test_same_text_with_different_provenance_is_a_different_evidence_row(repo, connection):
    note = excerpt(HARBOR_QUOTE, ProvenanceCategory.SELLER_OBSERVATION)
    saved = confirmed_from(candidate(source_excerpt=note))
    repo.save_confirmed_baec(saved)
    rows = connection.execute("SELECT provenance FROM interaction_evidence ORDER BY provenance").fetchall()
    assert rows == [("BUYER_FACT",), ("SELLER_OBSERVATION",)]
    assert repo.get_baec_record("B-1") == saved


# --- dormancy judgments -------------------------------------------------------


@pytest.mark.parametrize("plausibility", list(ReviewAnswer), ids=lambda a: "P=" + a.value)
@pytest.mark.parametrize("addressability", list(ReviewAnswer), ids=lambda a: "A=" + a.value)
def test_rc22_dormancy_judgment_round_trip(repo, plausibility, addressability):
    """RC-22: the human answers are stored as given, including UNKNOWN and NOT_YET."""
    save_confirmed(repo)
    original = judgment(plausibility, addressability)
    judgment_id = repo.record_dormancy_judgment(original)
    assert repo.list_dormancy_judgments("B-1") == (PersistedDormancyJudgment(judgment_id, original),)


def test_dormancy_judgments_are_listed_in_order_with_their_identifiers(repo):
    save_confirmed(repo)
    first = repo.record_dormancy_judgment(judgment(ReviewAnswer.NOT_YET, ReviewAnswer.NOT_YET))
    second = repo.record_dormancy_judgment(replace(judgment(), notes="Reassessed after renewal call."))
    stored = repo.list_dormancy_judgments("B-1")
    assert [p.judgment_id for p in stored] == [first, second]
    assert first < second
    assert stored[1].judgment.notes == "Reassessed after renewal call."
    assert stored[0].judgment.notes is None


def test_dormancy_judgment_for_an_unknown_baec_is_not_found(repo, connection):
    before = counts(connection)
    with pytest.raises(RepositoryNotFoundError):
        repo.record_dormancy_judgment(judgment(baec_id="B-404"))
    with pytest.raises(RepositoryNotFoundError):
        repo.list_dormancy_judgments("B-404")
    assert counts(connection) == before


def test_persisted_dormancy_judgment_validates_itself():
    with pytest.raises(RecordValidationError):
        PersistedDormancyJudgment(0, judgment())
    with pytest.raises(RecordValidationError):
        PersistedDormancyJudgment(1, "not a judgment")


# --- staleness ----------------------------------------------------------------


@pytest.mark.parametrize("status", list(StalenessStatus), ids=lambda s: s.value)
def test_rc29_older_confirmed_records_rehydrate_with_any_staleness_status(repo, connection, status):
    """New saves must be CURRENT, but a stored record with another status still loads."""
    save_confirmed(repo)
    connection.execute("UPDATE baec_records SET staleness_status = ?", (status.value,))
    assert repo.get_baec_record("B-1").staleness_status is status
