# Cenomi Service Request Chatbot — Complete Agent Design & Implementation Reference

> **Purpose:** This document is the single authoritative reference for the design, architecture, and implementation of the Cenomi Service Request Chatbot backend agent system. It synthesizes all architectural layers: LangGraph graph design, node implementations, domain services, LLM integration, observability, database schema, security guardrails, and testing strategy.
>
> **Cross-references:** Every section links to the focused companion doc that covers that topic in greater depth.

---

## Table of Contents

1. [Introduction & Companion Docs](#1-introduction--companion-docs)
2. [System Architecture Overview](#2-system-architecture-overview)
3. [Request Lifecycle (End-to-End)](#3-request-lifecycle-end-to-end)
4. [LangGraph Agent Graph](#4-langgraph-agent-graph)
5. [Graph State: `ServiceRequestGraphState`](#5-graph-state-servicerequestgraphstate)
6. [All Graph Nodes](#6-all-graph-nodes)
7. [Conditional Routing](#7-conditional-routing)
8. [Workflow Stages](#8-workflow-stages)
9. [LLM Integration](#9-llm-integration)
10. [Domain Services](#10-domain-services)
11. [Security Design](#11-security-design)
12. [Database Design](#12-database-design)
13. [Observability Architecture](#13-observability-architecture)
14. [Configuration Reference](#14-configuration-reference)
15. [Testing Strategy](#15-testing-strategy)
16. [Extensibility](#16-extensibility)

---

## 1. Introduction & Companion Docs

The Service Request Chatbot is a conversational agent that allows Cenomi mall tenants to submit, review, and approve handover service requests through natural language. The backend is built on **FastAPI** + **LangGraph** running on Python 3.11+. The frontend is a **generic conversational shell** built with Next.js 15 that sends only raw user input — all routing, intent classification, validation, and submission decisions belong exclusively to the backend.

**Core design principle:** _LLM proposes; code decides._

The system hosts **three named agents** within a single LangGraph graph:

| Agent | Identifier | Responsibilities |
|---|---|---|
| **Help Agent** | *(supervisor)* | Intent classification · FAQ Q&A with hybrid RAG (Azure AI Search) · Routes SR intents to registered agents · RBAC enforcement |
| **RDD Agent** | `rdd_handover_agent` | Full RDD Handover SR lifecycle — Path A (CREATE_SR → FM_REVIEW → RDD_REVIEW) + Path B (Already Trading fast-path) + FOS / END / TRADING sub-categories |
| **Work Permit Agent** | `work_permit_agent` | Single-stage Work Permit SR creation |

| Focused doc | What it covers |
|---|---|
| [`architecture.md`](architecture.md) | High-level system architecture, Frontend/Backend contract, DB schema |
| [`help-agent-pipeline.md`](help-agent-pipeline.md) | Help Agent design — all workflows, RBAC, lifecycle, FAQ RAG, API reference |
| [`rdd-handover-agent.md`](rdd-handover-agent.md) | RDD agent lifecycle — Path A (3-phase) and Path B (Already Trading fast-path) |
| [`handover-workflow.md`](handover-workflow.md) | CREATE_SR / FM_REVIEW / RDD_REVIEW stage definitions, payload shapes, validation rules |
| [`observability.md`](observability.md) | TraceManager, trace hierarchy, state diffs, redaction, Admin UI |
| [`security-guardrails.md`](security-guardrails.md) | Injection guard, field protection, confirmation enforcement, permission checks |
| [`api-reference.md`](api-reference.md) | REST endpoint contracts, request/response schemas (v1 + v2) |
| [`extensibility-guide.md`](extensibility-guide.md) | Adding new agents, pending items for FM/RDD completion |
| [`testing-strategy.md`](testing-strategy.md) | Full test pyramid, fixture conventions, running tests |
| [`agent-testing-guide.md`](agent-testing-guide.md) | Complete test guide — unit through eval |
| [`eval-howto.md`](eval-howto.md) | Running the eval suite locally — tiers, REPORT.md interpretation |
| [`eval-setup-guide.md`](eval-setup-guide.md) | Full eval infrastructure setup — all 27 tiers, scenario authoring, CI integration |
| [`test-automation-lifecycle.md`](test-automation-lifecycle.md) | Full lifecycle test automation (pytest + Playwright + eval) |
| [`local-development.md`](local-development.md) | Quick-start, docker-compose, environment setup |
| [`debugging-guide.md`](debugging-guide.md) | SQL inspection queries, common failure modes |
| [`database.md`](database.md) | Database schema, models, migrations |
| [`production-readiness.md`](production-readiness.md) | Production readiness assessment |
| [`uat-readiness.md`](uat-readiness.md) | UAT readiness assessment |
| [`help-agent-validation.md`](help-agent-validation.md) | Help Agent / MSP integration contract and gap table |
| [`faq-rag-integration.md`](faq-rag-integration.md) | FAQ RAG setup — Azure AI Search index, embedded knowledge, fallback |
| [`azure-deployment.md`](azure-deployment.md) | Azure deployment guide |

---

## 2. System Architecture Overview

```mermaid
graph TB
    subgraph Browser
        FE["Next.js 15 Chat UI\nGeneric Conversational Shell"]
    end

    subgraph MSPPlatform["MSP Platform Frontend"]
        MSP_FE["cenomi-ai-backend\nMSP Chat Client"]
    end

    subgraph BackendFastAPI["Backend (FastAPI + Python 3.11)"]
        API["POST /api/v2/chat/service-request\nchat.py router"]
        MSP_API["POST /api/v1/chat\nPOST /api/v1/chat/stream\nmsp_chat.py adapter"]
        GUARD["Injection Guard\ninjection_guard.scan_message()"]
        ORCH["ChatOrchestrationService\nchat_orchestration_service.py"]

        subgraph AgentRuntime["Agent Runtime (LangGraph — 26 nodes)"]
            LOAD["load_session\nConversationStateService.load()"]
            DOC_UP["document_upload\nPartition FM/RDD docs"]
            SYNC["sr_status_sync\nPlatform status check"]
            SUPER["supervisor (Help Agent)\nLLM intent classification + RBAC"]
            FAQ["faq_node\nHybrid RAG Q&A\nAzure AI Search"]
            REG["registry\nDeterministic agent lookup"]
            ENTRY["Stage Entry Nodes\nhandover_entry / fm_review_entry / rdd_review_entry"]
            PIPE["Data Pipeline\nfield_extraction → merge_state → lease_lookup → validation"]
            CONFIRM["Confirmation Gates\nconfirmation / fm_confirmation / rdd_confirmation"]
            BUILD["Payload Builders\npayload_builder / fm / rdd"]
            SUBMIT["API Submission Nodes\napi_submission / fm / rdd"]
            RESP["response_generation\nLLM response composition"]
            SAVE["save_state\nConversationStateService.save_checkpoint()"]
        end

        OBS["TraceManager\nObservability"]
    end

    subgraph Infra
        PG[("PostgreSQL 16\nConversation + Draft + Traces + Users")]
        REDIS[("Redis 7\nReadiness probe · reserved for cache")]
    end

    subgraph ExternalAPIs["External APIs (Cenomi Platform)"]
        LLM["OpenAI API\ngpt-4o-mini (configurable)"]
        AZURE["Azure AI Search\ncentomi-help-index (FAQ RAG)"]
        LEASE["Lease-Tenant API\nLEASE_TENANT_API_BASE_URL"]
        SR_API["Service Request API\nSERVICE_REQUEST_API_BASE_URL"]
        FILE["File Upload API\nFILE_UPLOAD_API_BASE_URL"]
    end

    FE -->|"session_id, message, action, corrected_fields, selected_lease_id"| API
    MSP_FE -->|"message, conversation_id, language, context\nx-internal-api-token"| MSP_API
    MSP_API --> ORCH
    API --> GUARD
    GUARD -->|"clean"| ORCH
    GUARD -->|"HIGH_RISK ≥ 0.7"| FE
    ORCH --> LOAD
    LOAD --> SUPER
    SUPER -->|"LLM classification"| LLM
    FAQ -->|"RAG + LLM"| AZURE
    FAQ -->|"LLM answer"| LLM
    ENTRY -->|"field extraction"| LLM
    RESP -->|"response composition"| LLM
    ENTRY --> PIPE
    PIPE -->|"lease resolution"| LEASE
    SUBMIT -->|"POST / PATCH service request"| SR_API
    API -->|"file upload"| FILE

    LOAD -.->|"load draft + session"| PG
    SAVE -.->|"checkpoint draft"| PG
    ORCH --> OBS
    OBS -.->|"traces, runs, snapshots"| PG
```

### Key Design Principles

| Principle | Implementation |
|---|---|
| **Generic frontend shell** | Frontend sends only `session_id`, `message`, `action`, `corrected_fields`, `selected_lease_id`. Never sends `active_agent`, `intent`, `workflow_stage`. |
| **Backend-owned routing** | All routing is pure Python. No LLM decides which node runs. The Agent Registry result always overrides any LLM-provided `target_agent` hint. |
| **Stateless graph, stateful DB** | LangGraph graph has no persistent memory. State is loaded from PostgreSQL at `load_session_node` and checkpointed at `save_state_node`. No LangGraph interrupt/resume. |
| **LLM proposes; code decides** | LLM extracts field values and generates text. Code validates, routes, confirms, builds payloads, and submits. |
| **Pre-graph injection guard** | Prompt injection scanning happens before the user message is persisted or the graph is invoked. |
| **Fail-safe observability** | Tracing exceptions are swallowed — they never crash the chat flow. |
| **MSP Platform adapter** | `POST /api/v1/chat` and `/api/v1/chat/stream` are contractually fixed MSP endpoints. They map `conversation_id` ↔ `session_id` and add `faq_sources` and `language` to the response. |
| **Dual API versioning** | All v1 routes remain active for backward compatibility. New callers should use `/api/v2/*`. MSP endpoints are v1-only (contract). |

---

## 3. Request Lifecycle (End-to-End)

```mermaid
sequenceDiagram
    participant Client as "Chat UI / API Client"
    participant Router as "POST /api/chat/service-request (chat.py)"
    participant Guard as "Injection Guard (injection_guard.py)"
    participant Orch as "ChatOrchestrationService"
    participant DB as "PostgreSQL"
    participant Trace as "TraceManager"
    participant Graph as "LangGraph (help_agent_graph)"
    participant LLM as "OpenAI API"
    participant Ext as "Cenomi Platform APIs"

    Client->>Router: message, session_id, action, corrected_fields, selected_lease_id
    Router->>Orch: process_turn(request, auth_context)

    Orch->>DB: Load or create ChatSession
    Orch->>Trace: start_trace(session_id, metadata) → AgentTrace (RUNNING)
    Orch->>DB: AuditLogRepository.create(turn.started)
    Orch->>Guard: scan_message(message)

    alt HIGH_RISK score ≥ 0.7
        Guard-->>Orch: InjectionScanResult(high_risk=True)
        Orch->>Trace: fail_trace(injection_detected)
        Orch->>DB: audit(security.injection_attempt)
        Orch-->>Client: refusal message (message NOT persisted)
    end

    Guard-->>Orch: clean
    Orch->>DB: ChatMessageRepository.create(role=user)

    Note over Orch: Build initial ServiceRequestGraphState<br/>Routing fields (active_agent, intent, workflow_stage)<br/>loaded from DB via ConversationStateService.load()<br/>NOT from frontend request

    Orch->>Graph: get_compiled_graph().ainvoke(initial_state)

    Graph->>Graph: load_session → sr_status_sync? → supervisor
    Graph->>LLM: supervisor LLM call (intent classification)
    Graph->>Graph: registry → stage entry node
    Graph->>LLM: field_extraction LLM call
    Graph->>Ext: lease_lookup (GET /leases)
    Graph->>Graph: validation → missing_field / confirmation
    Graph->>Ext: api_submission (POST /service-requests)
    Graph->>LLM: response_generation LLM call
    Graph->>DB: save_state (ConversationStateService.save_checkpoint)
    Graph-->>Orch: final ServiceRequestGraphState

    Orch->>Trace: finish_trace(final_state) → AgentTrace (COMPLETED)
    Orch->>DB: ChatMessageRepository.create(role=assistant)
    Orch->>DB: ChatSessionRepository.update(active_agent, intent, workflow_stage, status)
    Orch->>DB: AuditLogRepository.create(turn.completed)

    Orch-->>Router: ChatTurnResult
    Router-->>Client: message, response_ui, trace_id, draft_preview
```

### `ChatOrchestrationService.process_turn()` — Step by Step

**File:** `app/services/chat_orchestration_service.py`

1. **Session management** — Load existing `ChatSession` by `session_id`, or create a new one.
2. **Trace start** — `TraceManager.start_trace()` inserts an `AgentTrace` row with `status = "RUNNING"`.
3. **Audit** — `AuditLogRepository.create(action="turn.started")`.
4. **History load** — Last 10 messages fetched for graph context.
5. **Injection guard** — `scan_message()` is called. If `HIGH_RISK` (score ≥ 0.7): audit event written, `fail_trace` called, refusal returned, **message not persisted, graph not invoked**.
6. **Persist user message** — `ChatMessageRepository.create(role="user")`.
7. **Build graph state** — `ServiceRequestGraphState` is constructed. Routing fields (`active_agent`, `intent`, `workflow_stage`, `service_category`, `sub_category`) are **loaded from the database** via `ConversationStateService.load()`, never from the frontend request. UI interaction fields (`action`, `corrected_fields`, `selected_lease`) are injected from the request body.
8. **Graph invocation** — `get_compiled_graph().ainvoke(initial_state)` runs the full LangGraph pipeline synchronously.
9. **Trace finish** — `TraceManager.finish_trace(final_state)` updates `AgentTrace` to `COMPLETED`.
10. **Persist assistant reply** — `ChatMessageRepository.create(role="assistant")`.
11. **Sync session** — `ChatSessionRepository.update()` saves `active_agent`, `intent`, `workflow_stage`, `status` from the final graph state.
12. **Audit** — `AuditLogRepository.create(action="turn.completed")`.
13. **Return** — `ChatTurnResult` with `message`, `response_ui`, `trace_id`, and `draft_preview` built from `collected_data`.

---

## 4. LangGraph Agent Graph

**File:** `app/agents/graph/help_agent_graph.py`

The graph is compiled once at application startup as a singleton via `get_compiled_help_graph()`. Each HTTP turn calls `ainvoke(initial_state)` on this compiled graph from scratch — there is **no LangGraph interrupt/resume mechanism**. Conversation continuity is achieved by loading state from PostgreSQL at `load_session_node`. The graph currently contains **26 nodes** spanning session management, routing (Help Agent + FAQ), stage entry, data pipeline, confirmation gates, payload builders, submission, and finalization.

### Complete Graph Flowchart

```mermaid
flowchart TD
    START([START]) --> load_session

    load_session --> document_upload

    document_upload -->|"_user_wants_preview(msg)"| supervisor
    document_upload -->|"backend_refs.sr_id exists"| sr_status_sync
    document_upload -->|"active_agent set, no sr_id"| handover_entry
    document_upload -->|"no active_agent"| supervisor

    sr_status_sync -->|"workflow_stage = FM_REVIEW"| fm_review_entry
    sr_status_sync -->|"workflow_stage = RDD_REVIEW"| rdd_review_entry
    sr_status_sync -->|"SR_CREATED or SR_COMPLETED"| supervisor
    sr_status_sync -->|"CREATE_SR + active_agent"| handover_entry

    supervisor -->|"PREVIEW_SERVICE_REQUEST"| preview
    supervisor -->|"ASK_HELP / UNKNOWN"| faq_node
    supervisor -->|"WAITING_FOR_USER (RBAC denied)"| response_generation
    supervisor -->|"intent classified"| registry

    faq_node --> response_generation
    preview --> response_generation

    registry -->|"WAITING_FOR_USER"| response_generation
    registry -->|"agent resolved"| handover_entry

    handover_entry -->|"workflow cleared"| response_generation
    handover_entry -->|"action_override = cancel"| merge_state
    handover_entry -->|"cancel_sr confirmed → platform CANCELLED"| response_generation
    handover_entry -->|"normal turn"| field_extraction

    fm_review_entry -->|"WAITING_FOR_USER"| response_generation
    fm_review_entry -->|"fm_action set"| merge_state
    fm_review_entry -->|"normal turn"| field_extraction

    rdd_review_entry -->|"WAITING_FOR_USER"| response_generation
    rdd_review_entry -->|"rdd_action set"| merge_state
    rdd_review_entry -->|"normal turn"| field_extraction

    field_extraction --> merge_state

    merge_state -->|"selected_lease set or lease_id missing\n(CREATE_SR / HANDED_OVER only)"| lease_lookup
    merge_state -->|"lease_id present or FM/RDD stage"| validation

    lease_lookup -->|"WAITING_FOR_USER"| response_generation
    lease_lookup -->|"lease resolved"| validation

    validation -->|"blocking errors or missing fields"| missing_field
    validation -->|"CREATE_SR all valid"| confirmation
    validation -->|"HANDED_OVER all valid"| confirmation
    validation -->|"FM_REVIEW all valid"| fm_confirmation
    validation -->|"RDD_REVIEW all valid"| rdd_confirmation
    validation -->|"terminal stage"| response_generation

    missing_field --> response_generation

    confirmation -->|"CONFIRMED (CREATE_SR)"| payload_builder
    confirmation -->|"CONFIRMED (HANDED_OVER)"| payload_builder
    confirmation -->|"not CONFIRMED"| response_generation

    fm_confirmation -->|"CONFIRMED"| fm_payload_builder
    fm_confirmation -->|"not CONFIRMED"| response_generation

    rdd_confirmation -->|"CONFIRMED"| rdd_payload_builder
    rdd_confirmation -->|"not CONFIRMED"| response_generation

    payload_builder --> api_submission
    api_submission -->|"CREATE_SR → SR_CREATED"| response_generation
    api_submission -->|"HANDED_OVER → HANDED_OVER_COMPLETED (auto-approved)"| response_generation

    fm_payload_builder --> fm_api_submission
    fm_api_submission --> response_generation

    rdd_payload_builder --> rdd_api_submission
    rdd_api_submission --> response_generation

    response_generation --> save_state
    save_state --> END([END])
```

---

## 5. Graph State: `ServiceRequestGraphState`

**File:** `app/agents/graph/state.py`

The state is a `TypedDict` with `total=False` — all keys are optional because state is incrementally populated as the graph executes. Every turn starts with a freshly-built state dict.

### Field Reference

| Field | Type | Persisted to DB | Description |
|---|---|---|---|
| `session_id` | `str` | ✓ (session) | Chat session identifier |
| `user_id` | `str` | ✓ (session) | User identity |
| `user_message` | `str` | ✓ (message table) | Current turn raw input |
| `attachments` | `list[dict]` | — | File attachment metadata for this turn |
| `trace_id` | `str` | ✓ (agent_traces) | Observability trace for this turn |
| `conversation_history` | `list[dict]` | ✓ (chat_messages) | `[{"role": "user/assistant", "content": "..."}]` (last 10) |
| `active_agent` | `str \| None` | ✓ (chat_sessions) | e.g. `"rdd_handover_agent"` |
| `intent` | `str \| None` | ✓ (chat_sessions) | e.g. `"CREATE_RDD_SERVICE_REQUEST"` |
| `service_category` | `str \| None` | ✓ (draft) | e.g. `"FIT_OUT_AND_HANDOVER"` |
| `sub_category` | `str \| None` | ✓ (draft) | e.g. `"HANDOVER"` |
| `workflow_stage` | `str \| None` | ✓ (chat_sessions + draft) | `"CREATE_SR"` \| `"FM_REVIEW"` \| `"RDD_REVIEW"` \| `"SR_CREATED"` \| `"SR_COMPLETED"` |
| `status` | `str \| None` | ✓ (chat_sessions) | `"IN_PROGRESS"` \| `"WAITING_FOR_USER"` \| `"READY_TO_SUBMIT"` \| `"SUBMITTED"` \| `"COMPLETED"` \| `"FAILED"` |
| `collected_data` | `dict[str, Any]` | ✓ (service_request_drafts) | Canonical draft — single source of truth for all user-supplied and backend-derived fields |
| `extracted_fields` | `dict[str, Any]` | — | LLM extraction output this turn `{field: {value, confidence}}` — cleared by `merge_state_node` |
| `missing_fields` | `list[str]` | ✓ (draft) | Fields still required — recomputed every turn |
| `lease_matches` | `list[dict]` | — | Multi-match candidates from Lease-Tenant API |
| `selected_lease` | `dict \| None` | — | User-selected lease from disambiguation UI (turn-only; not persisted) |
| `documents` | `list[dict]` | ✓ (draft) | Uploaded document metadata |
| `confirmation_required` | `bool` | — | HITL gate flag |
| `confirmation_status` | `str \| None` | — | `None` \| `"PENDING"` \| `"CONFIRMED"` \| `"REJECTED"` |
| `backend_refs` | `dict[str, Any]` | ✓ (draft) | API references: `sr_id`, payloads, actions, platform status (see sub-keys below) |
| `validation_errors` | `list[dict]` | — | `[{field, validation_type, status, message, blocking}]` |
| `response_message` | `str` | ✓ (chat_messages) | Text to display to user |
| `response_ui` | `dict[str, Any]` | — | Structured UI: `confirmation_card`, `lease_selection`, `text_question`, `sr_preview_card`, `document_requirement`. Also carries `cancelled`, `stage_complete`, `workflow_complete` boolean signals. |
| `faq_sources` | `list[dict]` | — | RAG citations from `faq_node` `[{"source_type": "help_content", "title": "..."}]` — reset each turn by `load_session_node` |
| `language` | `str \| None` | — | `"en"` or `"ar"` — injected by MSP adapter when set in request |
| `msp_context` | `dict \| None` | — | `{current_url_pattern?, help_category?, help_subcategory?}` — injected by MSP adapter |
| `action_override` | `str \| None` | **NOT persisted** | UI interaction: `"confirm"` \| `"cancel"` \| `"save_fm_progress"` \| `"approve_fm_review"` \| `"submit_rdd_report"` etc. — injected from request, stripped before checkpoint |
| `corrected_fields` | `dict \| None` | **NOT persisted** | Inline edits from confirmation card — turn-only |
| `conversation_state_service` | `Any` | **NOT persisted** | Runtime-injected `ConversationStateService` instance |
| `trace_manager` | `Any` | **NOT persisted** | Runtime-injected `TraceManager` instance |

### `backend_refs` Sub-Keys

| Sub-key | Set by | Purpose |
|---|---|---|
| `sr_id` | `api_submission_node` | Platform service request ID after creation |
| `create_payload` | `payload_builder_node` | Full CREATE_SR POST body |
| `fm_payload` | `fm_payload_builder_node` | FM PATCH body |
| `rdd_payload` | `rdd_payload_builder_node` | RDD POST body |
| `fm_action` | `fm_review_entry_node` | `"save_fm_progress"` \| `"approve_fm_review"` \| `"reject_fm_review"` |
| `rdd_action` | `rdd_review_entry_node` | `"submit_rdd_report"` |
| `already_handovered` | `payload_builder_node` | `"initiate_handover"` (Path A) \| `"already_trading"` (Path B) |
| `platform_sr_status` | `sr_status_sync_node` | Raw status string from platform |
| `sr_operations` | `sr_status_sync_node` | Platform `service_request_operations` list |
| `user_role` | `fm/rdd_review_entry_node` | Role validated by entry node |
| `uploaded_documents` | `fm/rdd_review_entry_node` | List of document UUIDs partitioned by phase |
| `correlation_id` | `api_submission_node` | Platform-returned correlation ID |
| `service_request_status` | `sr_status_sync_node` | Mapped internal status |
| `fm_status` | `fm_api_submission_node` | Status after FM submission |
| `rdd_status` | `rdd_api_submission_node` | Status after RDD submission |
| `rdd_submitted_sr_id` | `rdd_api_submission_node` | SR ID returned by RDD POST |
| `rdd_document_id` | `rdd_review_entry_node` | RDD report document UUID |
| `cancel_reason` | `handover_entry_node` | Reason provided by user when cancelling SR |

### Key State Invariants

- `collected_data` is the canonical form accumulator — only fields that pass `merge_state_node` guards land here.
- `extracted_fields` is ephemeral — it holds raw LLM proposals for the current turn only and is consumed by `merge_state_node`.
- `action_override` and `corrected_fields` are never saved to the database by `save_state_node`.
- `confirmation_status` uses `"REJECTED"` (not `"DENIED"`) for declined confirmations.
- Runtime service objects (`trace_manager`, `conversation_state_service`) are injected by `ChatOrchestrationService` and stripped by `sanitize_state_for_trace` before any persistence.
- `faq_sources`, `language`, and `msp_context` are turn-only injection points from the MSP adapter — they are reset by `load_session_node` each turn.
- `notes` and `comments` are kept in sync by `merge_state_node` — writing to either field propagates to the other.
- `trading_date` (RDD_REVIEW) is auto-computed as `fitout_end_date + 8 days` when not explicitly provided.

---

## 6. All Graph Nodes

### 6.1 Session Layer

#### `load_session_node`

| | |
|---|---|
| **File** | `app/agents/graph/nodes/load_session_node.py` |
| **Trace run_type** | — (not decorated with `@trace_node`) |
| **Inputs consumed** | `session_id`, `conversation_state_service` |
| **Outputs produced** | `collected_data`, `missing_fields`, `documents`, `backend_refs`, `workflow_stage`, `active_agent` |

Calls `ConversationStateService.load(session_id)` to hydrate the graph state from the `ServiceRequestDraft` record. Clears stale `missing_fields` from the prior turn (they are recomputed by `validation_node` each turn). Resets `faq_sources` to `[]` so RAG citations never bleed across turns. If no draft exists, returns empty defaults. Does not run any LLM.

#### `document_upload_node`

| | |
|---|---|
| **File** | `app/agents/graph/nodes/document_upload_node.py` |
| **Trace run_type** | `TOOL` |
| **Inputs consumed** | `attachments`, `workflow_stage`, `backend_refs` |
| **Outputs produced** | `backend_refs.uploaded_documents` (partitioned by phase) |

Runs immediately after `load_session_node` on every turn. When `attachments` are present in the request, partitions them into FM or RDD document buckets in `backend_refs.uploaded_documents`. Ensures that documents uploaded during FM_REVIEW are not mixed with RDD_REVIEW documents. If no attachments are present, passes through with no side effects.

#### `sr_status_sync_node`

| | |
|---|---|
| **File** | `app/agents/graph/nodes/sr_status_sync_node.py` |
| **Trace run_type** | `TOOL` |
| **Inputs consumed** | `backend_refs.sr_id`, `user_id` |
| **Outputs produced** | `workflow_stage`, `backend_refs.platform_sr_status`, `backend_refs.sr_operations` |

Runs only when `backend_refs.sr_id` is present. Calls `ServiceRequestPlatformClient.get_service_request(sr_id)` and maps the platform's `service_request_operations` array to the internal `workflow_stage`:

| Platform operation | Internal `workflow_stage` |
|---|---|
| `FM_MANAGER IN_PROGRESS` | `FM_REVIEW` |
| `DD_ENGINEER IN_PROGRESS` | `RDD_REVIEW` |
| `COMPLETED` | `SR_COMPLETED` |
| *(none / initial)* | `SR_CREATED` |

---

### 6.2 Routing Layer

#### `supervisor_node`

| | |
|---|---|
| **File** | `app/agents/graph/nodes/supervisor_node.py` |
| **Trace run_type** | `SUPERVISOR` |
| **Inputs consumed** | `user_message`, `active_agent`, `intent`, `conversation_history` (last 4 turns), `user_role` |
| **Outputs produced** | `intent`, `service_category`, `sub_category`, `status`, `response_message` |
| **LLM call** | Yes — `LLMGateway.complete_json(SUPERVISOR_SYSTEM_PROMPT, user_content)` |

The entry point for intent classification and RBAC enforcement (the "Help Agent"). Skipped when `active_agent` is already set (workflow continuation) unless the user message triggers `_user_wants_preview()`. If confidence < 0.6 (`CONFIDENCE_THRESHOLD`), sets `intent = "UNKNOWN"` which routes to `faq_node`. RBAC is checked inside the node (not the routing function) — unauthorized intent returns `status = "WAITING_FOR_USER"` with a denial message directly.

**`SupervisorDecision` schema:**

```python
class SupervisorDecision(BaseModel):
    intent: Literal[
        "CREATE_RDD_SERVICE_REQUEST",
        "CREATE_HANDED_OVER_SR",          # Path B — Already Trading fast-path
        "UPDATE_RDD_SERVICE_REQUEST",
        "APPROVE_RDD_SERVICE_REQUEST",
        "CHECK_SERVICE_REQUEST_STATUS",
        "PREVIEW_SERVICE_REQUEST",
        "CANCEL_SR",                       # Cancel a submitted SR
        "ASK_HELP",                        # Route to faq_node
        "UNKNOWN",                         # Low confidence — route to faq_node
    ]
    service_category: str | None
    sub_category: str | None
    target_agent: str | None      # advisory only — registry result wins
    confidence: float             # 0.0–1.0
    reasoning: str                # stripped before trace persistence
```

Tracing: opens a nested `LLM` child run, calls `TraceManager.capture_llm_call(prompt_name="supervisor_prompt")`.

#### `faq_node`

| | |
|---|---|
| **File** | `app/agents/graph/nodes/faq_node.py` |
| **Trace run_type** | `AGENT` |
| **Inputs consumed** | `user_message`, `conversation_history`, `language`, `msp_context` |
| **Outputs produced** | `response_message`, `faq_sources`, `status = "WAITING_FOR_USER"` |
| **LLM call** | Yes — hybrid RAG: Azure AI Search (`cenomi-help-index`) → LLM answer |

Handles `ASK_HELP` and `UNKNOWN` intents. Executes a two-step pipeline:
1. **Retrieval:** `GET cenomi-help-index` — fetches top-K relevant help chunks from Azure AI Search based on the user message and optional `msp_context` (page URL, help category).
2. **Answer:** `LLMGateway.complete_json(FAQ_SYSTEM_PROMPT + retrieved_chunks, user_content)` — generates a natural-language answer with inline `[Source: ...]` citations.
3. **Fallback:** If Azure Search is unavailable, answers from `FAQ_SYSTEM_PROMPT` embedded knowledge only.

Returns `faq_sources` as a list of `{"source_type": "help_content", "title": "..."}` items — surfaced in the MSP Platform API response as `sources[]`.

#### `registry_node`

| | |
|---|---|
| **File** | `app/agents/graph/nodes/registry_node.py` |
| **Trace run_type** | `AGENT` |
| **Inputs consumed** | `service_category`, `sub_category`, `intent` |
| **Outputs produced** | `active_agent` |

Calls `lookup_agent(service_category, sub_category)` against `SERVICE_REQUEST_AGENT_REGISTRY`. The registry result **always overrides** any `target_agent` hint from the supervisor. If no match is found and no `active_agent` is set, routes to `response_generation` with an unsupported category message.

```python
SERVICE_REQUEST_AGENT_REGISTRY = {
    "FIT_OUT_AND_HANDOVER": {
        "HANDOVER":              {"agent_name": "rdd_handover_agent",             ...},  # Path A
        "HANDED_OVER":           {"agent_name": "rdd_handover_agent",             ...},  # Path B — Already Trading
        "FIT_OUT_CONFIRM_FOS":   {"agent_name": "rdd_fitout_start_agent",         ...},  # Fitout Start Confirmation
        "FIT_OUT_CONFIRM_END":   {"agent_name": "rdd_fitout_end_agent",           ...},  # Fitout End Confirmation
        "FIT_OUT_CONFIRM_TRADING": {"agent_name": "rdd_trading_confirmation_agent", ...},  # Trading Confirmation
    },
    "WORK_PERMIT": {
        "WORK_PERMIT":           {"agent_name": "work_permit_agent",              ...},  # Single-stage WP SR
    },
}
```

#### `preview_node`

| | |
|---|---|
| **File** | `app/agents/graph/nodes/preview_node.py` |
| **Trace run_type** | `AGENT` |
| **Inputs consumed** | `backend_refs.sr_id`, `collected_data`, `workflow_stage` |
| **Outputs produced** | `response_ui.sr_preview_card`, `response_message` |

Builds the `sr_preview_card` UI component. If `sr_id` is present, fetches the live SR from the platform. Otherwise, summarises the current `collected_data` draft. Always routes to `response_generation`. No LLM call.

---

### 6.3 Stage Entry Nodes (HITL Parsers)

These nodes parse structured UI actions and keyword-based user input before any LLM runs. They are the gatekeepers for each workflow stage.

#### `handover_entry_node`

| | |
|---|---|
| **File** | `app/agents/graph/nodes/handover_entry_node.py` |
| **Trace run_type** | `AGENT` |
| **Stage** | `CREATE_SR` |

Logic priority (evaluated in order):

1. **UI action override (Priority 0):** `action_override == "confirm"` → `confirmation_status = "CONFIRMED"`. `action_override == "cancel"` → `confirmation_status = "REJECTED"`, route to `merge_state`.
2. **Cancel SR path:** If intent is `CANCEL_SR` — prompts user to confirm cancellation. On confirmation, calls platform `PATCH /service-requests/{sr_id}` with `status: CANCELLED`, clears workflow state, emits `response_ui.cancelled = true`. Uses `cancel_sr` → `CANCEL_PENDING` → `confirm_cancel_sr` sub-flow.
3. **Workflow cancel/restart:** Message matches `_CANCEL_WORKFLOW_PHRASES` (e.g. `"start over"`, `"restart"`, `"new request"`) → clears all workflow state and routes to `response_generation`.
4. **Confirmation text parsing:** If `confirmation_status == "PENDING"`, matches `_CONFIRM_PHRASES` / `_REJECT_PHRASES` using word-boundary regex. Ambiguous input → clarification prompt, no status change.
5. **Normal turn:** Routes to `field_extraction`.

#### `fm_review_entry_node`

| | |
|---|---|
| **File** | `app/agents/graph/nodes/fm_review_entry_node.py` |
| **Trace run_type** | `AGENT` |
| **Stage** | `FM_REVIEW` |

Enforces role guard (`FM_MANAGER` or `OPERATIONS`). Dispatches UI actions:
- `save_fm_progress` → sets `backend_refs.fm_action = "save_fm_progress"`, routes to `merge_state`
- `approve_fm_review` → sets `backend_refs.fm_action = "approve_fm_review"`, routes to `merge_state`
- `reject_fm_review` → sets `backend_refs.fm_action = "reject_fm_review"`, routes to `merge_state`
- `cancel_update` → clears FM state, routes to `response_generation`
- `upload_document` → handles document upload metadata

#### `rdd_review_entry_node`

| | |
|---|---|
| **File** | `app/agents/graph/nodes/rdd_review_entry_node.py` |
| **Trace run_type** | `AGENT` |
| **Stage** | `RDD_REVIEW` |

Enforces role guard (`DD_ENGINEER`). Dispatches:
- `submit_rdd_report` → sets `backend_refs.rdd_action = "submit_rdd_report"`, routes to `merge_state`
- `cancel_update` / `upload_document` handled similarly

---

### 6.4 Data Pipeline

#### `field_extraction_node`

| | |
|---|---|
| **File** | `app/agents/graph/nodes/field_extraction_node.py` |
| **Trace run_type** | `AGENT` |
| **Inputs consumed** | `user_message`, `missing_fields`, `conversation_history`, `workflow_stage` |
| **Outputs produced** | `extracted_fields` |
| **LLM call** | Yes — via `FieldExtractionService` |

Calls `FieldExtractionService.extract(message, missing_fields, history)`. Returns `extracted_fields` as `{field_name: {"value": str, "confidence": float}}`. Does **not** merge into `collected_data` — that is `merge_state_node`'s job. Up to 2 retries on JSON/validation failure; never raises on total failure (returns empty fields).

#### `merge_state_node`

| | |
|---|---|
| **File** | `app/agents/graph/nodes/merge_state_node.py` |
| **Trace run_type** | `CHAIN` |
| **Inputs consumed** | `extracted_fields`, `corrected_fields`, `collected_data`, `backend_refs` |
| **Outputs produced** | `collected_data`, `missing_fields` |

The authoritative field accumulator. Merge logic:

1. For each `extracted_fields` entry: skip if field is in `BACKEND_PROTECTED_FIELDS`; skip if lease is resolved and field is in `_LEASE_CONFIRMED_FIELDS` (`lease_code`, `mall`, `brand`); skip if `confidence < 0.6`; otherwise merge into `collected_data`.
2. **Field intent disambiguation** — `_remap_extracted_to_active_field()` remaps an extracted value to the field the bot is currently asking for (e.g. a bare answer of "9:00 AM" maps to `inspectionTime`, not to `notes`). Applied to all user-supplied fields.
3. Apply `corrected_fields` (inline edits from confirmation card) with the same backend-field protection.
4. **`notes ↔ comments` sync** — writing to `notes` propagates to `comments` and vice versa so both fields stay in sync across the lifecycle.
5. Track `_corrections` audit trail.
6. Auto-generate `title = "handover-{lease_code}-{description_slug}"` once both `lease_code` and `description` are available. Never ask user for `title`.
7. Skips `lease_lookup` routing when `workflow_stage` is `FM_REVIEW` or `RDD_REVIEW`.

#### `lease_lookup_node`

| | |
|---|---|
| **File** | `app/agents/graph/nodes/lease_lookup_node.py` |
| **Trace run_type** | `TOOL` |
| **Inputs consumed** | `collected_data`, `selected_lease`, `user_id` |
| **Outputs produced** | `collected_data` (enriched), `lease_matches`, `response_ui.lease_selection` |

Resolves tenant lease from the Cenomi Lease-Tenant API:

| Result | Action |
|---|---|
| 0 matches | Set `WAITING_FOR_USER`, ask for lease code |
| 1 match | Enrich `collected_data` with `lease_id`, `tenant_profile_id`, `property_id`, `brand_id`, `unit_codes`, `city`, `contracted_area` |
| N matches | Set `response_ui.type = "lease_selection"` with candidates |
| `selected_lease` present | Resolve the user-chosen lease, enrich `collected_data` |

#### `validation_node`

| | |
|---|---|
| **File** | `app/agents/graph/nodes/validation_node.py` |
| **Trace run_type** | `AGENT` |
| **Inputs consumed** | `collected_data`, `documents`, `workflow_stage` |
| **Outputs produced** | `validation_errors`, `status` |

Calls `ValidationService.validate_draft(stage, collected_data, documents)`. Returns only **FAILED** `ValidationResult` dicts. Rules (see [handover-workflow.md](handover-workflow.md) for full details):

| Rule | Stage | Blocking |
|---|---|---|
| Required field present and non-empty | All | Yes |
| `inspection_done_by` ∈ `{"FM_MANAGER", "OPERATIONS"}` | CREATE_SR / HANDED_OVER | Yes |
| `startDate < endDate` | CREATE_SR / HANDED_OVER | Yes |
| RDD date chain order: `actual_handover ≤ fitout_start ≤ fitout_end ≤ trading_date` | RDD_REVIEW | Yes |
| `trading_date` auto-set to `fitout_end_date + 8 days` if not provided | RDD_REVIEW | — |
| `inspectionTime` required and combined into ISO 8601 timestamp with `startDate` | CREATE_SR | Yes |
| Document type in `ALL_DOCUMENT_TYPES` | FM/RDD | Yes |
| Role authorised for stage | All | Yes |

#### `missing_field_node`

| | |
|---|---|
| **File** | `app/agents/graph/nodes/missing_field_node.py` |
| **Trace run_type** | `AGENT` |
| **Inputs consumed** | `missing_fields`, `validation_errors`, `collected_data`, `workflow_stage` |
| **Outputs produced** | `response_message`, `response_ui.type = "text_question"`, `status = "WAITING_FOR_USER"` |

Asks exactly **one** question per turn. Priority:
1. Non-required blocking validation errors (e.g. `inspection_done_by` invalid value)
2. Backend-missing fields (those that require lease lookup)
3. First item in `missing_fields`

Uses `HANDOVER_FIELD_QUESTIONS` dict for human-readable question text.

---

### 6.5 Confirmation Gates

All three confirmation nodes share the same `confirmation_node.py` implementation but are registered under different names (`confirmation`, `fm_confirmation`, `rdd_confirmation`) to route to their respective payload builders.

| | |
|---|---|
| **File** | `app/agents/graph/nodes/confirmation_node.py` |
| **Trace run_type** | `AGENT` |
| **Inputs consumed** | `collected_data`, `confirmation_status`, `documents`, `workflow_stage` |
| **Outputs produced** | `response_ui.confirmation_card`, `confirmation_status = "PENDING"`, `response_message` |

When all required fields are present and no blocking validation errors exist: builds `confirmation_card` UI showing user-visible fields (title, description, dates, mall, brand, unit codes, `inspection_done_by`, `inspectionTime`, comments). Sets `confirmation_status = "PENDING"`. Respects existing `CONFIRMED`/`REJECTED` states — does not re-ask if already decided. Internal IDs (`tenant_profile_id`, `lease_id`, etc.) are not shown to the user.

---

### 6.6 Submission Pipelines

#### Payload Builders

| Node | File | Stage | Function called |
|---|---|---|---|
| `payload_builder_node` | `nodes/payload_builder_node.py` | CREATE_SR | `build_create_handover_payload(collected_data)` |
| `fm_payload_builder_node` | `nodes/fm_payload_builder_node.py` | FM_REVIEW | `build_fm_review_payload()` or `build_fm_approve_payload()` |
| `rdd_payload_builder_node` | `nodes/rdd_payload_builder_node.py` | RDD_REVIEW | `build_rdd_report_payload()` |

All builders are **pure deterministic functions** — no LLM, no I/O. They validate required keys before assembling the payload and raise `ValueError` with a descriptive message if any key is absent. Output stored in `backend_refs.create_payload` / `backend_refs.fm_payload` / `backend_refs.rdd_payload`.

#### API Submission Nodes

| Node | File | Method | Endpoint | Guards | UI Signal |
|---|---|---|---|---|---|
| `api_submission_node` | `nodes/api_submission_node.py` | POST | `/service-requests` | `confirmation_status == "CONFIRMED"` + no blocking errors + `create_payload` present | Clears `confirmation_status` (prevents CONFIRMED leaking into FM) |
| `fm_api_submission_node` | `nodes/fm_api_submission_node.py` | PATCH | `/service-requests/{sr_id}` | Permission check + `fm_payload` present | On approve: `response_ui.stage_complete = true` |
| `rdd_api_submission_node` | `nodes/rdd_api_submission_node.py` | POST | `/service-requests` (with `status=REPORT_SUBMITTED`) | Permission check + `rdd_payload` present | On final approve: `response_ui.workflow_complete = true` |

**Path A:** `api_submission_node` (CREATE_SR) → sets `workflow_stage = "SR_CREATED"`, `status = "SUBMITTED"`, stores `sr_id` in `backend_refs`.

**Path B:** `api_submission_node` (HANDED_OVER) → posts with `sub_category=HANDED_OVER` and `already_handovered=already_trading`. SR is **auto-approved** by the platform in a single transaction. Sets `workflow_stage = "HANDED_OVER_COMPLETED"`, `response_ui.workflow_complete = true`.

On `rdd_api_submission_node` (Phase 3b final approve) success, sets `workflow_stage = "SR_COMPLETED"`.

---

### 6.7 Turn Finalization

#### `response_generation_node`

| | |
|---|---|
| **File** | `app/agents/graph/nodes/response_generation_node.py` |
| **Trace run_type** | `AGENT` |
| **LLM call** | Yes — `LLMGateway.complete_json(RESPONSE_GENERATION_SYSTEM_PROMPT, context)` |
| **Inputs consumed** | `response_message` (hint), `workflow_stage`, `collected_data`, `missing_fields`, `validation_errors`, `confirmation_status`, `response_ui`, `conversation_history` (last 8 turns) |
| **Outputs produced** | `response_message` (polished), `status = "WAITING_FOR_USER"` (unless terminal) |

Takes the machine-generated `response_message` hint set by earlier nodes and polishes it into a natural-language reply using `build_response_generation_context()`. Falls back to the raw hint on LLM failure. Sets `status = "WAITING_FOR_USER"` unless `status` is already a terminal value (`SUBMITTED`, `FAILED`, `COMPLETED`).

#### `save_state_node`

| | |
|---|---|
| **File** | `app/agents/graph/nodes/save_state_node.py` |
| **Trace run_type** | — |

Calls `ConversationStateService.save_checkpoint(state)` which upserts the `ServiceRequestDraft` row. Explicitly **does not** persist `action_override` or `corrected_fields`.

---

## 7. Conditional Routing

All routing is implemented as pure Python functions in `app/agents/graph/help_agent_graph.py`. No LLM is involved in any routing decision.

### `_AGENT_ENTRY_NODES` Dispatch Table

```python
_AGENT_ENTRY_NODES = {
    "rdd_handover_agent":  "handover_entry",
    "work_permit_agent":   "handover_entry",   # WP uses same entry node pattern
    # FM/RDD are routed by workflow_stage after sr_status_sync, not by active_agent
}
```

### Router Functions

| Router | Key logic |
|---|---|
| `_route_after_load` | Preview phrase → `supervisor`; `sr_id` present → `sr_status_sync`; `active_agent` set → entry node (via `_AGENT_ENTRY_NODES`); else → `supervisor` |
| `_route_after_document_upload` | Transparent pass-through — same logic as `_route_after_load` (runs after doc partitioning) |
| `_route_after_sync` | `FM_REVIEW` → `fm_review_entry`; `RDD_REVIEW` → `rdd_review_entry`; `SR_CREATED` / `SR_COMPLETED` → `supervisor`; `CREATE_SR` + `active_agent` → `handover_entry` |
| `_route_after_supervisor` | `PREVIEW_SERVICE_REQUEST` → `preview`; `ASK_HELP` / `UNKNOWN` → `faq_node`; `WAITING_FOR_USER` (RBAC denied) → `response_generation`; else → `registry` |
| `_route_after_faq` | Always → `response_generation` |
| `_route_after_registry` | `WAITING_FOR_USER` → `response_generation`; else → entry node (via `_AGENT_ENTRY_NODES`) |
| `_route_after_handover_entry` | `active_agent` cleared → `response_generation`; cancel SR confirmed → `response_generation`; `action_override == "cancel"` → `merge_state`; else → `field_extraction` |
| `_route_after_fm_entry` | `WAITING_FOR_USER` → `response_generation`; `fm_action` set → `merge_state`; else → `field_extraction` |
| `_route_after_rdd_entry` | `WAITING_FOR_USER` → `response_generation`; `rdd_action` set → `merge_state`; else → `field_extraction` |
| `_route_after_merge` | `selected_lease` set or `lease_id` absent + stage is `CREATE_SR` / `HANDED_OVER` → `lease_lookup`; FM_REVIEW / RDD_REVIEW stages → skip lease, go to `validation`; else → `validation` |
| `_route_after_lease` | `WAITING_FOR_USER` → `response_generation`; else → `validation` |
| `_route_after_validation` | Blocking errors or missing fields → `missing_field`; `CREATE_SR` complete → `confirmation`; `HANDED_OVER` complete → `confirmation`; `FM_REVIEW` complete → `fm_confirmation`; `RDD_REVIEW` complete → `rdd_confirmation`; terminal stage → `response_generation` |
| `_route_after_confirmation` | `CONFIRMED` → `payload_builder`; else → `response_generation` |
| `_route_after_fm_confirmation` | `CONFIRMED` → `fm_payload_builder`; else → `response_generation` |
| `_route_after_rdd_confirmation` | `CONFIRMED` → `rdd_payload_builder`; else → `response_generation` |

---

## 8. Workflow Stages

**Source of truth:** `app/agents/schemas/handover_schema.py` — `STAGE_REGISTRY` and `WorkflowConfig`

```mermaid
flowchart LR
    subgraph PathB["Path B — Already Trading (HANDED_OVER)"]
        B_CREATE["CREATE_HANDED_OVER_SR\nMall Manager\n4 actual dates + lease\nAuto-approved"]
        B_DONE["HANDED_OVER_COMPLETED\nworkflow_complete=true"]
        B_CREATE -->|"POST sub_category=HANDED_OVER\nalready_handovered=already_trading"| B_DONE
    end

    subgraph PathA["Path A — Standard (HANDOVER / FOS / END / TRADING)"]
        CREATE["CREATE_SR\nMall Manager\nCollect fields + lease\nSubmit SR"]
        FM["FM_REVIEW\nFM Manager / Operations\nDates + 3 documents\nSave / Approve"]
        RDD["RDD_REVIEW\nDD Engineer\n5 dates + guideline + report\nSubmit → Final Approve"]
        DONE["SR_COMPLETED"]

        CREATE -->|"POST /service-requests\n→ sr_id"| SR_CREATED["SR_CREATED"]
        SR_CREATED -->|"Platform: FM_MANAGER IN_PROGRESS"| FM
        FM -->|"PATCH APPROVED"| RDD_GATE["Platform: DD_ENGINEER IN_PROGRESS"]
        RDD_GATE --> RDD
        RDD -->|"Phase 3a: POST REPORT_SUBMITTED\nPhase 3b: PATCH APPROVED"| DONE
    end
```

### Stage Definitions

#### CREATE_SR (Path A)

| Property | Value |
|---|---|
| `stage` | `"CREATE_SR"` |
| `role` | `"MALL_MANAGER"` |
| `required_fields` | `tenant_profile_id`, `property_id`, `lease_code`, `lease_id`, `brand_id`, `mall`, `brand`, `unit_codes`, `city`, `contracted_area`, `title`, `description`, `startDate`, `endDate`, `inspectionTime`, `inspection_done_by`, `comments` |
| `required_documents` | *(none — MM document upload offered only after SR creation)* |
| Backend-derived fields | `tenant_profile_id`, `property_id`, `lease_code`, `lease_id`, `brand_id`, `mall`, `brand`, `unit_codes`, `city`, `contracted_area` (all from lease lookup) |
| Auto-generated fields | `title = "handover-{lease_code}-{description_slug}"` |
| User-supplied fields | `description`, `startDate`, `endDate`, `inspectionTime`, `inspection_done_by`, `comments` |
| Sub-categories | `HANDOVER`, `FOS` (Fit-Out Start), `END` (End of lease), `TRADING` |

#### CREATE_HANDED_OVER_SR (Path B — Already Trading fast-path)

| Property | Value |
|---|---|
| `stage` | `"CREATE_SR"` (shared stage, differentiated by `sub_category`) |
| `sub_category` | `"HANDED_OVER"` |
| `role` | `"MALL_MANAGER"` |
| `required_fields` | `lease_id`, `actual_handover_date`, `fitout_start_date`, `fitout_end_date`, `trading_date` (minimal set vs Path A) |
| Platform payload | `already_handovered = "already_trading"`, `sub_category = "HANDED_OVER"` |
| Outcome | SR **auto-approved** in a single POST — no FM or RDD review |
| UI signal | `response_ui.workflow_complete = true` |

#### FM_REVIEW (Path A)

| Property | Value |
|---|---|
| `stage` | `"FM_REVIEW"` |
| `role` | `"FM_MANAGER"` or `"OPERATIONS"` |
| `required_fields` | `unit_readiness_date`, `expected_handover_date`, `comments` |
| `required_documents` | `SR_HANDOVER_CHECKLIST`, `SR_HANDOVER_SITE_SURVEY`, `SR_COP_CHECKLIST_OTHER` |
| Actions | `save_fm_progress` (PATCH `IN_PROCESS`), `approve_fm_review` (PATCH `APPROVED`), `reject_fm_review` |
| UI signal on approve | `response_ui.stage_complete = true` |

#### RDD_REVIEW (Path A)

| Property | Value |
|---|---|
| `stage` | `"RDD_REVIEW"` |
| `role` | `"DD_ENGINEER"` |
| `required_fields` | `guideLineLink`, `actual_handover_date`, `fitout_start_date`, `fitout_end_date`, `trading_date`, `comments` |
| `required_documents` | `DR_SR_HANDOVER_REPORT` |
| Date constraint | `actual_handover_date ≤ fitout_start_date ≤ fitout_end_date ≤ trading_date` |
| Auto-compute | `trading_date = fitout_end_date + 8 days` if not explicitly provided |
| Date wire format | `DD/MM/YYYY` (converted from ISO 8601 by `PayloadBuilderService`) |
| Phase 3a | `submit_rdd_report` → POST `REPORT_SUBMITTED` |
| Phase 3b | Final approve → PATCH `APPROVED` → `SR_COMPLETED` |
| UI signal on complete | `response_ui.workflow_complete = true` |

### Stage Helpers

```python
# app/agents/schemas/handover_schema.py
get_stage(stage_key: str) -> StageDefinition
get_required_fields(stage_key: str) -> tuple[str, ...]
get_missing_fields(stage_key: str, collected_data: dict) -> list[str]
role_can_act_on_stage(role: str, stage_key: str) -> bool
```

---

## 9. LLM Integration

### LLM Gateway

**File:** `app/agents/llm/gateway.py`

```python
class LLMGateway:
    async def complete_json(
        self,
        system_prompt: str,
        user_content: str,
    ) -> tuple[dict, int, int, float]:
        # Returns: (parsed_json, input_tokens, output_tokens, latency_ms)
```

- **Provider:** OpenAI Chat Completions (`openai.AsyncOpenAI`)
- **Model:** `settings.llm_model` (default: `gpt-4o-mini`)
- **Forced JSON mode:** `response_format={"type": "json_object"}`
- **Temperature:** `0.0`
- **Singleton:** `get_default_gateway()` / `set_default_gateway()` (for test injection)

### Four LLM Call Sites

```mermaid
graph TD
    S1["1. supervisor_node\nsupervisor_prompt.py"]
    S2["2. field_extraction_node\nFieldExtractionService\nhandover_extraction_prompt.py"]
    S3["3. faq_node\nFAQ_SYSTEM_PROMPT\n+ Azure AI Search RAG chunks"]
    S4["4. response_generation_node\nresponse_generation_prompt.py"]

    S1 -->|"→ SupervisorDecision\n(intent, service_category, confidence)"| R1["Intent classification + RBAC"]
    S2 -->|"→ HandoverExtractedFields\n{field: {value, confidence}}\nUp to 2 retries"| R2["Field extraction"]
    S3 -->|"→ {message: str, sources: [...]}\nFallback: embedded FAQ_SYSTEM_PROMPT only"| R3["Platform Q&A with RAG"]
    S4 -->|"→ {message: str}\nFalls back to raw hint on failure"| R4["Natural language reply"]
```

| Call site | Prompt file | Output schema | Retry / Fallback |
|---|---|---|---|
| `supervisor_node` | `prompts/supervisor_prompt.py` → `SUPERVISOR_SYSTEM_PROMPT` | `SupervisorDecision` (Pydantic) | No retry; low confidence → `UNKNOWN` → routes to `faq_node` |
| `field_extraction_node` | `prompts/handover_extraction_prompt.py` → `HANDOVER_EXTRACTION_SYSTEM_PROMPT` | `HandoverExtractedFields` (Pydantic) | Up to 2 retries on JSON/validation failure; returns empty on total failure |
| `faq_node` | `prompts/faq_prompt.py` → `FAQ_SYSTEM_PROMPT` + retrieved RAG chunks | `{"message": "...", "sources": [...]}` | Falls back to embedded FAQ_SYSTEM_PROMPT if Azure Search unavailable |
| `response_generation_node` | `prompts/response_generation_prompt.py` → `RESPONSE_GENERATION_SYSTEM_PROMPT` | `{"message": "..."}` | Falls back to raw `response_message` hint on failure |

### Prompt Design

**Supervisor prompt** (`SUPERVISOR_SYSTEM_PROMPT`): Intent classification and RBAC gating. Outputs one of the 8 intent literals with confidence and optional `service_category` / `sub_category`. Includes role-aware intent allowlist. Chain-of-thought via `reasoning` field (stripped before storage).

**Extraction prompt** (`HANDOVER_EXTRACTION_SYSTEM_PROMPT`): Extract only explicitly stated or strongly implied field values. Per-field confidence score. Rules enforced:
- Date normalization to ISO 8601
- `inspectionTime` extracted and preserved (combined into ISO 8601 timestamp with `startDate` by payload builder)
- `inspection_done_by` mapped to `FM_MANAGER` / `OPERATIONS` enum
- Active-field context applied — bare answers resolved against the field currently being asked
- Forbidden backend fields never extracted
- Declinations (user says "no description") → empty string `""`

**FAQ prompt** (`FAQ_SYSTEM_PROMPT`): Answers platform Q&A in conversational style. Retrieved Azure AI Search chunks are prepended as context. Includes inline `[Source: ...]` citation markers that map to `faq_sources[]` in the API response. Language-aware when `language == "ar"`.

**Response generation prompt** (`RESPONSE_GENERATION_SYSTEM_PROMPT`): Compose natural reply from structured context. User-context built by `build_response_generation_context()` including: response intent hint, workflow stage, collected data, missing fields, validation errors, confirmation status, UI type, conversation history (last 8 turns). Output `{"message": "..."}`.

---

## 10. Domain Services

### `LeaseLookupService`

**File:** `app/agents/services/lease_lookup_service.py`

Adapter pattern — two implementations selected by factory:

| Implementation | When used | Behaviour |
|---|---|---|
| `HttpLeaseLookupService` | `settings.lease_tenant_api_base_url` is set | `GET {LEASE_TENANT_API_BASE_URL}/leases?user_id=...` |
| `MockLeaseLookupService` | No URL configured | In-memory seed data (Under Armour, Nike, Zara, etc.) |

Factory: `get_lease_lookup_service()` — enables local development without external dependencies.

Search priority: `lease_code` → `brand + mall` → `brand` → `mall`.

Returns `LeaseLookupResult(matches: list[LeaseRecord], status, latency_ms, raw_payloads)`.

### `FieldExtractionService`

**File:** `app/agents/services/field_extraction_service.py`

Wraps `LLMGateway` with retry and validation logic:

1. Call `LLMGateway.complete_json(HANDOVER_EXTRACTION_SYSTEM_PROMPT, user_content)`.
2. Parse response into `HandoverExtractedFields` (Pydantic — strips `BACKEND_ONLY_FIELDS`).
3. On JSON or validation error → retry (up to 2 additional attempts).
4. On total failure → return empty `HandoverExtractedFields` (never raises).

Returns `(HandoverExtractedFields, ExtractionTraceMeta)`.

### `ValidationService`

**File:** `app/agents/services/validation_service.py`

Fully deterministic — no LLM. `validate_draft(stage, collected_data, documents, role=None)` runs all applicable rules and returns only `FAILED` `ValidationResult` dicts. Rules are stage-aware via `STAGE_REGISTRY`. All validation errors have a `blocking: bool` flag. See [handover-workflow.md](handover-workflow.md) for the complete rule table.

### `PayloadBuilderService`

**File:** `app/agents/services/payload_builder_service.py`

Four pure mapping functions — no I/O, no LLM:

| Function | Stage | Validates required keys | Produces |
|---|---|---|---|
| `build_create_handover_payload(data)` | CREATE_SR | Yes — raises `ValueError` | `backend_refs.create_payload` |
| `build_fm_review_payload(data, backend_refs)` | FM_REVIEW | Yes | `backend_refs.fm_payload` |
| `build_fm_approve_payload(data, backend_refs, comment)` | FM_REVIEW | Yes | `backend_refs.fm_payload` |
| `build_rdd_report_payload(data, backend_refs)` | RDD_REVIEW | Yes | `backend_refs.rdd_payload` |

RDD builder normalises dates from ISO 8601 to `DD/MM/YYYY` per the platform convention.

### `ServiceRequestPlatformClient`

**File:** `app/agents/services/platform_api_client.py`

**Single gateway** for all Cenomi platform HTTP calls. No graph node calls `httpx` directly.

| Method | HTTP | Endpoint | Purpose |
|---|---|---|---|
| `login()` | POST | `/cenomi-ai/login` | Service-to-service Bearer token (cached 1 hour) |
| `get_workflows()` | GET | `/service-requests/workflows` | Form skeleton |
| `create_service_request(payload)` | POST | `/service-requests` | CREATE_SR submission |
| `get_service_request(sr_id)` | GET | `/service-requests/{sr_id}` | Status sync |
| `patch_service_request(sr_id, payload)` | PATCH | `/service-requests/{sr_id}` | FM save/approve |
| `submit_report(payload)` | POST | `/service-requests` | RDD report submission |
| `upload_file(metadata, bytes)` | PUT | `/files` | Document upload |

Singleton via `get_platform_client()`. Sensitive fields redacted before trace persistence.

Auth: `PLATFORM_LOGIN_EMAIL` + `PLATFORM_INTERNAL_API_TOKEN` → `x-internal-api-token`. This is service-to-service auth, separate from user JWT.

Also: `ServiceRequestAPIService` (`service_request_api_service.py`) wraps the platform client with `HttpServiceRequestAPIService` / `MockServiceRequestAPIService` adapter pattern for submission nodes (falls back to mock when `SERVICE_REQUEST_API_BASE_URL` is unset).

### `DocumentUploadService`

**File:** `app/agents/services/document_upload_service.py`

Thin wrapper over `ServiceRequestPlatformClient.upload_file()`. Maps domain args to `FileUploadMetadata`. Returns `DocumentUploadResult(document_id, signed_url, file_path)` or error. Used by `POST /api/v1/upload` route and `document_upload_node`.

### `ConversationStateService`

**File:** `app/agents/services/conversation_state_service.py`

Manages the round-trip between the graph state and the database:

- `load(session_id)` → hydrates `ServiceRequestGraphState` from `ServiceRequestDraft`
- `save_checkpoint(state)` → upserts `ServiceRequestDraft` with `collected_data`, `missing_fields`, `documents`, `backend_refs`, `workflow_stage`

### `PermissionService`

**File:** `app/agents/services/permission_service.py`

Centralized authorization with fail-closed design:

```python
ACTION_PERMISSION_MAP = {
    "CREATE_HANDOVER_SR": "CAN_RAISE_HANDOVER_SR",
    "UPLOAD_FM_HANDOVER_DOCUMENT": "CAN_FM_REVIEW_HANDOVER_SR",
    "SAVE_FM_HANDOVER_PROGRESS": "CAN_FM_REVIEW_HANDOVER_SR",
    "APPROVE_FM_HANDOVER": "CAN_APPROVE_FM_HANDOVER_SR",
    "REJECT_FM_HANDOVER": "CAN_APPROVE_FM_HANDOVER_SR",
    "UPLOAD_RDD_HANDOVER_REPORT": "CAN_RDD_REVIEW_HANDOVER_SR",
    "SUBMIT_RDD_HANDOVER_REPORT": "CAN_RDD_REVIEW_HANDOVER_SR",
    "VIEW_HANDOVER_SR": "VIEW_FIT_OUT_HANDOVER",
    # Unknown actions: fail-closed → PermissionDeniedError raised immediately
}
```

### `AuditService`

**File:** `app/agents/services/audit_service.py`

Append-only wrapper around `AuditLogRepository`. Records `actor_id`, `action`, `resource_type`, `resource_id`, `details`. Note: `ChatOrchestrationService` also uses `AuditLogRepository.create()` directly for session-scoped events (`turn.started`, `security.injection_attempt`, `turn.completed`).

---

## 11. Security Design

> See [security-guardrails.md](security-guardrails.md) for full implementation details with code excerpts.

```mermaid
flowchart TD
    MSG["User Message"]
    IG["Layer 1: Injection Guard\ninjection_guard.scan_message()"]
    AUTH["Layer 2: Permission Check\nPermissionService (upload route, FM/RDD entry)"]
    EXTRACT["LLM Field Extraction\n(untrusted input)"]
    MERGE["Layer 3: Backend Field Protection\nmerge_state_node BACKEND_PROTECTED_FIELDS"]
    VALID["Layer 4: Required Field Enforcement\nValidationService (blocking errors)"]
    CONFIRM["Layer 5: Confirmation Enforcement\nhandover_entry_node keyword regex + api_submission_node hard guard"]
    SUBMIT["Service Request API"]

    MSG --> IG
    IG -->|"score ≥ 0.7 HIGH_RISK"| BLOCK["Refusal\nMessage NOT persisted\nGraph NOT invoked\nfail_trace + audit"]
    IG -->|"clean"| AUTH
    AUTH -->|"unauthorized"| DENY["PermissionDeniedError"]
    AUTH -->|"authorized"| EXTRACT
    EXTRACT --> MERGE
    MERGE --> VALID
    VALID -->|"blocking errors"| ASK["Ask user to correct"]
    VALID -->|"all valid"| CONFIRM
    CONFIRM -->|"not CONFIRMED"| WAIT["Wait for user"]
    CONFIRM -->|"CONFIRMED + no errors + payload present"| SUBMIT
```

### Layer 1 — Injection Guard

**File:** `app/core/injection_guard.py`

`scan_message(message)` applies a compiled regex catalog. Runs **before** the message is persisted or the graph is invoked. On `HIGH_RISK_THRESHOLD = 0.7`:
1. Audit `security.injection_attempt`
2. `TraceManager.fail_trace()`
3. Refusal returned to user
4. **Message NOT written to `chat_messages`**
5. **Graph NOT invoked**

### Layer 2 — Permission Checks

`PermissionService.check(action, auth)` raises `PermissionDeniedError` for unauthorized actions. **Unknown actions fail-closed** — any unrecognized action string raises immediately.

### Layer 3 — Backend Field Protection

Two sub-layers prevent LLM from injecting or overwriting backend-derived data:
- **Pydantic validator** on `HandoverExtractedFields` strips `BACKEND_ONLY_FIELDS` from LLM output before it reaches `merge_state_node`.
- **`BACKEND_PROTECTED_FIELDS` guard** in `merge_state_node` silently skips any attempt to overwrite `tenant_profile_id`, `property_id`, `brand_id`, `lease_id`, `contract_id`, `unit_codes`, `city`, `contracted_area`, `lease_brand_mall`.
- **Lease-confirmed lock** — once `lease_id` is resolved, `lease_code`, `mall`, `brand` become immutable.

### Layer 4 — Required Field Enforcement

`ValidationService` enforces all required fields with blocking errors before routing can reach `confirmation_node` or `payload_builder_node`.

### Layer 5 — Confirmation Enforcement

**`handover_entry_node` (text layer):** Parses `_CONFIRM_PHRASES` / `_REJECT_PHRASES` via word-boundary regex. UI `action_override` takes priority. Ambiguous input → clarification, no status change.

**`api_submission_node` (hard guard):** Three independent conditions checked before any API call:
1. `confirmation_status == "CONFIRMED"`
2. No blocking `validation_errors`
3. `backend_refs.create_payload` present

### Auth Gap Note (POC)

The main `POST /api/chat/service-request` endpoint accepts `user_id` from the request body and does **not** call `get_auth_context`. JWT scaffolding exists in `app/core/security.py` but real token validation and role population from token claims are TODO for production. See [security-guardrails.md](security-guardrails.md).

---

## 12. Database Design

> See [architecture.md](architecture.md) for the full ER diagram.

```mermaid
erDiagram
    chat_sessions {
        uuid id PK
        uuid user_id
        text active_agent
        text intent
        text workflow_stage
        text status
        timestamptz created_at
        timestamptz updated_at
    }
    chat_messages {
        uuid id PK
        uuid session_id FK
        text role
        text content
        jsonb metadata
        timestamptz created_at
    }
    service_request_drafts {
        uuid id PK
        uuid session_id FK
        text service_category
        text sub_category
        text workflow_stage
        jsonb collected_data
        jsonb missing_fields
        jsonb documents
        text sr_id
        text service_request_status
        boolean ready_to_submit
        timestamptz created_at
        timestamptz updated_at
    }
    service_request_chat_audit_logs {
        uuid id PK
        uuid session_id FK
        text action
        uuid actor_user_id
        jsonb before_state
        jsonb after_state
        jsonb metadata
        timestamptz created_at
    }
    agent_traces {
        uuid id PK
        uuid session_id FK
        uuid user_id
        text trace_type
        text status
        text input_message
        text output_message
        integer total_latency_ms
        integer total_token_count
        numeric estimated_cost
        jsonb metadata
        timestamptz created_at
        timestamptz completed_at
    }
    agent_runs {
        uuid id PK
        uuid trace_id FK
        uuid parent_run_id FK
        text run_name
        text run_type
        text node_name
        text status
        jsonb input
        jsonb output
        integer latency_ms
        timestamptz started_at
        timestamptz completed_at
    }
    agent_state_snapshots {
        uuid id PK
        uuid trace_id FK
        uuid run_id FK
        text snapshot_type
        jsonb state
        timestamptz created_at
    }
    agent_state_diffs {
        uuid id PK
        uuid trace_id FK
        uuid run_id FK
        jsonb diff
        timestamptz created_at
    }
    agent_llm_calls {
        uuid id PK
        uuid trace_id FK
        uuid run_id FK
        text provider
        text model
        text prompt_name
        integer input_tokens
        integer output_tokens
        integer latency_ms
        numeric estimated_cost
        jsonb structured_output
        boolean parse_success
        timestamptz created_at
    }
    agent_tool_calls {
        uuid id PK
        uuid trace_id FK
        uuid run_id FK
        text tool_name
        text tool_type
        jsonb request_payload
        jsonb response_payload
        integer status_code
        boolean success
        integer latency_ms
        timestamptz created_at
    }
    agent_feedback {
        uuid id PK
        uuid trace_id FK
        uuid run_id FK
        uuid user_id
        text feedback_type
        integer score
        text label
        text comment
        timestamptz created_at
    }

    chat_sessions ||--o{ chat_messages : "has"
    chat_sessions ||--o| service_request_drafts : "has"
    chat_sessions ||--o{ service_request_chat_audit_logs : "has"
    chat_sessions ||--o{ agent_traces : "has"
    agent_traces ||--o{ agent_runs : "contains"
    agent_runs ||--o{ agent_state_snapshots : "has"
    agent_runs ||--o{ agent_state_diffs : "has"
    agent_runs ||--o{ agent_llm_calls : "has"
    agent_runs ||--o{ agent_tool_calls : "has"
    agent_traces ||--o{ agent_feedback : "has"
```

### Repository Layer

**File:** `app/db/repositories/`

| Repository | Model | Key operations |
|---|---|---|
| `ChatSessionRepository` | `ChatSession` | `create`, `get_by_id`, `update(active_agent, intent, workflow_stage, status)` |
| `ChatMessageRepository` | `ChatMessage` | `create`, `get_by_id`, `list_by_session`, `list_recent_by_session(n)` |
| `ServiceRequestDraftRepository` | `ServiceRequestDraft` | `create`, `update`, `get_by_id`, `get_by_session` (most recent), `list_by_session` |
| `AuditLogRepository` | `ServiceRequestChatAuditLog` | `create` (append-only), `get_by_id`, `list_by_session` |

All repositories are async-first via SQLAlchemy + asyncpg.

### Migration History

| Migration | Tables created |
|---|---|
| `001_initial_schema.py` | `chat_sessions`, `chat_messages`, `service_request_drafts`, `service_request_chat_audit_logs`, legacy `observability_*` stubs |
| `002_agent_observability.py` | `agent_traces`, `agent_runs`, `agent_state_snapshots`, `agent_state_diffs`, `agent_llm_calls`, `agent_tool_calls`, `agent_feedback` |
| `003_users.py` | `users` table — `id`, `email`, `hashed_password`, `role`, `permissions`, `unique_property_ids`, `is_active`, timestamps |
| `004_*` | Any additional schema updates — check `alembic/versions/` |

Run migrations: `alembic upgrade head`

---

## 13. Observability Architecture

> See [observability.md](observability.md) for the full reference including Admin UI components.

### Component Overview

| Component | File | Role |
|---|---|---|
| `TraceManager` | `app/observability/trace_manager.py` | Facade for all trace/run lifecycle, snapshot, diff, and call capture |
| `@trace_node` | `app/observability/decorators.py` | Wraps LangGraph nodes with automatic span instrumentation |
| `build_json_diff` | `app/observability/state_diff.py` | Computes added/changed/removed key diffs between states |
| `MetricsRegistry` | `app/observability/metrics.py` | In-process counters (Prometheus-ready stub) |
| `sanitize_state_for_trace` | `app/observability/serializers.py` | Strips runtime objects and CoT fields before persistence |
| `redact_payload` | `app/observability/redaction.py` | Recursive key-based redaction of sensitive fields |

### Trace Hierarchy

```mermaid
graph TD
    AT["AgentTrace\none per user turn\nstatus: RUNNING → COMPLETED/FAILED"]

    AT --> R1["AgentRun: supervisor\nrun_type=SUPERVISOR"]
    AT --> R2["AgentRun: field_extraction\nrun_type=AGENT"]
    AT --> R3["AgentRun: lease_lookup\nrun_type=TOOL"]
    AT --> R4["AgentRun: validation\nrun_type=AGENT"]
    AT --> R5["AgentRun: api_submission\nrun_type=TOOL"]

    R1 --> L1["AgentLLMCall\n(supervisor)"]
    R1 --> S1a["StateSnapshot: BEFORE_NODE"]
    R1 --> S1b["StateSnapshot: AFTER_NODE"]
    R1 --> D1["AgentStateDiff"]

    R2 --> L2["AgentLLMCall\n(field extraction)"]
    R2 --> S2a["StateSnapshot: BEFORE_NODE"]
    R2 --> S2b["StateSnapshot: AFTER_NODE"]
    R2 --> D2["AgentStateDiff"]

    R5 --> TC["AgentToolCall\n(SR API POST)"]
    R5 --> S5["StateSnapshot: PAYLOAD_BUILDER_OUTPUT"]
```

### `@trace_node` Decorator Flow

```python
@trace_node("supervisor", "SUPERVISOR")
async def supervisor_node(state: ServiceRequestGraphState) -> dict:
    ...
```

For each decorated node:
1. Extract `trace_manager` + `trace_id` from state
2. `start_run()` → opens `AgentRun` with `status = "RUNNING"`
3. Capture `BEFORE_NODE` state snapshot
4. Execute node function
5. Capture `AFTER_NODE` state snapshot
6. `capture_state_diff(before, after)`
7. `finish_run()` → updates `AgentRun` to `COMPLETED`

**All trace operations are fail-safe** — exceptions in `TraceManager` are caught and logged; they never propagate to crash the graph execution.

### State Diff Format

```json
{
  "added": {
    "collected_data.title": "handover-T0028604-unit-ready-for-handover"
  },
  "removed": {},
  "changed": {
    "workflow_stage": {"before": null, "after": "CREATE_SR"},
    "status": {"before": "IN_PROGRESS", "after": "WAITING_FOR_USER"}
  }
}
```

### Observability API Endpoints

| Endpoint | Purpose |
|---|---|
| `GET /api/observability/traces` | Paginated trace list (`session_id`, `agent`, `status`, `limit`, `offset`) |
| `GET /api/observability/traces/{trace_id}` | Trace detail with full run tree |
| `POST /api/observability/traces/{trace_id}/feedback` | Submit user feedback (`score`, `label`, `comment`) |
| `GET /api/v1/observability/metrics/summary` | Aggregate metrics counters |

Admin UI at `/admin/agent-observability` — components: `TraceList`, `TraceDetail`, `ReplayViewer`, `LLMCallViewer`, `ToolCallViewer`, `StateDiffViewer`.

### Redaction Policy

Sensitive fields replaced with `"[REDACTED]"` before any persistence or API response:

| Category | Fields |
|---|---|
| Auth credentials | `jwt_secret_key`, `authorization`, `password`, `token`, `api_key` |
| Internal IDs (in payloads) | `tenant_profile_id`, `property_id`, `brand_id`, `lease_id` |
| LLM chain-of-thought | `reasoning`, `chain_of_thought`, `thoughts` |

---

## 14. Configuration Reference

**File:** `app/core/config.py` — Pydantic `Settings` model. Source: `.env` / `.env.local`.

| Variable | Default | Required in prod | Purpose |
|---|---|---|---|
| `APP_NAME` | `service-request-chatbot-api` | — | Application name tag |
| `ENVIRONMENT` | `development` | — | Runtime environment |
| `DEBUG` | `false` | — | Debug logging |
| `API_V1_PREFIX` | `/api/v1` | — | API prefix for health + upload |
| `CORS_ORIGINS` | `http://localhost:3000` | Yes | Comma-separated allowed origins |
| `DATABASE_URL` | `postgresql+asyncpg://postgres:postgres@localhost:5432/service_request_chatbot` | Yes | Async PostgreSQL connection |
| `REDIS_URL` | `redis://localhost:6379/0` | — | Redis (future use) |
| `OPENAI_API_KEY` | *(empty)* | Yes | LLM API key |
| `LLM_MODEL` | `gpt-4o-mini` | — | OpenAI model name |
| `LLM_BASE_URL` | *(empty)* | — | Optional OpenAI-compatible endpoint |
| `LLM_CONFIDENCE_THRESHOLD` | `0.6` | — | Supervisor routing confidence threshold |
| `SERVICE_REQUEST_API_BASE_URL` | *(empty)* | Yes (prod) | Cenomi SR API — unset → mock adapter |
| `LEASE_TENANT_API_BASE_URL` | *(empty)* | Yes (prod) | Cenomi lease API — unset → mock adapter |
| `FILE_UPLOAD_API_BASE_URL` | *(empty)* | Yes (prod) | File upload API |
| `PLATFORM_AUTH_BASE_URL` | *(empty)* | Yes (prod) | Platform auth — defaults to SR API base |
| `PLATFORM_INTERNAL_API_TOKEN` | *(empty)* | Yes (prod) | `x-internal-api-token` for service-to-service login |
| `PLATFORM_LOGIN_EMAIL` | *(empty)* | Yes (prod) | Service-to-service login email |
| `JWT_SECRET_KEY` | `change-me-in-production` | **Must change** | JWT signing secret |
| `JWT_ALGORITHM` | `HS256` | — | JWT algorithm |
| `MSP_SERVICE_TOKEN` | *(empty)* | Yes (MSP integration) | Service token validated by `POST /api/v1/chat` and `/api/v1/chat/stream` — shadow mode when unset |
| `AZURE_SEARCH_ENDPOINT` | *(empty)* | Yes (FAQ RAG) | Azure AI Search endpoint for `cenomi-help-index` |
| `AZURE_SEARCH_KEY` | *(empty)* | Yes (FAQ RAG) | Azure AI Search API key |
| `AZURE_SEARCH_INDEX_NAME` | `cenomi-help-index` | — | FAQ search index name |

**Mock mode:** When `LEASE_TENANT_API_BASE_URL` or `SERVICE_REQUEST_API_BASE_URL` are unset, factory functions return mock adapters, enabling full local development without Cenomi platform access. When `AZURE_SEARCH_ENDPOINT` is unset, `faq_node` falls back to embedded `FAQ_SYSTEM_PROMPT` knowledge only.

---

## 15. Testing Strategy

> See [testing-strategy.md](testing-strategy.md) for full fixture conventions and test authoring guide.

### Test Pyramid

```
backend/tests/
├── unit/           # ~46+ files, ~1,400+ tests — mocked AsyncSession; no DB, no network
├── integration/    # 8 files    — full graph/HTTP; mocked DB + external I/O
├── e2e/            # 3 files    — full HTTP stack via ASGITransport
└── eval/           # non-pytest — live backend scenario runner (27 tiers)

frontend/tests/e2e/
├── auth.spec.ts                  # login, redirect, role badge
├── lifecycle-stepper.spec.ts     # stage UI component tests
├── stage-actions.spec.ts         # FM/RDD action button RBAC
├── handover-lifecycle.spec.ts    # full CREATE→FM→RDD UI flow (mocked)
└── live/                         # live stack tests (real LLM + DB)
    ├── lifecycle-live.spec.ts    # 3-phase lifecycle in real browser
    ├── agent-eval.spec.ts        # 33 CREATE_SR eval scenarios
    └── lifecycle-eval.spec.ts    # 14 FM/RDD + full-lifecycle eval scenarios
```

**No Docker required for backend tests.** DB is always an `AsyncMock`; LLM, lease API, and SR API are patched. The graph and orchestration layer run for real.

### Unit Tests

**File:** `backend/tests/unit/`

Target: individual repository methods, observability components (`TraceManager`, state diff logic, redaction), graph nodes, services, and RBAC.

Notable test files: `test_agent_run_repo.py`, `test_validation_service.py` (100 tests), `test_payload_builder_service.py`, `test_fm_nodes.py`, `test_rdd_nodes.py`, `test_help_agent_graph.py` (76 tests), `test_msp_chat_endpoints.py` (18 tests), `test_security_guardrails.py`, `test_injection_guard.py`.

### Integration Tests

**File:** `backend/tests/integration/`

Target: full LangGraph `ainvoke` + `ChatOrchestrationService` with mocked external I/O.

| Test file | Coverage |
|---|---|
| `test_chat_endpoint.py` | HTTP layer for `POST /api/chat/service-request` |
| `test_help_agent_graph.py` | Full graph: intent → lease → validation → confirmation → submission |
| `test_handover_lifecycle.py` | CREATE_SR → FM_REVIEW → RDD_REVIEW lifecycle |
| `test_trace_lifecycle.py` | Observability side-effects during graph execution |
| `test_observability_endpoints.py` | Observability REST API |
| `test_fm_review_e2e.py` | FM_REVIEW: doc upload → merge → payload → API submission (14 tests) |
| `test_rdd_review_e2e.py` | RDD_REVIEW Phase 3a/3b (16 tests) |
| `test_role_stage_access.py` | Role × stage permission matrix (21 tests) |

**Key fixtures** (`tests/integration/conftest.py`): `mock_db`, `mock_llm_gateway`, `sample_lease`, `all_fields_data`, `mock_lease_service`, `mock_sr_api_service`.

### E2E Tests

**Files:** `backend/tests/e2e/`

| File | Coverage |
|---|---|
| `test_handover_sr_e2e.py` | 10 scenarios: creation, field edits, lease disambiguation, validation, API failure, permissions, injection detection |
| `test_handover_full_lifecycle_http.py` | 7 tests — 9-step CREATE_SR → FM_REVIEW → RDD_REVIEW sequence via ASGI |

### Eval (Non-pytest)

**File:** `backend/tests/eval/` · **Script:** `./run_agent_eval.sh`

Drives all 27 eval tiers against a live backend URL. Not part of the CI test suite — requires a running backend with valid platform credentials.

| Tier group | Tiers | Description |
|---|---|---|
| Smoke | `0`, `0fm`, `0p` | Basic agent + FM lifecycle + pipeline smoke tests |
| CREATE_SR | `1`, `2`, `3` | Happy path, edge cases, adversarial (CREATE_SR) |
| FM/RDD | `3a`, `3b`, `3c`, `3d`, `3e`, `3f` | FM/RDD happy, edge, battle+RBAC |
| Full lifecycle | `3g`, `3h`, `3i` | End-to-end CREATE → FM → RDD lifecycle |
| Path B | `4`, `5` | Already Trading fast-path happy + edge |
| Extended sub-types | `6a`–`6j` (+ `lc` variants) | FOS, END, TRADING sub-categories |
| FAQ + RBAC | `8` | Help agent FAQ + role-based access |

**Latest eval result (July 31, 2026):** ~98% overall across all tiers. 6 known LLM-variability scenarios (S4, S8, S23, S32, S37, S64) are not bugs.

### Running Tests

```bash
cd service-request-chatbot/backend

# Priority lifecycle suite (68 tests, ~52s)
./run_lifecycle_tests.sh
./run_lifecycle_tests.sh --coverage   # + HTML coverage report

# Full pytest suite (1,387+ tests, ~85s)
pytest tests/ -v --tb=short

# Agent evaluation — all 27 tiers via shell script
./run_agent_eval.sh                    # all tiers
./run_agent_eval.sh --tier 1           # single tier
./run_agent_eval.sh --tier 3a,3b,3c    # multiple tiers
```

```bash
cd service-request-chatbot/frontend

# Playwright mocked UI (42 tests, no backend required)
npm run test:e2e

# Playwright live stack (50 tests, real LLM + Postgres + Redis)
npm run test:live
```

```bash
# One-command full stack + all 5 test tiers
cd service-request-chatbot
./run-live-tests.sh
./run-live-tests.sh --tier unit    # pytest only
./run-live-tests.sh --tier eval    # eval scenarios
./run-live-tests.sh --no-start     # stack already running
```

---

## 16. Extensibility

> See [extensibility-guide.md](extensibility-guide.md) for the complete extensibility analysis.

### Adding a New Service Request Type

The architecture supports new agent domains with minimal changes — no frontend contract changes needed.

**Steps:**

1. **Agent Registry** (`app/agents/registry.py`) — add one entry:

```python
SERVICE_REQUEST_AGENT_REGISTRY = {
    "FIT_OUT_AND_HANDOVER": {
        "HANDOVER": {...},                # existing
        "NEW_CATEGORY": {                 # new entry
            "agent_name": "new_agent",
            "display_name": "New Agent",
            "schema_key": "new_schema",
        },
    },
    "WORK_PERMIT": {"WORK_PERMIT": {...}},  # existing
}
```

2. **Stage schema / WorkflowConfig** (`app/agents/schemas/`) — create a new `StageDefinition` with `required_fields`, `required_documents`, `role`, and `backend_fields`.

3. **Agent entry node** (`app/agents/graph/nodes/`) — create a new entry node (like `handover_entry_node.py`) that handles HITL parsing for the new workflow.

4. **Wire the graph** (`help_agent_graph.py`) — add the entry node and `_AGENT_ENTRY_NODES` dispatch entry.

5. **Payload builders + submission nodes** — follow the existing 4-builder pattern.

6. **Supervisor prompt** — add new intent literals to `SupervisorDecision` + update `SUPERVISOR_SYSTEM_PROMPT`.

7. **FAQ knowledge** (optional) — add help content to the Azure AI Search index or update `FAQ_SYSTEM_PROMPT` with static knowledge for the new SR type.

### Extensibility Ratings

| Layer | Extensibility | Notes |
|---|---|---|
| Agent Registry | Excellent | One dict entry per new agent |
| Stage schema / WorkflowConfig | Excellent | Stage-aware helpers auto-apply to new stages |
| Database | Excellent | `collected_data` is JSONB — no new migrations needed |
| Observability | Excellent | `@trace_node` is generic; zero extra code for new nodes |
| Graph topology | Good | `_AGENT_ENTRY_NODES` dispatch exists; add entry + wire nodes |
| Validation | Good | Stage-driven; rules auto-apply by stage key |
| Payload builders | Good | Pattern established; add new function + node |
| FAQ knowledge | Good | Azure AI Search index can be extended independently |
| Field extraction | Moderate | Currently tied to `HandoverExtractedFields`; needs parameterisation for new agent schema |
| Supervisor | Moderate | Requires enum + prompt change for new intent |

### What Does NOT Change When Adding a New Agent

- **Frontend contract** — the frontend sends the same 6 fields regardless of which agent is active.
- **Database schema** — `service_request_drafts.collected_data` is JSONB; no new columns needed.
- **Orchestration layer** — `ChatOrchestrationService.process_turn()` is generic.
- **Observability** — `@trace_node` instruments any new node automatically.
- **Session/state infrastructure** — `ServiceRequestGraphState`, `ConversationStateService`, `save_state_node` are all generic.
- **MSP adapter** — `msp_chat.py` wraps the same orchestration layer; new agent types are exposed automatically.

---

*Document last updated: 2026-07-31 (v1.5.0). For individual deep-dives, refer to the companion docs listed in [Section 1](#1-introduction--companion-docs).*
