# Phase 4 Design: Application & Human Authorization Boundary

> **Approved design — implemented at `c70feb4`; see `docs/PHASE4_IMPLEMENTATION_TRACEABILITY.md`.**
> This document is the approved design. Where the implementation refines it, the traceability document records the difference.

**Project:** BAEC Trigger Intelligence
**Baseline:** `phase-3-evidence-fidelity-hardening` (commit `7a01c24`), schema version 4, 1658 tests passing.
**Authority:** The manuscript, then `docs/RESEARCH_CONTRACT.md`, then this design, then code. This design adds no research rules. Every mechanism here is an **IMPLEMENTATION** choice. Phase numbers are planning labels (see `docs/PROJECT_PHASES.md`).

---

## 1. Problem

Gate 1 defines `HumanAuthorization` as a domain requirement (RC-31). Phase 3 persists it. Storing one is not proof that a human acted: any code holding a `Repository` can construct `HumanAuthorization(...)` and write authoritative state.

Phase 4 adds a thin application layer above the repository so that:

- authoritative writes can be reached only through an approval that a trusted human-interaction adapter issued for a stored, immutable request;
- `HumanAuthorization` is constructed in exactly one application module, from the redeemed request;
- a read-and-propose facade exists on a separately supplied query-only connection, with no route to approvals or commands;
- static rules stop production interface code from using the writable repository or constructing `HumanAuthorization`.

Phase 4 changes no locked layer. It makes no edits to `baec_app/domain`, `baec_app/data`, the schema, or any existing test. All 1658 existing tests must remain byte-identical and passing.

## 2. Trust and authority model

| Concern | Phase 4 provides | Phase 4 does not provide |
|---|---|---|
| Architecture and authority separation | Separate proposal and command paths. Commands accept only gate-issued approvals and take their content from stored requests. One construction site for `HumanAuthorization` in application code. Import-boundary tests. | Protection against malicious arbitrary code running inside the trusted process. Such code can import the gate, call `approve`, or use the repository directly. |
| Human-action provenance | An approval exists only if the trusted adapter called `approve` for a registered request in an open session. `authorized_by` and `authorized_at` come from that session and that action. | Proof that the adapter is honest, that the actor is who they claim to be, or that the human reviewed carefully. |
| Read path | A SQLite-enforced connection-level write guard (`PRAGMA query_only`, read-only open mode) for the configured proposal/read path. | Any guarantee about other connections or other processes. |
| Security | — | Authentication, identity, cryptographic signing, cross-process trust. These require a later UI/session/authentication layer. |

**Designed to prevent:**

1. model output, or any other data, being treated as human approval;
2. proposal objects being passed into authoritative writes;
3. a future model-facing layer changing authoritative state;
4. accidental direct repository writes from interface code;
5. an approval being replayed, or used for a different action, subject, or destination.

## 3. Alternatives considered

| | Approach | Decision |
|---|---|---|
| A | Callers pass `HumanAuthorization` directly (status quo) | Rejected. Any caller can mint it. |
| B | A sentinel-guarded approval type | Stops accidental construction, but the content can still be substituted. Used only as one part of C. |
| **C** | **A stored immutable request, plus a single-use approval issued by the gate, redeemed for the stored content** | **Approved** |
| D | Signed approval tokens (HMAC) | Deferred until approvals cross a process boundary. Compatible with C. |

C is the only option in which the redeemer cannot change what the human approved. Every command takes only the approval, so there is no parameter through which content could be substituted.

## 4. Architecture

```
 Trusted UI adapter (future)                  Deterministic application code
   │ explicit human action                       │ read / preview / propose (no model input in Phase 4)
   ▼                                             ▼
 HumanCommandFacade (writable Repository)       ProposalFacade (required query-only connection)
   ├─ request_*: referential validation + locked preview → ApprovalRequest (registered in gate)
   ├─ gate.approve(session, request_id, displayed_digest)  ← trusted adapter only
   ├─ command(approval): gate.redeem validates fully, THEN consumes
   │     └─ authority.authorize(request, approval) → HumanAuthorization
   │     └─ locked factory / Repository write (re-verifies everything)
   └─ save_nonconfirmed_classification (no HumanAuthorization; RC-31 requires none)
```

Package name: `baec_app/application/`. The repository is layered as domain → data → application → (future) interfaces. "Application" is the standard name for the use-case layer. `services/` was rejected as too generic, and `app/` because it reads as a UI entry point.

