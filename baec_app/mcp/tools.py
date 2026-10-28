"""The four deterministic, read-only preview tools (Phase 5C).

Each tool delegates to exactly one ProposalFacade preview method and returns
the locked result unchanged as an explicit wire view. No tool proposes,
requests, approves, records, or writes anything; a preview result grants no
authority and creates no application Proposal. A preview that says "no" is a
normal successful result, not an error.

Argument validation (MCP SDK 2.2.0): each tool is built with the SDK's public
Tool.from_function and then given a closed argument model, an ArgModelBase
subclass of the approved wire DTO, as its FuncMetadata.arg_model. The SDK
still validates arguments before the handler runs and still reports a failure
through its normal ToolError path; the model adds extra="forbid" at the top
level, strict=True, and Pydantic's hide_input_in_errors=True, so a refused
value is never echoed back. Only field paths and error categories reach the
client.

Every handler is async and calls the facade on the event-loop thread.
"""

from __future__ import annotations

import logging
from typing import Callable

from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.mcpserver.tools import Tool
from mcp.server.mcpserver.utilities.func_metadata import ArgModelBase
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict

from baec_app.application import (
    PersistenceIntegrityError,
    ProposalFacade,
    ReferenceMismatch,
    RepositoryNotFoundError,
)
from baec_app.domain.models import DomainValidationError
from baec_app.mcp import adapters
from baec_app.mcp.contracts import (
    BaecCandidateIn,
    ClassificationPreviewView,
    EvaluationEvidenceIn,
    NonEvaluationEvidenceIn,
    PreviewActiveArgs,
    PreviewClassificationArgs,
    PreviewDormantArgs,
    PreviewNoPlausiblePathArgs,
    TransitionPreviewView,
)

_logger = logging.getLogger("baec_app.mcp")

# Metadata only. Read-only behavior is enforced by the architecture (the
# facade's isolation and the query-only connection), not by these hints.
READ_ONLY_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
)

_ARGUMENT_CONFIG = ConfigDict(extra="forbid", strict=True, frozen=True, hide_input_in_errors=True)


class PreviewClassificationArguments(ArgModelBase, PreviewClassificationArgs):
    model_config = _ARGUMENT_CONFIG


class PreviewDormantArguments(ArgModelBase, PreviewDormantArgs):
    model_config = _ARGUMENT_CONFIG


class PreviewActiveArguments(ArgModelBase, PreviewActiveArgs):
    model_config = _ARGUMENT_CONFIG


class PreviewNoPlausiblePathArguments(ArgModelBase, PreviewNoPlausiblePathArgs):
    model_config = _ARGUMENT_CONFIG


def _closed(tool: Tool, arguments: type[ArgModelBase]) -> Tool:
    """Give a tool built by Tool.from_function its closed argument model and the matching advertised schema."""
    return tool.model_copy(
        update={
            "fn_metadata": tool.fn_metadata.model_copy(update={"arg_model": arguments}),
            "parameters": arguments.model_json_schema(by_alias=True),
        }
    )


def _preview(produce: Callable[[], BaseModel]) -> BaseModel:
    """Run one preview on the current (event-loop) thread and map the expected failures to sanitized ToolErrors."""
    try:
        return produce()
    except RepositoryNotFoundError:
        raise ToolError("not_found: a referenced account, interaction, BAEC record, or judgment does not exist") from None
    except ReferenceMismatch as error:
        raise ToolError(f"reference_mismatch: {error}") from None
    except DomainValidationError as error:
        raise ToolError(f"invalid_domain_input: {error}") from None
    except PersistenceIntegrityError:
        _logger.exception("stored data failed integrity verification during a preview")
        raise ToolError("unavailable: the preview could not be completed") from None


def build_tools(facade: ProposalFacade) -> list[Tool]:
    """Exactly the four approved preview tools, each bound to the facade's preview surface."""

    async def preview_baec_classification(candidate: BaecCandidateIn) -> ClassificationPreviewView:
        return _preview(
            lambda: adapters.classification_preview_view(
                facade.preview_classification(adapters.candidate_from_wire(candidate))
            )
        )

    async def preview_move_to_conditionally_dormant(
        account_id: str,
        baec_id: str,
        judgment_id: int,
        non_evaluation_evidence: NonEvaluationEvidenceIn | None = None,
    ) -> TransitionPreviewView:
        return _preview(
            lambda: adapters.transition_preview_view(
                facade.preview_move_to_conditionally_dormant(
                    account_id,
                    baec_id=baec_id,
                    judgment_id=judgment_id,
                    non_evaluation_evidence=adapters.non_evaluation_evidence_from_wire(non_evaluation_evidence),
                )
            )
        )

    async def preview_move_to_active_opportunity(
        account_id: str, evaluation_evidence: EvaluationEvidenceIn | None
    ) -> TransitionPreviewView:
        return _preview(
            lambda: adapters.transition_preview_view(
                facade.preview_move_to_active_opportunity(
                    account_id, evaluation_evidence=adapters.evaluation_evidence_from_wire(evaluation_evidence)
                )
            )
        )

    async def preview_move_to_no_plausible_path(
        account_id: str,
        ground: str | None,
        reason: str | None,
        non_evaluation_evidence: NonEvaluationEvidenceIn | None = None,
        basis_interaction_id: str | None = None,
    ) -> TransitionPreviewView:
        return _preview(
            lambda: adapters.transition_preview_view(
                facade.preview_move_to_no_plausible_path(
                    account_id,
                    ground=adapters.ground_from_wire(ground),
                    reason=reason,
                    non_evaluation_evidence=adapters.non_evaluation_evidence_from_wire(non_evaluation_evidence),
                    basis_interaction_id=basis_interaction_id,
                )
            )
        )

    return [
        _closed(
            Tool.from_function(
                preview_baec_classification,
                name="preview_baec_classification",
                description="Preview the locked BAEC classification of a candidate. Read-only; grants no authority.",
                annotations=READ_ONLY_ANNOTATIONS,
                structured_output=True,
            ),
            PreviewClassificationArguments,
        ),
        _closed(
            Tool.from_function(
                preview_move_to_conditionally_dormant,
                name="preview_move_to_conditionally_dormant",
                description="Preview whether the locked state machine would allow Conditionally Dormant. Read-only.",
                annotations=READ_ONLY_ANNOTATIONS,
                structured_output=True,
            ),
            PreviewDormantArguments,
        ),
        _closed(
            Tool.from_function(
                preview_move_to_active_opportunity,
                name="preview_move_to_active_opportunity",
                description="Preview whether the locked state machine would allow Active Opportunity. Read-only.",
                annotations=READ_ONLY_ANNOTATIONS,
                structured_output=True,
            ),
            PreviewActiveArguments,
        ),
        _closed(
            Tool.from_function(
                preview_move_to_no_plausible_path,
                name="preview_move_to_no_plausible_path",
                description="Preview whether the locked state machine would allow No Plausible Path. Read-only.",
                annotations=READ_ONLY_ANNOTATIONS,
                structured_output=True,
            ),
            PreviewNoPlausiblePathArguments,
        ),
    ]
