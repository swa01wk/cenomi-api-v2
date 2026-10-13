# Fitout Progress Agent — PRD

> **Status:** Proposal — nothing implemented yet. Sections marked **DECISION** are the
> choices worth arguing about before code starts.
> **Scope:** `FIT_OUT_AND_HANDOVER` / `FITOUT_PROGRESS`, create **and** approval, in the
> service-request chatbot.
> **Written against:** the platform reference `fitout-progress-service-request.md`
> (behaviour), `docs/fitout-confirm-agent.md` (the pattern this agent copies), and the
> live code in `service-request-chatbot/backend` + `cenomi-api-2`
> (branch `feature/fitout-progress-sr-v2`).

---

## 1. What we are building, in one paragraph

A **Fitout Progress** agent that lets an RDD Project Manager (`DD_ENGINEER`) record how far
along a lease's fit-out is — as a percentage — by answering **exactly one question**, with
suggested percentages offered as tappable chips (the same suggestion the web form pre-fills)
and free typing always allowed. The same agent handles the **approval** side: when a
Fitout Progress SR is already open (scheduler-created or raised by a non-DD admin), the
approver is asked the same single question and approves — or rejects — in one tap.

It is the fourth `FIT_OUT_AND_HANDOVER` sub-category on the chatbot after FOS / FOE /
Trading, and it reuses their pipeline wholesale: shared extraction → merge → lease lookup →
validation → confirmation → payload builder → submission.

---

## 2. Why one question

The web form has nine fields; eight of them are read-only lease reference data the platform
already knows, and one — `fitout_progress_percentage` — is the only thing a human supplies
(`comment` is optional and nobody fills it). The chatbot's advantage over the form is that
it can resolve the lease and compute the suggested percentage itself, so the entire
interaction collapses to:

```
"record fitout progress for t0105712"
   → "Fit-out ran 15 Jul → 15 Oct, so ~82% of the window has elapsed.
      How complete is it today?"        [82% · suggested] [75%] [100% · complete]
   → tap or type
   → one-tap confirm card
   → recorded, approved, lease updated
```

Two facts make the single question safe:

1. The suggested percentage is not a guess — it is the **same arithmetic the platform and the
   web form already use** (`ComputeFitoutProgressDefault` in Go,
   `computeFitoutProgressDefault` in `fitoutProgressDefaults.ts`). The chatbot mirrors it, so
   the number the assistant offers is the number the form would have pre-filled.
2. Everything else on the form is backend-derived and is already filled by the shared
   `lease_lookup_node`.

---

## 3. Platform facts this design is pinned to

Read out of the platform reference; each one changes the design.

| Fact | Consequence for the agent |
| --- | --- |
| A create by a user **holding `DD_ENGINEER`** is auto-approved inside the create transaction (`AutoApproveFitoutProgressForApproverCreator`), SR comes back `APPROVED`, and `leases.fitout_progress` is written immediately | The create flow is a **recording, not a request**. The wording must say "recorded", not "submitted for approval". The write is irreversible — see DECISION 1. |
| A create by any **other** role bootstraps L1 `IN_PROGRESS` for `DD_ENGINEER`, SR `IN_PROCESS` | The approval flow exists and is reachable, but only from a non-DD create or the scheduler. |
| The **weekly scheduler is commented out** in `app/main.go` (PR 6655), manual trigger `POST /test/fitout-progress` | Today there is **no organic source of approvable SRs**. See DECISION 4 and §13. |
| `allowed_actions` at L1 = `UPDATE / APPROVE / REJECT`; no `REASSIGN_TO`, no `ACTION_REQUIRED`, no `REQUESTED_RE_UPLOAD`, `IN_PROCESS` rejected | The agent must not offer reassign, "ask for more info", or a plain save. Any of them is a 400. |
| `CANCELLED` is not an action on this workflow | **There is no cancel.** The shared cancel flow must be suppressed, not inherited. |
| No `file` field, no document type, no `sr_attachments` | No upload panel, no document questions, no `document_count` validation. |
| `action_rules.UPDATE.editable_fields = ["fitout_progress_percentage", "comment"]` | On approve/update the agent must echo every other payload key back **byte-identical** or get `field "<key>" is not editable at level 1`. |
| `action_rules.APPROVE.require_fields = ["fitout_progress_percentage"]` | The percentage is mandatory to approve; not required to reject. |
| Create validation: percentage, **when present**, must parse as a number in `[0, 100]` | Validate locally before POST so the user sees a question, not a 400. |
| `tenant_profile_id` must be non-zero on create | Comes from lease lookup; hard-fail locally if absent rather than sending `0`. |
| Approve writes `leases.fitout_progress` + a `lease_planned_dates_tracking` row in the approval transaction | The confirmation message may state the lease was updated. |
| SLA 48h, first reminder 47h, no repeats | Nothing to build; an auto-approved SR closes before it fires. |
| Notifications: nobody is notified on an auto-approved create, on approve, or on reject | The agent's own message is the only feedback. Do not promise "the RDD PM has been notified" on a DD create. |

