"""Phase 5B: the eight read-only resources, exercised through the real in-process Client(server)."""

import json
import sqlite3
import threading

import pytest
from mcp import MCPError

from baec_app.application import ReadService
from baec_app.mcp import adapters, composition
from tests.mcp_builders import Writer, connected, run, seeded_database
from tests.persistence_builders import tamper


@pytest.fixture
def db(tmp_path):
    path = seeded_database(tmp_path)
    writer = Writer(path)
    yield path, writer
    writer.close()


def read_json(path, *uris):
    async def main():
        async with connected(path) as (_, client):
            results = []
            for uri in uris:
                result = await client.read_resource(uri)
                assert len(result.contents) == 1
                content = result.contents[0]
                assert content.mime_type == "application/json" and str(content.uri) == uri
                results.append(json.loads(content.text))
            return results

    return run(main)


def read_error(path, uri):
    async def main():
        async with connected(path) as (_, client):
            with pytest.raises(MCPError) as raised:
                await client.read_resource(uri)
            return raised.value

    return run(main)


def expected(view):
    return json.loads(view.model_dump_json())


# --- all eight resources, matched against the underlying Phase 4 reads -------------------


def test_accounts(db):
    path, writer = db
    (body,) = read_json(path, "baec://accounts")
    assert body == expected(adapters.account_list(writer.repository.list_accounts()))
    assert [a["account_id"] for a in body["accounts"]] == ["ACC-HARBOR", "ACC-SUMMIT", "ACC-MERIDIAN"]  # stored order
    assert {a["account_id"]: a["state"] for a in body["accounts"]} == {
        "ACC-HARBOR": "CONDITIONALLY_DORMANT", "ACC-MERIDIAN": "ACTIVE_OPPORTUNITY", "ACC-SUMMIT": "NO_PLAUSIBLE_PATH",
    }


@pytest.mark.parametrize("account_id", ["ACC-HARBOR", "ACC-MERIDIAN", "ACC-SUMMIT"])
def test_account_interactions_and_history(db, account_id):
    path, writer = db
    account, interactions, history = read_json(
        path,
        f"baec://accounts/{account_id}",
        f"baec://accounts/{account_id}/interactions",
        f"baec://accounts/{account_id}/transition-history",
    )
    repo = writer.repository
    assert account == expected(adapters.account_view(repo.get_account(account_id)))
    assert interactions == expected(adapters.interaction_list(repo.list_interactions(account_id)))
    assert history == expected(adapters.transition_history_list(repo.get_transition_history(account_id)))
    assert [i["text"] for i in interactions["interactions"]] == [i.text for i in repo.list_interactions(account_id)]
    assert len(history["transitions"]) == 1 and history["transitions"][0]["from_state"] is None


def test_one_interaction(db):
    path, writer = db
    (body,) = read_json(path, "baec://interactions/INT-HARBOR-001")
    stored = writer.repository.get_interaction("INT-HARBOR-001")
    assert body == expected(adapters.interaction_view(stored)) and body["text"] == stored.text


def test_all_baecs_are_composed_in_account_then_stored_order(db):
    path, writer = db
    (body,) = read_json(path, "baec://baecs")
    repo = writer.repository
    records = [r for a in repo.list_accounts() for r in repo.list_baec_records(a.account_id)]
    assert body == expected(adapters.baec_record_list(records))
    assert [r["baec_id"] for r in body["baec_records"]] == ["BAEC-HARBOR-001", "BAEC-MERIDIAN-001"]


def test_one_baec_preserves_threshold_text_and_classification(db):
    path, writer = db
    harbor, meridian = read_json(path, "baec://baecs/BAEC-HARBOR-001", "baec://baecs/BAEC-MERIDIAN-001")
    stored = writer.repository.get_baec_record("BAEC-HARBOR-001")
    assert harbor == expected(adapters.baec_record_view(stored))
    assert harbor["candidate"]["stringency"]["numeric_value"] == str(stored.candidate.stringency.numeric_value)
    assert harbor["candidate"]["stringency"]["verbatim_text"] == stored.candidate.stringency.verbatim_text
    assert harbor["classification"] == "CONFIRMED_BAEC" and harbor["confirmation"]["action"] == "CONFIRM_BAEC"
    assert meridian["classification"] != "CONFIRMED_BAEC" and meridian["classification_reason"]
    assert harbor["ai_derived_normalized_condition"] is None


