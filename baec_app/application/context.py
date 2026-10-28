"""Clock, identifiers, and the human-interaction context.

The actor is self-asserted in V0.1: there is no authentication, so an
actor_id is a label, not proof of identity.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

from baec_app.application.errors import ApplicationValidationError


def _require_text(value: object, field: str) -> None:
    if type(value) is not str or not value.strip():
        raise ApplicationValidationError(f"{field} must be a non-blank string")


def _require_aware(value: object, field: str) -> None:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise ApplicationValidationError(f"{field} must be a timezone-aware datetime")


class Clock(Protocol):
    def now(self) -> datetime:
        """Return the current time as a timezone-aware datetime."""


class IdFactory(Protocol):
    def new_baec_id(self) -> str:
        """Return a new, unused BAEC record identifier."""

    def new_token(self) -> str:
        """Return a new session, request, or approval identifier."""


class SystemClock:
    """The real clock, in UTC."""

    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class UuidIdFactory:
    """Random identifiers from uuid4."""

    def new_baec_id(self) -> str:
        return f"BAEC-{uuid.uuid4().hex}"

    def new_token(self) -> str:
        return uuid.uuid4().hex


@dataclass(frozen=True)
class Actor:
    """The person acting in a session. Self-asserted in V0.1."""

    actor_id: str

    def __post_init__(self) -> None:
        _require_text(self.actor_id, "Actor.actor_id")


@dataclass(frozen=True)
class InteractionSession:
    """One human-interaction session opened by the trusted adapter."""

    session_id: str
    actor: Actor
    opened_at: datetime

    def __post_init__(self) -> None:
        _require_text(self.session_id, "InteractionSession.session_id")
        if type(self.actor) is not Actor:
            raise ApplicationValidationError("InteractionSession.actor must be an Actor")
        _require_aware(self.opened_at, "InteractionSession.opened_at")