---

## 4. Scope

**In scope**

- New agent `rdd_fitout_progress_agent` for `FIT_OUT_AND_HANDOVER` / `FITOUT_PROGRESS`.
- Create flow (`CREATE_SR`) — one question, chips, confirm card, POST.
- Approval flow (`APPROVE_SR`) — one question, chips, Approve / Reject, PATCH.
- Percentage suggestion service mirroring the platform arithmetic.
- Lease eligibility for the picker (**requires a platform change**, §13).
- Supervisor intents, RBAC, eval scenarios, unit tests.

**Out of scope**

- Re-enabling the weekly scheduler (platform, one-line uncomment — DECISION 4).
- Reading `leases.fitout_progress` back anywhere else (no dashboard consumes it yet).
- Attachments, reassign, action-required, cancel — none exist on this workflow.
- Tenant-facing anything. The sub-category is never tenant-visible.
- Frontend work. `labeled_options` chips and `text_question` already render; no
  `cenomi-frontend-2` change is expected (§16).

---

## 5. Agent identity and registration

| Thing | Value |
| --- | --- |
| `active_agent` | `rdd_fitout_progress_agent` |
| Display name | `RDD Fitout Progress Agent` |
| `service_category` / `sub_category` | `FIT_OUT_AND_HANDOVER` / `FITOUT_PROGRESS` |
| `schema_key` | `fitout_progress_schema` |
| Create intent | `CREATE_FITOUT_PROGRESS` |
| Approve intent | `APPROVE_FITOUT_PROGRESS` |
| Lease lookup purpose | `fitout_progress` (new, §13) |
| SR title | `Fitout Progress - <pms_tenant_code>` (auto-generated, never asked) |
| Stages | `CREATE_SR`, `APPROVE_SR` → terminal `SR_CREATED`, `SR_APPROVED`, `SR_REJECTED` |
| `guided_tour_gate` | ~~`False`~~ → **left on** (revised 2026-09-27). The preamble looked
  like noise in front of a single question, but the choice governs whether the form field —
  which the form pre-fills with its own computed default — is locked while the assistant
  asks, and that is a real decision here. |

Utterances the supervisor must classify as `CREATE_FITOUT_PROGRESS`: "record fitout
progress", "update fit-out progress for t0105712", "fitout is 80% done", "log fitout
percentage", "how far along is fitout — set it to 60".
`APPROVE_FITOUT_PROGRESS`: "approve the fitout progress SR", "approve 8517", plus any turn
that arrives with an `sr_id` whose sub-category syncs to `FITOUT_PROGRESS`.

---

## 6. Flow A — Create (the happy path)

### 6.1 Turn by turn

