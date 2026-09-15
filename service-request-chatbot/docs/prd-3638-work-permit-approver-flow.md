# PRD: Work Permit Approver Flow (item 3638)

**Status:** Implemented on branch `3638`  
**Date:** 2026-08-21  
**Area:** `service-request-chatbot/backend` — Work Permit agent (`work_permit_agent`)  
**Platform contract:** Cenomi API V1 `workflow.flow` (workflow_id 5–8). Create is not a workflow level.

---

## 1. Background

The chatbot already collected a Work Permit create form (`CREATE_WORK_PERMIT`) and POSTed `/v1/service-requests`. It did **not** implement the platform review hops. Approvers opening an existing WP SR were mis-routed: `DD_ENGINEER IN_PROGRESS` mapped to Handover `RDD_REVIEW`.

Work Permits are not auto-approved as a category. They use the V1 workflow engine (`workflow.flow` JSON, `workflow_version` omitted/1). There is no platform field named `CREATE_SR` / `FM_REVIEW` / `RDD_REVIEW` — those were chatbot labels for Handover. Platform stages are `workflow_level` + `assigned_role`.

This PRD is the chatbot product spec for that contract.

---

## 2. Goals

1. Detect the current WP hop from GET `/service-requests/{id}` (`status=IN_PROCESS` + first `service_request_operations[]` with `status=IN_PROGRESS`).
2. Map hops to **WP-prefixed** chatbot stages so they never collide with Handover `FM_REVIEW` / `RDD_REVIEW`.
3. Let the matching role PATCH approve or reject with the same flat `{status, sub_category, payload, comment}` body as Handover/Fitout.
4. Cover all seven subtypes and four flow graphs (workflows 5–8).
5. Gate approve on **role vs IN_PROGRESS `assigned_role`**, not a fictional `APPROVE_SR_WORK_PERMIT` permission.

---

## 3. Non-Goals

- Portal UI (this repo is API/chatbot only).
- Post-approval `POST /service-requests/work-permit/status` (COMPLETE / EXTEND / CANCEL / CLOSE / ISSUE).
- Auto-generating permit PDF/QR (platform does that on final APPROVED).
- Full portal create DTO (workers/iqama, RFC3339 times). Chatbot create remains nested envelope + `planneddate` dates.
- Adding `APPROVE_WORK_PERMIT_SR` to `ACTION_PERMISSION_MAP` (unknown actions are fail-closed; hop gate is role match).

---

## 4. Lifecycle (platform)

`POST /service-requests` inserts one operation per `flow[]` row (`YET_TO_START`), then promotes level 1 to `IN_PROGRESS`. SR status starts `IN_PROCESS`. Create is not a workflow level.

| Subtype | Shared `workflow_id` | After CREATE (ordered hops) | Hops |
|---|---|---|---|
| `CONSTRUCTION_HOT`, `CONSTRUCTION_ROOF_ACCESS`, `MAINTENANCE_ROOF_ACCESS` | 5 | L1 `DD_ENGINEER` → L2 `FM_MANAGER` → L3 `MALL_MANAGER` | 3 |
| `CONSTRUCTION_COLD` | 6 | L1 `DD_ENGINEER` → L2 `MALL_MANAGER` **or** `OPERATION_EXECUTIVES` **or** `OPERATIONS` (same level) | 2 |
| `MAINTENANCE_HOT`, `MAINTENANCE_COLD` | 7 | L1 `FM_MANAGER` → L2 `MALL_MANAGER` | 2 |
| `OPERATIONS` | 8 | L1 `MALL_MANAGER` only | 1 |

Seed role IDs (migration `1707406122`): **11 = `MALL_MANAGER`**, **12 = `DD_ENGINEER`**, **20 = `FM_MANAGER`**. Handover also uses **81 = `OPERATIONS`**.

Cold L2: MM, OE, and OPERATIONS are all created at `workflow_level: 2` and all go `IN_PROGRESS`. Any matching role can PATCH `APPROVED`. Unlike Handover L1, WP does **not** delete sibling ops. Next level is empty → SR still goes `APPROVED`.

