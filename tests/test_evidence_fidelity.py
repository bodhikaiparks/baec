"""Evidence fidelity (Phase 3 hardening, schema version 4).

Implementation-level constraint, not a research claim: stored BUYER_FACT and
SELLER_OBSERVATION evidence text must occur verbatim (exact, case-sensitive
substring) in the text of the interaction it cites. Enforced on repository
save, on repository load, and by the interaction_evidence_verbatim trigger.
"""

import sqlite3
from dataclasses import replace

import pytest

from baec_app.data.database import (
    DatabaseVersionError,
    PersistenceIntegrityError,
    RepositoryVerificationError,
    open_database,
)
from baec_app.data.repository import Repository
from baec_app.data.seed import build_seed_database
from baec_app.domain.baec_rules import create_confirmed_baec_record, create_nonconfirmed_classification_record
from baec_app.domain.enums import AccountState, ArticulationOrigin, BaecCriterion, ProvenanceCategory
from baec_app.domain.models import CriterionAssessment, EvaluationEvidence, NonEvaluationEvidence
from tests.builders import AO, CD, HARBOR_QUOTE, NOW, NP, candidate, confirm_auth, excerpt, state_auth
from tests.persistence_builders import (  # noqa: F401
    INTERACTION_TEXTS,
    connection,
    dump,
    move,
    no_leaked_connections,
    put_in_state,
    repo,
    save_judgment,
    tamper,
)

SELLER = ProvenanceCategory.SELLER_OBSERVATION
EVALUATION_TEXT = "We have opened a formal supplier review."  # in INT-2
NON_EVALUATION_TEXT = "We closed the review and are staying put."  # in INT-3
FABRICATED = "We have started a formal RFP."  # in no interaction


def with_criterion_evidence(cand, criterion, evidence):
    assessments = tuple(
        CriterionAssessment(a.criterion, a.finding, (evidence,), a.rationale) if a.criterion is criterion else a
        for a in cand.assessments
    )
    return replace(cand, assessments=assessments)


def save_confirmed_candidate(repository, cand, baec_id="B-1"):
    repository.save_confirmed_baec(
        create_confirmed_baec_record(cand, baec_id=baec_id, captured_at=NOW, confirmation=confirm_auth(baec_id))
    )


# --- the seven evidence paths ---------------------------------------------------
# Each path stores one excerpt with the given text and provenance; every other
# excerpt in the write is valid. Returns a function that reads the result back.


def _source_excerpt(repository, text, provenance):
    save_confirmed_candidate(repository, candidate(source_excerpt=excerpt(text, provenance)))
    return lambda: repository.get_baec_record("B-1").candidate.source_excerpt.text


def _criterion(criterion):
    def path(repository, text, provenance):
        save_confirmed_candidate(repository, with_criterion_evidence(candidate(), criterion, excerpt(text, provenance)))
        record = repository.get_baec_record("B-1")
        return lambda: next(a for a in record.candidate.assessments if a.criterion is criterion).evidence[0].text

    return path


def _evaluation(repository, text, provenance):
    evidence = EvaluationEvidence("ACC-1", excerpt(text, provenance, source_id="INT-2"), NOW)
    assert move(repository, AO, evaluation_evidence=evidence).allowed
    return lambda: repository.get_transition_history("ACC-1")[-1].evaluation_evidence.evidence.text


def _non_evaluation(repository, text, provenance):
    evidence = NonEvaluationEvidence("ACC-1", excerpt(text, provenance, source_id="INT-3"), NOW)
    assert move(repository, NP, non_evaluation_evidence=evidence).allowed
    return lambda: repository.get_transition_history("ACC-1")[-1].non_evaluation_evidence.evidence.text


# path name: (function, valid text, cited interaction, text that lives in another interaction, setup)
PATHS = {
    "source-excerpt": (_source_excerpt, HARBOR_QUOTE, "INT-1", EVALUATION_TEXT, None),
    **{
        f"criterion-{c.value}": (_criterion(c), HARBOR_QUOTE, "INT-1", EVALUATION_TEXT, None)
        for c in BaecCriterion
    },
    "evaluation-evidence": (_evaluation, EVALUATION_TEXT, "INT-2", NON_EVALUATION_TEXT, None),
    "non-evaluation-evidence": (_non_evaluation, NON_EVALUATION_TEXT, "INT-3", EVALUATION_TEXT, AO),
}


