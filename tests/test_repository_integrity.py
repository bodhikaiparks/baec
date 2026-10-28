"""Corrupted rows fail closed, constraints hold, saves are verified, writes are atomic."""

import sqlite3
from dataclasses import replace

import pytest

from baec_app.data import repository as repository_module
from baec_app.data.database import (
    APPEND_ONLY_TABLES,
    PersistenceIntegrityError,
    RepositoryConflictError,
    RepositoryNotFoundError,
    RepositoryVerificationError,
)
from baec_app.data.repository import Repository
from baec_app.domain.baec_rules import create_nonconfirmed_classification_record
from baec_app.domain.enums import ArticulationOrigin, BaecClassification, BaecCriterion, CriterionFinding, StalenessStatus
from baec_app.domain.models import AiDerivedText
from tests.builders import (
    AO,
    HARBOR_QUOTE,
    NOW,
    candidate,
    confirmed_record,
    judgment,
    record,
    seller_seeded_record,
)
from tests.persistence_builders import (  # noqa: F401
    connection,
    counts,
    move,
    no_leaked_connections,
    repo,
    save_confirmed,
    save_judgment,
    tamper,
)

C = BaecClassification
O = ArticulationOrigin


def nonconfirmed(origin=O.SELLER_SEEDED, baec_id="B-1", **kw):
    return create_nonconfirmed_classification_record(candidate(origin, **kw), baec_id=baec_id, captured_at=NOW)


# --- corrupted rows fail closed -----------------------------------------------

CONFIRMED_CORRUPTIONS = {
    "unknown classification text": "UPDATE baec_records SET classification = 'HOT_LEAD'",
    "unknown articulation origin": "UPDATE baec_records SET articulation_origin = 'MAYBE_BUYER'",
    "unknown elicitation mode": "UPDATE baec_records SET elicitation_mode = 'GUESSED'",
    "unknown staleness status": "UPDATE baec_records SET staleness_status = 'FRESH'",
    "unknown criterion": "UPDATE criterion_assessments SET criterion = 'C5' WHERE position = 0",
    "unknown finding": "UPDATE criterion_assessments SET finding = 'PROBABLY' WHERE position = 0",
    "unknown provenance": "UPDATE interaction_evidence SET provenance = 'HEARSAY'",
    "external provenance on buyer evidence": "UPDATE interaction_evidence SET provenance = 'EXTERNAL_EVIDENCE'",
    "AI inference as evidence": "UPDATE interaction_evidence SET provenance = 'AI_INFERENCE'",
    "naive timestamp": "UPDATE baec_records SET captured_at = '2026-01-15T12:00:00'",
    "unreadable timestamp": "UPDATE baec_records SET captured_at = 'last Tuesday'",
    "timestamp stored as a number": "UPDATE baec_records SET captured_at = '1736942400'",
    "missing criterion": "DELETE FROM criterion_assessments WHERE position = 3",
    "broken position order": "UPDATE criterion_assessments SET position = 7 WHERE position = 3",
    "broken evidence position order": "UPDATE criterion_evidence SET position = 2 WHERE criterion = 'EVALUATION_LINKAGE'",
    "MET criterion with its evidence removed": "DELETE FROM criterion_evidence WHERE criterion = 'PROSPECTIVE_CONDITION'",
    "criterion flipped to NOT_MET under a confirmed record": "UPDATE criterion_assessments SET finding = 'NOT_MET' WHERE position = 0",
    "origin flipped to seller-seeded under a confirmed record": "UPDATE baec_records SET articulation_origin = 'SELLER_SEEDED'",
    "confirmed record relabelled NOT_BAEC": "UPDATE baec_records SET classification = 'NOT_BAEC', classification_reason = 'r'",
    "confirmed record with confirmation removed": "UPDATE baec_records SET confirmation_authorization_id = NULL",
    "confirmed record with staleness removed": "UPDATE baec_records SET staleness_status = NULL",
    "confirmation for a different BAEC": "UPDATE human_authorizations SET subject_id = 'B-OTHER'",
    "confirmation with the wrong action": "UPDATE human_authorizations SET action = 'RECORD_DORMANCY_JUDGMENT'",
    "confirmation carrying a target state": "UPDATE human_authorizations SET target_state = 'ACTIVE_OPPORTUNITY'",
    "confirmation row missing": "DELETE FROM human_authorizations",
    "source excerpt row missing": "UPDATE baec_records SET source_excerpt_id = 999",
    "evidence moved to another interaction": "UPDATE interaction_evidence SET interaction_id = 'INT-2'",
    "evidence text altered so the quote is no longer verbatim": "UPDATE interaction_evidence SET text = 'Something else entirely.'",
    "buyer statement rewritten": "UPDATE baec_records SET buyer_exact_statement = 'We will switch immediately.'",
    "blank buyer role": "UPDATE baec_records SET buyer_role = ''",
    "normalized model without normalized text": "UPDATE baec_records SET normalized_model = 'example-model'",
    "normalized timestamp without normalized text": "UPDATE baec_records SET normalized_generated_at = '2026-01-15T12:00:00+00:00'",
    "normalized text without a timestamp": "UPDATE baec_records SET normalized_text = 'Normalized wording.'",
}


