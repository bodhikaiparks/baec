# Phase 4 Implementation Traceability

**Project:** BAEC Trigger Intelligence
**Design:** `docs/PHASE4_APPLICATION_BOUNDARY_DESIGN.md` (approved, commit `2c2016d`)
**Implementation:** increments 4A `ac86daa`, 4B `a5a3be7`, 4C `427cf05`, 4D `12e7ea7`, 4E `c70feb4`; checkpoint tag `phase-4-application-boundary`
**Status:** Records how the approved Phase 4 design is implemented and tested, and where the implementation refines it.

---

## 1. What Phase 4 does and does not show

Phase 4 is software architecture. It does **not** validate BAEC or CEE theory, the four criteria, the account states, or any proposition (Research Contract §13). Its tests are software tests on synthetic data.

Phase 4 provides **application-level authority separation and accident resistance**:

- authoritative writes can be reached only by redeeming a single-use approval that the gate issued for a stored, immutable request;
- `HumanAuthorization` is constructed in exactly one application module, from the redeemed request;
- the proposal/read side has no route to approvals or commands and runs on a query-only connection;
- static checks keep interface code away from the writable repository and from authority construction.

Phase 4 does **not**:

- prove authenticated identity: `authorized_by` is the session's self-asserted actor;
- defend against arbitrary malicious code already executing inside the trusted process, which can import private names or use the repository directly;
- connect Claude, MCP, any model, or any external proposal input. Model/MCP proposal ingestion remains disabled until persistent AI-origin provenance is designed and implemented (design §18).

The read-path guard is a SQLite-enforced connection-level write guard for the configured proposal/read path (read-only open mode plus `PRAGMA query_only`). It is not a complete security boundary.

Phase 4 changes no locked layer: `baec_app/domain`, `baec_app/data`, the schema, and the seed data are unchanged, and the 1658 tests that predate Phase 4 are byte-identical.

## 2. Modules

| Module | Role | Increment |
|---|---|---|
| `errors.py` | Application error hierarchy (§8) | 4A–4E |
| `context.py` | `Clock`, `IdFactory`, `SystemClock`, `UuidIdFactory`, `Actor`, `InteractionSession` | 4A |
| `proposals.py` | `ProposalOrigin` (exactly `HUMAN_DRAFT`, `DETERMINISTIC`) and the five proposal objects | 4A, 4E |
| `canonical.py` | Canonical request serialization and SHA-256 digest | 4A |
| `requests.py` | `RequestKind`, five payloads, `ClassificationPreview`, `ApprovalRequest`, `build_request` | 4A |
| `approval.py` | `HumanConfirmationGate`, `HumanApproval`, private `_GATE_KEY` | 4B, 4C |
| `authority.py` | `authorize(request, approval)`: the only application construction of `HumanAuthorization` | 4C |
| `classification.py` | `preview_classification` (the one classification preview) and `ClassificationService` | 4C, 4E |
| `dormancy.py` | `DormancyJudgmentService` | 4C |
| `account_state.py` | `AccountStatePreviewService` (the one implementation of the three transition previews; repository only), `require_coherent_preview`, and `AccountStateService` (delegates previews) | 4D, 4E |
| `facades.py` | `ReadService`, `HumanCommandFacade`, `ProposalFacade` | 4E |
| `composition.py` | `open_read_connection`, `build_command_facade`, `build_proposal_facade` | 4E |

## 3. Design-to-test traceability

| Design section | Implementation | Tests |
|---|---|---|
| §6–§7 Canonical serialization, digest covers origin, content-only envelope, UTC instants, exponent-preserving decimals, refusal of unknown types | `canonical.py` (handwritten field tables dispatched by exact type), `requests.py` | `tests/test_application_canonical.py` (golden digests, ordered field-drift checks, subclass and spoof refusal, sensitivity of every field, cross-process determinism) |
| §11 Lifecycle: sessions, registration, one approval per request, validate-then-consume redemption | `approval.py` | `tests/test_application_approval.py` (forgery, copies, pickle, foreign gate, look-alike sessions, wrong digest or kind without consumption, 16-thread races) |
| §11 step 6 Single construction of `HumanAuthorization` from the approval and the stored request | `authority.py` | `tests/test_application_classification.py`, `tests/test_application_dormancy.py`, `tests/test_application_account_state.py` (persisted authorization fields; session/request/digest mismatches refused) |
| §10, §12 Classification: locked classifier at request time, confirmation via approval only | `classification.py` | `tests/test_application_classification.py` (all 243 finding/origin combinations; injected non-qualifying requests fail at the locked factory; fabricated evidence fails at the repository) |
| §12 Dormancy judgment, a separate human action | `dormancy.py` | `tests/test_application_dormancy.py` (every answer pair recorded as given; two-step judgment then transition) |
| §10, §12 Transitions: referential reads, one coherence comparison, execution re-verified by the repository | `account_state.py` | `tests/test_application_account_state.py` (locked state machine as oracle; stale requests fail closed; evidence enforcement; stored-content binding; `RequestNotCoherent.result` identity) |
| §13 Proposal path | `proposals.py`, `HumanCommandFacade.request_from_proposal` | `tests/test_application_proposals.py` (exact field types; authority substitutions refused; origin preserved into the request digest; normal request-time validation; no bypass of approval) |
| §8, §16 Command facade for the future UI | `facades.HumanCommandFacade`, `composition.build_command_facade` (one shared gate) | `tests/test_application_proposals.py`, `tests/test_application_errors.py`, `tests/test_application_boundary.py` |
| §8, §17 Proposal facade on a separate query-only connection | `facades.ProposalFacade`, `composition.open_read_connection`, `composition.build_proposal_facade` | `tests/test_application_proposals.py` (isolation walk; SQLite rejects writes; read connection failure cases; no file created) |
| §15 Errors propagate unchanged | all services and facades | `tests/test_application_errors.py` |
| §14 Production import boundaries | — | `tests/test_application_boundary.py` (§5 below) |