def _prepare(repository, path):
    setup = PATHS[path][4]
    if setup is not None:
        put_in_state(repository, setup)


def _mutations(valid, elsewhere):
    return {
        "case-changed": valid[0].swapcase() + valid[1:],
        "doubled-space": valid.replace(" ", "  ", 1),
        "space-as-newline": valid.replace(" ", "\n", 1),
        "trailing-space": valid + " ",
        "cyrillic-lookalike": valid.replace("e", "е", 1),
        "fabricated": FABRICATED,
        "from-another-interaction": elsewhere,
    }


MUTATION_NAMES = list(_mutations("a b e", "x"))


@pytest.mark.parametrize("part", ["whole", "prefix", "middle", "suffix"])
@pytest.mark.parametrize("path", list(PATHS))
def test_exact_substring_is_accepted(repo, path, part):
    function, valid, interaction_id, _, _ = PATHS[path]
    full = INTERACTION_TEXTS[interaction_id]
    start = full.index(valid)
    text = {
        "whole": full,
        "prefix": full[: start + 10],
        "middle": valid[3:-3],
        "suffix": full[start + 5 :],
    }[part]
    assert text in full
    _prepare(repo, path)
    read_back = function(repo, text, SELLER)
    assert read_back() == text


@pytest.mark.parametrize("mutation", MUTATION_NAMES)
@pytest.mark.parametrize("path", list(PATHS))
def test_non_verbatim_evidence_is_rejected_on_every_path(repo, connection, path, mutation):
    function, valid, interaction_id, elsewhere, _ = PATHS[path]
    text = _mutations(valid, elsewhere)[mutation]
    assert text not in INTERACTION_TEXTS[interaction_id]
    _prepare(repo, path)
    before, state, history = dump(connection), repo.get_account("ACC-1").state, repo.get_transition_history("ACC-1")
    with pytest.raises(RepositoryVerificationError, match="verbatim"):
        function(repo, text, ProvenanceCategory.BUYER_FACT)
    assert dump(connection) == before
    assert repo.get_account("ACC-1").state is state
    assert repo.get_transition_history("ACC-1") == history


@pytest.mark.parametrize("path", [p for p in PATHS if PATHS[p][2] == "INT-1"])
def test_curly_apostrophe_is_not_the_stored_straight_apostrophe(repo, connection, path):
    curly = HARBOR_QUOTE.replace("we'd", "we’d")
    assert curly != HARBOR_QUOTE
    before = dump(connection)
    with pytest.raises(RepositoryVerificationError, match="verbatim"):
        PATHS[path][0](repo, curly, ProvenanceCategory.BUYER_FACT)
    assert dump(connection) == before


def test_fabricated_candidate_source_excerpt_is_rejected_for_nonconfirmed_saves_too(repo, connection):
    cand = candidate(ArticulationOrigin.SELLER_SEEDED, source_excerpt=excerpt(FABRICATED))
    before = dump(connection)
    with pytest.raises(RepositoryVerificationError, match="verbatim"):
        repo.save_classification_record(create_nonconfirmed_classification_record(cand, baec_id="B-1", captured_at=NOW))
    assert dump(connection) == before


def test_fabricated_evaluation_evidence_cannot_activate():
    """Regression: on the seed at phase-3-persistence-hardening this moved Summit to ACTIVE_OPPORTUNITY."""
    seeded = build_seed_database()
    try:
        repository = Repository(seeded)
        interaction_id = repository.list_interactions("ACC-SUMMIT")[0].interaction_id
        evidence = EvaluationEvidence("ACC-SUMMIT", excerpt(FABRICATED, SELLER, interaction_id), NOW)
        before = dump(seeded)
        with pytest.raises(RepositoryVerificationError, match="verbatim"):
            repository.persist_transition_to_active_opportunity(
                "ACC-SUMMIT",
                evaluation_evidence=evidence,
                authorization=state_auth(AO, account_id="ACC-SUMMIT"),
                recorded_at=NOW,
            )
        assert dump(seeded) == before
        assert repository.get_account("ACC-SUMMIT").state is AccountState.NO_PLAUSIBLE_PATH
    finally:
        seeded.close()


