# V0.4 Phase 1 — Tenancy Test Report

Generated from `pytest baec_console/tests/test_tenancy.py -v` on branch `baec-crm-v0.4` (uncommitted working tree,
base tag `baec-crm-v0.3.0`). Synthetic data only. These are software tests of the product authorization boundary;
they are not evidence for the BAEC theory and not a production security assessment.

## Setup

Organization `org-a` (user `user-a`) and organization `org-b` (user `user-b`) share **one data root** — the same
Engine 1, Engine 2, console, and CRM SQLite files. Each organization runs the full workflow (interaction, confirmed
BAEC, pending draft, dormancy judgment, monitoring plan, observation, correspondence candidate, human-verified
review, preparation request, generated preparation) plus a contact, task, activity, and CRM interaction link.

Each case asserts that the acting principal receives `AccessDenied` with the exact message
"That record does not exist or is not available to you." **and** that the target organization's view of its
own account (timeline, state, drafts, observations, candidates, requests, preparations and their reviews/edits,
contacts, tasks, activities, interaction link) is identical before and after the attempt.
The guessed-ID column runs the same operation as `org-a` against fabricated IDs that exist nowhere.

## Cross-organization action matrix

| Action attempted | User A → Org B | User B → Org A | Guessed/unknown ID |
|---|---|---|---|
| accept artifact | Refused | Refused | Refused |
| accept edited artifact | Refused | Refused | Refused |
| add buyer interaction | Refused | Refused | Refused |
| add contact | Refused | Refused | Refused |
| add observation | Refused | Refused | Refused |
| add observation correcting a foreign observation | Refused | Refused | Refused |
| add observation to own condition from a foreign source | Refused | Refused | Refused |
| add task | Refused | Refused | Refused |
| add task to own account with foreign contact | Refused | Refused | Refused |
| attach Engine 1 interaction to activity | Refused | Refused | Refused |
| attach foreign interaction to own activity | Refused | Refused | Refused |
| block preparation | Refused | Refused | Refused |
| capture condition | Refused | Refused | Refused |
| capture condition on own account from foreign interaction | Refused | Refused | Refused |
| change account state | Refused | Refused | Refused |
| change own account state with foreign condition | Refused | Refused | Refused |
| change own account state with foreign evidence | Refused | Refused | Refused |
| complete task | Refused | Refused | Refused |
| configure monitoring | Refused | Refused | Refused |
| configure monitoring of foreign condition on own account | Refused | Refused | Refused |
| confirm condition | Refused | Refused | Refused |
| discard condition draft | Refused | Refused | Refused |
| edit artifact | Refused | Refused | Refused |
| generate preparation | Refused | Refused | Refused |
| link foreign interaction to own account | Refused | Refused | Refused |
| link interaction | Refused | Refused | Refused |
| log activity | Refused | Refused | Refused |
| log activity on own account with foreign contact | Refused | Refused | Refused |
| preview account state change | Refused | Refused | Refused |
| preview condition | Refused | Refused | Refused |
| queue foreign observation under own condition | Refused | Refused | Refused |
| queue observation for review | Refused | Refused | Refused |
| read account | Refused | Refused | Refused |
| read account timeline | Refused | Refused | Refused |
| read activities | Refused | Refused | Refused |
| read contacts | Refused | Refused | Refused |
| read correspondence review packet | Refused | Refused | Refused |
| read monitoring handoff | Refused | Refused | Refused |
| read preparation | Refused | Refused | Refused |
| read preparation eligibility | Refused | Refused | Refused |
| read preparation snapshot | Refused | Refused | Refused |
| read tasks of account | Refused | Refused | Refused |
| record condition as not confirmable | Refused | Refused | Refused |
| record dormancy judgment | Refused | Refused | Refused |
| reject artifact | Refused | Refused | Refused |
| request preparation | Refused | Refused | Refused |
| review correspondence | Refused | Refused | Refused |
| unblock preparation | Refused | Refused | Refused |

Matrix cases: 144; refused as required: 144; not refused: 0.

## Other tenancy and identity checks

| Test | Result |
|---|---|
| `test_other_organization_is_absent_from_every_list[A-to-B]` | Passed |
| `test_other_organization_is_absent_from_every_list[B-to-A]` | Passed |
| `test_same_organization_operations_still_succeed` | Passed |
| `test_users_in_one_organization_share_its_data_and_each_action_names_its_user` | Passed |
| `test_console_refuses_a_principal_that_is_not_an_active_stored_user` | Passed |
| `test_display_names_are_unique_within_an_organization_only` | Passed |
| `test_local_development_principal_is_deterministic` | Passed |
| `test_identity_store_holds_no_credentials` | Passed |
| `test_local_principal_literals_live_only_in_the_identity_module` | Passed |
| `test_sidebar_shows_the_current_principal` | Passed |
| `test_seeded_demo_belongs_to_the_loading_organization_only` | Passed |
| `test_v03_data_migrates_to_the_local_organization_without_changing_any_record` | Passed |
| `test_migration_assigns_v03_data_to_local_even_if_another_organization_opens_it_first` | Passed |
| `test_an_account_without_an_owner_is_visible_to_no_one` | Passed |

## Mutation check

With `ConsoleService._authorize` and `has_account` replaced by always-allow stubs, 112 of the 158 tests in
`test_tenancy.py` fail. The two cross-tenant cases that still pass ("add observation to own condition from a
foreign source") are covered by the separate source guard in `add_observation`; before that guard existed they
failed because Engine 2's refusal message named the foreign source.
