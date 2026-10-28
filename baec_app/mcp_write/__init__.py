"""Phase 7G: a separate one-tool MCP server that executes previously issued human authorization grants.

It exposes exactly one tool, confirm_baec(grant_id), and no resources or prompts. The tool hands the grant id to
the locked ConfirmationExecutionService, which re-checks everything and confirms the BAEC atomically, or refuses.
It cannot issue, extend, or alter a grant, and supplies no BAEC content: the human authorized the content when the
grant was issued, through the review surface, not through MCP. An MCP call is not human approval, and MCP client
identity is not authenticated human identity.

This package is separate from the read-only Phase 5 MCP Core (baec_app.mcp), which is unchanged. It is not a
sandbox: Python code with access to the local database could bypass these conventions. See
docs/PHASE7_HUMAN_AUTHORIZED_AI_PROPOSAL_BRIDGE_DESIGN.md (Phase 7G implementation clarification).
"""