@pytest.mark.parametrize("to", [NP, CD], ids=lambda s: s.value)
def test_fabricated_non_evaluation_evidence_cannot_leave_active(repo, connection, to):
    if to is CD:
        save_confirmed_candidate(repo, candidate())
        save_judgment(repo)
    put_in_state(repo, AO)
    fabricated = NonEvaluationEvidence("ACC-1", excerpt(FABRICATED, source_id="INT-3"), NOW)
    before = dump(connection)
    with pytest.raises(RepositoryVerificationError, match="verbatim"):
        move(repo, to, non_evaluation_evidence=fabricated)
    assert dump(connection) == before
    assert repo.get_account("ACC-1").state is AO


def test_text_from_another_interaction_does_not_authorize_citing_the_wrong_one(repo, connection):
    assert EVALUATION_TEXT in INTERACTION_TEXTS["INT-2"] and EVALUATION_TEXT not in INTERACTION_TEXTS["INT-3"]
    before = dump(connection)
    with pytest.raises(RepositoryVerificationError, match="verbatim"):
        move(repo, AO, evaluation_evidence=EvaluationEvidence("ACC-1", excerpt(EVALUATION_TEXT, source_id="INT-3"), NOW))
    assert dump(connection) == before


def test_the_same_text_may_be_cited_from_each_interaction_that_contains_it(repo):
    """Positive control: HARBOR_QUOTE is in INT-1 (ACC-1) and INT-9 (ACC-2)."""
    save_confirmed_candidate(repo, candidate())
    int9 = excerpt(source_id="INT-9")
    save_confirmed_candidate(
        repo,
        candidate(account_id="ACC-2", source_interaction_id="INT-9", source_excerpt=int9, criterion_evidence=int9),
        baec_id="B-9",
    )
    assert repo.get_baec_record("B-9").candidate.source_excerpt.source_id == "INT-9"


# --- the schema trigger ---------------------------------------------------------

RAW_INSERT = "INSERT INTO interaction_evidence (interaction_id, provenance, text) VALUES (?, ?, ?)"


@pytest.mark.parametrize("other_guards", [True, False], ids=["guards-on", "fk-and-checks-off"])
@pytest.mark.parametrize(
    "values",
    [("INT-1", "BUYER_FACT", FABRICATED), ("INT-1", "SELLER_OBSERVATION", HARBOR_QUOTE.upper()), ("INT-1", "BUYER_FACT", "")],
    ids=["fabricated", "case-changed", "empty"],
)
def test_raw_sql_fabricated_evidence_insert_is_refused_by_the_schema(repo, connection, values, other_guards):
    before = dump(connection)
    if not other_guards:
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute("PRAGMA ignore_check_constraints = ON")
    try:
        with pytest.raises(sqlite3.IntegrityError, match="verbatim"):
            connection.execute(RAW_INSERT, values)
    finally:
        connection.execute("PRAGMA ignore_check_constraints = OFF")
        connection.execute("PRAGMA foreign_keys = ON")
    assert dump(connection) == before


def test_raw_sql_evidence_for_a_missing_interaction_is_refused_even_with_foreign_keys_off(repo, connection):
    connection.execute("PRAGMA foreign_keys = OFF")
    try:
        with pytest.raises(sqlite3.IntegrityError, match="verbatim"):
            connection.execute(RAW_INSERT, ("INT-404", "BUYER_FACT", "x"))
    finally:
        connection.execute("PRAGMA foreign_keys = ON")


