# Spec: Work Permit Agent — Create, Approval, and Gap Fixes

**Item:** 3638
**Date:** 2026-09-10
**Status:** Implemented on branch `3638` (PR 5685)
**Owner area:** Service Request Chatbot (`work_permit_agent`)
**Platform contract:** Cenomi API V1 `workflow.flow` (workflow_id 5–8). Create is **not** a workflow level.

This is the canonical spec for Work Permit in the chatbot. It covers:

1. The **create** conversation (`CREATE_WORK_PERMIT_SR` → `POST /v1/service-requests`).
2. The **approval** conversation (`APPROVE_WORK_PERMIT_SR` → hop mapping → `PATCH`).
3. The **gap-fix pass** that closed GAP-1, GAP-2, and GAP-4 from the 2026-09-08 spec-vs-branch audit.

Supersedes the earlier split docs (`work-permit-agent-create-implementation-spec.md` and `3638-work-permit-agent-and-approval-flow-spec.md`) for post-gap behaviour.

---

## 1. Problem

The chatbot already collected a Work Permit create form and POSTed `/v1/service-requests`. It did **not** implement platform review hops.

Approvers opening an existing Work Permit were mis-routed: `DD_ENGINEER IN_PROGRESS` mapped to Handover `RDD_REVIEW`.

Work Permits are **not** auto-approved as a category. They use the V1 workflow engine (`workflow.flow` JSON, `workflow_version` omitted / 1). There is no platform field named `CREATE_SR`, `FM_REVIEW`, or `RDD_REVIEW` — those are chatbot labels for **Handover**. Platform stages are `workflow_level` + `assigned_role`.

---

## 2. Agent identity

| Key | Value |
|---|---|
| Agent name | `work_permit_agent` |
| Display name | Work Permit Agent |
| Service category | `WORK_PERMIT` |
| Schema | `work_permit_service_request_schema` |
| Create stage | `CREATE_WORK_PERMIT` (not Handover `CREATE_SR`) |
| Create success | `WP_CREATED`, or `WP_APPROVED` if the platform auto-approves on create |
| Supervisor intents | `CREATE_WORK_PERMIT_SR`, `APPROVE_WORK_PERMIT_SR` |

All seven subtypes (plus the legacy `WORK_PERMIT` key used before subtype is known) route to the **same** agent:

| Registry key (`sub_category`) | Label | Extraction hint |
|---|---|---|
| `WORK_PERMIT` (legacy) | Unknown / not yet chosen | — |
| `CONSTRUCTION_HOT` | Construction – Hot Work | Welding, cutting, heat |
| `CONSTRUCTION_COLD` | Construction – Cold Work | Construction without heat; **default** if the user says “construction” with no hot/cold |
| `CONSTRUCTION_ROOF_ACCESS` | Construction – Roof Access | Roof access for construction |
| `MAINTENANCE_HOT` | Maintenance – Hot Work | Heat-producing maintenance |
| `MAINTENANCE_COLD` | Maintenance – Cold Work | Maintenance without heat |
| `MAINTENANCE_ROOF_ACCESS` | Maintenance – Roof Access | Roof access for maintenance |
| `OPERATIONS` | Operations | Operational activities in the unit |

`lookup_agent(service_category, sub_category)` in `registry.py` is the authoritative lookup. `resolve_wp_sub_category()` later replaces the legacy `WORK_PERMIT` key with a real enum for the POST.

---

## 3. Gap changelog (audit 2026-09-08 → shipped 2026-09-10)

Source audit: `spec-vs-3638-gaps`. Shipped in `b80a151` (`fix: close Work Permit create validation, lease picker, and auto-approve gaps`).

### GAP-1 — Date format and order — **closed**

**Was:** `ValidationService._SINGLE_DATE_FIELDS` only listed Handover `startDate` / `endDate`. WP `start_date` / `end_date` were not format-checked. End-before-start was not enforced (the LLM prompt asked for it; the validator did not).

**Now:**

- `start_date` and `end_date` are in `_SINGLE_DATE_FIELDS` (ISO format check).
- `validate_wp_date_range()` enforces `start_date <= end_date`.
- **Same-day permits are valid.** Handover still uses strict `startDate < endDate`; do not reuse that helper for WP.

### GAP-2 — Lease browse chips on create — **closed**