## 5. File tree

```
baec_app/application/
  __init__.py         public API: builders, facades, DTOs, errors
  errors.py
  context.py          Clock, IdFactory, SystemClock, UuidIdFactory, Actor, InteractionSession
  canonical.py        deterministic canonical serialization and digest
  requests.py         RequestKind, payload types, ApprovalRequest
  approval.py         HumanConfirmationGate, HumanApproval, private _GATE_KEY
  authority.py        authorize(): the only HumanAuthorization construction site in application code
  proposals.py        ProposalOrigin, proposal DTOs
  classification.py   ClassificationService
  dormancy.py         DormancyJudgmentService
  account_state.py    AccountStateService (previews, request validation, transitions)
  facades.py          ReadService, HumanCommandFacade, ProposalFacade
  composition.py      build_command_facade, build_proposal_facade, open_read_connection
tests/
  application_builders.py
  test_application_boundary.py
  test_application_canonical.py
  test_application_approval.py
  test_application_proposals.py
  test_application_classification.py
  test_application_dormancy.py
  test_application_account_state.py
  test_application_errors.py
```

## 6. Types

```python
# context.py
class Clock(Protocol):
    def now(self) -> datetime: ...                     # must return an aware datetime
class IdFactory(Protocol):
    def new_baec_id(self) -> str: ...
    def new_token(self) -> str: ...                    # session, request, and approval ids

@dataclass(frozen=True)
class Actor:
    actor_id: str                                      # self-asserted in V0.1

@dataclass(frozen=True)
class InteractionSession:
    session_id: str
    actor: Actor
    opened_at: datetime

# proposals.py
class ProposalOrigin(Enum):
    HUMAN_DRAFT = "HUMAN_DRAFT"        # values entered by a human through the UI
    DETERMINISTIC = "DETERMINISTIC"    # produced by deterministic application code
    # Exactly these two in Phase 4. AI_MODEL is deliberately absent (see §18).

@dataclass(frozen=True)
class ConfirmationProposal:
    candidate: BaecCandidate; captured_at: datetime; origin: ProposalOrigin
@dataclass(frozen=True)
class DormancyJudgmentProposal:
    baec_id: str; plausibility: ReviewAnswer; addressability: ReviewAnswer; notes: str | None; origin: ProposalOrigin
@dataclass(frozen=True)
class MoveToDormantProposal:
    account_id: str; baec_id: str; judgment_id: int
    non_evaluation_evidence: NonEvaluationEvidence | None; origin: ProposalOrigin
@dataclass(frozen=True)
class MoveToActiveProposal:
    account_id: str; evaluation_evidence: EvaluationEvidence; origin: ProposalOrigin
@dataclass(frozen=True)
class MoveToNoPlausiblePathProposal:
    account_id: str; ground: NoPlausiblePathGround; reason: str
    non_evaluation_evidence: NonEvaluationEvidence | None; basis_interaction_id: str | None; origin: ProposalOrigin
# No proposal has an authorization, approval, approved, actor, or authorized_by field.
# __post_init__ refuses any nested HumanAuthorization, HumanApproval, or ApprovalRequest.

# requests.py
class RequestKind(Enum):
    CONFIRM_BAEC; RECORD_DORMANCY_JUDGMENT
    MOVE_TO_CONDITIONALLY_DORMANT; MOVE_TO_ACTIVE_OPPORTUNITY; MOVE_TO_NO_PLAUSIBLE_PATH

# Payloads have the same fields as the matching proposal, without origin.
# ConfirmBaecPayload also carries baec_id, which is assigned when the request is opened.

@dataclass(frozen=True)
class ApprovalRequest:
    request_id: str; session_id: str; opened_at: datetime          # bookkeeping; not in the digest
    kind: RequestKind
    action: AuthorizationAction                                     # derived from kind
    subject_id: str                                                 # derived from kind and payload
    target_state: AccountState | None                               # derived; set only for MOVE_* kinds
    origin: ProposalOrigin
    payload: <payload type for kind>
    digest: str                                                     # canonical.digest_request(...)
    preview: ClassificationPreview | TransitionResult | None        # display only; not in the digest

# approval.py  (constructed only by HumanConfirmationGate, inside approval.py)
@dataclass(frozen=True)
class HumanApproval:
    approval_id: str; request_id: str; session_id: str; actor_id: str
    digest: str; approved_at: datetime
    _key: object                                                    # must be approval._GATE_KEY

@dataclass(frozen=True)
class ClassificationPreview:
    candidate: BaecCandidate; result: ClassificationResult; confirmable: bool
```

