"""BAEC Console service layer: one workspace's product operations, with no Streamlit dependency.

A workspace is a directory holding three SQLite files:

    engine1.sqlite3   the released Engine 1 schema (baec_app.data.database.open_database)
    engine2.sqlite3   the released Engine 2 store (baec_app.engine2.persistence.open_engine2_database)
    console.sqlite3   the console-owned store (console_store.py)

The console runs two workspaces: "local" for accounts the user creates, and "synthetic" for demo data. Engine 1
and Engine 2 stores are append-only with no delete path, so synthetic data is kept in its own files and "reset"
removes only those files. User-created accounts live in other files and are never touched. Each organization has its
own synthetic workspace directory (synthetic_directory), so one organization's demo load or reset never reads,
changes, or blocks another's.

Every operation opens its connections, does its work through the engine adapters, and closes them. Authoritative
progression is always read back from the stored records, never from UI session state.

Storage backend (backends.py). The service reaches its stores only through a backend: SQLiteBackend (development,
the files above) or PostgresBackend (PostgreSQL test mode: identity, console, CRM, Engine 1, and Engine 2 in
PostgreSQL). The service code is the same for both. Each public operation runs inside one backend unit: on PostgreSQL
one tenant transaction, so an operation's product, Engine 1, and Engine 2 writes commit together or not at all. On
SQLite each store is its own file, so a write that spans Engine 1 and a product store is not atomic there (unchanged
V0.3 behavior).

Tenancy. A ConsoleService is bound to one verified Principal. Every account belongs to exactly one organization
(console_store.account_ownership); engine records are reached only through an account
the principal's organization owns. Each operation resolves the record it is given (draft, BAEC, candidate, review,
request, preparation, task, activity, interaction) to its account and checks ownership before reading or writing
anything. A record that does not exist and a record owned by another organization raise the same AccessDenied.
On SQLite this isolation is enforced here, in application code: anything that opens the SQLite files directly
bypasses it. On PostgreSQL every engine and product row also carries its organization and workspace, and row-level
security enforces the same isolation in the database; the checks here remain as defense in depth.

Principal freshness. Every public operation of ConsoleService and Console first re-validates the principal against
the identity store (_guarded): the user must still exist, be active, and belong to the same organization. A user
deactivated while a cached Console is running loses access on the next operation, without a restart. The display
name is not part of this check. A production console (production.py) also carries the sign-in credential it was
opened for, and the same guard re-checks it first: the sign-in link must still be unrevoked and name the same user and
organization, and the session must not be too old.

Attribution. Human actions record the principal's display name as their actor label, as before, and each one also
records an attribution (console_store.attributions): the user_id and organization_id that performed it, with the
display name at that moment as an immutable snapshot. No caller supplies an actor.
"""

from __future__ import annotations

import functools
import hashlib
import inspect
import threading
import uuid
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterator

from baec_app.application.context import Clock, SystemClock
from baec_app.data.records import SourceInteraction
from baec_app.data.repository import Repository
from baec_app.domain.enums import (
    AccountState,
    BaecClassification,
    NoPlausiblePathGround,
    ReviewAnswer,
)
from baec_app.domain.models import Account, EvaluationEvidence, NonEvaluationEvidence
from baec_app.engine2 import persistence as e2store
from baec_app.engine2.domain import CorrespondenceDimension, CorrespondenceOutcome
from baec_app.engine3.enums import (
    ArtifactOrigin,
    HumanReviewDecision,
    PreparationChannel,
    PreparationGoal,
)
from baec_app.engine3.errors import Engine3ValidationError

from . import engine1_adapter as e1a
from . import engine2_adapter as e2a
from . import engine3_adapter as e3a
from . import deployment, identity
from .backends import RESET_UNAVAILABLE, CommitsRecordedRefusal, PostgresBackend, SQLiteBackend
from .console_store import (
    ATTR_ACCOUNT,
    ATTR_BAEC_CONFIRMATION,
    ATTR_CRM_ACTIVITY,
    ATTR_CRM_ACTIVITY_ENGINE1_LINK,
    ATTR_CRM_CONTACT,
    ATTR_CRM_INTERACTION_LINK,
    ATTR_CRM_TASK,
    ATTR_DORMANCY_JUDGMENT,
    ATTR_DRAFT,
    ATTR_DRAFT_RESOLUTION,
    ATTR_ENGINE2,
    ATTR_EVENT,
    ATTR_INTERACTION,
    ATTR_PREPARATION,
    ATTR_PREPARATION_BLOCK,
    ATTR_PREPARATION_EDIT,
    ATTR_PREPARATION_REQUEST,
    ATTR_PREPARATION_REVIEW,
    ATTR_STATE_TRANSITION,
    BACKFILL_PENDING,
    ConsoleStore,
    display_time,
    legacy_actor_name,
)
from .crm_store import CRMStore
from .deterministic_preparation import GENERATOR, build_artifacts
from .identity import Principal

LOCAL = "local"
SYNTHETIC = "synthetic"
SYNTHETIC_ORGANIZATIONS = "synthetic-organizations"
IDENTITY_FILE = "identity.sqlite3"
PREPARATION_ACTOR = "Deterministic preparation"
NOT_ACCESSIBLE = "That record does not exist or is not available to you."
SEEDING_UNAVAILABLE = ("Synthetic demo data is not available to a signed-in production user: production never acts as "
                       "the synthetic-seed identity. Nothing was changed.")
RENAME_UNAVAILABLE = "In production, display names are set by your BAEC administrator. Nothing was changed."
ATTESTATIONS = (
    ("supported", "Every factual statement is supported by the recorded grounding."),
    ("permitted", "Every fact stated to the buyer is permitted for external use."),
    ("no_state_claims", "Nothing claims the buyer is evaluating, intends to buy, or that an opportunity exists."),
    ("no_internal_exposure", "No internal-only source or monitoring method is exposed."),
)
STAGES = ("REMEMBER", "MONITOR", "RECOGNIZE", "PREPARE")
STATE_LABELS = {
    AccountState.ACTIVE_OPPORTUNITY: "Active Opportunity",
    AccountState.CONDITIONALLY_DORMANT: "Conditionally Dormant",
    AccountState.NO_PLAUSIBLE_PATH: "No Plausible Path",
    None: "Not classified",
}


class ConsoleError(Exception):
    """A console guard refused the action. The message says what must happen next."""


class AccessDenied(ConsoleError):
    """The record does not exist, or the principal's organization does not own it. The two are not distinguished."""

    def __init__(self) -> None:
        super().__init__(NOT_ACCESSIBLE)


class PrincipalInvalid(ConsoleError):
    """The principal no longer matches an active user of its organization. Nothing was read or written."""

    def __init__(self) -> None:
        super().__init__("Your user is not active in this organization, so nothing was read or changed.")


class PreparationRefused(CommitsRecordedRefusal, ConsoleError):
    """Engine 3 refused preparation. When generation is refused, the refusal event the action recorded is kept."""

    def __init__(self, message: str, report) -> None:
        super().__init__(message)
        self.report = report


@dataclass(frozen=True)
class TimelineItem:
    at: datetime
    title: str
    actor_kind: str  # Human | Engine 1 | Engine 2 | Engine 3 | System
    actor: str
    stage: str
    event_type: str
    detail: str = ""
    account_id: str = ""
    actor_user_id: str | None = None  # the attributed user, when the action has an attribution


@dataclass
class AccountView:
    """Everything the workspace page shows for one account, read from stored records."""

    account: Account
    meta: dict
    workspace: str
    interactions: list = field(default_factory=list)  # [(SourceInteraction, meta)]
    drafts: list = field(default_factory=list)
    baecs: list = field(default_factory=list)  # BaecRecord, in stored order
    judgments: dict = field(default_factory=dict)  # baec_id -> [PersistedDormancyJudgment]
    transitions: list = field(default_factory=list)
    current_baec: object = None  # the latest CONFIRMED_BAEC record, if any
    plan: object = None  # e2a.PlanRefs for current_baec
    plan_record: object = None  # MonitoringPlan
    sources: dict = field(default_factory=dict)
    observations: list = field(default_factory=list)
    superseded: dict = field(default_factory=dict)  # observation id -> superseding id
    measurements: list = field(default_factory=list)
    candidates: list = field(default_factory=list)  # [{"candidate", "reviews": [RecordedReview]}]
    requests: list = field(default_factory=list)
    preparations: list = field(default_factory=list)
    blocks: dict = field(default_factory=dict)  # Engine 2 review id -> human block decisions, in order
    stage: str = "REMEMBER"
    next_action: str = ""
    last_activity: datetime | None = None

    @property
    def display_name(self) -> str:
        return self.meta.get("display_name") or self.account.name

    def current_request(self, review_id: str) -> dict | None:
        return current_requests(self.requests).get(review_id)

    def preparation_replaced(self, preparation: dict) -> bool:
        request = next(r for r in self.requests if r["request_id"] == preparation["request_id"])
        return _request_replaced(request, self.requests)

    @property
    def synthetic(self) -> bool:
        return bool(self.meta.get("synthetic"))

    @property
    def pending_drafts(self) -> list:
        return [d for d in self.drafts if d["resolution"] is None]

    def verified_correspondences(self) -> list[dict]:
        """Every HUMAN_VERIFIED_CORRESPONDENCE review, in recording order. None is singled out as authoritative.

        Only these records can be selected for preparation; POSSIBLE and NO outcomes never qualify.
        """
        out = []
        for c in self.candidates:
            later = []
            for review, outcome in reversed(c["reviews"]):
                if outcome is CorrespondenceOutcome.HUMAN_VERIFIED_CORRESPONDENCE:
                    out.append({"review": review, "candidate": c["candidate"], "later_reviews": list(reversed(later)),
                                "blocked": bool(self.blocks.get(review.review_id))
                                and self.blocks[review.review_id][-1]["action"] == "BLOCK",
                                "block_history": self.blocks.get(review.review_id, [])})
                later.append((review, outcome))
        return sorted(out, key=lambda v: v["review"].reviewed_at)


def _token() -> str:
    return uuid.uuid4().hex[:12].upper()


def synthetic_directory(root: str | Path, organization_id: str) -> Path:
    """One organization's synthetic workspace directory. The local-development organization keeps the V0.3 location,
    so synthetic data that existed before V0.4 stays where it was; every other organization gets its own directory,
    named by a digest of its id so any id is a safe directory name."""
    if organization_id == identity.LOCAL_ORGANIZATION_ID:
        return Path(root) / SYNTHETIC
    return Path(root) / SYNTHETIC_ORGANIZATIONS / hashlib.sha256(organization_id.encode("utf-8")).hexdigest()[:32]


# --- principal freshness ---------------------------------------------------------------------------------------------
# Public methods that touch no stored data. Every other public method is guarded (test_identity_freshness checks it).
UNGUARDED_METHODS = frozenset({"ids", "now"})
_GUARD = threading.local()