**Was:** `should_browse_leases()` returned true only for Handover `CREATE_SR`. WP asked for a typed `lease_code`.

**Now:**

- `_LEASE_BROWSE_STAGES = {CREATE_SR, CREATE_WORK_PERMIT}`.
- WP create shows the same eligible-lease picker as Handover when `HANDOVER_LEASE_PICKER_ENABLED` is on.
- WP does **not** send a fit-out `purpose` filter.
- Unpinned sessions must **not** get Handover leases by default. If `active_agent` is not `work_permit_agent` / `rdd_handover_agent` / a fit-out confirm agent, the picker stays off.

Flag off → fall back to the plain lease-code prompt (same as Handover).

### GAP-3 — Create DTO is not portal-complete — **still out of scope**

The chatbot does not collect workers / iqama lists, RFC3339 work times (`worktimefromLT` / `worktimetoLT`), `phone`, `work_type`, or `work_location`. Those keys exist on the inner payload as empty strings (or `workers` only if already in `collected_data`) for forward compatibility.

Full portal create DTO parity is **not** a 3638 requirement.

### GAP-4 — Operations auto-approve on create — **closed in the chatbot**

**Was:** After POST the chatbot always landed on `WP_CREATED`. The live platform may return `status=APPROVED` immediately when a Mall Manager creates an **Operations** WP (L1 is MM, `shouldAutoApproveOperation`). Re-open would eventually map `WP_APPROVED`; the create turn would not.

**Now:** After a successful create POST the chatbot **GETs** `/v1/service-requests/{id}`. If mapping yields `WP_APPROVED` or `WP_REJECTED`, that stage is used. GET failure keeps `WP_CREATED` (create still succeeded).

The local mock still seeds OPERATIONS as `IN_PROGRESS` / `IN_PROCESS` and does **not** auto-approve. Staging smoke is still useful: Mall Manager creates OPERATIONS → confirm whether the platform returns `APPROVED` immediately → confirm the create turn says auto-approved.

### Code-only notes (no extra product work)

| # | Item | Detail |
|---|---|---|
| C-1 | `backend_fields` includes `"lease"` | Stops the LLM proposing the inner `lease` alias. Harmless. |
| C-2 | Reject phrases | `{reject, decline, refuse, not approved, do not approve, don't approve, dont approve}` — broader than “reply reject”. |
| C-3 | `active_agent=None` after a mid-hop approve | `_review_result()` clears the agent when the next stage is still a WP review. `sr_status_sync` pins `work_permit_agent` on the next open. |
| C-4 | Review branch after entry | `_route_after_work_permit_entry`: if `workflow_stage` is a `WP_*_REVIEW` stage → `merge_state` (skip field extraction / lease lookup). |

---

## 4. Supervisor routing

### 4.1 Create

**Intent:** `CREATE_WORK_PERMIT_SR`

User wants a work permit for construction, maintenance, roof access, or operational work.

Keywords: work permit, hot work, cold work, roof access, construction permit, maintenance permit, operations permit, permit for work, I need a permit.

Routing:

- `service_category` = `WORK_PERMIT`
- `sub_category` = `WORK_PERMIT` until `work_permit_type` is extracted
- `target_agent` = `work_permit_agent`

`CREATE_WORK_PERMIT_SR` is in `_CREATE_NEW_SR_INTENTS`. An existing `backend_refs.sr_id` is **not** opened on this turn. RBAC denial happens inside `supervisor_node` and routes to `response_generation` with `WAITING_FOR_USER`.

### 4.2 Approve / reject / open existing

**Intent:** `APPROVE_WORK_PERMIT_SR`

User wants to approve, reject, or review an existing Work Permit. Opening by SR id is enough — status sync maps the hop.

Keywords: approve work permit, reject work permit, review work permit, sign off permit, open work permit SR.

Inbox / notification Open with `sub_category` in the seven WP enums (or category `WORK_PERMIT`) also lands here via `sr_status_sync`.

---

## 5. Create flow

### 5.1 Who may create

Permission: `CAN_RAISE_WORK_PERMIT_SR` (action `CREATE_WORK_PERMIT_SR`).

| Role | Can create | Can view |
|---|---|---|
| `MALL_MANAGER` | Yes | Yes |
| `ADMIN` | Yes | Yes |
| `FM_MANAGER` | No | Yes |
| `DD_ENGINEER` | No | Yes |
| `OPERATIONS` | No | Yes |
| `OPERATION_EXECUTIVES` | No | Yes |