## 7. Canonical request serialization (`canonical.py`)

`digest = SHA-256(UTF-8(canonical_json)).hexdigest()` (lowercase hex). The serialization never uses `repr()`, `str()` of dataclasses, `dataclasses.asdict`, or pickle.

**Envelope** (exactly these keys):

```
{"format": "baec-approval-request/v1", "kind", "action", "subject_id", "target_state", "origin", "payload"}
```

The digest covers content only. `request_id`, `session_id`, `opened_at` and `preview` are excluded. The gate registry enforces which request, session, and time an approval belongs to.

**JSON encoding:** `json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)`.

**Value rules.** This is an explicit encoder. Any other type raises `CanonicalizationError`, so it fails closed.

| Python value | Canonical form |
|---|---|
| `str` | As-is. No trimming, case folding, or Unicode normalization. |
| `None` | `null` |
| `int` (not `bool`) | JSON integer. `bool` is refused. |
| `Decimal` | `{"$decimal": str(value)}`. `"10"` and `"10.0"` stay distinct. |
| `datetime` | Must be aware. `{"$datetime": value.astimezone(UTC).isoformat(timespec="microseconds")}`, the UTC instant, the same encoding as the repository. |
| `Enum` | `member.value` |
| `tuple` | JSON array, order preserved |
| Domain or payload dataclass | `{"$type": "<ClassName>", <fields>}`, with fields taken from an explicit per-type field table |
| `float`, `list`, `dict`, `set`, `bytes`, anything else | Refused |

The field table covers `BaecCandidate`, `EvidenceExcerpt`, `CriterionAssessment`, `StringencyExpression`, `EvaluationEvidence`, `NonEvaluationEvidence`, and the five payload types. A test asserts each entry equals `dataclasses.fields` of its type. Changing the format requires bumping `v1`.

## 8. Public API (design level)

```python
# composition.py
def build_command_facade(repository: Repository, *, clock: Clock, ids: IdFactory) -> HumanCommandFacade

def build_proposal_facade(read_connection: sqlite3.Connection, *, clock: Clock) -> ProposalFacade
    # read_connection is required (no default) and is never derived from the command repository.
    # It must already be query-only (PRAGMA query_only == 1) and pass require_current_schema,
    # otherwise ReadOnlyConnectionRequired.

def open_read_connection(path: str) -> sqlite3.Connection
    # Opens an existing database file read-only and fails closed:
    #  - refuses ":memory:" and any path that does not name an existing database file;
    #  - opens with SQLite read-only mode (URI mode=ro), which never creates a file,
    #    so a missing database is an error and no empty database is created;
    #  - checks the SQLite version and the current schema version, then sets PRAGMA query_only = ON.
    # Uses only public data-layer functions (check_sqlite_version, require_current_schema,
    # make_read_only) plus sqlite3. No data-layer change.

# approval.py: HumanConfirmationGate (held only by HumanCommandFacade; thread-safe; in memory)
open_session(actor_id: str) -> InteractionSession
close_session(session: InteractionSession) -> None   # invalidates its open requests and unredeemed approvals
approve(session, request_id: str, *, displayed_digest: str) -> HumanApproval   # trusted adapter only; one per request
redeem(approval, expected: RequestKind) -> ApprovalRequest                     # called by command services
register(request: ApprovalRequest) -> None                                     # called by request_* methods

# HumanCommandFacade (for the future UI only)
gate: HumanConfirmationGate
reads: ReadService
preview_classification(candidate) -> ClassificationPreview
save_nonconfirmed_classification(candidate, *, captured_at) -> BaecRecord
request_baec_confirmation(session, candidate, *, captured_at, origin=HUMAN_DRAFT) -> ApprovalRequest
request_dormancy_judgment(session, baec_id, *, plausibility, addressability, notes=None, origin=HUMAN_DRAFT) -> ApprovalRequest
request_move_to_conditionally_dormant(session, account_id, *, baec_id, judgment_id,
                                      non_evaluation_evidence=None, origin=HUMAN_DRAFT) -> ApprovalRequest
request_move_to_active_opportunity(session, account_id, *, evaluation_evidence, origin=HUMAN_DRAFT) -> ApprovalRequest
request_move_to_no_plausible_path(session, account_id, *, ground, reason, non_evaluation_evidence=None,
                                  basis_interaction_id=None, origin=HUMAN_DRAFT) -> ApprovalRequest
request_from_proposal(session, proposal) -> ApprovalRequest          # origin copied from the proposal
confirm_baec(approval) -> BaecRecord
record_dormancy_judgment(approval) -> PersistedDormancyJudgment
move_to_conditionally_dormant(approval) -> TransitionResult
move_to_active_opportunity(approval) -> TransitionResult
move_to_no_plausible_path(approval) -> TransitionResult
preview_move_to_conditionally_dormant / _active_opportunity / _no_plausible_path(...) -> TransitionResult   # read-only

# ProposalFacade (deterministic read/preview/propose; no gate, request, command, or writable repository)
reads: ReadService
preview_classification(candidate) -> ClassificationPreview
preview_move_to_*(...) -> TransitionResult
propose_confirmation(candidate, *, captured_at) -> ConfirmationProposal      # origin DETERMINISTIC; refuses non-confirmable
propose_dormancy_judgment(...) / propose_move_to_*(...) -> <Proposal>        # origin DETERMINISTIC

# ReadService
get_account, list_accounts, get_interaction, list_interactions, get_baec_record,
list_baec_records, list_dormancy_judgments, get_transition_history
```

