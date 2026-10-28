"""V0.4 Phase 3: tenant context never outlives its transaction, and never crosses pooled requests."""

from __future__ import annotations

import threading

import pytest

from baec_console.postgres.database import (
    TenantContextError,
    TenantDatabase,
    act_for,
    current_organization,
    tenant_transaction,
)

from .conftest import AT, fresh, provision, psycopg


def _seed(storage, label: str) -> str:
    org = fresh(f"org-{label}")
    provision(storage, org, fresh(f"user-{label}"), f"Seller {label} (synthetic)")
    return org


@pytest.fixture
def one_connection_pool(migrated):
    """A pool with exactly one connection, so every request reuses the same server session."""
    with TenantDatabase(migrated["app"], min_size=1, max_size=1) as db:
        yield db


def _backend(connection) -> int:
    return connection.execute("SELECT pg_backend_pid()").fetchone()[0]


def _visible(connection) -> set[str]:
    return {r[0] for r in connection.execute("SELECT organization_id FROM baec.organizations").fetchall()}


def test_alternating_requests_on_one_pooled_connection_never_inherit_the_previous_tenant(storage,
                                                                                          one_connection_pool):
    a, b = _seed(storage, "a"), _seed(storage, "b")
    pids = set()
    for i in range(20):
        org = (a, b)[i % 2]
        with one_connection_pool.tenant(org) as connection:
            pids.add(_backend(connection))
            assert current_organization(connection) == org
            assert _visible(connection) == {org}
        with one_connection_pool.pool.connection() as raw:  # between requests: no tenant at all
            assert current_organization(raw) is None
            assert _visible(raw) == set()
    assert len(pids) == 1  # it really was the same server session every time


def test_a_session_wide_setting_is_cleared_before_the_connection_is_reused(storage, one_connection_pool):
    """Even if code inside a request wrongly sets the organization for the whole session (is_local => false), the
    pool's DISCARD ALL removes it before the next request."""
    a, b = _seed(storage, "a"), _seed(storage, "b")
    with one_connection_pool.tenant(a) as connection:
        connection.execute("SELECT set_config('baec.organization_id', %s, false)", (a,))
        pid = _backend(connection)
    with one_connection_pool.tenant(b) as connection:
        assert _backend(connection) == pid
        assert _visible(connection) == {b}
    with one_connection_pool.pool.connection() as raw:
        assert current_organization(raw) is None


def test_a_failed_request_leaves_no_tenant_behind(storage, one_connection_pool):
    a, b = _seed(storage, "a"), _seed(storage, "b")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with one_connection_pool.tenant(a) as connection:
            connection.execute("INSERT INTO baec.organizations (organization_id, name, created_at) "
                               "VALUES (%s, 'x', now())", (b,))
    with one_connection_pool.tenant(b) as connection:
        assert _visible(connection) == {b}


def test_a_connection_that_already_carries_an_organization_is_refused(storage, migrated):
    a = _seed(storage, "a")
    with psycopg.connect(migrated["app"], autocommit=True) as connection:
        connection.execute("SELECT set_config('baec.organization_id', %s, false)", (a,))
        with pytest.raises(TenantContextError, match="already carries"):
            with tenant_transaction(connection, a):
                pass


def test_tenant_context_is_set_only_inside_a_transaction_and_never_without_an_id(migrated):
    with psycopg.connect(migrated["app"], autocommit=True) as connection:
        with pytest.raises(TenantContextError, match="inside a transaction"):
            act_for(connection, "org-a")
        assert current_organization(connection) is None
        for bad in ("", "   ", None):
            with pytest.raises(TenantContextError):
                with tenant_transaction(connection, bad):
                    pass


def test_concurrent_requests_for_two_organizations_see_only_their_own(storage, migrated):
    a, b = _seed(storage, "a"), _seed(storage, "b")
    failures = []
    with TenantDatabase(migrated["app"], min_size=2, max_size=2) as db:
        def worker(org: str) -> None:
            for _ in range(25):
                with db.tenant(org) as connection:
                    seen = _visible(connection)
                    if seen != {org}:
                        failures.append((org, seen))
        threads = [threading.Thread(target=worker, args=(org,)) for org in (a, b, a, b)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    assert failures == []


def test_writes_in_one_tenant_transaction_commit_or_roll_back_together(storage):
    org = _seed(storage, "a")
    with pytest.raises(RuntimeError):
        with storage.session(org, "local") as s:
            s.console.add_ownership("ACC-ROLLED-BACK", org, AT, s.identity.users(org)[0].user_id)
            raise RuntimeError("request failed after a write")
    with storage.session(org, "local") as s:
        assert s.console.organization_of("ACC-ROLLED-BACK") is None