`PermissionService.ensure_can_create_work_permit(auth)` is `check("CREATE_WORK_PERMIT_SR", auth)`. Submission re-checks when `auth_context` is present. If `auth_context` is `None` (shadow mode), the submit guard is skipped.

Platform may also grant create to contractor / sub-contractor (sidebar). **This chatbot create path is Mall Manager / Admin only.**

### 5.2 Fields

**User-supplied (extracted from chat):**

| Field | Required | Notes |
|---|---|---|
| `lease_code` | Yes | Lease lookup trigger; picker or typed code |
| `work_permit_type` | Yes | One of the seven enums |
| `description` | Optional | `""` is an explicit skip; omitting (`None`) is missing |
| `start_date` | Yes | ISO `YYYY-MM-DD` in chat; sent as `DD/MM/YYYY` |
| `end_date` | Yes | Must be **on or after** start (same-day allowed) |
| `contractor_name` | Optional | Contractor performing the work |
| `comments` | Optional | Extra notes |

**Backend-derived (lease lookup — LLM must never invent):**

`lease_id`, `property_id`, `tenant_profile_id`, `unit_codes`, `mall`, `brand`, `city`, `contracted_area`

`get_missing_fields("CREATE_WORK_PERMIT", collected)`:

- Optional field → missing only if value is `None`.
- Required field → missing if `None`, `""`, or `[]`.

Typical collection order: lease → permit type → description → start date → end date → contractor → comments → confirmation card.

There are **no** required documents on create.

### 5.3 Graph (create)

```
load_state
  → supervisor                 (CREATE_WORK_PERMIT_SR; RBAC)
  → registry                   (active_agent = work_permit_agent)
  → work_permit_entry          (pin CREATE_WORK_PERMIT; HITL confirm / cancel)
  → field_extraction           (WP extraction prompt)
  → merge_state
  → lease_lookup               (picker or lease_code; skip if lease_id present)
  → validation                 (required, enum, ISO dates, start ≤ end)
  → missing_field              (if required fields still missing)
       or work_permit_confirmation  (card, confirmation_status=PENDING)
  → response_generation

On Confirm (action=confirm / confirm_create_wp, or “yes” on a PENDING card):
  work_permit_entry            (confirmation_status=CONFIRMED)
  → merge_state
  → lease_lookup / validation
  → work_permit_confirmation   (no-op if already CONFIRMED)
  → work_permit_payload_builder
  → work_permit_api_submission (POST, then GET)
  → response_generation        (WP_CREATED or WP_APPROVED)
```

`_AGENT_ENTRY_NODES["work_permit_agent"] = "work_permit_entry"`.

#### After entry (`_route_after_work_permit_entry`)

CONFIRMED is checked **before** a stale `WAITING_FOR_USER` so Confirm does not re-ask (same Fitout-style trap).

| Condition | Next |
|---|---|
| Guided-tour pending answer | `merge_state` |
| `confirmation_status == CONFIRMED` | `merge_state` (submit path) |
| `confirmation_status == REJECTED` | `field_extraction` (user wants to change a field) |
| `status == WAITING_FOR_USER` | form patch or `response_generation` |
| `action_override == cancel` | `merge_state` |
| Stage is a `WP_*_REVIEW` | `merge_state` (review; no extraction) |
| else (create) | form patch or `field_extraction` |

Cancel / start-over clears the workflow. Restart phrases (“start over”, “restart”, “new request”) wipe agent, collected data, and `backend_refs`.

**Second create in-session:** if intent is still `CREATE_WORK_PERMIT_SR` and stage is `WP_CREATED` / `WP_APPROVED` / `WP_REJECTED`, clear the draft and pin `CREATE_WORK_PERMIT`.

### 5.4 Lease lookup

`WorkflowConfig.lease_trigger_fields` = `lease_code`, `company`, `lease_brand_mall`.

If `lease_id` is missing and browse is enabled: `response_ui` is the searchable lease picker (recent eligible leases + search). If browse is disabled or the user types a code: `response_ui.type = text_question`, `field = lease_code`.

Once resolved, backend-derived fields are filled. The LLM is forbidden from inventing them (`BACKEND_DERIVED_FIELDS` / extraction prompt).

### 5.5 Validation (create)

