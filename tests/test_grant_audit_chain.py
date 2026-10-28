"""Phase 7F-B: the authority-construction boundary and the reconstructable audit chain.

authorize_grant validates the persisted grant on its own (action and binding digest), independently of the
executor. A successful confirmation is reconstructable, one to one, through
HumanAuthorization -> ai_proposal_confirmations -> human_authorization_grants -> proposal -> review revision ->
reviewed-content digest, enforced by the schema's UNIQUE keys, composite foreign keys, and triggers.
"""

from __future__ import annotations

import inspect
import sqlite3
from datetime import timedelta

import pytest

from baec_app.application import authority
from baec_app.application.errors import ApplicationValidationError, RequestNotRecognized
from baec_app.data.authorization_grants import GrantRecord
from baec_app.data.proposal_bridge import sha256_text
from baec_app.domain.enums import AuthorizationAction
from tests.review_builders import REVIEWER, decisions
from tests.test_proposal_authorization import ISSUED, Authorized


@pytest.fixture
def world(tmp_path):
    w = Authorized(str(tmp_path / "audit.sqlite3"))
    yield w
    w.connection.close()


def _altered(grant: GrantRecord, *, recompute: bool, **fields) -> GrantRecord:
    """A grant object with fields changed, bypassing its constructor (as corrupted storage or forged code would)."""
    altered = object.__new__(GrantRecord)
    altered.__dict__.update(grant.__dict__)
    for field, value in fields.items():
        object.__setattr__(altered, field, value)
    if recompute:
        object.__setattr__(altered, "grant_digest", altered.computed_digest())
    return altered


# --- authorize_grant validates on its own ---------------------------------------------------------------------


def test_a_valid_confirm_baec_grant_yields_exactly_its_own_authorization(world):
    grant = world.grants.get_grant(world.grant().grant_id)
    authorization = authority.authorize_grant(grant)
    assert (authorization.authorized_by, authorization.authorized_at, authorization.action, authorization.subject_id,
            authorization.target_state) == (grant.actor_label, grant.issued_at, AuthorizationAction.CONFIRM_BAEC,
                                            grant.baec_id, None)


def test_a_rebound_action_is_refused_even_with_a_recomputed_digest(world):
    grant = world.grants.get_grant(world.grant().grant_id)
    rebound = _altered(grant, recompute=True, action="CHANGE_ACCOUNT_STATE")
    assert rebound.binding_is_intact()  # the digest alone would not catch it
    with pytest.raises(RequestNotRecognized):
        authority.authorize_grant(rebound)


@pytest.mark.parametrize("field,value", [("actor_label", "someone-else"), ("baec_id", "BAEC-other"),
                                         ("review_content_digest", "0" * 64), ("account_id", "FIXTURE-ACC-2")])
def test_a_damaged_binding_digest_is_refused(world, field, value):
    grant = world.grants.get_grant(world.grant().grant_id)
    damaged = _altered(grant, recompute=False, **{field: value})
    assert not damaged.binding_is_intact()
    with pytest.raises(RequestNotRecognized):
        authority.authorize_grant(damaged)


def test_no_caller_can_substitute_the_actor_time_or_subject():
    assert list(inspect.signature(authority.authorize_grant).parameters) == ["grant"]
    with pytest.raises(ApplicationValidationError):
        authority.authorize_grant({"actor_label": "x", "baec_id": "BAEC-x"})


# --- the audit chain ----------------------------------------------------------------------------------------------


def _executed(world):
    grant = world.grants.get_grant(world.grant().grant_id)
    world.clock.advance(minutes=1)
    world.execution.execute(grant.grant_id)
    return grant


def test_an_authorization_reconstructs_exactly_one_grant_revision_and_reviewed_content_digest(world):
    grant = _executed(world)
    connection = world.connection
    (authorization_id, actor, issued, action, subject) = connection.execute(
        "SELECT authorization_id, authorized_by, authorized_at, action, subject_id FROM human_authorizations").fetchone()
    links = connection.execute("SELECT grant_id, review_revision_id, proposal_id, baec_id FROM ai_proposal_confirmations "
                               "WHERE authorization_id = ?", (authorization_id,)).fetchall()
    assert len(links) == 1
    grant_id, revision_id, proposal_id, baec_id = links[0]
    recovered = world.grants.get_grant(grant_id)
    revision = world.bridge.get_revision(revision_id)
    assert (recovered.action, recovered.account_id, recovered.proposal_id, recovered.review_revision_id,
            recovered.review_content_digest, recovered.actor_label, recovered.issued_at, recovered.baec_id) == (
        "CONFIRM_BAEC", world.proposal.account_id, world.pid, world.accepted.revision.review_revision_id,
        sha256_text(revision.content), REVIEWER, ISSUED, grant.baec_id)
    assert (actor, issued, action, subject) == (recovered.actor_label, "2026-05-01T12:05:00.000000+00:00",
                                                "CONFIRM_BAEC", recovered.baec_id)
    assert (proposal_id, baec_id) == (world.pid, recovered.baec_id) and recovered.binding_is_intact()
    assert connection.execute("SELECT confirmation_authorization_id FROM baec_records WHERE baec_id = ?",
                              (baec_id,)).fetchone() == (authorization_id,)


def test_a_consumed_grant_reconstructs_exactly_one_authorization(world):
    grant = _executed(world)
    rows = world.connection.execute(
        "SELECT authorization.authorization_id FROM human_authorization_grant_consumptions AS used "
        "JOIN ai_proposal_confirmations AS link ON link.grant_id = used.grant_id AND link.baec_id = used.baec_id "
        "JOIN human_authorizations AS authorization ON authorization.authorization_id = link.authorization_id "
        "WHERE used.grant_id = ?", (grant.grant_id,)).fetchall()
    assert len(rows) == 1