Every command method has exactly the signature `(self, approval)`.

Previews call the locked `transition_to_*` functions on objects read from storage, with `authorization=None`. `AUTHORIZATION_MISSING` is therefore always present in a preview.

## 9. Which actions require human authorization

No new authorization requirement is added. The table follows RC-31 and the locked domain functions.

| Action | `HumanAuthorization` |
|---|---|
| Confirm a `CONFIRMED_BAEC` | `CONFIRM_BAEC`, subject `baec_id` |
| Record a dormancy judgment | `RECORD_DORMANCY_JUDGMENT`, subject `baec_id` |
| Any account-state change | `CHANGE_ACCOUNT_STATE`, subject `account_id`, with `target_state` |
| Save a `NOT_BAEC` or `INSUFFICIENT_EVIDENCE` record | None. Offered on the command facade only; not offered on the proposal facade. |
| Classify, preview, read, propose | None; nothing is written |

## 10. Request-time validation

Before a request is registered, the facade loads every referenced persisted object through public repository reads, then builds the display preview with the locked functions. It does not re-implement any rule.

| Request | Loaded and checked | Locked preview attached |
|---|---|---|
| Confirm BAEC | Account; the source interaction exists and belongs to the account | `classify_candidate`. If not `CONFIRMED_BAEC`, raises `NotConfirmable` carrying the canonical `reason_text`. |
| Dormancy judgment | The BAEC record exists | none |
| Move to Conditionally Dormant | Account; BAEC record; the judgment is one of that BAEC's judgments (`list_dormancy_judgments`); the non-evaluation evidence's interaction exists and belongs to the account | `transition_to_conditionally_dormant(..., authorization=None)` |
| Move to Active Opportunity | Account; the evaluation evidence's interaction exists and belongs to the account | `transition_to_active_opportunity(..., authorization=None)` |
| Move to No Plausible Path | Account; the basis interaction and the evidence interaction exist and belong to the account | `transition_to_no_plausible_path(..., authorization=None)` |

- A missing object raises the repository's own `RepositoryNotFoundError`, unchanged.
- An object belonging to another account, or a judgment not recorded for the named BAEC, raises `ReferenceMismatch`. No repository method is added.
- If the locked transition preview contains any rejection other than `AUTHORIZATION_MISSING`, the request is refused with `RequestNotCoherent`, carrying the preview `TransitionResult` unchanged.
- Content rules are not pre-checked. Classification and state-machine eligibility come from the locked previews above. Evidence fidelity and every other repository rule are enforced at execution.

**Stale requests fail closed.** Execution re-loads everything through the repository and re-runs the state machine. A request that was valid when opened but has since gone stale is rejected at execution, or raises `RepositoryConflictError`.

## 11. Human-confirmation lifecycle

1. **Session.** The trusted adapter calls `gate.open_session(actor_id)`. The actor id is self-asserted.
2. **Request.** A `request_*` method validates (§10), derives the action, subject, and target from the kind, assigns ids, computes the canonical digest, attaches the preview, and registers the request with the gate. A request grants nothing.
3. **Display.** The UI shows the request content, its preview, and its digest.
4. **Explicit human action.** The human activates an explicit approve control. Only in that control's callback does the adapter call `gate.approve(session, request_id, displayed_digest=...)`. The gate checks that:
   - the session is open and is the request's session;
   - the request is registered, not yet approved, and not redeemed;
   - the displayed digest equals the stored digest.

   It then constructs one `HumanApproval` (inside `approval.py`, with the private sentinel) and keeps a reference to that exact object. Exactly one approval is issued per request.
