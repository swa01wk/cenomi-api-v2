# Database Reference

> **Audience:** Backend engineers, DevOps, and DBAs working on the Cenomi Service-Request Chatbot.  
> **Last updated:** July 2026  
> **Engine:** PostgreSQL 16 · **ORM:** SQLAlchemy 2.x async · **Migrations:** Alembic

---

## Table of Contents

1. [Overview](#1-overview)
2. [Technology Stack](#2-technology-stack)
3. [Connection & Session Layer](#3-connection--session-layer)
4. [Schema: Domain Tables](#4-schema-domain-tables)
5. [Schema: Agent Observability Tables](#5-schema-agent-observability-tables)
6. [Schema: Legacy Observability Tables](#6-schema-legacy-observability-tables)
7. [Entity Relationship Diagram](#7-entity-relationship-diagram)
8. [Indexes](#8-indexes)
9. [Migrations (Alembic)](#9-migrations-alembic)
10. [Repository Layer](#10-repository-layer)
11. [Provisioning: Shared Instance vs. New Instance](#11-provisioning-shared-instance-vs-new-instance)
12. [Connection String Reference](#12-connection-string-reference)
13. [Operational Runbook](#13-operational-runbook)

---

## 1. Overview

The chatbot's database stores everything that needs to survive a server restart or horizontal scale-out:

| Layer | What is stored |
|-------|---------------|
| **Domain** | Chat sessions, individual messages, service-request drafts, and audit logs |
| **Agent observability** | Traces, runs, state snapshots, state diffs, LLM call records, tool call records, user feedback |
| **Auth** | Internal users (for login-gated RDD chat) |
| **Legacy observability** | Original `observability_*` tables (stub — not actively written to; do not extend) |

All tables are owned exclusively by this application. There are no cross-database foreign keys and no shared tables with the Cenomi platform database.

---

## 2. Technology Stack

| Component | Library / Version |
|-----------|------------------|
| Database | PostgreSQL 16 |
| Async driver | `asyncpg` |
| ORM | SQLAlchemy 2.x — async declarative style |
| Migration tool | Alembic |
| Session management | `async_sessionmaker` + `asynccontextmanager` |
| Settings | `pydantic-settings` (reads `DATABASE_URL` env var) |

---

## 3. Connection & Session Layer

**File:** `app/db/session.py`

The engine and session factory are module-level singletons created lazily on first use.

```
DATABASE_URL (env var)
      │
      ▼
 create_async_engine()          pool_pre_ping=True, echo=settings.debug
      │
      ▼
 async_sessionmaker()           expire_on_commit=False, autoflush=False
      │
      ▼
 get_db_session()               asynccontextmanager — commits on success, rolls back on error
      │
      ▼
 DbSession = Annotated[AsyncSession, Depends(_get_db)]   ← FastAPI dependency alias
```

### Key behaviours

- `expire_on_commit=False` — ORM objects remain accessible after `commit()` without triggering extra SELECTs. Important for async because lazy-loading raises at await boundaries.
- `autoflush=False` — Repositories call `session.flush()` explicitly after each write to get the DB-assigned id without committing the outer transaction.
- `pool_pre_ping=True` — Stale connections from the pool are dropped automatically (critical for Azure Flexible Server idle-connection resets).
- The `NullPool` is used only during Alembic migration runs so the short-lived CLI process does not hold connections open.

### Usage in repositories

```python
# In a FastAPI route (dependency injection):
async def my_route(db: DbSession):
    repo = ChatSessionRepository(db)
    session = await repo.create(user_id=user_id)

# In agent services (outside FastAPI request context):
async with get_db_session() as db:
    repo = ChatSessionRepository(db)
    ...
```

---

## 4. Schema: Domain Tables

**File:** `app/db/models.py`

### 4.1 `users`

Stores internal platform users for login-gated RDD chat. Credentials are bcrypt-hashed; role drives RBAC.

| Column | Type | Notes |
|--------|------|-------|
| `id` | `UUID` PK | `gen_random_uuid()` |
| `username` | `TEXT` UNIQUE NOT NULL | |
| `email` | `TEXT` UNIQUE NOT NULL | |
| `password_hash` | `TEXT` NOT NULL | bcrypt |
| `role` | `TEXT` NOT NULL | e.g. `tenant`, `admin` |
| `unique_property_ids` | `JSONB` | Array of mall property IDs for row-level scoping |
| `mall_names` | `JSONB` | Array of mall name strings |
| `is_global_admin` | `BOOLEAN` | Default `false` |
| `is_active` | `BOOLEAN` | Default `true`; soft-disable without deletion |
| `created_at` | `TIMESTAMPTZ` | `now()` |
| `updated_at` | `TIMESTAMPTZ` | `now()`, updated on write |

**Indexes:** `ix_users_username` (unique), `ix_users_email` (unique), `ix_users_role`

---

### 4.2 `chat_sessions`

One row per conversation. Tracks which agent is currently active and the high-level workflow stage.

| Column | Type | Notes |
|--------|------|-------|
| `id` | `UUID` PK | |
| `user_id` | `UUID` NOT NULL | References the platform user (no FK — avoids coupling) |
| `active_agent` | `TEXT` | `help_agent`, `rdd_handover_agent`, `work_permit_agent` |
| `intent` | `TEXT` | Last classified intent |
| `workflow_stage` | `TEXT` | Current stage within the active agent |
| `status` | `TEXT` | `IN_PROGRESS`, `COMPLETED`, `ABANDONED` |
| `created_at` | `TIMESTAMPTZ` | |
| `updated_at` | `TIMESTAMPTZ` | |

**Indexes:** `ix_chat_sessions_user_id`

**Updatable fields (via repository):** `active_agent`, `intent`, `workflow_stage`, `status`

**Relationships:** `messages` (1→N), `drafts` (1→N), `audit_logs` (1→N) — all `CASCADE DELETE`

---

### 4.3 `chat_messages`

Individual turns within a session. Both `user` and `assistant` roles are stored.

| Column | Type | Notes |
|--------|------|-------|
| `id` | `UUID` PK | |
| `session_id` | `UUID` FK → `chat_sessions.id` `ON DELETE CASCADE` | |
| `role` | `TEXT` NOT NULL | `user` or `assistant` |
| `content` | `TEXT` NOT NULL | Raw message text |
| `metadata` | `JSONB` | Source references, confidence scores, workflow hints |
| `created_at` | `TIMESTAMPTZ` | |

**Indexes:** `ix_chat_messages_session_id`

> The ORM attribute is named `metadata_` (Python-side) to avoid a naming collision with SQLAlchemy's reserved `metadata` attribute; the actual column name in Postgres remains `metadata`.

---

### 4.4 `service_request_drafts`

Accumulates the fields collected during the RDD / Handover / Work-Permit workflow. A session may have multiple drafts (one per workflow run), but `get_by_session()` returns the most recent one for the common case.

| Column | Type | Notes |
|--------|------|-------|
| `id` | `UUID` PK | |
| `session_id` | `UUID` FK → `chat_sessions.id` `ON DELETE CASCADE` | |
| `service_category` | `TEXT` NOT NULL | e.g. `HANDOVER`, `WORK_PERMIT` |
| `sub_category` | `TEXT` NOT NULL | Workflow sub-type |
| `workflow_stage` | `TEXT` NOT NULL | Current node / stage |
| `collected_data` | `JSONB` | Key-value map of all collected fields |
| `missing_fields` | `JSONB` | Array of field names still required |
| `documents` | `JSONB` | Array of uploaded document objects |
| `sr_id` | `TEXT` | Platform SR id after successful submission |
| `service_request_status` | `TEXT` | Status synced back from the platform |
| `ready_to_submit` | `BOOLEAN` | `true` once all fields and documents are present |
| `created_at` | `TIMESTAMPTZ` | |
| `updated_at` | `TIMESTAMPTZ` | |

**Indexes:** `ix_service_request_drafts_session_id`

**Updatable fields (via repository):** `service_category`, `sub_category`, `workflow_stage`, `collected_data`, `missing_fields`, `documents`, `sr_id`, `service_request_status`, `ready_to_submit`

---

### 4.5 `service_request_chat_audit_logs`

Append-only audit trail. A new row is inserted for every meaningful action (draft created, field updated, SR submitted, SR approved). Rows are never modified after insert.

| Column | Type | Notes |
|--------|------|-------|
| `id` | `UUID` PK | |
| `session_id` | `UUID` FK → `chat_sessions.id` `ON DELETE CASCADE` | |
| `action` | `TEXT` NOT NULL | e.g. `DRAFT_CREATED`, `SR_SUBMITTED` |
| `actor_user_id` | `UUID` | The user who triggered the action |
| `before_state` | `JSONB` | Full snapshot before the action |
| `after_state` | `JSONB` | Full snapshot after the action |
| `metadata` | `JSONB` | Extra context (IP, request-id, etc.) |
| `created_at` | `TIMESTAMPTZ` | |

**Indexes:** `ix_service_request_chat_audit_logs_session_id`

---

## 5. Schema: Agent Observability Tables

These tables form the internal tracing layer used by `app/agents/services/audit_service.py`. They capture every LLM call, tool call, and state transition for debugging and cost monitoring.

### Table hierarchy

```
agent_traces                ← one per chat turn / user interaction
  └── agent_runs            ← one per node/span executed within the trace
        ├── agent_state_snapshots   ← full state captured before/after a node
        ├── agent_state_diffs       ← delta between consecutive states
        ├── agent_tool_calls        ← external API or tool invocations
        └── agent_llm_calls         ← LLM API calls with token/cost accounting
agent_feedback              ← user or automated feedback attached to a trace or run
```

---

### 5.1 `agent_traces`

| Column | Type | Notes |
|--------|------|-------|
| `id` | `UUID` PK | |
| `session_id` | `UUID` NOT NULL | Links to `chat_sessions.id` (no FK — loose coupling) |
| `user_id` | `UUID` NOT NULL | |
| `trace_type` | `TEXT` | Default `CHAT_TURN` |
| `active_agent` | `TEXT` | Which agent handled this turn |
| `intent` | `TEXT` | Classified intent |
| `service_category` | `TEXT` | |
| `sub_category` | `TEXT` | |
| `workflow_stage_before` | `TEXT` | Stage at turn start |
| `workflow_stage_after` | `TEXT` | Stage at turn end |
| `input_message` | `TEXT` | Redacted user input |
| `output_message` | `TEXT` | Redacted assistant reply |
| `status` | `TEXT` NOT NULL | `RUNNING`, `SUCCESS`, `ERROR` |
| `error_message` | `TEXT` | Populated on failure |
| `total_latency_ms` | `INTEGER` | Wall-clock latency for the full turn |
| `total_token_count` | `INTEGER` | Sum of tokens across all LLM calls |
| `estimated_cost` | `NUMERIC(12,6)` | USD cost estimate |
| `metadata` | `JSONB` | Catch-all extension field |
| `created_at` | `TIMESTAMPTZ` | |
| `completed_at` | `TIMESTAMPTZ` | |

**Indexes:** `ix_agent_traces_session_id`, `ix_agent_traces_user_id`, `ix_agent_traces_created_at`, `ix_agent_traces_status`, `ix_agent_traces_active_agent`

---

### 5.2 `agent_runs`

One row per named span. Supports hierarchical nesting via `parent_run_id`.

| Column | Type | Notes |
|--------|------|-------|
| `id` | `UUID` PK | |
| `trace_id` | `UUID` FK → `agent_traces.id` `ON DELETE CASCADE` | |
| `parent_run_id` | `UUID` FK → `agent_runs.id` `ON DELETE SET NULL` | Self-referential for nesting |
| `run_name` | `TEXT` NOT NULL | Human-readable name |
| `run_type` | `TEXT` NOT NULL | `SUPERVISOR`, `AGENT`, `LANGGRAPH_NODE`, `LLM_CALL`, `TOOL_CALL`, `VALIDATION`, `API_CALL`, `STATE_UPDATE`, `RESPONSE_GENERATION` |
| `node_name` | `TEXT` | LangGraph node name if applicable |
| `input` | `JSONB` | Redacted node input |
| `output` | `JSONB` | Redacted node output |
| `status` | `TEXT` NOT NULL | `PENDING`, `RUNNING`, `SUCCESS`, `ERROR` |
| `error_message` | `TEXT` | |
| `latency_ms` | `INTEGER` | |
| `started_at` | `TIMESTAMPTZ` | |
| `completed_at` | `TIMESTAMPTZ` | |

**Indexes:** `ix_agent_runs_trace_id`, `ix_agent_runs_started_at`

---

### 5.3 `agent_state_snapshots`

Full JSONB capture of the LangGraph state before or after a node executes.

| Column | Type | Notes |
|--------|------|-------|
| `id` | `UUID` PK | |
| `trace_id` | `UUID` FK → `agent_traces.id` `ON DELETE CASCADE` | |
| `run_id` | `UUID` FK → `agent_runs.id` `ON DELETE CASCADE` | |
| `snapshot_type` | `TEXT` NOT NULL | `BEFORE_NODE`, `AFTER_NODE`, `BEFORE_TRACE`, `AFTER_TRACE` |
| `state` | `JSONB` NOT NULL | Full serialised agent state |
| `created_at` | `TIMESTAMPTZ` | |

**Indexes:** `ix_agent_state_snapshots_trace_id`, `ix_agent_state_snapshots_run_id`

---

### 5.4 `agent_state_diffs`

Delta (diff) between two consecutive agent states. Smaller than a full snapshot and easier to read during debugging.

| Column | Type | Notes |
|--------|------|-------|
| `id` | `UUID` PK | |
| `trace_id` | `UUID` FK → `agent_traces.id` `ON DELETE CASCADE` | |
| `run_id` | `UUID` FK → `agent_runs.id` `ON DELETE CASCADE` | |
| `diff` | `JSONB` NOT NULL | Changed fields only |
| `created_at` | `TIMESTAMPTZ` | |

**Indexes:** `ix_agent_state_diffs_trace_id`, `ix_agent_state_diffs_run_id`

---

### 5.5 `agent_tool_calls`

Records every call to an external tool or platform API.

| Column | Type | Notes |
|--------|------|-------|
| `id` | `UUID` PK | |
| `trace_id` | `UUID` FK → `agent_traces.id` `ON DELETE CASCADE` | |
| `run_id` | `UUID` FK → `agent_runs.id` `ON DELETE CASCADE` | |
| `tool_name` | `TEXT` NOT NULL | |
| `tool_type` | `TEXT` NOT NULL | `LEASE_LOOKUP`, `DOCUMENT_UPLOAD`, `SERVICE_REQUEST_CREATE`, `SERVICE_REQUEST_PATCH`, `PERMISSION_CHECK` |
| `request_payload` | `JSONB` | Redacted before persistence |
| `response_payload` | `JSONB` | Redacted before persistence |
| `status_code` | `INTEGER` | HTTP status from the downstream API |
| `success` | `BOOLEAN` | |
| `latency_ms` | `INTEGER` | |
| `error_message` | `TEXT` | |
| `created_at` | `TIMESTAMPTZ` | |

**Indexes:** `ix_agent_tool_calls_trace_id`, `ix_agent_tool_calls_run_id`

---

### 5.6 `agent_llm_calls`

Per-call record of every LLM API invocation with full token and cost accounting.

| Column | Type | Notes |
|--------|------|-------|
| `id` | `UUID` PK | |
| `trace_id` | `UUID` FK → `agent_traces.id` `ON DELETE CASCADE` | |
| `run_id` | `UUID` FK → `agent_runs.id` `ON DELETE CASCADE` | |
| `provider` | `TEXT` | `openai`, `azure_openai` |
| `model` | `TEXT` | e.g. `gpt-4o-mini` |
| `temperature` | `NUMERIC(3,2)` | |
| `prompt_name` | `TEXT` | Named prompt identifier |
| `prompt_version` | `TEXT` | Prompt version tag |
| `input_tokens` | `INTEGER` | |
| `output_tokens` | `INTEGER` | |
| `total_tokens` | `INTEGER` | |
| `latency_ms` | `INTEGER` | |
| `estimated_cost` | `NUMERIC(12,6)` | USD, calculated from token counts |
| `structured_output` | `JSONB` | Parsed structured response |
| `parse_success` | `BOOLEAN` | Whether JSON parsing succeeded |
| `parse_error` | `TEXT` | Parse error detail if failed |
| `created_at` | `TIMESTAMPTZ` | |

**Indexes:** `ix_agent_llm_calls_trace_id`, `ix_agent_llm_calls_run_id`

---

### 5.7 `agent_feedback`

Explicit feedback from users or automated evaluation pipelines.

| Column | Type | Notes |
|--------|------|-------|
| `id` | `UUID` PK | |
| `trace_id` | `UUID` FK → `agent_traces.id` `ON DELETE CASCADE` | |
| `run_id` | `UUID` FK → `agent_runs.id` `ON DELETE SET NULL` | Optional — feedback may be trace-level |
| `user_id` | `UUID` | Who gave the feedback |
| `feedback_type` | `TEXT` NOT NULL | `THUMBS_UP`, `THUMBS_DOWN`, `SCORE`, `LABEL` |
| `score` | `INTEGER` | |
| `label` | `TEXT` | |
| `comment` | `TEXT` | |
| `created_at` | `TIMESTAMPTZ` | |

**Indexes:** `ix_agent_feedback_trace_id`

---

## 6. Schema: Legacy Observability Tables

These six tables were created in migration `001` alongside the domain tables. They are stubs from the initial design and are **no longer actively written to**. Do not add new columns or write new code against them — use the `agent_*` tables (section 5) instead.

| Table | Status |
|-------|--------|
| `observability_traces` | Legacy stub — read-only |
| `observability_runs` | Legacy stub — read-only |
| `observability_llm_calls` | Legacy stub — read-only |
| `observability_tool_calls` | Legacy stub — read-only |
| `observability_state_snapshots` | Legacy stub — read-only |
| `observability_feedback` | Legacy stub — read-only |

These tables can be dropped in a future migration once any retained historical data has been exported.

---

## 7. Entity Relationship Diagram

```
users
  │
  │ (user_id, no FK)
  ▼
chat_sessions ──────────────────────────┐
  │                                      │
  ├─► chat_messages                      │
  │     (session_id FK CASCADE)          │
  │                                      │
  ├─► service_request_drafts             │
  │     (session_id FK CASCADE)          │
  │                                      │
  └─► service_request_chat_audit_logs    │
        (session_id FK CASCADE)          │
                                         │
agent_traces ◄───────────────────────────┘
  │  (session_id, no FK — loose coupling)
  │
  ├─► agent_runs ─────────────────────┐
  │     (trace_id FK CASCADE)          │
  │     (parent_run_id FK SET NULL) ◄──┘  (self-referential nesting)
  │
  ├─► agent_state_snapshots
  │     (trace_id FK CASCADE, run_id FK CASCADE)
  │
  ├─► agent_state_diffs
  │     (trace_id FK CASCADE, run_id FK CASCADE)
  │
  ├─► agent_tool_calls
  │     (trace_id FK CASCADE, run_id FK CASCADE)
  │
  ├─► agent_llm_calls
  │     (trace_id FK CASCADE, run_id FK CASCADE)
  │
  └─► agent_feedback
        (trace_id FK CASCADE, run_id FK SET NULL)
```

---

## 8. Indexes

All indexes created by Alembic migrations:

| Index | Table | Columns | Unique |
|-------|-------|---------|--------|
| `ix_users_username` | `users` | `username` | Yes |
| `ix_users_email` | `users` | `email` | Yes |
| `ix_users_role` | `users` | `role` | No |
| `ix_chat_sessions_user_id` | `chat_sessions` | `user_id` | No |
| `ix_chat_messages_session_id` | `chat_messages` | `session_id` | No |
| `ix_service_request_drafts_session_id` | `service_request_drafts` | `session_id` | No |
| `ix_service_request_chat_audit_logs_session_id` | `service_request_chat_audit_logs` | `session_id` | No |
| `ix_agent_traces_session_id` | `agent_traces` | `session_id` | No |
| `ix_agent_traces_user_id` | `agent_traces` | `user_id` | No |
| `ix_agent_traces_created_at` | `agent_traces` | `created_at` | No |
| `ix_agent_traces_status` | `agent_traces` | `status` | No |
| `ix_agent_traces_active_agent` | `agent_traces` | `active_agent` | No |
| `ix_agent_runs_trace_id` | `agent_runs` | `trace_id` | No |
| `ix_agent_runs_started_at` | `agent_runs` | `started_at` | No |
| `ix_agent_state_snapshots_trace_id` | `agent_state_snapshots` | `trace_id` | No |
| `ix_agent_state_snapshots_run_id` | `agent_state_snapshots` | `run_id` | No |
| `ix_agent_state_diffs_trace_id` | `agent_state_diffs` | `trace_id` | No |
| `ix_agent_state_diffs_run_id` | `agent_state_diffs` | `run_id` | No |
| `ix_agent_tool_calls_trace_id` | `agent_tool_calls` | `trace_id` | No |
| `ix_agent_tool_calls_run_id` | `agent_tool_calls` | `run_id` | No |
| `ix_agent_llm_calls_trace_id` | `agent_llm_calls` | `trace_id` | No |
| `ix_agent_llm_calls_run_id` | `agent_llm_calls` | `run_id` | No |
| `ix_agent_feedback_trace_id` | `agent_feedback` | `trace_id` | No |

---

## 9. Migrations (Alembic)

### 9.1 Configuration

**File:** `backend/alembic.ini` — contains only a placeholder URL.  
**File:** `backend/alembic/env.py` — overrides the URL at runtime from `app.core.config.settings.database_url`.

This means `DATABASE_URL` is the single source of truth — the value in `alembic.ini` is never used.

The `env.py` uses an **async engine** (`async_engine_from_config` + `asyncio.run()`) with `NullPool` so Alembic CLI invocations don't hold idle connections open after the migration completes.

### 9.2 Migration history

| Revision | Description | Created |
|----------|-------------|---------|
| `001` | Initial schema — domain tables (`chat_sessions`, `chat_messages`, `service_request_drafts`, `service_request_chat_audit_logs`) + legacy observability stubs | 2026-05-13 |
| `002` | Agent observability tables — `agent_traces`, `agent_runs`, `agent_state_snapshots`, `agent_state_diffs`, `agent_tool_calls`, `agent_llm_calls`, `agent_feedback` | 2026-05-13 |
| `003` | `users` table — platform users with roles and mall property scoping | 2026-06-27 |
| `004` | Data migration — renames `active_agent` value `handover_service_request_agent` → `rdd_handover_agent` in `chat_sessions` and `agent_traces` | 2026-07-01 |
| `005` | Data migration — renames `active_agent` values: `rdd_agent` → `rdd_handover_agent`; `fitout_confirm_agent` split into `rdd_fitout_start_agent`, `rdd_fitout_end_agent`, `rdd_trading_confirmation_agent` (join on `sub_category` for the split) | 2026-07-29 |

### 9.3 Running migrations

```bash
# From service-request-chatbot/backend/

# Apply all pending migrations (run on every deploy):
alembic upgrade head

# Roll back the last migration:
alembic downgrade -1

# Show current applied revision:
alembic current

# Show full migration history:
alembic history --verbose

# Generate a new auto-detected migration:
alembic revision --autogenerate -m "describe_the_change"

# Preview the SQL that would be executed without applying it:
alembic upgrade head --sql
```

> The backend Dockerfile runs `alembic upgrade head` automatically before starting uvicorn:
> ```dockerfile
> CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 2"]
> ```
> On Azure Container Apps, verify the migration step completed successfully in startup logs before the first live request.

### 9.4 Writing a new migration

1. Make the model change in `app/db/models.py`.
2. Run `alembic revision --autogenerate -m "short_description"`.
3. Review the generated file in `alembic/versions/`. Autogenerate does not detect:
   - Column **server defaults** changes
   - Changes to **CHECK constraints** or custom types
   - Table/column **renames** (it generates drop + add — write these by hand)
4. Add `down_revision` to point to the previous revision id.
5. Test `upgrade` and `downgrade` on a dev DB before merging.

---

## 10. Repository Layer

**Directory:** `app/db/repositories/`

All database access goes through repository classes — no raw SQL or ORM queries outside this directory.

### 10.1 `ChatSessionRepository`

| Method | Description |
|--------|-------------|
| `create(user_id, ...)` | Insert a new session; flushes to get the PK |
| `update(session_id, updates)` | Partial update; only `active_agent`, `intent`, `workflow_stage`, `status` are allowed |
| `get_by_id(session_id)` | Returns `ChatSession \| None` |

### 10.2 `ChatMessageRepository`

Handles insert and retrieval of `ChatMessage` rows for a session. Used by the conversation state service to replay history into the LangGraph state.

### 10.3 `ServiceRequestDraftRepository`

| Method | Description |
|--------|-------------|
| `create(session_id, service_category, ...)` | Insert a new draft |
| `update(draft_id, updates)` | Partial update; only the 9 listed fields are allowed |
| `get_by_id(draft_id)` | Returns `ServiceRequestDraft \| None` |
| `get_by_session(session_id)` | Returns the most recent draft for a session, or `None` |
| `list_by_session(session_id)` | Returns all drafts ordered by `created_at` |

### 10.4 `AuditLogRepository`

Append-only. Inserts new `ServiceRequestChatAuditLog` rows. No update or delete methods.

### 10.5 `UserRepository`

CRUD for `User` records. Used by the auth routes (`POST /api/v1/auth/login`, user management).

### 10.6 Error types

**File:** `app/db/exceptions.py`

| Exception | Raised when |
|-----------|-------------|
| `RepositoryError` | Base class for all DB layer errors |
| `RecordNotFoundError` | `get_by_id` or `update` target does not exist |
| `InvalidUpdateFieldError` | `updates` dict contains a key not in the repository's `_UPDATABLE_FIELDS` frozenset |

---

## 11. Provisioning: Shared Instance vs. New Instance

### Recommendation: use a shared Postgres Flexible Server

Azure Database for PostgreSQL Flexible Server supports multiple databases per instance. The chatbot's schema is entirely self-contained — there are no cross-database foreign keys and no shared tables with the Cenomi platform database. You can create a dedicated database on an existing server:

```bash
# Create the database on an existing Flexible Server:
az postgres flexible-server db create \
  --resource-group <existing-resource-group> \
  --server-name <existing-postgres-server> \
  --database-name service_request_chatbot
```

Then set `DATABASE_URL` to point at that new database:

```
postgresql+asyncpg://pgadmin:<password>@<existing-server>.postgres.database.azure.com/service_request_chatbot?ssl=require
```

This is the recommended approach for cost and operational simplicity — you avoid paying for a second Flexible Server instance (~$30–60/mo for Burstable B1ms).

### When to create a new instance

| Scenario | Reason |
|----------|--------|
| Existing server is near connection/IOPS/storage capacity | Avoid contention with platform traffic |
| Compliance/isolation requirement | Hard physical separation of chatbot data |
| Different Azure region | Chatbot must be in `uaenorth` but existing server is elsewhere |
| Existing server runs Postgres < 14 | The migrations use JSONB operators requiring PG 14+ |

### Creating a new instance (if required)

```bash
az postgres flexible-server create \
  --resource-group cenomi-rdd-rg \
  --name cenomi-rdd-postgres \
  --location uaenorth \
  --admin-user pgadmin \
  --admin-password "<strong-password>" \
  --sku-name Standard_B1ms \
  --tier Burstable \
  --storage-size 32 \
  --version 16

az postgres flexible-server db create \
  --resource-group cenomi-rdd-rg \
  --server-name cenomi-rdd-postgres \
  --database-name service_request_chatbot

# Allow Container Apps outbound IPs (or use VNet private endpoint):
az postgres flexible-server firewall-rule create \
  --resource-group cenomi-rdd-rg \
  --name cenomi-rdd-postgres \
  --rule-name AllowAzureServices \
  --start-ip-address 0.0.0.0 \
  --end-ip-address 0.0.0.0
```

---

## 12. Connection String Reference

| Environment | `DATABASE_URL` format |
|-------------|----------------------|
| Local dev | `postgresql+asyncpg://postgres:postgres@localhost:5432/service_request_chatbot` |
| Azure (shared server) | `postgresql+asyncpg://pgadmin:<pw>@<existing-server>.postgres.database.azure.com/service_request_chatbot?ssl=require` |
| Azure (new server) | `postgresql+asyncpg://pgadmin:<pw>@cenomi-rdd-postgres.postgres.database.azure.com/service_request_chatbot?ssl=require` |
| Docker Compose | `postgresql+asyncpg://postgres:postgres@db:5432/service_request_chatbot` |

**SSL is mandatory on Azure.** The `?ssl=require` query parameter is enforced by Azure Flexible Server. Connections without SSL are rejected.

The URL is consumed by:
- `app/core/config.py` → `Settings.database_url`
- `app/db/session.py` → `create_async_engine(settings.database_url, ...)`
- `alembic/env.py` → `config.set_main_option("sqlalchemy.url", settings.database_url)`

---

## 13. Operational Runbook

### Check current migration state

```bash
# Inside the container or locally with DATABASE_URL set:
alembic current
# Expected output on a healthy deployment:
# 005 (head)
```

### Apply missed migrations manually

```bash
alembic upgrade head
```

### Roll back a bad migration

```bash
# Roll back to a specific revision:
alembic downgrade 003

# Or roll back one step:
alembic downgrade -1
```

### Inspect table sizes (Azure Query Editor or psql)

```sql
SELECT
  relname AS table,
  pg_size_pretty(pg_total_relation_size(oid)) AS total_size,
  pg_size_pretty(pg_relation_size(oid)) AS table_size,
  pg_size_pretty(pg_total_relation_size(oid) - pg_relation_size(oid)) AS index_size
FROM pg_class
WHERE relkind = 'r'
  AND relnamespace = (SELECT oid FROM pg_namespace WHERE nspname = 'public')
ORDER BY pg_total_relation_size(oid) DESC;
```

### Purge old observability data

The agent observability tables can grow large over time. Safe to delete old traces:

```sql
-- Delete agent traces older than 90 days (cascades to all child tables):
DELETE FROM agent_traces WHERE created_at < NOW() - INTERVAL '90 days';

-- Equivalent for legacy stubs:
DELETE FROM observability_traces WHERE started_at < NOW() - INTERVAL '90 days';
```

> Run during low-traffic hours. For very large tables (millions of rows), prefer `DELETE ... LIMIT 10000` in a loop to avoid lock contention.

### Connection pool exhaustion

Default `asyncpg` pool size is 5 (SQLAlchemy default). If you see `QueuePool limit of size 5 overflow 10 reached`, increase it:

```python
# In app/db/session.py create_async_engine call:
create_async_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_size=10,        # add this
    max_overflow=20,     # add this
    echo=settings.debug,
)
```

Azure Flexible Server B1ms supports ~50 max connections. With `--workers 2` (2 uvicorn workers × pool_size 10 = 20 connections), you have comfortable headroom.

### Backup

Azure Flexible Server performs automatic daily backups with a configurable retention period (default 7 days). For compliance requirements, extend to 35 days in the Azure Portal under **Settings → Compute + storage → Backup retention**.

Point-in-time restore:

```bash
az postgres flexible-server restore \
  --resource-group cenomi-rdd-rg \
  --name cenomi-rdd-postgres-restored \
  --source-server <source-server-id> \
  --restore-time "2026-07-01T12:00:00Z"
```
