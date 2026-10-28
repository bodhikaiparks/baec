"""Tenant-scoped PostgreSQL stores with the same methods and return values as the SQLite stores.

    PgIdentityStore  identity.IdentityStore    (subclass: principal, verify, and authenticated_principal are shared)
    PgConsoleStore   console_store.ConsoleStore
    PgCRMStore       crm_store.CRMStore

A store lives inside one tenant transaction (PostgresProductStorage.session) and is bound to that organization and
one workspace ('local' or 'synthetic'). Queries filter on both, and row-level security enforces the organization
again in the database, so a query that forgot the filter still sees only the current organization's rows.

Behavior that differs from SQLite, all deliberate:
  * another organization's rows do not exist for this store (RLS), so owning_organizations() and organization_of()
    report only the current organization;
  * a refused write aborts to the enclosing savepoint; the store's own error type is raised as in SQLite;
  * stores never hold a pending backfill: ownership_backfill() and attribution_backfill() are always DONE, and the
    backfill writers and migration_log are not provided (the importer refuses a source with a pending backfill);
  * contacts sort case-insensitively with lower() (Unicode) where SQLite's NOCASE folds only ASCII.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Iterator

from .. import identity
from ..console_store import (
    BACKFILL_DONE,
    ENGINE3_VALIDATOR_ORIGIN,
    ORIGIN_COMPATIBILITY_NOTE,
    ConsoleEvent,
    ConsoleStoreError,
)
from ..console_store import ts as _console_ts
from ..crm_store import CRMStoreError
from ..crm_store import _ts as _crm_ts
from ..identity import IdentityError, IdentityStore, Organization, User, UserKind
from . import require_driver
from .database import WORKSPACES, TenantDatabase
from .engine1 import Engine1Connection
from .engine2 import Engine2Connection


def _json(value) -> str:
    return json.dumps(value, sort_keys=True)


@contextmanager
def _savepoint(connection) -> Iterator[None]:
    with connection.transaction():
        yield


# --- identity --------------------------------------------------------------------------------------------------------


class PgIdentityStore(IdentityStore):
    """IdentityStore over the current tenant transaction. Only the current organization's rows are visible."""

    def __init__(self, connection) -> None:
        super().__init__(connection)

    def organization(self, organization_id: str) -> Organization | None:
        row = self.db.execute("SELECT organization_id, name, created_at FROM baec.organizations "
                              "WHERE organization_id = %s", (organization_id,)).fetchone()
        return None if row is None else Organization(*row)

    def user(self, user_id: str) -> User | None:
        row = self.db.execute("SELECT user_id, organization_id, display_name, created_at, active, kind FROM baec.users "
                              "WHERE user_id = %s", (user_id,)).fetchone()
        return None if row is None else User(*row[:5], UserKind(row[5]))

    def users(self, organization_id: str) -> list[User]:
        ids = self.db.execute("SELECT user_id FROM baec.users WHERE organization_id = %s ORDER BY seq",
                              (organization_id,)).fetchall()
        return [self.user(r[0]) for r in ids]

    def audit(self, user_id: str | None = None) -> list[identity.IdentityAuditEvent]:
        sql, args = "SELECT seq, occurred_at, event_type, user_id, detail FROM baec.identity_audit", ()
        if user_id is not None:
            sql, args = sql + " WHERE user_id = %s", (user_id,)
        return [identity.IdentityAuditEvent(*r) for r in self.db.execute(sql + " ORDER BY seq", args).fetchall()]

    def _audit(self, at: datetime, event_type: str, user_id: str, detail: str) -> None:
        identity._ts(at)
        self.db.execute("INSERT INTO baec.identity_audit (organization_id, occurred_at, event_type, user_id, detail) "
                        "SELECT organization_id, %s, %s, user_id, %s FROM baec.users WHERE user_id = %s",
                        (at, event_type, detail, user_id))

    def ensure_organization(self, organization_id: str, name: str, created_at: datetime) -> Organization:
        if not organization_id.strip() or not name.strip():
            raise IdentityError("an organization needs an id and a name")
        identity._ts(created_at)
        self.db.execute("INSERT INTO baec.organizations (organization_id, name, created_at) VALUES (%s, %s, %s) "
                        "ON CONFLICT (organization_id) DO NOTHING", (organization_id, name.strip(), created_at))
        return self.organization(organization_id)

    def ensure_user(self, user_id: str, organization_id: str, display_name: str, created_at: datetime, *,
                    kind: UserKind) -> User:
        if not isinstance(kind, UserKind):
            raise IdentityError("a user needs an explicit kind")
        if self.organization(organization_id) is None:
            raise IdentityError(f"unknown organization {organization_id!r}")
        existing = self.user(user_id)
        if existing is not None:
            if existing.organization_id != organization_id:
                raise IdentityError(f"user {user_id!r} belongs to a different organization")
            if existing.kind is not kind:
                raise IdentityError(f"user {user_id!r} is a {existing.kind.value} user, not {kind.value}")
            return existing
        name = identity._display_name(display_name)
        identity._ts(created_at)
        self._write("INSERT INTO baec.users (user_id, organization_id, display_name, created_at, active, kind) "
                    "VALUES (%s, %s, %s, %s, true, %s)", (user_id, organization_id, name, created_at, kind.value),
                    user_id=user_id)
        self._audit(created_at, "USER_CREATED", user_id, f"{kind.value} user created as {display_name.strip()!r}")
        return self.user(user_id)

    def rename_user(self, user_id: str, display_name: str, *, at: datetime) -> User:
        before = self.user(user_id)
        if before is None:
            raise IdentityError(f"unknown user {user_id!r}")
        name = identity._display_name(display_name)
        if name == before.display_name:
            return before
        self._write("UPDATE baec.users SET display_name = %s WHERE user_id = %s", (name, user_id), user_id=user_id)
        self._audit(at, "USER_RENAMED", user_id, f"{before.display_name!r} -> {name!r}")
        return self.user(user_id)

    def set_active(self, user_id: str, active: bool, *, at: datetime) -> None:
        before = self.user(user_id)
        if before is None:
            raise IdentityError(f"unknown user {user_id!r}")
        if before.active == active:
            return
        self.db.execute("UPDATE baec.users SET active = %s WHERE user_id = %s", (active, user_id))
        self._audit(at, "USER_REACTIVATED" if active else "USER_DEACTIVATED", user_id,
                    "reactivated" if active else "deactivated")

    def _write(self, sql: str, args: tuple, *, user_id: str) -> None:
        """A users write whose unique-key refusal becomes the same IdentityError the SQLite store raises."""
        psycopg = require_driver()
        try:
            with _savepoint(self.db):
                self.db.execute(sql, args)
        except psycopg.errors.UniqueViolation as error:
            if error.diag.constraint_name == "users_pkey":  # the id exists in an organization this store cannot see
                raise IdentityError(f"user {user_id!r} belongs to a different organization") from None
            raise IdentityError("That name is already used by someone else in this organization.") from None