def _guard(method):
    @functools.wraps(method)
    def guarded(self, *args, **kwargs):
        with self._unit():  # one backend unit per operation; a nested operation joins it
            active = _GUARD.__dict__.setdefault("active", set())
            key = self._guard_key()
            if key in active:  # an outer operation for this same principal already checked it
                return method(self, *args, **kwargs)
            self._check_principal()
            active.add(key)
            try:
                return method(self, *args, **kwargs)
            finally:
                active.discard(key)
    guarded.principal_guarded = True
    return guarded


def _guarded(cls):
    """Wrap every public method of `cls` (except UNGUARDED_METHODS) in the principal-freshness check, so a method
    added later is guarded without anyone remembering to add a call."""
    for name, value in list(vars(cls).items()):
        if not name.startswith("_") and name not in UNGUARDED_METHODS and inspect.isfunction(value):
            setattr(cls, name, _guard(value))
    return cls


class _PrincipalBound:
    """Holds one principal and re-validates it against the identity store before each operation."""

    principal: Principal
    _backend: SQLiteBackend | PostgresBackend
    authentication_required: bool = False
    # Production only: the sign-in it was opened for (production.SignedInCredential). check(principal) raises
    # IdentityError once the link is revoked or changed, or the session is too old.
    _credential = None
    _shared_adopt = None  # set by a Console on its services, so the current display name reaches all three

    def _guard_key(self) -> tuple:
        return (self._backend.key, self.principal.user_id, self.principal.organization_id)

    def _unit(self):
        return nullcontext()

    def _check_principal(self) -> None:
        """Fail closed unless the user still exists, is active, and belongs to the same organization (and, when
        authentication is required, is an authentication-eligible HUMAN user, and the sign-in credential, if any, is
        still valid). The display name is not compared; the user's current one is adopted, so new actions record it."""
        try:
            if self._credential is not None:
                self._credential.check(self.principal)
            with self._backend.identity(self.principal.organization_id) as store:
                current = store.verify(self.principal,
                                       require_authentication_eligible=self.authentication_required)
        except identity.IdentityError:
            raise PrincipalInvalid() from None
        self._adopt(current)

    def _adopt(self, principal: Principal) -> None:
        if self._shared_adopt is not None:
            self._shared_adopt(principal)
        else:
            self.principal = principal


