# V0.4 Phase 2 — Identity Durability and Authentication-Readiness Report

Branch `baec-crm-v0.4`, uncommitted working tree on top of `08c2b8f` (Phase 1). Synthetic data only. Everything below
is **implementation** (CLAUDE.md labels): software behavior and software tests of the product authorization
boundary. None of it is evidence for the BAEC theory, and none of it is a production security assessment. There is
still no authentication provider, no login, and no database-enforced tenant isolation.

## 1. Attribution audit (what was found)

Before Phase 2, every persisted actor field held a display-name string. None held a user id, except
`account_ownership.recorded_by_user_id` (Phase 1).

| Store | Record (id) | Actor field | Account link | Engine schema changed? |
|---|---|---|---|---|
| console | `account_meta` (account_id) | `created_by` | itself | — (product store) |
| console | `interaction_meta` (interaction_id) | `recorded_by` | `account_id` | — |
| console | `condition_drafts` (draft_id) | `captured_by` | `account_id` | — |
| console | `draft_resolutions` (draft_id) | `resolved_by` | via draft | — |
| console | `preparation_requests` (request_id) | `requested_by` | `account_id` | — |
| console | `preparations` (preparation_id) | `generated_by` = "Deterministic preparation" (no human) | via request | — |
| console | `preparation_edits` (edit_id) | `edited_by` | via preparation → request | — |
| console | `preparation_reviews` (preparation_id, artifact_id) | `reviewed_by` | via preparation → request | — |
| console | `preparation_blocks` (decision_id) | `decided_by` | `account_id` | — |
| console | `events` (event_id) | `actor` (+ `actor_kind`) | `account_id` | — |
| CRM | `contacts` (contact_id) | `created_by` | `account_id` | — |
| CRM | `tasks` (task_id) | `created_by`, `completed_by` | `account_id` | — |
| CRM | `activities` (activity_id) | `created_by` | `account_id` | — |
| CRM | `interaction_links`, activity → Engine 1 attachment | none | `account_id` | — |
| Engine 1 | BAEC confirmation (baec_id) | `human_authorizations.authorized_by` | candidate.account_id | **No** |
| Engine 1 | dormancy judgment (judgment_id) | authorization.authorized_by | via BAEC | **No** |
| Engine 1 | account-state transition (transition_id) | authorization.authorized_by | `account_id` | **No** |
| Engine 2 | audit events (entity_type, entity_id, event_type) | `actor` / `recorded_by` / `reviewer` | via plan → BAEC | **No** |

Engine 1 and Engine 2 records each have a stable identity of their own (BAEC id, judgment id, transition id, Engine 2
audit entity), so a product-side mapping keyed by that identity was preferable to changing a validated engine
schema. Engine 1–3 schemas and code are unchanged.

## 2. Immutable attribution model

`console_store.attributions` (console store schema V4), append-only (UPDATE/DELETE refused by trigger):

```
record_type, record_id, action  -> PRIMARY KEY
account_id, user_id, organization_id, display_name_snapshot, recorded_at, basis (RECORDED | BACKFILL)
```

* `user_id` is the immutable identity; `display_name_snapshot` is the name the user had when acting, never rewritten.
* Every existing actor label in every store is still written exactly as before. Attribution is additional.
* Console-store records are attributed in the same transaction as the record. CRM, Engine 1, and Engine 2 records
  live in other SQLite files, so their attribution is written immediately after; if that write fails, the record
  keeps its label and is **unattributed — never misattributed**.
* Engine 2 attributions use `("engine2." + audit entity_type, entity_id, audit event_type)`, so each human-caused
  Engine 2 audit event maps to exactly one attribution.
* Engine 1 returns no transition id; a state change is attributed only if exactly one new transition appeared on the
  account during the operation, otherwise left unattributed.
* `generate_preparation` is attributed as `GENERATION_REQUESTED` (who asked); the artifact's generator label stays
  "Deterministic preparation". Engine-kind events (Engine 1 classifier, Engine 2, Engine 3 validator) are never
  attributed to a person.
* `ConsoleService.attributions(account_id)` (tenancy-checked) and `TimelineItem.actor_user_id` expose it.

## 3. Migration and backfill

**Console store V3 → V4** (additive): creates `attributions` and `migration_log`, marks `attribution_backfill`
PENDING. The service completes it once, whoever opens the store first, in one transaction, then marks it DONE and
writes a `migration_log` entry with the rule text and per-record-type attributed/unattributed counts.

Backfill rule (conservative): a recorded label on an account owned by organization O is attributed to user U of O
only if (a) U is O's `SYSTEM_SEED` user and the label is U's display name, or (b) U is the **only non-seed user O
has ever had** (users are never deleted) and the label is U's current display name. Everything else stays
unattributed with its label intact. Consequences, all tested:

* V0.3 data whose actions carry the local user's name → attributed to `local-user`; the backfilled set equals
  exactly what live recording would have produced for the same workflow.