## 4. Authority boundaries

- **Gate sentinel.** `_GATE_KEY` exists only in `approval.py`; `HumanApproval.__post_init__` refuses any other key, so only `HumanConfirmationGate.approve` constructs approvals.
- **Exact identities.** A session is valid only as the exact object the gate issued. An approval is redeemable only as the exact object the gate issued. Copies, pickles, look-alikes, and objects from another gate are refused.
- **Registration** requires the exact open session and a request belonging to it (`register(session, request)`).
- **Redemption** checks, in order: type, issued identity, single use, open session, bound approved request, recomputed digest, expected kind. Any failure consumes nothing. After a valid redemption the approval stays consumed even if the domain or repository then refuses.
- **Authorization** takes actor and time from the approval and action, subject, and target from the stored request. It refuses a pair whose request id, digest, or session differ.
- **Commands** on every service and on `HumanCommandFacade` take exactly `(self, approval)`. All content comes from the redeemed stored request.

## 5. Static architecture rules (production code; tests exempt)

| Rule | Enforced in `tests/test_application_boundary.py` |
|---|---|
| R1 | `HumanAuthorization(...)` only in `data.repository`, `data.seed`, `application.authority` |
| R2 | Inside the application layer, only `authority.py` imports `HumanAuthorization` at runtime |
| R3 | `baec_app/interfaces/**` (not yet present) never imports `baec_app.data` |
| R4 | `_GATE_KEY` referenced only in `approval.py`; `HumanApproval` constructed only there |
| R5 | `application.authority` imported only by `classification.py`, `dormancy.py`, `account_state.py` |
| R6 | Domain imports neither data nor application; data does not import application |
| R7 | `proposals`, `requests`, `canonical`, `context`, `errors` import nothing from the data layer |
| Canonical purity | No generic introspection, `repr`, or pickling in `canonical.py` |
| Rejection kinds | Application code uses `TransitionRejectionKind` only as `.AUTHORIZATION_MISSING` (members, iteration, indexing, reflective access, and `.value` comparisons with other rejection values are reported) |
| No model/MCP | No production import of model or MCP client packages |
| No parser | No `parse_proposal` definition |
| No authority parameters | No public application or interface function takes `authorization`, `authorized_by`, `authorized_at`, `approved`, `human_confirmed`, or `is_approved` |
| Signatures | Every authoritative command is exactly `(self, approval)` |

Each rule is checked against synthetic violating and allowed source snippets. Known limitations, accepted under the Phase 4 threat model: dynamic Python construction (for example `type(obj)(...)`) and rejection strings held in variables are not detected statically.

## 6. Transition services

- Request-time reads use existing public repository methods only. `ReferenceMismatch` is used only for relations the locked domain cannot see: an interaction's owning account, and a judgment's membership in the named BAEC (an absent judgment id gives the same error).
- A transition request registers only when the locked preview's rejections are exactly `(AUTHORIZATION_MISSING,)`. Otherwise `RequestNotCoherent` carries the unchanged preview object. No application code branches on any other rejection kind.
- Execution calls the repository's `persist_transition_to_*`, which re-loads and re-verifies everything, and returns its `TransitionResult` unchanged. Stale requests return a rejected result or raise `RepositoryConflictError`; nothing partial is written.
- Active Opportunity requires real `EvaluationEvidence`. A human approval is authorization, never evidence; no signal type exists.

## 7. Proposal and read isolation