@_guarded
class ConsoleService(_PrincipalBound):
    """Product operations for one workspace of one organization, on one storage backend (SQLite by default)."""

    def __init__(self, root: str | Path, workspace: str, clock: Clock | None = None, *, principal: Principal,
                 authentication_required: bool = False, backend=None, credential=None) -> None:
        if workspace not in (LOCAL, SYNTHETIC):
            raise ConsoleError(f"unknown workspace {workspace!r}")
        if not isinstance(principal, Principal):
            raise ConsoleError("a console service needs a principal")
        self.root = Path(root)
        self._backend = backend or SQLiteBackend(self.root)
        self.identity_path = self.root / IDENTITY_FILE
        self.authentication_required = authentication_required
        self._credential = credential
        self.principal = principal
        self.workspace = workspace
        self.directory = self.root / LOCAL if workspace == LOCAL else synthetic_directory(self.root,
                                                                                         principal.organization_id)
        self._store = self._backend.workspace(self.directory, principal.organization_id, workspace)
        self.clock = clock or SystemClock()
        self._prefix = "SYN-" if workspace == SYNTHETIC else ""
        self.initialize()

    # --- files and connections ------------------------------------------------------------------------------

    @property
    def engine1_path(self) -> str:
        return str(self.directory / "engine1.sqlite3")

    @property
    def engine2_path(self) -> str:
        return str(self.directory / "engine2.sqlite3")

    @property
    def console_path(self) -> str:
        return str(self.directory / "console.sqlite3")

    @property
    def crm_path(self) -> str:
        return str(self.directory / "crm.sqlite3")

    def initialize(self) -> None:
        """Create the workspace stores on first use; verify their schemas on every later use."""
        self._store.create()
        self._backfill_ownership()
        self._backfill_attribution()

    def _backfill_ownership(self) -> None:
        """V0.3 data: assign every account that existed before ownership to the local-development organization,
        once, whoever opens the store first. Idempotent; account ids and every engine record stay as they were."""
        with self._cs() as cs:
            if cs.ownership_backfill() != BACKFILL_PENDING:
                return
            with self._e1_repo() as repo:
                existing = [a.account_id for a in repo.list_accounts()]
            with self._writing(cs):
                cs.complete_ownership_backfill(existing, identity.LOCAL_ORGANIZATION_ID, self.now(),
                                               identity.LOCAL_USER_ID)

    def _backfill_attribution(self) -> None:
        """Records made before attribution existed (V0.3, and V0.4 before Phase 2): attribute each recorded human
        action to a user only where the stored records prove who it was (_backfill_user). Runs once per store,
        whoever opens it first. Nothing existing changes: every actor label stays exactly as recorded, a record
        that cannot be attributed stays unattributed, and the outcome is written to the migration log."""
        with self._cs() as cs:
            if cs.attribution_backfill() != BACKFILL_PENDING:
                return
            candidates = cs.labelled_records()
        with self._crm() as crm:
            candidates += [(_CRM_ATTRIBUTION[table], *rest) for table, *rest in crm.labelled_records()]
        candidates += self._engine_labelled_records()
        with self._cs() as cs:
            owners = {c[3]: cs.organization_of(c[3]) for c in candidates}
        with self._backend.identity(self.principal.organization_id) as store:
            users = {org: store.users(org) for org in set(owners.values()) if org is not None}
        rows, counts = [], {}
        for record_type, record_id, action, account_id, label, at in candidates:
            organization_id = owners[account_id]
            user = _backfill_user(users.get(organization_id, []), label) if organization_id else None
            count = counts.setdefault(record_type, {"attributed": 0, "unattributed": 0})
            count["attributed" if user else "unattributed"] += 1
            if user is not None:
                rows.append({"record_type": record_type, "record_id": record_id, "action": action,
                             "account_id": account_id, "user_id": user.user_id, "organization_id": organization_id,
                             "display_name_snapshot": label, "recorded_at": at})
        detail = {"rule": _BACKFILL_RULE, "by_record_type": counts}
        with self._cs() as cs, self._writing(cs):
            cs.complete_attribution_backfill(rows, detail, self.now())

    def _engine_labelled_records(self) -> list[tuple]:
        """Engine 1 and Engine 2 records that carry a human actor label, as attribution-backfill candidates."""
        out, confirmed = [], []
        with self._e1_repo() as repo:
            for account in repo.list_accounts():
                account_id = account.account_id
                for record in repo.list_baec_records(account_id):
                    if record.classification is BaecClassification.CONFIRMED_BAEC:
                        c = record.confirmation
                        out.append((ATTR_BAEC_CONFIRMATION, record.baec_id, "CONFIRMED", account_id, c.authorized_by,
                                    c.authorized_at))
                        confirmed.append((account_id, record.baec_id))
                    for judgment in repo.list_dormancy_judgments(record.baec_id):
                        a = judgment.judgment.authorization
                        out.append((ATTR_DORMANCY_JUDGMENT, str(judgment.judgment_id), "RECORDED", account_id,
                                    a.authorized_by, a.authorized_at))
                for t in repo.get_transition_history(account_id):
                    out.append((ATTR_STATE_TRANSITION, str(t.transition_id), "RECORDED", account_id,
                                t.authorization.authorized_by, t.recorded_at))
        with self._e2() as e2:
            accounts = {}
            for account_id, baec_id in confirmed:
                for plan in e2a.plans_for_baec(e2, baec_id):
                    accounts.update(dict.fromkeys(e2a.entity_ids(e2, plan), account_id))
            for event in e2store.audit_events(e2):
                mapped = _ENGINE2_EVENTS.get(event.event_type)
                if mapped and mapped[1] == "Human" and event.entity_id in accounts:
                    out.append((ATTR_ENGINE2 + event.entity_type, event.entity_id, event.event_type,
                                accounts[event.entity_id], event.actor, event.recorded_at))
        return out

    def destroy(self) -> None:
        """Remove this workspace's files. Used only for the synthetic workspace reset, and only on SQLite.

        The directory is this organization's own (synthetic_directory). As a second check, the reset is still refused
        if any account in it belongs to another organization. PostgreSQL storage refuses before reading anything: its
        records are append-only (backends.RESET_UNAVAILABLE).
        """
        if self.workspace != SYNTHETIC:
            raise ConsoleError("only the synthetic workspace can be reset")
        if not self._store.resettable:
            raise ConsoleError(RESET_UNAVAILABLE)
        with self._cs() as cs:
            others = cs.owning_organizations() - {self.principal.organization_id}
        if others:
            raise ConsoleError("Synthetic data belongs to another organization and cannot be reset from here.")
        self._store.destroy()
        self.initialize()

    def ids(self, prefix: str) -> str:
        return f"{self._prefix}{prefix}-{_token()}"

    def now(self) -> datetime:
        return self.clock.now()

    @property
    def actor(self) -> str:
        """The label recorded on this principal's human actions."""
        return self.principal.display_name

    # --- attribution ---------------------------------------------------------------------------------------------

    def _attribute(self, cs: ConsoleStore, record_type: str, record_id, account_id: str, action: str,
                   at: datetime) -> None:
        """Record that this principal's user performed `action` on the record, with the current display name as an
        immutable snapshot. Call inside the console transaction that writes the record itself."""
        cs.add_attribution(record_type, record_id, action, account_id, self.principal.user_id,
                           self.principal.organization_id, self.actor, at)

    def _attribute_after(self, account_id: str, at: datetime, *entries: tuple[str, object, str]) -> None:
        """Attributions for records just written to another store (CRM, Engine 1, Engine 2). On SQLite the two files
        cannot share a transaction: if this write fails, the record keeps its actor label and has no attribution. On
        PostgreSQL, CRM, Engine 1, and Engine 2 records share the operation's transaction, so both are rolled back. A
        record is never attributed to anyone else."""
        with self._cs() as cs, self._writing(cs):
            for record_type, record_id, action in entries:
                self._attribute(cs, record_type, record_id, account_id, action, at)

    def _human_event(self, cs: ConsoleStore, account_id: str, event_type: str, at: datetime, summary: str,
                     ref_id: str | None = None, *, actor_kind: str = "Human") -> None:
        event_id = cs.add_event(account_id, event_type, actor_kind, self.actor, at, summary, ref_id)
        self._attribute(cs, ATTR_EVENT, event_id, account_id, "RECORDED", at)

    def attributions(self, account_id: str) -> list[dict]:
        """Who performed each attributed action on one owned account: the user_id and organization_id, and the
        display name the user had at that moment. In recording order."""
        self._authorize(account_id)
        with self._cs() as cs:
            return cs.attributions(account_id)

    @contextmanager
    def _e1(self) -> Iterator[e1a.Engine1Session]:
        with self._store.engine1() as connection:
            yield e1a.Engine1Session(connection, self.clock)

    @contextmanager
    def _e1_repo(self) -> Iterator[Repository]:
        with self._store.engine1() as connection:
            yield Repository(connection)

    def _unit(self):
        return self._store.unit()

    def _e2(self):
        return self._store.engine2()

    def _cs(self) -> Iterator[ConsoleStore]:
        return self._store.console()

    def _crm(self) -> Iterator[CRMStore]:
        return self._store.crm()

    def _writing(self, store):
        """A block of writes to one store that commit together (SQLite: its file's transaction; PostgreSQL: a
        savepoint of the operation's transaction)."""
        return self._store.writing(store)

    # --- accounts --------------------------------------------------------------------------------------------

    def create_account(self, name: str, *, industry: str = "", owner_note: str = "", synthetic: bool = False) -> str:
        """A new, unclassified account (state None), owned by the principal's organization. Nothing about the buyer
        is inferred from creating it."""
        if not name or not name.strip():
            raise ConsoleError("Enter an account name.")
        if synthetic != (self.workspace == SYNTHETIC):
            raise ConsoleError("synthetic accounts belong only in the synthetic workspace")
        account_id = self.ids("ACC")
        engine_name = name.strip() + (" (synthetic)" if synthetic else "")
        now = self.now()
        with self._e1_repo() as repo:
            repo.add_account(Account(account_id, engine_name))
        # Ownership is written with the metadata. On PostgreSQL a failure here rolls the Engine 1 account back too; on
        # SQLite (separate files) the account stays, with no owner, invisible to everyone: it fails closed.
        with self._cs() as cs, self._writing(cs):
            cs.add_ownership(account_id, self.principal.organization_id, now, self.principal.user_id)
            cs.add_account_meta(account_id, name.strip(), industry.strip() or None, owner_note.strip() or None,
                                synthetic, now, self.actor)
            self._attribute(cs, ATTR_ACCOUNT, account_id, account_id, "CREATED", now)
            self._human_event(cs, account_id, "ACCOUNT_CREATED", now,
                              "Synthetic account created" if synthetic else "Account created",
                              actor_kind="System" if synthetic else "Human")
        return account_id

    def account_ids(self) -> list[str]:
        """The accounts the principal's organization owns, in Engine 1 order."""
        with self._cs() as cs:
            owned = cs.owned_account_ids(self.principal.organization_id)
        with self._e1_repo() as repo:
            return [a.account_id for a in repo.list_accounts() if a.account_id in owned]

    def has_account(self, account_id: str) -> bool:
        with self._cs() as cs:
            return cs.organization_of(account_id) == self.principal.organization_id

    # --- authorization ------------------------------------------------------------------------------------------
    # Each _account_of_* resolves a record to its account and returns it only if the principal's organization owns
    # that account. Any failure to resolve (no such record, broken lineage, another organization) is AccessDenied.

    def _authorize(self, account_id: str) -> str:
        if not isinstance(account_id, str) or not self.has_account(account_id):
            raise AccessDenied()
        return account_id

    def _resolve(self, lookup) -> str:
        try:
            account_id = lookup()
        except Exception:  # noqa: BLE001 - an unresolvable record is indistinguishable from an inaccessible one
            raise AccessDenied() from None
        return self._authorize(account_id)

    def _account_of_interaction(self, interaction_id: str) -> str:
        def lookup():
            with self._e1_repo() as repo:
                return repo.get_interaction(interaction_id).account_id
        return self._resolve(lookup)

    def _account_of_baec(self, baec_id: str) -> str:
        def lookup():
            with self._e1_repo() as repo:
                return repo.get_baec_record(baec_id).candidate.account_id
        return self._resolve(lookup)

    def _account_of_draft(self, draft_id: str) -> str:
        def lookup():
            with self._cs() as cs:
                return cs.draft(draft_id)["account_id"]
        return self._resolve(lookup)

    def _baec_of_engine2(self, kind: str, record_id: str) -> str:
        with self._e2() as e2:
            if kind == "candidate":
                return e2store.load_correspondence_candidate(e2, record_id).baec_id
            if kind == "review":
                return e2store.load_review(e2, record_id).candidate.baec_id
            observation = e2store.load_observation(e2, record_id)
            return e2store.load_monitoring_plan(e2, observation.monitoring_plan_id, observation.plan_version).baec_id

    def _account_of_engine2(self, kind: str, record_id: str) -> str:
        def lookup():
            baec_id = self._baec_of_engine2(kind, record_id)
            with self._e1_repo() as repo:
                return repo.get_baec_record(baec_id).candidate.account_id
        return self._resolve(lookup)

    def _account_of_request(self, request_id: str) -> str:
        def lookup():
            with self._cs() as cs:
                return cs.preparation_request(request_id)["account_id"]
        return self._resolve(lookup)

    def _account_of_preparation(self, preparation_id: str) -> str:
        def lookup():
            with self._cs() as cs:
                return cs.preparation_request(cs.preparation(preparation_id)["request_id"])["account_id"]
        return self._resolve(lookup)

    def _account_of_crm(self, table: str, record_id: str) -> str:
        def lookup():
            with self._crm() as crm:
                return crm.account_of(table, record_id)
        return self._resolve(lookup)

    def _require_contact(self, account_id: str, contact_id: str | None) -> None:
        """A contact attached to a CRM record must belong to that same account."""
        if contact_id and self._account_of_crm("contacts", contact_id) != account_id:
            raise ConsoleError("That contact belongs to a different account.")

    # --- CRM operating layer -----------------------------------------------------------------------------------

    def crm_add_contact(
        self,
        account_id: str,
        *,
        name: str,
        title: str = "",
        email: str = "",
        phone: str = "",
        notes: str = "",
    ) -> str:
        self._authorize(account_id)
        if not name or not name.strip():
            raise ConsoleError("Enter a contact name.")

        contact_id = self.ids("CON")
        now = self.now()

        with self._crm() as crm:
            crm.add_contact(
                contact_id,
                account_id,
                name=name,
                title=title,
                email=email,
                phone=phone,
                notes=notes,
                created_at=now,
                created_by=self.actor,
            )
        self._attribute_after(account_id, now, (ATTR_CRM_CONTACT, contact_id, "CREATED"))

        return contact_id

    def crm_contacts(self, account_id: str) -> list[dict]:
        self._authorize(account_id)
        with self._crm() as crm:
            return crm.contacts(account_id)

    def crm_add_task(
        self,
        account_id: str,
        *,
        contact_id: str | None,
        title: str,
        due_date,
        priority: str,
        notes: str = "",
    ) -> str:
        self._authorize(account_id)
        self._require_contact(account_id, contact_id)
        if not title or not title.strip():
            raise ConsoleError("Enter a task.")
        if priority not in ("LOW", "NORMAL", "HIGH"):
            raise ConsoleError("Unknown task priority.")

        task_id = self.ids("TASK")
        now = self.now()

        with self._crm() as crm:
            crm.add_task(
                task_id,
                account_id,
                contact_id=contact_id,
                title=title,
                due_date=due_date,
                priority=priority,
                notes=notes,
                created_at=now,
                created_by=self.actor,
            )
        self._attribute_after(account_id, now, (ATTR_CRM_TASK, task_id, "CREATED"))

        return task_id

    def crm_tasks(
        self,
        *,
        account_id: str | None = None,
        status: str | None = None,
    ) -> list[dict]:
        """Tasks for one owned account, or, with no account, for every account the organization owns."""
        if account_id is not None:
            self._authorize(account_id)
        owned = set(self.account_ids())
        with self._crm() as crm:
            tasks = crm.tasks(
                account_id=account_id,
                status=status,
            )
        return [t for t in tasks if t["account_id"] in owned]

    def crm_complete_task(
        self,
        task_id: str,
    ) -> None:
        account_id = self._account_of_crm("tasks", task_id)
        now = self.now()
        with self._crm() as crm:
            crm.complete_task(
                task_id,
                completed_at=now,
                completed_by=self.actor,
            )
        self._attribute_after(account_id, now, (ATTR_CRM_TASK, task_id, "COMPLETED"))

    def crm_link_interaction(
        self,
        interaction_id: str,
        account_id: str,
        *,
        contact_id: str | None,
        interaction_type: str,
    ) -> None:
        if self._account_of_interaction(interaction_id) != self._authorize(account_id):
            raise ConsoleError("That interaction belongs to a different account.")
        self._require_contact(account_id, contact_id)
        now = self.now()
        with self._crm() as crm:
            crm.link_interaction(
                interaction_id,
                account_id,
                contact_id=contact_id,
                interaction_type=interaction_type,
                linked_at=now,
            )
        self._attribute_after(account_id, now, (ATTR_CRM_INTERACTION_LINK, interaction_id, "LINKED"))

    def crm_interaction_link(
        self,
        interaction_id: str,
    ) -> dict | None:
        """The CRM link for an interaction, or None. A link on another organization's account is also None."""
        with self._crm() as crm:
            link = crm.interaction_link(interaction_id)
        if link is None or not self.has_account(link["account_id"]):
            return None
        return link

    def crm_add_activity(
        self,
        account_id: str,
        *,
        contact_id: str | None,
        interaction_type: str,
        occurred_at: datetime,
        raw_notes: str,
        summary: str = "",
        buyer_role: str = "",
        buyer_statement: str = "",
        seller_notes: str = "",
        ai_assisted: bool = False,
    ) -> str:
        self._authorize(account_id)
        self._require_contact(account_id, contact_id)

        if not raw_notes or not raw_notes.strip():
            raise ConsoleError("Enter interaction notes.")

        activity_id = self.ids("ACT")
        now = self.now()

        with self._crm() as crm:
            crm.add_activity(
                activity_id,
                account_id,
                contact_id=contact_id,
                interaction_type=interaction_type,
                occurred_at=occurred_at,
                raw_notes=raw_notes,
                summary=summary,
                buyer_role=buyer_role,
                buyer_statement=buyer_statement,
                seller_notes=seller_notes,
                ai_assisted=ai_assisted,
                created_at=now,
                created_by=self.actor,
            )
        self._attribute_after(account_id, now, (ATTR_CRM_ACTIVITY, activity_id, "CREATED"))

        return activity_id

    def crm_activities(
        self,
        account_id: str,
    ) -> list[dict]:
        self._authorize(account_id)
        with self._crm() as crm:
            return crm.activities(account_id)

    def crm_attach_engine1_interaction(
        self,
        activity_id: str,
        interaction_id: str,
    ) -> None:
        account_id = self._account_of_crm("activities", activity_id)
        if self._account_of_interaction(interaction_id) != account_id:
            raise ConsoleError("That interaction belongs to a different account.")
        with self._crm() as crm:
            crm.attach_engine1_interaction(
                activity_id,
                interaction_id,
            )
        self._attribute_after(account_id, self.now(), (ATTR_CRM_ACTIVITY_ENGINE1_LINK, activity_id, "ATTACHED"))

    # --- Engine 1: interactions and conditions ----------------------------------------------------------------

    def record_interaction(self, account_id: str, *, buyer_role: str, occurred_at: datetime, buyer_statement: str,
                           seller_notes: str = "") -> str:
        self._authorize(account_id)
        if not buyer_statement or not buyer_statement.strip():
            raise ConsoleError("Record the buyer's exact statement.")
        statement = buyer_statement.strip()
        text = e1a.compose_interaction_text(buyer_role, statement, seller_notes)
        interaction_id = self.ids("INT")
        with self._e1_repo() as repo:
            repo.add_interaction(SourceInteraction(interaction_id, account_id, occurred_at, text))
        now = self.now()
        with self._cs() as cs, self._writing(cs):
            cs.add_interaction_meta(interaction_id, account_id, buyer_role.strip() or None, statement,
                                    seller_notes.strip() or None, now, self.actor)
            self._attribute(cs, ATTR_INTERACTION, interaction_id, account_id, "RECORDED", now)
        return interaction_id

    def _candidate(self, repo: Repository, cs: ConsoleStore, draft: dict):
        interaction = repo.get_interaction(draft["interaction_id"])
        meta = cs.interaction_meta(draft["interaction_id"])
        if meta is None:
            raise ConsoleError("This interaction was not recorded through BAEC Console.")
        form = e1a.ConditionForm.from_dict(draft["form"])
        return e1a.build_candidate(draft["account_id"], interaction, meta["buyer_statement"], meta["buyer_role"], form)

    def capture_condition(self, account_id: str, interaction_id: str, form: e1a.ConditionForm) -> str:
        """Store what the salesperson captured. Builds the Engine 1 candidate first, so invalid input is refused."""
        if self._account_of_interaction(interaction_id) != self._authorize(account_id):
            raise ConsoleError("That interaction belongs to a different account.")
        with self._e1_repo() as repo, self._cs() as cs:
            interaction = repo.get_interaction(interaction_id)
            meta = cs.interaction_meta(interaction_id)
            if meta is None:
                raise ConsoleError("This interaction was not recorded through BAEC Console.")
            e1a.build_candidate(account_id, interaction, meta["buyer_statement"], meta["buyer_role"], form)
            draft_id = self.ids("DRAFT")
            now = self.now()
            with self._writing(cs):
                cs.add_draft(draft_id, account_id, interaction_id, form.to_dict(), now, self.actor)
                self._attribute(cs, ATTR_DRAFT, draft_id, account_id, "CAPTURED", now)
                self._human_event(cs, account_id, "CONDITION_CAPTURED", now,
                                  "Condition captured (not yet confirmed)", draft_id)
        return draft_id

    def preview_condition(self, draft_id: str) -> dict:
        """The locked classifier's preview of exactly what would be recorded. Writes nothing."""
        self._account_of_draft(draft_id)
        with self._e1() as session, self._cs() as cs:
            draft = cs.draft(draft_id)
            candidate = self._candidate(session.repository, cs, draft)
            preview = session.facade.preview_classification(candidate)
        return {"draft": draft, "candidate": candidate, "preview": preview,
                "fingerprint": e1a.fingerprint(candidate)}

    def _open_draft(self, cs: ConsoleStore, draft_id: str) -> dict:
        draft = cs.draft(draft_id)
        if draft["resolution"] is not None:
            raise ConsoleError("This captured condition has already been recorded.")
        return draft

    def confirm_condition(self, draft_id: str, *, displayed_fingerprint: str) -> str:
        """Human confirmation through the Engine 1 gate. Only a confirmable candidate can be confirmed."""
        self._account_of_draft(draft_id)
        with self._e1() as session, self._cs() as cs:
            draft = self._open_draft(cs, draft_id)
            candidate = self._candidate(session.repository, cs, draft)
            if e1a.fingerprint(candidate) != displayed_fingerprint:
                raise ConsoleError("The condition changed after it was previewed. Preview it again before confirming.")
            human = session.human_session(self.actor)
            request = session.facade.request_baec_confirmation(human, candidate, captured_at=draft["captured_at"])
            record = session.approve_and_run(human, request, session.facade.confirm_baec)
            now = self.now()
            with self._writing(cs):
                cs.resolve_draft(draft_id, "CONFIRMED", record.baec_id, now, self.actor)
                self._attribute(cs, ATTR_DRAFT_RESOLUTION, draft_id, draft["account_id"], "CONFIRMED", now)
                self._attribute(cs, ATTR_BAEC_CONFIRMATION, record.baec_id, draft["account_id"], "CONFIRMED",
                                record.confirmation.authorized_at)
        return record.baec_id

    def record_not_confirmable(self, draft_id: str, *, displayed_fingerprint: str) -> str:
        """Preserve a NOT_BAEC or INSUFFICIENT_EVIDENCE classification with its reason (RC-10, RC-13)."""
        self._account_of_draft(draft_id)
        with self._e1() as session, self._cs() as cs:
            draft = self._open_draft(cs, draft_id)
            candidate = self._candidate(session.repository, cs, draft)
            if e1a.fingerprint(candidate) != displayed_fingerprint:
                raise ConsoleError("The condition changed after it was previewed. Preview it again.")
            record = session.facade.save_nonconfirmed_classification(candidate, captured_at=draft["captured_at"])
            now = self.now()
            with self._writing(cs):
                cs.resolve_draft(draft_id, "RECORDED_NOT_CONFIRMABLE", record.baec_id, now, self.actor)
                self._attribute(cs, ATTR_DRAFT_RESOLUTION, draft_id, draft["account_id"],
                                "RECORDED_NOT_CONFIRMABLE", now)
        return record.baec_id

    def discard_draft(self, draft_id: str) -> None:
        self._account_of_draft(draft_id)
        with self._cs() as cs:
            draft = self._open_draft(cs, draft_id)
            now = self.now()
            with self._writing(cs):
                cs.resolve_draft(draft_id, "DISCARDED", None, now, self.actor)
                self._attribute(cs, ATTR_DRAFT_RESOLUTION, draft_id, draft["account_id"], "DISCARDED", now)
                self._human_event(cs, draft["account_id"], "CONDITION_DISCARDED", now, "Captured condition discarded",
                                  draft_id)

    # --- Engine 1: account state (all through the human gate) ---------------------------------------------------

    def record_dormancy_judgment(self, baec_id: str, *, plausibility: ReviewAnswer, addressability: ReviewAnswer,
                                 notes: str) -> int:
        account_id = self._account_of_baec(baec_id)
        with self._e1() as session:
            human = session.human_session(self.actor)
            request = session.facade.request_dormancy_judgment(human, baec_id, plausibility=plausibility,
                                                               addressability=addressability,
                                                               notes=notes.strip() or None)
            judgment_id = session.approve_and_run(human, request, session.facade.record_dormancy_judgment).judgment_id
            stored = next(j for j in session.repository.list_dormancy_judgments(baec_id)
                          if j.judgment_id == judgment_id)
        at = stored.judgment.authorization.authorized_at
        self._attribute_after(account_id, at, (ATTR_DORMANCY_JUDGMENT, str(judgment_id), "RECORDED"))
        return judgment_id

    def _evidence(self, repo: Repository, cs: ConsoleStore, kind: type, account_id: str, interaction_id: str,
                  excerpt: str):
        if not interaction_id or not excerpt or not excerpt.strip():
            return None
        if self._account_of_interaction(interaction_id) != account_id:
            raise ConsoleError("That interaction belongs to a different account.")
        interaction = repo.get_interaction(interaction_id)
        meta = cs.interaction_meta(interaction_id)
        statement = meta["buyer_statement"] if meta else ""
        return e1a.build_evidence(kind, account_id, interaction, statement, excerpt.strip(), interaction.occurred_at)

    def _transition_inputs(self, session: e1a.Engine1Session, cs: ConsoleStore, account_id: str, inputs: dict) -> dict:
        self._authorize(account_id)
        if inputs.get("baec_id") and self._account_of_baec(inputs["baec_id"]) != account_id:
            raise ConsoleError("That condition belongs to a different account.")
        repo = session.repository
        return {
            "non_evaluation_evidence": self._evidence(repo, cs, NonEvaluationEvidence, account_id,
                                                      inputs.get("non_eval_interaction", ""),
                                                      inputs.get("non_eval_excerpt", "")),
            "evaluation_evidence": self._evidence(repo, cs, EvaluationEvidence, account_id,
                                                  inputs.get("eval_interaction", ""), inputs.get("eval_excerpt", "")),
        }

    def preview_state_change(self, account_id: str, target: AccountState, inputs: dict):
        """The locked state machine's answer, without authorization. Writes nothing."""
        with self._e1() as session, self._cs() as cs:
            ev = self._transition_inputs(session, cs, account_id, inputs)
            f = session.facade
            if target is AccountState.CONDITIONALLY_DORMANT:
                return f.preview_move_to_conditionally_dormant(
                    account_id, baec_id=inputs["baec_id"], judgment_id=inputs["judgment_id"],
                    non_evaluation_evidence=ev["non_evaluation_evidence"])
            if target is AccountState.NO_PLAUSIBLE_PATH:
                return f.preview_move_to_no_plausible_path(
                    account_id, ground=inputs.get("ground"), reason=(inputs.get("reason") or "").strip() or None,
                    non_evaluation_evidence=ev["non_evaluation_evidence"])
            return f.preview_move_to_active_opportunity(account_id, evaluation_evidence=ev["evaluation_evidence"])

    def change_account_state(self, account_id: str, target: AccountState, inputs: dict):
        """Request, human approval, and the approval-only Engine 1 command. The state machine decides.

        Engine 1 returns no transition id, so the transition is attributed only if exactly one new transition
        appears on the account during this operation; otherwise it stays unattributed rather than guessed."""
        self._authorize(account_id)
        with self._e1_repo() as repo:
            before = {t.transition_id for t in repo.get_transition_history(account_id)}
        result = self._change_account_state(account_id, target, inputs)
        with self._e1_repo() as repo:
            new = [t for t in repo.get_transition_history(account_id) if t.transition_id not in before]
        if len(new) == 1:
            self._attribute_after(account_id, new[0].recorded_at,
                                  (ATTR_STATE_TRANSITION, str(new[0].transition_id), "RECORDED"))
        return result

    def _change_account_state(self, account_id: str, target: AccountState, inputs: dict):
        with self._e1() as session, self._cs() as cs:
            ev = self._transition_inputs(session, cs, account_id, inputs)
            f = session.facade
            human = session.human_session(self.actor)
            if target is AccountState.CONDITIONALLY_DORMANT:
                request = f.request_move_to_conditionally_dormant(
                    human, account_id, baec_id=inputs["baec_id"], judgment_id=inputs["judgment_id"],
                    non_evaluation_evidence=ev["non_evaluation_evidence"])
                return session.approve_and_run(human, request, f.move_to_conditionally_dormant)
            if target is AccountState.NO_PLAUSIBLE_PATH:
                ground = inputs.get("ground")
                request = f.request_move_to_no_plausible_path(
                    human, account_id, ground=ground if isinstance(ground, NoPlausiblePathGround) else None,
                    reason=(inputs.get("reason") or "").strip() or None,
                    non_evaluation_evidence=ev["non_evaluation_evidence"])
                return session.approve_and_run(human, request, f.move_to_no_plausible_path)
            request = f.request_move_to_active_opportunity(human, account_id,
                                                           evaluation_evidence=ev["evaluation_evidence"])
            return session.approve_and_run(human, request, f.move_to_active_opportunity)

    # --- Engine 2: monitoring and correspondence ----------------------------------------------------------------

    def handoff(self, baec_id: str):
        """The Stage K handoff, or HandoffRefused explaining why monitoring is not eligible."""
        self._account_of_baec(baec_id)
        return e2a.read_handoff(self._store.engine1_reader, baec_id)

    def start_monitoring(self, account_id: str, baec_id: str, *, target_entities: tuple[str, ...],
                         required: frozenset[CorrespondenceDimension], sources: tuple[e2a.SourceForm, ...]
                         ) -> e2a.PlanRefs:
        self._authorize(account_id)
        handoff = self.handoff(baec_id)
        if handoff.account_id != account_id:
            raise ConsoleError("That condition belongs to a different account.")
        entities = tuple(dict.fromkeys(e.strip() for e in target_entities if e and e.strip()))
        if not entities:
            raise ConsoleError("Name at least one organization to monitor.")
        now = self.now()
        with self._e2() as e2:
            if any(p.activation_result == "ACTIVATED" for p in e2a.plans_for_baec(e2, baec_id)):
                raise ConsoleError("Monitoring is already active for this condition.")
            refs = e2a.start_monitoring(e2, handoff, ids=self.ids, target_entities=entities, required=required,
                                        sources=sources, actor=self.actor, now=now)
            plan = e2store.load_monitoring_plan(e2, refs.monitoring_plan_id, refs.plan_version)
        self._attribute_after(
            account_id, now,
            *((ATTR_ENGINE2 + "AuthorizedSource", s, "AUTHORIZED_SOURCE_RECORDED") for s in sorted(plan.authorized_source_ids)),
            (ATTR_ENGINE2 + "MonitoringPlan", f"{refs.monitoring_plan_id}@v{refs.plan_version}",
             "MONITORING_PLAN_RECORDED"))
        return refs

    def add_observation(self, baec_id: str, form: e2a.ObservationForm) -> e2a.ObservationResult:
        account_id = self._account_of_baec(baec_id)
        if form.corrects_observation_id and self._account_of_engine2("observation",
                                                                     form.corrects_observation_id) != account_id:
            raise ConsoleError("That observation belongs to a different account.")
        with self._e2() as e2:
            plans = [p for p in e2a.plans_for_baec(e2, baec_id) if p.activation_result == "ACTIVATED"]
            if not plans:
                raise ConsoleError("Monitoring is not active for this condition. Start monitoring first.")
            # Authorized sources are stored workspace-wide. Engine 2 refuses a source outside the plan, but its message
            # names the source; refuse first, the same way as an unknown source, so no other plan's source is revealed.
            plan = e2store.load_monitoring_plan(e2, plans[-1].monitoring_plan_id, plans[-1].plan_version)
            if form.source_id not in plan.authorized_source_ids:
                raise AccessDenied()
            now = self.now()
            result = e2a.add_observation(e2, plans[-1], form, ids=self.ids, actor=self.actor, now=now)
        entries = [(ATTR_ENGINE2 + "Observation", result.observation_id, "OBSERVATION_RECORDED")]
        if form.corrects_observation_id:
            entries.append((ATTR_ENGINE2 + "Supersession", form.corrects_observation_id, "SUPERSESSION_RECORDED"))
        if result.candidate_id:
            entries.append((ATTR_ENGINE2 + "CorrespondenceCandidate", result.candidate_id,
                            "CORRESPONDENCE_CANDIDATE_RECORDED"))
        self._attribute_after(account_id, now, *entries)
        return result

    def queue_observation_for_review(
        self, baec_id: str, observation_id: str
    ) -> str:
        """Queue an existing current observation for human correspondence review."""
        account_id = self._account_of_baec(baec_id)
        if self._account_of_engine2("observation", observation_id) != account_id:
            raise ConsoleError("That observation belongs to a different account.")
        with self._e2() as e2:
            plans = [
                p for p in e2a.plans_for_baec(e2, baec_id)
                if p.activation_result == "ACTIVATED"
            ]
            if not plans:
                raise ConsoleError("Monitoring is not active for this condition.")

            now = self.now()
            candidate_id = e2a.queue_observation_for_review(
                e2,
                plans[-1],
                observation_id,
                ids=self.ids,
                actor=self.actor,
                now=now,
            )
        self._attribute_after(account_id, now, (ATTR_ENGINE2 + "CorrespondenceCandidate", candidate_id,
                                                "CORRESPONDENCE_CANDIDATE_RECORDED"))
        return candidate_id

    def review_packet(self, candidate_id: str):
        self._account_of_engine2("candidate", candidate_id)
        with self._e2() as e2:
            return e2a.packet(e2, candidate_id)

    def submit_correspondence_review(self, candidate_id: str, *, displayed_context_digest: str,
                                     findings: dict, notes: dict, sufficiency: str,
                                     revalidation_triggers: tuple[str, ...] = (),
                                     check_notes: dict | None = None):
        account_id = self._account_of_engine2("candidate", candidate_id)
        now = self.now()
        with self._e2() as e2:
            recorded = e2a.submit_review(e2, candidate_id, displayed_context_digest=displayed_context_digest,
                                         findings=findings, notes=notes, sufficiency=sufficiency,
                                         revalidation_triggers=revalidation_triggers,
                                         check_notes=check_notes or {}, ids=self.ids, actor=self.actor, now=now)
        self._attribute_after(account_id, now, (ATTR_ENGINE2 + "HumanCorrespondenceReview", recorded.review.review_id,
                                                "HUMAN_REVIEW_RECORDED"))
        return recorded

    # --- Engine 3: preparation -----------------------------------------------------------------------------------

    def preparation_snapshot(self, review_id: str) -> dict:
        """Engine 3's upstream view of exactly this review, including any human preparation block on it."""
        self._account_of_engine2("review", review_id)
        with self._cs() as cs:
            blocked = cs.is_blocked(review_id)
        with self._e2() as e2:
            return e3a.upstream_snapshot(self._store.engine1_reader, e2, review_id, blocked=blocked)

    def _require_verified(self, review_id: str) -> dict:
        snap = self.preparation_snapshot(review_id)
        if not snap["lineage_reconstructable"]:
            raise ConsoleError("That correspondence record could not be reconstructed.")
        if not e3a.is_verified(snap["outcome"]):
            raise ConsoleError("Only human-verified correspondence can be selected for preparation.")
        return snap

    def block_preparation(self, review_id: str, *, reason: str) -> None:
        """A human decision that no preparation may be drafted from this correspondence. Append-only."""
        self._set_block(review_id, "BLOCK", reason)

    def unblock_preparation(self, review_id: str, *, reason: str) -> None:
        """A new, separate human decision lifting an earlier block. The earlier decision stays on record."""
        self._set_block(review_id, "UNBLOCK", reason)

    def _set_block(self, review_id: str, action: str, reason: str) -> None:
        snap = self._require_verified(review_id)
        now = self.now()
        with self._cs() as cs:
            if cs.is_blocked(review_id) == (action == "BLOCK"):
                raise ConsoleError("Preparation is already blocked for this correspondence." if action == "BLOCK"
                                   else "Preparation is not blocked for this correspondence.")
            with self._writing(cs):
                decision_id = cs.add_block_decision(snap["account_id"], review_id, action, reason.strip() or None, now,
                                                    self.actor)
                self._attribute(cs, ATTR_PREPARATION_BLOCK, decision_id, snap["account_id"], action, now)
                self._human_event(cs, snap["account_id"], f"PREPARATION_{action}ED", now,
                                  ("Preparation blocked by human decision" if action == "BLOCK"
                                   else "Preparation block lifted by human decision")
                                  + f" (correspondence verified {snap['reviewed_on']})", review_id)

    def preparation_eligibility(self, review_id: str):
        """Engine 3's eligibility answer for a review, assuming the human then requests preparation."""
        snap = self.preparation_snapshot(review_id)
        return snap, e3a.eligibility_report(snap, request_present=True)

    def request_preparation(self, review_id: str, request: dict) -> str:
        """A separate human Preparation Request. It authorizes drafting only, never outreach."""
        self._require_verified(review_id)  # an explicit, qualifying correspondence record; never substituted
        snap, report = self.preparation_eligibility(review_id)
        if not report.allowed:
            raise PreparationRefused("Preparation is not eligible.", report)
        PreparationChannel(request["channel"])
        PreparationGoal(request["goal"])
        if request.get("statement_use") not in ("PARAPHRASE", "QUOTE"):
            raise ConsoleError("Choose how drafts may refer to the buyer's earlier statement.")
        if request["statement_use"] == "PARAPHRASE" and not (request.get("paraphrase") or "").strip():
            raise ConsoleError("Write how the buyer's condition should be described back to them.")
        request_id = self.ids("PREQ")
        now = self.now()
        with self._cs() as cs, self._writing(cs):
            cs.add_preparation_request(request_id, snap["account_id"], snap["baec_id"], review_id,
                                       snap["candidate_id"], request, snap, e3a.context_digest(snap, request), now,
                                       self.actor)
            self._attribute(cs, ATTR_PREPARATION_REQUEST, request_id, snap["account_id"], "REQUESTED", now)
            self._human_event(cs, snap["account_id"], "PREPARATION_REQUESTED", now,
                              f"Preparation requested from the correspondence verified {snap['reviewed_on']} "
                              "(drafting only; not outreach authorization)", request_id)
        return request_id

    def _current_context(self, stored_request: dict):
        current = self.preparation_snapshot(stored_request["review_id"])  # the request's own bound review
        with self._cs() as cs:
            replaced = _request_replaced(stored_request, cs.preparation_requests(stored_request["account_id"]))
        state = e3a.context_state(stored_request, current, newer_request_exists=replaced)
        upstream = e3a.upstream_eligibility(current, request_present=True)
        grounding = e3a.grounding_items(current, stored_request["request"]) if current["lineage_reconstructable"] else ()
        return upstream, state, grounding

    def generate_preparation(self, request_id: str) -> str:
        """Build the template outputs and submit each, unchanged, to the Engine 3 validator."""
        self._account_of_request(request_id)
        with self._cs() as cs:
            stored = cs.preparation_request(request_id)
            if any(p["request_id"] == request_id for p in cs.preparations()):
                raise ConsoleError("Preparation was already generated for this request.")
        upstream, state, grounding = self._current_context(stored)
        # Engine 3 decides first whether anything may be drafted at all (eligibility, human block, stale context).
        # When it refuses, nothing is generated; the refusal is recorded as an event.
        gate = e3a.validate_case(e3a.ValidationCase(upstream=upstream, context=state))
        if not gate.allowed:
            with self._cs() as cs, self._writing(cs):
                cs.add_event(stored["account_id"], "PREPARATION_REFUSED", "Engine 3", "Engine 3 validator", self.now(),
                             "Preparation refused by Engine 3: " + "; ".join(
                                 e3a.reason_text(i.reason_code.value) for i in gate.issues), request_id)
            raise PreparationRefused("Engine 3 refused preparation.", gate)
        outputs = []
        try:
            artifacts = build_artifacts(stored["snapshot"], stored["request"])
        except Engine3ValidationError as error:
            artifacts = []
            outputs.append({"artifact": None, "report": e3a.structural_refusal(str(error))})
        for artifact in artifacts:
            report = e3a.validate(artifact, e3a.TEMPLATE_VALIDATOR_ORIGIN, upstream, state, grounding)
            outputs.append({"artifact": e3a.artifact_to_dict(artifact), "report": e3a.report_to_dict(report),
                            "audience": artifact.audience.value})
        preparation_id = self.ids("PREP")
        now = self.now()
        passed = sum(1 for o in outputs if o["report"]["allowed"])
        with self._cs() as cs, self._writing(cs):
            cs.add_preparation(preparation_id, request_id, GENERATOR, outputs, now, PREPARATION_ACTOR)
            # The template generated it; the attribution records which user asked for the generation.
            self._attribute(cs, ATTR_PREPARATION, preparation_id, stored["account_id"], "GENERATION_REQUESTED", now)
            cs.add_event(stored["account_id"], "PREPARATION_GENERATED", "Engine 3", PREPARATION_ACTOR, now,
                         f"Preparation generated; {passed} of {len(outputs)} outputs passed Engine 3 validation",
                         preparation_id)
        return preparation_id

    def _revalidate(self, preparation: dict, artifact_dict: dict, origin: ArtifactOrigin):
        with self._cs() as cs:
            stored = cs.preparation_request(preparation["request_id"])
        upstream, state, grounding = self._current_context(stored)
        return e3a.validate(e3a.artifact_from_dict(artifact_dict), origin, upstream, state, grounding)

    def edit_artifact(self, preparation_id: str, artifact_id: str, segments: list[dict]) -> dict:
        """A human edit becomes a new HUMAN_EDITED artifact, validated again. The original is never changed."""
        self._account_of_preparation(preparation_id)
        with self._cs() as cs:
            preparation = cs.preparation(preparation_id)
            if artifact_id in cs.reviews(preparation_id):
                raise ConsoleError("This draft has already been reviewed.")
            stored = cs.preparation_request(preparation["request_id"])
            _refuse_if_replaced(stored, cs.preparation_requests(stored["account_id"]))
            parent = next(o for o in preparation["artifacts"]
                          if o["artifact"] and o["artifact"]["artifact_id"] == artifact_id)
        edit_id = self.ids("EDIT")
        try:
            edited = e3a.build_edited_artifact(e3a.artifact_from_dict(parent["artifact"]), edit_id, segments)
        except Engine3ValidationError as error:
            raise ConsoleError(f"The edited draft is not well formed: {error}") from error
        upstream, state, grounding = self._current_context(stored)
        report = e3a.validate(edited, ArtifactOrigin.HUMAN_EDITED, upstream, state, grounding)
        record = {"artifact": e3a.artifact_to_dict(edited), "report": e3a.report_to_dict(report),
                  # A human edit is its own provenance: never the deterministic template, never a model.
                  "provenance": {"console_origin": "HUMAN_EDIT", "engine3_validator_origin": ArtifactOrigin.HUMAN_EDITED.value,
                                 "edited_by": self.actor, "parent_preparation_id": preparation_id,
                                 "parent_artifact_id": artifact_id}}
        now = self.now()
        with self._cs() as cs, self._writing(cs):
            cs.add_edit(edit_id, preparation_id, artifact_id, record, now, self.actor)
            self._attribute(cs, ATTR_PREPARATION_EDIT, edit_id, stored["account_id"], "EDITED", now)
            self._human_event(cs, stored["account_id"], "DRAFT_EDITED", now, "Draft edited by human; original preserved",
                              edit_id)
            cs.add_event(stored["account_id"], "DRAFT_EDIT_VALIDATED", "Engine 3", "Engine 3 validator", now,
                         "Human edit passed Engine 3 validation" if report.allowed
                         else "Human edit refused by Engine 3 validation", edit_id)
        return {"edit_id": edit_id, **record}

    def review_artifact(self, preparation_id: str, artifact_id: str, decision: HumanReviewDecision, *,
                        attestations: dict, reason: str = "", edit_id: str | None = None) -> None:
        """Record the human preparation review. Acceptance requires a passing, current validation and attestations."""
        self._account_of_preparation(preparation_id)
        if not isinstance(decision, HumanReviewDecision):
            raise ConsoleError("unknown review decision")
        with self._cs() as cs:
            preparation = cs.preparation(preparation_id)
            if artifact_id in cs.reviews(preparation_id):
                raise ConsoleError("This draft has already been reviewed.")
            stored = cs.preparation_request(preparation["request_id"])
            _refuse_if_replaced(stored, cs.preparation_requests(stored["account_id"]))  # Reject included
            output = next((o for o in preparation["artifacts"]
                           if o["artifact"] and o["artifact"]["artifact_id"] == artifact_id), None)
            edits = {e["edit_id"]: e for e in cs.edits(preparation_id)}
        if output is None:
            raise ConsoleError("No such draft in this preparation.")
        if decision is not HumanReviewDecision.REJECT:
            missing = [label for key, label in ATTESTATIONS if not attestations.get(key)]
            if missing:
                raise ConsoleError("Confirm every review statement before accepting: " + missing[0])
            if decision is HumanReviewDecision.ACCEPT_AS_DRAFTED:
                if not output["report"]["allowed"]:
                    raise ConsoleError("Engine 3 refused this draft; it cannot be accepted as drafted.")
                report = self._revalidate(preparation, output["artifact"], e3a.TEMPLATE_VALIDATOR_ORIGIN)
            else:
                edit = edits.get(edit_id or "")
                if edit is None or edit["parent_artifact_id"] != artifact_id:
                    raise ConsoleError("Choose the edited version to accept.")
                if not edit["artifact"]["report"]["allowed"]:
                    raise ConsoleError("Engine 3 refused this edit; revise it or reject the draft.")
                report = self._revalidate(preparation, edit["artifact"]["artifact"], ArtifactOrigin.HUMAN_EDITED)
            if not report.allowed:
                codes = ", ".join(e3a.reason_text(i.reason_code.value) for i in report.issues)
                raise ConsoleError(f"The records changed since this draft was validated: {codes}")
        now = self.now()
        titles = {HumanReviewDecision.ACCEPT_AS_DRAFTED: "Draft accepted by human",
                  HumanReviewDecision.EDIT: "Edited draft accepted by human",
                  HumanReviewDecision.REJECT: "Draft rejected by human"}
        with self._cs() as cs, self._writing(cs):
            cs.add_review(
                preparation_id,
                artifact_id,
                decision.value,
                edit_id if decision is HumanReviewDecision.EDIT else None,
                {k: bool(attestations.get(k)) for k, _ in ATTESTATIONS},
                reason.strip() or None,
                now,
                self.actor,
            )
            self._attribute(cs, ATTR_PREPARATION_REVIEW, f"{preparation_id}/{artifact_id}", stored["account_id"],
                            decision.value, now)
            self._human_event(
                cs,
                stored["account_id"],
                "DRAFT_REVIEWED",
                now,
                f"{titles[decision]}: "
                f"{e3a.ARTIFACT_LABELS[e3a.ArtifactType(output['artifact']['artifact_type'])]}",
                preparation_id,
            )

            current_reviews = cs.reviews(preparation_id)
            if preparation_status(preparation, current_reviews) == "Accepted":
                self._human_event(
                    cs,
                    stored["account_id"],
                    "PREPARATION_COMPLETED",
                    now,
                    "Preparation completed for human use; outreach remains a separate human decision",
                    preparation_id,
                )

    def preparation_view(self, preparation_id: str) -> dict:
        self._account_of_preparation(preparation_id)
        with self._cs() as cs:
            preparation = cs.preparation(preparation_id)
            request = cs.preparation_request(preparation["request_id"])
            replaced = _request_replaced(request, cs.preparation_requests(request["account_id"]))
            reviews = cs.reviews(preparation_id)
            edits = cs.edits(preparation_id)
            meta = cs.account_meta(request["account_id"]) or {}
            provenance = cs.provenance(preparation_id)
        return {"preparation": preparation, "request": request, "reviews": reviews, "edits": edits,
                "provenance": provenance,
                "status": preparation_status(preparation, reviews, replaced=replaced), "account_name": meta.get("display_name", ""),
                "workspace": self.workspace}

    def preparation_ids(self) -> list[str]:
        """Preparations on accounts the principal's organization owns."""
        owned = set(self.account_ids())
        with self._cs() as cs:
            accounts = {r["request_id"]: r["account_id"] for r in cs.preparation_requests()}
            return [p["preparation_id"] for p in cs.preparations() if accounts.get(p["request_id"]) in owned]

    # --- reads ---------------------------------------------------------------------------------------------------

    def account_view(self, account_id: str) -> AccountView:
        self._authorize(account_id)
        with self._e1_repo() as repo, self._cs() as cs:
            account = repo.get_account(account_id)
            meta = cs.account_meta(account_id) or {"display_name": account.name, "synthetic": False}
            view = AccountView(account=account, meta=meta, workspace=self.workspace)
            view.interactions = [(i, cs.interaction_meta(i.interaction_id)) for i in repo.list_interactions(account_id)]
            view.drafts = cs.drafts(account_id)
            view.baecs = list(repo.list_baec_records(account_id))
            view.judgments = {b.baec_id: list(repo.list_dormancy_judgments(b.baec_id)) for b in view.baecs}
            view.transitions = list(repo.get_transition_history(account_id))
            view.requests = cs.preparation_requests(account_id)
            request_ids = {r["request_id"] for r in view.requests}
            view.preparations = [{**p, "reviews": cs.reviews(p["preparation_id"]), "edits": cs.edits(p["preparation_id"]),
                                  "provenance": cs.provenance(p["preparation_id"])}
                                 for p in cs.preparations() if p["request_id"] in request_ids]
        confirmed = [b for b in view.baecs if b.classification is BaecClassification.CONFIRMED_BAEC]
        view.current_baec = confirmed[-1] if confirmed else None
        if view.current_baec is not None:
            with self._e2() as e2:
                plans = e2a.plans_for_baec(e2, view.current_baec.baec_id)
                active = [p for p in plans if p.activation_result == "ACTIVATED"]
                view.plan = active[-1] if active else (plans[-1] if plans else None)
                if view.plan is not None:
                    view.plan_record = e2store.load_monitoring_plan(e2, view.plan.monitoring_plan_id,
                                                                     view.plan.plan_version)
                    all_sources = e2store.load_authorized_sources(e2)
                    view.sources = {s: all_sources[s] for s in view.plan_record.authorized_source_ids}
                    view.observations = [e2store.load_observation(e2, i) for i in e2a.observation_ids(e2, view.plan)]
                    view.superseded = {s: e2store.load_supersession(e2, s).superseding_observation_id
                                       for s in e2a.superseded_ids(e2, view.plan)}
                    view.measurements = [e2store.load_derived_measurement(e2, i)
                                         for i in e2a.measurement_ids(e2, view.plan)]
                    for cid in e2a.candidate_ids(e2, view.plan):
                        reviews = [e2store.load_review(e2, r) for r in e2store.review_ids_for_candidate(e2, cid)]
                        view.candidates.append({"candidate": e2store.load_correspondence_candidate(e2, cid),
                                                "reviews": [(r, r.outcome) for r in reviews]})
                        with self._cs() as cs:
                            for r in reviews:
                                decisions = cs.block_decisions(r.review_id)
                                if decisions:
                                    view.blocks[r.review_id] = decisions
        _derive_progress(view)
        items = self.timeline(account_id, view=view)
        view.last_activity = items[-1].at if items else None
        return view

    def timeline(self, account_id: str, *, view: AccountView | None = None) -> list[TimelineItem]:
        """Account history from the engines' own records plus console events. Nothing is synthesized."""
        self._authorize(account_id)
        view = view or self.account_view(account_id)
        with self._cs() as cs:
            attributed = {(a["record_type"], a["record_id"], a["action"]): a["user_id"]
                          for a in cs.attributions(account_id)}

        def user(record_type: str, record_id, action: str = "RECORDED") -> str | None:
            return attributed.get((record_type, str(record_id), action))

        items: list[TimelineItem] = []
        add = items.append
        for interaction, meta in view.interactions:
            meta = meta or {}
            role = meta.get("buyer_role") or "buyer"
            add(TimelineItem(meta.get("recorded_at", interaction.occurred_at), "Buyer interaction recorded", "Human",
                             meta.get("recorded_by", ""), "REMEMBER", "INTERACTION",
                             f"With the {role}; the conversation took place "
                             f"{display_time(interaction.occurred_at):%d %b %Y, %H:%M}".replace(" 0", " "),
                             actor_user_id=user(ATTR_INTERACTION, interaction.interaction_id)))
        for record in view.baecs:
            if record.classification is BaecClassification.CONFIRMED_BAEC:
                add(TimelineItem(record.confirmation.authorized_at, "Buyer-articulated condition confirmed", "Human",
                                 record.confirmation.authorized_by, "REMEMBER", "BAEC_CONFIRMED",
                                 "Engine 1 classified it a confirmed BAEC; a human confirmed it",
                                 actor_user_id=user(ATTR_BAEC_CONFIRMATION, record.baec_id, "CONFIRMED")))
            else:
                add(TimelineItem(record.captured_at, "Condition recorded as not a confirmed BAEC", "Engine 1",
                                 "Engine 1 classifier", "REMEMBER", "BAEC_NOT_CONFIRMED",
                                 record.classification_reason or ""))
            for judgment in view.judgments.get(record.baec_id, []):
                j = judgment.judgment
                add(TimelineItem(j.authorization.authorized_at, "Dormancy judgment recorded", "Human",
                                 j.authorization.authorized_by, "ACCOUNT", "DORMANCY_JUDGMENT",
                                 f"Plausibility {j.plausibility.value.lower()}, "
                                 f"addressability {j.addressability.value.lower()}",
                                 actor_user_id=user(ATTR_DORMANCY_JUDGMENT, judgment.judgment_id)))
        for t in view.transitions:
            add(TimelineItem(t.recorded_at, f"Account state: {STATE_LABELS[t.to_state]}", "Human",
                             t.authorization.authorized_by, "ACCOUNT", "STATE_CHANGE",
                             f"From {STATE_LABELS[t.from_state]}",
                             actor_user_id=user(ATTR_STATE_TRANSITION, t.transition_id)))
        if view.current_baec is not None:
            items.extend(self._engine2_events(view, user))
        with self._cs() as cs:
            for e in cs.events(account_id):
                add(TimelineItem(e.occurred_at, e.summary, e.actor_kind, e.actor, _EVENT_STAGES.get(e.event_type,
                                 "ACCOUNT"), e.event_type, actor_user_id=user(ATTR_EVENT, e.event_id)))
        items = [TimelineItem(**{**i.__dict__, "account_id": account_id}) for i in items]
        return sorted(items, key=lambda i: i.at)

    def _engine2_events(self, view: AccountView, user) -> list[TimelineItem]:
        out = []
        with self._e2() as e2:
            plans = e2a.plans_for_baec(e2, view.current_baec.baec_id)
            owned = set().union(*(e2a.entity_ids(e2, p) for p in plans)) if plans else set()
            for event in e2store.audit_events(e2):
                if event.entity_id not in owned:
                    continue
                mapped = _ENGINE2_EVENTS.get(event.event_type)
                if mapped is None:
                    continue
                title, kind, stage = mapped
                detail = ""
                human = user(ATTR_ENGINE2 + event.entity_type, event.entity_id, event.event_type)
                if event.event_type == "HUMAN_REVIEW_RECORDED":
                    outcome = CorrespondenceOutcome(event.details["outcome"])
                    out.append(TimelineItem(event.recorded_at, "Correspondence review recorded", "Human", event.actor,
                                            "RECOGNIZE", event.event_type, actor_user_id=human))
                    title, kind = f"Engine 2 outcome: {e2a.OUTCOME_LABELS[outcome]}", "Engine 2"
                    detail = "Derived by the locked Engine 2 rule from the human's findings"
                if event.event_type == "SUPERSESSION_RECORDED" and event.details.get("retraction"):
                    title = "Earlier observation retracted"
                if event.event_type == "PLAN_ACTIVATION_REFUSED":
                    detail = event.details.get("refusal_reason", "")
                out.append(TimelineItem(event.recorded_at, title, kind,
                                        event.actor if kind == "Human" else "Engine 2", stage, event.event_type,
                                        detail, actor_user_id=human if kind == "Human" else None))
        return out

    def review_queue(self) -> dict:
        """Pending and completed human reviews in this workspace, read from stored records."""
        pending, completed = [], []
        for account_id in self.account_ids():
            view = self.account_view(account_id)
            name = view.display_name
            base = {"account_id": account_id, "account": name, "workspace": self.workspace}
            for d in view.drafts:
                row = {**base, "type": "Condition confirmation", "created": d["captured_at"],
                       "summary": (d.get("form") and _draft_summary(view, d)) or "", "ref": d["draft_id"],
                       "stage": "REMEMBER"}
                if d["resolution"] is None:
                    pending.append({**row, "status": "Awaiting human confirmation"})
                else:
                    completed.append({**row, "status": _DRAFT_STATUS[d["resolution"]]})
            for c in view.candidates:
                cand = c["candidate"]
                text = "; ".join(o.exact_evidence_content for o in cand.observations)
                created = min(o.observed_at for o in cand.observations)
                row = {**base, "type": "Correspondence review", "created": created, "summary": text,
                       "ref": cand.candidate_id, "stage": "RECOGNIZE"}
                if not c["reviews"]:
                    pending.append({**row, "status": "Awaiting human review"})
                else:
                    completed.append({**row, "status": e2a.OUTCOME_LABELS[c["reviews"][-1][1]]})
            for p in view.preparations:
                # Engine 3 refuses any draft whose request was replaced by a newer one for the same correspondence,
                # so those drafts are history, not human-review work. The records themselves are kept.
                replaced = view.preparation_replaced(p)
                for output in p["artifacts"]:
                    if not output["artifact"]:
                        continue
                    aid = output["artifact"]["artifact_id"]
                    label = e3a.ARTIFACT_LABELS[e3a.ArtifactType(output["artifact"]["artifact_type"])]
                    row = {**base, "type": "Draft review", "created": p["generated_at"],
                           "summary": f"{label}: {output['artifact']['rendered_text'][:140]}",
                           "ref": p["preparation_id"], "artifact_id": aid, "stage": "PREPARE"}
                    review = p["reviews"].get(aid)
                    if review is not None:
                        completed.append({**row, "status": _DECISION_STATUS[review["decision"]]})
                    elif output["report"]["allowed"] and replaced:
                        completed.append({**row, "status": REPLACED})
                    elif output["report"]["allowed"]:
                        pending.append({**row, "status": "Awaiting human review"})
                    else:
                        completed.append({**row, "status": "Refused by Engine 3 validation"})
        return {"pending": pending, "completed": completed}


