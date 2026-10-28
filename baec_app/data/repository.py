"""Stores and rehydrates the locked domain objects.

Rules this module follows:

* It never classifies a BAEC and never decides a state transition. It may
  call the locked domain functions to verify, and it refuses to persist
  anything those functions would not produce.
* No caller-built TransitionResult is accepted. A transition is persisted
  only after the repository itself re-runs the locked state machine against
  the stored account and stored prerequisites.
* Every load goes back through the locked domain constructors. Corrupt or
  contradictory stored data raises PersistenceIntegrityError; nothing
  partial or repaired is ever returned.
* Every mutation is one immediate write transaction that rolls back fully
  on any error.
* The repository never reads the clock; timestamps are supplied by callers.
* Evidence fidelity (implementation constraint): stored evidence text must
  occur verbatim in the text of the interaction it cites, on save and on load.

HumanAuthorization rows are records of a domain requirement. Storing one is
not proof that a human acted; later application layers must establish that.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Iterator

from baec_app.data.database import (
    INTERACTION_EVIDENCE_PROVENANCE,
    PersistenceError,
    PersistenceIntegrityError,
    RepositoryConflictError,
    RepositoryNotFoundError,
    RepositoryVerificationError,
    StorageRefusal,
    transaction,
)
from baec_app.data.records import (
    PersistedDormancyJudgment,
    RecordValidationError,
    SourceInteraction,
    TransitionHistoryEntry,
)
from baec_app.domain.baec_rules import classify_candidate
from baec_app.domain.enums import (
    AccountState,
    ArticulationOrigin,
    AuthorizationAction,
    BaecClassification,
    BaecCriterion,
    CriterionFinding,
    ElicitationMode,
    NoPlausiblePathGround,
    ProvenanceCategory,
    ReviewAnswer,
    StalenessStatus,
    ThresholdComparator,
    TransitionUnresolvedKind,
)
from baec_app.domain.models import (
    Account,
    AiDerivedText,
    BaecCandidate,
    BaecRecord,
    CriterionAssessment,
    DomainValidationError,
    DormancyJudgment,
    EvaluationEvidence,
    EvidenceExcerpt,
    HumanAuthorization,
    NonEvaluationEvidence,
    StringencyExpression,
)
from baec_app.domain.state_machine import (
    TransitionResult,
    transition_to_active_opportunity,
    transition_to_conditionally_dormant,
    transition_to_no_plausible_path,
)

# --- serialization ------------------------------------------------------------


def encode_datetime(value: datetime) -> str:
    """Timezone-aware datetime -> UTC ISO 8601 text with microseconds and offset."""
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise RecordValidationError("timestamps must be timezone-aware datetimes")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


def decode_datetime(text: str) -> datetime:
    if not isinstance(text, str):
        raise ValueError("stored timestamp is not text")
    value = datetime.fromisoformat(text)
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"stored timestamp {text!r} has no UTC offset")
    return value


def encode_decimal(value: Decimal | None) -> str | None:
    """Decimal -> text. No float is ever involved, so '10' and '10.0' stay distinct."""
    if value is None:
        return None
    if not isinstance(value, Decimal):
        raise RecordValidationError("thresholds must be Decimal values")
    return str(value)


def decode_decimal(text: str | None) -> Decimal | None:
    if text is None:
        return None
    if not isinstance(text, str):
        raise ValueError("stored threshold is not text")
    try:
        return Decimal(text)
    except InvalidOperation as error:
        raise ValueError(f"stored threshold {text!r} is not a decimal") from error


def _enum_value(member) -> str | None:
    return None if member is None else member.value


def _optional_enum(enum_class, text):
    return None if text is None else enum_class(text)


@contextmanager
def _fail_closed(what: str) -> Iterator[None]:
    """Turn any failure while rebuilding an object into PersistenceIntegrityError."""
    try:
        yield
    except (PersistenceIntegrityError, RepositoryNotFoundError):
        raise
    except (DomainValidationError, RecordValidationError, ValueError, TypeError, KeyError) as error:
        raise PersistenceIntegrityError(f"stored {what} is corrupt or contradictory: {error}") from error


def _positions_are_contiguous(positions: list[int]) -> bool:
    return positions == list(range(len(positions)))


def _check_new_confirmed_record(record: BaecRecord) -> None:
    """The checks every newly confirmed BAEC passes before it is written (unchanged from Phase 3)."""
    if not isinstance(record, BaecRecord):
        raise RepositoryVerificationError("save_confirmed_baec requires a BaecRecord")
    if record.classification is not BaecClassification.CONFIRMED_BAEC:
        raise RepositoryVerificationError(
            "only a CONFIRMED_BAEC record can be saved with save_confirmed_baec"
        )
    if record.staleness_status is not StalenessStatus.CURRENT:
        raise RepositoryVerificationError("a newly saved confirmed BAEC must be CURRENT")
    Repository._verify_against_rules(record)


def insert_confirmed_record(connection: sqlite3.Connection, record: BaecRecord) -> int:
    """Write one newly confirmed BAEC inside the caller's open transaction; return its authorization id.

    The narrow confirmed-BAEC write primitive (Phase 7F-B). It applies exactly the checks and inserts of
    Repository.save_confirmed_baec (the authorization row, verbatim evidence rows, the record, assessments, and
    stringency) and nothing else: no account state, transition, dormancy, staleness, or other write. It never
    opens, commits, or rolls back a transaction; the caller owns the one transaction that must contain it.
    """
    if not connection.in_transaction:
        raise PersistenceError("insert_confirmed_record must run inside the caller's open transaction")
    _check_new_confirmed_record(record)
    Repository(connection)._insert_record(record)
    return connection.execute(
        "SELECT confirmation_authorization_id FROM baec_records WHERE baec_id = ?", (record.baec_id,)
    ).fetchone()[0]


class Repository:
    """Reads and writes domain objects on one SQLite connection."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._db = connection

    @contextmanager
    def _write(self) -> Iterator[None]:
        try:
            with transaction(self._db):
                yield
        except (sqlite3.IntegrityError, StorageRefusal) as error:
            raise RepositoryConflictError(f"the database refused the write: {error}") from error

    def _insert_returning(self, sql: str, parameters: tuple) -> int:
        """Run one INSERT ... RETURNING <id> and return the id the database assigned.

        RETURNING (SQLite 3.35+, below the 3.38 minimum) rather than cursor.lastrowid, so the same statement reads
        the id back on every backend. The cursor is drained so the statement is complete before the next one.
        """
        return self._db.execute(sql, parameters).fetchall()[0][0]

    # --- accounts -------------------------------------------------------------

    def add_account(self, account: Account) -> None:
        """Insert a new, unclassified account. State changes only via transitions."""
        if not isinstance(account, Account):
            raise RepositoryVerificationError("add_account requires an Account")
        if account.state is not None:
            raise RepositoryVerificationError(
                "accounts are created with state None; state changes only through "
                "a persisted domain transition"
            )
        with self._write():
            if self._account_row(account.account_id) is not None:
                raise RepositoryConflictError(f"account {account.account_id!r} already exists")
            self._db.execute(
                "INSERT INTO accounts (account_id, name, state) VALUES (?, ?, NULL)",
                (account.account_id, account.name),
            )

    def _account_row(self, account_id: str):
        return self._db.execute(
            "SELECT account_id, name, state FROM accounts WHERE account_id = ?", (account_id,)
        ).fetchone()

    def get_account(self, account_id: str) -> Account:
        row = self._account_row(account_id)
        if row is None:
            raise RepositoryNotFoundError(f"account {account_id!r} does not exist")
        with _fail_closed(f"account {account_id!r}"):
            account = Account(row[0], row[1], _optional_enum(AccountState, row[2]))
            self._verify_state_matches_history(account)
        return account

    def _verify_state_matches_history(self, account: Account) -> None:
        rows = self._db.execute(
            "SELECT from_state, to_state FROM account_state_transitions "
            "WHERE account_id = ? ORDER BY transition_id",
            (account.account_id,),
        ).fetchall()
        expected_from = None
        for from_text, to_text in rows:
            if from_text != expected_from:
                raise ValueError("transition history is not an unbroken chain")
            expected_from = to_text
        if _enum_value(account.state) != expected_from:
            raise ValueError("account state does not match its transition history")

    def list_accounts(self) -> tuple[Account, ...]:
        ids = self._db.execute("SELECT account_id FROM accounts ORDER BY rowid").fetchall()
        return tuple(self.get_account(row[0]) for row in ids)

    # --- interactions ---------------------------------------------------------

    def add_interaction(self, interaction: SourceInteraction) -> None:
        if not isinstance(interaction, SourceInteraction):
            raise RepositoryVerificationError("add_interaction requires a SourceInteraction")
        with self._write():
            if self._account_row(interaction.account_id) is None:
                raise RepositoryNotFoundError(f"account {interaction.account_id!r} does not exist")
            if self._interaction_row(interaction.interaction_id) is not None:
                raise RepositoryConflictError(
                    f"interaction {interaction.interaction_id!r} already exists"
                )
            self._db.execute(
                "INSERT INTO interactions (interaction_id, account_id, occurred_at, text) "
                "VALUES (?, ?, ?, ?)",
                (
                    interaction.interaction_id,
                    interaction.account_id,
                    encode_datetime(interaction.occurred_at),
                    interaction.text,
                ),
            )

    def _interaction_row(self, interaction_id: str):
        return self._db.execute(
            "SELECT interaction_id, account_id, occurred_at, text FROM interactions "
            "WHERE interaction_id = ?",
            (interaction_id,),
        ).fetchone()

    def _require_interaction_row(self, interaction_id: str):
        row = self._interaction_row(interaction_id)
        if row is None:
            raise RepositoryNotFoundError(f"interaction {interaction_id!r} does not exist")
        return row

    def get_interaction(self, interaction_id: str) -> SourceInteraction:
        row = self._require_interaction_row(interaction_id)
        with _fail_closed(f"interaction {interaction_id!r}"):
            return SourceInteraction(row[0], row[1], decode_datetime(row[2]), row[3])

    def list_interactions(self, account_id: str) -> tuple[SourceInteraction, ...]:
        self.get_account(account_id)
        ids = self._db.execute(
            "SELECT interaction_id FROM interactions WHERE account_id = ? ORDER BY rowid",
            (account_id,),
        ).fetchall()
        return tuple(self.get_interaction(row[0]) for row in ids)

    # --- evidence rows --------------------------------------------------------

    def _evidence_id(self, excerpt: EvidenceExcerpt) -> int:
        """Get or create the row for an excerpt.

        EvidenceExcerpt has no identifier in the domain, so its persistence
        identity is (interaction, provenance, text). An excerpt that supports
        several criteria is stored once and reused.
        """
        if excerpt.provenance not in INTERACTION_EVIDENCE_PROVENANCE:
            raise RepositoryVerificationError(
                f"{excerpt.provenance.value} evidence cannot be stored as interaction evidence"
            )
        interaction = self._require_interaction_row(excerpt.source_id)
        if excerpt.text not in interaction[3]:
            raise RepositoryVerificationError(
                f"evidence text does not occur verbatim in interaction {excerpt.source_id!r}"
            )
        key = (excerpt.source_id, excerpt.provenance.value, excerpt.text)
        row = self._db.execute(
            "SELECT evidence_id FROM interaction_evidence "
            "WHERE interaction_id = ? AND provenance = ? AND text = ?",
            key,
        ).fetchone()
        if row is not None:
            return row[0]
        return self._insert_returning(
            "INSERT INTO interaction_evidence (interaction_id, provenance, text) VALUES (?, ?, ?) "
            "RETURNING evidence_id",
            key,
        )

    def _load_excerpt(self, evidence_id: int) -> EvidenceExcerpt:
        row = self._db.execute(
            "SELECT text, provenance, interaction_id FROM interaction_evidence WHERE evidence_id = ?",
            (evidence_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"evidence row {evidence_id} is missing")
        interaction = self._interaction_row(row[2])
        if interaction is None:
            raise ValueError(f"evidence row {evidence_id} cites a missing interaction")
        if not isinstance(row[0], str) or row[0] not in interaction[3]:
            raise ValueError(f"evidence row {evidence_id} does not occur verbatim in its interaction")
        return EvidenceExcerpt(row[0], ProvenanceCategory(row[1]), row[2])

    # --- authorizations -------------------------------------------------------

    def _insert_authorization(self, authorization: HumanAuthorization) -> int:
        return self._insert_returning(
            "INSERT INTO human_authorizations "
            "(authorized_by, authorized_at, action, subject_id, target_state) VALUES (?, ?, ?, ?, ?) "
            "RETURNING authorization_id",
            (
                authorization.authorized_by,
                encode_datetime(authorization.authorized_at),
                authorization.action.value,
                authorization.subject_id,
                _enum_value(authorization.target_state),
            ),
        )

    def _load_authorization(self, authorization_id: int) -> HumanAuthorization:
        row = self._db.execute(
            "SELECT authorized_by, authorized_at, action, subject_id, target_state "
            "FROM human_authorizations WHERE authorization_id = ?",
            (authorization_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"authorization row {authorization_id} is missing")
        return HumanAuthorization(
            row[0],
            decode_datetime(row[1]),
            AuthorizationAction(row[2]),
            row[3],
            _optional_enum(AccountState, row[4]),
        )

    # --- BAEC records ---------------------------------------------------------

    def save_classification_record(self, record: BaecRecord) -> None:
        """Save a NOT_BAEC or INSUFFICIENT_EVIDENCE record.

        The classification is re-run here; the record is refused unless its
        classification and reason text match the locked rules exactly.
        """
        if not isinstance(record, BaecRecord):
            raise RepositoryVerificationError("save_classification_record requires a BaecRecord")
        if record.classification is BaecClassification.CONFIRMED_BAEC:
            raise RepositoryVerificationError(
                "a confirmed BAEC must be saved with save_confirmed_baec"
            )
        self._verify_against_rules(record)
        with self._write():
            self._insert_record(record)

    def save_confirmed_baec(self, record: BaecRecord) -> None:
        """Save a newly confirmed BAEC. New confirmed records must be CURRENT."""
        _check_new_confirmed_record(record)
        with self._write():
            self._insert_record(record)

    @staticmethod
    def _verify_against_rules(record: BaecRecord) -> None:
        """Verification only: compare the record with what the locked rules compute."""
        result = classify_candidate(record.candidate)
        if result.classification is not record.classification:
            raise RepositoryVerificationError(
                f"record says {record.classification.value} but the rules compute "
                f"{result.classification.value}"
            )
        if result.reason_text != record.classification_reason:
            raise RepositoryVerificationError(
                "classification_reason does not equal the canonical reason text"
            )

    def _insert_record(self, record: BaecRecord) -> None:
        candidate = record.candidate
        if self._account_row(candidate.account_id) is None:
            raise RepositoryNotFoundError(f"account {candidate.account_id!r} does not exist")
        interaction = self._require_interaction_row(candidate.source_interaction_id)
        if interaction[1] != candidate.account_id:
            raise RepositoryVerificationError(
                "the source interaction belongs to a different account"
            )
        if self._record_exists(record.baec_id):
            raise RepositoryConflictError(f"BAEC record {record.baec_id!r} already exists")

        confirmation_id = None
        if record.confirmation is not None:
            confirmation_id = self._insert_authorization(record.confirmation)
        source_excerpt_id = self._evidence_id(candidate.source_excerpt)
        self._insert_record_row(record, source_excerpt_id, confirmation_id)
        self._insert_assessments(record)
        self._insert_stringency(record)

    def _record_exists(self, baec_id: str) -> bool:
        row = self._db.execute(
            "SELECT 1 FROM baec_records WHERE baec_id = ?", (baec_id,)
        ).fetchone()
        return row is not None

    def _insert_record_row(self, record: BaecRecord, source_excerpt_id: int, confirmation_id) -> None:
        candidate = record.candidate
        normalized = record.normalized_condition
        self._db.execute(
            "INSERT INTO baec_records (baec_id, account_id, source_interaction_id, "
            "source_excerpt_id, captured_at, buyer_role, buyer_exact_statement, "
            "articulation_origin, elicitation_mode, classification, classification_reason, "
            "staleness_status, confirmation_authorization_id, normalized_text, "
            "normalized_generated_at, normalized_model) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                record.baec_id,
                candidate.account_id,
                candidate.source_interaction_id,
                source_excerpt_id,
                encode_datetime(record.captured_at),
                candidate.buyer_role,
                candidate.buyer_exact_statement,
                candidate.articulation_origin.value,
                candidate.elicitation_mode.value,
                record.classification.value,
                record.classification_reason,
                _enum_value(record.staleness_status),
                confirmation_id,
                normalized.text if normalized else None,
                encode_datetime(normalized.generated_at) if normalized else None,
                normalized.model if normalized else None,
            ),
        )

    def _insert_assessments(self, record: BaecRecord) -> None:
        candidate = record.candidate
        for position, assessment in enumerate(candidate.assessments):
            self._db.execute(
                "INSERT INTO criterion_assessments (baec_id, criterion, position, finding, rationale) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    record.baec_id,
                    assessment.criterion.value,
                    position,
                    assessment.finding.value,
                    assessment.rationale,
                ),
            )
            for evidence_position, excerpt in enumerate(assessment.evidence):
                self._db.execute(
                    "INSERT INTO criterion_evidence "
                    "(baec_id, criterion, position, evidence_id, source_interaction_id) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        record.baec_id,
                        assessment.criterion.value,
                        evidence_position,
                        self._evidence_id(excerpt),
                        candidate.source_interaction_id,
                    ),
                )

    def _insert_stringency(self, record: BaecRecord) -> None:
        stringency = record.candidate.stringency
        if stringency is None:
            return
        self._db.execute(
            "INSERT INTO stringency_expressions (baec_id, verbatim_text, comparator, "
            "numeric_value, unit, qualitative_term, recurrence_text, timing_text) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                record.baec_id,
                stringency.verbatim_text,
                _enum_value(stringency.comparator),
                encode_decimal(stringency.numeric_value),
                stringency.unit,
                stringency.qualitative_term,
                stringency.recurrence_text,
                stringency.timing_text,
            ),
        )

    def get_baec_record(self, baec_id: str) -> BaecRecord:
        row = self._db.execute(
            "SELECT baec_id, account_id, source_interaction_id, source_excerpt_id, captured_at, "
            "buyer_role, buyer_exact_statement, articulation_origin, elicitation_mode, "
            "classification, classification_reason, staleness_status, "
            "confirmation_authorization_id, normalized_text, normalized_generated_at, "
            "normalized_model FROM baec_records WHERE baec_id = ?",
            (baec_id,),
        ).fetchone()
        if row is None:
            raise RepositoryNotFoundError(f"BAEC record {baec_id!r} does not exist")
        with _fail_closed(f"BAEC record {baec_id!r}"):
            return self._rehydrate_record(row)

    def _rehydrate_record(self, row) -> BaecRecord:
        """Rebuild a record through the locked constructors, in dependency order."""
        baec_id = row[0]
        # 0. the source interaction must exist and belong to the record's account
        interaction = self._interaction_row(row[2])
        if interaction is None:
            raise ValueError(f"source interaction {row[2]!r} is missing")
        if interaction[1] != row[1]:
            raise ValueError("the source interaction belongs to a different account")
        # 1. source excerpt
        source_excerpt = self._load_excerpt(row[3])
        # 2. assessments in stored order, each with its evidence in stored order
        assessment_rows = self._db.execute(
            "SELECT criterion, position, finding, rationale FROM criterion_assessments "
            "WHERE baec_id = ? ORDER BY position",
            (baec_id,),
        ).fetchall()
        if not _positions_are_contiguous([r[1] for r in assessment_rows]):
            raise ValueError("criterion assessment positions are not contiguous from 0")
        assessments = []
        for criterion_text, _, finding_text, rationale in assessment_rows:
            evidence_rows = self._db.execute(
                "SELECT position, evidence_id FROM criterion_evidence "
                "WHERE baec_id = ? AND criterion = ? ORDER BY position",
                (baec_id, criterion_text),
            ).fetchall()
            if not _positions_are_contiguous([r[0] for r in evidence_rows]):
                raise ValueError("criterion evidence positions are not contiguous from 0")
            assessments.append(
                CriterionAssessment(
                    BaecCriterion(criterion_text),
                    CriterionFinding(finding_text),
                    tuple(self._load_excerpt(r[1]) for r in evidence_rows),
                    rationale,
                )
            )
        # 3. stringency
        stringency = self._load_stringency(baec_id)
        # 4. candidate
        candidate = BaecCandidate(
            account_id=row[1],
            source_interaction_id=row[2],
            source_excerpt=source_excerpt,
            assessments=tuple(assessments),
            articulation_origin=ArticulationOrigin(row[7]),
            elicitation_mode=ElicitationMode(row[8]),
            buyer_exact_statement=row[6],
            buyer_role=row[5],
            stringency=stringency,
        )
        # 5. confirmation
        confirmation = None if row[12] is None else self._load_authorization(row[12])
        # 6. AI-derived text: all absent, or text with a timestamp
        normalized = None
        if row[13] is None:
            if row[14] is not None or row[15] is not None:
                raise ValueError("normalized fields are present without normalized text")
        else:
            if row[14] is None:
                raise ValueError("normalized text has no generated timestamp")
            normalized = AiDerivedText(row[13], decode_datetime(row[14]), row[15])
        # 7. record
        record = BaecRecord(
            baec_id=baec_id,
            captured_at=decode_datetime(row[4]),
            candidate=candidate,
            classification=BaecClassification(row[9]),
            classification_reason=row[10],
            normalized_condition=normalized,
            staleness_status=_optional_enum(StalenessStatus, row[11]),
            confirmation=confirmation,
        )
        # 8. the stored classification and reason must still match the locked rules
        try:
            self._verify_against_rules(record)
        except RepositoryVerificationError as error:
            raise ValueError(str(error)) from error
        return record

    def _load_stringency(self, baec_id: str) -> StringencyExpression | None:
        row = self._db.execute(
            "SELECT verbatim_text, comparator, numeric_value, unit, qualitative_term, "
            "recurrence_text, timing_text FROM stringency_expressions WHERE baec_id = ?",
            (baec_id,),
        ).fetchone()
        if row is None:
            return None
        return StringencyExpression(
            verbatim_text=row[0],
            comparator=_optional_enum(ThresholdComparator, row[1]),
            numeric_value=decode_decimal(row[2]),
            unit=row[3],
            qualitative_term=row[4],
            recurrence_text=row[5],
            timing_text=row[6],
        )

    def list_baec_records(self, account_id: str) -> tuple[BaecRecord, ...]:
        self.get_account(account_id)
        ids = self._db.execute(
            "SELECT baec_id FROM baec_records WHERE account_id = ? ORDER BY rowid", (account_id,)
        ).fetchall()
        return tuple(self.get_baec_record(row[0]) for row in ids)

    # --- dormancy judgments ---------------------------------------------------

    def record_dormancy_judgment(self, judgment: DormancyJudgment) -> int:
        if not isinstance(judgment, DormancyJudgment):
            raise RepositoryVerificationError("record_dormancy_judgment requires a DormancyJudgment")
        with self._write():
            if not self._record_exists(judgment.baec_id):
                raise RepositoryNotFoundError(f"BAEC record {judgment.baec_id!r} does not exist")
            authorization_id = self._insert_authorization(judgment.authorization)
            return self._insert_judgment_row(judgment, authorization_id)

    def _insert_judgment_row(self, judgment: DormancyJudgment, authorization_id: int) -> int:
        return self._insert_returning(
            "INSERT INTO dormancy_judgments "
            "(baec_id, plausibility, addressability, notes, authorization_id) VALUES (?, ?, ?, ?, ?) "
            "RETURNING judgment_id",
            (
                judgment.baec_id,
                judgment.plausibility.value,
                judgment.addressability.value,
                judgment.notes,
                authorization_id,
            ),
        )

    def _load_judgment(self, judgment_id: int) -> DormancyJudgment:
        row = self._db.execute(
            "SELECT baec_id, plausibility, addressability, notes, authorization_id "
            "FROM dormancy_judgments WHERE judgment_id = ?",
            (judgment_id,),
        ).fetchone()
        if row is None:
            raise RepositoryNotFoundError(f"dormancy judgment {judgment_id!r} does not exist")
        with _fail_closed(f"dormancy judgment {judgment_id!r}"):
            return DormancyJudgment(
                baec_id=row[0],
                plausibility=ReviewAnswer(row[1]),
                addressability=ReviewAnswer(row[2]),
                authorization=self._load_authorization(row[4]),
                notes=row[3],
            )

    def list_dormancy_judgments(self, baec_id: str) -> tuple[PersistedDormancyJudgment, ...]:
        if not self._record_exists(baec_id):
            raise RepositoryNotFoundError(f"BAEC record {baec_id!r} does not exist")
        ids = self._db.execute(
            "SELECT judgment_id FROM dormancy_judgments WHERE baec_id = ? ORDER BY judgment_id",
            (baec_id,),
        ).fetchall()
        return tuple(PersistedDormancyJudgment(row[0], self._load_judgment(row[0])) for row in ids)

    # --- evaluation-state evidence --------------------------------------------

    def _require_evidence_interaction(self, evidence) -> str:
        """Supplied evidence must point at an already-stored interaction.

        Returns the account that interaction belongs to. An interaction is
        never created from evidence.
        """
        return self._require_interaction_row(evidence.evidence.source_id)[1]

    @staticmethod
    def _require_same_account(interaction_account: str | None, account_id: str) -> None:
        if interaction_account is not None and interaction_account != account_id:
            raise RepositoryVerificationError(
                "the supplied evidence comes from an interaction of a different account"
            )

    def _insert_state_evidence(self, table: str, evidence) -> int:
        evidence_id = self._evidence_id(evidence.evidence)
        return self._insert_returning(
            f"INSERT INTO {table} (account_id, interaction_id, evidence_id, observed_at) "
            f"VALUES (?, ?, ?, ?) RETURNING {table}_id",
            (
                evidence.account_id,
                evidence.evidence.source_id,
                evidence_id,
                encode_datetime(evidence.observed_at),
            ),
        )

    def _load_state_evidence(self, table: str, key: str, row_id: int, cls):
        row = self._db.execute(
            f"SELECT account_id, interaction_id, evidence_id, observed_at FROM {table} "
            f"WHERE {key} = ?",
            (row_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"{table} row {row_id} is missing")
        excerpt = self._load_excerpt(row[2])
        if excerpt.source_id != row[1]:
            raise ValueError(f"{table} row {row_id} points at evidence from another interaction")
        return cls(row[0], excerpt, decode_datetime(row[3]))

    # --- transitions ----------------------------------------------------------

    def persist_transition_to_conditionally_dormant(
        self,
        account_id: str,
        *,
        baec_id: str | None,
        judgment_id: int | None,
        authorization: HumanAuthorization | None,
        non_evaluation_evidence: NonEvaluationEvidence | None = None,
        recorded_at: datetime,
    ) -> TransitionResult:
        """Re-run the locked state machine on stored data; persist only if allowed."""
        recorded = encode_datetime(recorded_at)
        with self._write():
            account = self.get_account(account_id)
            record = None if baec_id is None else self.get_baec_record(baec_id)
            judgment = None if judgment_id is None else self._load_judgment(judgment_id)
            evidence_account = None
            if isinstance(non_evaluation_evidence, NonEvaluationEvidence):
                evidence_account = self._require_evidence_interaction(non_evaluation_evidence)

            result = transition_to_conditionally_dormant(
                account,
                baec_record=record,
                judgment=judgment,
                authorization=authorization,
                non_evaluation_evidence=non_evaluation_evidence,
            )
            if not result.allowed:
                return result
            self._require_same_account(evidence_account, account_id)

            self._insert_history_row(
                result,
                authorization,
                recorded,
                baec_id=baec_id,
                judgment_id=judgment_id,
                non_evaluation_evidence=non_evaluation_evidence,
            )
            self._update_account_state(result)
            return result

    def persist_transition_to_active_opportunity(
        self,
        account_id: str,
        *,
        evaluation_evidence: EvaluationEvidence | None,
        authorization: HumanAuthorization | None,
        recorded_at: datetime,
    ) -> TransitionResult:
        """Re-run the locked state machine on stored data; persist only if allowed."""
        recorded = encode_datetime(recorded_at)
        with self._write():
            account = self.get_account(account_id)
            evidence_account = None
            if isinstance(evaluation_evidence, EvaluationEvidence):
                evidence_account = self._require_evidence_interaction(evaluation_evidence)

            result = transition_to_active_opportunity(
                account, evaluation_evidence=evaluation_evidence, authorization=authorization
            )
            if not result.allowed:
                return result
            self._require_same_account(evidence_account, account_id)

            self._insert_history_row(
                result, authorization, recorded, evaluation_evidence=evaluation_evidence
            )
            self._update_account_state(result)
            return result

    def persist_transition_to_no_plausible_path(
        self,
        account_id: str,
        *,
        ground: NoPlausiblePathGround | None,
        reason: str | None,
        authorization: HumanAuthorization | None,
        non_evaluation_evidence: NonEvaluationEvidence | None = None,
        basis_interaction_id: str | None = None,
        recorded_at: datetime,
    ) -> TransitionResult:
        """Re-run the locked state machine on stored data; persist only if allowed."""
        recorded = encode_datetime(recorded_at)
        with self._write():
            account = self.get_account(account_id)
            evidence_account = None
            if isinstance(non_evaluation_evidence, NonEvaluationEvidence):
                evidence_account = self._require_evidence_interaction(non_evaluation_evidence)
            if basis_interaction_id is not None:
                basis = self._require_interaction_row(basis_interaction_id)
                if basis[1] != account_id:
                    raise RepositoryVerificationError(
                        "the basis interaction belongs to a different account"
                    )

            result = transition_to_no_plausible_path(
                account,
                ground=ground,
                reason=reason,
                authorization=authorization,
                non_evaluation_evidence=non_evaluation_evidence,
            )
            if not result.allowed:
                return result
            self._require_same_account(evidence_account, account_id)

            self._insert_history_row(
                result,
                authorization,
                recorded,
                non_evaluation_evidence=non_evaluation_evidence,
                basis_interaction_id=basis_interaction_id,
            )
            self._update_account_state(result)
            return result

    def _insert_history_row(
        self,
        result: TransitionResult,
        authorization: HumanAuthorization,
        recorded: str,
        *,
        baec_id: str | None = None,
        judgment_id: int | None = None,
        evaluation_evidence: EvaluationEvidence | None = None,
        non_evaluation_evidence: NonEvaluationEvidence | None = None,
        basis_interaction_id: str | None = None,
    ) -> int:
        authorization_id = self._insert_authorization(authorization)
        evaluation_id = (
            None
            if evaluation_evidence is None
            else self._insert_state_evidence("evaluation_evidence", evaluation_evidence)
        )
        non_evaluation_id = (
            None
            if non_evaluation_evidence is None
            else self._insert_state_evidence("non_evaluation_evidence", non_evaluation_evidence)
        )
        return self._insert_returning(
            "INSERT INTO account_state_transitions (account_id, from_state, to_state, "
            "authorization_id, baec_id, judgment_id, evaluation_evidence_id, "
            "non_evaluation_evidence_id, ground, reason, basis_interaction_id, unresolved, "
            "recorded_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING transition_id",
            (
                result.account_id,
                _enum_value(result.from_state),
                result.to_state.value,
                authorization_id,
                baec_id,
                judgment_id,
                evaluation_id,
                non_evaluation_id,
                _enum_value(result.ground),
                result.reason,
                basis_interaction_id,
                json.dumps([kind.value for kind in result.unresolved]),
                recorded,
            ),
        )

    def _update_account_state(self, result: TransitionResult) -> None:
        """Compare-and-set: succeeds only if the stored state is still from_state."""
        cursor = self._db.execute(
            "UPDATE accounts SET state = ? WHERE account_id = ? AND state IS ?",
            (result.to_state.value, result.account_id, _enum_value(result.from_state)),
        )
        if cursor.rowcount != 1:
            raise RepositoryConflictError(
                f"account {result.account_id!r} changed state during the transition"
            )

    def get_transition_history(self, account_id: str) -> tuple[TransitionHistoryEntry, ...]:
        self.get_account(account_id)  # also checks state against the history chain
        rows = self._db.execute(
            "SELECT transition_id, account_id, from_state, to_state, authorization_id, baec_id, "
            "judgment_id, evaluation_evidence_id, non_evaluation_evidence_id, ground, reason, "
            "basis_interaction_id, unresolved, recorded_at FROM account_state_transitions "
            "WHERE account_id = ? ORDER BY transition_id",
            (account_id,),
        ).fetchall()
        entries = []
        for row in rows:
            with _fail_closed(f"transition {row[0]!r}"):
                entries.append(self._rehydrate_history_entry(row))
        return tuple(entries)

    def _rehydrate_history_entry(self, row) -> TransitionHistoryEntry:
        unresolved_names = json.loads(row[12])
        if not isinstance(unresolved_names, list):
            raise ValueError("stored unresolved value is not a list")
        entry = self._build_history_entry(row, unresolved_names)
        if entry.to_state is AccountState.CONDITIONALLY_DORMANT:
            self._verify_dormant_history_entry(entry)
        return entry

    def _verify_dormant_history_entry(self, entry: TransitionHistoryEntry) -> None:
        """A stored dormancy transition must still agree with what it references.

        These checks hold even if the database constraints were bypassed. The
        referenced BAEC is deliberately NOT required to still be CURRENT: a
        later lifecycle phase may legitimately mark a BAEC stale without
        invalidating the historical transition.
        """
        try:
            record = self.get_baec_record(entry.baec_id)
            judgment = self._load_judgment(entry.judgment_id)
        except RepositoryNotFoundError as error:
            raise ValueError(f"dormant transition references a missing row: {error}") from error
        if record.candidate.account_id != entry.account_id:
            raise ValueError("dormant transition references a BAEC of a different account")
        if record.classification is not BaecClassification.CONFIRMED_BAEC:
            raise ValueError("dormant transition references a record that is not a confirmed BAEC")
        if judgment.baec_id != entry.baec_id:
            raise ValueError("dormant transition references a judgment for a different BAEC")
        if judgment.plausibility is not ReviewAnswer.YES:
            raise ValueError("dormant transition references a judgment whose plausibility is not YES")
        if judgment.addressability not in (ReviewAnswer.YES, ReviewAnswer.UNKNOWN):
            raise ValueError(
                "dormant transition references a judgment whose addressability blocks dormancy"
            )
        expected_unresolved = (
            (TransitionUnresolvedKind.ADDRESSABILITY_UNKNOWN,)
            if judgment.addressability is ReviewAnswer.UNKNOWN
            else ()
        )
        if entry.unresolved != expected_unresolved:
            raise ValueError("stored unresolved items disagree with the judgment's addressability")

    def _build_history_entry(self, row, unresolved_names: list) -> TransitionHistoryEntry:
        return TransitionHistoryEntry(
            transition_id=row[0],
            account_id=row[1],
            from_state=_optional_enum(AccountState, row[2]),
            to_state=AccountState(row[3]),
            authorization=self._load_authorization(row[4]),
            recorded_at=decode_datetime(row[13]),
            baec_id=row[5],
            judgment_id=row[6],
            evaluation_evidence=(
                None
                if row[7] is None
                else self._load_state_evidence(
                    "evaluation_evidence", "evaluation_evidence_id", row[7], EvaluationEvidence
                )
            ),
            non_evaluation_evidence=(
                None
                if row[8] is None
                else self._load_state_evidence(
                    "non_evaluation_evidence",
                    "non_evaluation_evidence_id",
                    row[8],
                    NonEvaluationEvidence,
                )
            ),
            ground=_optional_enum(NoPlausiblePathGround, row[9]),
            reason=row[10],
            basis_interaction_id=row[11],
            unresolved=tuple(TransitionUnresolvedKind(name) for name in unresolved_names),
        )