@pytest.mark.parametrize("sql", CONFIRMED_CORRUPTIONS.values(), ids=CONFIRMED_CORRUPTIONS.keys())
def test_corrupted_confirmed_record_fails_closed(repo, connection, sql):
    """Stored data that contradicts the domain rules raises; nothing is returned."""
    record_with_quote = replace(
        confirmed_record(),
        candidate=candidate(buyer_exact_statement=HARBOR_QUOTE, buyer_role="Materials Manager"),
    )
    repo.save_confirmed_baec(record_with_quote)
    assert repo.get_baec_record("B-1") == record_with_quote
    tamper(connection, sql)
    with pytest.raises(PersistenceIntegrityError):
        repo.get_baec_record("B-1")
    with pytest.raises(PersistenceIntegrityError):
        repo.list_baec_records("ACC-1")


def test_record_moved_to_another_account_fails_closed(repo, connection):
    save_confirmed(repo)
    tamper(connection, "UPDATE baec_records SET account_id = 'ACC-2'")
    with pytest.raises(PersistenceIntegrityError):
        repo.get_baec_record("B-1")
    with pytest.raises(PersistenceIntegrityError):
        repo.list_baec_records("ACC-2")
    tamper(connection, "UPDATE baec_records SET source_interaction_id = 'INT-404'")
    with pytest.raises(PersistenceIntegrityError):
        repo.get_baec_record("B-1")


STRINGENCY_CORRUPTIONS = {
    "threshold that is not a decimal": "UPDATE stringency_expressions SET numeric_value = 'ten'",
    "threshold removed under a numeric comparator": "UPDATE stringency_expressions SET numeric_value = NULL",
    "unknown comparator": "UPDATE stringency_expressions SET comparator = 'ROUGHLY'",
    "number invented under a qualitative comparator": "UPDATE stringency_expressions SET comparator = 'QUALITATIVE_ONLY'",
    "blank verbatim wording": "UPDATE stringency_expressions SET verbatim_text = ''",
}


@pytest.mark.parametrize("sql", STRINGENCY_CORRUPTIONS.values(), ids=STRINGENCY_CORRUPTIONS.keys())
def test_rc18_corrupted_stringency_fails_closed(repo, connection, sql):
    from decimal import Decimal

    from baec_app.domain.baec_rules import create_confirmed_baec_record
    from baec_app.domain.enums import ThresholdComparator
    from baec_app.domain.models import StringencyExpression
    from tests.builders import confirm_auth

    stringency = StringencyExpression("more than 10%", ThresholdComparator.GREATER_THAN, Decimal("10"), "%")
    repo.save_confirmed_baec(
        create_confirmed_baec_record(
            candidate(stringency=stringency), baec_id="B-1", captured_at=NOW, confirmation=confirm_auth()
        )
    )
    tamper(connection, sql)
    with pytest.raises(PersistenceIntegrityError):
        repo.get_baec_record("B-1")