REPLACED = "Replaced by a newer Preparation Request"


def current_requests(requests: list[dict]) -> dict[str, dict]:
    """The current Preparation Request for each bound Engine 2 review: the one stored last (highest seq).

    A newer request for the same correspondence replaces the older one. "Newer" is the store's insertion order,
    not requested_at, so a clock that moves backward or repeats a timestamp cannot make two requests current.
    """
    current: dict[str, dict] = {}
    for r in requests:
        if r["review_id"] not in current or r["seq"] > current[r["review_id"]]["seq"]:
            current[r["review_id"]] = r
    return current


def _request_replaced(request: dict, requests: list[dict]) -> bool:
    return current_requests(requests)[request["review_id"]]["request_id"] != request["request_id"]


def _refuse_if_replaced(request: dict, requests: list[dict]) -> None:
    """No human decision or edit is recorded against a replaced request's drafts; earlier decisions stay as they are."""
    if _request_replaced(request, requests):
        raise ConsoleError(e3a.REASON_TEXT[e3a.ReasonCode.PREPARATION_REQUEST_CHANGED])


def _draft_summary(view: AccountView, draft: dict) -> str:
    for interaction, meta in view.interactions:
        if interaction.interaction_id == draft["interaction_id"] and meta:
            return meta["buyer_statement"]
    return ""