For `CREATE_WORK_PERMIT`:

1. Required fields from `CREATE_WORK_PERMIT_STAGE`.
2. Enum: `work_permit_type` must be in `VALID_WORK_PERMIT_TYPES`.
3. Date **format** on `start_date` / `end_date` (ISO).
4. Date **order** via `validate_wp_date_range`: `start_date <= end_date`.
5. No document-count / document-type checks.

### 5.6 Confirmation card (create)

Skipped if any create required fields are still missing.

Otherwise:

```
confirmation_required = True
confirmation_status   = PENDING
status                = READY_TO_SUBMIT
response_ui.type      = confirmation_card
```

Display rows (skip empty): lease_code, mall, brand, unit_codes, work_permit_type (human label), description, start_date, end_date, contractor_name, work_location, work_type, comments.

**Editable on create:** `work_permit_type`, `description`, `start_date`, `end_date`, `contractor_name`, `comments`. Inline edits use `corrected_fields`.

Message: *All required details have been collected. Please review the Work Permit Service Request below and confirm, or let me know if you want to change anything.*

UI actions: `confirm` / `confirm_create_wp`. Cancel: `cancel` / `cancel_create_wp`.

### 5.7 Create API

`POST /v1/service-requests`

Nested envelope (not a flat Handover body):

```json
{
  "service_category": "WORK_PERMIT",
  "sub_category": "CONSTRUCTION_HOT",
  "title": "Work Permit - CONSTRUCTION_HOT",
  "lease_id": 123,
  "tenant_profile_id": 456,
  "property_id": 3041,
  "lease_code": "t0105712",
  "service_request_id": "",
  "payload": {
    "mall": "Jawharat Jeddah",
    "brand": "Under Armour",
    "lease": "t0105712",
    "lease_code": "t0105712",
    "contractor_name": "ABC Contractors",
    "comments": "",
    "description": "Welding on unit FF050",
    "phone": "",
    "work_type": "",
    "work_location": "",
    "unit_codes": ["FF050"],
    "planneddate": {
      "worktimedate": "26/09/2026",
      "completionworkdate": "26/09/2026",
      "worktimefromLT": "",
      "worktimetoLT": ""
    }
  }
}
```

Dates under `payload.planneddate` use **`DD/MM/YYYY`**. Default platform status if omitted: `IN_PROCESS`. Title defaults to `Work Permit - {sub_category}` unless the user supplied one.

Hard required keys before POST: `lease_code`, `lease_id`, `work_permit_type`, `start_date`, `end_date`.

After HTTP success the chatbot GETs the SR (GAP-4):

| GET mapping | Chatbot stage | User message |
|---|---|---|
| Terminal approved (or GET failed / still in process) | `WP_CREATED` | Submitted; routed to the next reviewer. GET failure stays here. |
| Platform already `APPROVED` / `ACTIVE` / … | `WP_APPROVED` | Submitted **and auto-approved**. |
| Platform `REJECTED` | `WP_REJECTED` | Rare on create; mapped if GET says so. |

### 5.8 Typical conversation

1. Mall Manager: “I need a work permit for construction at Jawharat Jeddah.”
2. Supervisor → `CREATE_WORK_PERMIT_SR` → `work_permit_agent` → `CREATE_WORK_PERMIT`.
3. Agent offers lease browse (or asks for a lease code).
4. User picks `t0105712` or types it.
5. Lease lookup fills mall / brand / unit / ids.
6. Agent asks permit type if not inferred (`construction` → `CONSTRUCTION_COLD`).
7. Description, start date, end date, contractor, comments (optional until set or skipped with `""`).
8. Confirmation card.
9. User Confirm.
10. Nested POST → GET → `WP_CREATED` (or `WP_APPROVED` for MM Operations auto-approve) + SR id.

Packed one-shot (eval `_WP_S241` / `S207`): lease + type + description + dates + contractor + “No comments” in one message can skip to the card in one turn.

---

## 6. Platform approval lifecycle

`POST /service-requests` inserts one operation per `flow[]` row (`YET_TO_START`), then promotes **level 1** to `IN_PROGRESS`. SR status starts `IN_PROCESS`. Create is not a workflow level.

### 6.1 Four flow graphs, seven subtypes