NONCONFIRMED_CORRUPTIONS = {
    "tampered reason text": "UPDATE baec_records SET classification_reason = 'Buyer is a hot lead.'",
    "reason removed": "UPDATE baec_records SET classification_reason = NULL",
    "relabelled CONFIRMED_BAEC": "UPDATE baec_records SET classification = 'CONFIRMED_BAEC'",
    "relabelled INSUFFICIENT_EVIDENCE": "UPDATE baec_records SET classification = 'INSUFFICIENT_EVIDENCE'",
    "origin changed so the stored reason no longer applies": "UPDATE baec_records SET articulation_origin = 'UNCERTAIN'",
    "staleness added to a non-confirmed record": "UPDATE baec_records SET staleness_status = 'CURRENT'",
}


@pytest.mark.parametrize("sql", NONCONFIRMED_CORRUPTIONS.values(), ids=NONCONFIRMED_CORRUPTIONS.keys())
def test_rc13_corrupted_nonconfirmed_record_fails_closed(repo, connection, sql):
    """RC-13: a stored classification or reason that the rules would not produce is refused."""
    repo.save_classification_record(nonconfirmed())
    tamper(connection, sql)
    with pytest.raises(PersistenceIntegrityError):
        repo.get_baec_record("B-1")


JUDGMENT_CORRUPTIONS = {
    "unknown plausibility answer": "UPDATE dormancy_judgments SET plausibility = 'MAYBE'",
    "unknown addressability answer": "UPDATE dormancy_judgments SET addressability = 'SORT_OF'",
    "judgment authorization with the wrong action": "UPDATE human_authorizations SET action = 'CONFIRM_BAEC' WHERE authorization_id = 2",
    "judgment authorization for another BAEC": "UPDATE human_authorizations SET subject_id = 'B-OTHER' WHERE authorization_id = 2",
    "judgment authorization missing": "DELETE FROM human_authorizations WHERE authorization_id = 2",
    "judgment authorization with a naive timestamp": "UPDATE human_authorizations SET authorized_at = '2026-01-15T12:00:00' WHERE authorization_id = 2",
}


@pytest.mark.parametrize("sql", JUDGMENT_CORRUPTIONS.values(), ids=JUDGMENT_CORRUPTIONS.keys())
def test_rc22_corrupted_dormancy_judgment_fails_closed(repo, connection, sql):
    save_confirmed(repo)
    save_judgment(repo)
    tamper(connection, sql)
    with pytest.raises(PersistenceIntegrityError):
        repo.list_dormancy_judgments("B-1")


def test_corrupted_account_and_interaction_rows_fail_closed(repo, connection):
    tamper(connection, "UPDATE accounts SET state = 'HOT_LEAD' WHERE account_id = 'ACC-2'")
    with pytest.raises(PersistenceIntegrityError):
        repo.get_account("ACC-2")
    with pytest.raises(PersistenceIntegrityError):
        repo.list_accounts()
    tamper(connection, "UPDATE interactions SET occurred_at = 'yesterday' WHERE interaction_id = 'INT-1'")
    with pytest.raises(PersistenceIntegrityError):
        repo.get_interaction("INT-1")
    tamper(connection, "UPDATE interactions SET text = '' WHERE interaction_id = 'INT-2'")
    with pytest.raises(PersistenceIntegrityError):
        repo.get_interaction("INT-2")


# --- database constraints -----------------------------------------------------

