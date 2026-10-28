"""O-3, O-5, and O-6 readiness: validation only. Nothing is filled in and nothing executes.

Every value below is a test placeholder, not a frozen configuration, staffing decision, or supplement.
"""

import ast
import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

from experiments.engine3_stage_f.envelope import OUTPUT_SCHEMA_VERSION
from experiments.engine3_stage_f.readiness import (
    ARM_A_SUPPLEMENT_COUNT,
    AnnotatorStaffing,
    FrozenRunConfiguration,
    PilotReadiness,
    SupplementArtifact,
    check_pilot_readiness,
)

REPO = Path(__file__).resolve().parents[2]
STAFF = AnnotatorStaffing("TEST-ONLY-A1", "TEST-ONLY-A2", "TEST-ONLY-ADJ", frozen=True)
CONFIG = FrozenRunConfiguration("TEST-ONLY-CONFIG", "TEST-ONLY-MODEL-ID", "TEST-ONLY-VERSION", "c" * 64,
                                (("temperature", 0),), OUTPUT_SCHEMA_VERSION, frozen=True)
SUPPLEMENT = tuple(SupplementArtifact(f"TEST-ONLY-SUPP-{n:02d}", f"{n:064x}", ("TEST-ONLY-AUTHOR",), True, True)
                   for n in range(1, ARM_A_SUPPLEMENT_COUNT + 1))

# HARNESS_AMENDMENT_001: the author role aliases recorded in the frozen O-6 supplement.
DOCS = REPO / "docs" / "engine3"
FROZEN_PATH = DOCS / "ENGINE3_STAGE_F_ARM_A_ADDITIONAL_ARTIFACTS_FROZEN.json"
FREEZE_MANIFEST_PATH = DOCS / "ENGINE3_STAGE_F_ARM_A_FREEZE_MANIFEST.json"
OWNER, DRAFTING = "PROJECT_OWNER", "CLAUDE_CODE_ASSISTED_DRAFTING"
ROLE_SUPPLEMENT = tuple(replace(a, author_aliases=(DRAFTING, OWNER)) for a in SUPPLEMENT)


def _items(readiness: PilotReadiness) -> set[str]:
    return {item for item, _ in readiness.unresolved}


def test_nothing_supplied_leaves_o3_o5_o6_unresolved():
    readiness = check_pilot_readiness()
    assert not readiness.preconditions_met and _items(readiness) == {"O-3", "O-5", "O-6"}


def test_complete_inputs_meet_preconditions_but_never_authorize_a_run():
    readiness = check_pilot_readiness(STAFF, CONFIG, SUPPLEMENT)
    assert readiness.preconditions_met and readiness.unresolved == ()
    assert readiness.owner_approval_still_required is True
    public = {n for n in dir(readiness) if not n.startswith("_")}
    assert public == {"unresolved", "preconditions_met", "owner_approval_still_required"}


@pytest.mark.parametrize("staffing", [
    replace(STAFF, frozen=False), replace(STAFF, primary_b_alias="TEST-ONLY-A1"),
    replace(STAFF, adjudicator_alias="TEST-ONLY-A2"), replace(STAFF, adjudicator_alias=" "), "A1,A2,ADJ",
])
def test_o3_requires_two_distinct_primaries_and_a_separate_frozen_adjudicator(staffing):
    assert "O-3" in _items(check_pilot_readiness(staffing, CONFIG, SUPPLEMENT))


@pytest.mark.parametrize("configuration", [
    replace(CONFIG, frozen=False), replace(CONFIG, model_identifier=""), replace(CONFIG, model_version=""),
    replace(CONFIG, experiment_configuration_id=""), replace(CONFIG, prompt_sha256="C" * 64),
    replace(CONFIG, sampling_configuration=()), replace(CONFIG, sampling_configuration=(("t", 0), ("t", 1))),
    replace(CONFIG, output_schema_version="engine3.ai_preparation_proposal.v1"),
])
def test_o5_requires_an_exact_frozen_configuration(configuration):
    assert _items(check_pilot_readiness(STAFF, configuration, SUPPLEMENT)) == {"O-5"}