| Subtype | `workflow_id` | After CREATE (ordered hops) | Hops |
|---|---|---|---|
| `CONSTRUCTION_HOT` | 5 | L1 `DD_ENGINEER` → L2 `FM_MANAGER` → L3 `MALL_MANAGER` | 3 |
| `CONSTRUCTION_ROOF_ACCESS` | 5 | same as Hot | 3 |
| `MAINTENANCE_ROOF_ACCESS` | 5 | same as Hot | 3 |
| `CONSTRUCTION_COLD` | 6 | L1 `DD_ENGINEER` → L2 `MALL_MANAGER` **or** `OPERATION_EXECUTIVES` **or** `OPERATIONS` (same level) | 2 |
| `MAINTENANCE_HOT` | 7 | L1 `FM_MANAGER` → L2 `MALL_MANAGER` | 2 |
| `MAINTENANCE_COLD` | 7 | same as Maintenance Hot | 2 |
| `OPERATIONS` | 8 | L1 `MALL_MANAGER` only | 1 |

### 6.2 Seed role IDs

Migration `1707406122`:

| ID | Role |
|---|---|
| 11 | `MALL_MANAGER` |
| 12 | `DD_ENGINEER` |
| 20 | `FM_MANAGER` |
| 81 | `OPERATIONS` (also used by Handover) |

GET `assigned_role` is a **role name string** or one of these ids. The chatbot normalizes both (`normalize_wp_role`).

### 6.3 Cold L2 (same-level OR)

Mall Manager, Operation Executives, and Operations are all created at `workflow_level: 2` and all go `IN_PROGRESS`.

- Any matching role can PATCH `APPROVED`.
- Unlike Handover L1, Work Permit does **not** delete sibling ops.
- Next level is empty → SR still goes `APPROVED`.

### 6.4 Auto-approve on create

Only if the creator’s role matches level 1 (`shouldAutoApproveOperation`). There is **no** Handover-style “don’t auto-approve final level” exception.

| Creator | Subtype | Result |
|---|---|---|
| Mall Manager | Operations | L1 is MM → **can auto-approve the whole SR on create** |
| Mall Manager | Hot / Cold / Maintenance / Roof | L1 is DD or FM → **does not** auto-approve |
| Tenant / contractor | any | Admin L1 stays `IN_PROGRESS` |

The chatbot detects this via the post-create GET (GAP-4), not by guessing the role locally.

---

## 7. Chatbot approval flow

### 7.1 Chatbot stages (WP-prefixed)

Review stages are **WP-prefixed** so they never collide with Handover `FM_REVIEW` / `RDD_REVIEW` in shared graph routing.

| Chatbot stage | When |
|---|---|
| `CREATE_WORK_PERMIT` | Mall Manager / tenant / contractor POSTs |
| `WP_DD_REVIEW` | L1 op `assigned_role=DD_ENGINEER`, `status=IN_PROGRESS` |
| `WP_FM_REVIEW` | `FM_MANAGER` op `IN_PROGRESS` |
| `WP_MM_REVIEW` | `MALL_MANAGER` op `IN_PROGRESS` (Operations L1; Hot L3; Maintenance L2) |
| `WP_MM_OPS_REVIEW` | Cold L2: MM / OE / OPERATIONS all `IN_PROGRESS` (or more than one of those roles) |
| `WP_CREATED` | Create POST succeeded; waiting on first approver |
| `WP_APPROVED` | Last hop PATCH `APPROVED`, or platform terminal `APPROVED` / `ACTIVE` / … |
| `WP_REJECTED` | PATCH `REJECTED` |

Human labels:

| Stage | Label |
|---|---|
| `CREATE_WORK_PERMIT` | Create Work Permit |
| `WP_DD_REVIEW` | DD Review |
| `WP_FM_REVIEW` | FM Review |
| `WP_MM_REVIEW` | Mall Manager Review |
| `WP_MM_OPS_REVIEW` | Mall Manager / Operations Review |
| `WP_CREATED` | Submitted |
| `WP_APPROVED` | Approved |
| `WP_REJECTED` | Rejected |

### 7.2 Mapping GET operations → stage

Source: GET `/v1/service-requests/{id}` → `status` + `service_request_operations[]` (detail DTO; **not** list `operations`).

`map_wp_operations_to_stage` priority:

1. Platform status `REJECTED` → `WP_REJECTED`
2. Platform status in `{APPROVED, COMPLETED, DONE, ACTIVE, EXPIRED, CLOSED, EXTENDED}` → `WP_APPROVED`
3. Else first IN_PROGRESS roles:
   - DD Engineer → `WP_DD_REVIEW`
   - FM Manager → `WP_FM_REVIEW`
   - MM / OE / Operations → `WP_MM_OPS_REVIEW` if subtype is `CONSTRUCTION_COLD` **or** more than one of those roles; otherwise `WP_MM_REVIEW`
4. No IN_PROGRESS ops → keep `current_stage`

**Critical:** `_map_operations_to_stage` must **early-exit for `WORK_PERMIT`** (`is_work_permit_category`) **before** Handover mapping. Otherwise Hot WP `DD_ENGINEER IN_PROGRESS` becomes `RDD_REVIEW`.

`allowed_actions` stay empty on WP (V1 JSON has no `step_type`).

### 7.3 Who may approve (hop RBAC)

There is **no** `APPROVE_SR_WORK_PERMIT` permission. Approve = caller’s role is in the current IN_PROGRESS `assigned_role` set. **Admin bypasses.**

| Role | Where they approve |
|---|---|
| `DD_ENGINEER` | Hot / Roof / Cold L1 |
| `FM_MANAGER` | Hot / Roof L2; Maintenance L1 |
| `MALL_MANAGER` | Hot / Roof L3; Cold L2; Maintenance L2; Operations L1 |
| `OPERATION_EXECUTIVES` | Cold L2 only |
| `OPERATIONS` | Cold L2 only |

Not in WP `flow[]`: HSE, CONTRACTOR, SUB_CONTRACTOR, `REGIONAL_DD_ENGINEER`.

Stage → allowed roles (chatbot):

| Stage | Roles |
|---|---|
| `WP_DD_REVIEW` | `DD_ENGINEER`, `ADMIN` |
| `WP_FM_REVIEW` | `FM_MANAGER`, `ADMIN` |
| `WP_MM_REVIEW` | `MALL_MANAGER`, `ADMIN` |
| `WP_MM_OPS_REVIEW` | `MALL_MANAGER`, `OPERATIONS`, `OPERATION_EXECUTIVES`, `ADMIN` |

Wrong-hop example: FM Manager cannot PATCH while DD Engineer is the IN_PROGRESS assigned role.

`APPROVE_WORK_PERMIT_SR` is **not** added to `ACTION_PERMISSION_MAP` (unknown actions are fail-closed). Hop gate is `PermissionService.ensure_can_approve_work_permit(auth, workflow_stage=, in_progress_roles=)`.

### 7.4 Graph (review)

```
supervisor (APPROVE_WORK_PERMIT_SR or open by sr_id / notification)
  → sr_status_sync          (GET → WP_*_REVIEW; set active_agent=work_permit_agent)
  → work_permit_entry       (confirm_approve_wp / reject_wp)
  → merge_state             (review skips field extraction / lease lookup)
  → validation              (review stages have no required fields)
  → work_permit_confirmation  (review card; fields not editable)
  → work_permit_payload_builder  (PATCH body)
  → work_permit_api_submission   (PATCH, then GET)
  → response_generation
```

Review hops collect **no extra DTO**. The approver resubmits the current GET payload (`backend_refs.create_payload.payload`, else rebuilt from `collected_data`).

UI actions:

| Action | Meaning |
|---|---|
| `confirm_approve_wp` / `approve` / `approve_wp` | Approve current hop (`backend_refs.wp_action = approve`) |
| `reject_wp` / `reject` | Reject the SR (`wp_action = reject`) |

Review card: `confirmAction = confirm_approve_wp`, `rejectAction = reject_wp`. Fields are **not** editable.

### 7.5 Open existing API

`GET /v1/service-requests/{id}` → `ServiceRequestDetailResponse`.

Use:

- `status` / `sr_status`
- `category` (detail) or `service_category`
- `sub_category`
- `service_request_operations[]` (not list DTO `operations`)

Inbox: `GET /v1/service-requests?service_category=WORK_PERMIT&status=IN_PROCESS&self=true`

### 7.6 Approve / reject API

`PATCH /v1/service-requests/{service_request_id}`
Permission: `SERVICE_REQUEST_EDIT` on the platform; chatbot hop RBAC as §7.3.

Body is **flat `status` + nested `payload`**, not `{ payload: { status } }`.