CONSTRAINT_VIOLATIONS = {
    "evidence for an interaction that does not exist": (
        "INSERT INTO interaction_evidence (interaction_id, provenance, text) VALUES ('INT-404', 'BUYER_FACT', 'x')"
    ),
    "external evidence stored as interaction evidence": (
        "INSERT INTO interaction_evidence (interaction_id, provenance, text) VALUES ('INT-1', 'EXTERNAL_EVIDENCE', 'x')"
    ),
    "AI inference stored as interaction evidence": (
        "INSERT INTO interaction_evidence (interaction_id, provenance, text) VALUES ('INT-1', 'AI_INFERENCE', 'x')"
    ),
    "duplicate evidence identity": (
        "INSERT INTO interaction_evidence (interaction_id, provenance, text) "
        "SELECT interaction_id, provenance, text FROM interaction_evidence"
    ),
    "record whose interaction belongs to another account": (
        "INSERT INTO baec_records (baec_id, account_id, source_interaction_id, source_excerpt_id, captured_at, "
        "articulation_origin, elicitation_mode, classification, classification_reason) "
        "VALUES ('B-X', 'ACC-2', 'INT-1', 1, 't', 'SELLER_SEEDED', 'UNKNOWN', 'NOT_BAEC', 'r')"
    ),
    "record whose source excerpt is from another interaction": (
        "INSERT INTO baec_records (baec_id, account_id, source_interaction_id, source_excerpt_id, captured_at, "
        "articulation_origin, elicitation_mode, classification, classification_reason) "
        "VALUES ('B-X', 'ACC-1', 'INT-2', 1, 't', 'SELLER_SEEDED', 'UNKNOWN', 'NOT_BAEC', 'r')"
    ),
    "criterion evidence from another interaction": (
        "INSERT INTO criterion_evidence (baec_id, criterion, position, evidence_id, source_interaction_id) "
        "VALUES ('B-1', 'EVALUATION_LINKAGE', 1, 1, 'INT-2')"
    ),
    "criterion evidence for a criterion with no assessment": (
        "INSERT INTO criterion_evidence (baec_id, criterion, position, evidence_id, source_interaction_id) "
        "VALUES ('B-404', 'EVALUATION_LINKAGE', 0, 1, 'INT-1')"
    ),
    "a fifth assessment for the same criterion": (
        "INSERT INTO criterion_assessments (baec_id, criterion, position, finding) "
        "VALUES ('B-1', 'EVALUATION_LINKAGE', 4, 'MET')"
    ),
    "one authorization confirming two records": (
        "INSERT INTO baec_records (baec_id, account_id, source_interaction_id, source_excerpt_id, captured_at, "
        "articulation_origin, elicitation_mode, classification, staleness_status, confirmation_authorization_id) "
        "VALUES ('B-X', 'ACC-1', 'INT-1', 1, 't', 'BUYER_GENERATED', 'UNKNOWN', 'CONFIRMED_BAEC', 'CURRENT', 1)"
    ),
    "one authorization reused by a second judgment": (
        "INSERT INTO dormancy_judgments (baec_id, plausibility, addressability, authorization_id) "
        "VALUES ('B-1', 'YES', 'YES', 2)"
    ),
    "judgment for a BAEC that does not exist": (
        "INSERT INTO dormancy_judgments (baec_id, plausibility, addressability, authorization_id) "
        "VALUES ('B-404', 'YES', 'YES', 1)"
    ),
    "state-change authorization without a target": (
        "INSERT INTO human_authorizations (authorized_by, authorized_at, action, subject_id) "
        "VALUES ('x', 't', 'CHANGE_ACCOUNT_STATE', 'ACC-1')"
    ),
    "non-state authorization with a target": (
        "INSERT INTO human_authorizations (authorized_by, authorized_at, action, subject_id, target_state) "
        "VALUES ('x', 't', 'CONFIRM_BAEC', 'B-1', 'ACTIVE_OPPORTUNITY')"
    ),
    "unsupported account state": "UPDATE accounts SET state = 'HOT_LEAD'",
    "deleting an account that has interactions": "DELETE FROM accounts WHERE account_id = 'ACC-1'",
    "deleting a record that has assessments": "DELETE FROM baec_records WHERE baec_id = 'B-1'",
    "evaluation evidence whose interaction belongs to another account": (
        "INSERT INTO evaluation_evidence (account_id, interaction_id, evidence_id, observed_at) "
        "VALUES ('ACC-2', 'INT-1', 1, 't')"
    ),
    "non-evaluation evidence pointing at evidence from another interaction": (
        "INSERT INTO non_evaluation_evidence (account_id, interaction_id, evidence_id, observed_at) "
        "VALUES ('ACC-1', 'INT-2', 1, 't')"
    ),
    "transition whose basis interaction belongs to another account": (
        "INSERT INTO account_state_transitions (account_id, to_state, authorization_id, basis_interaction_id, "
        "unresolved, recorded_at) VALUES ('ACC-1', 'NO_PLAUSIBLE_PATH', 1, 'INT-9', '[]', 't')"
    ),
}


