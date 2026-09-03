# RDD Agent — Technical Reference

> **Agent identifier:** `rdd_handover_agent`
> **Lifecycle:** RDD Handover Service Request — Path A (three sequential phases) and Path B (Already Trading fast-path)
> **Entry point:** Activated by the Help Agent supervisor when intent is `CREATE_RDD_SERVICE_REQUEST`, `UPDATE_RDD_SERVICE_REQUEST`, `APPROVE_RDD_SERVICE_REQUEST`, or `CREATE_HANDED_OVER_SR`
>
> **Related docs:**
> - [`help-agent-pipeline.md`](help-agent-pipeline.md) — Help Agent (supervisor + FAQ) that routes to this agent
> - [`production-readiness.md`](production-readiness.md) — Production gap assessment

---

## Table of Contents

1. [Overview](#1-overview)
2. [Lifecycle Architecture](#2-lifecycle-architecture)
3. [Phase 0 — CREATE_HANDED_OVER_SR (Path B — Already Trading)](#3-phase-0--create_handed_over_sr-path-b--already-trading)
4. [Phase 1 — CREATE_SR (Mall Manager)](#4-phase-1--create_sr-mall-manager)
5. [Phase 2 — FM_REVIEW (FM Manager / Operations)](#5-phase-2--fm_review-fm-manager--operations)
6. [Phase 3 — RDD_REVIEW (DD Engineer)](#6-phase-3--rdd_review-dd-engineer)
7. [Shared Pipeline Nodes](#7-shared-pipeline-nodes)
8. [WorkflowConfig](#8-workflowconfig)
9. [RBAC — Role Permissions](#9-rbac--role-permissions)
10. [State Schema](#10-state-schema)
11. [File Structure](#11-file-structure)
12. [Adding a New Phase (Extension Guide)](#12-adding-a-new-phase-extension-guide)

---

## 1. Overview

The **RDD Agent** handles the complete Handover Service Request lifecycle for the Cenomi Mall Management Platform. It is a single LangGraph agent (`rdd_handover_agent`) that owns two distinct sub-flows:

- **Path A — `HANDOVER` (standard):** Three sequential workflow phases from SR creation through final approval (Mall Manager → FM Manager → DD Engineer).
- **Path B — `HANDED_OVER` (Already Trading fast-path):** Single-phase SR creation where the SR is automatically approved in the same transaction. No FM or RDD review steps.

### Core design principles

| Principle | Implementation |
|-----------|---------------|
| **One agent, one lifecycle** | `rdd_handover_agent` stays active in `chat_sessions.active_agent` for the entire lifecycle across all three phases |
| **Sequential phases enforced by platform** | `sr_status_sync_node` reads the live platform SR status each turn and routes to the correct phase entry node |
| **LLM extracts, code validates** | Field extraction is LLM-assisted; all validation, date constraints, and submission logic is deterministic Python |
| **Per-phase confirmation** | Each phase has its own confirmation card before API submission |
| **Shared pipeline** | `field_extraction → merge_state → lease_lookup → validation → missing_field` is identical across all phases |
| **HITL at every stage** | No automatic submission; every phase requires explicit user confirmation |

### Roles involved

| Role | Phase | Action |
|------|-------|--------|
| `MALL_MANAGER` | CREATE_SR (Path A) | Creates the SR, provides inspection details |
| `MALL_MANAGER` | CREATE_HANDED_OVER_SR (Path B) | Creates Already Trading SR — auto-approved immediately |
| `FM_MANAGER` or `OPERATIONS` | FM_REVIEW (Path A only) | Reviews SR, uploads FM documents, sets handover dates |
| `DD_ENGINEER` | RDD_REVIEW (Path A only) | Uploads handover report, submits final RDD review |

---

## 2. Lifecycle Architecture

### 2.1 High-Level Design

The diagram below shows the two paths, the actors, the key state transitions, and where the MSP Platform API is called. Internal pipeline nodes (field extraction, merge, validation, etc.) are collapsed into "Collect + Validate" blocks for clarity.

```mermaid
flowchart TB
    subgraph actors ["Actors"]
        direction LR
        MM_a["Mall Manager"]
        FM_a["FM Manager\nor Operations"]
        RDD_a["DD Engineer"]
    end

    subgraph help_layer ["Help Agent Layer (per-turn shared infra)"]
        direction LR
        SUP["Supervisor\nLLM intent classification\nRBAC enforcement"]
        SYNC["sr_status_sync\nGET platform SR\nMap status → workflow_stage"]
        FAQ_node["faq_node\nRAG Q&A"]
    end

    subgraph rdd_block ["RDD Agent"]
        direction TB

        subgraph pathB ["Path B — HANDED_OVER  (Already Trading fast-path)"]
            direction LR
            B_collect["Collect + Validate\n4 actual dates · lease · title"]
            B_confirm["Confirmation card\n(4 actual dates)"]
            B_submit["POST /service-requests\nsub_category=HANDED_OVER\nalready_handovered=already_trading"]
            B_done(["HANDED_OVER_COMPLETED\nSR auto-APPROVED\nworkflow_complete=true"])
            B_collect --> B_confirm --> B_submit --> B_done
        end

        subgraph pathA ["Path A — HANDOVER  (Standard multi-level)"]
            direction TB

            subgraph phase1 ["Phase 1 — CREATE_SR  (Mall Manager)"]
                direction LR
                A1_collect["Collect + Validate\ntenant · lease · dates\ninspection_done_by · title"]
                A1_confirm["Confirmation card\n(17 fields)"]
                A1_submit["POST /service-requests\nsub_category=HANDOVER\nalready_handovered=initiate_handover"]
                A1_done(["SR_CREATED\nMM: FINISHED\nFM: IN_PROGRESS"])
                A1_collect --> A1_confirm --> A1_submit --> A1_done
            end

            subgraph phase2 ["Phase 2 — FM_REVIEW  (FM Manager / Operations)"]
                direction LR
                A2_collect["Upload docs\nSR_HANDOVER_CHECKLIST\nSR_HANDOVER_SITE_SURVEY\nSR_COP_CHECKLIST_OTHER\nSet unit_readiness_date"]
                A2_confirm["FM confirmation card\n(dates + docs)"]
                A2_save["PATCH status=IN_PROCESS\n(save progress)"]
                A2_approve["PATCH status=APPROVED\n(FM approve)"]
                A2_done(["FM: FINISHED\nRDD: IN_PROGRESS\nstage_complete=true"])
                A2_collect --> A2_confirm
                A2_confirm -->|"save"| A2_save
                A2_confirm -->|"approve"| A2_approve
                A2_approve --> A2_done
            end

            subgraph phase3 ["Phase 3 — RDD_REVIEW  (DD Engineer)"]
                direction LR
                A3_collect["Upload DR_SR_HANDOVER_REPORT\nProvide 4 actual dates\nactual_handover · fitout_start\nfitout_end · trading_date"]
                A3_submit["POST status=REPORT_SUBMITTED"]
                A3_approve["PATCH status=APPROVED\n(final approve)"]
                A3_done(["SR_COMPLETED\nworkflow_complete=true"])
                A3_collect --> A3_submit --> A3_approve --> A3_done
            end

            A1_done -->|"Platform notifies FM Manager"| phase2
            A2_done -->|"Platform notifies DD Engineer"| phase3
        end
    end

    MSP_API[/"MSP Platform API\nPOST · PATCH · GET\n/service-requests"/]

    MM_a -->|"message / action"| SUP
    FM_a -->|"message / action"| SUP
    RDD_a -->|"message / action"| SUP

    SUP -->|"ASK_HELP / UNKNOWN"| FAQ_node
    SUP -->|"sr_id present or extracted"| SYNC
    SUP -->|"SR action intent, RBAC ok"| SYNC

    SYNC -->|"sub_category=HANDED_OVER"| pathB
    SYNC -->|"CREATE_SR stage"| phase1
    SYNC -->|"FM_REVIEW stage"| phase2
    SYNC -->|"RDD_REVIEW stage"| phase3

    B_submit --> MSP_API
    A1_submit --> MSP_API
    A2_save --> MSP_API
    A2_approve --> MSP_API
    A3_submit --> MSP_API
    A3_approve --> MSP_API
```

### 2.2 Detailed Node Graph

The diagram below reflects the actual compiled LangGraph (`help_agent_graph.py`). Every chat turn starts at `load_session` and always ends at `save_state → END`. The RDD agent nodes are grouped inside the dashed boundary; the supervisor, sr_status_sync, and response layer are shared infrastructure.

```mermaid
flowchart TD
    START([Start]) --> load_session["load_session\nRestore chat session from DB"]
    load_session --> doc_upload["document_upload\nPartition uploaded docs\ninto FM / RDD buckets"]

    doc_upload -->|"sr_id present, non-cancel"| sr_sync["sr_status_sync\nGET platform SR status\nMap → workflow_stage"]
    doc_upload -->|"cancel action + sr_id"| h_entry
    doc_upload -->|"active_agent set, no sr_id"| h_entry
    doc_upload -->|"no sr_id, no agent"| sup

    sr_sync -->|"FM_REVIEW"| fm_entry
    sr_sync -->|"RDD_REVIEW"| rdd_entry
    sr_sync -->|"CREATE_SR or CREATE_HANDED_OVER_SR"| h_entry
    sr_sync -->|"terminal stage"| sup
    sr_sync -->|"sync error"| resp_gen

    sup["supervisor\nLLM intent classification\nRBAC enforcement"] -->|"sr_id extracted in message"| sr_sync
    sup -->|"ASK_HELP / UNKNOWN"| faq["faq_node\nRAG Q&A answer"]
    sup -->|"PREVIEW_SR / CHECK_STATUS"| preview["preview\nShow SR draft or status"]
    sup -->|"SR action intent, RBAC ok"| registry["registry\nResolve agent\nSet active_agent"]
    sup -->|"RBAC denial / parse fail"| resp_gen

    faq --> save_state
    preview --> resp_gen
    registry --> h_entry

    subgraph rdd_agent ["RDD Agent (rdd_agent)"]

        subgraph entry_nodes ["Stage Entry Nodes"]
            h_entry["handover_entry\nHITL: confirm / cancel / MM upload gate\nPath A vs B pin on first turn"]
            fm_entry["fm_review_entry\nFM_MANAGER or OPERATIONS role gate\nfm_action detection"]
            rdd_entry["rdd_review_entry\nDD_ENGINEER role gate\nrdd_action / REPORT_SUBMITTED detection"]
        end

        subgraph pipeline ["Shared Data Pipeline — runs for every collection stage"]
            fe["field_extraction\nLLM extracts fields (confidence ≥ 0.6)"]
            merge["merge_state\nMerge into collected_data\nAuto-compute title / expected dates"]
            lease["lease_lookup\nGET /leases\nResolve 10 backend fields"]
            val["validation\nDate chain · enum · required\ndoc count checks"]
            mf["missing_field\nAsk exactly one field per turn"]
        end

        subgraph phase_create ["CREATE phase — Path A and Path B share these nodes"]
            conf["confirmation\nShow draft card\nCREATE_SR and CREATE_HANDED_OVER_SR"]
            pb["payload_builder\nPath A: build HANDOVER body\nPath B: build HANDED_OVER body"]
            api_sub["api_submission\nPOST /service-requests\nPath A → stage=SR_CREATED\nPath B → stage=HANDED_OVER_COMPLETED + workflow_complete"]
        end

        subgraph phase_fm ["FM_REVIEW phase — Path A only"]
            fm_conf["fm_confirmation\nShow FM draft card\n(unit_readiness_date + docs)"]
            fm_pb["fm_payload_builder\nBuild save-progress PATCH\nor approve PATCH"]
            fm_api["fm_api_submission\nPATCH /service-requests/:id\nstage_complete=true on approve"]
        end

        subgraph phase_rdd ["RDD_REVIEW phase — Path A only"]
            rdd_conf["rdd_confirmation\nShow RDD dates + document card"]
            rdd_pb["rdd_payload_builder\nBuild submit-report POST\nor final-approve PATCH"]
            rdd_api["rdd_api_submission\nPOST status=REPORT_SUBMITTED\nor PATCH status=APPROVED\nSR_COMPLETED on final approve"]
        end

    end

    h_entry -->|"WAITING_FOR_USER\nMM upload offer or cancel pending"| resp_gen
    h_entry -->|"action=cancel"| merge
    h_entry -->|"proceed"| fe

    fm_entry -->|"WAITING_FOR_USER"| resp_gen
    fm_entry -->|"fm_action or CONFIRMED"| merge
    fm_entry -->|"proceed"| fe

    rdd_entry -->|"WAITING_FOR_USER or REPORT_SUBMITTED idle"| resp_gen
    rdd_entry -->|"rdd_action or CONFIRMED"| merge
    rdd_entry -->|"proceed"| fe

    fe --> merge
    merge -->|"no lease_id or selected_lease"| lease
    merge -->|"FM or RDD stage (lease already resolved)"| val
    lease -->|"resolved"| val
    lease -->|"WAITING_FOR_USER (0 or N matches)"| resp_gen

    val -->|"blocking errors"| mf
    mf --> resp_gen

    val -->|"CREATE_SR or CREATE_HANDED_OVER_SR — all valid"| conf
    val -->|"FM_REVIEW — all valid"| fm_conf
    val -->|"RDD_REVIEW — all valid"| rdd_conf

    conf -->|"CONFIRMED"| pb
    conf -->|"not confirmed"| resp_gen
    pb --> api_sub
    api_sub --> resp_gen

    fm_conf -->|"CONFIRMED"| fm_pb
    fm_conf -->|"not confirmed"| resp_gen
    fm_pb --> fm_api
    fm_api --> resp_gen

    rdd_conf -->|"CONFIRMED"| rdd_pb
    rdd_conf -->|"not confirmed"| resp_gen
    rdd_pb --> rdd_api
    rdd_api --> resp_gen

    resp_gen["response_generation\nLLM formats reply + builds UI JSON payload"] --> save_state["save_state\nPersist graph state to DB"]
    save_state --> END_NODE([End])
```

### 2.3 Phase sequencing — Path A (standard HANDOVER)

The platform's own status machine drives phase transitions:

1. Mall Manager creates SR → platform status becomes `FM_REVIEW`
2. `sr_status_sync_node` detects `FM_REVIEW` → routes to `fm_review_entry_node`
3. FM Manager approves → platform status becomes `RDD_REVIEW`
4. `sr_status_sync_node` detects `RDD_REVIEW` → routes to `rdd_review_entry_node`
5. DD Engineer submits → platform status becomes `SR_COMPLETED`

No code-side sequence enforcement is needed — the platform API is the source of truth.

### 2.4 Phase sequencing — Path B (HANDED_OVER / Already Trading)

Path B has no FM or RDD phases. The entire lifecycle is one agent turn:

1. Mall Manager provides 4 actual dates for an already-trading unit
2. `api_submission_node` POSTs with `sub_category = "HANDED_OVER"` and `already_handovered = "already_trading"`
3. Backend auto-approves the SR in the same transaction — status immediately becomes `APPROVED`
4. Agent emits `workflow_complete: true`, `workflow_stage: "HANDED_OVER_COMPLETED"`

No `sr_status_sync_node` routing to FM or RDD occurs for Path B.

---

## 3. Phase 0 — CREATE_HANDED_OVER_SR (Path B — Already Trading)

### Trigger

Intent `CREATE_HANDED_OVER_SR` classified by the supervisor → registry resolves `rdd_handover_agent` → `handover_entry_node`. The entry node detects `already_handovered in ("already_trading", "already_handovered")` or `sub_category == "HANDED_OVER"` and routes the entire session to the Path B collection flow.

### Entry node — `handover_entry_node.py`

Handles:
- `already_handovered` / `sub_category` pin on first turn — sets `workflow_stage = "CREATE_HANDED_OVER_SR"`
- Stale-clear when `workflow_stage = "HANDED_OVER_COMPLETED"` (terminal) before starting a new collection
- Standard confirmation / cancel gates

### Required fields (Path B)

| Field | Source | Description |
|-------|--------|-------------|
| `tenant_profile_id` | Backend (lease lookup) | Tenant profile identifier |
| `property_id` | Backend (lease lookup) | Mall property identifier |
| `lease_code` | User → Backend | Lease code (e.g. `t0105712`) |
| `lease_id` | Backend (lease lookup) | Contract/lease UUID |
| `brand_id` | Backend (lease lookup) | Brand identifier |
| `mall` | Backend (lease lookup) | Mall display name |
| `brand` | Backend (lease lookup) | Brand display name |
| `title` | User (optional) or auto-generated | Defaults to `Already Trading - {lease_code}` |
| `actual_shop_handover_date` | User | `YYYY-MM-DD` — when the shop was physically handed over |
| `actual_fitout_start_date` | User | `YYYY-MM-DD` — when tenant fit-out work started |
| `actual_fitout_end_date` | User | `YYYY-MM-DD` — when tenant fit-out work ended |
| `actual_trading_date` | User | `YYYY-MM-DD` — when the unit started trading |

**No** `description`, `startDate`, `endDate`, `inspection_done_by`, or `guideLineLink`.

### Validation rules (Path B)

| Rule | Blocking |
|------|---------|
| All 4 actual-date fields present and non-empty | Yes |
| `actual_shop_handover_date` ≤ `actual_fitout_start_date` ≤ `actual_fitout_end_date` ≤ `actual_trading_date` | Yes |
| `lease_id` confirmed before submission | Yes |

### Confirmation card (Path B)

Shows the 4 actual dates only. No Path A inspection fields. `confirmAction = "confirm"`.

### Submission

`api_submission_node.py` → `POST /service-requests` with:
- `sub_category = "HANDED_OVER"`
- `already_handovered = "already_trading"`
- 4 actual dates in `YYYY-MM-DD` format

### Success response

```python
{
  "workflow_stage": "HANDED_OVER_COMPLETED",
  "workflow_complete": True,
  "message": "Your Already Trading SR has been created and automatically approved — reference {sr_id}. No further FM or RDD review steps are required."
}
```

The SR is immediately `APPROVED` on the platform. No MM optional upload offer is shown.

### Conversation flow (3–5 turns)

```
Turn 1: "already trading for t0105712"
  → Extracts lease="t0105712", already_handovered="already_trading"
  → Lease lookup auto-fills 8 backend fields
  → Reply: "Found your lease. Please provide the 4 actual dates."

Turn 2–3: Collects actual_shop_handover_date, actual_fitout_start_date,
           actual_fitout_end_date, actual_trading_date

Turn 4: Shows confirmation card (4 dates + title)
  → User: "Confirm"

Turn 5: Submits SR → auto-approved → returns sr_id
  → workflow_complete: true, workflow_stage: HANDED_OVER_COMPLETED
```

---

## 4. Phase 1 — CREATE_SR (Mall Manager)

### Trigger

Intent `CREATE_RDD_SERVICE_REQUEST` classified by the supervisor → registry resolves `rdd_handover_agent` → `handover_entry_node`.

### Entry node — `handover_entry_node.py`

Handles:
- Explicit UI `action_override` (`confirm`, `cancel`, `upload_document`, `cancel_update`)
- Workflow cancel/restart phrases → clears all state
- Confirmation card response parsing (`yes` / `no` phrases)

### Lease suggestion picker

When CREATE_SR has no lease identifiers yet, `lease_lookup_node` returns
`IN_PROGRESS` with a `resource_picker` decoration instead of only asking for the
code. Routing continues to `validation` → `missing_field`, so the user gets the
lease-code question *and* selectable chips.

Emitted by `build_create_lease_picker()`
(`app/agents/services/resource_picker_decorations.py`):

```json
{
  "suggestions": {
    "kind": "resource_picker",
    "resource": "leases",
    "filters": { "limit": 5, "offset": 0, "sort_by": "activation_date", "order": "desc" },
    "selection": { "mode": "select_lease" },
    "searchable": true
  }
}
```

The backend emits the spec only — it never fetches the leases. The frontend
registry (`libs/api/src/lib/srChatbot/resourcePickerRegistry.ts`) calls the
platform `GET /leases` list mode directly with `limit`, `offset`, `sort_by`,
`order` and the typed `search` term, and renders chips + a read-more pager.

| Concern | Owner | Notes |
|---------|-------|-------|
| Whether to show the picker | This backend | `should_browse_leases()`, gated by `HANDOVER_LEASE_PICKER_ENABLED` (default **on**) |
| Page size / ordering | This backend | `filters` in the spec above |
| Fetching + paging + search UI | Frontend | `ResourcePicker.tsx`, 300 ms debounce, resets to offset 0 per term |
| Eligibility, ordering, `search` matching | Platform (`cenomi-api`) | `FetchHandoverEligibleLeasesList`, ordered `COALESCE(pms_lease_activation_date, start_date) DESC` |

Because all three own a slice, a change to `sort_by`/`search` semantics needs the
platform released **before or with** the frontend — the frontend calls
`GET /leases` directly, so an unrecognised param is ignored silently rather than
erroring.

Only `rdd_handover_agent` gets this picker. The Fitout Start / End / Trading
agents use purpose-scoped lookups (`purpose=fitout_confirm` etc.) that
deliberately include leases which already have a handover SR, and the list query
hardcodes `has_handover_sr IS NULL` — so it cannot be reused for them as-is.

### Required fields (17)

| Field | Source | Description |
|-------|--------|-------------|
| `tenant_profile_id` | Backend (lease lookup) | Tenant profile identifier |
| `property_id` | Backend (lease lookup) | Mall property identifier |
| `lease_code` | User → Backend | Lease code (e.g. `T0028604`) |
| `lease_id` | Backend (lease lookup) | Contract/lease UUID |
| `brand_id` | Backend (lease lookup) | Brand identifier |
| `mall` | Backend (lease lookup) | Mall display name |
| `brand` | Backend (lease lookup) | Brand display name |
| `lease` | Backend (lease lookup) | Lease display label |
| `unit_codes` | Backend (lease lookup) | Unit code list |
| `city` | Backend (lease lookup) | City |
| `contracted_area` | Backend (lease lookup) | Area in sqm |
| `title` | User (optional) or auto-generated | `handover-{lease_code}-{description_slug}` |
| `description` | User | Purpose of handover inspection |
| `startDate` | User | Inspection start date (YYYY-MM-DD) |
| `endDate` | User | Inspection end date (must be after startDate) |
| `inspection_done_by` | User | `FM_MANAGER` or `OPERATIONS` |
| `comments` | User (optional) | Additional notes |

**Backend-derived fields** (10): resolved automatically from the Lease-Tenant API — never asked of the user.
**Auto-generated fields**: `title` is generated from `lease_code` + first 5 words of `description` if the user does not provide one.

### Validation rules

| Rule | Blocking |
|------|---------|
| All required fields present and non-empty | Yes |
| `inspection_done_by` ∈ `{FM_MANAGER, OPERATIONS}` | Yes |
| `startDate` < `endDate` (strict — equal dates rejected) | Yes |
| Past dates rejected | Yes |
| `lease_id` confirmed before submission | Yes |

### Submission

`api_submission_node.py` → `POST /service-requests` (platform API). Returns `sr_id` (e.g. `SR-2026-00741`) stored in `backend_refs.sr_id`.

### Conversation flow (5–8 turns)

```
Turn 1: "Create a handover SR for Under Armour"
  → Extracts brand="Under Armour"
  → Lease lookup: T0028604 at Jawharat Jeddah (auto-fills 10 backend fields)
  → Reply: "Found your lease. What is the purpose of this handover?"

Turn 2–4: Collects description, startDate, endDate, inspection_done_by, comments

Turn 5: Shows confirmation card (all 17 fields)
  → User: "Confirm"

Turn 6: Submits SR → returns SR-2026-00741
```

---

## 5. Phase 2 — FM_REVIEW (FM Manager / Operations)

### Trigger

FM Manager or Operations opens the SR from their notification or SR list. The frontend sends `sr_id` in the request body → `sr_status_sync_node` reads platform status `FM_REVIEW` → routes to `fm_review_entry_node`.

### Entry node — `fm_review_entry_node.py`

Handles:
- Role guard: only `FM_MANAGER` or `OPERATIONS` may act
- `fm_action` detection in `backend_refs` (save-progress vs approve actions)
- Document upload coordination

### Required fields (2)

| Field | Source | Description |
|-------|--------|-------------|
| `unit_readiness_date` | FM Manager | Date the unit is ready for handover (YYYY-MM-DD) |
| `expected_handover_date` | Auto-computed | `unit_readiness_date + 7 days` (computed by `merge_state_node`) |

### Required documents (3)

| Document type | Description |
|---------------|-------------|
| `SR_HANDOVER_CHECKLIST` | SR Handover Checklist (PDF/JPEG/PNG) |
| `SR_HANDOVER_SITE_SURVEY` | SR Handover Site Survey (PDF/JPEG/PNG) |
| `SR_COP_CHECKLIST_OTHER` | COP Checklist / Other document |

Documents are uploaded via `POST /api/v1/upload` with `document_type` form field. The upload route checks `CAN_FM_REVIEW_HANDOVER_SR` permission.

### Actions

| Action | Description |
|--------|-------------|
| Save progress | `fm_action=save` — saves collected data, does not advance the lifecycle |
| Approve | `fm_action=approve` — submits FM review, advances SR to `RDD_REVIEW` |
| Reject | `fm_action=reject` — returns SR to Mall Manager for corrections |

### Submission

`fm_api_submission_node.py` → `PATCH /service-requests/{sr_id}` (platform API).

### Conversation flow (2–4 turns)

```
FM Manager opens SR from notification
  → sr_status_sync detects FM_REVIEW → routes to fm_review_entry

Turn 1: "What dates do I need to provide?"
  → Explains unit_readiness_date; expected_handover_date is auto-computed

Turn 2: "Unit ready 2026-07-15"
  → unit_readiness_date=2026-07-15, expected_handover_date=2026-07-22 (auto)

Turn 3: FM Manager uploads 3 documents via upload interface

Turn 4: Confirmation card → Approve
  → FM review submitted → SR advances to RDD_REVIEW
```

---

## 6. Phase 3 — RDD_REVIEW (DD Engineer)

### Trigger

DD Engineer opens SR from notification. Frontend sends `sr_id` → `sr_status_sync_node` reads platform status `RDD_REVIEW` → routes to `rdd_review_entry_node`.

### Entry node — `rdd_review_entry_node.py`

Handles:
- Role guard: only `DD_ENGINEER` may act
- `rdd_action` detection in `backend_refs` (submit vs final-approve)
- Document upload coordination

### Required fields (5)

| Field | Source | Description |
|-------|--------|-------------|
| `guideLineLink` | DD Engineer | URL to the handover guideline document |
| `actual_handover_date` | DD Engineer | Date the unit was actually handed over |
| `fitout_start_date` | DD Engineer | Fit-out works start date |
| `fitout_end_date` | DD Engineer | Fit-out works end date |
| `trading_date` | DD Engineer | Date the unit starts trading |

### Date chain constraint

All four dates must satisfy this strict ordering:
```
actual_handover_date ≤ fitout_start_date ≤ fitout_end_date ≤ trading_date
```
Violation of this constraint is a blocking validation error.

### Required documents (1)

| Document type | Description |
|---------------|-------------|
| `DR_SR_HANDOVER_REPORT` | Final RDD Handover Report (PDF/JPEG/PNG) — uploaded with `APPROVED` status flag |

The upload route checks `CAN_RDD_REVIEW_HANDOVER_SR` permission. The `DR_SR_HANDOVER_REPORT` type automatically receives `document_type_status=APPROVED` on upload.

### Submission

`rdd_api_submission_node.py` → `PATCH /service-requests/{sr_id}` (platform API). On success the SR status becomes `SR_COMPLETED` and the lifecycle ends.

### Conversation flow (2–4 turns)

```
DD Engineer opens SR from notification
  → sr_status_sync detects RDD_REVIEW → routes to rdd_review_entry

Turn 1: "What do I need to provide?"
  → Explains 5 date fields and the handover report

Turn 2–3: Collects dates, validates date chain constraint

Turn 4: Uploads DR_SR_HANDOVER_REPORT → Confirmation card → Submit
  → SR_COMPLETED
```

---

## 7. Shared Pipeline Nodes

These nodes run identically for all three phases. They sit between the phase entry node and the phase-specific confirmation node.

```mermaid
flowchart LR
    ENTRY["Phase entry node\n(handover / fm / rdd)"]
    FE["field_extraction_node\nLLM extracts fields\nwith confidence scores"]
    MS["merge_state_node\nMerge into collected_data\nAuto-compute expected_handover_date"]
    LL{"lease resolved?"}
    LLU["lease_lookup_node\nGET /leases\n0 / 1 / N matches"]
    VAL{"valid + complete?"}
    MF["missing_field_node\nAsk one question at a time"]
    CONF["Phase confirmation node"]

    ENTRY --> FE --> MS --> LL
    LL -->|"no lease_id"| LLU --> VAL
    LL -->|"lease_id present"| VAL
    VAL -->|"missing or invalid"| MF --> FE
    VAL -->|"all fields present and valid"| CONF
```

| Node | File | Responsibility |
|------|------|----------------|
| `field_extraction_node` | `handover/field_extraction_node.py` | LLM extracts field values with per-field confidence (≥ 0.6 threshold) |
| `merge_state_node` | `handover/merge_state_node.py` | Merges extracted fields into `collected_data`; auto-computes `expected_handover_date`; protects backend-derived fields |
| `lease_lookup_node` | `handover/lease_lookup_node.py` | Calls Lease-Tenant API; resolves 10 backend fields; handles 0/1/N match cases |
| `validation_node` | `handover/validation_node.py` | Validates required fields, date ordering, enum constraints |
| `missing_field_node` | `handover/missing_field_node.py` | Asks exactly one missing field per turn using `WorkflowConfig.field_questions` |

---

## 8. WorkflowConfig

The `rdd_handover_agent`'s entire configuration is declared in one `WorkflowConfig` object registered in `WORKFLOW_CONFIG_REGISTRY`.

```python
# app/agents/registries/workflow_config.py

WorkflowConfig(
    agent_name="rdd_handover_agent",
    extraction_prompt=HANDOVER_EXTRACTION_SYSTEM_PROMPT,
    field_questions=HANDOVER_FIELD_QUESTIONS,       # field → question string per phase

    # Phase routing: workflow_stage → graph node name
    stage_sync_nodes={
        "FM_REVIEW":  "fm_review_entry",
        "RDD_REVIEW": "rdd_review_entry",
        # CREATE_SR has no sync entry — it is the default entry via _AGENT_ENTRY_NODES
    },

    # Phases in which field collection runs
    collection_stages=frozenset({"CREATE_SR", "FM_REVIEW", "RDD_REVIEW", "CREATE_HANDED_OVER_SR"}),

    # Confirmation node per phase
    confirmation_nodes={
        "CREATE_SR":  "confirmation",
        "FM_REVIEW":  "fm_confirmation",
        "RDD_REVIEW": "rdd_confirmation",
    },

    # Stages that end the lifecycle (supervisor gets control again)
    terminal_stages=frozenset({"SR_CREATED", "SR_COMPLETED", "HANDED_OVER_COMPLETED"}),

    # Fields resolved by backend APIs — never asked of the user
    backend_fields=frozenset({
        "tenant_profile_id", "property_id", "brand_id", "lease_id",
        "contract_id", "unit_codes", "city", "contracted_area",
        "lease_brand_mall", "lease",
    }),

    # Fields auto-computed by merge_state_node
    auto_generated_fields=frozenset({"title", "expected_handover_date"}),

    # Fields to ask when backend fields are unresolved
    lease_trigger_fields=("lease_code", "lease_brand_mall"),

    # Supervisor intents that activate this agent
    action_intents=frozenset({
        "CREATE_RDD_SERVICE_REQUEST",
        "UPDATE_RDD_SERVICE_REQUEST",
        "APPROVE_RDD_SERVICE_REQUEST",
        "CREATE_HANDED_OVER_SR",          # Path B — Already Trading fast-path
    }),
)
```

Shared nodes (`field_extraction_node`, `missing_field_node`, `_route_after_sync`, `_route_after_validation`) read from this config at runtime — no hard-coded phase names in shared code.

---

## 9. RBAC — Role Permissions

| Role | Permitted SR intents | Phase access |
|------|---------------------|-------------|
| `MALL_MANAGER` | `CREATE_RDD_SERVICE_REQUEST`, `UPDATE_RDD_SERVICE_REQUEST`, `CREATE_HANDED_OVER_SR` | Phase 1 (CREATE_SR, Path A) · Phase 0 (CREATE_HANDED_OVER_SR, Path B) |
| `FM_MANAGER` | `APPROVE_RDD_SERVICE_REQUEST` | Phase 2 (FM_REVIEW — Path A only) |
| `OPERATIONS` | `APPROVE_RDD_SERVICE_REQUEST` | Phase 2 (FM_REVIEW — save only, cannot approve independently) |
| `DD_ENGINEER` | *(none — phase 3 triggered by `sr_status_sync`, not by intent)* | Phase 3 (RDD_REVIEW — Path A only) |
| `ADMIN` | All intents | All phases |

All roles additionally have read intents: `ASK_HELP`, `CHECK_SERVICE_REQUEST_STATUS`, `PREVIEW_SERVICE_REQUEST`, `UNKNOWN`.

Permission enforcement happens at two points:
1. **Supervisor RBAC** (`supervisor_node.py`) — intent checked against `ROLE_PERMITTED_INTENTS` before any workflow node runs
2. **Phase entry RBAC** (`permission_service.py`) — `ensure_can_*` checks at the entry node level for fine-grained action control

---

## 10. State Schema

The RDD agent uses `ServiceRequestGraphState` (defined in `app/agents/graph/state.py`). Key fields written by RDD agent nodes:

| Field | Type | Written by | Description |
|-------|------|-----------|-------------|
| `active_agent` | `str \| None` | supervisor, registry | `"rdd_handover_agent"` for all three phases |
| `intent` | `str \| None` | supervisor | `CREATE_RDD_SERVICE_REQUEST` etc. |
| `service_category` | `str \| None` | supervisor | `"FIT_OUT_AND_HANDOVER"` |
| `sub_category` | `str \| None` | supervisor | `"HANDOVER"` (Path A) or `"HANDED_OVER"` (Path B) |
| `workflow_stage` | `str \| None` | sr_status_sync, entry nodes | `"CREATE_SR"`, `"FM_REVIEW"`, `"RDD_REVIEW"`, `"CREATE_HANDED_OVER_SR"`, `"HANDED_OVER_COMPLETED"` |
| `collected_data` | `dict` | merge_state_node | All user-supplied + backend-resolved field values |
| `extracted_fields` | `dict` | field_extraction_node | LLM proposals (pre-merge) with confidence scores |
| `missing_fields` | `list[str]` | missing_field_node | Fields still needed from the user |
| `lease_matches` | `list[dict]` | lease_lookup_node | Candidate leases when multiple found |
| `selected_lease` | `dict \| None` | handover_entry, merge_state | User-selected lease |
| `documents` | `list[dict]` | upload route | Uploaded document metadata |
| `confirmation_status` | `str \| None` | entry nodes | `PENDING`, `CONFIRMED`, `REJECTED` |
| `confirmation_required` | `bool` | confirmation nodes | True when awaiting user confirmation |
| `backend_refs` | `dict` | api_submission nodes | `sr_id`, `service_request_status` |
| `validation_errors` | `list[dict]` | validation_node | Blocking and non-blocking field errors |
| `response_message` | `str` | all nodes | Draft message for response_generation |
| `response_ui` | `dict` | confirmation nodes | Structured UI payload (confirmation card, etc.) |
| `status` | `str` | all nodes | `IN_PROGRESS`, `WAITING_FOR_USER`, `READY_TO_SUBMIT`, `SUBMITTED`, `COMPLETED`, `FAILED` |

---

## 11. File Structure

```
app/agents/
├── registry.py                              ← SERVICE_REQUEST_AGENT_REGISTRY entry for rdd_agent
├── registries/
│   └── workflow_config.py                   ← WorkflowConfig for rdd_agent (_build_rdd_config)
├── schemas/
│   └── handover_schema.py                   ← StageDefinition, required fields, document types
├── prompts/
│   └── handover_extraction_prompt.py        ← LLM field extraction system prompt
└── graph/
    └── nodes/
        └── handover/                        ← All rdd_agent node implementations
            ├── __init__.py
            │
            ├── Phase 1 — CREATE_SR
            ├── handover_entry_node.py        ← Phase 1 entry: confirmation + cancel parsing
            ├── payload_builder_node.py       ← Phase 1: build POST /service-requests payload
            ├── api_submission_node.py        ← Phase 1: POST /service-requests
            ├── confirmation_node.py          ← Shared confirmation logic (used by all phases)
            │
            ├── Phase 2 — FM_REVIEW
            ├── fm_review_entry_node.py       ← Phase 2 entry: FM role guard + action dispatch
            ├── fm_payload_builder_node.py    ← Phase 2: build PATCH payload
            ├── fm_api_submission_node.py     ← Phase 2: PATCH /service-requests (approve/save)
            │
            ├── Phase 3 — RDD_REVIEW
            ├── rdd_review_entry_node.py      ← Phase 3 entry: DD role guard + action dispatch
            ├── rdd_payload_builder_node.py   ← Phase 3: build PATCH payload
            ├── rdd_api_submission_node.py    ← Phase 3: PATCH /service-requests (submit/approve)
            │
            └── Shared pipeline (all phases)
                ├── field_extraction_node.py  ← LLM field extraction with confidence scores
                ├── merge_state_node.py       ← Merge + auto-compute + protect backend fields
                ├── lease_lookup_node.py      ← Lease-Tenant API resolution
                ├── validation_node.py        ← Field + date constraint validation
                ├── missing_field_node.py     ← One-question-at-a-time missing field prompts
                └── preview_node.py           ← SR draft/submitted preview
```

---

## 12. Adding a New Phase (Extension Guide)

To add a **Phase 4** to the RDD lifecycle (e.g. a final sign-off by a Senior Engineer):

### Step 1 — Define the phase fields and documents

Add a new `StageDefinition` in `app/agents/schemas/handover_schema.py`:

```python
SENIOR_REVIEW_STAGE = StageDefinition(
    stage="SENIOR_REVIEW",
    role="SENIOR_ENGINEER",
    required_fields=("sign_off_date", "sign_off_notes"),
    required_documents=("SR_SENIOR_SIGN_OFF",),
)
STAGE_REGISTRY["SENIOR_REVIEW"] = SENIOR_REVIEW_STAGE
```

### Step 2 — Register the phase in WorkflowConfig

Update `_build_rdd_config()` in `app/agents/registries/workflow_config.py`:

```python
stage_sync_nodes={
    "FM_REVIEW":       "fm_review_entry",
    "RDD_REVIEW":      "rdd_review_entry",
    "SENIOR_REVIEW":   "senior_review_entry",   # new
},
collection_stages=frozenset({"CREATE_SR", "FM_REVIEW", "RDD_REVIEW", "SENIOR_REVIEW"}),
confirmation_nodes={
    "CREATE_SR":      "confirmation",
    "FM_REVIEW":      "fm_confirmation",
    "RDD_REVIEW":     "rdd_confirmation",
    "SENIOR_REVIEW":  "senior_confirmation",    # new
},
```

### Step 3 — Implement new phase nodes

Create `app/agents/graph/nodes/handover/senior_review_entry_node.py`, `senior_payload_builder_node.py`, `senior_api_submission_node.py`.

### Step 4 — Wire into the graph

In `app/agents/graph/help_agent_graph.py`:

```python
# Add nodes
graph.add_node("senior_review_entry", senior_review_entry_node)
graph.add_node("senior_confirmation", confirmation_node)
graph.add_node("senior_payload_builder", senior_payload_builder_node)
graph.add_node("senior_api_submission", senior_api_submission_node)

# Add edges to _route_after_sync (auto-handled via WorkflowConfig)
# Add edges for the new phase pipeline
```

### Step 5 — DB migration

The `active_agent` and `workflow_stage` columns are plain text — no schema migration needed for adding a phase. The new stage name (`SENIOR_REVIEW`) is automatically handled by the existing `WorkflowConfig` routing.

### What does NOT change

When you update `WorkflowConfig`, these shared nodes automatically pick up the new phase:

| Node | What it adapts automatically |
|------|------------------------------|
| `field_extraction_node` | Uses `workflow_cfg.extraction_prompt` |
| `missing_field_node` | Uses `workflow_cfg.field_questions` and `backend_fields` |
| `_route_after_sync` | Maps new `stage_sync_nodes` entry |
| `_route_after_validation` | Maps new `collection_stages` and `confirmation_nodes` entries |

---

## Appendix — Document Upload Reference

All document uploads go through `POST /api/v1/upload`:

| Form field | Value |
|-----------|-------|
| `file` | The file (multipart) |
| `document_type` | One of `ALL_DOCUMENT_TYPES` |
| `session_id` | Chat session UUID (to load draft backend refs) |
| `sr_id` | Optional — overrides draft-derived sr_id |

Permission check by document type:

| Document type | Required permission | Role |
|---------------|--------------------|----|
| `SR_HANDOVER_CHECKLIST`, `SR_HANDOVER_SITE_SURVEY`, `SR_COP_CHECKLIST_OTHER` | `CAN_FM_REVIEW_HANDOVER_SR` | FM_MANAGER, OPERATIONS |
| `DR_SR_HANDOVER_REPORT` | `CAN_RDD_REVIEW_HANDOVER_SR` | DD_ENGINEER |
| Others | `CAN_RAISE_HANDOVER_SR` | MALL_MANAGER |

Returned response includes `document_id` which is written back to the draft's `documents` list for subsequent graph turns.