```
Turn 1  — user: "record fitout progress for t0105712"
          supervisor → CREATE_FITOUT_PROGRESS
          registry   → rdd_fitout_progress_agent
          entry      → stage-pin CREATE_SR
          extraction → lease_code = "t0105712"
          lease_lookup (purpose=fitout_progress) → lease_id, tenant_profile_id,
                       property_id, company/brand/mall, actual_fitout_start_date,
                       base_fitout_end_date, recorded fitout_progress
          validation → missing: fitout_progress_percentage
   reply: "Under Armour — Jawharat Jeddah (t0105712). Fit-out started 15 Jul 2026 and is
           due 15 Oct 2026, so about 82% of the window has elapsed.
           How complete is the fit-out today?"
   chips: [82% · suggested] [75%] [100% · complete]

Turn 2  — user taps "82% · suggested"  (chat message: "82")
          entry      → active_field == fitout_progress_percentage and the message is a
                       bare percentage → write it straight into collected_data, skip the LLM
          validation → complete
   reply: confirmation card — Lease, Fit-out window, Progress 82%
   chips: [Confirm] [Change the percentage]

Turn 3  — user taps Confirm  (action_override = confirm_create_sr)
          payload_builder → POST /service-requests
          api_submission  → 201 { service_request_id: "8517" }
                          → GET /service-requests/8517 → status APPROVED
   reply: "Recorded. SR 8517 — fit-out progress for t0105712 set to 82%.
           Because you are the approver, it was approved on creation and the lease
           now reads 82%."
          workflow_stage = SR_APPROVED
```

If the user's first message carries the percentage too ("fitout for t0105712 is 82% done"),
the question is skipped entirely and turn 1 returns the confirmation card. That is the
target shape for a returning user.

### 6.2 If the user has no lease in mind

Lease resolution is the shared step every agent has, not a second question of ours: with no
identifier, `lease_lookup_node` returns the existing `resource_picker` chip list — the five
most recently qualifying leases plus search — carrying `purpose=fitout_progress` so only
leases with a started, unfinished fit-out are listed.

### 6.3 What the create flow never does

- Never asks for `comment` (captured only if volunteered in the same sentence).
- Never asks for dates, brand, mall, company, or title.
- Never offers an upload panel.
- Never claims anybody was notified.

---

## 7. Flow B — Approval

Reachable when the SR was created by the scheduler or by a non-DD admin, i.e. SR is
`IN_PROCESS` with L1 `DD_ENGINEER IN_PROGRESS`.

```
Turn 1  — approver opens SR 8517 from the portal (sr_id on the request, empty message)
          sr_status_sync → sub_category FITOUT_PROGRESS, L1 IN_PROGRESS → APPROVE_SR
          entry          → stage-pin APPROVE_SR, hydrate collected_data from the stored payload
   reply: "SR 8517 — Fitout Progress for Under Armour — Jawharat Jeddah (t0105712).
           It currently records 65%. Fit-out ran 15 Jul → 15 Oct, so ~82% of the window
           has elapsed. Approve 65%, or give me the right percentage."
   chips: [Approve 65%] [82% · suggested] [100% · complete] [Reject]

Turn 2a — user taps "82% · suggested" (message "82") or types "it's more like 90"
          → percentage replaces the stored one; confirm card; Confirm → PATCH
            { status: APPROVED, payload: {...unchanged..., fitout_progress_percentage: "82"} }
   reply: "SR 8517 approved at 82%. The lease now reads 82%."   → SR_APPROVED

Turn 2b — user taps "Approve 65%"  (action = confirm_approve_fitout_progress)
          → PATCH with the stored percentage unchanged        → SR_APPROVED

Turn 2c — user taps "Reject", or types "reject — these numbers are wrong"
          → confirm card ("Reject SR 8517?"), Confirm → PATCH { status: REJECTED,
            comment: <reason if the user gave one, else "Rejected via assistant"> }
   reply: "SR 8517 rejected. The lease was not updated."       → SR_REJECTED
```

**No separate "update only" action.** The platform's `RE_EXAMINE`/`UPDATE` (stay on level) is
hidden from the web menu for exactly this reason — `APPROVE` already persists the edited
payload — and the agent hides it too. A corrected percentage is approved in the same turn,
which is what the web form's *Update and Approve* button does.

**Rejection reason is never a second question.** It is captured if volunteered; otherwise a
fixed comment is sent. `REJECT` has no `require_fields`.

---

## 8. The one question, and its chips

### 8.1 Suggestion arithmetic (mirrors the platform exactly)

New module `app/agents/services/fitout_progress_suggestions_service.py`.

```
elapsed(start, end, today) =
    round(100 * (today - start) / (end - start)), clamped to [0, 100]
    0   when either date is missing/invalid, when end <= start, or when today <= start
    100 when today >= end
```