@pytest.mark.parametrize("sql", CONSTRAINT_VIOLATIONS.values(), ids=CONSTRAINT_VIOLATIONS.keys())
def test_database_constraints_refuse_structurally_invalid_rows(repo, connection, sql):
    """Raw SQL that bypasses the repository is still refused by keys and checks."""
    save_confirmed(repo)
    save_judgment(repo)
    before = counts(connection)
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(sql)
    assert counts(connection) == before


def _one_row_per_append_only_table(repo):
    save_confirmed(repo)
    from decimal import Decimal

    from baec_app.domain.baec_rules import create_confirmed_baec_record
    from baec_app.domain.enums import ThresholdComparator
    from baec_app.domain.models import StringencyExpression
    from tests.builders import confirm_auth

    repo.save_confirmed_baec(
        create_confirmed_baec_record(
            candidate(stringency=StringencyExpression("more than 10%", ThresholdComparator.GREATER_THAN, Decimal("10"))),
            baec_id="B-2",
            captured_at=NOW,
            confirmation=confirm_auth("B-2"),
        )
    )
    save_judgment(repo)
    assert move(repo, AO).allowed
    from tests.builders import NP

    assert move(repo, NP).allowed  # leaving Active stores non-evaluation evidence


@pytest.mark.parametrize("operation", ["UPDATE", "DELETE"])
@pytest.mark.parametrize("table", APPEND_ONLY_TABLES)
def test_rc33_append_only_tables_refuse_update_and_delete(repo, connection, table, operation):
    """RC-33: source evidence and history cannot be silently rewritten or removed."""
    _one_row_per_append_only_table(repo)
    assert counts(connection)[table] >= 1
    before = connection.execute(f"SELECT * FROM {table}").fetchall()
    column = connection.execute(f"PRAGMA table_info({table})").fetchall()[-1][1]
    sql = f"UPDATE {table} SET {column} = {column}" if operation == "UPDATE" else f"DELETE FROM {table}"
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        connection.execute(sql)
    assert connection.execute(f"SELECT * FROM {table}").fetchall() == before


# --- saves are verified against the locked rules ------------------------------


def test_rc13_save_classification_record_refuses_a_confirmed_record(repo, connection):
    with pytest.raises(RepositoryVerificationError):
        repo.save_classification_record(confirmed_record())
    assert counts(connection)["baec_records"] == 0


def test_rc13_save_classification_record_refuses_non_canonical_reason(repo, connection):
    """The model only requires a non-empty reason; the repository requires the canonical one."""
    hand_written = seller_seeded_record()  # reason: "seller supplied the condition"
    with pytest.raises(RepositoryVerificationError):
        repo.save_classification_record(hand_written)
    assert counts(connection)["baec_records"] == 0
    repo.save_classification_record(nonconfirmed())
    assert counts(connection)["baec_records"] == 1


def test_rc13_save_refuses_when_the_rules_compute_a_different_classification(repo, connection, monkeypatch):
    """If the locked rules disagree with the record, the record is not stored."""
    from baec_app.domain.baec_rules import classify_candidate

    saved = nonconfirmed()  # seller-seeded: NOT_BAEC
    disagreeing = classify_candidate(candidate(O.UNCERTAIN))  # INSUFFICIENT_EVIDENCE
    monkeypatch.setattr(repository_module, "classify_candidate", lambda _: disagreeing)
    with pytest.raises(RepositoryVerificationError):
        repo.save_classification_record(saved)
    assert counts(connection)["baec_records"] == 0


def test_save_confirmed_baec_refuses_non_confirmed_and_non_current_records(repo, connection):
    with pytest.raises(RepositoryVerificationError):
        repo.save_confirmed_baec(nonconfirmed())
    for status in (StalenessStatus.REVIEW_DUE, StalenessStatus.STALE, StalenessStatus.RETIRED):
        with pytest.raises(RepositoryVerificationError):
            repo.save_confirmed_baec(confirmed_record(staleness=status))
    assert counts(connection)["baec_records"] == 0
    assert counts(connection)["human_authorizations"] == 0


def test_save_refuses_wrong_types(repo):
    for method in (repo.save_classification_record, repo.save_confirmed_baec, repo.record_dormancy_judgment):
        with pytest.raises(RepositoryVerificationError):
            method("not a domain object")
    with pytest.raises(RepositoryVerificationError):
        repo.add_account("ACC-9")
    with pytest.raises(RepositoryVerificationError):
        repo.add_interaction("INT-9")