5. **Redeem: validate everything first, then consume.** Under the gate's lock, `gate.redeem(approval, expected_kind)` checks, in order:
   - the value is a `HumanApproval` and is the same object the gate issued (identity, so copies, unpickled objects, and hand-built objects fail);
   - it is not already consumed;
   - its session is still open;
   - its request is registered and unredeemed;
   - the digest recomputed from the stored request equals both the stored digest and the approval's digest;
   - the request kind equals the expected kind.

   Any failure raises and consumes nothing. A forged object, a wrong session, a digest mismatch, or a wrong kind leaves a genuine approval redeemable. Only once every check passes does the gate mark the approval and the request consumed, and then it returns the stored request.
6. **Authorize.** The command service calls `authority.authorize(request, approval)`, which returns `HumanAuthorization(approval.actor_id, approval.approved_at, request.action, request.subject_id, request.target_state)`. Command services never touch the sentinel.
7. **Execute.** The locked factory and/or repository write runs. A domain rejection or repository error after this point leaves the approval consumed. A retry needs a new request and a new approval.

## 12. Use-case sequences

**Save a non-confirmed classification.** `preview_classification` (locked classifier) → `save_nonconfirmed_classification` → `create_nonconfirmed_classification_record`, which refuses a confirmable candidate → `repo.save_classification_record`, which re-classifies and checks the reason text exactly. No approval.

**Confirm a BAEC.** `request_baec_confirmation` (the classifier runs at request time; `NotConfirmable` if the result is not `CONFIRMED_BAEC`) → human action → `confirm_baec(approval)` → redeem → `authorize` (`CONFIRM_BAEC`, `baec_id`) → `create_confirmed_baec_record`, which re-classifies, so confirmation cannot upgrade `NOT_BAEC` or `INSUFFICIENT_EVIDENCE` → `repo.save_confirmed_baec`, which re-classifies and enforces evidence fidelity.

**Dormancy.** These are two separate human actions:

1. `request_dormancy_judgment(baec_id, plausibility, addressability, notes)` → human action → `record_dormancy_judgment(approval)` → `authorize` (`RECORD_DORMANCY_JUDGMENT`, `baec_id`) → `DormancyJudgment` → `repo.record_dormancy_judgment` → `judgment_id`.
2. `request_move_to_conditionally_dormant(account_id, baec_id, judgment_id, non_evaluation_evidence?)` → referential checks; the locked preview may contain only `AUTHORIZATION_MISSING` → a second human action → `move_to_conditionally_dormant(approval)` → `authorize` (`CHANGE_ACCOUNT_STATE`, `account_id`, `CONDITIONALLY_DORMANT`) → `repo.persist_transition_to_conditionally_dormant(..., recorded_at=clock.now())`. The repository re-runs the state machine on the stored account, BAEC, and judgment:
   - addressability `UNKNOWN` is allowed and returned in `unresolved`;
   - `NOT_YET` and `NO` are rejected;
   - a BAEC that is not `CURRENT` is rejected.

   The `TransitionResult` is returned unchanged.

**Active Opportunity.** The request type requires an `EvaluationEvidence`:

- its constructor refuses `EXTERNAL_EVIDENCE`, and `EvidenceExcerpt` refuses `AI_INFERENCE` and `UNKNOWN`;
- the repository requires its text to occur verbatim in a stored interaction of the same account;
- then: human action → `move_to_active_opportunity` → `repo.persist_transition_to_active_opportunity`.

A human action supplies authorization, never evidence. No signal, AI inference, monitoring result, or button press alone can activate an opportunity.

**No Plausible Path.** Same pattern, with the ground, the reason, an optional basis interaction, and non-evaluation evidence (required when leaving Active Opportunity). It is recorded as easily as any other outcome (RC-23).

## 13. Proposal path

- In Phase 4, proposals come from exactly two sources:
  - `ProposalFacade.propose_*`, deterministic application code (origin `DETERMINISTIC`);
  - UI code carrying values a human entered (origin `HUMAN_DRAFT`).