- `start` = first valid of `actual_fitout_start_date` → `planned_fitout_start_date` →
  `base_fitout_start_date`
- `end`   = first valid of `base_fitout_end_date` → `planned_fitout_end_date`
- `0001-01-01…` counts as unset (the lease sync writes Go zero values, not `NULL`)
- "today" is **Asia/Riyadh wall clock**, as in `inspection_suggestions_service`

Eligibility guarantees `actual_fitout_start_date` is set, so in practice `start` is always
the actual start.

### 8.2 Chip set

```
lead   = max(elapsed, recorded)        # progress does not go backwards
ladder = the nearest quarter below lead, the nearest quarter above lead, then 100
chips  = dedupe([lead] + ladder), clamped to [0,100], first 4 kept
```

If neither a usable date pair nor a recorded value exists, there is no lead and the chips
are the plain ladder `[25] [50] [75] [100]`.

| Situation | Chips (label · value) |
| --- | --- |
| elapsed 82, nothing recorded | `82% · suggested`→`82`, `75%`→`75`, `100% · complete`→`100` |
| elapsed 82, recorded 90 | `90% · recorded`→`90`, `75%`→`75`, `100% · complete`→`100` |
| elapsed 50 | `50% · suggested`→`50`, `25%`→`25`, `75%`→`75`, `100% · complete`→`100` |
| elapsed 0 (fit-out starts tomorrow) | `0% · not started`→`0`, `25%`→`25`, `100% · complete`→`100` |
| elapsed 100 (past the due date) | `100% · complete`→`100`, `75%`→`75` |
| dates unusable | `25%`→`25`, `50%`→`50`, `75%`→`75`, `100%`→`100` |

Contract, following `inspection_suggestions_service`: `kind: "labeled_options"`, each option
`{label, value}` **with no `action`** — the frontend sends the value as a plain user message,
so the transcript shows the percentage as the user's own answer and no new action code has
to be taught to the frontend. The `Approve <stored>%` and `Reject` chips on the approval
turn *do* carry actions (`confirm_approve_fitout_progress`, `reject_fitout_progress`)
because they are decisions, not values.

### 8.3 Accepting typed answers

The entry node resolves the field **without the LLM** when `active_field ==
"fitout_progress_percentage"` and the message is a bare percentage — the same direct-set
shortcut the radio chips use, and the reason a chip tap costs no extraction round-trip.

| Typed | Parsed | Note |
| --- | --- | --- |
| `82`, `82%`, `82 percent`, ` 82 % ` | `82` | |
| `82.5` | `82.5` | platform column is `NUMERIC(5,2)`; round to 2 dp |
| `hundred`, `done`, `complete` | `100` | small word list, no LLM |
| `none`, `not started` | `0` | |
| `120`, `-5` | rejected | blocking validation error → re-ask with chips |
| `eighty-ish`, `most of it` | not parsed | falls through to LLM extraction, then re-ask |

Anything the shortcut does not recognise still goes through normal extraction, so a sentence
like "we're at 82, MEP finished last week" yields `fitout_progress_percentage = "82"` and
`comment = "MEP finished last week"`.

### 8.4 Validation

| Rule | Severity |
| --- | --- |
| numeric, `0 ≤ x ≤ 100` | **blocking** — re-ask (prevents a platform 400) |
| new value **below** the recorded `leases.fitout_progress` | non-blocking warning shown on the card: "this is lower than the 90% already recorded" |
| `lease_id` or `tenant_profile_id` unresolved | **blocking** — re-ask for the lease (prevents `should provide a valid tenant profile id`) |
| approver role not `DD_ENGINEER` at `APPROVE_SR` | **blocking**, refused before any PATCH |

---

## 9. Schema — `app/agents/schemas/fitout_progress_schema.py`

Mirrors `fitout_confirm_schema.py` so shared nodes need no new concepts.