def test_save_identifier_errors(repo, connection):
    save_confirmed(repo)
    with pytest.raises(RepositoryConflictError):
        save_confirmed(repo)
    with pytest.raises(RepositoryNotFoundError):
        repo.save_confirmed_baec(confirmed_record(account_id="ACC-404", baec_id="B-2"))
    with pytest.raises(RepositoryNotFoundError):
        repo.get_baec_record("B-404")
    with pytest.raises(RepositoryNotFoundError):
        repo.list_baec_records("ACC-404")
    assert counts(connection)["baec_records"] == 1
    assert counts(connection)["human_authorizations"] == 1


def test_record_whose_interaction_is_missing_or_belongs_to_another_account_is_refused(repo, connection):
    from tests.builders import confirm_auth, excerpt

    from baec_app.domain.baec_rules import create_confirmed_baec_record

    missing = candidate(
        source_interaction_id="INT-404",
        source_excerpt=excerpt(source_id="INT-404"),
        criterion_evidence=excerpt(source_id="INT-404"),
    )
    with pytest.raises(RepositoryNotFoundError):
        repo.save_confirmed_baec(
            create_confirmed_baec_record(missing, baec_id="B-1", captured_at=NOW, confirmation=confirm_auth())
        )
    with pytest.raises(RepositoryVerificationError):
        repo.save_confirmed_baec(confirmed_record(account_id="ACC-2"))  # INT-1 belongs to ACC-1
    assert counts(connection)["baec_records"] == 0
    assert counts(connection)["human_authorizations"] == 0
    assert counts(connection)["interaction_evidence"] == 0


# --- transactions roll back completely ----------------------------------------


def _boom(*args, **kwargs):
    raise RuntimeError("forced failure midway through the write")


def test_failed_classification_record_write_leaves_no_rows(repo, connection, monkeypatch):
    before = counts(connection)
    monkeypatch.setattr(Repository, "_insert_stringency", _boom)
    with pytest.raises(RuntimeError):
        repo.save_classification_record(nonconfirmed())
    assert counts(connection) == before


def test_failed_confirmed_baec_write_leaves_no_rows(repo, connection, monkeypatch):
    before = counts(connection)
    monkeypatch.setattr(Repository, "_insert_assessments", _boom)
    with pytest.raises(RuntimeError):
        repo.save_confirmed_baec(confirmed_record())
    assert counts(connection) == before  # including the confirmation authorization and evidence row


def test_failed_dormancy_judgment_write_leaves_no_rows(repo, connection, monkeypatch):
    save_confirmed(repo)
    before = counts(connection)
    monkeypatch.setattr(Repository, "_insert_judgment_row", _boom)
    with pytest.raises(RuntimeError):
        repo.record_dormancy_judgment(judgment())
    assert counts(connection) == before


def test_failed_transition_write_leaves_no_rows_and_state_unchanged(repo, connection, monkeypatch):
    before = counts(connection)
    monkeypatch.setattr(Repository, "_update_account_state", _boom)
    with pytest.raises(RuntimeError):
        move(repo, AO)
    assert counts(connection) == before
    assert repo.get_account("ACC-1").state is None


def test_database_constraint_failure_inside_a_write_is_reported_and_rolled_back(repo, connection, monkeypatch):
    before = counts(connection)

    def break_a_foreign_key(self, saved):
        self._db.execute(
            "INSERT INTO stringency_expressions (baec_id, verbatim_text) VALUES ('B-404', 'x')"
        )

    monkeypatch.setattr(Repository, "_insert_stringency", break_a_foreign_key)
    with pytest.raises(RepositoryConflictError):
        repo.save_confirmed_baec(confirmed_record())
    assert counts(connection) == before


def test_a_write_after_a_rolled_back_write_still_works(repo, connection, monkeypatch):
    with monkeypatch.context() as patch:
        patch.setattr(Repository, "_insert_assessments", _boom)
        with pytest.raises(RuntimeError):
            repo.save_confirmed_baec(confirmed_record())
    saved = save_confirmed(repo)
    assert repo.get_baec_record("B-1") == saved