def test_dormancy_judgments(db):
    path, writer = db
    harbor, meridian = read_json(
        path, "baec://baecs/BAEC-HARBOR-001/dormancy-judgments", "baec://baecs/BAEC-MERIDIAN-001/dormancy-judgments"
    )
    assert harbor == expected(adapters.dormancy_judgment_list(writer.repository.list_dormancy_judgments("BAEC-HARBOR-001")))
    assert len(harbor["dormancy_judgments"]) == 1 and meridian == {"dormancy_judgments": []}


def test_repeated_reads_are_identical(db):
    path, _ = db
    uris = ["baec://accounts", "baec://baecs", "baec://accounts/ACC-HARBOR/transition-history"]
    assert read_json(path, *uris) == read_json(path, *uris)


def test_no_resource_read_changes_the_database(db):
    path, writer = db
    before = writer.dump()
    read_json(
        path,
        "baec://accounts", "baec://accounts/ACC-HARBOR", "baec://accounts/ACC-HARBOR/interactions",
        "baec://accounts/ACC-HARBOR/transition-history", "baec://interactions/INT-HARBOR-001", "baec://baecs",
        "baec://baecs/BAEC-HARBOR-001", "baec://baecs/BAEC-HARBOR-001/dormancy-judgments",
    )
    assert writer.dump() == before


# --- invalid identifiers fail before any application read ----------------------------------

INVALID = [
    "baec://accounts/",
    "baec://accounts/bad%20id",
    "baec://accounts/ACC..1",
    "baec://accounts/%20ACC-HARBOR",
    "baec://accounts/ACC%01",
    "baec://interactions/.hidden",
    "baec://baecs/B%09/dormancy-judgments",
]


@pytest.mark.parametrize("uri", INVALID)
def test_invalid_identifiers_fail_before_any_read(db, monkeypatch, uri):
    path, _ = db
    calls = []
    for name in ("get_account", "get_interaction", "get_baec_record", "list_dormancy_judgments", "list_interactions"):
        real = getattr(ReadService, name)
        monkeypatch.setattr(ReadService, name, lambda self, *a, _real=real, _n=name: calls.append(_n) or _real(self, *a))
    error = read_error(path, uri)
    assert error.code == -32602
    assert calls == []


@pytest.mark.parametrize("uri", ["baec://accounts/a/b", "baec://accounts/..%2Fetc", "baec://unknown", "file:///etc/passwd"])
def test_unknown_or_traversal_uris_are_refused(db, uri):
    path, _ = db
    assert read_error(path, uri).code == -32602


# --- missing, corrupt, and unexpected failures --------------------------------------------

MISSING = [
    "baec://accounts/ACC-404",
    "baec://accounts/ACC-404/interactions",
    "baec://accounts/ACC-404/transition-history",
    "baec://interactions/INT-404",
    "baec://baecs/BAEC-404",
    "baec://baecs/BAEC-404/dormancy-judgments",
]


@pytest.mark.parametrize("uri", MISSING)
def test_a_missing_object_is_a_sanitized_resource_not_found(db, uri):
    path, _ = db
    error = read_error(path, uri)
    assert error.code == -32602
    assert str(error) == f"resource not found: {uri}"
    assert error.error.data == {"uri": uri}


def test_an_integrity_failure_is_a_generic_public_error(db):
    path, writer = db
    tamper(writer.connection, "UPDATE baec_records SET articulation_origin = 'SELLER_SEEDED' WHERE baec_id = 'BAEC-HARBOR-001'")
    error = read_error(path, "baec://baecs/BAEC-HARBOR-001")
    assert error.code == -32603 and str(error) == "the requested resource is unavailable"