```python
PROGRESS_FIELD = "fitout_progress_percentage"

REQUIRED_BY_STAGE = {
    "CREATE_SR":  ("lease_brand_mall", "lease_code", "lease_id", PROGRESS_FIELD),
    "APPROVE_SR": (PROGRESS_FIELD,),
}

OPTIONAL_FIELDS = frozenset({"comment"})

BACKEND_DERIVED_FIELDS = frozenset({
    "lease_id", "lease_code", "company_name", "brand_name", "mall_name",
    "pms_tenant_code", "tenant_profile_id", "property_id",
    "fitout_start_date",          # actual → planned → base
    "fitout_end_date",            # base → planned
    "recorded_fitout_progress",   # display + chip floor only, never sent
    "lease",
})

APPROVE_ROLES  = frozenset({"DD_ENGINEER"})
PERMISSION_MAP = {"DD_ENGINEER": ("CREATE_SR", "APPROVE_SR")}

def get_required_fields(stage_name: str) -> tuple[str, ...]: ...
def get_missing_fields(stage_name: str, collected: dict) -> list[str]: ...
def build_title(pms_tenant_code: str) -> str:   # "Fitout Progress - t0105712"
def role_can_approve(role: str) -> bool: ...
def role_can_act_on_stage(role: str, stage_name: str) -> bool: ...
```

There is no `SUBCAT_CONFIG`-style branching: one sub-category, one field, no radio.

---

## 10. WorkflowConfig

```python
WorkflowConfig(
    agent_name="rdd_fitout_progress_agent",
    extraction_prompt=FITOUT_PROGRESS_EXTRACTION_SYSTEM_PROMPT,
    field_questions=FITOUT_PROGRESS_FIELD_QUESTIONS,     # one entry
    stage_sync_nodes={"APPROVE_SR": "fitout_progress_entry"},
    collection_stages=frozenset({"CREATE_SR", "APPROVE_SR"}),
    confirmation_nodes={
        "CREATE_SR":  "fitout_progress_confirmation",
        "APPROVE_SR": "fitout_progress_confirmation",
    },
    terminal_stages=frozenset({"SR_CREATED", "SR_APPROVED", "SR_REJECTED"}),
    backend_fields=BACKEND_DERIVED_FIELDS,
    auto_generated_fields=frozenset({"title"}),
    lease_trigger_fields=("lease_code", "company", "lease_brand_mall"),
    action_intents=frozenset({"CREATE_FITOUT_PROGRESS", "APPROVE_FITOUT_PROGRESS"}),
    guided_tour_gate=False,
)
```

The extraction prompt is tiny — one numeric field and an optional comment — and must say
explicitly: **never invent a percentage**, and **never read a date as a percentage**.

---

## 11. Payloads

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

- `lease_brand_mall` truncated at `--` (drop the Arabic half), as the scheduler does.
- Percentage sent as a **string**, matching the form and `extractFitoutProgressPercentage`.
- `status` is ignored by the platform; do not send one.
- `?send_email=false` has no effect on V2 create; do not bother.
- After a 2xx, **GET the SR once** and read `status` so the reply states what actually
  happened (`APPROVED` → recorded, `IN_PROCESS` → pending with the RDD PM) instead of
  assuming the auto-approve fired. It silently does not fire when the creator holds no
  roles or the percentage is unparseable.

### Approve / Reject — `PATCH /service-requests/{sr_id}`

```json
{
  "payload": { "...stored payload, byte-identical except fitout_progress_percentage and comment..." },
  "status": "APPROVED",
  "comment": "Verified on site.",
  "title": "Fitout Progress - t0105712",
  "sub_category": "FITOUT_PROGRESS"
}
```

`"status": "REJECTED"` for the reject branch. The payload builder must start from the
payload returned by `GET /service-requests/{id}` and change **only** those two keys —
`ValidateEditableFields` compares against the stored payload key by key.

---

## 12. RBAC

| Role | Create | Approve |
| --- | --- | --- |
| `DD_ENGINEER` | yes | yes |
| Everyone else (admin) | **no** — the agent refuses, matching the web create-dropdown gate (`role.includes(DD_ENGINEER)`) | no |
| Tenant | no | no |

- `ROLE_PERMITTED_INTENTS` in `help_agent_schema.py` grants both intents to `DD_ENGINEER`
  only.