_DRAFT_STATUS = {"CONFIRMED": "Confirmed by human", "RECORDED_NOT_CONFIRMABLE": "Recorded as not a confirmed BAEC",
                 "DISCARDED": "Discarded"}
_DECISION_STATUS = {"ACCEPT_AS_DRAFTED": "Accepted as drafted", "EDIT": "Edited and accepted", "REJECT": "Rejected"}
_EVENT_STAGES = {"ACCOUNT_CREATED": "ACCOUNT", "CONDITION_CAPTURED": "REMEMBER", "CONDITION_DISCARDED": "REMEMBER",
                 "PREPARATION_REQUESTED": "PREPARE", "PREPARATION_BLOCKED": "PREPARE", "PREPARATION_UNBLOCKED": "PREPARE",
                 "PREPARATION_REFUSED": "PREPARE", "PREPARATION_GENERATED": "PREPARE", "DRAFT_EDITED": "PREPARE",
                 "DRAFT_EDIT_VALIDATED": "PREPARE", "DRAFT_REVIEWED": "PREPARE",
                 "PREPARATION_COMPLETED": "PREPARE"}
_ENGINE2_EVENTS = {
    "AUTHORIZED_SOURCE_RECORDED": ("Source authorized for monitoring", "Human", "MONITOR"),
    "MONITORING_PLAN_RECORDED": ("Monitoring plan authorized", "Human", "MONITOR"),
    "PLAN_ACTIVATION_RECORDED": ("Monitoring activated", "Engine 2", "MONITOR"),
    "PLAN_ACTIVATION_REFUSED": ("Monitoring activation refused", "Engine 2", "MONITOR"),
    "SOURCE_ITEM_REFUSED": ("Source item refused", "Engine 2", "MONITOR"),
    "OBSERVATION_RECORDED": ("New observation added", "Human", "MONITOR"),
    "SUPERSESSION_RECORDED": ("Earlier observation corrected", "Human", "MONITOR"),
    "DERIVED_MEASUREMENT_RECORDED": ("Measurement derived", "Engine 2", "MONITOR"),
    "CORRESPONDENCE_CANDIDATE_RECORDED": ("Flagged for correspondence review", "Human", "RECOGNIZE"),
    "HUMAN_REVIEW_RECORDED": ("Correspondence review recorded", "Human", "RECOGNIZE"),
}
_CRM_ATTRIBUTION = {"contacts": ATTR_CRM_CONTACT, "tasks": ATTR_CRM_TASK, "activities": ATTR_CRM_ACTIVITY}
_BACKFILL_RULE = ("A recorded actor label on an account owned by organization O is attributed to user U of O only if "
                  "U is O's SYSTEM_SEED user and the label is U's display name, or U is the only non-seed user O has "
                  "ever had (users are never deleted) and the label is U's display name. Otherwise unattributed.")


