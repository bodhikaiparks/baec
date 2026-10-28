"""Phase 7E boundaries: Streamlit stays in baec_app/interfaces, and 7E adds no grant, BAEC, MCP-write, or state path."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
PACKAGES = ("application", "domain", "data", "mcp", "ai", "interfaces")


def _imports(path: Path) -> set[str]:
    names = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def _modules(package: str) -> list[Path]:
    return sorted((REPO / "baec_app" / package).rglob("*.py"))


@pytest.mark.parametrize("package", ["application", "domain", "data", "mcp", "ai"])
def test_streamlit_is_never_imported_outside_the_interface_layer(package):
    for path in _modules(package):
        assert not {n for n in _imports(path) if n == "streamlit" or n.startswith("streamlit.")}, path.name
    for path in sorted((REPO / "scripts").glob("*.py")):
        assert "streamlit" not in _imports(path), path.name


def test_streamlit_is_imported_only_by_the_review_page():
    importers = {path.relative_to(REPO).as_posix() for package in PACKAGES for path in _modules(package)
                 if any(n == "streamlit" or n.startswith("streamlit.") for n in _imports(path))}
    # Engine 1 public demo: the second human-interaction page (baec_app/interfaces/public_demo.py).
    assert importers == {"baec_app/interfaces/review_page.py", "baec_app/interfaces/public_demo.py"}


def test_the_interface_layer_reaches_neither_data_nor_ai_nor_mcp():
    for path in _modules("interfaces"):
        project = {n for n in _imports(path) if n.startswith("baec_app")}
        assert not {n for n in project if n.startswith(("baec_app.data", "baec_app.ai", "baec_app.mcp"))}, path.name


def test_streamlit_is_pinned_exactly_in_the_existing_manifest():
    lines = (REPO / "requirements-dev.txt").read_text(encoding="utf-8").split()
    assert "streamlit==1.65.0" in lines and [l for l in lines if l.startswith("streamlit")] == ["streamlit==1.65.0"]
    import streamlit
    assert streamlit.__version__ == "1.65.0"


# Phase 7F-B: grants and their execution exist, and only in these modules. Name kept for ID continuity.
GRANT_SURFACE = {
    "issue_grant": set(), "issue_confirmation_grant": set(), "request_grant": set(),
    "authorize_confirmation": {"baec_app/application/proposal_authorization.py"},
    "authorize_grant": {"baec_app/application/authority.py"},
    "GrantExecutionFacade": set(), "GrantExecutionStore": {"baec_app/data/authorization_grants.py"},  # 7G split
    "ConfirmationExecutionService": {"baec_app/application/proposal_authorization.py"},
    "AuthorizationGrantStore": {"baec_app/data/authorization_grants.py"},
    "insert_confirmed_record": {"baec_app/data/repository.py"},
}
GRANT_TABLE_USERS = {"baec_app/data/authorization_grants.py", "baec_app/data/database.py",
                     "baec_app/data/proposal_bridge.py"}


def test_no_grant_confirmation_or_mcp_write_surface_exists_yet():
    """Since 7G the write package exists (name kept for ID continuity); it defines no grant surface of its own."""
    assert (REPO / "baec_app" / "mcp_write").is_dir()
    for name, allowed in GRANT_SURFACE.items():
        definers = set()
        for package in PACKAGES + ("mcp_write",):
            for path in _modules(package):
                tree = ast.parse(path.read_text(encoding="utf-8"))
                if name in {node.name for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.ClassDef))}:
                    definers.add(path.relative_to(REPO).as_posix())
        assert definers == allowed, name
    writers = {path.relative_to(REPO).as_posix() for package in PACKAGES + ("mcp_write",) for path in _modules(package)
               if "human_authorization_grant" in path.read_text(encoding="utf-8").split('"""', 2)[-1]}
    assert writers == GRANT_TABLE_USERS


def test_the_phase7e_modules_reach_no_account_state_or_dormancy_operation():
    for path in (REPO / "baec_app" / "application" / "proposal_review.py", REPO / "baec_app" / "interfaces" / "review_page.py"):
        imported = _imports(path)
        assert "baec_app.domain.state_machine" not in imported and "baec_app.application.account_state" not in imported
        code = path.read_text(encoding="utf-8").split('"""', 2)[2]
        for forbidden in ("persist_transition", "record_dormancy_judgment", "move_to_", "staleness_status",
                          "save_confirmed_baec", "save_classification_record", "outreach"):
            assert forbidden not in code, (path.name, forbidden)