Auto-approve on create exists only if the creator’s role matches level 1 (`shouldAutoApproveOperation`). There is no Handover-style “don’t auto-approve final level” exception.

- Mall Manager creates **Operations** WP → L1 is MM → can auto-approve the whole SR on create.
- Mall Manager creates Hot/Cold/Maintenance → L1 is DD or FM → does **not** auto-approve.
- Tenant / contractor create → admin L1 stays `IN_PROGRESS`.

---

## 5. Chatbot stages

Keep create as `CREATE_WORK_PERMIT` (not `CREATE_SR`). Review stages are WP-prefixed:

| Chatbot stage | When |
|---|---|
| `CREATE_WORK_PERMIT` | Mall Manager / tenant / contractor POSTs |
| `WP_DD_REVIEW` | L1 op `assigned_role=DD_ENGINEER`, `status=IN_PROGRESS` |
| `WP_FM_REVIEW` | `FM_MANAGER` op `IN_PROGRESS` |
| `WP_MM_REVIEW` | `MALL_MANAGER` op `IN_PROGRESS` (sole hop for Operations; Hot L3; Maintenance L2) |
| `WP_MM_OPS_REVIEW` | Cold L2: MM / OE / OPERATIONS all `IN_PROGRESS` (or more than one of those roles) |
| `WP_CREATED` | Create POST succeeded; waiting on first approver |
| `WP_APPROVED` | Last hop PATCH `APPROVED` (or platform terminal `APPROVED` / `ACTIVE` / …) |
| `WP_REJECTED` | PATCH `REJECTED` |

Mapping priority: platform `REJECTED` / `APPROVED` / `ACTIVE` / … → terminal; else IN_PROGRESS roles: **DD → `WP_DD_REVIEW`**, **FM → `WP_FM_REVIEW`**, then MM/OE/Ops → `WP_MM_OPS_REVIEW` if `CONSTRUCTION_COLD` **or** more than one of those roles, else `WP_MM_REVIEW`.

`assigned_role` on GET is a **role name string** (or seed id 11/12/20/81). `allowed_actions` stay empty on WP (V1 JSON has no `step_type`).

---

## 6. Who acts

| Role | Where |
|---|---|
| `DD_ENGINEER` | Hot / Roof / Cold L1 |
| `FM_MANAGER` | Hot/Roof L2; Maintenance L1 |
| `MALL_MANAGER` | Hot/Roof L3; Cold L2; Maintenance L2; Operations L1 |
| `OPERATION_EXECUTIVES` | Cold L2 only |
| `OPERATIONS` | Cold L2 only |

Not in WP `flow[]`: HSE, CONTRACTOR, SUB_CONTRACTOR, `REGIONAL_DD_ENGINEER`.  
`CONTRACTOR` / `SUB_CONTRACTOR` get `CAN_RAISE_WORK_PERMIT_SR` (create sidebar only).

Approve = caller’s role is in the current IN_PROGRESS `assigned_role` set. `ADMIN` bypasses.

Create remains `CAN_RAISE_WORK_PERMIT_SR` (Mall Manager / Admin in this chatbot).

Supervisor intents: `CREATE_WORK_PERMIT_SR`, `APPROVE_WORK_PERMIT_SR`.

---

## 7. APIs the chatbot uses

### 7.1 Open existing

`GET /v1/service-requests/{id}` → `ServiceRequestDetailResponse`.

Use `status` / `sr_status`, `category` (detail) or `service_category`, `sub_category`, `service_request_operations[]` (not list DTO `operations`). Inbox: `GET /v1/service-requests?service_category=WORK_PERMIT&status=IN_PROCESS&self=true`.

### 7.2 Review / approve / reject (all hops)

`PATCH /v1/service-requests/{service_request_id}`  
Permission: `SERVICE_REQUEST_EDIT`. Body is **flat `status` + nested `payload`**, not `{ payload: { status } }`.

Approve (payload **required** — empty payload fails `ValidateSR`):

```json
{
  "status": "APPROVED",
  "sub_category": "CONSTRUCTION_HOT",
  "comment": "optional",
  "payload": { }
}
```

