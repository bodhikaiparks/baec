"""The synthetic seed dataset and the canonical / working database split (Phase 3)."""

import json
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from baec_app.data.database import create_working_copy, open_database, table_counts
from baec_app.data.repository import Repository
from baec_app.data.seed import (
    DEFAULT_SEED_DIRECTORY,
    SEED_AUTHORIZER,
    SEED_FILES,
    SeedError,
    build_canonical_seed_database,
    build_seed_database,
    load_seed_inputs,
    seed_database,
)
from baec_app.domain.enums import (
    AccountState,
    ArticulationOrigin,
    BaecClassification,
    BaecCriterion,
    CriterionFinding,
    ElicitationMode,
    NoPlausiblePathGround,
    ProvenanceCategory,
    ReviewAnswer,
    StalenessStatus,
    ThresholdComparator,
)
from baec_app.domain.models import Account
from tests.builders import AO, CD, NP
from tests.persistence_builders import dump, no_leaked_connections  # noqa: F401

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
HARBOR_STATEMENT = (
    "If our supplier raises pricing by more than 10% when our agreement renews, we'd evaluate other options."
)
EXPECTED_COUNTS = {
    "accounts": 3,
    "interactions": 3,
    "interaction_evidence": 4,
    "human_authorizations": 5,
    "baec_records": 2,
    "criterion_assessments": 8,
    "criterion_evidence": 8,
    "stringency_expressions": 1,
    "dormancy_judgments": 1,
    "evaluation_evidence": 1,
    "non_evaluation_evidence": 0,
    "account_state_transitions": 3,
    # Schema version 5: the AI provenance tables start empty in the canonical seed.
    "ai_runs": 0,
    "ai_run_results": 0,
    "ai_run_outputs": 0,
    "ai_artifacts": 0,
    "ai_artifact_excerpts": 0,
    # Schema version 7: the Phase 7 bridge tables start empty in the canonical seed.
    "ai_proposals": 0,
    "ai_proposal_review_revisions": 0,
    "ai_proposal_review_decisions": 0,
    "human_authorization_grants": 0,
    "human_authorization_grant_supersessions": 0,
    "ai_proposal_confirmations": 0,
    "human_authorization_grant_consumptions": 0,
}


@pytest.fixture
def seeded():
    connection = build_seed_database()
    yield connection
    connection.close()


@pytest.fixture
def seeded_repo(seeded):
    return Repository(seeded)


@pytest.fixture
def seed_copy(tmp_path):
    """A private, editable copy of the seed files."""
    directory = tmp_path / "seed"
    shutil.copytree(DEFAULT_SEED_DIRECTORY, directory)
    return directory


def edit(directory, file_name, change):
    path = directory / file_name
    data = json.loads(path.read_text(encoding="utf-8"))
    change(data)
    path.write_text(json.dumps(data), encoding="utf-8")


# --- contents -----------------------------------------------------------------


def test_seed_row_counts(seeded):
    assert table_counts(seeded) == EXPECTED_COUNTS


def test_rc20_three_demo_accounts_end_in_their_expected_states(seeded_repo):
    states = {a.name: a.state for a in seeded_repo.list_accounts()}
    assert states == {
        "Harbor Surgical Center": AccountState.CONDITIONALLY_DORMANT,
        "Summit Manufacturing": AccountState.NO_PLAUSIBLE_PATH,
        "Meridian Clinical Systems": AccountState.ACTIVE_OPPORTUNITY,
    }


def test_rc21_every_seed_state_came_from_a_real_transition_out_of_unclassified(seeded_repo):
    for account in seeded_repo.list_accounts():
        history = seeded_repo.get_transition_history(account.account_id)
        assert len(history) == 1
        assert history[0].from_state is None
        assert history[0].to_state is account.state