def _backfill_user(users: list[identity.User], label: str) -> identity.User | None:
    """The user a pre-attribution actor label provably names, or None. See _BACKFILL_RULE.

    A label alone does not identify a user: names change and can be reused. It is proof only when the organization
    has a single possible human actor whose name is that label, or the label is the fixed name of the seed user.
    Labels from before a rename therefore stay unattributed.
    """
    for user in users:
        if user.kind is identity.UserKind.SYSTEM_SEED and user.display_name == label:
            return user
    others = [u for u in users if u.kind is not identity.UserKind.SYSTEM_SEED]
    if len(others) == 1 and others[0].display_name == label:
        return others[0]
    return None


def preparation_status(preparation: dict, reviews: dict, *, replaced: bool = False) -> str:
    """Needs review | Accepted | Rejected | REPLACED, derived from the validation reports and human decisions.

    A replaced preparation with undecided drafts is no longer work: Engine 3 refuses them. Decisions already
    recorded keep their outcome.
    """
    reviewable = [o["artifact"]["artifact_id"] for o in preparation["artifacts"]
                  if o["artifact"] and o["report"]["allowed"]]
    if any(aid not in reviews for aid in reviewable):
        return REPLACED if replaced else "Needs review"
    if any(r["decision"] in ("ACCEPT_AS_DRAFTED", "EDIT") for r in reviews.values()):
        return "Accepted"
    return "Rejected"


