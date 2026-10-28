"""PostgreSQL persistence (V0.4 Phase 3 and 3B): schema migrations, row-level security, tenant-scoped stores.

Product-owned data (identity, console store, CRM; Phase 3) and Engine 2 records (schema baec_engine2, Phase 3B) live
here. Engine 1 records have no PostgreSQL store, so a PostgreSQL deployment is not yet complete: see
docs/V04_PHASE3B_PRODUCTION_PERSISTENCE_REPORT.md.

psycopg is an optional dependency (requirements-postgres.txt). Nothing in the development SQLite path imports this
package; a module here that needs the driver raises PostgresUnavailable when it is missing.
"""


class PostgresError(Exception):
    """Base class for PostgreSQL configuration, migration, and storage refusals."""


class PostgresUnavailable(PostgresError):
    """The PostgreSQL driver is not installed."""


def require_driver():
    try:
        import psycopg
    except ImportError:
        raise PostgresUnavailable("PostgreSQL support needs psycopg: install requirements-postgres.txt") from None
    return psycopg
