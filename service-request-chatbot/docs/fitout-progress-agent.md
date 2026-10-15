# Fitout Progress Agent — Design & Implementation Reference

> **Audience:** Backend engineers and QA extending or testing the Fitout Progress workflow.
> **Last updated:** September 2026
> **Status:** Implemented — create and approval flows, 283 tests. Not yet exercised
> against the live platform; the weekly platform scheduler that raises approvable
> SRs is disabled (PR 6655), so the approve path has no organic source of work.
> **Proposal it was built from:** [`docs/specs/fitout-progress-agent-prd.md`](specs/fitout-progress-agent-prd.md)

---

## Table of Contents

1. [Overview — one question, one field](#1-overview--one-question-one-field)
2. [Agent identity and registration](#2-agent-identity-and-registration)
3. [The one question](#3-the-one-question)
4. [Stages](#4-stages)
5. [Create flow](#5-create-flow)
6. [Approval flow](#6-approval-flow)
7. [What this workflow does not have](#7-what-this-workflow-does-not-have)
8. [Schema](#8-schema)
9. [WorkflowConfig](#9-workflowconfig)
10. [Payloads](#10-payloads)
11. [RBAC](#11-rbac)
12. [Lease eligibility](#12-lease-eligibility)
13. [File inventory](#13-file-inventory)
14. [Validation and errors](#14-validation-and-errors)
15. [Tests and evals](#15-tests-and-evals)
16. [Known gaps](#16-known-gaps)

---

## 1. Overview — one question, one field

A Fitout Progress SR records **how far along a lease's fit-out is, as a percentage**.
The platform form has nine fields; eight are read-only lease reference data the
backend already knows, and `comment` is optional. So the chatbot asks exactly one
question, and offers the answer as tappable chips:

```
"record fitout progress for t0512099"
  → "Fit-out started 15 Jan 2026 and is due 30 Jun 2027, so about 48% of the
     window has elapsed. The lease currently records 38%. How complete is the
     fit-out today? Tap a percentage or type your own (0-100)."
     [48% · suggested] [25%] [50%] [100% · complete]
  → tap, or type
  → one-tap review card
  → recorded; the lease is updated in the platform's create transaction
```

The suggested percentage is not a guess: it is the same arithmetic the platform
scheduler (`ComputeFitoutProgressDefault`) and the web form
(`computeFitoutProgressDefault`) use to pre-fill the field, mirrored in Python and
checked against the web form's own function over 2,088 date combinations.

The workflow shares the pipeline the fitout confirmation agents use — extraction →
merge → lease lookup → validation → confirmation → payload → submission — and
contributes four nodes of its own.

---

## 2. Agent identity and registration

| Thing | Value |
| --- | --- |
| `active_agent` | `rdd_fitout_progress_agent` |
| Display name | `RDD Fitout Progress Agent` |
| `service_category` / `sub_category` | `FIT_OUT_AND_HANDOVER` / `FITOUT_PROGRESS` |
| `schema_key` | `fitout_progress_schema` |
| Intents | `CREATE_FITOUT_PROGRESS`, `APPROVE_FITOUT_PROGRESS` |
| Lease lookup purpose | `fitout_progress` |
| SR title | `Fitout Progress - <pms_tenant_code>` (auto-generated) |
| Platform engine | V2 (`workflow.version = 2`), the only `FIT_OUT_AND_HANDOVER` sub-category on V2 |

Registered in: `registry.py`, `registries/workflow_config.py`,
`schemas/supervisor_schema.py`, `prompts/supervisor_prompt.py`,
`schemas/help_agent_schema.py` (`ROLE_PERMITTED_INTENTS`), `graph/help_agent_graph.py`
(`_AGENT_ENTRY_NODES`, nodes, edges, routers), and `observability/analytics_constants.py`
(`CREATE_SR_INTENTS`, `FLOW_FITOUT_PROGRESS`).

---

## 3. The one question

**File:** `app/agents/services/fitout_progress_suggestions_service.py`

### 3.1 Suggestion arithmetic

```
elapsed % = round(100 × (now − start) / (end − start)), clamped to [0, 100]
            0   when either date is missing/invalid, when end <= start, or when now <= start
            100 once now >= end
```

- `start` = `fitout_start_date` (an SR payload) → `actual_fitout_start_date` →
  `planned_fitout_start_date` → `base_fitout_start_date`
- `end` = `fitout_end_date` → `base_fitout_end_date` → `planned_fitout_end_date`
- `0001-01-01…` counts as unset — the lease sync writes Go's zero value, not `NULL`
- "now" is Asia/Riyadh wall clock
- Rounding is half-away-from-zero, matching Go's `math.Round` and JS's `Math.round`;
  Python's built-in `round` is banker's rounding and would disagree on exact halves

### 3.2 Chips

```
lead   = max(elapsed, recorded)        # progress does not go backwards
ladder = nearest quarter below lead, nearest quarter above lead, then 100
chips  = dedupe([lead] + ladder), clamped to [0, 100], first 4 kept
```

No usable date pair and no recorded value → the plain ladder `[25] [50] [75] [100]`.

| Situation | Chips |
| --- | --- |
| elapsed 82 | `82% · suggested`, `75%`, `100% · complete` |
| elapsed 82, lease records 90 | `90% · recorded`, `75%`, `100% · complete` |
| elapsed 50 | `50% · suggested`, `25%`, `75%`, `100% · complete` |
| elapsed 0 | `0% · not started`, `25%`, `100% · complete` |
| elapsed ≥ 100 | `100% · complete`, `75%` |
| dates unusable | `25%`, `50%`, `75%`, `100%` |

Percentage chips are **value-only** (`kind: labeled_options`, no `action`): the number
arrives as the user's own message, so the transcript reads as their answer and the
frontend needs no new action code. On the approve turn the decision chips
`Approve <stored>%` and `Reject` do carry actions — a decision must not go through
extraction.

### 3.3 Parsing a typed answer

`parse_percentage_answer` resolves the field in the entry node without an LLM call
whenever the percentage is the open question.

| Typed | Result |
| --- | --- |
| `82`, `82%`, `82 percent`, `make it 90`, `~82.5` | accepted |
| `complete` / `done` / `half` / `not started` | 100 / 100 / 50 / 0 |
| `120`, `-5` | refused → blocking validation error, question re-asked |
| `check SR 8517`, `between 60 and 70`, `t0512099`, `2026-07-15` | not a percentage → LLM extraction |

A second number, or words after the number, disqualifies the message — which is what
stops an SR id or a lease code being read as a reading.

---

## 4. Stages

| Stage | Who acts | What happens |
| --- | --- | --- |
| `CREATE_SR` | DD_ENGINEER | Collects the percentage, confirms, POSTs |
| `APPROVE_SR` | DD_ENGINEER | Approves (optionally with a corrected percentage) or rejects, PATCHes |

Terminal: `SR_CREATED` (raised, awaiting approval), `SR_APPROVED`, `SR_REJECTED`.

**There is no `CANCELLED`.** The platform workflow has no cancel action for this
sub-category, so no such stage can be reached — see [§7](#7-what-this-workflow-does-not-have).

Stage is pinned from the platform, never from chat state alone:
`sr_status_sync_node` maps `IN_PROGRESS` → `APPROVE_SR`, `APPROVED` → `SR_APPROVED`,
`REJECTED` → `SR_REJECTED`.

---

## 5. Create flow

```
Turn 1  "record fitout progress for t0512099"
        supervisor → CREATE_FITOUT_PROGRESS → registry → rdd_fitout_progress_agent
        entry: stage-pin CREATE_SR
        extraction: lease_code
        lease_lookup (purpose=fitout_progress): lease_id, tenant_profile_id,
                     property_id, company/brand/mall, the fit-out window,
                     and the recorded percentage
        → the one question, verbatim, with chips

Turn 2  "82"  (typed, or a chip tap — the same message either way)
        entry: resolved into corrected_fields, no extraction call
        → review card

Turn 3  Confirm
        payload_builder → POST /service-requests
        → "Recorded. SR 8517 … Because you're the approver, it was approved on
           creation and the lease now reads 82%."
        workflow_stage = SR_APPROVED
```

If the first message carries both the lease and the percentage
("fitout for t0512099 is 82% done"), the question is skipped and turn 1 returns the card.

### Why the reply says "recorded", not "submitted"

`AutoApproveFitoutProgressForApproverCreator` approves level 1 inside the platform's
create transaction whenever the creator holds the level's role, and
`AfterFitoutProgressApproved` writes `leases.fitout_progress` in the same transaction.
Because create is DD-only here, that is the normal path. The submission node reads the
platform's status when the create response carries one and falls back to the caller's
role when it does not, so both outcomes are reported honestly:

| Outcome | Stage | Message |
| --- | --- | --- |
| Auto-approved | `SR_APPROVED` | "Recorded … approved on creation and the lease now reads 82%." |
| Pending | `SR_CREATED` | "… with the RDD Project Manager for approval — the lease is updated once they approve." |

### Why there is a review card at all

The write is irreversible: no cancel, no rewind, and the lease is updated on creation.
The card is a tap, not a second question, and it is the only place an 8-instead-of-80
can be caught.

---

## 6. Approval flow

Reachable when the SR was raised by the weekly scheduler or by a non-DD admin — the
SR is `IN_PROCESS` with level 1 `IN_PROGRESS`.

```
Turn 1  approver opens SR 8517
        sr_status_sync: sub_category FITOUT_PROGRESS, stage APPROVE_SR,
                        stored_payload kept verbatim, agent named explicitly
        → "SR 8517 records 65%. Fit-out ran 15 Jul 2026 → 15 Oct 2026, so about
           81% of the window has elapsed. Approve 65%, or give me the right
           percentage."
          [Approve 65%] [81% · suggested] [75%] [Reject]

Turn 2a tap "Approve 65%"     → PATCH status APPROVED, percentage unchanged
Turn 2b type "90"             → review card → Confirm → PATCH APPROVED at 90%
Turn 2c tap "Reject"          → rejection card → Confirm → PATCH REJECTED
```

- **No separate update-only action.** The platform's `UPDATE` (stay on level) is hidden
  from the web menu because `APPROVE` already persists the edited payload; the agent
  hides it for the same reason. A corrected percentage is approved in the same step —
  what the form's *Update and Approve* button does.
- **The stored percentage is context, not an answer.** `_progress_confirmed` tracks
  whether *this* user answered; without it an approver could approve a number nobody
  in the conversation ever saw.
- **The PATCH echoes the stored payload** with only `fitout_progress_percentage` and
  `comment` changed — `ValidateEditableFields` compares key by key, and anything else
  differing is `field "<key>" is not editable at level 1`.
- **Rejection is terminal and changes nothing on the lease** (the write-back hook is
  registered for `APPROVED` only). It gets its own read-only card, and the reason is
  never a second question — `REJECT` has no `require_fields`. A reason volunteered in
  the same breath travels as the actor comment; otherwise `Rejected via assistant` is sent.
- Backing out of a rejection card disarms it, so the next confirm cannot reject a
  request the user returned to approve.

---

## 7. What this workflow does not have

Level 1's `allowed_actions` is `UPDATE / APPROVE / REJECT`. Everything below is refused
locally, in the entry node, rather than earning a 400 the workflow JSON already predicted.

| User says | Agent |
| --- | --- |
| "cancel" before submitting | clears the draft — local only, no API call |
| "cancel" after submitting | "SR 8517 can't be cancelled — a fit-out progress record goes straight onto the lease." No PATCH is attempted. |
| "reassign this" | "This request has a single approver step, so it can't be reassigned." |
| "attach the site photos" | "Fit-out progress records don't carry attachments." |
| "send it back and ask them" | "There's no action-required step on this request." (Deliberately *not* read as a rejection.) |
| "just save it without approving" | "Saving and approving are the same step here." |

---

## 8. Schema

**File:** `app/agents/schemas/fitout_progress_schema.py`

```python
PROGRESS_FIELD = "fitout_progress_percentage"   # platform wants a string: "82"
COMMENT_FIELD  = "comment"                      # singular — the confirmations use "comments"

CREATE_SR_STAGE.required_fields  = (lease_brand_mall, lease_code, lease_id, PROGRESS_FIELD)
APPROVE_SR_STAGE.required_fields = (PROGRESS_FIELD,)

TERMINAL_STAGES = {SR_CREATED, SR_APPROVED, SR_REJECTED}
OPTIONAL_FIELDS = {comment}
APPROVE_ROLES   = {DD_ENGINEER}
PERMISSION_MAP  = {DD_ENGINEER: (CREATE_SR, APPROVE_SR)}

PROGRESS_CONFIRMED_KEY = "_progress_confirmed"
STAGES_REQUIRING_EXPLICIT_PROGRESS = {APPROVE_SR}
```

`get_missing_fields(stage, collected)` reports the percentage missing when it is absent,
and **also whenever `_progress_confirmed` is unset** — on both stages.

Two things put a percentage in the draft that nobody answered:

- on `APPROVE_SR`, the stored payload the SR was raised with;
- on `CREATE_SR`, the **web form**, which computes the same elapsed-time default this
  agent suggests and writes it into the field as soon as a lease is picked
  (`computeFitoutProgressDefault` in `ServiceReqForms.tsx`); the form→chat bridge then
  syncs it back through `corrected_fields`.

`merge_state_node` therefore keys the flag on **the turn, not the channel**: it is set
when the user typed something the extractor read, or took an action (a chip tap arrives
as their message, a card edit as an `action_override`). A background form sync — empty
message, no action — leaves the value in the draft so the form and the chat agree, and
leaves the question owed.

> Reported live on `DUMMY-CUST-001`: the form's default reached the chat as if it were an
> answer, and the flow went from the question straight to "Recording fit-out progress at
> 100%" without the user typing anything.

`FitoutProgressExtractedFields` drops anything outside `ALLOWED_EXTRACTED_FIELDS` or
inside `BACKEND_ONLY_FIELDS`, so a hallucinated lease id or fit-out date cannot reach
`collected_data`.

---

## 9. WorkflowConfig

```python
WorkflowConfig(
    agent_name="rdd_fitout_progress_agent",
    extraction_prompt=FITOUT_PROGRESS_EXTRACTION_SYSTEM_PROMPT,
    field_questions=FITOUT_PROGRESS_FIELD_QUESTIONS,
    stage_sync_nodes={"APPROVE_SR": "fitout_progress_entry"},
    collection_stages=frozenset({"CREATE_SR", "APPROVE_SR"}),
    confirmation_nodes={
        "CREATE_SR": "fitout_progress_confirmation",
        "APPROVE_SR": "fitout_progress_confirmation",
    },
    terminal_stages=frozenset({"SR_CREATED", "SR_APPROVED", "SR_REJECTED"}),
    backend_fields=BACKEND_DERIVED_FIELDS,
    auto_generated_fields=frozenset({"title"}),
    lease_trigger_fields=("lease_code", "company", "lease_brand_mall"),
    action_intents=frozenset({"CREATE_FITOUT_PROGRESS", "APPROVE_FITOUT_PROGRESS"}),
)
```

`guided_tour_gate` is left at its default, like every other workflow, so the guided/open
choice is offered here too — once per draft, before the percentage question.

It was off at first, on the reasoning that a "shall I walk you through it?" preamble in
front of a single question is noise. That reasoning missed what the choice actually
governs: the form pre-fills the percentage with its own computed default, so *"leave the
form open and you can type straight into it"* is a real option here, and the answer is what
decides whether that field is locked while the assistant asks. The cost is one extra turn
before the question.

> The test suite disables `guided_tour_choice_enabled` globally
> (`tests/conftest.py`), so any test covering the choice has to turn it back on —
> otherwise a workflow that silently opted out would still look correct.

---

## 10. Payloads

### Create — `POST /service-requests`

```json
{
  "payload": {
    "lease_brand_mall": "t0105712 - Brand Under Armour - Jawharat Jeddah",
    "lease_code": "t0105712",
    "company_name": "Under Armour Trading Co.",
    "brand_name": "Brand Under Armour",
    "mall_name": "Jawharat Jeddah",
    "fitout_start_date": "2026-07-15",
    "fitout_end_date": "2026-10-15",
    "fitout_progress_percentage": "82",
    "comment": "",
    "contract_id": 95404
  },
  "title": "Fitout Progress - t0105712",
  "tenant_profile_id": 116,
  "property_id": 3041,
  "service_category": "FIT_OUT_AND_HANDOVER",
  "sub_category": "FITOUT_PROGRESS",
  "lease_code": "t0105712",
  "lease_id": 95404,
  "service_request_id": ""
}
```

- The percentage is a **string** (`extractFitoutProgressPercentage` accepts a JSON string
  or number; the form sends a string).
- `lease_brand_mall` is truncated at `--`, dropping the Arabic half, as the scheduler does.
- A zero/absent `tenant_profile_id` is refused locally — the platform answers
  *should provide a valid tenant profile id* — and becomes a question about the lease.
- No `status` is sent: V2 `Create` forces `CREATED`.

### Approve / reject — `PATCH /service-requests/{sr_id}`

```json
{
  "payload": { "...stored payload, with only fitout_progress_percentage and comment changed..." },
  "status": "APPROVED",
  "comment": "Verified on site.",
  "title": "Fitout Progress - t0105712",
  "sub_category": "FITOUT_PROGRESS",
  "service_request_id": "8517"
}
```

`"status": "REJECTED"` for the reject branch.

---

## 11. RBAC

| Role | Create | Approve |
| --- | --- | --- |
| `DD_ENGINEER` | yes | yes |
| `ADMIN` | yes (superuser) | yes |
| Every other admin role | **no** | no |
| Tenant | no | no |

Create is DD-only on purpose: the platform would accept a create from another admin role,
but the web form hides the sub-category from everyone without `DD_ENGINEER`
(`filterSubCategoryList`), and the assistant must not offer a path the form refuses.

Enforced in three places: `ROLE_PERMITTED_INTENTS` (supervisor), the entry node's
create guard (at the workflow boundary, before any lease lookup), and `role_can_approve`
in the submission node (before any PATCH). Lease/mall scope is the platform's — `GET /leases`
is pre-filtered by the caller's properties, and the platform re-checks `authorizeScopeAccess`.

---

## 12. Lease eligibility

The SR only means anything for a lease whose fit-out **has started and has not ended**.
`GET /leases?purpose=fitout_progress` applies exactly that, added on the platform side:

```go
LeaseLookupPurposeFitoutProgress: {
    RequiresSet:   "actual_fitout_start_date",
    RequiresUnset: "actual_fitout_end_date",
    SentinelAware: true,
}
```

`SentinelAware` compares against the lease sync's Go zero value (`'0001-01-01'`) instead
of trusting `NULL` alone. Only this purpose opts in: 12,377 of 14,204 leases carry a
sentinel `actual_shop_handover_date`, so switching the confirmation purposes over would
quietly shrink pickers that have already shipped (tracked separately).

Measured against a local copy of the platform DB: **56 leases** qualify, 46 of them with a
usable window — so the "suggested" lead chip appears about four times in five, and the rest
fall back to the ladder.

The scheduler's "nothing created for this lease in the last 6 days" guard is deliberately
**not** replicated: that is batch de-duplication, not eligibility, and a PM correcting a
number on Tuesday should not be told the lease does not exist.

**Platform files:** `domain/handover_lease_lookup.go`,
`lease/delivery/http/lease_lookup_handler.go`,
`lease/repository/postgres/handover_lease_lookup_repository.go` (in `cenomi-api-2`).

---

## 13. File inventory

### Own nodes — `app/agents/graph/nodes/fitout_progress/`

| File | Purpose |
| --- | --- |
| `fitout_progress_entry_node.py` | Role guard, percentage resolution without the LLM, confirm/reject parsing, cancel, local refusals, stage pin, stale-clear |
| `fitout_progress_confirmation_node.py` | Review card; rejection card (read-only) |
| `fitout_progress_payload_builder_node.py` | POST body; PATCH body echoing the stored payload |
| `fitout_progress_api_submission_node.py` | Guards, POST/PATCH, approver guard, honest terminal wording |

### Own schema / services / prompts

| File | Purpose |
| --- | --- |
| `schemas/fitout_progress_schema.py` | Stages, field sets, permissions, extraction model |
| `services/fitout_progress_suggestions_service.py` | Date resolution, the platform's arithmetic, chips, parsing, the question text |
| `prompts/fitout_progress_extraction_prompt.py` | Extraction prompt + fallback field questions |

### Shared nodes touched

| File | Change |
| --- | --- |
| `shared/missing_field_node.py` | The verbatim question, the chips (initial ask and correction re-ask), the missing-field dispatch, and an exemption from the handover stage-normalisation that was rewriting `APPROVE_SR` to `CREATE_SR` |
| `shared/merge_state_node.py` | Sets `_progress_confirmed` when this user answers |
| `shared/validation_node.py` | Required fields + the percentage rule; `percentage_format` clears the rejected value |
| `shared/sr_status_sync_node.py` | Stage mapping (incl. `SR_REJECTED`), sub-category inference, `stored_payload`, agent naming, opening message |
| `shared/lease_lookup_node.py` | `purpose=fitout_progress`; `fitout_progress` in the enrichment fields |
| `services/lease_lookup_service.py` | `LeaseRecord.fitout_progress`; the `t0512099` mock lease |
| `services/resource_picker_decorations.py` | Picker purpose |
| `services/review_decorations_service.py` | `fitout_progress_percentage` / `comment` survive payload hydration |
| `services/validation_service.py` | `validate_fitout_progress` |

---

## 14. Validation and errors

| Rule | Severity |
| --- | --- |
| numeric, `0 ≤ x ≤ 100` | **blocking** — re-asked; the rejected value is cleared from the draft |
| below the percentage already recorded on the lease | non-blocking warning — a typo usually, a correction sometimes, and only the user knows |
| `lease_id` / `tenant_profile_id` unresolved | **blocking** — the lease is re-asked |
| role may not approve | refused before any PATCH |

| Situation | Handling |
| --- | --- |
| POST/PATCH 4xx/5xx | stage `FAILED`, "nothing was recorded", never auto-retried (a repeated POST is a second SR and a second lease write) |
| PATCH 403 on a closed SR | re-sync, and say it already closed |
| PATCH 400 `field "<key>" is not editable` | a payload-echo bug — logged loudly |
| Stored payload missing on approve | "I couldn't read this request's details back from the platform, so I haven't sent your decision." |

---

## 15. Tests and evals

| File | Coverage |
| --- | --- |
| `tests/unit/test_fitout_progress_schema.py` | Stages, missing fields, field sets, role helpers, extraction model |
| `tests/unit/test_fitout_progress_suggestions.py` | The arithmetic edge for edge, the chip ladder, the question, the approve row |
| `tests/unit/test_fitout_progress_parsing.py` | Accept/refuse boundary for typed answers |
| `tests/unit/test_fitout_progress_nodes.py` | Entry (role, percentage, confirm, cancel, refusals, stage pin), cards, payloads, submission |
| `tests/unit/test_fitout_progress_wiring.py` | Registry, WorkflowConfig, intents, graph routing, lease purpose, the question, status sync |
| `tests/integration/test_fitout_progress_graph.py` | The compiled graph: create in three turns, one-shot, out-of-range, approve, update-and-approve, reject, closed SR |

```bash
cd backend
.venv/bin/python -m pytest tests/unit/test_fitout_progress_*.py \
    tests/integration/test_fitout_progress_graph.py -q
```

**Evals — Tier 6k** (`tests/eval/scenarios.py`, S84–S88): happy path, one-shot,
out-of-range re-ask, RBAC denial, unsupported actions. Lease `t0512099` is the
fit-out-in-progress fixture.

```bash
# The runner lives beside backend/, not inside it, and defaults to
# BASE_URL=http://localhost:8000 — the port the Go platform API uses locally,
# so point it at the chatbot backend explicitly when that is where it runs.
cd service-request-chatbot
./run_agent_eval.sh --no-start --base-url http://localhost:8100 --tier 6k
```

Needs the chatbot backend already running (`--no-start`), the seeded eval users
(`sara@cenomi.com` for the DD scenarios, `aisha@cenomi.com` for the RBAC one) and a live
LLM key — the supervisor call is what these scenarios exercise.

---

## 16. Known gaps

1. **The approve flow has no eval scenario, and cannot have one yet.** A DD create is
   auto-approved, and a non-DD create is refused by the agent, so neither the flat nor the
   lifecycle harness can produce an SR that is waiting for approval. Seed one with
   `POST /backend_api/test/fitout-progress` and open it by hand; the flow itself is covered
   deterministically by the graph tests.
2. **Nothing has run against the live platform.** Every test stubs the supervisor LLM,
   field extraction, the lease API and the SR API.
3. **The weekly platform scheduler is disabled** (`app/main.go`, PR 6655), so in practice
   the create path is the only one users will reach until it is re-enabled.
4. **`leases.fitout_progress` is still write-only in the platform API** — no dashboard reads
   it back. The agent shows it as context because it reads the lease row through
   `GET /leases`.
5. **The live e2e spec picks Tier 6k up only after `npm run generate-scenarios`** —
   `frontend/tests/e2e/live/scenarios.json` is generated, and still holds the older 1–38 set.
6. **The suggestion follows the platform's arithmetic, which is elapsed time alone.** A
   lease whose window has fully elapsed is suggested at 100% even when it records far
   less — `DUMMY-CUST-001` (21 Aug → 21 Sep, recording 38%) leads with 100%. The form
   pre-fills the same number, so agent and form agree, but whether a fully-elapsed window
   should really suggest 100% is a product question, not a bug in the mirror.
