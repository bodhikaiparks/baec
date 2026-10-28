"""The provider-neutral request specification, response, failures, and provider protocol.

AiRequestSpec is the immutable, application-owned description of one inference
request. Its nested JSON values (messages and output_config) are held only as
validated canonical JSON text, so nothing can mutate what was digested; every
accessor returns freshly decoded values. The same spec object is the sole source
of the provider call and of request_digest. API keys, headers, timeouts, retry
settings, and transport details are never part of it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Protocol

from baec_app.ai.canonical import CanonicalizationError, canonical_digest, canonical_json

# The fields the provider sends, taken from the spec and nothing else.
SENT_FIELDS = ("model", "max_tokens", "system", "messages", "output_config")

API_ERROR_CATEGORIES = (
    "authentication", "permission", "rate_limited", "overloaded", "invalid_request", "server_error", "other",
)
# Each transport category has exactly one delivery state.
TRANSPORT_FAILURES = {"connection_not_established": "not_sent", "timeout_or_disconnect": "unknown"}


def _text(value: object, field: str) -> str:
    if type(value) is not str or not value.strip():
        raise ValueError(f"{field} must be a non-blank string")
    return value


def _canonical_text(value: object, field: str, kind: type) -> str:
    """Accept canonical JSON text of the given top-level kind, exactly as canonicalized."""
    if type(value) is not str:
        raise ValueError(f"{field} must be canonical JSON text")
    try:
        decoded = json.loads(value)
        if type(decoded) is not kind or canonical_json(decoded) != value:
            raise ValueError
    except (ValueError, CanonicalizationError):
        raise ValueError(f"{field} must be canonical JSON of a {kind.__name__}") from None
    return value


@dataclass(frozen=True)
class AiRequestSpec:
    """One immutable inference request. messages and output_config are stored as canonical JSON text."""

    request_spec_version: str
    provider: str
    api_method: str
    model: str
    max_tokens: int
    system: str
    messages_json: str
    output_config_json: str
    prompt_version: str
    input_version: str
    output_schema_version: str

    @classmethod
    def build(
        cls, *, request_spec_version: str, provider: str, api_method: str, model: str, max_tokens: int,
        system: str, messages: list, output_config: dict, prompt_version: str, input_version: str,
        output_schema_version: str,
    ) -> AiRequestSpec:
        return cls(
            request_spec_version=request_spec_version, provider=provider, api_method=api_method, model=model,
            max_tokens=max_tokens, system=system, messages_json=canonical_json(messages),
            output_config_json=canonical_json(output_config), prompt_version=prompt_version,
            input_version=input_version, output_schema_version=output_schema_version,
        )

    def __post_init__(self) -> None:
        for field in ("request_spec_version", "provider", "api_method", "model", "system", "prompt_version",
                      "input_version", "output_schema_version"):
            _text(getattr(self, field), f"AiRequestSpec.{field}")
        if type(self.max_tokens) is not int or self.max_tokens < 1:
            raise ValueError("AiRequestSpec.max_tokens must be a positive int")
        _canonical_text(self.messages_json, "AiRequestSpec.messages", list)
        _canonical_text(self.output_config_json, "AiRequestSpec.output_config", dict)

    @property
    def messages(self) -> list:
        """A fresh copy; mutating it cannot change the spec."""
        return json.loads(self.messages_json)

    @property
    def output_config(self) -> dict:
        """A fresh copy; mutating it cannot change the spec."""
        return json.loads(self.output_config_json)

    def to_json_object(self) -> dict:
        """The spec as its semantic JSON object (fresh values)."""
        return {
            "request_spec_version": self.request_spec_version,
            "provider": self.provider,
            "api_method": self.api_method,
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": self.system,
            "messages": self.messages,
            "output_config": self.output_config,
            "prompt_version": self.prompt_version,
            "input_version": self.input_version,
            "output_schema_version": self.output_schema_version,
        }

    def canonical_json(self) -> str:
        return canonical_json(self.to_json_object())

    def digest(self) -> str:
        """request_digest: SHA-256 of the canonical JSON of the complete spec."""
        return canonical_digest(self.to_json_object())

    def sdk_arguments(self) -> dict:
        """Fresh keyword arguments for the provider call: exactly SENT_FIELDS, from this spec only."""
        semantic = self.to_json_object()
        return {field: semantic[field] for field in SENT_FIELDS}


@dataclass(frozen=True)
class ProviderResponse:
    """The provenance the service needs from one provider response. No SDK object, no thinking blocks."""

    provider_message_id: str | None
    response_model: str | None
    stop_reason: str | None
    provider_request_id: str | None
    input_tokens: int | None
    output_tokens: int | None
    cache_creation_input_tokens: int | None
    cache_read_input_tokens: int | None
    text_blocks: tuple[str, ...]

    def __post_init__(self) -> None:
        for field in ("provider_message_id", "response_model", "stop_reason", "provider_request_id"):
            value = getattr(self, field)
            if value is not None and type(value) is not str:
                raise ValueError(f"ProviderResponse.{field} must be text or None")
        for field in ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"):
            value = getattr(self, field)
            if value is not None and (type(value) is not int or value < 0):
                raise ValueError(f"ProviderResponse.{field} must be a non-negative int or None")
        if type(self.text_blocks) is not tuple or any(type(block) is not str for block in self.text_blocks):
            raise ValueError("ProviderResponse.text_blocks must be a tuple of strings")


class ProviderError(Exception):
    """An expected provider failure. Carries only safe machine provenance, never bodies or source text."""


class ProviderApiError(ProviderError):
    """The provider returned an error response (remote_outcome is response_received)."""

    def __init__(self, category: str, provider_request_id: str | None = None) -> None:
        if category not in API_ERROR_CATEGORIES:
            raise ValueError("unknown provider API error category")
        if provider_request_id is not None and type(provider_request_id) is not str:
            raise ValueError("provider_request_id must be text or None")
        super().__init__(f"provider API error: {category}")
        self.category = category
        self.provider_request_id = provider_request_id


class ProviderTransportError(ProviderError):
    """No response was received: the request was not sent, or its outcome is unknown."""

    def __init__(self, category: str, remote_outcome: str) -> None:
        if TRANSPORT_FAILURES.get(category) != remote_outcome:
            raise ValueError("transport category and remote outcome do not match")
        super().__init__(f"provider transport failure: {category}")
        self.category = category
        self.remote_outcome = remote_outcome


class ExtractionProvider(Protocol):
    """A provider of structured extraction. prepare_request is local; invoke makes exactly one remote attempt."""

    provider_name: str
    sdk_name: str
    sdk_version: str

    def prepare_request(
        self, *, model: str, max_tokens: int, system: str, user_content: str, prompt_version: str,
        input_version: str, output_schema_version: str,
    ) -> AiRequestSpec: ...

    def invoke(self, spec: AiRequestSpec) -> ProviderResponse: ...