@pytest.fixture
def second(world):
    """A second, independent lineage on the same interaction with its own grant, issued by the same actor at the
    same instant, so that only the schema's keys (not timing or actor) can tell the two lineages apart."""
    from baec_app.application.ai_proposal_mapping import AiProposalMappingService
    artifact_id = world.artifact()
    proposal = AiProposalMappingService(world.connection, clock=world.clock).map_artifact(
        artifact_id, created_by=REVIEWER).proposal
    world.service.accept_review(proposal.proposal_id, decisions(), actor_label=REVIEWER)
    grant = world.auth.authorize_confirmation(proposal.proposal_id, actor_label=REVIEWER)
    return world.grants.get_grant(grant.grant_id)


LINK_COLUMNS = ("baec_id", "artifact_id", "proposal_id", "review_revision_id", "grant_id", "authorization_id",
                "account_id", "interaction_id")


def _link(world):
    row = world.connection.execute(f"SELECT {', '.join(LINK_COLUMNS)} FROM ai_proposal_confirmations").fetchone()
    return dict(zip(LINK_COLUMNS, row))


def _insert_link(world, values):
    columns = tuple(values)
    world.connection.execute(f"INSERT INTO ai_proposal_confirmations ({', '.join(columns)}) "
                             f"VALUES ({', '.join('?' for _ in columns)})", tuple(values[c] for c in columns))


def test_a_confirmation_cannot_be_associated_with_another_grant_revision_actor_or_baec(world, second):
    first = _executed(world)
    assert (second.actor_label, second.issued_at) == (first.actor_label, first.issued_at)  # only the keys differ
    existing = _link(world)
    other_revision = second.review_revision_id
    attempts = {
        "another grant": dict(existing, grant_id=second.grant_id),
        "another revision": dict(existing, review_revision_id=other_revision),
        "another baec": dict(existing, baec_id=second.baec_id),
        "same authorization twice": dict(existing, grant_id=second.grant_id, baec_id=second.baec_id,
                                         review_revision_id=other_revision, proposal_id=second.proposal_id,
                                         artifact_id=second.artifact_id),
    }
    for name, values in attempts.items():
        with pytest.raises(sqlite3.IntegrityError):
            _insert_link(world, values)
    assert world.count("ai_proposal_confirmations") == 1


def test_the_authorization_must_carry_the_grant_actor_and_issuance_time(world):
    """The link trigger binds the HumanAuthorization's actor and time to the grant's; another actor cannot link."""
    grant = world.grants.get_grant(world.grant().grant_id)
    from baec_app.application.proposal_review import rebuild_reviewed_candidate
    from baec_app.data.repository import Repository, insert_confirmed_record
    from baec_app.domain.baec_rules import create_confirmed_baec_record
    from baec_app.domain.models import HumanAuthorization
    candidate = rebuild_reviewed_candidate(world.proposal, world.accepted.revision,
                                           Repository(world.connection).get_interaction(grant.interaction_id).text)
    for actor, when in (("someone-else", grant.issued_at), (grant.actor_label, grant.issued_at + timedelta(seconds=1))):
        with pytest.raises(sqlite3.IntegrityError):
            with world.connection:
                world.connection.execute("BEGIN IMMEDIATE")
                record = create_confirmed_baec_record(
                    candidate, baec_id=grant.baec_id, captured_at=grant.issued_at,
                    confirmation=HumanAuthorization(actor, when, AuthorizationAction.CONFIRM_BAEC, grant.baec_id))
                authorization_id = insert_confirmed_record(world.connection, record)
                world.grants.record_confirmation(grant, authorization_id=authorization_id,
                                                 consumed_at=grant.issued_at + timedelta(minutes=1))
        if world.connection.in_transaction:
            world.connection.execute("ROLLBACK")
        assert world.count("ai_proposal_confirmations") == 0 and world.count("baec_records") == 0


def test_a_fresh_confirmation_link_cannot_point_to_another_lineages_grant(world, second):
    """No link exists yet, so neither UNIQUE key nor the primary key can refuse: only the composite foreign key
    from the link to its grant (grant_id, baec_id, account, interaction, artifact, proposal, revision) can."""
    grant = world.grants.get_grant(world.grant().grant_id)
    assert (second.actor_label, second.issued_at) == (grant.actor_label, grant.issued_at)  # the trigger cannot tell
    from baec_app.application.proposal_review import rebuild_reviewed_candidate
    from baec_app.data.repository import Repository, insert_confirmed_record
    from baec_app.domain.baec_rules import create_confirmed_baec_record
    candidate = rebuild_reviewed_candidate(world.proposal, world.accepted.revision,
                                           Repository(world.connection).get_interaction(grant.interaction_id).text)
    world.connection.execute("BEGIN IMMEDIATE")
    try:
        record = create_confirmed_baec_record(candidate, baec_id=grant.baec_id, captured_at=grant.issued_at,
                                              confirmation=authority.authorize_grant(grant))
        authorization_id = insert_confirmed_record(world.connection, record)
        values = dict(baec_id=grant.baec_id, artifact_id=grant.artifact_id, proposal_id=grant.proposal_id,
                      review_revision_id=grant.review_revision_id, grant_id=second.grant_id,
                      authorization_id=authorization_id, account_id=grant.account_id,
                      interaction_id=grant.interaction_id)
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
            _insert_link(world, values)
    finally:
        world.connection.execute("ROLLBACK")
    assert world.count("ai_proposal_confirmations") == 0 and world.count("baec_records") == 0