# --- console store ---------------------------------------------------------------------------------------------------


class PgConsoleStore:
    """ConsoleStore for one organization's workspace."""

    def __init__(self, connection, organization_id: str, workspace: str) -> None:
        self.db = connection
        self.organization_id = organization_id
        self.workspace = workspace
        self._scope = (organization_id, workspace)

    def _rows(self, sql: str, args: tuple = ()) -> list[tuple]:
        return self.db.execute(sql, self._scope + args).fetchall()

    def _one(self, sql: str, args: tuple = ()):
        return self.db.execute(sql, self._scope + args).fetchone()

    def _insert(self, table: str, columns: str, values: tuple, returning: str | None = None):
        names = "organization_id, workspace, " + columns
        marks = ", ".join(["%s"] * (len(values) + 2))
        sql = f"INSERT INTO baec.{table} ({names}) VALUES ({marks})" + (f" RETURNING {returning}" if returning else "")
        cursor = self.db.execute(sql, self._scope + values)
        return cursor.fetchone()[0] if returning else None

    # --- events ------------------------------------------------------------------

    def add_event(self, account_id: str, event_type: str, actor_kind: str, actor: str, occurred_at: datetime,
                  summary: str, ref_id: str | None = None) -> int:
        _console_ts(occurred_at)
        return self._insert("events", "account_id, event_type, actor_kind, actor, occurred_at, summary, ref_id",
                            (account_id, event_type, actor_kind, actor, occurred_at, summary, ref_id), "event_id")

    def events(self, account_id: str) -> list[ConsoleEvent]:
        rows = self._rows("SELECT event_id, account_id, event_type, actor_kind, actor, occurred_at, summary, ref_id "
                          "FROM baec.events WHERE organization_id = %s AND workspace = %s AND account_id = %s "
                          "ORDER BY event_id", (account_id,))
        return [ConsoleEvent(*r) for r in rows]

    # --- accounts and interactions -------------------------------------------------

    def add_account_meta(self, account_id: str, display_name: str, industry: str | None, owner_note: str | None,
                         synthetic: bool, created_at: datetime, created_by: str) -> None:
        _console_ts(created_at)
        self._insert("account_meta", "account_id, display_name, industry, owner_note, synthetic, created_at, created_by",
                     (account_id, display_name, industry, owner_note, bool(synthetic), created_at, created_by))

    def account_meta(self, account_id: str) -> dict | None:
        row = self._one("SELECT account_id, display_name, industry, owner_note, synthetic, created_at, created_by "
                        "FROM baec.account_meta WHERE organization_id = %s AND workspace = %s AND account_id = %s",
                        (account_id,))
        if row is None:
            return None
        return {"account_id": row[0], "display_name": row[1], "industry": row[2], "owner_note": row[3],
                "synthetic": row[4], "created_at": row[5], "created_by": row[6]}

    def add_interaction_meta(self, interaction_id: str, account_id: str, buyer_role: str | None, buyer_statement: str,
                             seller_notes: str | None, recorded_at: datetime, recorded_by: str) -> None:
        _console_ts(recorded_at)
        self._insert("interaction_meta", "interaction_id, account_id, buyer_role, buyer_statement, seller_notes, "
                     "recorded_at, recorded_by",
                     (interaction_id, account_id, buyer_role, buyer_statement, seller_notes, recorded_at, recorded_by))

    def interaction_meta(self, interaction_id: str) -> dict | None:
        row = self._one("SELECT interaction_id, account_id, buyer_role, buyer_statement, seller_notes, recorded_at, "
                        "recorded_by FROM baec.interaction_meta WHERE organization_id = %s AND workspace = %s "
                        "AND interaction_id = %s", (interaction_id,))
        if row is None:
            return None
        return {"interaction_id": row[0], "account_id": row[1], "buyer_role": row[2], "buyer_statement": row[3],
                "seller_notes": row[4], "recorded_at": row[5], "recorded_by": row[6]}

    # --- condition drafts ------------------------------------------------------------

    def add_draft(self, draft_id: str, account_id: str, interaction_id: str, form: dict, captured_at: datetime,
                  captured_by: str) -> None:
        _console_ts(captured_at)
        self._insert("condition_drafts", "draft_id, account_id, interaction_id, form_json, captured_at, captured_by",
                     (draft_id, account_id, interaction_id, _json(form), captured_at, captured_by))

    def resolve_draft(self, draft_id: str, resolution: str, baec_id: str | None, resolved_at: datetime,
                      resolved_by: str) -> None:
        _console_ts(resolved_at)
        self._insert("draft_resolutions", "draft_id, resolution, baec_id, resolved_at, resolved_by",
                     (draft_id, resolution, baec_id, resolved_at, resolved_by))

    def drafts(self, account_id: str | None = None) -> list[dict]:
        sql = ("SELECT d.draft_id, d.account_id, d.interaction_id, d.form_json, d.captured_at, d.captured_by, "
               "r.resolution, r.baec_id, r.resolved_at, r.resolved_by FROM baec.condition_drafts d "
               "LEFT JOIN baec.draft_resolutions r ON (r.organization_id, r.workspace, r.draft_id) = "
               "(d.organization_id, d.workspace, d.draft_id) WHERE d.organization_id = %s AND d.workspace = %s")
        args: tuple = ()
        if account_id is not None:
            sql, args = sql + " AND d.account_id = %s", (account_id,)
        return [{"draft_id": r[0], "account_id": r[1], "interaction_id": r[2], "form": json.loads(r[3]),
                 "captured_at": r[4], "captured_by": r[5], "resolution": r[6], "baec_id": r[7], "resolved_at": r[8],
                 "resolved_by": r[9]} for r in self._rows(sql + " ORDER BY d.seq", args)]

    def draft(self, draft_id: str) -> dict:
        for item in self.drafts():
            if item["draft_id"] == draft_id:
                return item
        raise ConsoleStoreError(f"no condition draft {draft_id}")

    # --- preparation -----------------------------------------------------------------

    def add_preparation_request(self, request_id: str, account_id: str, baec_id: str, review_id: str,
                                candidate_id: str, request: dict, snapshot: dict, context_digest: str,
                                requested_at: datetime, requested_by: str) -> None:
        _console_ts(requested_at)
        self._insert("preparation_requests", "request_id, account_id, baec_id, review_id, candidate_id, request_json, "
                     "snapshot_json, context_digest, requested_at, requested_by",
                     (request_id, account_id, baec_id, review_id, candidate_id, _json(request), _json(snapshot),
                      context_digest, requested_at, requested_by))

    def preparation_requests(self, account_id: str | None = None) -> list[dict]:
        sql = ("SELECT request_id, account_id, baec_id, review_id, candidate_id, request_json, snapshot_json, "
               "context_digest, requested_at, requested_by, seq FROM baec.preparation_requests "
               "WHERE organization_id = %s AND workspace = %s")
        args: tuple = ()
        if account_id is not None:
            sql, args = sql + " AND account_id = %s", (account_id,)
        return [{"request_id": r[0], "account_id": r[1], "baec_id": r[2], "review_id": r[3], "candidate_id": r[4],
                 "request": json.loads(r[5]), "snapshot": json.loads(r[6]), "context_digest": r[7],
                 "requested_at": r[8], "requested_by": r[9], "seq": r[10]}
                for r in self._rows(sql + " ORDER BY seq", args)]

    def preparation_request(self, request_id: str) -> dict:
        for item in self.preparation_requests():
            if item["request_id"] == request_id:
                return item
        raise ConsoleStoreError(f"no preparation request {request_id}")

    def add_preparation(self, preparation_id: str, request_id: str, generator: str, artifacts: list[dict],
                        generated_at: datetime, generated_by: str) -> None:
        request = self.preparation_request(request_id)
        _console_ts(generated_at)
        self._insert("preparations", "preparation_id, request_id, generator, artifacts_json, generated_at, generated_by",
                     (preparation_id, request_id, generator, _json(artifacts), generated_at, generated_by))
        self._insert("preparation_provenance", "preparation_id, bound_review_id, bound_candidate_id, "
                     "console_generator_origin, engine3_validator_origin, origin_compatibility_note, model_used",
                     (preparation_id, request["review_id"], request["candidate_id"], generator,
                      ENGINE3_VALIDATOR_ORIGIN, ORIGIN_COMPATIBILITY_NOTE, False))

    def provenance(self, preparation_id: str) -> dict:
        row = self._one("SELECT preparation_id, bound_review_id, bound_candidate_id, console_generator_origin, "
                        "engine3_validator_origin, origin_compatibility_note, model_used FROM baec.preparation_provenance "
                        "WHERE organization_id = %s AND workspace = %s AND preparation_id = %s", (preparation_id,))
        if row is None:
            raise ConsoleStoreError(f"preparation {preparation_id} has no provenance record")
        return {"preparation_id": row[0], "bound_review_id": row[1], "bound_candidate_id": row[2],
                "console_generator_origin": row[3], "engine3_validator_origin": row[4],
                "origin_compatibility_note": row[5], "model_used": row[6]}

    # --- human preparation blocks ------------------------------------------------------

    def add_block_decision(self, account_id: str, review_id: str, action: str, reason: str | None,
                           decided_at: datetime, decided_by: str) -> int:
        _console_ts(decided_at)
        return self._insert("preparation_blocks", "account_id, review_id, action, reason, decided_at, decided_by",
                            (account_id, review_id, action, reason, decided_at, decided_by), "decision_id")

    def block_decisions(self, review_id: str) -> list[dict]:
        rows = self._rows("SELECT decision_id, account_id, review_id, action, reason, decided_at, decided_by "
                          "FROM baec.preparation_blocks WHERE organization_id = %s AND workspace = %s "
                          "AND review_id = %s ORDER BY decision_id", (review_id,))
        return [{"decision_id": r[0], "account_id": r[1], "review_id": r[2], "action": r[3], "reason": r[4],
                 "decided_at": r[5], "decided_by": r[6]} for r in rows]

    def is_blocked(self, review_id: str) -> bool:
        decisions = self.block_decisions(review_id)
        return bool(decisions) and decisions[-1]["action"] == "BLOCK"

    def preparations(self) -> list[dict]:
        rows = self._rows("SELECT preparation_id, request_id, generator, artifacts_json, generated_at, generated_by "
                          "FROM baec.preparations WHERE organization_id = %s AND workspace = %s ORDER BY seq")
        return [{"preparation_id": r[0], "request_id": r[1], "generator": r[2], "artifacts": json.loads(r[3]),
                 "generated_at": r[4], "generated_by": r[5]} for r in rows]

    def preparation(self, preparation_id: str) -> dict:
        for item in self.preparations():
            if item["preparation_id"] == preparation_id:
                return item
        raise ConsoleStoreError(f"no preparation {preparation_id}")

    def add_edit(self, edit_id: str, preparation_id: str, parent_artifact_id: str, artifact: dict,
                 edited_at: datetime, edited_by: str) -> None:
        _console_ts(edited_at)
        self._insert("preparation_edits", "edit_id, preparation_id, parent_artifact_id, artifact_json, edited_at, "
                     "edited_by", (edit_id, preparation_id, parent_artifact_id, _json(artifact), edited_at, edited_by))

    def edits(self, preparation_id: str) -> list[dict]:
        rows = self._rows("SELECT edit_id, preparation_id, parent_artifact_id, artifact_json, edited_at, edited_by "
                          "FROM baec.preparation_edits WHERE organization_id = %s AND workspace = %s "
                          "AND preparation_id = %s ORDER BY seq", (preparation_id,))
        return [{"edit_id": r[0], "preparation_id": r[1], "parent_artifact_id": r[2], "artifact": json.loads(r[3]),
                 "edited_at": r[4], "edited_by": r[5]} for r in rows]

    def add_review(self, preparation_id: str, artifact_id: str, decision: str, edit_id: str | None,
                   attestations: dict, reason: str | None, reviewed_at: datetime, reviewed_by: str) -> None:
        _console_ts(reviewed_at)
        self._insert("preparation_reviews", "preparation_id, artifact_id, decision, edit_id, attestations_json, reason, "
                     "reviewed_at, reviewed_by",
                     (preparation_id, artifact_id, decision, edit_id, _json(attestations), reason, reviewed_at,
                      reviewed_by))

    def reviews(self, preparation_id: str) -> dict[str, dict]:
        rows = self._rows("SELECT artifact_id, decision, edit_id, attestations_json, reason, reviewed_at, reviewed_by "
                          "FROM baec.preparation_reviews WHERE organization_id = %s AND workspace = %s "
                          "AND preparation_id = %s ORDER BY seq", (preparation_id,))
        return {r[0]: {"artifact_id": r[0], "decision": r[1], "edit_id": r[2], "attestations": json.loads(r[3]),
                       "reason": r[4], "reviewed_at": r[5], "reviewed_by": r[6]} for r in rows}

    def account_ids(self) -> list[str]:
        return [r[0] for r in self._rows("SELECT account_id FROM baec.account_meta "
                                         "WHERE organization_id = %s AND workspace = %s ORDER BY seq")]

    # --- account ownership --------------------------------------------------------------------------------------

    def add_ownership(self, account_id: str, organization_id: str, recorded_at: datetime, recorded_by_user_id: str,
                      basis: str = "CREATED") -> None:
        _console_ts(recorded_at)
        self.db.execute("INSERT INTO baec.account_ownership (organization_id, workspace, account_id, recorded_at, "
                        "recorded_by_user_id, basis) VALUES (%s, %s, %s, %s, %s, %s)",
                        (organization_id, self.workspace, account_id, recorded_at, recorded_by_user_id, basis))

    def organization_of(self, account_id: str) -> str | None:
        row = self.db.execute("SELECT organization_id FROM baec.account_ownership WHERE workspace = %s "
                              "AND account_id = %s", (self.workspace, account_id)).fetchone()
        return None if row is None else row[0]

    def owned_account_ids(self, organization_id: str) -> set[str]:
        return {r[0] for r in self.db.execute("SELECT account_id FROM baec.account_ownership WHERE workspace = %s "
                                              "AND organization_id = %s", (self.workspace, organization_id))}

    def owning_organizations(self) -> set[str]:
        return {r[0] for r in self.db.execute("SELECT DISTINCT organization_id FROM baec.account_ownership "
                                              "WHERE workspace = %s", (self.workspace,))}

    def ownership_backfill(self) -> str:
        return BACKFILL_DONE

    # --- attribution ------------------------------------------------------------------------------------------------

    def add_attribution(self, record_type: str, record_id: str, action: str, account_id: str, user_id: str,
                        organization_id: str, display_name_snapshot: str, recorded_at: datetime,
                        basis: str = "RECORDED") -> None:
        _console_ts(recorded_at)
        self.db.execute("INSERT INTO baec.attributions (organization_id, workspace, record_type, record_id, action, "
                        "account_id, user_id, display_name_snapshot, recorded_at, basis) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                        (organization_id, self.workspace, record_type, str(record_id), action, account_id, user_id,
                         display_name_snapshot, recorded_at, basis))

    def attributions(self, account_id: str | None = None) -> list[dict]:
        sql = ("SELECT record_type, record_id, action, account_id, user_id, organization_id, display_name_snapshot, "
               "recorded_at, basis FROM baec.attributions WHERE organization_id = %s AND workspace = %s")
        args: tuple = ()
        if account_id is not None:
            sql, args = sql + " AND account_id = %s", (account_id,)
        return [{"record_type": r[0], "record_id": r[1], "action": r[2], "account_id": r[3], "user_id": r[4],
                 "organization_id": r[5], "display_name_snapshot": r[6], "recorded_at": r[7], "basis": r[8]}
                for r in self._rows(sql + " ORDER BY seq", args)]

    def attribution_backfill(self) -> str:
        return BACKFILL_DONE


