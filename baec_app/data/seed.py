"""Builds the synthetic demo database from the JSON seed files.

Lifecycle: JSON inputs -> locked domain constructors and factories ->
verified repository writes -> expected outcomes checked.

The JSON files hold inputs and expectations only. They never assert an
outcome: every classification comes from classify_candidate and every
account state comes from a real state-machine transition persisted by the
repository. If a result disagrees with the stated expectation, seeding
fails.

All seed data is fictional. Seed authorizations are fixtures marked
"synthetic-seed-fixture"; they do not record any real human action. The
seed contains no AI-generated content.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from baec_app.data.database import (
    PersistenceError,
    make_read_only,
    open_database,
    transaction,
)
from baec_app.data.records import SourceInteraction
from baec_app.data.repository import Repository
from baec_app.domain.baec_rules import (
    classify_candidate,
    create_confirmed_baec_record,
    create_nonconfirmed_classification_record,
)
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
    ThresholdComparator,
)
from baec_app.domain.models import (
    Account,
    BaecCandidate,
    CriterionAssessment,
    DormancyJudgment,
    EvaluationEvidence,
    EvidenceExcerpt,
    HumanAuthorization,
    StringencyExpression,
)

DEFAULT_SEED_DIRECTORY = Path(__file__).resolve().parents[2] / "data" / "demo"
SEED_FILES = ("accounts.json", "interactions.json", "baecs.json", "account_events.json")
SEED_AUTHORIZER = "synthetic-seed-fixture"


class SeedError(PersistenceError):
    """The seed files are inconsistent with the domain rules or their own expectations."""


def load_seed_inputs(directory: Path | str = DEFAULT_SEED_DIRECTORY) -> dict:
    directory = Path(directory)
    inputs = {}
    for name in SEED_FILES:
        with open(directory / name, encoding="utf-8") as handle:
            inputs[name.removesuffix(".json")] = json.load(handle)
    return inputs


def _time(text: str) -> datetime:
    return datetime.fromisoformat(text)


def _authorization(spec: dict, action: AuthorizationAction, subject_id: str, target=None):
    if spec["authorized_by"] != SEED_AUTHORIZER:
        raise SeedError(f"seed authorizations must be marked {SEED_AUTHORIZER!r}")
    return HumanAuthorization(
        spec["authorized_by"], _time(spec["authorized_at"]), action, subject_id, target
    )


def _excerpt(spec: dict, interaction: SourceInteraction) -> EvidenceExcerpt:
    if spec["text"] not in interaction.text:
        raise SeedError(
            f"seed evidence does not appear verbatim in {interaction.interaction_id}: {spec['text']!r}"
        )
    return EvidenceExcerpt(
        spec["text"], ProvenanceCategory(spec["provenance"]), interaction.interaction_id
    )


def _stringency(spec: dict | None) -> StringencyExpression | None:
    if spec is None:
        return None
    return StringencyExpression(
        verbatim_text=spec["verbatim_text"],
        comparator=None if spec["comparator"] is None else ThresholdComparator(spec["comparator"]),
        # The number is kept as text in JSON so it never passes through a float.
        numeric_value=None if spec["numeric_value"] is None else Decimal(spec["numeric_value"]),
        unit=spec["unit"],
        qualitative_term=spec["qualitative_term"],
        recurrence_text=spec["recurrence_text"],
        timing_text=spec["timing_text"],
    )


def _save_baec(repository: Repository, spec: dict) -> None:
    interaction = repository.get_interaction(spec["source_interaction_id"])
    candidate = BaecCandidate(
        account_id=spec["account_id"],
        source_interaction_id=spec["source_interaction_id"],
        source_excerpt=_excerpt(spec["source_excerpt"], interaction),
        assessments=tuple(
            CriterionAssessment(
                BaecCriterion(item["criterion"]),
                CriterionFinding(item["finding"]),
                tuple(_excerpt(e, interaction) for e in item["evidence"]),
            )
            for item in spec["assessments"]
        ),
        articulation_origin=ArticulationOrigin(spec["articulation_origin"]),
        elicitation_mode=ElicitationMode(spec["elicitation_mode"]),
        buyer_exact_statement=spec["buyer_exact_statement"],
        buyer_role=spec["buyer_role"],
        stringency=_stringency(spec["stringency"]),
    )
    result = classify_candidate(candidate)
    if (
        result.classification.value != spec["expected_classification"]
        or result.reason_text != spec["expected_reason"]
    ):
        raise SeedError(
            f"{spec['baec_id']}: the rules computed {result.classification.value} "
            f"({result.reason_text!r}), not the expected {spec['expected_classification']}"
        )
    captured_at = _time(spec["captured_at"])
    if result.classification is BaecClassification.CONFIRMED_BAEC:
        confirmation = _authorization(
            spec["confirmation"], AuthorizationAction.CONFIRM_BAEC, spec["baec_id"]
        )
        repository.save_confirmed_baec(
            create_confirmed_baec_record(
                candidate, baec_id=spec["baec_id"], captured_at=captured_at, confirmation=confirmation
            )
        )
    else:
        if spec["confirmation"] is not None:
            raise SeedError(f"{spec['baec_id']}: a non-confirmed record cannot have a confirmation")
        repository.save_classification_record(
            create_nonconfirmed_classification_record(
                candidate, baec_id=spec["baec_id"], captured_at=captured_at
            )
        )


def _record_judgment(repository: Repository, spec: dict) -> int:
    judgment = DormancyJudgment(
        baec_id=spec["baec_id"],
        plausibility=ReviewAnswer(spec["plausibility"]),
        addressability=ReviewAnswer(spec["addressability"]),
        authorization=_authorization(
            spec["authorization"], AuthorizationAction.RECORD_DORMANCY_JUDGMENT, spec["baec_id"]
        ),
        notes=spec["notes"],
    )
    return repository.record_dormancy_judgment(judgment)


def _apply_transition(repository: Repository, spec: dict, judgment_ids: dict[str, int]) -> None:
    account_id = spec["account_id"]
    to_state = AccountState(spec["to_state"])
    authorization = _authorization(
        spec["authorization"], AuthorizationAction.CHANGE_ACCOUNT_STATE, account_id, to_state
    )
    recorded_at = _time(spec["recorded_at"])

    if to_state is AccountState.CONDITIONALLY_DORMANT:
        result = repository.persist_transition_to_conditionally_dormant(
            account_id,
            baec_id=spec["baec_id"],
            judgment_id=judgment_ids[spec["judgment_key"]],
            authorization=authorization,
            recorded_at=recorded_at,
        )
    elif to_state is AccountState.ACTIVE_OPPORTUNITY:
        evidence_spec = spec["evaluation_evidence"]
        interaction = repository.get_interaction(evidence_spec["interaction_id"])
        evidence = EvaluationEvidence(
            account_id, _excerpt(evidence_spec, interaction), _time(evidence_spec["observed_at"])
        )
        result = repository.persist_transition_to_active_opportunity(
            account_id,
            evaluation_evidence=evidence,
            authorization=authorization,
            recorded_at=recorded_at,
        )
    else:
        result = repository.persist_transition_to_no_plausible_path(
            account_id,
            ground=NoPlausiblePathGround(spec["ground"]),
            reason=spec["reason"],
            authorization=authorization,
            basis_interaction_id=spec.get("basis_interaction_id"),
            recorded_at=recorded_at,
        )
    if not result.allowed:
        raise SeedError(f"seed transition for {account_id} was rejected: {result.rejection_text}")


def seed_database(
    connection: sqlite3.Connection, directory: Path | str = DEFAULT_SEED_DIRECTORY
) -> None:
    """Seed an empty, schema-initialized database. Refuses a database that has accounts."""
    if connection.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] != 0:
        raise SeedError("the database already contains accounts; seed only an empty database")
    inputs = load_seed_inputs(directory)
    repository = Repository(connection)

    # Accounts are always inserted unclassified.
    for spec in inputs["accounts"]["accounts"]:
        repository.add_account(Account(spec["account_id"], spec["name"]))
    for spec in inputs["interactions"]["interactions"]:
        repository.add_interaction(
            SourceInteraction(
                spec["interaction_id"], spec["account_id"], _time(spec["occurred_at"]), spec["text"]
            )
        )

    baec_specs = {spec["baec_id"]: spec for spec in inputs["baecs"]["baecs"]}
    saved = set()
    judgment_ids: dict[str, int] = {}
    for event in inputs["account_events"]["events"]:
        if event["type"] == "save_baec":
            _save_baec(repository, baec_specs[event["baec_id"]])
            saved.add(event["baec_id"])
        elif event["type"] == "dormancy_judgment":
            judgment_ids[event["key"]] = _record_judgment(repository, event)
        elif event["type"] == "transition":
            _apply_transition(repository, event, judgment_ids)
        else:
            raise SeedError(f"unknown seed event type {event['type']!r}")
    if saved != set(baec_specs):
        raise SeedError("every BAEC in baecs.json must be saved by exactly one save_baec event")

    # Final states must be the ones the seed says it expects.
    for spec in inputs["accounts"]["accounts"]:
        state = repository.get_account(spec["account_id"]).state
        if state is None or state.value != spec["expected_final_state"]:
            raise SeedError(
                f"{spec['account_id']} ended in {state} instead of {spec['expected_final_state']}"
            )

    with transaction(connection):
        connection.executemany(
            "INSERT INTO schema_meta (key, value) VALUES (?, ?)",
            [
                ("seed_version", inputs["accounts"]["seed_version"]),
                ("data_policy", inputs["accounts"]["data_policy"]),
            ],
        )


def build_seed_database(
    path: str = ":memory:", directory: Path | str = DEFAULT_SEED_DIRECTORY
) -> sqlite3.Connection:
    """Create and seed a new database. The caller must close the returned connection."""
    connection = open_database(path)
    try:
        seed_database(connection, directory)
    except BaseException:
        connection.close()
        raise
    return connection


def build_canonical_seed_database(
    directory: Path | str = DEFAULT_SEED_DIRECTORY,
) -> sqlite3.Connection:
    """The canonical synthetic seed, in memory and read-only.

    Session databases are made from it with database.create_working_copy.
    The caller must close the returned connection.
    """
    connection = build_seed_database(":memory:", directory)
    make_read_only(connection)
    return connection
