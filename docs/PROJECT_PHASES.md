# Project Phases

**Project:** BAEC Trigger Intelligence
**Purpose:** Records how the repository's checkpoints are numbered, what each one contains, and how phase numbers should be read.

Phase numbers are implementation-planning labels for this software. They are not research stages and make no claim about the BAEC or CEE theory. The research authority is the manuscript, then `docs/RESEARCH_CONTRACT.md`, then code (see `CLAUDE.md`).

---

## 1. Checkpoints

| Phase | Name | Git tag | Commit | Status |
|---|---|---|---|---|
| Gate 1 / Phase 1 | Deterministic domain correctness | `gate-1-domain-correctness` | `b4deb78` | Complete |
| Phase 2 | — | none | none | Not materialized (see §3) |
| Phase 3 | Persistence and synthetic data | `phase-3-persistence` | `4063c23` | Complete |
| Phase 3 hardening | RC-33 schema guards | `phase-3-persistence-hardening` | `3e68030` | Complete |
| Phase 3 hardening | Evidence fidelity | `phase-3-evidence-fidelity-hardening` | `7a01c24` | Complete |
| Phase 4 | Application & Human Authorization Boundary | `phase-4-application-boundary` | the tagged commit | Complete |
| Phase 5 | MCP Core (read-only, deterministic) | `phase-5-mcp-core` | the tagged commit | Complete |

Existing tags and commits are not renamed, renumbered, moved, or rewritten.

## 2. Gate 1 / Phase 1: deterministic domain correctness

Tag `gate-1-domain-correctness`, commit `b4deb78`.

Contents:

- `baec_app/domain/`: enums, frozen domain models, BAEC classification rules (`classify_candidate` and the record factories), and the account-state machine.
- `docs/RESEARCH_CONTRACT.md`, approved by the project owner for Phase 1.
- `docs/PHASE1_SMOKE_MIGRATION.md`: every exploratory smoke check from Phase 1, mapped to its pytest equivalent.
- Tests: 813 passing at this tag.

Out of scope at this checkpoint: persistence, AI or model calls, MCP, UI, monitoring, and signals.

"Gate 1" and "Phase 1" name the same checkpoint. The tag uses the gate name because this checkpoint was the domain-correctness gate that later phases build on.

## 3. Phase 2: not materialized

No separate Phase 2 checkpoint exists in the repository. Work that might have been labelled Phase 2 was either part of Gate 1 / Phase 1 or delivered in Phase 3. There is no Phase 2 tag or commit, and none will be created after the fact.

The numbering goes straight from Phase 1 to Phase 3. That gap is a fact about how the repository was built. It does not mean any work is missing.

## 4. Phase 3: persistence and synthetic data

Tag `phase-3-persistence`, commit `4063c23`.

Contents:

- `baec_app/data/database.py`: SQLite connection, STRICT schema (version 2 at the tag), append-only triggers, immediate write transactions, read-only canonical copies, and working copies.
- `baec_app/data/repository.py`: stores and rehydrates the locked domain objects. On every save it re-checks the classification against the locked rules, and it re-runs the state machine on stored data before saving any transition. It fails closed on corrupt stored data.
- `baec_app/data/records.py`: persistence-only data transfer objects (`SourceInteraction`, `PersistedDormancyJudgment`, `TransitionHistoryEntry`).
- `baec_app/data/seed.py`, `scripts/seed_demo.py`, `data/demo/*.json`: a fictional three-account demo dataset built only through the domain layer.
- Tests: 1403 passing at this tag.

Out of scope at this checkpoint: application/service layer, proof that an authorization came from a real human action, AI or model calls, MCP, UI, monitoring, signals, correspondence matching, and outbound contact.

### Phase 3 hardening

Tag `phase-3-persistence-hardening`, after the `phase-3-persistence` tag. Not a new phase. It closes two RC-33 gaps found after the tag:

- `baec_records` source, provenance, and decision columns could be changed by raw SQL in ways that still passed the load-time checks.
- Rows in append-only tables could be rewritten with SQLite `REPLACE`, which deletes and re-inserts a row without firing DELETE triggers.

Schema version becomes 3. Version 2 databases are refused and must be rebuilt from the seed files. Tests: 1548 passing.

### Phase 3 evidence-fidelity hardening

Tag `phase-3-evidence-fidelity-hardening`, after `phase-3-persistence-hardening`. Not a new phase. It closes a gap found while designing Phase 4: stored evidence text was never checked against the interaction it cites, so text that appears nowhere in an interaction could be stored as evidence from it, including evidence used to move an account to Active Opportunity.