- Proposals never carry or produce authority. A proposal reaches the command side only when the UI calls `request_from_proposal`, and then follows the full path: request, human action, redemption.
- Mappings, `True`, dictionaries such as `{"approved": True}`, `HumanAuthorization` objects, and proposals are refused by `approve`, `redeem`, every command, and `request_from_proposal` (which accepts only the five proposal types).
- Parsing external or model-produced data into proposals is not part of Phase 4 (see §18).

## 14. Production import boundaries

These are AST tests over production code: every module under `baec_app/` (which automatically includes any future `baec_app/interfaces/`) and `scripts/`. The `tests/` directory is exempt.

| Rule | Requirement |
|---|---|
| R1 | `HumanAuthorization(...)` is constructed only in `domain/models.py` (definition), `data/repository.py` (rehydration), `data/seed.py` (synthetic fixture), and `application/authority.py`. |
| R2 | Inside `baec_app/application/`, only `authority.py` imports the name `HumanAuthorization` at runtime. Other modules may import it only under `TYPE_CHECKING`. |
| R3 | Modules under `baec_app/interfaces/**` may import only the `baec_app.application` package API and `baec_app.domain`. They never import `baec_app.data`, and never construct `HumanAuthorization`. |
| R4 | `_GATE_KEY` is private to `application/approval.py`. No other production module imports or references it: not command services, facades, `authority.py`, interface modules, or anything else. `HumanApproval(...)` is constructed only inside `approval.py`, by `HumanConfirmationGate`. |
| R5 | `application/authority.py` is imported only by the command services (`classification.py`, `dormancy.py`, `account_state.py`). |
| R6 | Layering: `domain` imports neither `data` nor `application`; `data` does not import `application`. |
| R7 | `proposals.py`, `requests.py`, `canonical.py`, `context.py`, and `errors.py` import nothing from `baec_app.data`. |

The scanner is tested against synthetic source strings containing violations, to prove it reports them.

## 15. Error model

```
ApplicationError
├─ HumanActionError
│  ├─ SessionNotRecognized          unknown, closed, or from another gate
│  ├─ RequestNotRecognized          unknown, redeemed, or from another session
│  ├─ RequestAlreadyApproved
│  ├─ DigestMismatch
│  ├─ ApprovalNotRecognized         not the exact object this gate issued
│  ├─ ApprovalAlreadyUsed
│  └─ ApprovalKindMismatch
├─ ProposalNotAuthoritative         proposal, mapping, bool, or HumanAuthorization passed as authority
├─ ProposalValidationError          invalid proposal construction
├─ CanonicalizationError            a value outside the canonical encoder
├─ ReadOnlyConnectionRequired       proposal facade given a writable connection
├─ ReadDatabaseUnavailable          open_read_connection: missing, non-file, or unopenable database
├─ NotConfirmable                   carries ClassificationResult; message is the canonical reason_text
├─ ReferenceMismatch                a referenced object belongs to another account or BAEC
└─ RequestNotCoherent               carries the locked preview TransitionResult unchanged
```

| Source | Handling |
|---|---|
| Domain rejection at execution | `TransitionResult(allowed=False)` returned unchanged. Nothing written. The approval stays consumed. |
| `DomainValidationError` | Propagates unchanged. |
| `RepositoryNotFoundError`, `RepositoryConflictError`, `RepositoryVerificationError`, `PersistenceIntegrityError` | Propagate unchanged: never wrapped, reinterpreted, or reworded. |
| Invalid human-action context, or an attempt to use a proposal as authority | `HumanActionError` or `ProposalNotAuthoritative`. The repository is never called, and nothing is consumed. |

**Transactions.** Nothing beyond Phase 3. Each command makes exactly one repository write call, and each is one immediate, all-or-nothing transaction. The gate's state lives in memory and is not part of that transaction. Consumption precedes the write, so after a failed write the human must approve again (fail closed).

## 16. Future UI boundary (Streamlit; not built in Phase 4)

| Step | Who |
|---|---|
| Display a proposal or request (content, locked preview, digest) | UI rendering. No authority. |
| The human activates an explicit approve control (one per request, labelled with action, subject, and destination) | Human |
| `gate.approve(session, request_id, displayed_digest)`, only inside that control's callback and never on render or rerun | Trusted UI adapter. Creates the human-action context. |
| Constructing `HumanAuthorization` and passing it to the repository | Command service, via `authority.authorize`. The UI never sees or constructs one. |