@pytest.mark.parametrize("supplement", [
    SUPPLEMENT[:15], SUPPLEMENT + (replace(SUPPLEMENT[0], artifact_id="X"),),
    (replace(SUPPLEMENT[0], human_approved=False),) + SUPPLEMENT[1:],
    (replace(SUPPLEMENT[0], frozen=False),) + SUPPLEMENT[1:],
    (replace(SUPPLEMENT[0], artifact_sha256="not-a-hash"),) + SUPPLEMENT[1:],
    (replace(SUPPLEMENT[0], author_aliases=()),) + SUPPLEMENT[1:],
    (replace(SUPPLEMENT[0], author_aliases=("TEST-ONLY-A1", "TEST-ONLY-A2")),) + SUPPLEMENT[1:],
    (replace(SUPPLEMENT[0], author_aliases=("TEST-ONLY-A1",)),) + SUPPLEMENT[1:],
    (replace(SUPPLEMENT[1], artifact_id=SUPPLEMENT[0].artifact_id),) + SUPPLEMENT[1:],
])
def test_o6_requires_sixteen_approved_frozen_independently_annotated_artifacts(supplement):
    assert _items(check_pilot_readiness(STAFF, CONFIG, supplement)) == {"O-6"}


def test_o6_author_independence_cannot_be_checked_without_o3():
    assert _items(check_pilot_readiness(None, CONFIG, SUPPLEMENT)) == {"O-3", "O-6"}


# --- HARNESS_AMENDMENT_001: either primary being an author fails author independence -------------

def _author_failures(staffing, supplement=ROLE_SUPPLEMENT) -> list[str]:
    return [p for item, p in check_pilot_readiness(staffing, CONFIG, supplement).unresolved
            if item == "O-6" and p.endswith("a primary annotator is one of its authors")]


@pytest.mark.parametrize("primary_a, primary_b", [
    (OWNER, "TEST-ONLY-A2"),                                      # 1. Primary A is the owner
    ("TEST-ONLY-A1", OWNER),                                      # 2. Primary B is the owner
    (DRAFTING, "TEST-ONLY-A2"),                                   # 3. Primary A is the drafting role
    ("TEST-ONLY-A1", DRAFTING),
    ("project_owner", "TEST-ONLY-A2"),                            # 4. case variants
    ("TEST-ONLY-A1", "Project_Owner"),
    ("claude_code_assisted_drafting", "TEST-ONLY-A2"),
    (" Project_Owner", "TEST-ONLY-A2"),                           # 5. whitespace variants
    ("TEST-ONLY-A1", "PROJECT_OWNER \t"),
    (" CLAUDE_CODE_ASSISTED_DRAFTING", "TEST-ONLY-A2"),
    (OWNER, DRAFTING),                                            # 7. both primaries are authors
])
def test_either_primary_matching_an_author_fails_every_artifact(primary_a, primary_b):
    staffing = AnnotatorStaffing(primary_a, primary_b, "TEST-ONLY-ADJ", frozen=True)
    readiness = check_pilot_readiness(staffing, CONFIG, ROLE_SUPPLEMENT)
    assert _items(readiness) == {"O-6"} and not readiness.preconditions_met
    assert len(_author_failures(staffing)) == ARM_A_SUPPLEMENT_COUNT


@pytest.mark.parametrize("primary_a, primary_b", [
    ("TEST-ONLY-A1", "TEST-ONLY-A2"),                             # 6. two distinct non-author primaries
    ("TEST-ONLY-OWNER", "TEST-ONLY-CLAUDE"),                      # no substring matching
    ("PROJECT_OWNER_REVIEWER", "CLAUDE_CODE_ASSISTED_DRAFTING_CHECKER"),
])
def test_non_author_primaries_pass_author_independence(primary_a, primary_b):
    staffing = AnnotatorStaffing(primary_a, primary_b, "TEST-ONLY-ADJ", frozen=True)
    assert check_pilot_readiness(staffing, CONFIG, ROLE_SUPPLEMENT).unresolved == ()