`payload` must be the current SR payload (GET → PATCH), still valid for that subtype.

Reject (payload not required):

```json
{
  "status": "REJECTED",
  "sub_category": "CONSTRUCTION_HOT",
  "comment": "optional"
}
```

Success: HTTP 200, body `"Successfully processed"`. Then GET again.

Chatbot UI actions: `confirm_approve_wp` / `reject_wp`.

### 7.3 Create

```json
{
  "service_category": "WORK_PERMIT",
  "sub_category": "CONSTRUCTION_HOT",
  "title": "...",
  "lease_id": 123,
  "tenant_profile_id": 456,
  "payload": { }
}
```

Hot / roof / maintenance dates live under `payload.planneddate` (`worktimedate` / `completionworkdate` = `DD/MM/YYYY`). Default create status if omitted: `IN_PROCESS`.

---

## 8. Chatbot implementation map

| Concern | Location |
|---|---|
| Stages, role IDs, `map_wp_operations_to_stage` | `app/agents/schemas/work_permit_schema.py` |
| Registry (7 subtypes + legacy `WORK_PERMIT`) | `app/agents/registry.py` |
| Sync nodes / intents | `app/agents/registries/workflow_config.py` |
| GET → WP stage **before** Handover DD/FM map | `app/agents/graph/nodes/shared/sr_status_sync_node.py` |
| Entry / confirm / nested POST / PATCH | `app/agents/graph/nodes/work_permit/*` |
| Graph routing (CONFIRMED before stale `WAITING_FOR_USER`) | `app/agents/graph/help_agent_graph.py` |
| Hop RBAC | `PermissionService.ensure_can_approve_work_permit` |
| Mock V1 hops (local eval) | `MockServiceRequestAPIService` |

---

## 9. Eval suite (tier 9)

Run against a live backend (`./run_agent_eval.sh --no-start --tier 9`).

| ID | Kind | What it proves |
|---|---|---|
| S207 | Smoke | Packed Hot create → confirmation → `WP_CREATED` |
| S240 | Flat | Supervisor classifies create work permit |
| S241 | Flat | Packed Hot create happy path |
| S242 | Flat | FM cannot create |
| S243 | Flat | DD cannot create |
| S244 | Flat | Invalid permit type recovery |
| L240 | Lifecycle | Hot: MM create → DD → FM → MM → `WP_APPROVED` |
| L241 | Lifecycle | Cold: DD → OPERATIONS at L2 → `WP_APPROVED` |
| L242 | Lifecycle | Maintenance Hot: FM → MM |
| L243 | Lifecycle | Operations: MM only |
| L244 | Lifecycle | DD reject → `WP_REJECTED` |
| L245 | Lifecycle | FM denied on DD hop |

Local mock must seed WP operations (not Handover FM→DD) or L240–L245 will enter `RDD_REVIEW`.

---

## 10. Acceptance criteria

- [x] Opening a Hot WP with `DD_ENGINEER IN_PROGRESS` sets `workflow_stage=WP_DD_REVIEW`, not `RDD_REVIEW`.
- [x] Nested CREATE payload (`service_category` / `sub_category` / `payload.planneddate`).
- [x] Approve PATCH includes current GET payload; reject omits payload.
- [x] Role must match IN_PROGRESS `assigned_role` (Admin bypass).
- [x] Cold L2 maps to `WP_MM_OPS_REVIEW`; Operations maps to `WP_MM_REVIEW`.
- [x] Eval tier 9 scenarios registered; `./run_agent_eval.sh --tier 9` wired.
- [x] Unit tests for mapping, RBAC, PATCH, mock hops (`tests/unit/test_work_permit_review.py`).

---

## 11. Open follow-ups

- Portal-complete create payload (workers, iqama, work times).
- Post-approval status API in the chatbot.
- Seed `OPERATION_EXECUTIVES` eval user if Cold L2 OE-only paths are required.
- Fitout lifecycle eval aliases (`TIER6A_FOS_*_LC`) are empty on this branch; restore separately if needed.