def test_an_unexpected_failure_reveals_nothing_internal(db, monkeypatch):
    path, _ = db
    secret = "SECRET /private/var/db.sqlite3 row 7 SELECT * FROM accounts"

    def explode(self):
        raise RuntimeError(secret)

    monkeypatch.setattr(ReadService, "list_accounts", explode)
    error = read_error(path, "baec://accounts")
    text = f"{error} {error.error.data}"
    for leaked in ("SECRET", ".sqlite3", "row 7", "SELECT", "Traceback", "RuntimeError"):
        assert leaked not in text


# --- thread affinity ----------------------------------------------------------------------


def test_reads_run_on_the_thread_that_opened_the_read_connection(db, monkeypatch):
    path, _ = db
    seen = {}
    real_open = composition.open_read_connection
    real_read = ReadService.list_accounts

    def opening(database_path):
        seen["opened"] = threading.get_ident()
        return real_open(database_path)

    def reading(self):
        seen["read"] = threading.get_ident()
        return real_read(self)

    monkeypatch.setattr(composition, "open_read_connection", opening)
    monkeypatch.setattr(ReadService, "list_accounts", reading)
    read_json(path, "baec://accounts")
    assert seen["opened"] == seen["read"] == threading.get_ident()


def test_the_read_connection_keeps_sqlites_thread_check(db, monkeypatch):
    path, _ = db
    captured = {}
    real_open = composition.open_read_connection
    monkeypatch.setattr(composition, "open_read_connection", lambda p: captured.setdefault("c", real_open(p)))

    async def main():
        async with connected(path):
            errors = []

            def other_thread():
                try:
                    captured["c"].execute("SELECT 1")
                except sqlite3.ProgrammingError as error:
                    errors.append(error)

            thread = threading.Thread(target=other_thread)
            thread.start()
            thread.join()
            return errors

    errors = run(main)
    assert len(errors) == 1 and "thread" in str(errors[0])


# --- collection ordering: explicit, not incidental ---------------------------------------
# Repository.list_accounts() and list_baec_records() order by "ORDER BY rowid"
# (baec_app/data/repository.py), i.e. insertion order. The MCP collections keep
# exactly that guaranteed order rather than imposing another.


def test_collections_follow_the_repositorys_explicit_insertion_order_not_identifier_order(tmp_path):
    from baec_app.data.records import SourceInteraction
    from baec_app.domain.baec_rules import create_confirmed_baec_record
    from baec_app.domain.models import Account
    from tests.builders import HARBOR_QUOTE, NOW, candidate, confirm_auth, excerpt

    path = str(tmp_path / "ordering.sqlite3")
    writer = Writer(path)
    try:
        repo = writer.repository
        account_order = ["ACC-Z", "ACC-A", "ACC-M"]
        baec_order = {"ACC-Z": ["B-Z2", "B-Z1"], "ACC-A": [], "ACC-M": ["B-M9", "B-M1", "B-M5"]}
        for account_id in account_order:
            repo.add_account(Account(account_id, f"Synthetic {account_id}"))
            interaction_id = f"INT-{account_id}"
            repo.add_interaction(SourceInteraction(interaction_id, account_id, NOW, "Buyer: " + HARBOR_QUOTE))
            cited = excerpt(source_id=interaction_id)
            for baec_id in baec_order[account_id]:
                cand = candidate(account_id=account_id, source_interaction_id=interaction_id, source_excerpt=cited, criterion_evidence=cited)
                repo.save_confirmed_baec(create_confirmed_baec_record(cand, baec_id=baec_id, captured_at=NOW, confirmation=confirm_auth(baec_id)))
        before = writer.dump()
        accounts, baecs = read_json(path, "baec://accounts", "baec://baecs")
        again = read_json(path, "baec://accounts", "baec://baecs")
        assert writer.dump() == before
    finally:
        writer.close()
    assert [a["account_id"] for a in accounts["accounts"]] == account_order
    expected_baecs = [b for account_id in account_order for b in baec_order[account_id]]
    assert [r["baec_id"] for r in baecs["baec_records"]] == expected_baecs == ["B-Z2", "B-Z1", "B-M9", "B-M1", "B-M5"]
    assert expected_baecs != sorted(expected_baecs)  # proves the order is not identifier order
    assert again == [accounts, baecs]
