"""Phase 5 MCP Core: a read-only, preview-only MCP interface to the BAEC prototype.

The server receives only a Phase 4 ProposalFacade and uses only its reads
(and, from 5C, its previews). It exposes BAEC information and deterministic
application reasoning; it does not grant authority. An MCP call is not human
approval, MCP client identity is not authenticated human identity, and stored
buyer text is untrusted data, never instruction. It demonstrates an MCP
interface to the prototype; it does not validate BAEC theory. See
docs/PHASE5_MCP_CORE_DESIGN.md.
"""