**Approve** — payload **required** (empty payload fails `ValidateSR`). Payload must be the current GET payload, still valid for that subtype:

```json
{
  "status": "APPROVED",
  "sub_category": "CONSTRUCTION_HOT",
  "comment": "optional",
  "payload": { }
}
```

**Reject** — payload not required:

```json
{
  "status": "REJECTED",
  "sub_category": "CONSTRUCTION_HOT",
  "comment": "optional"
}
```

Success: HTTP 200, body `"Successfully processed"`. Chatbot then GETs again to map the next hop.

After a mid-flow approve:

> Your approval for Work Permit SR **{id}** was recorded. The request is now with **{next stage label}**.

`active_agent` is cleared so the next reviewer is not stuck on the previous hop’s agent pin. After last hop: stage `WP_APPROVED` + `workflow_complete`. After reject: `WP_REJECTED`.

---

## 8. End-to-end hop examples

### 8.1 Construction Hot (workflow 5)

1. Mall Manager creates → `WP_CREATED`, L1 DD `IN_PROGRESS`
2. DD Engineer opens → `WP_DD_REVIEW` → PATCH APPROVED → `WP_FM_REVIEW`
3. FM Manager opens → `WP_FM_REVIEW` → PATCH APPROVED → `WP_MM_REVIEW`
4. Mall Manager opens → `WP_MM_REVIEW` → PATCH APPROVED → `WP_APPROVED`

### 8.2 Construction Cold (workflow 6)

1. Mall Manager creates → `WP_CREATED`, L1 DD `IN_PROGRESS`
2. DD Engineer approves → `WP_MM_OPS_REVIEW` (MM, OE, Operations all `IN_PROGRESS`)
3. Any of MM / OE / Operations PATCHes APPROVED → `WP_APPROVED`

### 8.3 Maintenance Hot / Cold (workflow 7)

1. Mall Manager creates → `WP_CREATED`, L1 FM `IN_PROGRESS`
2. FM Manager approves → `WP_MM_REVIEW`
3. Mall Manager approves → `WP_APPROVED`

### 8.4 Operations (workflow 8)

1. Mall Manager creates → L1 is MM. Platform **may** auto-approve the whole SR.
2. If auto-approved: create turn lands on `WP_APPROVED` (post-create GET).
3. If not: Mall Manager opens `WP_MM_REVIEW` → PATCH APPROVED → `WP_APPROVED`

---

## 9. Create vs approval (boundary)

| Create | Approval |
|---|---|
| Intent `CREATE_WORK_PERMIT_SR` | Intent `APPROVE_WORK_PERMIT_SR` (or open by `sr_id`) |
| Stage `CREATE_WORK_PERMIT` → `WP_CREATED` (or `WP_APPROVED`) | Stages `WP_*_REVIEW` → `WP_APPROVED` / `WP_REJECTED` |
| `POST /service-requests` nested envelope | `PATCH /service-requests/{id}` flat status + payload |
| Permission `CAN_RAISE_WORK_PERMIT_SR` | Role vs IN_PROGRESS `assigned_role` (Admin bypass) |
| Collects fields; confirmation card editable | No extra DTO; review card not editable |
| Then GET to detect auto-approve | Then GET to map the next hop |

---

## 10. Implementation map

| Concern | Location |
|---|---|
| Stages, role ids, `map_wp_operations_to_stage` | `app/agents/schemas/work_permit_schema.py` |
| Extraction prompt | `app/agents/prompts/work_permit_extraction_prompt.py` |
| Registry (7 subtypes + legacy `WORK_PERMIT`) | `app/agents/registry.py` |
| Sync nodes / intents / field questions | `app/agents/registries/workflow_config.py` |
| GET → WP stage **before** Handover DD/FM map | `app/agents/graph/nodes/shared/sr_status_sync_node.py` |
| Entry / confirm / nested POST / PATCH | `app/agents/graph/nodes/work_permit/*` |
| Graph routing | `app/agents/graph/help_agent_graph.py` |
| Date format + `start_date <= end_date` | `app/agents/services/validation_service.py` (`validate_wp_date_range`) |
| Lease browse on `CREATE_WORK_PERMIT` | `app/agents/services/resource_picker_decorations.py` |
| Create permission | `PermissionService.ensure_can_create_work_permit` |
| Hop RBAC | `PermissionService.ensure_can_approve_work_permit` |
| Mock V1 hops (local eval) | `MockServiceRequestAPIService` |
| Unit tests | `tests/unit/test_work_permit_review.py`, `test_work_permit_permissions.py` |
| Eval | `tests/eval/scenarios.py` (`S207`, `_WP_S240`–`_WP_S244`), `lifecycle_scenarios.py` (`L240`–`L245`) |