def test_harbor_confirmed_baec(seeded_repo):
    record = seeded_repo.get_baec_record("BAEC-HARBOR-001")
    candidate = record.candidate
    assert record.classification is BaecClassification.CONFIRMED_BAEC
    assert record.staleness_status is StalenessStatus.CURRENT
    assert record.classification_reason is None
    assert candidate.buyer_exact_statement == HARBOR_STATEMENT
    assert candidate.source_excerpt.text == HARBOR_STATEMENT
    assert candidate.buyer_role == "Materials Manager"
    assert candidate.articulation_origin is ArticulationOrigin.BUYER_GENERATED
    assert candidate.elicitation_mode is ElicitationMode.CEE_ELICITED
    assert [a.finding for a in candidate.assessments] == [CriterionFinding.MET] * 4
    evidence = {a.criterion: [e.text for e in a.evidence] for a in candidate.assessments}
    assert evidence[BaecCriterion.PRESENT_NON_EVALUATION] == ["We're not looking at other suppliers right now."]
    for criterion in (BaecCriterion.PROSPECTIVE_CONDITION, BaecCriterion.BUYER_ARTICULATION, BaecCriterion.EVALUATION_LINKAGE):
        assert evidence[criterion] == [HARBOR_STATEMENT]


def test_rc18_harbor_threshold_is_preserved_exactly(seeded_repo, seeded):
    stringency = seeded_repo.get_baec_record("BAEC-HARBOR-001").candidate.stringency
    assert stringency.comparator is ThresholdComparator.GREATER_THAN
    assert stringency.comparator is not ThresholdComparator.AT_LEAST
    assert str(stringency.numeric_value) == "10"
    assert stringency.unit == "%"
    assert stringency.verbatim_text == "more than 10% when our agreement renews"
    assert stringency.timing_text == "when our agreement renews"
    assert seeded.execute("SELECT numeric_value, typeof(numeric_value) FROM stringency_expressions").fetchall() == [
        ("10", "text")
    ]


def test_harbor_judgment_and_dormancy_transition(seeded_repo):
    (stored,) = seeded_repo.list_dormancy_judgments("BAEC-HARBOR-001")
    assert stored.judgment.plausibility is ReviewAnswer.YES
    assert stored.judgment.addressability is ReviewAnswer.YES
    (entry,) = seeded_repo.get_transition_history("ACC-HARBOR")
    assert entry.to_state is CD
    assert entry.baec_id == "BAEC-HARBOR-001"
    assert entry.judgment_id == stored.judgment_id
    assert entry.unresolved == ()


def test_harbor_seller_cee_question_lives_only_in_the_interaction_text(seeded_repo, seeded):
    question = "What, if anything, would have to change for that to become worthwhile?"
    assert question in seeded_repo.get_interaction("INT-HARBOR-001").text
    assert seeded.execute("SELECT COUNT(*) FROM interaction_evidence WHERE text LIKE '%would have to change%'").fetchone()[0] == 0
    assert seeded.execute("SELECT COUNT(*) FROM interaction_evidence WHERE provenance = 'SELLER_OBSERVATION'").fetchone()[0] == 0


def test_rc23_summit_has_no_baec_and_a_structured_no_plausible_path_decision(seeded_repo):
    assert seeded_repo.list_baec_records("ACC-SUMMIT") == ()
    (entry,) = seeded_repo.get_transition_history("ACC-SUMMIT")
    assert entry.to_state is NP
    assert entry.ground is NoPlausiblePathGround.NO_PLAUSIBLE_BAEC
    assert entry.basis_interaction_id == "INT-SUMMIT-001"
    assert entry.reason == (
        "After a CEE question, the buyer identified no prospective condition that would make evaluation worthwhile."
    )


def test_rc27_meridian_is_active_because_of_buyer_evaluation_evidence(seeded_repo):
    (entry,) = seeded_repo.get_transition_history("ACC-MERIDIAN")
    assert entry.to_state is AO
    assert entry.evaluation_evidence.evidence.provenance is ProvenanceCategory.BUYER_FACT
    assert entry.evaluation_evidence.evidence.text == (
        "We opened a formal supplier review last month and are comparing three vendors."
    )
    assert entry.baec_id is None


