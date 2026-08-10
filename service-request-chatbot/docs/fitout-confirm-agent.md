# Fitout Confirmation Agents — Design & Implementation Reference

> **Audience:** Backend engineers, new developers, and QA extending or testing the fitout confirmation workflows.
> **Last updated:** August 2026
> **Status:** Production-ready — all three agents fully implemented (entry, validation, payload builder, API submission, confirmation nodes wired in the main graph).

---

## Table of Contents

1. [Overview — Three Agents, One Shared Pipeline](#1-overview--three-agents-one-shared-pipeline)
2. [Agent Taxonomy](#2-agent-taxonomy)
3. [SUBCAT_CONFIG — Radio-Field Yes/No Branching](#3-subcat_config--radio-field-yesno-branching)
4. [Workflow Stages — CREATE_SR and APPROVE_SR](#4-workflow-stages--create_sr-and-approve_sr)
5. [Dynamic Required Fields (get_required_fields / get_missing_fields)](#5-dynamic-required-fields)
6. [APPROVE_ROLE_MAP — Who Can Approve Each Sub-category](#6-approve_role_map--who-can-approve-each-sub-category)
7. [PERMISSION_MAP — Stage-Level Role Gating](#7-permission_map--stage-level-role-gating)
8. [Entry Node — fitout_confirm_entry_node](#8-entry-node--fitout_confirm_entry_node)
9. [Payload Builder — CREATE_SR and APPROVE_SR Paths](#9-payload-builder--create_sr-and-approve_sr-paths)
10. [API Submission Node](#10-api-submission-node)
11. [Confirmation Node](#11-confirmation-node)
12. [WorkflowConfig Registration](#12-workflowconfig-registration)
13. [Agent Registry Entries](#13-agent-registry-entries)
14. [Node File Inventory](#14-node-file-inventory)
15. [Supervisor Intents & RBAC](#15-supervisor-intents--rbac)
16. [Graph Wiring](#16-graph-wiring)
17. [Typical Conversation Flows](#17-typical-conversation-flows)
18. [Eval Coverage](#18-eval-coverage)

---

## 1. Overview — Three Agents, One Shared Pipeline

The fitout confirmation workflows handle three milestone confirmation sub-categories within the `FIT_OUT_AND_HANDOVER` service category. Each sub-category has its own dedicated `active_agent` identifier and a corresponding `WorkflowConfig` registration, but all three share:

- The same **entry node** (`fitout_confirm_entry_node`)
- The same **validation node** (the shared `validation_node` via WorkflowConfig)
- The same **payload builder node** (`fitout_confirm_payload_builder_node`)
- The same **API submission node** (`fitout_confirm_api_submission_node`)
- The same **confirmation node** (`fitout_confirm_confirmation_node`)

Field branching (which fields are required, which are shown) is driven entirely by `SUBCAT_CONFIG` — a single config map in `fitout_confirm_schema.py`. No agent-specific code paths exist outside the config map.

```
Supervisor intent
    │
    ▼
registry_node ─── FIT_OUT_AND_HANDOVER / FIT_OUT_CONFIRM_FOS  → rdd_fitout_start_agent
                  FIT_OUT_AND_HANDOVER / FIT_OUT_CONFIRM_END  → rdd_fitout_end_agent
                  FIT_OUT_AND_HANDOVER / FIT_OUT_CONFIRM_TRADING → rdd_trading_confirmation_agent
    │
    ▼
fitout_confirm_entry_node ─── stage-pin (CREATE_SR or APPROVE_SR)
    │                         confirmation parsing (yes/no/CANCEL_PENDING)
    │                         action_override handling (confirm, cancel_sr, ...)
    ▼
shared pipeline: field_extraction → merge_state → lease_lookup → validation
    │
    ▼
fitout_confirm_confirmation_node ─── PENDING card
    │
    ▼
fitout_confirm_payload_builder_node ─── CREATE or APPROVE payload
    │
    ▼
fitout_confirm_api_submission_node ─── POST (CREATE_SR) or PATCH (APPROVE_SR)
    │
    ▼
response_generation → save_state
```

---

## 2. Agent Taxonomy

| Agent identifier | `active_agent` value | Sub-category | Service Request type |
|---|---|---|---|
| RDD Fitout Start Agent | `rdd_fitout_start_agent` | `FIT_OUT_CONFIRM_FOS` | Fitout Start Confirmation |
| RDD Fitout End Agent | `rdd_fitout_end_agent` | `FIT_OUT_CONFIRM_END` | Fitout End Confirmation |
| RDD Trading Confirmation Agent | `rdd_trading_confirmation_agent` | `FIT_OUT_CONFIRM_TRADING` | Trading Confirmation |

All three sit under `service_category = "FIT_OUT_AND_HANDOVER"`.

### Supervisor intents

| Sub-category | Create intent | Approve intent |
|---|---|---|
| `FIT_OUT_CONFIRM_FOS` | `CREATE_FITOUT_CONFIRM_FOS` | `APPROVE_FITOUT_CONFIRM_FOS` |
| `FIT_OUT_CONFIRM_END` | `CREATE_FITOUT_CONFIRM_END` | `APPROVE_FITOUT_CONFIRM_END` |
| `FIT_OUT_CONFIRM_TRADING` | `CREATE_FITOUT_CONFIRM_TRADING` | `APPROVE_FITOUT_CONFIRM_TRADING` |

---

## 3. SUBCAT_CONFIG — Radio-Field Yes/No Branching

**File:** `app/agents/schemas/fitout_confirm_schema.py`

`SUBCAT_CONFIG` is the **single source of truth** for all field branching. No sub-category conditionals exist anywhere else in the codebase — every node that needs to know which fields apply reads from this map.

```python
SUBCAT_CONFIG: dict[str, dict[str, Any]] = {
    "FIT_OUT_CONFIRM_FOS": {
        "radio_field":       "fitout_start_done",
        "radio_label":       "Fitout Start Done",
        "actual_date_field": "actual_fitout_start_date",
        "actual_date_label": "Actual Fitout Start Date",
        "planned_date_fields": [
            "planned_fitout_start_date",
            "planned_fitout_end_date",
            "planned_trading_date",
        ],
        "planned_date_labels": {...},
        "prior_milestone_field": "actual_handover_date_readonly",
        "prior_milestone_label": "Actual Handover Date",
        "base_date_fields": [
            "base_fitout_start_date",
            "base_fitout_end_date",
            "base_trading_date",
        ],
        "delay_reason_required": True,
        "has_documents": True,
        "has_comments":  True,
        "approver_role": "DD_ENGINEER",
        "title_prefix":  "Confirm Fitout Start",
    },
    "FIT_OUT_CONFIRM_END": {
        "radio_field":       "fitout_complete_done",
        "radio_label":       "Fitout Complete Done",
        "actual_date_field": "actual_fitout_complete_date",
        "actual_date_label": "Actual Fitout Complete Date",
        "planned_date_fields": [
            "planned_fitout_complete_date",
            "planned_trading_date",
        ],
        "prior_milestone_field": "actual_fitout_start_date_readonly",
        "base_date_fields": ["base_fitout_end_date", "base_trading_date"],
        "delay_reason_required": True,
        "has_documents": True,
        "has_comments":  True,
        "approver_role": "DD_ENGINEER",
        "title_prefix":  "Confirm Fitout End",
    },
    "FIT_OUT_CONFIRM_TRADING": {
        "radio_field":       "trading_done",
        "radio_label":       "Trading Done",
        "actual_date_field": "actual_trading_date",
        "actual_date_label": "Actual Trading Date",
        "planned_date_fields": ["planned_trading_date"],
        "prior_milestone_field": "actual_fitout_end_date_readonly",
        "base_date_fields": ["base_trading_date"],
        "delay_reason_required": True,     # required on NO branch, same as FOS/END
        "has_documents": False,            # TRADING has NO document upload
        "has_comments":  False,            # TRADING has NO comments field
        "approver_role": "MALL_MANAGER",  # also OPERATIONS — see APPROVE_ROLE_MAP
        "title_prefix":  "Confirm Trading",
    },
}
```

### Branching logic at a glance

Every turn the `get_missing_fields()` function reads `SUBCAT_CONFIG[sub_category]` and applies:

```
radio_field always required first
│
├── radio == "yes"  →  require actual_date_field
│                      (planned_date_fields and delay_reason NOT required)
│                      (comments optional if has_comments)
│
├── radio == "no"   →  require planned_date_fields
│                      delay_reason required for all three sub-categories
│                      (actual_date_field NOT required)
│                      (comments optional if has_comments)
│
└── radio not yet answered  →  ask for radio_field only; defer all branch fields
```

### Key differences between sub-categories

| Property | FOS | END | TRADING |
|---|---|---|---|
| Radio field | `fitout_start_done` | `fitout_complete_done` | `trading_done` |
| Actual date (YES branch) | `actual_fitout_start_date` | `actual_fitout_complete_date` | `actual_trading_date` |
| Planned dates (NO branch) | 3 dates | 2 dates | 1 date |
| `delay_reason` | Required | Required | Required |
| Document upload | Yes | Yes | **No** |
| Comments field | Yes | Yes | **No** |
| Approver role | `DD_ENGINEER` | `DD_ENGINEER` | `MALL_MANAGER` / `OPERATIONS` |

---

## 4. Workflow Stages — CREATE_SR and APPROVE_SR

**File:** `app/agents/schemas/fitout_confirm_schema.py`

Unlike the RDD handover agent (3 phases), the fitout confirm agents have exactly **two workflow stages**:

| Stage | Who acts | What happens |
|---|---|---|
| `CREATE_SR` | Tenant / Mall Manager | Collects fields, confirms, submits POST to platform |
| `APPROVE_SR` | Approver (DD_ENGINEER or MALL_MANAGER/OPERATIONS) | Reviews SR, optionally updates fields, confirms, submits PATCH with `status: APPROVED` |

Terminal stages (workflow is over):

| Stage | Meaning |
|---|---|
| `SR_CREATED` | POST succeeded; `sr_id` stored in `backend_refs` |
| `SR_APPROVED` | PATCH with APPROVED succeeded |
| `CANCELLED` | User cancelled the SR |

### Stage-pin logic (entry node)

On the **first turn** for a fitout confirm agent (when `workflow_stage` is `None`):

```
backend_refs.sr_id present? → APPROVE_SR  (user is an approver reviewing existing SR)
intent in APPROVE_INTENTS?  → APPROVE_SR
else                        → CREATE_SR   (user is creating a new SR)
```

### Stale-clear logic

When `intent` is a CREATE intent and `workflow_stage` is already terminal (`SR_CREATED`, `SR_APPROVED`, `CANCELLED`), the entry node resets the workflow to `CREATE_SR` in the same turn, allowing the user to start a new request without a page reload.

---

## 5. Dynamic Required Fields

**File:** `app/agents/schemas/fitout_confirm_schema.py`

### `get_required_fields(stage_name, sub_category)`

Returns all fields that *could* be required for the given stage and sub-category — useful for validation to understand the full potential field set. The actual required subset depends on the radio answer.

```python
# Returns: lease_brand_mall + lease_code + lease_id + radio_field
#          + actual_date_field (if YES branch)
#          + planned_date_fields (if NO branch)
#          + delay_reason (if required)
#          + comments (if has_comments and stage==CREATE_SR)
```

### `get_missing_fields(stage_name, sub_category, collected)`

The runtime function called by the shared validation node every turn. Returns the list of fields still absent from `collected_data`, applying the branching logic described in §3.

**Important:** `lease_brand_mall` is the **lease lookup trigger**. When it is missing, the agent asks for it (and `lease_lookup_node` resolves `lease_code`, `lease_id`, and all other backend-derived fields from it).

### Backend-derived fields (never asked of the user)

```python
BACKEND_DERIVED_FIELDS = frozenset({
    "lease_id",
    "lease_code",          # also user-supplied but overwritten by lookup result
    "company_name",
    "brand_name",
    "mall_name",
    "pms_tenant_code",
    "actual_handover_date_readonly",       # from GET /contracts
    "actual_fitout_start_date_readonly",
    "actual_fitout_end_date_readonly",
    "base_fitout_start_date",
    "base_fitout_end_date",
    "base_trading_date",
    "lease",               # display label
})
```

### Optional fields

```python
OPTIONAL_FIELDS = frozenset({"comments"})
```

`comments` is only applicable for FOS and END (where `has_comments=True`); it is asked but skippable.
`delay_reason` is required for all three sub-categories when `radio == "no"`.

---

## 6. APPROVE_ROLE_MAP — Who Can Approve Each Sub-category

**File:** `app/agents/schemas/fitout_confirm_schema.py`

```python
APPROVE_ROLE_MAP: dict[str, frozenset[str]] = {
    "FIT_OUT_CONFIRM_FOS":     frozenset({"DD_ENGINEER"}),
    "FIT_OUT_CONFIRM_END":     frozenset({"DD_ENGINEER"}),
    "FIT_OUT_CONFIRM_TRADING": frozenset({"MALL_MANAGER", "OPERATIONS"}),
}
```

The helper `role_can_approve(role, sub_category)` is called by `fitout_confirm_api_submission_node` before processing an APPROVE_SR submission. If the caller's role is not in the frozenset for their SR's sub-category, the submission is rejected.

---

## 7. PERMISSION_MAP — Stage-Level Role Gating

```python
PERMISSION_MAP: dict[str, tuple[str, ...]] = {
    "DD_ENGINEER":  ("CREATE_SR", "APPROVE_SR"),   # FOS and END
    "MALL_MANAGER": ("CREATE_SR", "APPROVE_SR"),   # TRADING
    "OPERATIONS":   ("CREATE_SR", "APPROVE_SR"),   # TRADING (shared with MALL_MANAGER)
}
```

`role_can_act_on_stage(role, stage_name)` is used by the shared validation node to produce a blocking validation error when a role attempts a stage they are not permitted to act on.

---

## 8. Entry Node — fitout_confirm_entry_node

**File:** `app/agents/graph/nodes/fitout_confirm/fitout_confirm_entry_node.py`

The entry node is the first node executed on every turn where `active_agent` is one of the three fitout agents. It is a pure routing / state mutation node — it **never calls the LLM** and **never validates form fields**.

### Priority order of checks

```
0. action_override == "confirm" / "confirm_create_sr" / "confirm_approve_sr"
      → confirmation_status = CONFIRMED (pass to payload_builder)

1. action_override == "cancel" (during PENDING confirmation)
      → confirmation_status = REJECTED

2. action_override == "cancel_sr" / "cancel_create_sr"
      → if sr_id present: ask "Are you sure?" (CANCEL_PENDING)
      → if no sr_id: clear all state, return cancelled message

3. action_override == "confirm_cancel_sr"
      → PATCH /service-requests/{sr_id} {status: CANCELLED}
      → clear state, workflow_stage = CANCELLED

4. action_override == "decline_cancel_sr"
      → clear confirmation_status, return "request still active" message

5. action_override == "cancel_update"
      → return "update cancelled" message (no state change)

6. confirmation_status == CANCEL_PENDING  (text-based yes/no handling)
      → "yes" → PATCH cancel + clear state
      → "no"  → clear confirmation_status, keep SR active
      → ambiguous → ask to confirm/deny

7. Text-based cancel phrases ("cancel", "cancel request", ...)
      → same as action_override cancel_sr

8. Text-based workflow-reset phrases ("start over", "restart", ...)
      → clear all state

9. confirmation_status == PENDING  (text-based yes/no)
      → "yes" → CONFIRMED
      → "no"  → REJECTED
      → ambiguous → ask to reply yes/no

10. Stale-clear: CREATE intent + terminal stage → reset to CREATE_SR

11. Stage-pin on first turn (workflow_stage is None)
        → APPROVE_SR if sr_id in backend_refs or intent is APPROVE_*
        → CREATE_SR  otherwise

12. Fall-through → {} (no-op; shared pipeline takes over)
```

### Action overrides

| `action_override` value | Effect |
|---|---|
| `confirm` | Sets `confirmation_status = CONFIRMED` |
| `confirm_create_sr` | Sets `confirmation_status = CONFIRMED` |
| `confirm_approve_sr` | Sets `confirmation_status = CONFIRMED` |
| `cancel` (during PENDING) | Sets `confirmation_status = REJECTED` |
| `cancel_sr` | If sr_id present: asks for cancel confirmation; else clears state |
| `cancel_create_sr` | Same as `cancel_sr` |
| `confirm_cancel_sr` | Cancels the SR on platform (PATCH `status: CANCELLED`) |
| `decline_cancel_sr` | Aborts cancel flow; keeps SR active |
| `cancel_update` | Returns "update cancelled" without state change |

---

## 9. Payload Builder — CREATE_SR and APPROVE_SR Paths

**File:** `app/agents/graph/nodes/fitout_confirm/fitout_confirm_payload_builder_node.py`

The payload builder reads `workflow_stage` to decide which function to call:

| Stage | Function | HTTP method |
|---|---|---|
| `CREATE_SR` | `build_create_fitout_confirm_payload()` | POST `/service-requests` |
| `APPROVE_SR` | `build_approve_fitout_confirm_payload()` | PATCH `/service-requests/{sr_id}` |

### CREATE_SR payload structure

```json
{
  "payload": {
    "lease_brand_mall": "T0028604 - Brand Under Armour - Jawharat Jeddah",
    "lease_code": "T0028604",
    "company_name": "...",
    "brand_name": "...",
    "mall_name": "...",
    "fitout_start_done": "yes",        // radio_field: "yes" or "no"
    "actual_fitout_start_date": "2026-07-15",  // YES branch
    "planned_fitout_start_date": "",   // empty-string when YES branch
    "planned_fitout_end_date": "",
    "planned_trading_date": "",
    "delay_reason": "",                // empty-string when YES branch
    "base_fitout_start_date": "...",
    "base_fitout_end_date": "...",
    "base_trading_date": "...",
    "actual_handover_date_readonly": "...",
    "documents_ids": [],               // FOS/END only; empty for TRADING
    "comments": ""                     // FOS/END only; absent for TRADING
  },
  "title": "Confirm Fitout Start - T0028604",
  "service_category": "FIT_OUT_AND_HANDOVER",
  "sub_category": "FIT_OUT_CONFIRM_FOS",
  "lease_code": "T0028604",
  "lease_id": 95404,
  "service_request_id": ""
}
```

### APPROVE_SR payload structure

Same as CREATE_SR payload with two additional top-level keys:

```json
{
  ...same as CREATE_SR...,
  "status": "APPROVED",          // or "IN_PROCESS" if update_only
  "service_request_id": "SR-2026-00999"
}
```

The `fitout_action` key in `backend_refs` controls approval mode:
- `backend_refs["fitout_action"] != "update_only"` → `status: APPROVED` (default)
- `backend_refs["fitout_action"] == "update_only"` → `status: IN_PROCESS` (save without approving)

### Branch field rules

| radio value | Fields set | Fields empty-stringed |
|---|---|---|
| `"yes"` | `actual_date_field` | all `planned_date_fields`, `delay_reason` |
| `"no"` | all `planned_date_fields`, `delay_reason` (if applicable) | `actual_date_field` |

---

## 10. API Submission Node

**File:** `app/agents/graph/nodes/fitout_confirm/fitout_confirm_api_submission_node.py`

### Submission guards (hard-coded, cannot be bypassed)

```python
# All three must pass independently — no API call is made unless all pass:
if state.get("confirmation_status") != "CONFIRMED":
    return {"status": "WAITING_FOR_USER"}

if any(e.get("blocking") for e in state.get("validation_errors", [])):
    return {"status": "WAITING_FOR_USER"}

if not state.get("backend_refs", {}).get("create_payload"):
    return {"status": "WAITING_FOR_USER"}
```

### Additional APPROVE_SR guard

For APPROVE_SR submissions, the node also calls `role_can_approve(role, sub_category)` from `fitout_confirm_schema`. If the user's role is not authorised to approve the given sub-category, the submission is rejected with a permission error.

### Outcome states

| HTTP result | `workflow_stage` set to |
|---|---|
| POST success (`CREATE_SR`) | `SR_CREATED` |
| PATCH success (`APPROVE_SR`) | `SR_APPROVED` |
| Network / API error | `FAILED` (user can retry) |

---

## 11. Confirmation Node

**File:** `app/agents/graph/nodes/fitout_confirm/fitout_confirm_confirmation_node.py`

Builds the confirmation card UI (`response_ui = {"type": "confirmation_card", ...}`) and sets `confirmation_status = "PENDING"`. The node is shared for both CREATE_SR and APPROVE_SR — the card title and field list adapt based on `workflow_stage` and `sub_category`.

Routing after confirmation node:
- `confirmation_status == "CONFIRMED"` → `fitout_confirm_payload_builder`
- otherwise → `response_generation` (waiting for user input)

---

## 12. WorkflowConfig Registration

**File:** `app/agents/registries/workflow_config.py`

Each of the three fitout agents is registered as a `WorkflowConfig`. All three share the same extraction prompt and field questions (since the field vocabulary is identical — branching is at the `SUBCAT_CONFIG` level, not at the prompt level).

```python
WorkflowConfig(
    agent_name="rdd_fitout_start_agent",      # or rdd_fitout_end_agent / rdd_trading_confirmation_agent
    extraction_prompt=_fitout_confirm_extraction_prompt(),
    field_questions=_fitout_confirm_field_questions(),
    stage_sync_nodes={"APPROVE_SR": "fitout_confirm_entry"},
    collection_stages=frozenset({"CREATE_SR", "APPROVE_SR"}),
    confirmation_nodes={
        "CREATE_SR": "fitout_confirm_confirmation",
        "APPROVE_SR": "fitout_confirm_confirmation",
    },
    terminal_stages=frozenset({"SR_CREATED", "SR_APPROVED", "CANCELLED"}),
    backend_fields=_fitout_confirm_backend_fields(),
    auto_generated_fields=frozenset({"title"}),
    lease_trigger_fields=("lease_code", "company", "lease_brand_mall"),
    action_intents=frozenset({
        "CREATE_FITOUT_CONFIRM_FOS",    # or END / TRADING
        "APPROVE_FITOUT_CONFIRM_FOS",
    }),
)
```

Key `WorkflowConfig` fields explained:

| Field | Value | Why |
|---|---|---|
| `stage_sync_nodes` | `{"APPROVE_SR": "fitout_confirm_entry"}` | When platform status sync resolves to APPROVE_SR, route to entry node (not the generic handover_entry) |
| `collection_stages` | `{"CREATE_SR", "APPROVE_SR"}` | Field collection (extraction → merge → validation loop) runs in **both** stages |
| `confirmation_nodes` | both stages → `fitout_confirm_confirmation` | Same confirmation card for create and approve |
| `auto_generated_fields` | `{"title"}` | Title is built from `build_title(sub_category, pms_tenant_code)` |

---

## 13. Agent Registry Entries

**File:** `app/agents/registry.py`

```python
SERVICE_REQUEST_AGENT_REGISTRY = {
    "FIT_OUT_AND_HANDOVER": {
        "HANDOVER": {
            "agent_name": "rdd_handover_agent",
            "display_name": "RDD Handover Agent",
            "schema_key": "handover_service_request_schema",
        },
        "HANDED_OVER": {
            "agent_name": "rdd_handover_agent",
            "display_name": "RDD Handover Agent",
            "schema_key": "handover_service_request_schema",
        },
        "FIT_OUT_CONFIRM_FOS": {
            "agent_name": "rdd_fitout_start_agent",
            "display_name": "RDD Fitout Start Agent",
            "schema_key": "fitout_confirm_schema",
        },
        "FIT_OUT_CONFIRM_END": {
            "agent_name": "rdd_fitout_end_agent",
            "display_name": "RDD Fitout End Agent",
            "schema_key": "fitout_confirm_schema",
        },
        "FIT_OUT_CONFIRM_TRADING": {
            "agent_name": "rdd_trading_confirmation_agent",
            "display_name": "RDD Trading Confirmation Agent",
            "schema_key": "fitout_confirm_schema",
        },
    },
    "WORK_PERMIT": {
        "WORK_PERMIT": {
            "agent_name": "work_permit_agent",
            "display_name": "Work Permit Agent",
            "schema_key": "work_permit_service_request_schema",
        },
    },
}
```

---

## 14. Node File Inventory

### Fitout confirm nodes (`nodes/fitout_confirm/`)

| File | Purpose |
|---|---|
| `fitout_confirm_entry_node.py` | Entry gate — stage-pin, confirmation parsing, action_override handling, cancel flow |
| `fitout_confirm_validation_node.py` | Re-export shim → delegates to `nodes/shared/validation_node.py` |
| `fitout_confirm_payload_builder_node.py` | Build POST (CREATE_SR) or PATCH (APPROVE_SR) payload from `collected_data` + `SUBCAT_CONFIG` |
| `fitout_confirm_api_submission_node.py` | Submit payload to platform; apply submission hard guards + APPROVE_ROLE_MAP check |
| `fitout_confirm_confirmation_node.py` | Build confirmation card UI; set `confirmation_status = PENDING` |
| `fitout_confirm_missing_field_node.py` | Re-export shim → delegates to `nodes/shared/missing_field_node.py` |

### Shared nodes used by fitout confirm (no changes needed)

| File | Node name | Purpose |
|---|---|---|
| `nodes/shared/field_extraction_node.py` | `field_extraction` | LLM extracts fields using fitout confirm prompt (via WorkflowConfig) |
| `nodes/shared/merge_state_node.py` | `merge_state` | Merge extracted fields into `collected_data`; apply confidence threshold |
| `nodes/shared/lease_lookup_node.py` | `lease_lookup` | Resolve `lease_brand_mall` → backend fields |
| `nodes/shared/validation_node.py` | `validation` | Call `get_missing_fields` from WorkflowConfig; produce blocking/non-blocking errors |
| `nodes/shared/missing_field_node.py` | `missing_field` | Ask exactly one field question per turn |
| `nodes/shared/response_generation_node.py` | `response_generation` | LLM polishes response message |
| `nodes/shared/save_state_node.py` | `save_state` | Checkpoint draft to PostgreSQL |
| `nodes/shared/load_session_node.py` | `load_session` | Restore draft from PostgreSQL |
| `nodes/shared/registry_node.py` | `registry` | `(service_category, sub_category)` → `active_agent` lookup |

### Schema and config files

| File | Purpose |
|---|---|
| `app/agents/schemas/fitout_confirm_schema.py` | `SUBCAT_CONFIG`, `APPROVE_ROLE_MAP`, `PERMISSION_MAP`, `get_required_fields()`, `get_missing_fields()`, `FitoutConfirmExtractedFields` Pydantic model |
| `app/agents/registries/workflow_config.py` | `_build_fitout_start_config()`, `_build_fitout_end_config()`, `_build_trading_confirm_config()` |
| `app/agents/registry.py` | `SERVICE_REQUEST_AGENT_REGISTRY` entries for the three sub-categories |

### Test files

| File | Coverage |
|---|---|
| `tests/unit/test_fitout_confirm_schema.py` | `SUBCAT_CONFIG`, `get_missing_fields`, `APPROVE_ROLE_MAP`, `build_title` |
| `tests/unit/test_fitout_confirm_validation.py` | Validation node with fitout confirm WorkflowConfig |
| `tests/unit/test_fitout_confirm_payload_builder.py` | Payload builder — YES/NO branches, FOS/END/TRADING differences, approve flag |
| `tests/unit/test_workflow_config.py` | WorkflowConfig registration — all 5 agents |

---

## 15. Supervisor Intents & RBAC

The supervisor classifies one of six fitout confirm intents:

```
CREATE_FITOUT_CONFIRM_FOS
APPROVE_FITOUT_CONFIRM_FOS
CREATE_FITOUT_CONFIRM_END
APPROVE_FITOUT_CONFIRM_END
CREATE_FITOUT_CONFIRM_TRADING
APPROVE_FITOUT_CONFIRM_TRADING
```

`ROLE_PERMITTED_INTENTS` in `help_agent_schema.py` controls which roles may trigger each intent. The intents are also listed in `ALL_INTENTS`, which is auto-derived from all `WorkflowConfig.action_intents` registered in `WORKFLOW_CONFIG_REGISTRY`.

---

## 16. Graph Wiring

The three fitout confirm agents share the same entry node (`fitout_confirm_entry`) in `_AGENT_ENTRY_NODES`:

```python
_AGENT_ENTRY_NODES: dict[str, str] = {
    "rdd_handover_agent":           "handover_entry",
    "work_permit_agent":            "work_permit_entry",
    "rdd_fitout_start_agent":       "fitout_confirm_entry",
    "rdd_fitout_end_agent":         "fitout_confirm_entry",
    "rdd_trading_confirmation_agent": "fitout_confirm_entry",
}
```

After `fitout_confirm_entry`, the standard shared pipeline runs (`field_extraction` → `merge_state` → `lease_lookup` → `validation` → `missing_field` / `fitout_confirm_confirmation`). The fitout-specific nodes (`fitout_confirm_payload_builder` and `fitout_confirm_api_submission`) are wired as their own nodes in the graph, separate from the RDD handover submission nodes.

---

## 17. Typical Conversation Flows

### CREATE_SR — FOS (Fitout Start Confirmation), YES branch

```
Turn 1 — User triggers CREATE_FITOUT_CONFIRM_FOS
  User:  "I want to confirm fitout start for lease T0028604"
  Agent: Classifies CREATE_FITOUT_CONFIRM_FOS
         registry → FIT_OUT_CONFIRM_FOS → rdd_fitout_start_agent
         entry_node: no workflow_stage → stage-pin CREATE_SR
         field_extraction: extracts lease_code = "T0028604"
         lease_lookup: resolves all backend fields
  Reply: "Lease found: [Under Armour — Jawharat Jeddah]. Has the fitout started?
          Reply 'yes' if started, or 'no' if not yet started."

Turn 2 — Radio answer (YES branch)
  User:  "Yes, it started on July 15 2026"
  Agent: Extracts fitout_start_done = "yes", actual_fitout_start_date = "2026-07-15"
         Validation: all required fields present (YES branch)
  Reply: [Confirmation card — shows all fields]

Turn 3 — Confirm
  User:  Clicks Confirm (action = "confirm")
  Agent: payload_builder → POST /service-requests
  Reply: "Fitout Start Confirmation SR created: SR-2026-01001."
```

### CREATE_SR — FOS, NO branch (delay scenario)

```
Turn 2 — Radio answer (NO branch)
  User:  "No, fitout hasn't started yet. New planned start: Aug 1, end: Sep 1, trading: Sep 15."
  Agent: Extracts fitout_start_done = "no"
         planned_fitout_start_date = "2026-08-01"
         planned_fitout_end_date   = "2026-09-01"
         planned_trading_date      = "2026-09-15"
  Reply: "Please provide the reason for the delay."

Turn 3 — Delay reason
  User:  "Contractor was delayed due to material shortage."
  Agent: Extracts delay_reason; asks for comments (optional)

Turn 4 — Comments (optional)
  User:  "No comments."
  Agent: All fields collected → confirmation card shown
```

### APPROVE_SR — TRADING

```
Turn 1 — Approver opens SR
  Request: {sr_id: "SR-2026-01002", message: ""}
  Agent:   sr_status_sync → TRADING sub_category detected
           entry_node: backend_refs.sr_id present → stage-pin APPROVE_SR
           Loads existing collected_data from draft
  Reply:   "SR SR-2026-01002 (Trading Confirmation) is pending your approval.
            Please confirm: has trading started? (yes/no)"

Turn 2 — Review / update + approve
  User:  Clicks "Update and Approve" (action = "confirm_approve_sr")
  Agent: payload_builder → build_approve_fitout_confirm_payload (status: APPROVED)
         api_submission → PATCH /service-requests/SR-2026-01002
  Reply: "SR SR-2026-01002 approved. Trading Confirmation complete."
```

---

## 18. Eval Coverage

Fitout confirm scenarios live in `tests/eval/scenarios.py` under **Tier 6** sub-tiers (separate from the handover lifecycle Tier 4/5/6):

| Sub-tier | List constant | Scenarios | Coverage |
|---|---|---|---|
| 6A | `TIER6A_FOS_HAPPY` | `_S73` | FOS YES branch happy path |
| 6B | `TIER6B_FOS_EDGE` | `_S70`, `_S75`, `_S78`, `_S82` | FOS NO branch, edge cases |
| 6C | `TIER6C_FOS_BATTLE` | `_S79`, `_S81` | FOS battle tests |
| 6E | `TIER6E_END_HAPPY` | `_S83` | END YES branch happy path |
| 6F | `TIER6F_END_EDGE` | `_S71`, `_S77` | END NO branch, edge cases |
| 6H | `TIER6H_TRADING_HAPPY` | `_S74` | TRADING YES branch happy path |
| 6I | `TIER6I_TRADING_EDGE` | `_S72`, `_S76` | TRADING NO branch, edge cases |
| 6J | `TIER6J_TRADING_BATTLE` | `_S80` | TRADING battle test |

All fitout confirm scenarios are grouped in `FITOUT_ALL_SCENARIOS` and also included in Tier 8 (`TIER8_SCENARIOS`).

Run fitout confirm scenarios:

```bash
cd backend
# All fitout confirm scenarios (Tier 8 in the shell script)
./run_agent_eval.sh --no-start --tier 8

# Or directly:
python -m tests.eval.run_eval --scenarios 70,71,72,73,74,75,76,77,78,79,80,81,82,83 --verbose
```
