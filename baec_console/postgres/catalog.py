"""The validated PostgreSQL schema, as catalog facts, and the comparison the production gate makes against it.

snapshot(db) reads, for every BAEC schema, the facts that safety and integrity depend on:

    relations    every table, index, sequence, view, ... by kind; for tables RLS enabled and forced
    columns      name, type, NOT NULL, default expression, identity, collation
    constraints  every PRIMARY KEY, FOREIGN KEY, UNIQUE, and CHECK, by name, as pg_get_constraintdef renders it
    indexes      every index, as pg_get_indexdef renders it
    triggers     every trigger, as pg_get_triggerdef renders it, and whether it fires in ordinary sessions
    policies     every RLS policy: permissive or restrictive, command, whether it applies to every role, predicates
    functions    every function: argument and return types, language, body, volatility, SECURITY DEFINER, settings,
                 and whether the connecting role may execute it
    privileges   what the connecting role may do on each table, column, and schema (its effective privileges,
                 inherited ones included), so a widened grant is seen whoever granted it

Nothing in it names a role, an owner, an object id, or a row.

The expected facts are not written by hand. schema_manifest.json is the snapshot of a freshly migrated database taken
as the application role, together with the (version, name, checksum) of the migrations it came from; the PostgreSQL
test suite rebuilds that snapshot from a fresh migration on every run and fails if it differs from the file
(tests_postgres/test_live_schema_gate.py), so the manifest cannot drift from the migrations silently. After a new
migration, regenerate it with

    python -m baec_console.postgres.catalog write <application-role URL of a freshly migrated database>

differences(live, expected) lists every difference by object name. The gate refuses on any of them and repairs
nothing.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from .database import connect, validate_database_url

MANIFEST = Path(__file__).resolve().parent / "schema_manifest.json"
SCHEMAS = ("baec", "baec_auth", "baec_engine1", "baec_engine2", "baec_migrations")
_TABLE_PRIVILEGES = ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER")
_COLUMN_PRIVILEGES = ("SELECT", "INSERT", "UPDATE", "REFERENCES")
_NOUNS = {"schemas": "schema", "relations": "relation", "columns": "column", "constraints": "constraint",
          "indexes": "index", "triggers": "trigger", "policies": "policy", "functions": "function",
          "privileges": "privilege"}


def _rows(db, sql: str):
    if "%(" in sql:
        return db.execute(sql, {"s": list(SCHEMAS), "t": list(_TABLE_PRIVILEGES),
                                "c": list(_COLUMN_PRIVILEGES)}).fetchall()
    return db.execute(sql, (list(SCHEMAS),)).fetchall()


def snapshot(db) -> dict:
    """The catalog facts of every BAEC schema, from the point of view of the connecting role."""
    out: dict = {"schemas": {}, "relations": {}, "columns": {}, "constraints": {}, "indexes": {}, "triggers": {},
                 "policies": {}, "functions": {}, "privileges": {}}
    for schema, granted in _rows(
            db, "SELECT nspname, ARRAY(SELECT p FROM unnest(ARRAY['USAGE', 'CREATE']) AS p "
                "WHERE has_schema_privilege(current_user, oid, p)) FROM pg_namespace WHERE nspname = ANY(%s)"):
        out["schemas"][schema] = list(granted)
    for schema, name, kind, rls, forced in _rows(
            db, "SELECT n.nspname, c.relname, c.relkind::text, c.relrowsecurity, c.relforcerowsecurity "
                "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = ANY(%s)"):
        out["relations"][f"{schema}.{name}"] = [kind, rls, forced] if kind == "r" else [kind]
    for schema, table, column, type_, notnull, default, identity, generated, collation in _rows(
            db, "SELECT n.nspname, c.relname, a.attname, format_type(a.atttypid, a.atttypmod), a.attnotnull, "
                "pg_get_expr(d.adbin, d.adrelid), a.attidentity::text, a.attgenerated::text, "
                "CASE WHEN a.attcollation <> 0 THEN (SELECT collname FROM pg_collation WHERE oid = a.attcollation) "
                "END FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum "
                "WHERE n.nspname = ANY(%s) AND c.relkind = 'r' AND a.attnum > 0 AND NOT a.attisdropped"):
        out["columns"][f"{schema}.{table}.{column}"] = [type_, notnull, default, identity, generated, collation]
    for schema, table, name, definition in _rows(
            db, "SELECT n.nspname, c.relname, k.conname, pg_get_constraintdef(k.oid) FROM pg_constraint k "
                "JOIN pg_class c ON c.oid = k.conrelid JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = ANY(%s)"):
        out["constraints"][f"{schema}.{table}.{name}"] = definition
    for schema, name, definition in _rows(
            db, "SELECT n.nspname, c.relname, pg_get_indexdef(c.oid) FROM pg_index i JOIN pg_class c "
                "ON c.oid = i.indexrelid JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = ANY(%s)"):
        out["indexes"][f"{schema}.{name}"] = definition
    for schema, table, name, definition, enabled in _rows(
            db, "SELECT n.nspname, c.relname, t.tgname, pg_get_triggerdef(t.oid), t.tgenabled::text FROM pg_trigger t "
                "JOIN pg_class c ON c.oid = t.tgrelid JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = ANY(%s) AND NOT t.tgisinternal"):
        out["triggers"][f"{schema}.{table}.{name}"] = [definition, enabled]
    for schema, table, name, permissive, command, every_role, using, check in _rows(
            db, "SELECT n.nspname, c.relname, p.polname, p.polpermissive, p.polcmd::text, p.polroles = '{0}', "
                "pg_get_expr(p.polqual, p.polrelid), pg_get_expr(p.polwithcheck, p.polrelid) FROM pg_policy p "
                "JOIN pg_class c ON c.oid = p.polrelid JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = ANY(%s)"):
        out["policies"][f"{schema}.{table}.{name}"] = [permissive, command, every_role, using, check]
    for schema, name, arguments, returns, language, body, definer, volatility, config, executable in _rows(
            db, "SELECT n.nspname, p.proname, pg_get_function_identity_arguments(p.oid), "
                "format_type(p.prorettype, NULL), l.lanname, p.prosrc, p.prosecdef, p.provolatile::text, "
                "p.proconfig, has_function_privilege(current_user, p.oid, 'EXECUTE') FROM pg_proc p "
                "JOIN pg_namespace n ON n.oid = p.pronamespace JOIN pg_language l ON l.oid = p.prolang "
                "WHERE n.nspname = ANY(%s)"):
        out["functions"][f"{schema}.{name}({arguments})"] = [returns, language, body, definer, volatility, config,
                                                              executable]
    for schema, table, granted in _rows(
            db, "SELECT n.nspname, c.relname, ARRAY(SELECT p FROM unnest(%(t)s::text[]) WITH ORDINALITY AS u (p, i) "
                "WHERE has_table_privilege(current_user, c.oid, p) ORDER BY i) FROM pg_class c "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = ANY(%(s)s) AND c.relkind IN ('r', 'v', 'm', 'S', 'f', 'p')"):
        out["privileges"][f"{schema}.{table}"] = [list(granted), {}]
    for schema, table, column, extra in _rows(
            db, "SELECT n.nspname, c.relname, a.attname, ARRAY(SELECT p FROM unnest(%(c)s::text[]) WITH ORDINALITY "
                "AS u (p, i) WHERE has_column_privilege(current_user, c.oid, a.attnum, p) "
                "AND NOT has_table_privilege(current_user, c.oid, p) ORDER BY i) FROM pg_attribute a "
                "JOIN pg_class c ON c.oid = a.attrelid JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = ANY(%(s)s) AND c.relkind IN ('r', 'v', 'm', 'f', 'p') AND a.attnum > 0 "
                "AND NOT a.attisdropped"):
        if extra:
            out["privileges"][f"{schema}.{table}"][1][column] = list(extra)
    for facts in out["privileges"].values():
        facts[1] = dict(sorted(facts[1].items()))
    return {kind: dict(sorted(facts.items())) for kind, facts in out.items()}


def _migrations() -> list[list]:
    from .migrate import migrations
    return [[m.version, m.name, m.checksum] for m in migrations()]


def manifest() -> dict:
    """The validated schema: the snapshot of a fresh migration, and the migrations it was taken from."""
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def differences(live: dict, expected: dict, schemas: tuple[str, ...] = SCHEMAS) -> list[str]:
    """Every difference between two snapshots, for objects in `schemas`. Object names only: never a row or a URL."""
    out = []
    for kind in sorted(set(live) | set(expected)):
        have, want = live.get(kind, {}), expected.get(kind, {})
        noun = _NOUNS.get(kind, kind)
        for name in sorted(set(have) | set(want)):
            if name.split(".", 1)[0] not in schemas:
                continue
            if name not in have:
                out.append(f"{noun} {name} is missing")
            elif name not in want:
                out.append(f"unexpected {noun} {name}")
            elif have[name] != want[name]:
                out.append(f"{noun} {name} differs from the validated schema")
    return out


def failures(db, schemas: tuple[str, ...], live: dict | None = None) -> list[str]:
    """Differences between the live schema (`live`, or a snapshot taken now) and the validated manifest, for
    `schemas`. Never repairs anything."""
    expected = manifest()
    if expected.get("migrations") != _migrations():
        return ["the validated schema manifest was not taken from these migrations"]
    return differences(snapshot(db) if live is None else live, expected["snapshot"], schemas)


def write(url: str) -> None:
    """Regenerate schema_manifest.json from a freshly migrated database, connecting as the application role."""
    db = connect(validate_database_url(url))
    try:
        if db.execute("SELECT current_user").fetchone()[0] != "baec_app":
            raise SystemExit("connect as the application role (baec_app) to a freshly migrated database")
        document = {"migrations": _migrations(), "snapshot": snapshot(db)}
    finally:
        db.close()
    MANIFEST.write_text(json.dumps(document, indent=1, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] != "write":
        raise SystemExit("usage: python -m baec_console.postgres.catalog write <application-role URL>")
    write(sys.argv[2])
