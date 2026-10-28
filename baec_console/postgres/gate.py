"""The production database checks: run against the application role's connection before anything else is opened.

check_database() passes only if:
  * the URL is a PostgreSQL URL that requires verified TLS (sslmode=verify-full), the driver is installed, and the
    database answers; and both connections (application and sign-in lookup) really use TLS, as the client and the
    server each report it (transport_failures; V0.4 Phase 5);
  * every migration this code has is applied, with unchanged checksums, and no other is recorded;
  * the connecting role is not a superuser, cannot bypass RLS, replicate, or create roles or databases, belongs to no
    role that can, has a database-side connection limit (V0.4 Phase 5), owns no BAEC object, cannot create objects
    in the BAEC schemas, and has no DELETE, TRUNCATE, REFERENCES, or TRIGGER privilege on a tenant table (TRUNCATE
    skips row-level security);
  * every table with an organization_id column is exactly the expected tenant set, and each has RLS enabled and
    forced, with the tenant_isolation (permissive) and tenant_boundary (restrictive) policies on the expected
    predicate; Engine 2 tables (schema baec_engine2) also have exactly the workspace_scope (restrictive) policy;
  * the application role can only read Engine 2's store metadata (engine2_meta), and cannot set
    session_replication_role (which would stop every trigger from firing);
  * the Engine 2 schema is exactly the validated one (engine2_failures): the tables Engine 2 defines, exactly the
    expected triggers (the 92 Engine 2 defines for SQLite plus the PostgreSQL-only additions), each enabled and with
    the expected definition, every BAEC function with exactly the body its migration created, and the engine2_meta
    rows. A mismatch there is reported as Engine2SchemaRefused, with one safe message;
  * the Engine 1 schema is exactly the validated one (engine1_failures): the 25 tables Engine 1 defines, exactly one
    PostgreSQL trigger for each of the 92 SQLite triggers (derived from Engine 1's own schema statements) plus the
    PostgreSQL-only additions, each enabled and with the expected definition, every Engine 1 function with exactly
    its migration's body. Reported as Engine1SchemaRefused;
  * every BAEC schema (product, Engine 1, Engine 2, migrations) matches the validated catalog manifest
    (catalog.py): tables, column types and defaults, PRIMARY/FOREIGN KEY, UNIQUE and CHECK definitions, indexes,
    triggers and their enabled state, RLS flags and policies, functions (body, volatility, SECURITY DEFINER), and
    the connecting role's effective table, column, schema, and function privileges. A product-schema difference is
    reported as ProductSchemaRefused;
  * the sign-in schema (baec_auth, migration 0005) matches the manifest, its functions are exactly the migration's,
    its policies apply to exactly the roles they name, and the sign-in lookup role (the second URL) is exactly as
    narrow as designed: it is baec_auth_lookup, has no role attribute or membership, owns nothing, may use only the
    baec_auth schema, may read only the five lookup columns of auth_identities, and sees no row without an exact
    issuer and subject (auth_failures, auth_lookup_failures). Reported as AuthSchemaRefused.
It reports every failure at once. It never changes a privilege, a policy, or the schema: an unsafe database is
refused, not repaired.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from baec_app.data import database as engine1
from baec_app.engine2 import persistence as engine2

from . import PostgresError, require_driver
from . import catalog
from .database import DatabaseSettings, connect, require_verified_tls, validate_database_url
from .migrate import expected_versions, migrations, recorded

TENANT_TABLES = ("organizations", "users", "identity_audit", "account_ownership", "account_meta", "interaction_meta",
                 "condition_drafts", "draft_resolutions", "preparation_requests", "preparations", "preparation_edits",
                 "preparation_reviews", "preparation_provenance", "preparation_blocks", "events", "attributions",
                 "crm_contacts", "crm_tasks", "crm_interaction_links", "crm_activities")
# Engine 2's own schema definition is the source of truth for its tables and triggers (baec_app/engine2/persistence.py).
ENGINE2_STORE_METADATA = "engine2_meta"
ENGINE2_TABLES = tuple(t for t in engine2.TABLES if t != ENGINE2_STORE_METADATA)
# Engine 1's own schema definition is the source of truth for its tables (baec_app/data/database.py).
ENGINE1_TABLES = engine1.ALL_TABLES
SCHEMAS = ("baec", "baec_auth", "baec_engine1", "baec_engine2", "baec_migrations")
TENANT_PREDICATE = "(organization_id = baec.current_organization())"
WORKSPACE_PREDICATE = "(workspace = baec.current_workspace())"
_FORBIDDEN_TABLE_PRIVILEGES = ("DELETE", "TRUNCATE", "REFERENCES", "TRIGGER")


class DatabaseRefused(PostgresError):
    """The database is not safe for the application to use. Lists every failed check; never the URL."""

    def __init__(self, failures: list[str]) -> None:
        self.failures = failures
        super().__init__("the production database was refused: " + "; ".join(failures))


ENGINE2_SCHEMA_MISMATCH = ("Production storage schema does not match the validated BAEC Engine 2 PostgreSQL schema. "
                           "BAEC Console is closed. Nothing was read or changed.")


class Engine2SchemaRefused(DatabaseRefused):
    """The Engine 2 schema objects differ from the validated ones. The message is fixed; `failures` names the
    objects (schema object names only: never a URL, credential, or row)."""

    def __init__(self, failures: list[str]) -> None:
        super().__init__(failures)
        self.args = (ENGINE2_SCHEMA_MISMATCH,)

    def __str__(self) -> str:
        return ENGINE2_SCHEMA_MISMATCH


ENGINE1_SCHEMA_MISMATCH = ("Production storage schema does not match the validated BAEC Engine 1 PostgreSQL schema. "
                           "BAEC Console is closed. Nothing was read or changed.")
PRODUCT_SCHEMA_MISMATCH = ("Production storage schema does not match the validated BAEC product PostgreSQL schema. "
                           "BAEC Console is closed. Nothing was read or changed.")


class Engine1SchemaRefused(Engine2SchemaRefused):
    """The Engine 1 schema objects differ from the validated ones. Fixed message; `failures` names the objects."""

    def __init__(self, failures: list[str]) -> None:
        DatabaseRefused.__init__(self, failures)
        self.args = (ENGINE1_SCHEMA_MISMATCH,)

    def __str__(self) -> str:
        return ENGINE1_SCHEMA_MISMATCH


AUTH_SCHEMA_MISMATCH = ("Production sign-in storage does not match the validated BAEC sign-in schema and lookup role. "
                        "BAEC Console is closed. Nothing was read or changed.")


class ProductSchemaRefused(Engine2SchemaRefused):
    """The product schema objects differ from the validated ones. Fixed message; `failures` names the objects."""

    def __init__(self, failures: list[str]) -> None:
        DatabaseRefused.__init__(self, failures)
        self.args = (PRODUCT_SCHEMA_MISMATCH,)

    def __str__(self) -> str:
        return PRODUCT_SCHEMA_MISMATCH


class AuthSchemaRefused(Engine2SchemaRefused):
    """The sign-in schema or the sign-in lookup role differs from the validated one. Fixed message; `failures` names
    the objects and privileges."""

    def __init__(self, failures: list[str]) -> None:
        DatabaseRefused.__init__(self, failures)
        self.args = (AUTH_SCHEMA_MISMATCH,)

    def __str__(self) -> str:
        return AUTH_SCHEMA_MISMATCH


@dataclass(frozen=True)
class DatabaseReport:
    role: str
    schema_versions: tuple[int, ...]
    tenant_tables: tuple[str, ...]
    # The database-side connection limits (pg_roles.rolconnlimit) of the application and sign-in lookup roles.
    connection_limits: tuple[int, int] = (-1, -1)


def transport_failures(db, label: str) -> list[str]:
    """How a live connection falls short of verified TLS: the client must report TLS in use and the server must
    report this backend as a TLS connection. (Certificate and host-name verification are libpq's, required by
    sslmode=verify-full: a connection that failed them never opened.)"""
    failures = []
    if not db.pgconn.ssl_in_use:
        failures.append(f"the {label} connection does not use TLS")
    row = db.execute("SELECT ssl FROM pg_catalog.pg_stat_ssl WHERE pid = pg_catalog.pg_backend_pid()").fetchone()
    if not (row and row[0]):
        failures.append(f"the server does not report TLS for the {label} connection")
    return failures


def _connection_limit(db) -> int:
    return db.execute("SELECT rolconnlimit FROM pg_roles WHERE rolname = current_user").fetchone()[0]


def _role_failures(db) -> list[str]:
    failures = []
    row = db.execute("SELECT rolsuper, rolbypassrls, rolcreaterole, rolcreatedb, rolreplication FROM pg_roles "
                     "WHERE rolname = current_user").fetchone()
    for flag, label in zip(row, ("is a superuser", "can bypass row-level security", "can create roles",
                                 "can create databases", "can replicate")):
        if flag:
            failures.append(f"the application role {label}")
    if _connection_limit(db) < 0:
        failures.append("the application role has no connection limit (set one with ALTER ROLE ... CONNECTION LIMIT)")
    inherited = db.execute("SELECT count(*) FROM pg_roles r WHERE r.rolname <> current_user "
                           "AND (r.rolsuper OR r.rolbypassrls) AND pg_has_role(current_user, r.oid, 'MEMBER')"
                           ).fetchone()[0]
    if inherited:
        failures.append("the application role is a member of a superuser or RLS-bypassing role")
    if db.execute("SELECT count(*) FROM pg_auth_members WHERE member = (SELECT oid FROM pg_roles "
                  "WHERE rolname = current_user)").fetchone()[0]:
        failures.append("the application role is a member of another role")
    owned = db.execute("SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                       "WHERE n.nspname = ANY(%s) AND pg_has_role(current_user, c.relowner, 'MEMBER')",
                       (list(SCHEMAS),)).fetchone()[0]
    owned += db.execute("SELECT count(*) FROM pg_namespace WHERE nspname = ANY(%s) "
                        "AND pg_has_role(current_user, nspowner, 'MEMBER')", (list(SCHEMAS),)).fetchone()[0]
    if owned:
        failures.append("the application role owns BAEC schema objects (an owner can alter or disable policies)")
    for schema in (*SCHEMAS, "public"):
        if db.execute("SELECT has_schema_privilege(current_user, %s, 'CREATE')", (schema,)).fetchone()[0]:
            failures.append(f"the application role can create objects in schema {schema}")
    for privilege in ("INSERT", "UPDATE", "DELETE", "TRUNCATE"):
        if db.execute("SELECT has_table_privilege(current_user, 'baec_migrations.schema_migrations', %s)",
                      (privilege,)).fetchone()[0]:
            failures.append(f"the application role has {privilege} on schema_migrations")
    if db.execute("SELECT has_parameter_privilege(current_user, 'session_replication_role', 'SET')").fetchone()[0]:
        failures.append("the application role can set session_replication_role (it would disable every trigger)")
    if db.execute("SELECT has_table_privilege(current_user, 'baec.operator_log', 'SELECT')").fetchone()[0]:
        failures.append("the application role can read the operator log")
    # Only tables that exist: a missing table is reported by the table checks, and has_table_privilege would raise.
    for table in filter(lambda t: _exists(db, t), _tenant_tables()):
        for privilege in _FORBIDDEN_TABLE_PRIVILEGES:
            if db.execute("SELECT has_table_privilege(current_user, %s, %s)", (table, privilege)).fetchone()[0]:
                failures.append(f"the application role has {privilege} on {table.removeprefix('baec.')}")
    for privilege in ("INSERT", "UPDATE", "DELETE", "TRUNCATE") if _exists(db, "baec_engine2.engine2_meta") else ():
        if db.execute("SELECT has_table_privilege(current_user, 'baec_engine2.engine2_meta', %s)",
                      (privilege,)).fetchone()[0]:
            failures.append(f"the application role has {privilege} on baec_engine2.engine2_meta")
    return failures


def _exists(db, table: str) -> bool:
    return db.execute("SELECT to_regclass(%s) IS NOT NULL", (table,)).fetchone()[0]


def _tenant_tables() -> list[str]:
    return ([f"baec.{t}" for t in TENANT_TABLES] + [f"baec_engine1.{t}" for t in ENGINE1_TABLES]
            + [f"baec_engine2.{t}" for t in ENGINE2_TABLES])


def _policy_failures(db) -> list[str]:
    failures = []
    tenant = {"tenant_isolation": (True, "*", TENANT_PREDICATE, TENANT_PREDICATE, True),
              "tenant_boundary": (False, "*", TENANT_PREDICATE, TENANT_PREDICATE, True)}
    workspace = {**tenant, "workspace_scope": (False, "*", WORKSPACE_PREDICATE, WORKSPACE_PREDICATE, True)}
    for schema, expected_tables, expected in (("baec", TENANT_TABLES, tenant),
                                              ("baec_engine1", ENGINE1_TABLES, workspace),
                                              ("baec_engine2", ENGINE2_TABLES, workspace)):
        found = {r[0] for r in db.execute(
            "SELECT c.relname FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid "
            "JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = %s AND c.relkind = 'r' "
            "AND a.attname = 'organization_id' AND NOT a.attisdropped", (schema,)).fetchall()}
        prefix = "" if schema == "baec" else f"{schema}."
        if found != set(expected_tables):
            failures.append(f"tenant tables in schema {schema} differ from the expected set "
                            f"(unexpected: {sorted(found - set(expected_tables))}, "
                            f"missing: {sorted(set(expected_tables) - found)})")
        for table in sorted(found & set(expected_tables)):
            enabled, forced = db.execute("SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                                         "WHERE oid = %s::regclass", (f"{schema}.{table}",)).fetchone()
            if not (enabled and forced):
                failures.append(f"row-level security is not enabled and forced on {prefix}{table}")
            policies = {r[0]: r[1:] for r in db.execute(
                "SELECT polname, polpermissive, polcmd, pg_get_expr(polqual, polrelid), "
                "pg_get_expr(polwithcheck, polrelid), polroles = '{0}' FROM pg_policy WHERE polrelid = %s::regclass",
                (f"{schema}.{table}",)).fetchall()}
            if policies != expected:
                failures.append(f"the row-level security policies on {prefix}{table} are not the expected tenant "
                                f"policies")
    return failures


# --- Engine 2 schema integrity ------------------------------------------------------------------------------------

_SQLITE_TRIGGER = re.compile(r"CREATE TRIGGER (\w+) BEFORE (INSERT|UPDATE|DELETE) ON (\w+) ")
_FUNCTION = re.compile(r"CREATE (?:OR REPLACE )?FUNCTION (\w+)\.(\w+)\(([\w, ]*)\) RETURNS ([\w ]+?)\s+LANGUAGE (\w+)\s+"
                       r"(?:(?:STABLE|IMMUTABLE)\s+)?AS \$\$(.*?)\$\$;", re.S)


def _trigger_definition(name: str, timing_event: str, table: str, level: str, function: str,
                        arguments: tuple[str, ...] = ()) -> str:
    """The text pg_get_triggerdef() returns for a trigger created as migration 0003 creates it."""
    args = ", ".join("'" + a.replace("'", "''") + "'" for a in arguments)
    return (f"CREATE TRIGGER {name} {timing_event} ON baec_engine2.{table} FOR EACH {level} "
            f"EXECUTE FUNCTION baec_engine2.{function}({args})")


def expected_engine2_triggers() -> dict[str, str]:
    """Every trigger the validated Engine 2 schema has, name -> definition.

    The 92 SQLite triggers come from Engine 2's own schema (persistence._schema_statements and its key map), so this
    set changes only when Engine 2's definition changes. Each maps to the PostgreSQL function that reproduces it
    (migration 0003). PostgreSQL adds two kinds SQLite has no counterpart for: <table>_no_truncate on every table, and
    audit_events_assign_event_id (SQLite's INTEGER PRIMARY KEY numbering)."""
    expected = {}
    for statement in engine2._schema_statements():
        match = _SQLITE_TRIGGER.match(statement)
        if not statement.startswith("CREATE TRIGGER"):
            continue
        if match is None:
            raise DatabaseRefused([f"an Engine 2 trigger definition could not be read: {statement[:60]}"])
        name, event, table = match.groups()
        if name.endswith(("_no_update", "_no_delete")):
            function, arguments = "refuse_change", ()
        elif name.endswith("_no_replace"):
            function, arguments = (("refuse_meta_replace", ()) if table == ENGINE2_STORE_METADATA
                                   else ("refuse_replace", engine2._KEYS[table]))
        elif name.endswith("_needs_activation"):
            function, arguments = "require_activation", ()
        else:
            raise DatabaseRefused([f"Engine 2 defines trigger {name}, which has no validated PostgreSQL equivalent"])
        expected[name] = _trigger_definition(name, f"BEFORE {event}", table, "ROW", function, arguments)
    for table in engine2.TABLES:
        name = f"{table}_no_truncate"
        expected[name] = _trigger_definition(name, "BEFORE TRUNCATE", table, "STATEMENT", "refuse_change")
    name = "audit_events_assign_event_id"
    expected[name] = _trigger_definition(name, "BEFORE INSERT", "audit_events", "ROW", "assign_event_id")
    return expected


def expected_functions(schemas: tuple[str, ...] = ("baec", "baec_engine2")) -> dict[str, tuple[str, str, str]]:
    """Every function in `schemas`, as its migration created it: name -> (returns, language, body). Read from the
    migration files the schema checksum check already pins; a later migration's CREATE OR REPLACE FUNCTION wins."""
    found = {}
    for migration in migrations():
        definitions = _FUNCTION.findall(migration.sql)
        if len(definitions) != len(re.findall(r"CREATE (?:OR REPLACE )?FUNCTION", migration.sql)):
            raise DatabaseRefused([f"the function definitions of migration {migration.version} could not be read"])
        for schema, name, _arguments, returns, language, body in definitions:
            if schema in schemas:
                found[f"{schema}.{name}"] = (returns, language, body)
    return found


def _function_failures(db, schemas: tuple[str, ...]) -> list[str]:
    failures = []
    functions = {f"{schema}.{name}": (returns, language, body, definer) for schema, name, returns, language, body,
                 definer in db.execute(
        "SELECT n.nspname, p.proname, pg_catalog.format_type(p.prorettype, NULL), l.lanname, p.prosrc, p.prosecdef "
        "FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace JOIN pg_language l ON l.oid = p.prolang "
        "WHERE n.nspname = ANY(%s)", (list(schemas),)).fetchall()}
    expected = expected_functions(schemas)
    for name, (returns, language, body) in sorted(expected.items()):
        if name not in functions:
            failures.append(f"function {name} is missing")
        elif functions[name][:3] != (returns, language, body):
            failures.append(f"function {name} differs from its migration")
        elif functions[name][3]:
            failures.append(f"function {name} is SECURITY DEFINER")
    for name in sorted(set(functions) - set(expected)):
        failures.append(f"unexpected function {name}")
    return failures


def engine2_failures(db, catalog_snapshot: dict | None = None) -> list[str]:
    """Differences between the live Engine 2 schema objects and the validated ones. Never repairs anything.
    `catalog_snapshot` is a catalog snapshot already taken for this check (catalog.snapshot), or None to take one."""
    failures = []
    tables = {r[0] for r in db.execute("SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                                       "WHERE n.nspname = 'baec_engine2' AND c.relkind = 'r'").fetchall()}
    for table in sorted(set(engine2.TABLES) - tables):
        failures.append(f"Engine 2 table baec_engine2.{table} is missing")
    for table in sorted(tables - set(engine2.TABLES)):
        failures.append(f"unexpected table baec_engine2.{table}")
    live = {name: (definition, enabled) for name, definition, enabled in db.execute(
        "SELECT t.tgname, pg_get_triggerdef(t.oid), t.tgenabled FROM pg_trigger t "
        "JOIN pg_class c ON c.oid = t.tgrelid JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'baec_engine2' AND NOT t.tgisinternal").fetchall()}
    for name, definition in sorted(expected_engine2_triggers().items()):
        if name not in live:
            failures.append(f"Engine 2 trigger {name} is missing")
        elif live[name][0] != definition:
            failures.append(f"Engine 2 trigger {name} differs from its validated definition")
        elif live[name][1] != "O":  # 'D' disabled; 'R' / 'A' fire only under a changed session_replication_role
            failures.append(f"Engine 2 trigger {name} is not enabled for ordinary sessions")
    for name in sorted(set(live) - set(expected_engine2_triggers())):
        failures.append(f"unexpected Engine 2 trigger {name}")
    failures += _function_failures(db, ("baec", "baec_engine2"))
    if ENGINE2_STORE_METADATA in tables:
        meta = dict(db.execute("SELECT key, value FROM baec_engine2.engine2_meta").fetchall())
        if meta != {"engine": engine2.ENGINE, "schema_version": str(engine2.SCHEMA_VERSION)}:
            failures.append("Engine 2 store metadata (engine2_meta) differs from the validated rows")
    return failures + catalog.failures(db, ("baec_engine2",), catalog_snapshot)


# --- Engine 1 schema integrity --------------------------------------------------------------------------------------

_SQLITE_ENGINE1_TRIGGER = re.compile(r"\s*CREATE TRIGGER (\w+)\s+(BEFORE|AFTER) (INSERT|UPDATE|DELETE)(?: OF ([\w, ]+))? "
                                     r"ON (\w+)")
# Engine 1's six SQLite rowid ids (INTEGER PRIMARY KEY, read back by the repository): assigned in PostgreSQL by
# f9_<table>_assign_id, and marked # in the no_replace keys (while unassigned they read as -1, as in SQLite).
ENGINE1_ROWID_KEYS = {"interaction_evidence": "evidence_id", "human_authorizations": "authorization_id",
                      "dormancy_judgments": "judgment_id", "evaluation_evidence": "evaluation_evidence_id",
                      "non_evaluation_evidence": "non_evaluation_evidence_id",
                      "account_state_transitions": "transition_id"}
_ENGINE1_KEYS = engine1.REPLACE_GUARDED_KEYS | engine1.AI_REPLACE_GUARDED_KEYS | engine1.BRIDGE_REPLACE_GUARDED_KEYS


def _engine1_trigger_definition(name: str, timing_event: str, table: str, function: str,
                                arguments: tuple[str, ...] = (), when: str = "", level: str = "ROW") -> str:
    args = ", ".join("'" + a.replace("'", "''") + "'" for a in arguments)
    return (f"CREATE TRIGGER {name} {timing_event} ON baec_engine1.{table} FOR EACH {level} {when}"
            f"EXECUTE FUNCTION baec_engine1.{function}({args})")


def engine1_sqlite_triggers() -> list[tuple[str, str, str, str | None, str]]:
    """(name, timing, event, OF columns, table) of every trigger Engine 1's SQLite schema creates, in creation
    order (baec_app/data/database.py, the source that creates the SQLite triggers)."""
    found = []
    for statement in engine1._schema_statements():
        if "CREATE TRIGGER" not in statement:
            continue
        match = _SQLITE_ENGINE1_TRIGGER.match(statement)
        if match is None:
            raise DatabaseRefused([f"an Engine 1 trigger definition could not be read: {statement.strip()[:60]}"])
        found.append(match.groups())
    return found


def engine1_trigger_names() -> dict[str, str]:
    """SQLite trigger name -> its PostgreSQL trigger name. SQLite fires the BEFORE INSERT triggers of one table newest
    first; the PostgreSQL name f<k>_<name> (k = that firing position) makes PostgreSQL's alphabetical order the same."""
    triggers = engine1_sqlite_triggers()
    names = {}
    for name, timing, event, _columns, table in triggers:
        if (timing, event) != ("BEFORE", "INSERT"):
            names[name] = name
            continue
        same = [n for n, t, e, _, tb in triggers if (t, e, tb) == ("BEFORE", "INSERT", table)]
        names[name] = f"f{list(reversed(same)).index(name) + 1}_{name}"
    return names


def expected_engine1_triggers() -> dict[str, str]:
    """Every trigger the validated Engine 1 schema has: PostgreSQL name -> definition.

    One for each of the 92 SQLite triggers, so this set changes only when Engine 1's definition changes: G1
    (_no_update, _no_delete) -> refuse_change, G2 (_no_replace) -> refuse_replace with the table's UNIQUE keys,
    every other trigger -> the function of the same name (migration 0004). PostgreSQL adds <table>_no_truncate on
    every table and f9_<table>_assign_id for the six rowid ids."""
    expected = {}
    pg_names = engine1_trigger_names()
    for name, timing, event, columns, table in engine1_sqlite_triggers():
        pg_name = pg_names[name]
        if name.endswith(("_no_update", "_no_delete")) and table != "baec_records":
            definition = _engine1_trigger_definition(pg_name, f"{timing} {event}", table, "refuse_change")
        elif name.endswith("_no_replace") and table != "ai_proposal_review_decisions":
            rowid = ENGINE1_ROWID_KEYS.get(table)
            keys = tuple(",".join(("#" + c) if c == rowid else c for c in key) for key in _ENGINE1_KEYS[table])
            definition = _engine1_trigger_definition(pg_name, f"{timing} {event}", table, "refuse_replace", keys)
        elif columns:  # baec_records_protected_no_update: the protected columns, and the tenancy columns
            of = ", ".join([c.strip() for c in columns.split(",")] + ["organization_id", "workspace"])
            definition = _engine1_trigger_definition(pg_name, f"{timing} {event} OF {of}", table, name)
        elif name == "ai_proposal_review_decisions_reject_supersedes":
            definition = _engine1_trigger_definition(pg_name, f"{timing} {event}", table, name,
                                                     when="WHEN ((new.decision = 'REJECTED'::text)) ")
        else:
            definition = _engine1_trigger_definition(pg_name, f"{timing} {event}", table, name)
        expected[pg_name] = definition
    for table in ENGINE1_TABLES:
        name = f"{table}_no_truncate"
        expected[name] = _engine1_trigger_definition(name, "BEFORE TRUNCATE", table, "refuse_change",
                                                     level="STATEMENT")
    for table, column in ENGINE1_ROWID_KEYS.items():
        name = f"f9_{table}_assign_id"
        expected[name] = _engine1_trigger_definition(name, "BEFORE INSERT", table, "assign_rowid", (column,))
    return expected


def engine1_failures(db, catalog_snapshot: dict | None = None) -> list[str]:
    """Differences between the live Engine 1 schema objects and the validated ones. Never repairs anything."""
    failures = []
    tables = {r[0] for r in db.execute("SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                                       "WHERE n.nspname = 'baec_engine1' AND c.relkind = 'r'").fetchall()}
    for table in sorted(set(ENGINE1_TABLES) - tables):
        failures.append(f"Engine 1 table baec_engine1.{table} is missing")
    for table in sorted(tables - set(ENGINE1_TABLES)):
        failures.append(f"unexpected table baec_engine1.{table}")
    live = {name: (definition, enabled) for name, definition, enabled in db.execute(
        "SELECT t.tgname, pg_get_triggerdef(t.oid), t.tgenabled FROM pg_trigger t "
        "JOIN pg_class c ON c.oid = t.tgrelid JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'baec_engine1' AND NOT t.tgisinternal").fetchall()}
    expected = expected_engine1_triggers()
    for name, definition in sorted(expected.items()):
        if name not in live:
            failures.append(f"Engine 1 trigger {name} is missing")
        elif live[name][0] != definition:
            failures.append(f"Engine 1 trigger {name} differs from its validated definition")
        elif live[name][1] != "O":
            failures.append(f"Engine 1 trigger {name} is not enabled for ordinary sessions")
    for name in sorted(set(live) - set(expected)):
        failures.append(f"unexpected Engine 1 trigger {name}")
    failures += _function_failures(db, ("baec_engine1",))
    return failures + catalog.failures(db, ("baec_engine1",), catalog_snapshot)


def product_failures(db, catalog_snapshot: dict | None = None) -> list[str]:
    """Differences between the live product schema (baec, baec_migrations) and the validated catalog manifest."""
    return catalog.failures(db, ("baec", "baec_migrations"), catalog_snapshot)


# --- sign-in schema and lookup role (V0.4 Phase 4) -------------------------------------------------------------------

AUTH_LOOKUP_ROLE = "baec_auth_lookup"
AUTH_PROVISIONING_ROLE = "baec_provisioner"
AUTH_TABLE = "baec_auth.auth_identities"
# The only columns the sign-in lookup role may read (grant in migration 0005).
AUTH_LOOKUP_COLUMNS = ("issuer", "subject", "organization_id", "user_id", "revoked_at")
AUTH_SUBJECT_PREDICATE = "((issuer = baec_auth.current_issuer()) AND (subject = baec_auth.current_subject()))"
# Policy name -> (permissive, command, roles, USING, WITH CHECK), as pg_policy and pg_get_expr render them.
AUTH_POLICIES = {
    "subject_lookup": (True, "r", [AUTH_LOOKUP_ROLE], AUTH_SUBJECT_PREDICATE, None),
    "subject_boundary": (False, "r", [AUTH_LOOKUP_ROLE], AUTH_SUBJECT_PREDICATE, None),
    "provisioning": (True, "*", [AUTH_PROVISIONING_ROLE], TENANT_PREDICATE, TENANT_PREDICATE),
    "provisioning_boundary": (False, "*", [AUTH_PROVISIONING_ROLE], TENANT_PREDICATE, TENANT_PREDICATE),
}
_COLUMN_PRIVILEGES = ("SELECT", "INSERT", "UPDATE", "REFERENCES")


def auth_failures(db, catalog_snapshot: dict | None = None) -> list[str]:
    """Differences between the live sign-in schema and the validated one, seen as the application role (which must
    have no privilege there; the manifest records that). Never repairs anything."""
    failures = []
    # Joined by name: the application role has no USAGE on baec_auth, so it cannot resolve the table's name itself.
    policies = {r[0]: (r[1], r[2], sorted(r[3]), r[4], r[5]) for r in db.execute(
        "SELECT p.polname, p.polpermissive, p.polcmd::text, "
        "ARRAY(SELECT rolname FROM pg_roles WHERE oid = ANY(p.polroles)), pg_get_expr(p.polqual, p.polrelid), "
        "pg_get_expr(p.polwithcheck, p.polrelid) FROM pg_policy p JOIN pg_class c ON c.oid = p.polrelid "
        "JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = 'baec_auth' AND c.relname = 'auth_identities'"
    ).fetchall()}
    if policies != AUTH_POLICIES:
        failures.append("the row-level security policies on baec_auth.auth_identities are not the expected "
                        "sign-in policies (predicates or roles differ)")
    failures += _function_failures(db, ("baec_auth",))
    return failures + catalog.failures(db, ("baec_auth",), catalog_snapshot)


def auth_lookup_failures(lookup, *, probe: bool = True) -> list[str]:
    """How the sign-in lookup role (connected as `lookup`) differs from the narrow role migration 0005 designed. It
    must be able to read one exact sign-in link and nothing else. With `probe`, and only if nothing else differs, it
    also reads the table as that role with no issuer or subject set, and must see no row (check_database probes only
    once the sign-in schema itself has passed, so a broken policy or function cannot turn the probe into an error)."""
    if lookup.execute("SELECT current_user").fetchone()[0] != AUTH_LOOKUP_ROLE:
        return [f"the sign-in lookup URL does not connect as {AUTH_LOOKUP_ROLE}"]
    failures = []
    row = lookup.execute("SELECT rolsuper, rolbypassrls, rolcreaterole, rolcreatedb, rolreplication FROM pg_roles "
                         "WHERE rolname = current_user").fetchone()
    for flag, label in zip(row, ("is a superuser", "can bypass row-level security", "can create roles",
                                 "can create databases", "can replicate")):
        if flag:
            failures.append(f"the sign-in lookup role {label}")
    memberships = lookup.execute(
        "SELECT count(*) FROM pg_auth_members m, pg_roles r WHERE r.rolname = current_user "
        "AND (m.member = r.oid OR m.roleid = r.oid)").fetchone()[0]
    if memberships:
        failures.append("the sign-in lookup role is a member of a role, or has members")
    owned = lookup.execute("SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                           "WHERE n.nspname = ANY(%s) AND pg_has_role(current_user, c.relowner, 'MEMBER')",
                           (list(SCHEMAS),)).fetchone()[0]
    owned += lookup.execute("SELECT count(*) FROM pg_namespace WHERE nspname = ANY(%s) "
                            "AND pg_has_role(current_user, nspowner, 'MEMBER')", (list(SCHEMAS),)).fetchone()[0]
    if owned:
        failures.append("the sign-in lookup role owns BAEC schema objects")
    for schema in (*SCHEMAS, "public"):
        granted = {p for p in ("USAGE", "CREATE") if lookup.execute(
            "SELECT has_schema_privilege(current_user, %s, %s)", (schema, p)).fetchone()[0]} \
            if lookup.execute("SELECT to_regnamespace(%s) IS NOT NULL", (schema,)).fetchone()[0] else set()
        if granted != ({"USAGE"} if schema == "baec_auth" else set()):
            failures.append(f"the sign-in lookup role has {sorted(granted)} on schema {schema}")
    tables = lookup.execute(
        "SELECT n.nspname || '.' || c.relname, ARRAY(SELECT p FROM unnest(%s::text[]) AS p "
        "WHERE has_table_privilege(current_user, c.oid, p)) FROM pg_class c "
        "JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = ANY(%s) AND c.relkind IN ('r', 'v', 'm', 'p', 'f')",
        (list(_FORBIDDEN_TABLE_PRIVILEGES + ("SELECT", "INSERT", "UPDATE")), list(SCHEMAS))).fetchall()
    for table, granted in tables:
        if granted:
            failures.append(f"the sign-in lookup role has {sorted(granted)} on {table}")
    columns = {tuple(r) for r in lookup.execute(
        "SELECT n.nspname || '.' || c.relname, a.attname, p FROM pg_attribute a "
        "JOIN pg_class c ON c.oid = a.attrelid JOIN pg_namespace n ON n.oid = c.relnamespace "
        "CROSS JOIN unnest(%s::text[]) AS p WHERE n.nspname = ANY(%s) AND c.relkind IN ('r', 'v', 'm', 'p', 'f') "
        "AND a.attnum > 0 AND NOT a.attisdropped AND has_column_privilege(current_user, c.oid, a.attnum, p)",
        (list(_COLUMN_PRIVILEGES), list(SCHEMAS))).fetchall()}
    expected = {(AUTH_TABLE, column, "SELECT") for column in AUTH_LOOKUP_COLUMNS}
    for table, column, privilege in sorted(columns ^ expected):
        state = "may" if (table, column, privilege) in columns else "may not"
        failures.append(f"the sign-in lookup role {state} {privilege} {table}.{column}")
    executable = {r[0] for r in lookup.execute(
        "SELECT p.proname FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
        "WHERE n.nspname = 'baec_auth' AND has_function_privilege(current_user, p.oid, 'EXECUTE')").fetchall()}
    if executable != {"current_issuer", "current_subject"}:
        failures.append(f"the sign-in lookup role may execute {sorted(executable)} in schema baec_auth")
    if lookup.execute("SELECT has_parameter_privilege(current_user, 'session_replication_role', 'SET')").fetchone()[0]:
        failures.append("the sign-in lookup role can set session_replication_role")
    if _connection_limit(lookup) < 0:
        failures.append("the sign-in lookup role has no connection limit")
    if probe and not failures:  # the live policy, as this role meets it: no exact issuer and subject, no row
        with lookup.transaction():
            lookup.execute("SET TRANSACTION READ ONLY")
            if lookup.execute(f"SELECT count(*) FROM {AUTH_TABLE}").fetchone()[0]:
                failures.append("the sign-in lookup role sees sign-in links without an exact issuer and subject")
    return failures


def check_database(url: str | None, auth_url: str | None, settings: DatabaseSettings | None = None
                   ) -> DatabaseReport:
    """Every production database check, on fresh connections (the application role's, `url`, and the sign-in lookup
    role's, `auth_url`) that are closed afterwards. Both URLs must require verified TLS before either is used.
    Raises DatabaseRefused. A database error while checking (for example a role that may not even read the migration
    record) is a refusal too, named by its kind only. The connections carry `settings`' timeouts, so a stuck catalog
    query cannot hold startup indefinitely."""
    url = require_verified_tls(validate_database_url(url))
    auth_url = require_verified_tls(validate_database_url(auth_url, name="BAEC_AUTH_DATABASE_URL"),
                                    name="BAEC_AUTH_DATABASE_URL")
    options = (settings or DatabaseSettings()).options
    db = connect(url, name="BAEC_DATABASE_URL", options=options)
    psycopg = require_driver()
    try:
        return _check(db, auth_url, options)
    except psycopg.Error as error:
        raise DatabaseRefused([f"the database check could not complete ({type(error).__name__}); the connecting "
                               f"role is not a safe BAEC application role"]) from None
    finally:
        db.close()


def _check(db, auth_url: str, options: str) -> DatabaseReport:
    failures = transport_failures(db, "application")
    if failures:
        raise DatabaseRefused(failures)
    applied = recorded(db)
    names = db.execute("SELECT version, name FROM baec_migrations.schema_migrations ORDER BY version").fetchall() \
        if applied else []
    if applied != expected_versions() or names != [(m.version, m.name) for m in migrations()]:
        failures.append("the database schema is not at the expected migration version")
    failures += _role_failures(db) if applied else []
    failures += _policy_failures(db) if applied else []
    if failures:
        raise DatabaseRefused(failures)
    catalog_snapshot = catalog.snapshot(db)
    schema = engine2_failures(db, catalog_snapshot)
    if schema:
        raise Engine2SchemaRefused(schema)
    schema = engine1_failures(db, catalog_snapshot)
    if schema:
        raise Engine1SchemaRefused(schema)
    schema = product_failures(db, catalog_snapshot)
    if schema:
        raise ProductSchemaRefused(schema)
    schema = auth_failures(db, catalog_snapshot)
    lookup = connect(auth_url, name="BAEC_AUTH_DATABASE_URL", options=options)
    try:
        transport = transport_failures(lookup, "sign-in lookup")
        if transport:
            raise DatabaseRefused(transport)
        schema += auth_lookup_failures(lookup, probe=not schema)
        lookup_limit = _connection_limit(lookup)
    finally:
        lookup.close()
    if schema:
        raise AuthSchemaRefused(schema)
    return DatabaseReport(db.execute("SELECT current_user").fetchone()[0], tuple(v for v, _ in applied),
                          TENANT_TABLES + tuple(f"baec_engine1.{t}" for t in ENGINE1_TABLES)
                          + tuple(f"baec_engine2.{t}" for t in ENGINE2_TABLES),
                          (_connection_limit(db), lookup_limit))