@pytest.mark.parametrize("adjudicator", [OWNER, DRAFTING, " project_owner "])
def test_adjudicator_matching_an_author_is_not_a_new_hard_rule(adjudicator):
    """8. Adjudicator independence from drafting stays preferred, not a locked requirement."""
    staffing = AnnotatorStaffing("TEST-ONLY-A1", "TEST-ONLY-A2", adjudicator, frozen=True)
    assert check_pilot_readiness(staffing, CONFIG, ROLE_SUPPLEMENT).unresolved == ()


def test_frozen_o6_supplement_is_unchanged_and_uses_the_reserved_author_roles():
    """9. The O-6 frozen corpus and freeze manifest are byte-identical to the 3be6743 freeze."""
    def sha(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = json.loads(FREEZE_MANIFEST_PATH.read_text(encoding="utf-8"))
    frozen = json.loads(FROZEN_PATH.read_text(encoding="utf-8"))
    assert sha(FREEZE_MANIFEST_PATH) == "2d477ff6b05433b64e76aeb7efd3695146cf92fc347b2bf40f6f1a677d402967"
    assert sha(FROZEN_PATH) == manifest["frozen_file_sha256"] == (
        "9211996f730a575a1e393eb52065740fbf824095f4c9afd7c1032cbe9242876f")
    assert manifest["corpus_freeze_sha256"] == "c7ff8233173046b248d5548e06fab336b2d697ee6c67a1317525bd065d1b0961"
    assert manifest["status"] == "O6_RESOLVED_FROZEN" and frozen["o6_status"] == "RESOLVED_FROZEN"
    assert sorted(frozen["author_role_aliases"]) == sorted([OWNER, DRAFTING])
    assert {i["id"]: i["status"] for i in manifest["pre_run_required"]} == {
        "O-3": "PRE_RUN_REQUIRED", "O-5": "PRE_RUN_REQUIRED", "O-6": "RESOLVED_FROZEN"}


def test_the_frozen_o6_records_fail_when_an_author_role_is_one_primary():
    manifest = json.loads(FREEZE_MANIFEST_PATH.read_text(encoding="utf-8"))
    frozen = json.loads(FROZEN_PATH.read_text(encoding="utf-8"))
    supplement = tuple(SupplementArtifact(a["artifact_id"], manifest["artifact_freeze_sha256"][a["artifact_id"]],
                                          tuple(frozen["author_role_aliases"]), a["human_approved"], a["frozen"])
                       for a in frozen["artifacts"])
    independent = AnnotatorStaffing("TEST-ONLY-A1", "TEST-ONLY-A2", "TEST-ONLY-ADJ", frozen=True)
    assert check_pilot_readiness(independent, CONFIG, supplement).unresolved == ()
    owner_as_one = replace(independent, primary_b_alias=" project_owner")
    assert len(_author_failures(owner_as_one, supplement)) == ARM_A_SUPPLEMENT_COUNT


@pytest.mark.parametrize("primary_a, primary_b, adjudicator", [
    ("R1", "r1", "R3"),                                           # 1. case variant primaries
    (" Reviewer-A ", "reviewer-a", "R3"),                         # 2. whitespace and case variant primaries
    ("R1", "R2", " r1 "),                                         # 3. primary and adjudicator differ by case/spacing
    ("R1", "R2", "R2\t"),
])
def test_reviewer_aliases_must_be_distinct_after_normalization(primary_a, primary_b, adjudicator):
    staffing = AnnotatorStaffing(primary_a, primary_b, adjudicator, frozen=True)
    readiness = check_pilot_readiness(staffing, CONFIG, SUPPLEMENT)
    assert readiness.unresolved == (
        ("O-3", "two distinct primary annotators and a separate adjudicator are required"),)


def test_normalized_distinct_aliases_pass_and_are_stored_as_given():
    """4. Normalization is for comparison only."""
    staffing = AnnotatorStaffing("R1", "R2", " r3 ", frozen=True)
    assert check_pilot_readiness(staffing, CONFIG, SUPPLEMENT).unresolved == ()
    assert (staffing.primary_a_alias, staffing.primary_b_alias, staffing.adjudicator_alias) == ("R1", "R2", " r3 ")


def test_o3_record_keeps_project_owner_alias_governance():
    """6. The owner is visible to the author check only under PROJECT_OWNER; the record must say so and comply."""
    record = json.loads((DOCS / "ENGINE3_STAGE_F_O3_ANNOTATOR_STAFFING_CANDIDATE.json").read_text(encoding="utf-8"))
    assert record["reserved_author_role_aliases"] == [OWNER, DRAFTING]
    assert any("exactly PROJECT_OWNER" in rule for rule in record["alias_rules"])
    for role in record["roles"].values():
        assert "relationship_to_project" in role and "stable_reviewer_alias" in role
        alias, relationship = role["stable_reviewer_alias"], role["relationship_to_project"]
        if relationship == OWNER:
            assert alias == OWNER
        if isinstance(alias, str) and alias.strip().casefold() == OWNER.casefold():
            assert alias == OWNER and relationship == OWNER
    for primary in ("primary_a", "primary_b"):
        assert record["roles"][primary]["requirements"]["may_be_project_owner"] is False


def test_readiness_stays_false_while_o3_and_o5_are_unresolved():
    """10. With the frozen O-6 supplement and nothing else, O-3 and O-5 are unresolved and nothing is ready."""
    manifest = json.loads(FREEZE_MANIFEST_PATH.read_text(encoding="utf-8"))
    frozen = json.loads(FROZEN_PATH.read_text(encoding="utf-8"))
    supplement = tuple(SupplementArtifact(a["artifact_id"], manifest["artifact_freeze_sha256"][a["artifact_id"]],
                                          tuple(frozen["author_role_aliases"]), a["human_approved"], a["frozen"])
                       for a in frozen["artifacts"])
    readiness = check_pilot_readiness(None, None, supplement)
    assert not readiness.preconditions_met and {"O-3", "O-5"} <= _items(readiness)
    # The only O-6 items are the D-9 cross-check waiting on O-3; every O-6 record check passes.
    assert all(p.endswith("author independence cannot be checked without O-3")
               for item, p in readiness.unresolved if item == "O-6")


@pytest.mark.parametrize("aliases", [("", "", ""), (None, None, None), ("TEST-ONLY-A1", " ", "TEST-ONLY-ADJ")])
def test_o3_cannot_be_ready_with_empty_or_null_reviewer_identities(aliases):
    """10. Empty identities never satisfy O-3, and author independence is not treated as checked."""
    staffing = AnnotatorStaffing(*aliases, frozen=True)
    readiness = check_pilot_readiness(staffing, CONFIG, ROLE_SUPPLEMENT)
    assert not readiness.preconditions_met and _items(readiness) == {"O-3", "O-6"}
    assert all(p.endswith("author independence cannot be checked without O-3")
               for item, p in readiness.unresolved if item == "O-6")


def test_repository_supplies_no_readiness_record():
    """No harness or script constructs a staffing, configuration, or supplement record."""
    records = {"AnnotatorStaffing", "FrozenRunConfiguration", "SupplementArtifact"}
    paths = sorted((REPO / "experiments").rglob("*.py")) + sorted((REPO / "scripts").glob("*.py"))
    for path in paths:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id not in records, path.relative_to(REPO).as_posix()