- Lease scope is the platform's: `GET /leases` is already filtered by the caller's
  `property_ids`, and the platform re-checks `authorizeScopeAccess` on create and PATCH.
- The approval path additionally calls `role_can_approve(role)` in the submission node
  before any PATCH, as the fitout confirm agents do.

**DECISION 2 — should a non-DD admin be allowed to create?** The API permits it (it produces
a reviewable SR and is the only non-scheduler way to exercise the approval flow), but the web
UI does not offer it. Recommendation: **refuse it in the chatbot too**, so the assistant and
the form agree, and use the internal trigger for approval test data.

---

## 13. Lease eligibility — platform change required

The SR is only meaningful for a lease whose fit-out **has started and has not ended**. The
scheduler expresses that as `actual_fitout_start_date IS NOT NULL AND (actual_fitout_end_date
IS NULL OR <= '0001-01-01')`.

`GET /leases` cannot express it today. In `cenomi-api-2`,
`domain/handover_lease_lookup.go` maps a purpose to exactly one "must be set" column:

```go
var milestonePrerequisiteColumns = map[string]string{
    LeaseLookupPurposeFitoutStart: "actual_shop_handover_date",
    LeaseLookupPurposeFitoutEnd:   "actual_fitout_start_date",
    LeaseLookupPurposeTrading:     "actual_fitout_end_date",
}
```

**Required platform change** (small, on `feature/fitout-progress-sr-v2`):

1. Add `LeaseLookupPurposeFitoutProgress = "fitout_progress"`.
2. Widen the map value to carry both halves of the rule, e.g.
   `{RequireNotNull: "actual_fitout_start_date", RequireNull: "actual_fitout_end_date"}`,
   and emit `AND l.actual_fitout_end_date IS NULL` alongside the existing NOT NULL clause.
   Column names stay from the fixed table, never from the request, so the interpolation
   stays safe.
3. Accept `fitout_progress` in `skipHandoverCheck` in `lease_lookup_handler.go`.
4. **Nice to have, not blocking:** add `FitoutProgress` to `HandoverLeaseLookupDTO`
   (`fitout_progress,omitempty`) so the chatbot can show "currently records 65%" and use it
   as the chip floor. Without it the agent can still read the recorded value from the SR
   payload on the approval path, but not on create.

Deliberately **not** replicated: the scheduler's "nothing created for this lease in the last
6 days" guard. That is a batch de-duplication rule, not an eligibility rule — a PM who wants
to correct a number on Tuesday should not be told the lease does not exist. Consequence: a
manual create suppresses that lease from the next Monday batch (the platform's 6-day guard
counts every SR regardless of status), which is the behaviour the platform already documents.

---

## 14. What the agent must refuse, and how

| User says | Agent |
| --- | --- |
| "cancel" **before** submitting | clears the draft, "nothing was recorded" — local only, no API call |
| "cancel" **after** submitting | "A fit-out progress record can't be cancelled — it's already on the lease. Raise a new one with the right percentage." No PATCH is attempted: `CANCELLED` is not an allowed action here and would 400. |
| "reassign this to someone else" | "This request has a single approver step and can't be reassigned." |
| "ask the tenant for more detail" | "There's no action-required step on this request." |
| "attach the site photos" | "Fit-out progress records don't carry attachments." |
| "just save it without approving" | "Saving and approving are the same step here — give me the percentage and I'll record it." |
| Anything on an already `APPROVED`/`REJECTED` SR | "SR 8517 is closed (approved at 82%). Want to record a new one?" (the platform returns 403) |

Every one of these is a local refusal — the point is to never spend a round-trip earning a
400 the workflow JSON already told us about.

---

## 15. Errors

| Situation | Handling |
| --- | --- |
| percentage non-numeric or outside `[0,100]` | caught locally, re-ask with chips |
| lease not found / not eligible | "I couldn't find a lease with a started, unfinished fit-out for that code" + picker chips |
| `tenant_profile_id` missing after lookup | blocking; re-ask for the lease. Never POST `0`. |
| POST/PATCH 4xx | surface the platform message verbatim under a one-line plain-English lead; stage → `FAILED`, retryable |
| POST/PATCH 5xx or timeout | "the platform didn't accept it — try again"; stage `FAILED`; **never** auto-retry a POST (a duplicate SR is a duplicate lease write) |
| PATCH 403 `ErrServiceRequestEditNotAllowed` | re-sync status, tell the user the SR already closed |
| PATCH 400 `field "<key>" is not editable` | log loudly as a payload-echo bug: it means the builder mutated a read-only key |
| `GET` after create fails | still report success with the SR id, and say the approval status is unconfirmed |