def _derive_progress(view: AccountView) -> None:
    """Workflow stage and next action, from stored records only."""
    def set_(stage: str, action: str) -> None:
        view.stage, view.next_action = stage, action

    if not view.interactions:
        return set_("REMEMBER", "Record a buyer interaction")
    if view.current_baec is None:
        if view.pending_drafts:
            return set_("REMEMBER", "Confirm the captured condition")
        return set_("REMEMBER", "Capture the buyer's condition")
    if view.plan is None:
        return set_("MONITOR", "Start monitoring")
    if view.plan.activation_result != "ACTIVATED":
        return set_("MONITOR", "Monitoring activation was refused")
    if any(not c["reviews"] for c in view.candidates):
        return set_("RECOGNIZE", "Review possible correspondence")
    verified = view.verified_correspondences()
    if not verified:
        return set_("MONITOR", "Add observations as evidence arrives")
    # Progress is read across every verified correspondence; no single review is treated as the operative one.
    # Only requests bound to a correspondence shown here count: a request (and its preparation) from an earlier
    # correspondence never makes a newer, unprepared one look prepared.
    shown = {v["review"].review_id for v in verified}
    requests = [r for r in view.requests if r["review_id"] in shown]
    newest = verified[-1]
    if not newest["blocked"] and not any(r["review_id"] == newest["review"].review_id for r in requests):
        return set_("PREPARE", "Request preparation")
    if not requests:
        if all(v["blocked"] for v in verified):
            return set_("PREPARE", "Preparation blocked by human decision")
        return set_("PREPARE", "Request preparation")
    request = max(requests, key=lambda r: r["seq"])  # the most recent explicit human request (current for its review)
    if request["review_id"] in view.blocks and view.blocks[request["review_id"]][-1]["action"] == "BLOCK":
        return set_("PREPARE", "Preparation blocked by human decision")
    preps = [p for p in view.preparations if p["request_id"] == request["request_id"]]
    if not preps:
        return set_("PREPARE", "Generate preparation")
    status = preparation_status(preps[-1], preps[-1]["reviews"])
    if status == "Needs review":
        return set_("PREPARE", "Review prepared drafts")
    if status == "Accepted":
        return set_("PREPARED", "Prepared for human use. The salesperson decides.")
    return set_("PREPARE", "Drafts rejected; request preparation again if useful")