Stored buyer-fact and seller-observation evidence text must now occur verbatim in the text of the interaction it cites. This is an implementation-level evidence-fidelity constraint for this software, enforced on repository save, on repository load, and by a schema trigger. It is not a research claim.

Schema version becomes 4. Version 3 databases are refused and must be rebuilt from the seed files. Tests: 1658 passing.

How Phase 3 and its hardening map to the Research Contract is recorded in `docs/PHASE3_PERSISTENCE_TRACEABILITY.md`.

## 5. Phase 4: Application & Human Authorization Boundary

Tag `phase-4-application-boundary`. Design approved in `docs/PHASE4_APPLICATION_BOUNDARY_DESIGN.md` (commit `2c2016d`), then implemented in five reviewed increments:

| Increment | Commit | Contents |
|---|---|---|
| 4A | `ac86daa` | Context, errors, proposal origins, canonical request serialization, request types, architecture scanner |
| 4B | `a5a3be7` | Human confirmation gate: sessions, one approval per request, validate-then-consume redemption |
| 4C | `427cf05` | `authority.py`, classification and dormancy-judgment services; gate registration tightened to `register(session, request)` |
| 4D | `12e7ea7` | Account-state service: previews, request coherence, human-authorized transitions |
| 4E | `c70feb4` | Proposal objects, read path, facades, composition, final boundary rules, traceability |

Contents: `baec_app/application/` (no change to the domain, data, schema, or seed data). Tests: 2659 passing, of which 1001 are Phase 4 application tests; the 1658 earlier tests are unchanged.

Phase 4 provides application-level authority separation and accident resistance. It does not authenticate anyone, does not defend against malicious code running inside the trusted process, and does not connect any model or MCP input. How the design maps to code and tests is recorded in `docs/PHASE4_IMPLEMENTATION_TRACEABILITY.md`.

## 6. Phase 5: MCP Core

Final tag: `phase-5-mcp-core`. The annotated tag identifies the final Phase 5 checkpoint (the 5E commit). Status: Complete.

Design approved in `docs/PHASE5_MCP_CORE_DESIGN.md`, then implemented in reviewed increments:

| Increment | Commit | Contents | Tests |
|---|---|---|---|
| 5A | `6c6eb7bf578487dcb2047080d2501d7fe6bd2797` | Docs: approve Phase 5 MCP Core design | baseline: 2659 passed |
| 5B | `c15f928136fe1219d7f395e7636035a97fc5103a` | Phase 5B: MCP read-only resource core | checkpoint: 2956 passed, 1 expected xfail |
| 5C | `fcc84bda4f86392e4bc78f70455d0a71e9fae13f` | Phase 5C: deterministic MCP preview tools | checkpoint: 3013 passed, 0 xfailed |
| 5D | `97460133cb37e399a6ece197064d78d273766556` | Phase 5D: stdio runtime and adversarial hardening | checkpoint: 3248 passed, 0 xfailed |
| 5E | this tagged commit (referenced by `phase-5-mcp-core`) | Docs: finalize Phase 5 MCP Core. Final documentation and checkpoint increment: traceability, usage documentation, and this record | 3248 passed, 0 xfailed |

Contents: `baec_app/mcp/` exposes a read-only, deterministic MCP server over stdio. It has 8 resources, 4 preview tools, and 0 prompts, and no proposal, request, approval, or write tools. It is pinned to `mcp[cli]==2.2.0`. The only change outside `baec_app/mcp/` is an additive re-export of six existing types from `baec_app/application/__init__.py`. The domain, data layer, schema, and seed data are unchanged.

MCP Core exposes BAEC information and deterministic application reasoning; it does not grant authority. An MCP tool call is not human approval. Phase 5 does not authenticate anyone, connects no model, and makes no claim about BAEC theory. How the design maps to code and tests is recorded in `docs/PHASE5_MCP_CORE_TRACEABILITY.md`. Operator usage is described in `docs/MCP_USAGE.md`.

## 7. Future phases

Later phase numbers are planning labels only. A phase is defined by its approved design document, not by its number. A future phase may be renumbered, split, or dropped before it is approved. Nothing is built ahead of the approved phase (`CLAUDE.md`).

Before any model or MCP proposal input is enabled, persistent AI-origin provenance must be designed and implemented (Phase 4 design §18; Phase 5 design §2).

## 8. What a checkpoint does and does not show

A checkpoint shows that the software at that commit passed its own tests on synthetic data. It does not show that BAEC or CEE is valid, that BAECs predict evaluation or purchase, that the software improves sales performance, or that the software is secure for production use (Research Contract §13).