def test_rc13_meridian_negative_record_is_preserved_and_does_not_drive_state(seeded_repo):
    record = seeded_repo.get_baec_record("BAEC-MERIDIAN-001")
    findings = {a.criterion: a.finding for a in record.candidate.assessments}
    assert record.classification is BaecClassification.NOT_BAEC
    assert record.classification_reason == (
        "NOT_BAEC: criterion not met: PRESENT_NON_EVALUATION; criterion not met: EVALUATION_LINKAGE."
    )
    assert findings[BaecCriterion.PRESENT_NON_EVALUATION] is CriterionFinding.NOT_MET
    assert findings[BaecCriterion.EVALUATION_LINKAGE] is CriterionFinding.NOT_MET
    assert findings[BaecCriterion.PROSPECTIVE_CONDITION] is CriterionFinding.MET
    assert findings[BaecCriterion.BUYER_ARTICULATION] is CriterionFinding.MET
    assert record.candidate.stringency is None
    assert "can't hold pricing" in record.candidate.buyer_exact_statement
    assert record.confirmation is None and record.staleness_status is None
    assert record.candidate.elicitation_mode is ElicitationMode.SPONTANEOUS
    # The Active state rests on evaluation evidence, not on this record.
    (entry,) = seeded_repo.get_transition_history("ACC-MERIDIAN")
    assert entry.baec_id is None and entry.judgment_id is None


def test_rc33_every_seeded_evidence_text_appears_verbatim_in_its_interaction(seeded):
    rows = seeded.execute(
        "SELECT e.text, i.text FROM interaction_evidence e JOIN interactions i USING (interaction_id)"
    ).fetchall()
    assert len(rows) == 4
    for evidence_text, interaction_text in rows:
        assert evidence_text in interaction_text


def test_rc31_every_seed_authorization_is_marked_as_a_synthetic_fixture(seeded):
    assert SEED_AUTHORIZER == "synthetic-seed-fixture"
    assert seeded.execute("SELECT DISTINCT authorized_by FROM human_authorizations").fetchall() == [
        ("synthetic-seed-fixture",)
    ]
    actions = [row[0] for row in seeded.execute("SELECT action FROM human_authorizations ORDER BY authorization_id")]
    assert actions == [
        "CONFIRM_BAEC",
        "RECORD_DORMANCY_JUDGMENT",
        "CHANGE_ACCOUNT_STATE",
        "CHANGE_ACCOUNT_STATE",
        "CHANGE_ACCOUNT_STATE",
    ]


def test_seed_contains_no_ai_generated_content(seeded, seeded_repo):
    assert seeded.execute(
        "SELECT COUNT(*) FROM baec_records WHERE normalized_text IS NOT NULL "
        "OR normalized_generated_at IS NOT NULL OR normalized_model IS NOT NULL"
    ).fetchone()[0] == 0
    assert seeded.execute("SELECT COUNT(*) FROM criterion_assessments WHERE rationale IS NOT NULL").fetchone()[0] == 0
    for baec_id in ("BAEC-HARBOR-001", "BAEC-MERIDIAN-001"):
        assert seeded_repo.get_baec_record(baec_id).normalized_condition is None


def test_seed_stores_no_external_evidence(seeded):
    assert seeded.execute("SELECT DISTINCT provenance FROM interaction_evidence").fetchall() == [("BUYER_FACT",)]


def test_seed_metadata_marks_the_data_as_synthetic(seeded):
    meta = dict(seeded.execute("SELECT key, value FROM schema_meta"))
    assert meta["seed_version"] == "1"
    assert meta["data_policy"].startswith("SYNTHETIC_ONLY")


def test_seed_thresholds_are_text_in_the_json_never_floats():
    for spec in load_seed_inputs()["baecs"]["baecs"]:
        if spec["stringency"] is not None and spec["stringency"]["numeric_value"] is not None:
            assert isinstance(spec["stringency"]["numeric_value"], str)


def test_seed_directory_contains_exactly_the_four_approved_files():
    assert sorted(p.name for p in DEFAULT_SEED_DIRECTORY.iterdir()) == sorted(SEED_FILES)