The UI keeps the session in `st.session_state`. Echoing the digest catches a rerun that rebuilt different content behind the same control.

## 17. Future model/MCP boundary (not connected in Phase 4)

**In Phase 4, `ProposalFacade` is built and tested but is not exposed or connected to Claude, MCP, or any model-originated input.** The only origins are `HUMAN_DRAFT` and `DETERMINISTIC`. This prevents model-produced content from being labelled `DETERMINISTIC`.

`ProposalFacade` is the intended future boundary for model-facing code. It may be connected only after a later AI provenance phase has:

1. extended the origin model (for example with `AI_MODEL`); and
2. designed and implemented persistent AI-origin provenance (§18).

The future boundary, when enabled, is expected to look like this (to be confirmed in that phase):

- model-facing code receives only a `ProposalFacade` built on `open_read_connection(path)`, preferably in a separate process that never holds the gate or the command facade;
- it may read, analyze, and propose, and is never offered `approve`, `request_*`, `request_from_proposal`, commands, `save_nonconfirmed_classification`, or the writable repository;
- confirmation, dormancy judgment, and every state change still require an approval issued independently in a UI session by the trusted adapter.

## 18. Audit and provenance

- **Phase 4 uses transient approval context.** What persists is the existing `HumanAuthorization` row: `authorized_by` (self-asserted), `authorized_at`, action, subject, and target. Session id, request digest, and origin are not persisted. This is acceptable because Phase 4 origins are only `HUMAN_DRAFT` and `DETERMINISTIC`.
- **`AI_MODEL` and model/MCP proposal ingestion may not be enabled until persistent AI-origin provenance is designed and implemented.** The future phase will decide whether this involves proposal-ingestion records, approval receipts, atomic write receipts, or a combination. This design does not fix that schema or transaction design.
- A Phase 4 test asserts that `ProposalOrigin` has exactly `{HUMAN_DRAFT, DETERMINISTIC}`. Its failure message points to this requirement.
- Without authentication, a stored `authorized_by` is a label, not proof of identity.

## 19. Test plan

New test files only. The 1658 existing tests remain byte-identical and passing.

**Boundary** (`test_application_boundary.py`)
- R1–R7 on production code, including:
  - no production module except `approval.py` references `_GATE_KEY`;
  - `HumanApproval(...)` is constructed only in `approval.py`;
  - `authority.py` is imported only by the command services.
- Scanner self-tests against synthetic violations.
- Every command signature is exactly `(self, approval)`.
- Walking `ProposalFacade`'s attributes reaches no gate, request method, command service, or writable repository.
- `ProposalOrigin` has exactly two members.

**Canonical** (`test_application_canonical.py`)
- A golden digest vector for a fixed request.
- Field tables equal `dataclasses.fields`.
- Changing any single envelope or payload field changes the digest, origin included.
- `Decimal("10")` and `Decimal("10.0")` produce different digests.
- The same instant in different time zones produces the same digest.
- NFC and NFD forms of the same text produce different digests.
- `float`, `bool`, `list`, `dict`, naive `datetime`, and unknown types are refused.
- The digest is identical across processes with different `PYTHONHASHSEED` values.

**Approval** (`test_application_approval.py`)
- Single use, and exactly one approval per request.
- Refused, in each case: copied, deep-copied, unpickled, and hand-built approvals, and approvals from another gate.
- A closed session, a wrong session, and a wrong displayed digest are refused.
- **No consumption on failure:** each invalid attempt (forged object, wrong session, digest mismatch, wrong kind) leaves the genuine approval redeemable, and it then succeeds.
- **Consumption after a valid redemption:** a later domain rejection or repository error leaves the approval consumed.
- Concurrent redemption from threads: exactly one succeeds.
- `authorized_by` and `authorized_at` come from the session and the approval time (fixed clock).

**Proposals and the read path** (`test_application_proposals.py`)
- Proposal types refuse nested authority objects.
- Passing a proposal, a mapping such as `{"approved": True}`, `True`, or a `HumanAuthorization` to `approve`, `redeem`, any command, or `request_from_proposal` is refused, with `dump()` unchanged.
- `build_proposal_facade` refuses a writable connection, and a missing argument raises `TypeError`.
- `open_read_connection`:
  - fails closed for a missing path (no file is created), for `:memory:`, for a non-database file, and for a wrong schema version;
  - returns a connection with `query_only` set.
