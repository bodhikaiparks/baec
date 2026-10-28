"""Phase 7F-B boundaries: who may issue grants, who may execute them, and how narrow the execution path is."""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

from baec_app.application.proposal_authorization import ConfirmationExecutionService, ProposalAuthorizationService
from baec_app.data.authorization_grants import AuthorizationGrantStore, GrantExecutionStore

REPO = Path(__file__).resolve().parents[1]
GRANT_MODULES = ("baec_app.application.proposal_authorization", "baec_app.data.authorization_grants")


def _imports(path: Path) -> set[str]:
    names = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def _calls(path: Path) -> set[str]:
    return {node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))) if isinstance(node, ast.Call)}


def _files(*parts: str) -> list[Path]:
    root = REPO.joinpath(*parts)
    return sorted(root.rglob("*.py")) if root.is_dir() else [root.with_suffix(".py")]


def test_the_ai_package_mapper_normalizer_and_mcp_core_cannot_issue_grants():
    for path in (_files("baec_app", "ai") + _files("baec_app", "mcp")
                 + _files("baec_app", "application", "ai_proposal_mapping")
                 + _files("baec_app", "application", "human_normalization")):
        assert not _imports(path) & set(GRANT_MODULES), path.name
        assert not _calls(path) & {"authorize_confirmation", "add_grant", "authorize_grant", "execute"}, path.name
    for path in _files("baec_app", "mcp_write"):  # since 7G: it may execute a grant, never issue one
        assert "baec_app.data.authorization_grants" not in _imports(path), path.name
        assert not _calls(path) & {"authorize_confirmation", "add_grant", "authorize_grant", "issue"}, path.name


def test_review_rendering_and_accept_cannot_issue_grants():
    review = REPO / "baec_app" / "application" / "proposal_review.py"
    assert not _imports(review) & set(GRANT_MODULES) and "authorize_grant" not in _calls(review)
    assert "authorize_confirmation" not in _calls(review)


def test_the_page_may_request_a_grant_but_never_execute_or_write_one():
    page = REPO / "baec_app" / "interfaces" / "review_page.py"
    assert "baec_app.application.proposal_authorization" in _imports(page)
    assert "baec_app.data.authorization_grants" not in _imports(page)
    assert not {"execute", "add_grant", "record_confirmation", "insert_confirmed_record"} & _calls(page)
    tree = ast.parse(page.read_text(encoding="utf-8"))
    imported = {alias.name for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
                and node.module == "baec_app.application.proposal_authorization" for alias in node.names}
    assert imported == {"AuthorizationRefused", "ProposalAuthorizationService"}


def test_the_executor_holds_no_broad_repository_and_offers_only_execute(tmp_path):
    from baec_app.data.database import open_database
    from tests.application_builders import FixedClock
    connection = open_database(str(tmp_path / "x.sqlite3"))
    try:
        executor = ConfirmationExecutionService(connection, clock=FixedClock())
        # Since 7G the executor holds only the execution-only grant capability, never the issuance-capable store.
        assert {type(v).__name__ for v in vars(executor).values()} == {"Connection", "GrantExecutionStore",
                                                                       "FixedClock"}
        assert {n for n in dir(GrantExecutionStore) if not n.startswith("_")} == {
            "get_grant", "lifecycle", "record_confirmation"}
        assert {n for n in dir(ConfirmationExecutionService) if not n.startswith("_")} == {"execute"}
        assert {n for n in dir(ProposalAuthorizationService) if not n.startswith("_")} == {
            "authorize_confirmation", "authorization_status"}
        assert {n for n in dir(AuthorizationGrantStore) if not n.startswith("_")} == {
            "add_grant", "record_confirmation", "get_grant", "grants_for_revision", "grants_for_proposal", "lifecycle"}
    finally:
        connection.close()
    source = inspect.getsource(ConfirmationExecutionService)
    for forbidden in ("save_confirmed_baec", "persist_transition", "record_dormancy_judgment", "add_account",
                      "staleness", "HumanConfirmationGate", "request_from_proposal", "UPDATE accounts"):
        assert forbidden not in source, forbidden
    assert "insert_confirmed_record(" in source and "authority.authorize_grant(grant)" in source


def test_the_narrow_write_primitive_writes_only_the_confirmed_record():
    from baec_app.data import repository
    source = inspect.getsource(repository.insert_confirmed_record)
    assert "_insert_record(record)" in source and "in_transaction" in source
    for forbidden in ("persist_transition", "accounts", "dormancy", "staleness", "COMMIT", "transaction("):
        assert forbidden not in source.split('"""')[-1], forbidden


def test_grant_tables_are_written_only_by_the_grant_store():
    writers = set()
    for path in _files("baec_app"):
        code = path.read_text(encoding="utf-8")
        if "INSERT INTO human_authorization_grants" in code or "INSERT INTO human_authorization_grant_consumptions" in code \
                or "INSERT INTO ai_proposal_confirmations" in code:
            writers.add(path.relative_to(REPO).as_posix())
    assert writers == {"baec_app/data/authorization_grants.py"}
