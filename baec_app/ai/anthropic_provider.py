"""The Anthropic implementation of ExtractionProvider: the only production module that imports anthropic.

Implementation contract: docs/PHASE6C_ANTHROPIC_SDK_CHARACTERIZATION.md.
- The client is built with max_retries=0, so one invoke is one HTTP attempt, and
  an explicit timeout of ANTHROPIC_TIMEOUT_SECONDS; an injected client must have
  the same settings. The timeout is transport configuration: it is never part of
  AiRequestSpec or any digest. Authentication comes only from the SDK and the
  environment.
- Requests use messages.create with an explicit Structured Outputs output_config
  (never messages.parse), with arguments taken only from the AiRequestSpec: no
  tools, thinking, sampling parameters, or metadata.
- Responses keep text blocks in order and drop every other block, including
  thinking. Failures map to the locked provider-neutral categories, carrying no
  response body, exception text, or source text.
httpx2 is imported only to recognise httpx2.ConnectError as a cause.
"""

from __future__ import annotations

import anthropic
import httpx2

from baec_app.ai.canonical import canonical_json
from baec_app.ai.contracts import API_METHOD, OUTPUT_SCHEMA_VERSION, PROVIDER, REQUEST_SPEC_VERSION, BaecExtractionOutput
from baec_app.ai.provider import AiRequestSpec, ProviderApiError, ProviderResponse, ProviderTransportError

# Transport configuration (6C-C1): an explicit per-request timeout, never part of the request spec or a digest.
ANTHROPIC_TIMEOUT_SECONDS = 180.0

# The structured-output model behind each output schema version.
_OUTPUT_MODELS = {OUTPUT_SCHEMA_VERSION: BaecExtractionOutput}

_STATUS_CATEGORIES = {401: "authentication", 403: "permission", 429: "rate_limited", 529: "overloaded",
                      400: "invalid_request", 404: "invalid_request", 413: "invalid_request", 422: "invalid_request"}


class AnthropicSchemaDriftError(RuntimeError):
    """The output model's transformed schema no longer matches the request. Raised before any attempt."""


def _output_config(output_schema_version: str) -> dict:
    model = _OUTPUT_MODELS.get(output_schema_version)
    if model is None:
        raise ValueError("unsupported output schema version")
    return {"format": {"type": "json_schema", "schema": anthropic.transform_schema(model)}}


def _api_category(status_code: int) -> str:
    if status_code in _STATUS_CATEGORIES:
        return _STATUS_CATEGORIES[status_code]
    return "server_error" if status_code >= 500 else "other"


class AnthropicExtractionProvider:
    provider_name = PROVIDER
    sdk_name = "anthropic"
    sdk_version = anthropic.__version__

    def __init__(self, client: anthropic.Anthropic | None = None) -> None:
        if client is None:
            client = anthropic.Anthropic(max_retries=0, timeout=ANTHROPIC_TIMEOUT_SECONDS)
        if type(client) is not anthropic.Anthropic or client.max_retries != 0 \
                or type(client.timeout) is not float or client.timeout != ANTHROPIC_TIMEOUT_SECONDS:
            raise ValueError("the Anthropic client must be anthropic.Anthropic with max_retries=0 and the locked timeout")
        self._client = client

    def prepare_request(
        self, *, model: str, max_tokens: int, system: str, user_content: str, prompt_version: str,
        input_version: str, output_schema_version: str,
    ) -> AiRequestSpec:
        """Local only: the complete, immutable request."""
        if output_schema_version != OUTPUT_SCHEMA_VERSION:
            raise ValueError("unsupported output schema version")
        return AiRequestSpec.build(
            request_spec_version=REQUEST_SPEC_VERSION, provider=PROVIDER, api_method=API_METHOD, model=model,
            max_tokens=max_tokens, system=system, messages=[{"role": "user", "content": user_content}],
            output_config=_output_config(output_schema_version), prompt_version=prompt_version,
            input_version=input_version, output_schema_version=output_schema_version,
        )

    def invoke(self, spec: AiRequestSpec) -> ProviderResponse:
        """Exactly one attempt, with arguments taken only from spec."""
        if type(spec) is not AiRequestSpec or spec.provider != PROVIDER or spec.api_method != API_METHOD:
            raise ValueError("invoke requires an Anthropic messages.create AiRequestSpec")
        if canonical_json(_output_config(spec.output_schema_version)) != spec.output_config_json:
            raise AnthropicSchemaDriftError("the output schema changed since the request was prepared")
        arguments = spec.sdk_arguments()
        try:
            message = self._client.messages.create(
                model=arguments["model"],
                max_tokens=arguments["max_tokens"],
                system=arguments["system"],
                messages=arguments["messages"],
                output_config=arguments["output_config"],
            )
        except anthropic.APIStatusError as error:
            raise ProviderApiError(_api_category(error.status_code), error.request_id) from None
        except anthropic.APITimeoutError:
            raise ProviderTransportError("timeout_or_disconnect", "unknown") from None
        except anthropic.APIConnectionError as error:
            if type(error.__cause__) is httpx2.ConnectError:
                raise ProviderTransportError("connection_not_established", "not_sent") from None
            raise ProviderTransportError("timeout_or_disconnect", "unknown") from None
        usage = message.usage
        return ProviderResponse(
            provider_message_id=message.id,
            response_model=message.model,
            stop_reason=message.stop_reason,
            provider_request_id=message._request_id,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_creation_input_tokens=usage.cache_creation_input_tokens,
            cache_read_input_tokens=usage.cache_read_input_tokens,
            text_blocks=tuple(block.text for block in message.content if block.type == "text"),
        )