- `ProposalFacade` exposes reads, previews, and `propose_*` methods for BAEC confirmation and the three transitions; their proposals always have origin `DETERMINISTIC`. It holds a `ReadService` and an `AccountStatePreviewService` over the query-only repository, and no gate, command service, command facade, or writable repository; a structural walk and a direct write attempt confirm this.
- The proposal facade never proposes a dormancy judgment. Plausibility and addressability are human judgments, so a `DormancyJudgmentProposal` is created by UI code as a `HUMAN_DRAFT` and still goes through request, explicit approval, and recording. This is an implementation refinement, not a research-rule change.
- Each preview workflow has exactly one implementation: `classification.preview_classification` and the three `AccountStatePreviewService` methods. The command services delegate to them.
- `build_proposal_facade(read_connection)` requires an exact `sqlite3.Connection` at the current schema with `PRAGMA query_only == 1`; it is never derived from the command repository. It takes no clock.
- `open_read_connection` accepts a `str` or an `os.PathLike[str]` such as `pathlib.Path` (bytes paths are refused). It refuses empty paths, `:memory:`, caller-supplied `file:` URIs, directories, and missing files; opens an existing file with SQLite `mode=ro` (never creating a database); converts unreadable or non-database files to `ReadDatabaseUnavailable`; keeps `DatabaseVersionError` unchanged; sets and verifies `query_only`; and closes the connection on every failure after opening.
- A proposal grants nothing. `request_from_proposal` accepts only the five exact proposal types, runs the normal request-time validation, and copies the proposal's origin into the request digest. The request still needs an explicit human approval.

## 8. Error hierarchy

```
ApplicationError
├─ ApplicationValidationError (also ValueError)
│  └─ CanonicalizationError
├─ HumanActionError
│  ├─ SessionNotRecognized, RequestNotRecognized, RequestAlreadyRegistered,
│  ├─ RequestAlreadyApproved, DigestMismatch, ApprovalNotRecognized,
│  └─ ApprovalAlreadyUsed, ApprovalKindMismatch
├─ ProposalNotAuthoritative
├─ NotConfirmable            (carries the locked ClassificationResult)
├─ ReferenceMismatch
├─ RequestNotCoherent        (carries the locked preview TransitionResult)
├─ ReadOnlyConnectionRequired
└─ ReadDatabaseUnavailable
```

Domain and repository errors are never wrapped or reworded.

## 9. Refinements to the approved design

| Topic | Design | Implementation |
|---|---|---|
| Gate registration | `register(request)` | `register(session, request)`, requiring the exact issued session (4C correction) |
| Errors | Listed hierarchy | Adds `ApplicationValidationError` and `RequestAlreadyRegistered`. `ProposalValidationError` is not implemented: with no parser, invalid proposal objects raise `ApplicationValidationError`. `ProposalNotAuthoritative` sits directly under `ApplicationError`, as designed |
| Payload field tables | Keyed by type | Keyed by exact type; the payload table is filled on first use by a local import to avoid an import cycle |
| `ClassificationPreview` | Location unspecified | `requests.py`, with consistency checks against the locked classifier |
| R1 allowlist | Included `domain/models.py` | Only modules that actually construct: `data.repository`, `data.seed`, `application.authority` |
| Proposal objects | "Refuse nested authority objects" | Exact per-field type validation; no recursive scan |
| `parse_proposal` | In an earlier draft | Deferred; not implemented |
| Preview architecture | Unspecified | Gate-free `AccountStatePreviewService(repository)` owns the three transition previews and `preview_classification` owns the classification preview; `AccountStateService` and `ClassificationService` delegate to them, and `ProposalFacade` uses them over its query-only repository. `require_coherent_preview` is the single coherence comparison |
| `build_proposal_facade` | `(read_connection, *, clock)` | `(read_connection)`: the read/proposal path needs no clock |
| `propose_dormancy_judgment` | On `ProposalFacade` | Removed: dormancy judgments stay human-originated (`HUMAN_DRAFT`); the DTO and `request_from_proposal` support remain |
| `open_read_connection` paths | `str` | `str` or `os.PathLike[str]`, with all refusals unchanged |
| Service method names | Facade `request_*` | Services use `open_*_request`; the facade exposes the designed `request_*` names |
| Non-verbatim evidence | Content rules enforced at execution | A request with non-verbatim evidence can be opened and approved; execution then fails at the repository's evidence-fidelity check and the approval stays consumed |

## 10. Explicit deferrals

Not in Phase 4: Claude API; AI extraction; the `AI_MODEL` origin; persistent AI-origin provenance; connecting `ProposalFacade` to MCP, Claude, or any model or external input; `parse_proposal` or any mapping-to-proposal API; monitoring specifications; signals; correspondence matching; MCP server and tools; Streamlit UI; outbound or re-engagement messaging; the BAEC staleness lifecycle; Agent Skills; Subagents; authentication/SSO; signed approval tokens; proposal delivery between processes; account and interaction intake services.

## 11. Test runs

| Checkpoint | Result |
|---|---|
| 4A `ac86daa` | 1867 passed |
| 4B `a5a3be7` | 1937 passed |
| 4C `427cf05` | 2274 passed |
| 4D `12e7ea7` | 2403 passed |
| 4E `c70feb4` | 2659 passed, of which 1001 are Phase 4 application tests (Python 3.14.3, SQLite 3.50.4, 2026-10-01) |

These are software tests on synthetic data. They do not validate the theory.