* Labels from before a rename, or under a different V0.3 name → unattributed (a name alone is not proof).
* An organization with two possible human actors → nothing attributed (names can be reused).
* Idempotent: reopening adds no rows and no second log entry. Engine 1, Engine 2, and CRM files are only read
  (Phase 1's byte-digest test still passes).

**Identity store V1 → V2** (additive): adds `users.kind` and the append-only `identity_audit`. Kinds are assigned only
to the exact ids Phase 1 code created — `local-user` in `local` → `LOCAL_DEVELOPMENT`, `seed_user_id(org)` →
`SYSTEM_SEED` — matched on stored user id, never on display name. Any other pre-existing user becomes
`LEGACY_UNCLASSIFIED` (never eligible). Each classification is audited.

## 4. Principal freshness

Every public method of `ConsoleService` and `Console` is wrapped by `_guarded` (a class decorator, so a method added
later is guarded automatically; `ids` and `now` are the only exemptions and touch no stored data). Before the method
body runs, the guard opens the identity store and requires: user exists, user active, user's organization equals the
principal's organization, organization exists, and — when `authentication_required` — the user is an eligible HUMAN.
Failure raises `PrincipalInvalid` (a `ConsoleError`, distinct from `AccessDenied`); nothing is read or written.
Nested calls for the same principal in the same thread are checked once (thread-local).

* The display name is **not** compared. A principal carrying a stale or wrong label is still that user; the guard
  adopts the stored current name so new actions record it. (Phase 1 refused a wrong label at construction; that
  check was removed deliberately and its test updated.)
* Reactivation: access resumes on the next operation of the same running console. Nothing was read or written while
  inactive.
* Streamlit: `_console(path, mode, authenticated_user_id)` is cached by immutable identity inputs only (no display
  name). The cached console re-validates on every operation, and the app shows the refusal and stops on
  `PrincipalInvalid`.

## 5. Rename

`IdentityStore.rename_user(user_id, name, at=...)` keeps `user_id` and organization, refuses a name another user of
the same organization holds, and audits `USER_RENAMED old -> new`. Old attributions keep their snapshot; new actions
record the new name; authorization never reads the name. A rename made outside the running console (another
session) reaches it on its next operation.

## 6. System and development identities

`UserKind`: `HUMAN`, `LOCAL_DEVELOPMENT`, `SYSTEM_SEED`, `PUBLIC_DEMO_SESSION`, `LEGACY_UNCLASSIFIED`. Stored explicitly,
required on creation (`ensure_user(..., kind=)`), and fixed by trigger (`a user kind never changes`).
`User.authentication_eligible` is true only for an active HUMAN user. `IdentityStore.authenticated_principal(user_id)`
— the seam a future provider must use — refuses every other kind. Re-provisioning an existing id with another kind
is refused.

## 7. Deployment mode

`deployment.py` reads `BAEC_DEPLOYMENT_MODE`, which must be exactly `development` or `production`. Unset, empty,
malformed, or any other value raises `DeploymentModeNotConfigured`; there is no default and no fallback. The app shows
the configuration error and stops before any Console exists, so no identity store, local principal, workspace, or
CRM file is created or opened. Local development is launched with `BAEC_DEPLOYMENT_MODE=development` set explicitly;
tests set it in an autouse fixture.

* Development: `Console.local_development` as before.
* Production: `runtime_console` requires an authenticated user id naming an eligible HUMAN; there is no provider, so
  the app always receives none and shows "Sign-in is not available yet… Nothing was read or changed." It renders no
  navigation, name field, or demo control, and creates no identity store. `Console.local_development` itself refuses
  outside development, so nothing can fall back to `local-user`; `local-user` and seed ids are refused even if named
  explicitly; a production console re-checks eligibility on every operation.

(Revised after Phase 2 review: the first gate draft treated an unset mode as development. That default was removed.)

## 8. Synthetic demo isolation

Each organization has its own synthetic workspace directory (`synthetic_directory`): the `local` organization keeps
the V0.3 `synthetic/` location (so migrated V0.3/Phase 1 demo data stays where it was); every other organization uses
`synthetic-organizations/<sha256(org id)[:32]>/`. Load, reset, and inspect touch only that directory. The Phase 1
"another organization owns synthetic accounts" check is kept inside `destroy` as a second check. Engine isolation is
unchanged: each directory has its own four stores, created through the same engine openers.

Edge case: a Phase 1 store in which a non-local organization had seeded the shared `synthetic/` directory leaves that
data in place, invisible to everyone but its owner and no longer reachable from that organization's new directory;
the local organization's reset of `synthetic/` is then refused by the second check. Synthetic data only.

## 9. Public-runtime capability boundary

`public_runtime.py` is now an allowlist: `PUBLIC_SERVICE_READS` (18 read methods), `PUBLIC_CONSOLE_READS` (5), plus a
few plain values (`workspace`, `actor`, `principal`, `root`). Any other method — including any added later — returns a
function that refuses with the read-only message; any other attribute (paths, clock) is refused on access. `owner` and
`owner_of_preparation` return the wrapped services. The public visitor is a `PUBLIC_DEMO_SESSION` user.

Refused today (enumerated by the test from the classes, not hand-listed): 28 `ConsoleService` methods —
`add_observation, block_preparation, capture_condition, change_account_state, confirm_condition, create_account,
crm_add_activity, crm_add_contact, crm_add_task, crm_attach_engine1_interaction, crm_complete_task,
crm_link_interaction, destroy, discard_draft, edit_artifact, generate_preparation, ids, initialize, now,
queue_observation_for_review, record_dormancy_judgment, record_interaction, record_not_confirmable,
request_preparation, review_artifact, start_monitoring, submit_correspondence_review, unblock_preparation` — on both
workspaces, and 4 `Console` methods — `local_development, reset_synthetic, seed_service, set_actor`. Each refusal is
checked to leave the session's state unchanged. A reviewed-reads list in the test must be edited before anything can
join the allowlist. The frozen public research release was not modified.

## 10. Account ownership transfer (audit only)

`account_ownership` has one row per account (primary key `account_id`) and refuses UPDATE and DELETE by trigger.
There is no transfer mechanism, and none was added. A future transfer needs at least: an explicit policy (who may
initiate and approve, in which organizations); an append-only ownership-history model (current owner = latest
entry) rather than an `UPDATE organization_id`; a decision about attributions, CRM records, and preparations made
under the old owner (they stay attributed to the old organization's users); a decision about synthetic accounts
(transfer should be refused); and an audit event visible to both organizations.

## 11. Application-code isolation (known V0.4 limitation)

Tenancy is enforced in `ConsoleService`. **Direct repository/store access bypasses product tenancy**: anything that
opens the SQLite files (engine repositories, `ConsoleStore`, `CRMStore`, `IdentityStore`) sees every organization's
rows in the shared `local/` files. This is not equivalent to database-enforced tenant isolation; that is planned with
the production Postgres/RLS architecture.

## 12. Test results (`test_identity_freshness.py`, `test_tenancy.py`, `test_public_runtime.py`)

| Matrix item | Result |
|---|---|
| Deactivated user, same console object: 6 reads, 9 lists, 10 writes refused; state unchanged; reactivation restores | Passed |
| Refusal is `PrincipalInvalid`, not `AccessDenied`, and identical for real and guessed ids | Passed |
| Principal claiming another organization refused; `organization_id` cannot be updated | Passed |
| Display-name change never invalidates; current name adopted | Passed |
| Every public `ConsoleService` / `Console` method guarded (static) | Passed |
| Rename: user id survives, old snapshot kept, new action uses new name, rename audited | Passed |
| Rename made elsewhere reaches a running console | Passed |
| Every human action in the full workflow attributed (20 record types); engine items never attributed | Passed |
| Attributions append-only | Passed |
| V0.3 backfill equals live attribution; idempotent; logged | Passed |
| Unprovable labels / two-actor organization left unattributed | Passed |
| V1 identity store: kinds by stored id only; unknown → `LEGACY_UNCLASSIFIED`; audited; idempotent | Passed |
| Cross-user: A's and B's consoles never serve each other; B's deactivation does not affect A | Passed |
| App cache keyed by `(path, mode, authenticated_user_id)` only | Passed |
| Two organizations load/reset/inspect demos independently, seeded by their own seed users | Passed |
| Only active HUMAN eligible; LOCAL_DEVELOPMENT / SYSTEM_SEED / inactive refused; kind immutable | Passed |
| Mode must be explicit: unset, empty, malformed, unknown → configuration error, nothing created, app shows error only | Passed |
| Explicit development permits the local-development principal | Passed |
| Production with no user refuses and creates nothing; no fallback to `local-user` | Passed |
| No LOCAL_DEVELOPMENT / SYSTEM_SEED / PUBLIC_DEMO_SESSION / LEGACY_UNCLASSIFIED user satisfies production sign-in | Passed |
| Production accepts an eligible human and keeps re-checking; app in production shows refusal only | Passed |
| App shows refusal after its user is deactivated (same process) | Passed |
| Public runtime: 28 service mutators × 2 workspaces + 4 console mutators refused, state unchanged | Passed |
| Phase 1 cross-tenant matrix, now 49 actions incl. "read attributions" × (A→B, B→A, guessed) = 147 cases | Passed |

Mutation checks: with the freshness guard replaced by a no-op, 8 of the 28 Phase 2 tests fail (the deactivation,
organization-mismatch, rename-propagation, cross-user, production re-check, and app-refusal tests). With the public
allowlist replaced by pass-through, 63 of 68 public-runtime tests fail.

## 13. Known limitations before real authentication and Postgres

* No authentication provider, login, sessions, or credential handling; `runtime_console(authenticated_user_id=)` is
  only the seam a provider would call.
* `HumanAuthorization` in the domain layer remains a requirement, not proof of human approval.
* Tenancy is application-code only (§11); SQLite files are shared across organizations in `local/`.
* Cross-file writes are not atomic: a CRM/engine record can exist without its attribution (never with a wrong one).
* The freshness check opens the identity store on every outermost operation; acceptable for SQLite, to be revisited.
* No user administration UI (deactivate/reactivate/rename others), invitations, roles, or organization switching.
* Ownership transfer is not supported (§10). Phase 1 shared-synthetic edge case (§8).