# --- CRM -------------------------------------------------------------------------------------------------------------

_CRM_TABLES = {"contacts": ("crm_contacts", "contact_id"), "tasks": ("crm_tasks", "task_id"),
               "activities": ("crm_activities", "activity_id")}


class PgCRMStore:
    """CRMStore for one organization's workspace."""

    def __init__(self, connection, organization_id: str, workspace: str) -> None:
        self.db = connection
        self._scope = (organization_id, workspace)

    def _rows(self, sql: str, args: tuple = ()) -> list[tuple]:
        return self.db.execute(sql, self._scope + args).fetchall()

    def add_contact(self, contact_id: str, account_id: str, *, name: str, title: str, email: str, phone: str,
                    notes: str, created_at: datetime, created_by: str) -> None:
        _crm_ts(created_at)
        self.db.execute("INSERT INTO baec.crm_contacts (organization_id, workspace, contact_id, account_id, name, title, "
                        "email, phone, notes, active, created_at, created_by) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, true, %s, %s)",
                        self._scope + (contact_id, account_id, name.strip(), title.strip() or None,
                                       email.strip() or None, phone.strip() or None, notes.strip() or None,
                                       created_at, created_by))

    def contacts(self, account_id: str) -> list[dict]:
        rows = self._rows("SELECT contact_id, account_id, name, title, email, phone, notes, active, created_at, "
                          "created_by FROM baec.crm_contacts WHERE organization_id = %s AND workspace = %s "
                          "AND account_id = %s ORDER BY active DESC, lower(name), seq", (account_id,))
        return [{"contact_id": r[0], "account_id": r[1], "name": r[2], "title": r[3], "email": r[4], "phone": r[5],
                 "notes": r[6], "active": r[7], "created_at": r[8], "created_by": r[9]} for r in rows]

    def account_of(self, table: str, record_id: str) -> str | None:
        name, key = _CRM_TABLES[table]
        row = self.db.execute(f"SELECT account_id FROM baec.{name} WHERE organization_id = %s AND workspace = %s "
                              f"AND {key} = %s", self._scope + (record_id,)).fetchone()
        return None if row is None else row[0]

    def add_task(self, task_id: str, account_id: str, *, contact_id: str | None, title: str, due_date: date | None,
                 priority: str, notes: str, created_at: datetime, created_by: str) -> None:
        _crm_ts(created_at)
        self.db.execute("INSERT INTO baec.crm_tasks (organization_id, workspace, task_id, account_id, contact_id, title, "
                        "due_date, priority, status, notes, created_at, created_by) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'OPEN', %s, %s, %s)",
                        self._scope + (task_id, account_id, contact_id, title.strip(), due_date, priority,
                                       notes.strip() or None, created_at, created_by))

    def tasks(self, *, account_id: str | None = None, status: str | None = None) -> list[dict]:
        sql = ("SELECT task_id, account_id, contact_id, title, due_date, priority, status, notes, created_at, "
               "created_by, completed_at, completed_by FROM baec.crm_tasks WHERE organization_id = %s AND workspace = %s")
        args: tuple = ()
        if account_id is not None:
            sql, args = sql + " AND account_id = %s", args + (account_id,)
        if status is not None:
            sql, args = sql + " AND status = %s", args + (status,)
        sql += (" ORDER BY CASE status WHEN 'OPEN' THEN 0 ELSE 1 END, "
                "CASE priority WHEN 'HIGH' THEN 0 WHEN 'NORMAL' THEN 1 ELSE 2 END, "
                "CASE WHEN due_date IS NULL THEN 1 ELSE 0 END, due_date, created_at DESC, seq")
        return [{"task_id": r[0], "account_id": r[1], "contact_id": r[2], "title": r[3], "due_date": r[4],
                 "priority": r[5], "status": r[6], "notes": r[7], "created_at": r[8], "created_by": r[9],
                 "completed_at": r[10], "completed_by": r[11]} for r in self._rows(sql, args)]

    def complete_task(self, task_id: str, *, completed_at: datetime, completed_by: str) -> None:
        row = self._rows("SELECT status FROM baec.crm_tasks WHERE organization_id = %s AND workspace = %s "
                         "AND task_id = %s", (task_id,))
        if not row:
            raise CRMStoreError("Task does not exist.")
        if row[0][0] == "DONE":
            raise CRMStoreError("Task is already complete.")
        _crm_ts(completed_at)
        self.db.execute("UPDATE baec.crm_tasks SET status = 'DONE', completed_at = %s, completed_by = %s "
                        "WHERE organization_id = %s AND workspace = %s AND task_id = %s",
                        (completed_at, completed_by) + self._scope + (task_id,))

    def link_interaction(self, interaction_id: str, account_id: str, *, contact_id: str | None,
                         interaction_type: str, linked_at: datetime) -> None:
        _crm_ts(linked_at)
        self.db.execute("INSERT INTO baec.crm_interaction_links (organization_id, workspace, interaction_id, account_id, "
                        "contact_id, interaction_type, linked_at) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                        self._scope + (interaction_id, account_id, contact_id, interaction_type, linked_at))

    def interaction_link(self, interaction_id: str) -> dict | None:
        rows = self._rows("SELECT interaction_id, account_id, contact_id, interaction_type, linked_at "
                          "FROM baec.crm_interaction_links WHERE organization_id = %s AND workspace = %s "
                          "AND interaction_id = %s", (interaction_id,))
        if not rows:
            return None
        r = rows[0]
        return {"interaction_id": r[0], "account_id": r[1], "contact_id": r[2], "interaction_type": r[3],
                "linked_at": r[4]}

    def add_activity(self, activity_id: str, account_id: str, *, contact_id: str | None, interaction_type: str,
                     occurred_at: datetime, raw_notes: str, summary: str, buyer_role: str, buyer_statement: str,
                     seller_notes: str, ai_assisted: bool, created_at: datetime, created_by: str) -> None:
        _crm_ts(occurred_at)
        _crm_ts(created_at)
        self.db.execute("INSERT INTO baec.crm_activities (organization_id, workspace, activity_id, account_id, "
                        "contact_id, interaction_type, occurred_at, raw_notes, summary, buyer_role, buyer_statement, "
                        "seller_notes, engine1_interaction_id, ai_assisted, created_at, created_by) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NULL, %s, %s, %s)",
                        self._scope + (activity_id, account_id, contact_id, interaction_type, occurred_at,
                                       raw_notes.strip(), summary.strip() or None, buyer_role.strip() or None,
                                       buyer_statement.strip() or None, seller_notes.strip() or None,
                                       bool(ai_assisted), created_at, created_by))

    def activities(self, account_id: str) -> list[dict]:
        rows = self._rows("SELECT activity_id, account_id, contact_id, interaction_type, occurred_at, raw_notes, "
                          "summary, buyer_role, buyer_statement, seller_notes, engine1_interaction_id, ai_assisted, "
                          "created_at, created_by FROM baec.crm_activities WHERE organization_id = %s "
                          "AND workspace = %s AND account_id = %s ORDER BY occurred_at DESC, created_at DESC, seq",
                          (account_id,))
        return [{"activity_id": r[0], "account_id": r[1], "contact_id": r[2], "interaction_type": r[3],
                 "occurred_at": r[4], "raw_notes": r[5], "summary": r[6], "buyer_role": r[7], "buyer_statement": r[8],
                 "seller_notes": r[9], "engine1_interaction_id": r[10], "ai_assisted": r[11], "created_at": r[12],
                 "created_by": r[13]} for r in rows]

    def attach_engine1_interaction(self, activity_id: str, interaction_id: str) -> None:
        rows = self._rows("SELECT engine1_interaction_id FROM baec.crm_activities WHERE organization_id = %s "
                          "AND workspace = %s AND activity_id = %s", (activity_id,))
        if not rows:
            raise CRMStoreError("Activity does not exist.")
        if rows[0][0] is not None:
            raise CRMStoreError("Activity is already linked to Engine 1.")
        self.db.execute("UPDATE baec.crm_activities SET engine1_interaction_id = %s "
                        "WHERE organization_id = %s AND workspace = %s AND activity_id = %s",
                        (interaction_id,) + self._scope + (activity_id,))


# --- sessions --------------------------------------------------------------------------------------------------------


@dataclass
class ProductSession:
    """The identity, console, CRM, Engine 1, and Engine 2 stores of one organization's workspace, sharing one tenant
    transaction: their writes commit together or not at all (the SQLite stores are five files and cannot)."""

    connection: object
    organization_id: str
    workspace: str
    identity: PgIdentityStore = field(init=False)
    console: PgConsoleStore = field(init=False)
    crm: PgCRMStore = field(init=False)
    _engine1: Engine1Connection | None = field(init=False, default=None)
    _engine2: Engine2Connection | None = field(init=False, default=None)

    def __post_init__(self) -> None:
        if self.workspace not in WORKSPACES:
            raise ConsoleStoreError(f"unknown workspace {self.workspace!r}")
        self.identity = PgIdentityStore(self.connection)
        self.console = PgConsoleStore(self.connection, self.organization_id, self.workspace)
        self.crm = PgCRMStore(self.connection, self.organization_id, self.workspace)

    @property
    def engine1(self) -> Engine1Connection:
        """Engine 1's tables in this workspace (created on first use; needs a transaction acting in the workspace)."""
        if self._engine1 is None:
            self._engine1 = Engine1Connection(self.connection)
        return self._engine1

    @property
    def engine2(self) -> Engine2Connection:
        """Engine 2's tables in this workspace (created on first use; needs a transaction acting in the workspace)."""
        if self._engine2 is None:
            self._engine2 = Engine2Connection(self.connection)
        return self._engine2


class PostgresProductStorage:
    """Product storage over a pool of application-role connections."""

    def __init__(self, database: TenantDatabase) -> None:
        self.database = database

    @contextmanager
    def session(self, organization_id: str, workspace: str) -> Iterator[ProductSession]:
        if workspace not in WORKSPACES:
            raise ConsoleStoreError(f"unknown workspace {workspace!r}")
        with self.database.tenant(organization_id, workspace) as connection:
            yield ProductSession(connection, organization_id, workspace)

