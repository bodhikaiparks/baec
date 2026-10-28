"""Session-isolated, read-only runtime for the public BAEC Console."""

from __future__ import annotations

import shutil
import weakref
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from tempfile import mkdtemp

from . import demo_seed
from . import identity
from .console_service import IDENTITY_FILE, Console, ConsoleError


PUBLIC_ACTOR = "Public demo visitor"
PUBLIC_ORGANIZATION_ID = "public-demo"
PUBLIC_USER_ID = "public-demo-visitor"

# The public demo's capability boundary is an allowlist. A public session may call only these methods, each of which
# reads stored records and writes nothing. Every other method -- every mutator, and any method added to
# ConsoleService or Console later -- is refused. test_public_runtime enumerates every public method of both classes
# and checks that each one outside these sets is refused.
PUBLIC_SERVICE_READS = frozenset({
    "account_ids",
    "has_account",
    "account_view",
    "timeline",
    "attributions",
    "review_queue",
    "preparation_ids",
    "preparation_view",
    "preview_condition",
    "preview_state_change",
    "handoff",
    "review_packet",
    "preparation_snapshot",
    "preparation_eligibility",
    "crm_contacts",
    "crm_tasks",
    "crm_activities",
    "crm_interaction_link",
})
PUBLIC_CONSOLE_READS = frozenset({
    "account_views",
    "review_queue",
    "activity",
    "preparations",
    "dashboard",
})
# Plain values, not operations.
PUBLIC_SERVICE_VALUES = frozenset({"workspace", "actor", "principal"})
PUBLIC_CONSOLE_VALUES = frozenset({"actor", "principal", "root"})

LOCKED = ("This action is locked in the public demo. "
          "The full Console supports it, but public sessions are read-only.")


def _remove_directory(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)


def _blocked(*args, **kwargs):
    raise ConsoleError(LOCKED)


def _allowed(target, name: str, reads: frozenset, values: frozenset):
    if name in reads or name in values:
        return getattr(target, name)
    if callable(getattr(type(target), name, None)):
        return _blocked  # refused when called, so the page can still render the control in its locked state
    raise ConsoleError(LOCKED)


class ReadOnlyService:
    def __init__(self, service) -> None:
        self._service = service

    def __getattr__(self, name):
        return _allowed(self._service, name, PUBLIC_SERVICE_READS, PUBLIC_SERVICE_VALUES)


class ReadOnlyConsole:
    def __init__(self, console: Console) -> None:
        self._console = console
        self.local = ReadOnlyService(console.local)
        self.synthetic = ReadOnlyService(console.synthetic)

    @property
    def services(self):
        return (self.local, self.synthetic)

    def owner(self, account_id: str):
        if self.local.has_account(account_id):
            return self.local
        if self.synthetic.has_account(account_id):
            return self.synthetic
        return self._console.owner(account_id)  # raises AccessDenied

    def owner_of_preparation(self, preparation_id: str):
        if preparation_id in self.local.preparation_ids():
            return self.local
        if preparation_id in self.synthetic.preparation_ids():
            return self.synthetic
        return self._console.owner_of_preparation(preparation_id)  # raises AccessDenied

    def __getattr__(self, name):
        return _allowed(self._console, name, PUBLIC_CONSOLE_READS, PUBLIC_CONSOLE_VALUES)


@dataclass
class PublicRuntime:
    directory: Path
    console: ReadOnlyConsole
    account_id: str
    _finalizer: weakref.finalize = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._finalizer = weakref.finalize(
            self,
            _remove_directory,
            self.directory,
        )

    def cleanup(self) -> None:
        if self._finalizer.alive:
            self._finalizer()


def new_public_runtime() -> PublicRuntime:
    directory = Path(mkdtemp(prefix="baec-console-public-"))

    # Each public session has its own data root, identity store, and organization.
    principal = identity.provision(directory / IDENTITY_FILE, organization_id=PUBLIC_ORGANIZATION_ID,
                                   organization_name="Public demo session", user_id=PUBLIC_USER_ID,
                                   display_name=PUBLIC_ACTOR, kind=identity.UserKind.PUBLIC_DEMO_SESSION,
                                   now=datetime.now(timezone.utc))
    writable_console = Console(directory, principal=principal)

    account_id = demo_seed.load(
        writable_console,
        "completed",
    )

    return PublicRuntime(
        directory=directory,
        console=ReadOnlyConsole(writable_console),
        account_id=account_id,
    )