@_guarded
class Console(_PrincipalBound):
    """The two workspaces, presented as one product, for one verified principal.

    The principal must match an active user in this root's identity store, or construction fails, and it is
    re-validated before every operation (_guarded). Every read and write below goes through services bound to that
    principal, so only its organization's accounts are visible. A Console is never shared between principals.
    """

    def __init__(self, root: str | Path, clock: Clock | None = None, *, principal: Principal,
                 authentication_required: bool = False, backend=None, credential=None) -> None:
        self.root = Path(root)
        self._backend = backend or SQLiteBackend(self.root)
        if isinstance(self._backend, SQLiteBackend):  # PostgreSQL storage creates no directory
            self.root.mkdir(parents=True, exist_ok=True)
        self.authentication_required = authentication_required
        self._credential = credential
        if credential is not None:
            credential.check(principal)
        with self._backend.identity(principal.organization_id) as store:
            principal = store.verify(principal, require_authentication_eligible=authentication_required)
        self.principal = principal
        self.local = ConsoleService(self.root, LOCAL, clock, principal=principal,
                                    authentication_required=authentication_required, backend=self._backend,
                                    credential=credential)
        self.synthetic = ConsoleService(self.root, SYNTHETIC, clock, principal=principal,
                                        authentication_required=authentication_required, backend=self._backend,
                                        credential=credential)
        self.local._shared_adopt = self.synthetic._shared_adopt = self._adopt

    def _adopt(self, principal: Principal) -> None:
        self.principal = self.local.principal = self.synthetic.principal = principal

    @classmethod
    def local_development(cls, root: str | Path, clock: Clock | None = None) -> Console:
        """The console for the deterministic local-development principal (not authentication). Refused outside
        development mode (deployment.py): production never falls back to it.

        On first use the local user takes the V0.3 "Your name" preference as its display name, so history recorded
        before V0.4 and after it carries the same actor label.
        """
        deployment.require_development("The local-development principal")
        root = Path(root)
        clock = clock or SystemClock()
        principal = identity.local_development_principal(
            root / IDENTITY_FILE, now=clock.now(), legacy_display_name=legacy_actor_name(root / LOCAL / "console.sqlite3"))
        return cls(root, clock, principal=principal)

    @property
    def identity_path(self) -> Path:
        return self.root / IDENTITY_FILE

    def check_access(self) -> None:
        """Nothing but the guard: the principal (and, in production, its sign-in) is still valid. The app calls it
        before drawing anything, so a session that lost access sees only the refusal."""

    def seed_service(self) -> ConsoleService:
        """The synthetic workspace acting as this organization's synthetic-seed user, so seeded actions are never
        attributed to the person who loaded the demo. Seeded accounts belong to this principal's organization.
        Refused for a signed-in (authentication-required) console: production never acts as a system identity."""
        if self.authentication_required:
            raise ConsoleError(SEEDING_UNAVAILABLE)
        seed = self._backend.seed_principal(self.principal.organization_id, now=self.synthetic.now())
        return ConsoleService(self.root, SYNTHETIC, self.synthetic.clock, principal=seed, backend=self._backend)

    @property
    def services(self) -> tuple[ConsoleService, ConsoleService]:
        return (self.local, self.synthetic)

    def owner(self, account_id: str) -> ConsoleService:
        for service in self.services:
            if service.has_account(account_id):
                return service
        raise AccessDenied()

    def account_views(self) -> list[AccountView]:
        return [s.account_view(a) for s in self.services for a in s.account_ids()]

    def owner_of_preparation(self, preparation_id: str) -> ConsoleService:
        for service in self.services:
            if preparation_id in service.preparation_ids():
                return service
        raise AccessDenied()

    def reset_synthetic(self) -> None:
        """Delete only the synthetic workspace files (SQLite only; PostgreSQL storage refuses). The local workspace is
        never opened for writing here."""
        self.synthetic.destroy()

    @property
    def actor(self) -> str:
        """The current principal's display name: the label recorded on every human action."""
        return self.principal.display_name

    def set_actor(self, name: str) -> None:
        """Rename the current principal's own user. The user_id is unchanged; earlier records keep the display-name
        snapshot they were recorded under, and new actions record the new name. Refused for a signed-in
        (authentication-required) console: there, display names are provisioned (postgres/provision.py)."""
        if self.authentication_required:
            raise ConsoleError(RENAME_UNAVAILABLE)
        if not name or not name.strip():
            raise ConsoleError("Enter a name.")
        with self._backend.identity(self.principal.organization_id) as store:
            try:
                store.rename_user(self.principal.user_id, name, at=self.local.now())
            except identity.IdentityError as error:
                raise ConsoleError(str(error)) from error
            principal = store.principal(self.principal.user_id)
        self._adopt(principal)

    def review_queue(self) -> dict:
        merged = {"pending": [], "completed": []}
        for service in self.services:
            queue = service.review_queue()
            merged["pending"] += queue["pending"]
            merged["completed"] += queue["completed"]
        for key in merged:
            merged[key].sort(key=lambda r: r["created"], reverse=True)
        return merged

    def activity(self) -> list[TimelineItem]:
        items = [i for s in self.services for a in s.account_ids() for i in s.timeline(a)]
        return sorted(items, key=lambda i: i.at, reverse=True)

    def preparations(self) -> list[dict]:
        return [s.preparation_view(p) for s in self.services for p in s.preparation_ids()]

    def dashboard(self, views: list[AccountView] | None = None) -> dict:
        """Counts read from stored records."""
        views = views if views is not None else self.account_views()
        verified = monitoring = awaiting = ready = 0
        for v in views:
            verified += sum(1 for b in v.baecs if b.classification is BaecClassification.CONFIRMED_BAEC)
            monitoring += int(v.plan is not None and v.plan.activation_result == "ACTIVATED")
            awaiting += sum(1 for c in v.candidates if not c["reviews"])
            ready += int(v.next_action == "Request preparation")
        return {"accounts": len(views), "verified": verified, "monitoring": monitoring, "awaiting": awaiting,
                "ready": ready}
