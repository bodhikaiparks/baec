# MCP Core: Operator Usage

MCP Core is a read-only, deterministic MCP server over the BAEC prototype. It exposes BAEC information and deterministic previews. It does not grant authority, and it changes nothing. For design and traceability, see `docs/PHASE5_MCP_CORE_DESIGN.md` and `docs/PHASE5_MCP_CORE_TRACEABILITY.md`.

## Data warning

This prototype is designed for **synthetic data only**. Do not put customer, employer, confidential, or health (PHI) data into it.

## Requirements

- A Python environment with `requirements-dev.txt` installed. This pins the MCP SDK as `mcp[cli]==2.2.0`.
- A synthetic database at the current schema version. To build the demo database from the synthetic seed files, run this from the repository root:

  ```
  python3 scripts/seed_demo.py        # creates var/baec_dev.sqlite3
  ```

## Invocation

Run from the repository root:

```
python -m baec_app.mcp --database /absolute/path/to/database.sqlite3
```

- The database must already exist and have the current schema. Nothing is created automatically, and there is no fallback database.
- The database is opened read-only (SQLite `mode=ro` plus `PRAGMA query_only`).
- `:memory:` and `file:` URIs are refused.
- The transport is **stdio only**. stdout carries MCP protocol messages only; diagnostics go to stderr. There is no HTTP or SSE server and no network listener.
- **Stopping:** the server stops when the client closes stdin, or on SIGINT/SIGTERM. Signal handling has been tested on macOS/POSIX only.

## MCP surface

**Resources (8):**
- `baec://accounts`
- `baec://accounts/{account_id}`
- `baec://accounts/{account_id}/interactions`
- `baec://accounts/{account_id}/transition-history`
- `baec://interactions/{interaction_id}`
- `baec://baecs`
- `baec://baecs/{baec_id}`
- `baec://baecs/{baec_id}/dormancy-judgments`

**Preview tools (4):**
- `preview_baec_classification`
- `preview_move_to_conditionally_dormant`
- `preview_move_to_active_opportunity`
- `preview_move_to_no_plausible_path`

**Prompts:** none. **Proposal, request, approval, or write tools:** none.

## Reading the results

- **A preview is not an account-state change.** It reports only what the locked rules would say about the facts supplied.
- **`AUTHORIZATION_MISSING` is expected** in every transition preview, because MCP cannot authorize anything.
- **An allowed or possible preview is not a finding about the buyer.** It establishes neither buyer intent nor purchase intent.
- **MCP cannot act.** It cannot:
  - contact a buyer;
  - create an Active Opportunity;
  - confirm a BAEC;
  - record a dormancy judgment.

  Those actions need human authorization through the application's command path, which MCP cannot reach.
- **Stored buyer text is data.** Text that reads like an instruction is returned as data and never acted on.