Frontend has **no** Work Permit-specific screens; create and review use the shared confirmation card.

HTTP surface is the shared chat endpoint: `POST /api/chat/service-request` (`message`, `session_id`, `action`, `corrected_fields`, `selected_lease_id`).

---

## 11. Eval suite (tier 9)

Python names are `_WP_S240`–`_WP_S244` so they do not overwrite TIER2 `_S240`–`_S244`. After merging `origin/dev`, **`--tier 9` is Work Permit**; notifications run with **`--tier notif`**.

```bash
cd service-request-chatbot
./run_agent_eval.sh --no-start --tier 9
# lifecycle hops:
./run_lifecycle_eval.sh --no-start --tier 9a   # L240–L243 happy
./run_lifecycle_eval.sh --no-start --tier 9b   # L244 reject
./run_lifecycle_eval.sh --no-start --tier 9c   # L245 wrong-hop deny
```

| ID | Kind | What it proves |
|---|---|---|
| S207 | Smoke | Packed Hot create → confirmation → `WP_CREATED` |
| `_WP_S240` | Flat | Supervisor classifies create work permit |
| `_WP_S241` | Flat | Packed Hot create happy path |
| `_WP_S242` | Flat | FM cannot create |
| `_WP_S243` | Flat | DD cannot create |
| `_WP_S244` | Flat | Invalid permit type recovery |
| L240 | Lifecycle | Hot: MM create → DD → FM → MM → `WP_APPROVED` |
| L241 | Lifecycle | Cold: DD → OPERATIONS at L2 → `WP_APPROVED` |
| L242 | Lifecycle | Maintenance Hot: FM → MM |
| L243 | Lifecycle | Operations: MM only |
| L244 | Lifecycle | DD reject → `WP_REJECTED` |
| L245 | Lifecycle | FM denied on DD hop |

Local mock **must** seed WP operations (not Handover FM → DD) or L240–L245 will enter `RDD_REVIEW`.

---

## 12. Acceptance criteria

- [x] Opening a Hot WP with `DD_ENGINEER IN_PROGRESS` sets `workflow_stage=WP_DD_REVIEW`, not `RDD_REVIEW`.
- [x] Nested CREATE payload (`service_category` / `sub_category` / `payload.planneddate`).
- [x] Approve PATCH includes current GET payload; reject omits payload.
- [x] Role must match IN_PROGRESS `assigned_role` (Admin bypass).
- [x] Cold L2 maps to `WP_MM_OPS_REVIEW`; Operations maps to `WP_MM_REVIEW`.
- [x] All seven subtypes registered to `work_permit_agent`.
- [x] Eval `--tier 9` is Work Permit; `_WP_S240`–`_WP_S244` and L240–L245 registered.
- [x] Unit tests for mapping, RBAC, PATCH, mock hops.
- [x] GAP-1: `start_date` / `end_date` format + `start <= end` (same-day allowed).
- [x] GAP-2: lease browse chips on `CREATE_WORK_PERMIT` (flag-gated; unpinned sessions do not get Handover leases).
- [x] GAP-4: post-create GET maps platform auto-approve to `WP_APPROVED`.
- [ ] GAP-3: portal-complete create DTO — **out of scope**.

---

## 13. Out of scope / follow-ups

- Portal UI (this repo is API / chatbot only).
- Portal-complete create DTO (GAP-3: workers / iqama / RFC3339 times / phone / work_type / work_location).
- Post-approval `POST /service-requests/work-permit/status` (COMPLETE / EXTEND / CANCEL / CLOSE / ISSUE).
- Auto-generating permit PDF / QR (platform does that on final APPROVED).
- Seed `OPERATION_EXECUTIVES` eval user if Cold L2 OE-only paths are required.
- Mock auto-approve for Operations create (chatbot handles live platform GET; mock stays `IN_PROCESS`).
- Staging smoke: Mall Manager creates OPERATIONS WP and confirms immediate `APPROVED` if `shouldAutoApproveOperation` applies.