---

## 16. Files to touch

### Chatbot backend (`service-request-chatbot/backend`)

| File | Change |
| --- | --- |
| `app/agents/schemas/fitout_progress_schema.py` | **new** — §9 |
| `app/agents/prompts/fitout_progress_extraction_prompt.py` | **new** — one field + comment |
| `app/agents/services/fitout_progress_suggestions_service.py` | **new** — §8.1/§8.2 |
| `app/agents/graph/nodes/fitout_progress/fitout_progress_entry_node.py` | **new** — stage pin, confirm/reject parsing, numeric direct-set, cancel refusals |
| `app/agents/graph/nodes/fitout_progress/fitout_progress_confirmation_node.py` | **new** — card + PENDING |
| `app/agents/graph/nodes/fitout_progress/fitout_progress_payload_builder_node.py` | **new** — create / approve / reject payloads |
| `app/agents/graph/nodes/fitout_progress/fitout_progress_api_submission_node.py` | **new** — POST / PATCH + post-create GET + `role_can_approve` guard |
| `app/agents/registry.py` | `FITOUT_PROGRESS` entry under `FIT_OUT_AND_HANDOVER` |
| `app/agents/registries/workflow_config.py` | `_build_fitout_progress_config()` + registry entry |
| `app/agents/schemas/supervisor_schema.py` | two intents on the `Literal` |
| `app/agents/prompts/supervisor_prompt.py` | intent docs + routing block |
| `app/agents/schemas/help_agent_schema.py` | `ROLE_PERMITTED_INTENTS` for `DD_ENGINEER` |
| `app/agents/graph/help_agent_graph.py` | `_AGENT_ENTRY_NODES` + node/edge wiring |
| `app/agents/graph/nodes/shared/sr_status_sync_node.py` | `FITOUT_PROGRESS` → agent map, single-step stage mapping (reuse the fitout-confirm early exit), open-SR label |
| `app/agents/graph/nodes/shared/missing_field_node.py` | chip block for `fitout_progress_percentage` (initial ask **and** `_CORRECTION_FIELD_DECORATIONS`) |
| `app/agents/graph/nodes/shared/lease_lookup_node.py` | `_LEASE_LOOKUP_PURPOSE_BY_AGENT` → `fitout_progress` |
| `app/agents/services/resource_picker_decorations.py` | `LEASE_PICKER_PURPOSE_BY_AGENT` + `should_browse_leases` |
| `app/agents/services/lease_lookup_service.py` | optional `fitout_progress` on `LeaseRecord` (only if §13.4 ships) |
| `app/observability/analytics_constants.py`, `display_labels.py` | new intents / display label *Fitout Progress* |

### Platform (`cenomi-api-2`, branch `feature/fitout-progress-sr-v2`)

`domain/handover_lease_lookup.go`, `lease/delivery/http/lease_lookup_handler.go`,
`lease/repository/postgres/handover_lease_lookup_repository.go` — §13.

### Frontend (`cenomi-frontend-2`)

**No change expected.** The question is a `text_question`, the chips are
`labeled_options` with plain values, and the confirm card is the existing
`confirmation_card`. Worth one manual check that a 4-chip row of short labels wraps cleanly
in `SrFabMessages.tsx`; if it does not, that is the only FE ticket.

---

## 17. Test plan

**Unit** (`backend/tests/unit/`)

- `test_fitout_progress_schema.py` — required/missing fields per stage, `build_title`,
  role helpers.
- `test_fitout_progress_suggestions.py` — the elapsed formula against the Go table
  (missing dates, `end <= start`, `today <= start`, `today >= end`, `0001-01-01`, Riyadh
  midnight), chip ladder and dedupe for every row of the §8.2 table.