@pytest.mark.parametrize("provenance", ["EXTERNAL_EVIDENCE", "AI_INFERENCE", "UNKNOWN"])
def test_provenance_check_still_refuses_non_interaction_evidence_with_verbatim_text(repo, connection, provenance):
    """The verbatim trigger now fires first for older 'x' cases; verbatim text keeps this CHECK exercised."""
    before = dump(connection)
    with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
        connection.execute(RAW_INSERT, ("INT-1", provenance, HARBOR_QUOTE))
    assert dump(connection) == before


def test_raw_sql_verbatim_evidence_insert_is_accepted(repo, connection):
    connection.execute(RAW_INSERT, ("INT-1", "SELLER_OBSERVATION", "Rep note: pricing at renewal."))
    assert connection.execute("SELECT COUNT(*) FROM interaction_evidence").fetchone()[0] == 1


AGREEMENT_HAYSTACKS = [INTERACTION_TEXTS["INT-1"], "Café — “quoted” naïve résumé"]
AGREEMENT_NEEDLES = (
    [HARBOR_QUOTE, HARBOR_QUOTE.replace("we'd", "we’d"), "Rep note", "rep note", "Café", "Café"]
    + list(_mutations(HARBOR_QUOTE, EVALUATION_TEXT).values())
    + ["“quoted”", '"quoted"', "naïve", "naive"]
)


@pytest.mark.parametrize("haystack", AGREEMENT_HAYSTACKS, ids=["harbor", "non-ascii"])
def test_sql_and_python_agree_on_verbatim_matching(connection, haystack):
    for needle in AGREEMENT_NEEDLES:
        sql = connection.execute("SELECT instr(?, ?) > 0", (haystack, needle)).fetchone()[0] == 1
        assert sql is (needle in haystack), needle


# --- bypass every guard: loading still fails closed -----------------------------

FABRICATED_ROW = "INSERT INTO interaction_evidence (evidence_id, interaction_id, provenance, text) VALUES (900, ?, 'BUYER_FACT', ?)"


@pytest.mark.parametrize(
    "repoint",
    ["UPDATE baec_records SET source_excerpt_id = 900"]
    + [f"UPDATE criterion_evidence SET evidence_id = 900 WHERE criterion = '{c.value}'" for c in BaecCriterion],
    ids=["source-excerpt"] + [f"criterion-{c.value}" for c in BaecCriterion],
)
def test_bypassed_schema_still_fails_closed_on_load_for_baec_evidence(repo, connection, repoint):
    save_confirmed_candidate(repo, candidate())
    tamper(connection, FABRICATED_ROW, ("INT-1", FABRICATED))
    tamper(connection, repoint)
    with pytest.raises(PersistenceIntegrityError, match="verbatim"):
        repo.get_baec_record("B-1")


@pytest.mark.parametrize("table", ["evaluation_evidence", "non_evaluation_evidence"])
def test_bypassed_schema_still_fails_closed_on_load_for_state_evidence(repo, connection, table):
    put_in_state(repo, AO)
    assert move(repo, NP).allowed  # stores non-evaluation evidence from INT-3
    interaction = "INT-2" if table == "evaluation_evidence" else "INT-3"
    tamper(connection, FABRICATED_ROW, (interaction, FABRICATED))
    tamper(connection, f"UPDATE {table} SET evidence_id = 900")
    with pytest.raises(PersistenceIntegrityError, match="verbatim"):
        repo.get_transition_history("ACC-1")


def test_interaction_text_rewritten_under_its_evidence_fails_closed(repo, connection):
    save_confirmed_candidate(repo, candidate())
    tamper(connection, "UPDATE interactions SET text = 'Buyer: nothing to add.' WHERE interaction_id = 'INT-1'")
    with pytest.raises(PersistenceIntegrityError, match="verbatim"):
        repo.get_baec_record("B-1")


def test_database_created_with_schema_version_3_is_refused(tmp_path):
    """Version 3 lacked evidence fidelity; such a file must be rebuilt."""
    path = str(tmp_path / "v3.sqlite3")
    first = open_database(path)
    first.execute("PRAGMA user_version = 3")
    first.close()
    with pytest.raises(DatabaseVersionError, match="rebuild the database from the seed files"):
        open_database(path)