- Every `ProposalFacade` method runs on a query-only file connection and leaves `dump()` unchanged.
- An attempted write through the configured query-only connection raises `sqlite3.OperationalError`.

**Classification**
- Of all 243 finding/origin combinations, only all `MET` plus `BUYER_GENERATED` is confirmable. All others raise `NotConfirmable` with the canonical reason, and no request is registered.
- A confirmation request injected for a non-qualifying candidate fails at the factory with `DomainValidationError`, and nothing is written.
- `save_nonconfirmed_classification` refuses a confirmable candidate.

**Dormancy**
- The full sequence works for addressability `YES` and `UNKNOWN` (unresolved item returned and stored).
- `NOT_YET`, `NO`, and plausibility other than `YES`:
  - are refused at request time with `RequestNotCoherent` carrying the exact rejections;
  - are rejected at execution when the state changed after the request.
- A BAEC raw-set to `STALE` after the request is rejected at execution with `BAEC_NOT_CURRENT`.
- A judgment not recorded for the named BAEC raises `ReferenceMismatch`.
- A judgment approval cannot drive a transition, and the reverse.

**Account state**
- A Conditionally Dormant approval cannot drive the Active Opportunity or No Plausible Path commands.
- An approval for one account can never write another.
- Activation requires `EvaluationEvidence`. External, AI-inference, and non-verbatim evidence are refused.
- Leaving Active Opportunity without non-evaluation evidence is refused.
- Previews write nothing.
- A stale request (state changed by another writer) yields a rejected result or `RepositoryConflictError`, with no partial rows.

**Errors**
- Each repository error type propagates with its original class and message.
- `PersistenceIntegrityError` from tampered storage propagates at request time and at execution.

## 20. Deferred (not in Phase 4)

- Claude API, AI extraction, the `AI_MODEL` origin, and persistent AI-origin provenance
- Connecting `ProposalFacade` to MCP, Claude, or any model-originated input
- Parsing external or model-produced data into proposals
- Monitoring specifications, signals, correspondence matching
- MCP server and tools; advanced MCP
- Streamlit UI
- Outbound or re-engagement messaging
- BAEC staleness lifecycle
- Agent Skills, Subagents
- Authentication/SSO, signed approval tokens
- Delivering proposals between processes
- Account and interaction intake services
- Any change to the domain, data, or schema layers

## 21. Tradeoffs

- The in-memory gate loses requests and approvals on restart (fail closed). There is no expiry interval, because none is invented.
- Dormancy needs two human actions, and any failed execution needs a fresh approval. Both add friction and are safer.
- Previews and request-time validation read data that can change before execution. Execution is the authority.
- A live proposal facade needs a file-backed database. Tests use temporary file databases.
- Phase 4 provides accident-resistance and authority separation, not security against code running in the trusted process.

## 22. Decision record

| Ref | Decision |
|---|---|
| D2 | Package `baec_app/application/` |
| D3 | Stored immutable request plus single-use, gate-issued approval, with a private sentinel |
| D4 | Non-confirmed classification writes without `HumanAuthorization` |
| D5 | Transient approval context in Phase 4; AI-origin input requires persistent provenance first |
| D6 | Validate the redemption fully before consuming; consumed after any later domain or repository failure |
| D7 | Separate human actions for the dormancy judgment and the dormancy transition |
| D8 | Read-only transition previews |
| D9 | No `AI_MODEL` origin in Phase 4 |
| D10 | Account and interaction intake services deferred |
| F1 | `RequestNotCoherent` when a locked preview contains any rejection other than `AUTHORIZATION_MISSING` |
| F2 | Content-only digest; request, session, and time binding live in the gate registry |
| F3 | Datetimes canonicalized to the UTC instant |
| F4 | Exactly one approval per request |
| F5 | `ReferenceMismatch` without changing the repository API |
| F6 | `open_read_connection` built from public data-layer functions; fails closed and never creates a database |
| F7 | Production import scan covers `baec_app/**` and `scripts/`; tests exempt |
| F8 | This design is committed as a design checkpoint before implementation; no implementation tag yet |
| Rev. 1 | `_GATE_KEY` private to `approval.py`; `HumanApproval` constructed only there; enforced by a static test |
| Rev. 2 | `ProposalFacade` not connected to any model or MCP input in Phase 4 |
| Rev. 3 | `query_only` is described as a SQLite-enforced connection-level write guard, not a database-level guarantee |
| Rev. 4 | The future AI provenance mechanism is not fixed by this design |