- `test_fitout_progress_parsing.py` — every row of the §8.3 table.
- `test_fitout_progress_payload_builder.py` — create shape; approve echoes the stored
  payload byte-identical except the two editable keys; reject shape.
- `test_fitout_progress_entry.py` — stage pin, the cancel refusals in §14, direct-set.
- `test_workflow_config.py` — registration of the 6th agent.

**Eval** (`backend/tests/eval/scenarios.py`) — new Tier 6 sub-tiers at the next free
scenario ids:

| Sub-tier | Coverage |
| --- | --- |
| create · happy | lease + percentage in one sentence → card → confirm → APPROVED |
| create · chips | lease only → question → chip tap → confirm |
| create · edge | `120`, `-5`, "eighty-ish", lease not eligible, percentage below recorded |
| create · battle | user tries to cancel after submit, asks to attach a photo, asks to reassign |
| approve · happy | open SR → `Approve 65%` |
| approve · edit | open SR → types `90` → approved at 90 |
| approve · reject | open SR → Reject with a volunteered reason |
| approve · rbac | non-DD role refused before any PATCH |

**Live** (`frontend/tests/e2e/live/`) — one create lifecycle and one approve lifecycle,
the approve one seeded by `POST /backend_api/test/fitout-progress` against a lease with a
started, unfinished fit-out. Per the project convention, e2e runs only when we are pushing.

---

## 18. Decisions to confirm before coding

**DECISION 1 — keep the one-tap confirm card, or submit straight off the answer?**
A DD create writes `leases.fitout_progress` inside the create transaction and there is **no
cancel and no rewind**; a fat-fingered `8` instead of `80` lands on the lease permanently and
the only remedy is another SR. Recommendation: **keep the card** — it is a tap, not a
question, so "one question" still holds, and it is the only place a typo can be caught. A
config flag (`FITOUT_PROGRESS_SKIP_CONFIRMATION`, default off) can drop it for chip taps
later if PMs find it slow.

**DECISION 2 — non-DD create.** Refuse in the chatbot (§12).

**DECISION 3 — chip ladder shape.** Quarters (`25/50/75/100`) around the suggestion, max 4
chips. The alternative — `suggested-10 / suggested / suggested+10` — reads as false precision
on a number nobody measures to the percentage point. Quarters also match how PMs talk
("about half done").

**DECISION 4 — the weekly scheduler stays disabled.** The approval flow ships fully built
and tested, and its only organic source of SRs is a platform one-liner that master
deliberately turned off (PR 6655). Recommendation: **ship the agent, leave the scheduler
alone**, and raise re-enabling separately with whoever disabled it. Worth confirming that we
are not expected to re-enable it as part of this work.

**DECISION 5 — show the recorded percentage on create.** Needs the optional DTO field in
§13.4. Without it the create question cannot say "currently records 65%" and cannot floor
the chips. Recommendation: include it — it is three lines of Go and it is what stops a PM
accidentally lowering a lease's progress.

---

## 19. Phasing

> **Status (2026-09-27):** phases 0-3 are implemented — the platform purpose, the
> schema and suggestion service, the create flow end to end, and the approval
> flow. 283 tests cover them (5 unit files + a graph-level integration file);
> the backend unit suite sits on its pre-existing 62-failure baseline. Phase 4
> (evals, live lifecycle specs, the reference doc, the change-log entry) is open,
> and nothing has yet been exercised against the live platform.

| Phase | Content | Depends on |
| --- | --- | --- |
| 0 | Platform: `fitout_progress` purpose + the `IS NULL` half of the rule (+ optional DTO field) | DECISION 5 |
| 1 | Schema, suggestions service, extraction prompt, unit tests — no graph wiring yet | — |
| 2 | Nodes + graph wiring + registrations; create flow end to end | 1, and 0 for the picker |
| 3 | Approval flow: status sync mapping, approve/reject payloads, RBAC guard | 2 |
| 4 | Eval tiers, live lifecycle specs, `docs/fitout-progress-agent.md` reference doc, change-log entry | 3 |

Phase 1 can start immediately; phase 0 is small and independent, but the lease picker is
unusable without it, so it should land first or alongside.