def test_harbor_dormant_history_repointed_at_meridians_baec_fails_closed(seeded, seeded_repo):
    """Reproduction from independent review: corrupted history must not load."""
    from baec_app.data.database import PersistenceIntegrityError
    from tests.persistence_builders import tamper

    assert len(seeded_repo.get_transition_history("ACC-HARBOR")) == 1
    tamper(
        seeded,
        "UPDATE account_state_transitions SET baec_id = 'BAEC-MERIDIAN-001' WHERE account_id = 'ACC-HARBOR'",
    )
    with pytest.raises(PersistenceIntegrityError):
        seeded_repo.get_transition_history("ACC-HARBOR")


# --- determinism --------------------------------------------------------------


def test_reseeding_produces_identical_logical_contents_and_identifiers(seeded):
    """Deterministic contents, identifiers, order, and domain results (not byte-identical files)."""
    again = build_seed_database()
    try:
        assert dump(again) == dump(seeded)
        first, second = Repository(seeded), Repository(again)
        assert first.list_accounts() == second.list_accounts()
        for account in first.list_accounts():
            assert first.list_baec_records(account.account_id) == second.list_baec_records(account.account_id)
            assert first.get_transition_history(account.account_id) == second.get_transition_history(account.account_id)
    finally:
        again.close()


def test_seed_file_database_matches_the_in_memory_one(seeded, tmp_path):
    on_disk = build_seed_database(str(tmp_path / "seed.sqlite3"))
    try:
        assert dump(on_disk) == dump(seeded)
    finally:
        on_disk.close()


# --- the seed cannot assert an outcome the domain does not produce -------------

SEED_FAILURES = {
    "expected classification the rules do not produce": (
        "baecs.json",
        lambda d: d["baecs"][1].update(expected_classification="CONFIRMED_BAEC", expected_reason=None),
    ),
    "expected reason that is not the canonical text": (
        "baecs.json",
        lambda d: d["baecs"][1].update(expected_reason="NOT_BAEC: buyer is already evaluating."),
    ),
    "expected final state the transitions do not reach": (
        "accounts.json",
        lambda d: d["accounts"][1].update(expected_final_state="CONDITIONALLY_DORMANT"),
    ),
    "authorization not marked as a seed fixture": (
        "baecs.json",
        lambda d: d["baecs"][0]["confirmation"].update(authorized_by="a-real-person"),
    ),
    "evidence text that is not in the interaction": (
        "baecs.json",
        lambda d: d["baecs"][0]["assessments"][0]["evidence"][0].update(text="We might look around someday."),
    ),
    "buyer statement that is not verbatim in the source excerpt": (
        "baecs.json",
        lambda d: d["baecs"][0].update(buyer_exact_statement="If pricing rises 10% we'd look."),
    ),
    "seeded transition the state machine rejects": (
        "account_events.json",
        lambda d: d["events"][1].update(plausibility="NO"),
    ),
    "threshold with no number under a numeric comparator": (
        "baecs.json",
        lambda d: d["baecs"][0]["stringency"].update(numeric_value=None),
    ),
    "unknown event type": ("account_events.json", lambda d: d["events"][0].update(type="set_state")),
    "BAEC that no event saves": ("account_events.json", lambda d: d["events"].pop(4)),
}


@pytest.mark.parametrize("case", SEED_FAILURES.values(), ids=SEED_FAILURES.keys())
def test_seed_fails_when_inputs_disagree_with_the_domain(seed_copy, case):
    file_name, change = case
    edit(seed_copy, file_name, change)
    with pytest.raises(Exception) as caught:
        build_seed_database(directory=seed_copy)
    assert not isinstance(caught.value, (KeyError, AttributeError, TypeError, IndexError))


def test_unmodified_seed_copy_builds(seed_copy):
    connection = build_seed_database(directory=seed_copy)
    try:
        assert table_counts(connection) == EXPECTED_COUNTS
    finally:
        connection.close()


def test_seed_refuses_a_database_that_already_has_accounts(seeded):
    with pytest.raises(SeedError):
        seed_database(seeded)
    assert table_counts(seeded) == EXPECTED_COUNTS


# --- canonical seed versus mutable working copies -----------------------------


@pytest.fixture
def canonical():
    connection = build_canonical_seed_database()
    yield connection
    connection.close()


