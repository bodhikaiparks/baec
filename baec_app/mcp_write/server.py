"""The one-tool write server: confirm_baec(grant_id) -> ConfirmationExecutionService.execute(grant_id).

The tool is an adapter, not an application layer. It makes no decision of its own: it passes the grant id to the
executor once and translates the outcome. All authority and validation stays in the executor.

Argument validation (MCP SDK 2.2.0), as in Phase 5: the tool is built with Tool.from_function and given a closed
argument model (extra="forbid", strict=True, hide_input_in_errors=True). An unknown field, such as an actor, a
baec_id, or reviewed content, is rejected before the handler runs, and the refused value is not echoed back.

Results.
- Confirmed: structured ConfirmationView {status: "confirmed", baec_id, proposal_id, review_revision_id, grant_id}.
- Refused: an MCP tool error (isError) whose text, after the SDK's "Error executing tool confirm_baec: " prefix,
  is the canonical JSON {"code": <ExecutionRefused code>, "grant_id": ..., "status": "refused"}. The code is the
  executor's own, unchanged. Nothing was written and the grant was not used.
- Anything else: the SDK's generic tool error; details are logged on the server's stderr only. Nothing is retried.
"""

from __future__ import annotations

import json
from typing import Literal

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.mcpserver.tools import Tool
from mcp.server.mcpserver.utilities.func_metadata import ArgModelBase
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field

from baec_app.application.proposal_authorization import ConfirmationExecutionService, ExecutionRefused

SERVER_NAME = "baec-trigger-intelligence-confirmation"
TOOL_NAME = "confirm_baec"
TOOL_DESCRIPTION = (
    "Execute one previously issued human authorization grant for BAEC confirmation. The grant must already exist "
    "and be valid: issued by a human through the review surface, unexpired, unused, and not superseded. This tool "
    "cannot issue, extend, or alter a grant and supplies no BAEC content; it records the confirmed BAEC the human "
    "authorized, or refuses with the executor's code. It changes no account state."
)

# Metadata only, not controls. Not read-only: a valid grant creates a confirmed BAEC record. Not destructive: it
# only adds records. Closed world: it touches only the local database. idempotentHint is left unset: a repeat call
# writes nothing more, but it returns a refusal rather than the same result.
TOOL_ANNOTATIONS = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False)


class ConfirmBaecArguments(ArgModelBase):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, hide_input_in_errors=True)

    grant_id: str = Field(pattern=r"^grant_[0-9a-f]{64}$")


class ConfirmationView(BaseModel):
    """Confirmation metadata only: no interaction text, evidence, review content, or authorization internals."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["confirmed"]
    baec_id: str
    proposal_id: str
    review_revision_id: str
    grant_id: str


def _refusal_text(code: str, grant_id: str) -> str:
    return json.dumps({"code": code, "grant_id": grant_id, "status": "refused"}, sort_keys=True,
                      separators=(",", ":"))


def _confirm_baec_tool(executor: ConfirmationExecutionService) -> Tool:
    async def confirm_baec(grant_id: str) -> ConfirmationView:
        try:
            result = executor.execute(grant_id)
        except ExecutionRefused as refusal:
            raise ToolError(_refusal_text(refusal.code, grant_id)) from None
        return ConfirmationView(status="confirmed", baec_id=result.baec_id, proposal_id=result.proposal_id,
                                review_revision_id=result.review_revision_id, grant_id=result.grant_id)

    tool = Tool.from_function(confirm_baec, name=TOOL_NAME, description=TOOL_DESCRIPTION,
                              annotations=TOOL_ANNOTATIONS, structured_output=True)
    return tool.model_copy(update={
        "fn_metadata": tool.fn_metadata.model_copy(update={"arg_model": ConfirmBaecArguments}),
        "parameters": ConfirmBaecArguments.model_json_schema(by_alias=True),
    })


def build_write_server(executor: ConfirmationExecutionService) -> MCPServer:
    """Build the write server over exactly one ConfirmationExecutionService: one tool, no resources, no prompts."""
    if type(executor) is not ConfirmationExecutionService:
        raise TypeError("build_write_server requires a ConfirmationExecutionService")
    return MCPServer(
        name=SERVER_NAME,
        title="BAEC Trigger Intelligence (grant-execution MCP server)",
        version="phase-7g",
        tools=[_confirm_baec_tool(executor)],
    )