def test_canonical_seed_database_is_read_only(canonical):
    with pytest.raises(sqlite3.OperationalError):
        canonical.execute("INSERT INTO accounts (account_id, name) VALUES ('X', 'Synthetic')")
    with pytest.raises(sqlite3.OperationalError):
        Repository(canonical).add_account(Account("ACC-NEW", "Synthetic"))
    assert table_counts(canonical) == EXPECTED_COUNTS


def test_changing_a_working_copy_leaves_the_canonical_seed_untouched(canonical):
    before = dump(canonical)
    working = create_working_copy(canonical)
    try:
        assert dump(working) == before
        repository = Repository(working)
        repository.add_account(Account("ACC-NEW", "Session-only Synthetic Account"))
        from baec_app.data.records import SourceInteraction
        from baec_app.domain.enums import AuthorizationAction
        from baec_app.domain.models import HumanAuthorization, NonEvaluationEvidence
        from tests.builders import NOW, excerpt

        closing_text = "We closed the supplier review and are staying with our current supplier."
        repository.add_interaction(
            SourceInteraction("INT-SESSION-001", "ACC-MERIDIAN", NOW, "Buyer (Director of Supply Chain): " + closing_text)
        )
        result = repository.persist_transition_to_no_plausible_path(
            "ACC-MERIDIAN",
            ground=NoPlausiblePathGround.OTHER,
            reason="Session-only change made in a working copy.",
            authorization=HumanAuthorization(
                "session-user", NOW, AuthorizationAction.CHANGE_ACCOUNT_STATE, "ACC-MERIDIAN", NP
            ),
            non_evaluation_evidence=NonEvaluationEvidence(
                "ACC-MERIDIAN", excerpt(closing_text, source_id="INT-SESSION-001"), NOW
            ),
            recorded_at=NOW,
        )
        assert result.allowed
        assert repository.get_account("ACC-MERIDIAN").state is NP
        assert dump(working) != before
    finally:
        working.close()
    assert dump(canonical) == before
    assert Repository(canonical).get_account("ACC-MERIDIAN").state is AO


def test_two_working_copies_are_independent_of_each_other(canonical):
    first = create_working_copy(canonical)
    second = create_working_copy(canonical)
    try:
        Repository(first).add_account(Account("ACC-ONLY-FIRST", "Synthetic"))
        assert len(Repository(first).list_accounts()) == 4
        assert len(Repository(second).list_accounts()) == 3
    finally:
        first.close()
        second.close()


def test_working_copy_can_be_a_file_and_is_fully_usable(canonical, tmp_path):
    working = create_working_copy(canonical, str(tmp_path / "session.sqlite3"))
    try:
        assert Repository(working).get_baec_record("BAEC-HARBOR-001").classification is BaecClassification.CONFIRMED_BAEC
    finally:
        working.close()
    reopened = open_database(str(tmp_path / "session.sqlite3"))
    try:
        assert table_counts(reopened) == EXPECTED_COUNTS
    finally:
        reopened.close()


# --- development script -------------------------------------------------------


def run_script(*arguments):
    return subprocess.run(
        [sys.executable, "-W", "error", str(REPOSITORY_ROOT / "scripts" / "seed_demo.py"), *arguments],
        capture_output=True,
        text=True,
        cwd=REPOSITORY_ROOT,
    )


def test_seed_demo_script_builds_a_development_database(tmp_path):
    target = tmp_path / "dev folder" / "baec dev.sqlite3"
    first = run_script("--path", str(target))
    assert first.returncode == 0, first.stderr
    assert "Synthetic Data Only" in first.stdout
    assert "interaction_evidence: 4" in first.stdout
    assert first.stderr == ""
    assert not target.with_name(target.name + ".building").exists()

    connection = open_database(str(target))
    try:
        assert table_counts(connection) == EXPECTED_COUNTS
    finally:
        connection.close()

    second = run_script("--path", str(target))
    assert second.returncode == 1 and "--force" in second.stdout
    third = run_script("--path", str(target), "--force")
    assert third.returncode == 0, third.stderr
