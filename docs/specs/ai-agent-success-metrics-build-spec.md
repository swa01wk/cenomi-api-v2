# Fable / Claude Build Spec — AI Agent Success Metrics

**Download this file and give it to Claude + Fable as the single source of truth.**

| Field | Value |
|-------|-------|
| **Ticket** | **3713** — AI agent success metrics (token usage, session time, usefulness, KPIs) |
| **Repo** | `cenomi-api-v2` |
| **Implement in** | `service-request-chatbot/backend` **and** `service-request-chatbot/frontend` |
| **Base branch** | `dev` at `4f2dc582a724d5f6509f1f5b253f69ebe7f89fe7` (cut `feature/3713-ai-agent-success-metrics`) |
| **Do not touch** | Cenomi platform API repo, `cenomi-frontend-2`, indexer job, graph routing/workflow logic |
| **Alembic** | Head on `dev` is `006` (`006_indexer_watermarks.py`). This branch ships **`007_agent_analytics.py`** |

---

## 0. Prompt to paste into Fable / Claude

```
Implement the AI agent success-metrics pipeline in cenomi-api-v2 exactly as
specified in docs/specs/ai-agent-success-metrics-build-spec.md.

Repo: cenomi-api-v2
Base branch: dest
Cut branch: feature/3713-ai-agent-success-metrics

This repo already has engineering observability (agent_traces, agent_llm_calls
with tokens/cost, agent_tool_calls, admin-only feedback on traces, a global
metrics summary). It does NOT measure product success: per-user tokens, real
session time, end-user usefulness of the SR flow, funnel drop-off, or a
scorecard that says whether the AI agent implementation worked.

Hard rules:
- Do all work in this repo. Do NOT change the Cenomi platform API or
  cenomi-frontend-2. Expose a stable HTTP contract the platform widget can
  call later.
- Do NOT duplicate token counts into a new table at request time. Tokens and
  cost already live on agent_llm_calls / agent_traces.user_id. Aggregate.
- Do NOT dump full traces/prompts into App Insights. Postgres is the product
  store. structlog stays as-is.
- Do NOT treat admin star ratings on /admin/agent-observability as user CSAT.
- Do NOT make feedback mandatory. Skip is allowed. One FLOW_USEFUL rating per
  session (upsert).
- Do NOT end a chat_session just because an SR was submitted — the user may
  continue (FAQ, another SR). Session end = idle timeout or client-ended.
- Do NOT change handover / fit-out / work-permit / notification routing.
  Analytics hooks are append-only (emit event, capture LLM tokens).
- Follow existing conventions: async SQLAlchemy + asyncpg, structlog events
  with dots (analytics.session_heartbeat), pydantic-settings on
  app.core.config.Settings, repositories with explicit flush, fail-safe
  TraceManager style (analytics must never crash a chat turn).
- Alembic head today is 006. Next migration MUST be 007.
- Unit tests with no network. Do not "fix" unrelated failing handover tests.

Deliver: Alembic 007, session lifecycle columns, analytics_events,
extended agent_feedback, heartbeat + session feedback APIs, filterable
metrics APIs (summary/users/funnel/feedback), capture_llm_call on faq_node
and response_generation_node, chat UI thumbs + usefulness card + heartbeat,
admin AI Success dashboard, tests, and this spec remaining the contract.
```

---

## 1. Why this exists

Leadership needs to know whether the SR chatbot is **successful as a product**, not only whether the LangGraph run finished.

Today the admin UI at `/admin/agent-observability` shows graph health: total traces, graph success rate, avg latency, total tokens, estimated cost, failed traces. That answers “did the backend run?” It does **not** answer:

1. What did **each user** cost in tokens / USD?
2. How long was the user actually in the session (not `total_latency_ms`)?
3. Was creating / completing the SR **useful with AI**?
4. Where do users drop off, and is the agent cheaper and faster than the old form?

**North-star event:** a user completes an SR through the chatbot and rates the AI flow as useful.

---

## 2. Current-repo facts (verify before coding)

Verified on `dev` @ `4f2dc58` (2026-08-31).

| Fact | Location |
|------|----------|
| Python 3.11, FastAPI, async SQLAlchemy, `structlog`, Alembic | `service-request-chatbot/backend` |
| Chat turn | `POST /api/chat/service-request` (also mounted at `/api/v2`) — `app/api/routes/chat.py` |
| Orchestration starts a trace per turn and returns `trace_id` | `app/services/chat_orchestration_service.py` |
| Per-turn tokens + cost already rolled onto the trace | `TraceManager.capture_llm_call` + `finish_trace`; `cost_calculator.py` via litellm |
| `agent_traces.user_id` + `session_id` already populated | `app/db/models.py` `AgentTrace` |
| LLM capture **is** called from supervisor + field extraction | `supervisor_node.py`, `field_extraction_node.py` |
| LLM capture **is NOT** called from FAQ or response generation | `faq_node.py` discards `_in, _out, _ms` from `complete_json`; `response_generation_node.py` has no `capture_llm_call` |
| Tool types for SR create/patch | `ToolCallRepository.VALID_TOOL_TYPES` includes `SERVICE_REQUEST_CREATE`, `SERVICE_REQUEST_PATCH` |
| `chat_sessions` columns | `id, user_id, active_agent, intent, workflow_stage, status, created_at, updated_at` — **no** last_activity / ended_at / client_active_ms |
| Session repo updatable fields | `{"active_agent", "intent", "workflow_stage", "status"}` only — `chat_session_repo.py` |
| Session statuses used in graph | `IN_PROGRESS`, `WAITING_FOR_USER`, `READY_TO_SUBMIT`, `SUBMITTED`, `COMPLETED`, `FAILED` |
| Feedback table | `agent_feedback`: `trace_id` **NOT NULL**, `user_id` nullable, no `session_id` / `sr_id` / `metadata` |
| Feedback API | `POST /api/observability/traces/{trace_id}/feedback` — does **not** persist JWT `user_id`; admin QA UI only |
| Metrics API | `GET /api/v1/observability/metrics/summary` — **no** `from`/`to`/`user_id`/`agent` filters; all-time global totals. File uses `Any` without importing it — fix when editing |
| Routers | traces/feedback/sessions at `/api/observability/*`; metrics at `/api/v1/observability/metrics/summary` (`app/main.py`) |
| Auth | `get_auth_context` — shadow mode allows anonymous; `subject_id` from JWT `user_id` / `sub`. Do not invent a new auth model |
| Workflow-complete flag for UI | `response_ui.workflow_complete === true` (handover Path B, RDD final approve, fit-out submit) |
| Create-SR intents (funnel denominator) | `CREATE_RDD_SERVICE_REQUEST`, `CREATE_HANDED_OVER_SR`, `CREATE_FITOUT_CONFIRM_FOS`, `CREATE_FITOUT_CONFIRM_END`, `CREATE_FITOUT_CONFIRM_TRADING`, plus `CREATE_WORK_PERMIT_SR` if still routed |
| Alembic head | `006` revises `005` |
| Frontend chat | `frontend/components/chatbot/ServiceRequestChat.tsx` — stores `traceId` on messages, no thumbs, no CSAT, no heartbeat |
| Admin UI | `frontend/app/admin/agent-observability/page.tsx` + `MetricsCards.tsx` |
| Production widget | `cenomi-frontend-2` `SrCopilotPanel` — **out of this repo**. Ship HTTP contract only |

**Do not** write to the legacy `observability_*` tables (migration `001` stubs).

---

## 3. Non-goals

- App Insights / OpenTelemetry exporters for traces
- Prometheus (the in-process `MetricsRegistry` stub stays unused)
- Budget caps, rate limits, or blocking users by token spend
- Mandatory feedback
- Changing SR create/patch payloads or platform APIs
- Materialized `analytics_user_daily` in v1 (live SQL is enough; optional Phase 5)
- Authz rewrite for observability routes (keep current shadow/enforce behaviour)
- Implementing thumbs/CSAT inside `cenomi-frontend-2` (document the contract; they wire later)

---

## 4. Architecture

Postgres remains the source of truth. Product analytics **extends** the existing observability layer.

```
Chat UI (this repo)                    Graph (unchanged routing)
  thumbs / CSAT / heartbeat              TraceManager (existing)
  │                                      agent_traces, agent_llm_calls
  │                                      agent_tool_calls
  ▼                                      chat_sessions, drafts
POST /api/chat/sessions/{id}/heartbeat
POST /api/chat/sessions/{id}/feedback
POST /api/observability/traces/{id}/feedback   (now stamps user_id)
  │
  ▼
chat_sessions        (lifecycle timestamps + client_active_ms)
agent_feedback       (TURN_THUMBS, FLOW_USEFUL, CSAT, ADMIN_REVIEW, …)
analytics_events     (append-only product log — not graph internals)
  │
  ▼  live SQL (no nightly job in v1)
GET /api/v1/observability/metrics/summary
GET /api/v1/observability/metrics/users
GET /api/v1/observability/metrics/funnel
GET /api/v1/observability/metrics/feedback
  │
  ▼
Admin /admin/agent-success  +  existing /admin/agent-observability
```

**Fail-safe rule:** every analytics write is wrapped like `TraceManager` — log and swallow. A heartbeat or CSAT failure must never 500 a chat turn. Dedicated analytics routes may 4xx on bad input.

---

## 5. Schema — Alembic `007_agent_analytics.py`

`revision = "007"`, `down_revision = "006"`.

### 5.1 Extend `chat_sessions`

| Column | Type | Notes |
|--------|------|--------|
| `last_activity_at` | `TIMESTAMPTZ NOT NULL` | Default `now()`. Update on every chat turn and every heartbeat |
| `last_heartbeat_at` | `TIMESTAMPTZ NULL` | Last heartbeat that incremented `client_active_ms` (min-interval gate) |
| `ended_at` | `TIMESTAMPTZ NULL` | Set when session closes |
| `end_reason` | `TEXT NULL` | `IDLE_TIMEOUT` \| `CLIENT_ENDED` |
| `client_active_ms` | `INTEGER NOT NULL` | Default `0`. Sum of focused intervals from heartbeats |

Backfill: `last_activity_at = updated_at` (or `created_at` if null). `client_active_ms = 0`. Leave `ended_at` / `end_reason` null.

Index: `ix_chat_sessions_last_activity_at` on `(last_activity_at)` — used by idle expiry.

**Three clocks (all required, all documented on the metrics APIs):**

| Clock | Formula | Question it answers |
|-------|---------|---------------------|
| Wall session | `coalesce(ended_at, last_activity_at) - created_at` | How long was the conversation open? |
| Active time | `client_active_ms` | How long was the tab focused? |
| Agent time | `sum(agent_traces.total_latency_ms)` for the session | How long did the graph/LLM work? |

`total_latency_ms` on a trace is **not** session time. Do not rename it or reuse it as session duration.

**Do not** set `ended_at` when `workflow_complete` is true. The user may keep chatting.

### 5.2 Extend `agent_feedback`

| Change | Detail |
|--------|--------|
| `trace_id` | Make **nullable**. Session-level FLOW_USEFUL / CSAT has no single turn |
| `session_id` | `UUID NULL`, indexed |
| `sr_id` | `TEXT NULL` |
| `metadata` | `JSONB NOT NULL` default `'{}'` — reason codes, client locale, etc. |
| Check | `trace_id IS NOT NULL OR session_id IS NOT NULL` |

Keep existing `feedback_type`, `score`, `label`, `comment`, `user_id`, `run_id`.

Canonical `feedback_type` values (store these strings, not the admin panel’s `"rating"`):

| Type | Who | Shape |
|------|-----|--------|
| `TURN_THUMBS` | End user | `label` = `THUMBS_UP` \| `THUMBS_DOWN`; `trace_id` required |
| `FLOW_USEFUL` | End user | `label` = `USEFUL` \| `NOT_USEFUL`; `session_id` required; optional `sr_id`; `metadata.reason` when not useful |
| `CSAT` | End user | `score` 1–5; `session_id` required |
| `ADMIN_REVIEW` | Admin UI | keep current behaviour |
| `AUTOMATED_EVAL` | Eval harness | keep current behaviour |

`FLOW_USEFUL` reason enum when `NOT_USEFUL` (store in `metadata.reason`):

`WRONG_LEASE` \| `WRONG_FIELDS` \| `TOO_MANY_STEPS` \| `CONFUSING` \| `OTHER`

**Upsert rule:** at most one `FLOW_USEFUL` row per `session_id`. Repeat POST updates that row. Thumbs are append-only (one per trace per user is enough; ignore duplicates).

### 5.3 New table `analytics_events`

Append-only product log. No message text. No payloads. Redaction is “don’t put secrets in `properties`” — keep properties to enums, ids, counts, durations.

```
analytics_events
  id              UUID PK
  occurred_at     TIMESTAMPTZ NOT NULL DEFAULT now()
  user_id         UUID NOT NULL
  session_id      UUID NOT NULL
  trace_id        UUID NULL
  event_name      TEXT NOT NULL
  properties      JSONB NOT NULL DEFAULT '{}'
```

Indexes:

- `ix_analytics_events_session_id`
- `ix_analytics_events_user_occurred` on `(user_id, occurred_at)`
- `ix_analytics_events_name_occurred` on `(event_name, occurred_at)`

**Allowed `event_name` values** (reject others at the repository):

| event_name | When | properties (minimum) |
|------------|------|----------------------|
| `session_started` | `ChatSessionRepository.create` | `{}` |
| `session_ended` | idle expiry or client end | `{ "reason": "IDLE_TIMEOUT" \| "CLIENT_ENDED" }` |
| `sr_submitted` | First successful `SERVICE_REQUEST_CREATE` tool call for the draft | `{ "sr_id", "sub_category", "active_agent" }` |
| `sr_lifecycle_completed` | Stage becomes `SR_COMPLETED` or `HANDED_OVER_COMPLETED` (or fit-out terminal with `workflow_complete`) | `{ "sr_id", "workflow_stage" }` |
| `field_corrected` | Request body `corrected_fields` is non-empty | `{ "keys": ["title", ...] }` — **keys only**, not values |
| `flow_cancelled` | `action` is `cancel` | `{ "workflow_stage" }` |

Do **not** insert a row per heartbeat. Heartbeats only `UPDATE chat_sessions`.

ORM: add `AnalyticsEvent` in `app/db/models.py`. Relationship from `ChatSession` is optional; prefer no cascade from session delete onto events if you want history — **but** this repo currently `CASCADE DELETE`s drafts/messages with the session. Match that: `session_id` has **no FK** (same loose coupling as `agent_traces.session_id`) so deleting a session does not wipe the product log. Document that.

### 5.4 Settings (no secrets)

Add to `app/core/config.py` / `.env.example`:

| Env | Default | Meaning |
|-----|---------|---------|
| `ANALYTICS_IDLE_TIMEOUT_SECONDS` | `600` | Mark `IN_PROGRESS` sessions idle after this gap |
| `ANALYTICS_HEARTBEAT_MIN_INTERVAL_SECONDS` | `15` | Ignore heartbeats faster than this (abuse / tight loops) |
| `ANALYTICS_MAX_ACTIVE_DELTA_MS` | `120000` | Clamp a single heartbeat `client_active_delta_ms` |

---

## 6. Session time behaviour

### 6.1 Touch on every chat turn

In `ChatOrchestrationService` after a session is loaded or created:

- set `last_activity_at = now()`
- if this is a new session, emit `session_started`

Extend `ChatSessionRepository._UPDATABLE_FIELDS` with `last_activity_at`, `ended_at`, `end_reason`, `client_active_ms`. Update `test_chat_session_repo.py` accordingly.

### 6.2 Heartbeat

`POST /api/chat/sessions/{session_id}/heartbeat`

Auth: `get_auth_context`. Resolve `user_id` the same way `post_service_request_chat` does (JWT `subject_id` wins over body). **404** if session missing. **403** if `session.user_id` does not match the caller (when a real user id is present; skip the check for anonymous shadow-mode if that is how chat currently works — stay consistent with chat ownership).

Body:

```json
{ "client_active_delta_ms": 30000 }
```

Server:

1. No-op (200) if session already has `ended_at`.
2. If `now - last_heartbeat` < min interval, still 200 but do not add delta (avoid double-count). Store last heartbeat time in `chat_sessions` **or** in `metadata` — simplest: compare `now - last_activity_at` and skip add when below min interval, but **do** still refresh `last_activity_at` so idle expiry does not fire during a long focused read. Prefer a dedicated `last_heartbeat_at` column if the skip-add vs refresh-activity conflict is messy. **If you add `last_heartbeat_at`, put it in the same 007 migration.** Recommended: add `last_heartbeat_at TIMESTAMPTZ NULL`.
3. `client_active_ms += clamp(delta, 0, ANALYTICS_MAX_ACTIVE_DELTA_MS)`
4. `last_activity_at = now()`, `last_heartbeat_at = now()`
5. Return `{ "client_active_ms", "last_activity_at", "ended_at" }`

### 6.3 Client-ended

`POST /api/chat/sessions/{session_id}/end`

Body: `{ "reason": "CLIENT_ENDED" }` (only allowed value from clients).

Sets `ended_at`, `end_reason`, emits `session_ended` once (idempotent if already ended).

Frontend: call on “new chat”, `pagehide`, and `visibilitychange` → hidden after the idle UX. `keepalive` fetch is fine.

### 6.4 Idle expiry (no worker)

Implement `ChatSessionRepository.expire_idle(now, timeout_seconds) -> int` as a single `UPDATE … WHERE ended_at IS NULL AND last_activity_at < now() - timeout`. Set `end_reason = 'IDLE_TIMEOUT'`. Then insert one `session_ended` event per expired id (or skip events for idle if that is too heavy — **prefer emitting the event** so funnel/abandon queries stay on `analytics_events`).

Call `expire_idle` from:

- heartbeat handler (cheap)
- metrics summary / users / funnel (so dashboards show current abandon counts)

Do not add a cron Container App.

---

## 7. Token usage (per user)

No new write path for tokens.

**Phase 0 (required in this ticket):** call `TraceManager.capture_llm_call` from:

1. `faq_node` — `complete_json` already returns `(parsed, input_tokens, output_tokens, latency_ms)`. Stop discarding them. `prompt_name="faq_prompt"`.
2. `response_generation_node` — whatever gateway method it uses; capture the same fields as field extraction. `prompt_name="response_generation_prompt"`.

Follow the field-extraction pattern: `start_run` type `LLM` if a run id is needed; never raise into the node.

Embeddings used for FAQ search: if the search/embed path exposes token counts, capture them as an LLM call with `prompt_name="faq_embedding"` (model = `EMBEDDING_MODEL`). If it does not, log `analytics.embedding_tokens_unavailable` once per process at debug and skip — do not block the ticket on indexer internals.

Per-user rollup is a **read query** (see §9.2), not a trigger.

---

## 8. Feedback APIs

### 8.1 Turn thumbs (extend existing)

`POST /api/observability/traces/{trace_id}/feedback`

Keep the current body. **Changes:**

- Resolve `user_id` from `get_auth_context` / the same helper chat uses (`user_id_to_uuid`). Persist it.
- Accept `feedback_type` of `TURN_THUMBS` (and still accept `ADMIN_REVIEW` / `rating` from the admin panel — map `rating` → `ADMIN_REVIEW` if that is what the admin UI sends today).
- Chat UI sends:

```json
{ "feedback_type": "TURN_THUMBS", "label": "THUMBS_UP" }
```

Duplicate thumbs from the same user on the same trace: 200 with the existing row (idempotent), do not insert a second.

### 8.2 Session usefulness / CSAT (new)

`POST /api/chat/sessions/{session_id}/feedback`

```json
{
  "feedback_type": "FLOW_USEFUL",
  "label": "USEFUL",
  "comment": null,
  "sr_id": "11711",
  "metadata": { "reason": null }
}
```

or

```json
{
  "feedback_type": "FLOW_USEFUL",
  "label": "NOT_USEFUL",
  "comment": "It kept asking for the lease code",
  "metadata": { "reason": "TOO_MANY_STEPS" }
}
```

or `{ "feedback_type": "CSAT", "score": 4 }`.

Validation:

- `FLOW_USEFUL.label` in `USEFUL` \| `NOT_USEFUL`
- `NOT_USEFUL` may omit reason; if present it must be in the enum
- `CSAT.score` 1–5
- session must exist and be owned by the caller
- `trace_id` is null for these rows

Copy (English; Arabic if `language=ar` on the last turn — optional v1, English is enough in this repo’s chatbot):

> Was creating this service request with the assistant useful?

Buttons: **Yes** / **No** / **Skip**. If No, show the reason chips. Never block the SR card.

Show the card when **either**:

- `ui.workflow_complete === true`, or
- `state.action_result === "success"` after a create/submit turn

Do not show it on every FAQ turn.

---

## 9. Metrics APIs

Mount on the existing metrics router (`prefix="/observability"`, included at `/api/v1` **and** `/api/v2` like today). All accept:

```
from_ts    optional ISO-8601  (query: from)
to_ts      optional ISO-8601  (query: to)
agent      optional active_agent
user_id    optional UUID
```

Default window if both omitted: last 7 days (`created_at` / `occurred_at` / `last_activity_at` as appropriate). **Always** filter — the current unfiltered all-time summary is the bug you are replacing. Keep the JSON keys the existing `MetricsCards` already reads (`total_traces`, `success_rate`, `failed_traces`, `avg_latency_ms`, `total_tokens`, `total_cost`) and **add** the new keys below so the old cards do not break.

### 9.1 `GET /observability/metrics/summary`

Existing keys plus:

```json
{
  "window": { "from": "...", "to": "..." },
  "sessions": 120,
  "ended_sessions": 80,
  "idle_abandoned": 25,
  "avg_wall_session_ms": 180000,
  "avg_client_active_ms": 95000,
  "avg_agent_time_ms": 12000,
  "sr_submitted": 40,
  "sr_completion_rate": 0.55,
  "usefulness_rate": 0.72,
  "feedback_coverage": 0.38,
  "cost_per_successful_sr": 0.0412,
  "p95_latency_ms": 7200
}
```

Definitions (implement exactly):

| Field | SQL meaning |
|-------|-------------|
| `sessions` | `chat_sessions` created in window |
| `sr_submitted` | distinct sessions with `analytics_events.event_name = 'sr_submitted'` in window **or** `service_request_drafts.sr_id IS NOT NULL` if the event was missed — prefer events, fall back to drafts |
| `sr_completion_rate` | `sr_submitted / create_intent_sessions` where create-intent = sessions whose traces include any intent in `CREATE_SR_INTENTS` (define a frozenset in `app/observability/analytics_constants.py`) |
| `usefulness_rate` | `FLOW_USEFUL AND label=USEFUL` / all `FLOW_USEFUL` in window (0 if none) |
| `feedback_coverage` | sessions with a `FLOW_USEFUL` row / `sr_submitted` (0 if none submitted) |
| `cost_per_successful_sr` | `sum(estimated_cost)` of traces in submitted sessions / `sr_submitted` |
| `p95_latency_ms` | percentile of `agent_traces.total_latency_ms` in window (`percentile_cont(0.95)`) |
| `idle_abandoned` | sessions with `end_reason = 'IDLE_TIMEOUT'` in window |

Graph `success_rate` stays `SUCCESS traces / all traces` — do not redefine it as SR completion.

### 9.2 `GET /observability/metrics/users`

Paginated. Sort by `total_cost` desc default.

```json
{
  "items": [
    {
      "user_id": "...",
      "sessions": 3,
      "turns": 18,
      "prompt_tokens": 12000,
      "completion_tokens": 3000,
      "total_tokens": 15000,
      "total_cost": 0.21,
      "sr_submitted": 1,
      "avg_client_active_ms": 80000,
      "thumbs_up": 4,
      "thumbs_down": 1,
      "useful": 1,
      "not_useful": 0
    }
  ],
  "total": 50,
  "page": 1,
  "page_size": 25
}
```

Tokens/cost: `SUM` from `agent_llm_calls` joined to `agent_traces` on `user_id` and `created_at` in window.

### 9.3 `GET /observability/metrics/funnel`

```json
{
  "steps": [
    { "key": "sessions_started", "count": 100 },
    { "key": "create_intent", "count": 70 },
    { "key": "draft_started", "count": 62 },
    { "key": "preview_or_confirm", "count": 50 },
    { "key": "sr_submitted", "count": 40 },
    { "key": "lifecycle_completed", "count": 18 }
  ],
  "drop_off_by_stage": [
    { "workflow_stage": "CREATE_SR", "last_sessions": 12 },
    { "workflow_stage": "FM_REVIEW", "last_sessions": 8 }
  ]
}
```

Step rules:

| key | Rule |
|-----|------|
| `sessions_started` | sessions in window |
| `create_intent` | session has ≥1 trace with intent in `CREATE_SR_INTENTS` |
| `draft_started` | session has a `service_request_drafts` row with non-empty `collected_data` |
| `preview_or_confirm` | any trace with `workflow_stage_after` in `{CREATE_SR, READY_TO_SUBMIT}` **or** a confirmation/preview UI is too implicit — use `workflow_stage_after IN ('CREATE_SR')` AND draft `ready_to_submit = true`, **or** traces where stage moved to a review stage. Pragmatic v1: count sessions whose max stage (see order below) ≥ `CONFIRM` |
| `sr_submitted` | `sr_submitted` event or `drafts.sr_id IS NOT NULL` |
| `lifecycle_completed` | `sr_lifecycle_completed` event or `workflow_stage` in `{SR_COMPLETED, HANDED_OVER_COMPLETED}` |

Stage order for drop-off (last `chat_sessions.workflow_stage` of sessions that did **not** submit):

`CREATE_SR` → `FM_REVIEW` → `RDD_REVIEW` → `SR_CREATED` → `SR_COMPLETED` / `HANDED_OVER_COMPLETED`

Include unknown stages in `drop_off_by_stage` as their raw string.

### 9.4 `GET /observability/metrics/feedback`

```json
{
  "thumbs_up": 40,
  "thumbs_down": 8,
  "thumbs_up_rate": 0.83,
  "useful": 22,
  "not_useful": 6,
  "usefulness_rate": 0.79,
  "csat_avg": 4.1,
  "csat_n": 10,
  "not_useful_reasons": [
    { "reason": "TOO_MANY_STEPS", "count": 3 },
    { "reason": "WRONG_FIELDS", "count": 2 }
  ]
}
```

---

## 10. Emit hooks (backend)

Keep these tiny. No workflow changes.

| Hook | File | Event |
|------|------|--------|
| New session | `chat_session_repo.create` or orchestration after create | `session_started` |
| `corrected_fields` present | `chat_orchestration_service` before graph invoke | `field_corrected` (keys only) |
| `action == "cancel"` | same | `flow_cancelled` |
| Tool success `SERVICE_REQUEST_CREATE` | `TraceManager.capture_tool_call` after insert, or api_submission nodes | `sr_submitted` — **dedupe** per session+sr_id |
| `workflow_complete` or terminal stage on `finish_trace` | `trace_manager.finish_trace` reading `final_state` | `sr_lifecycle_completed` — dedupe per session+sr_id |
| Idle / client end | §6 | `session_ended` |

Create `app/observability/analytics_repo.py` (or `app/observability/repositories/analytics_event_repo.py`) with `emit(event_name, **fields)` that never raises.

Create `app/observability/analytics_constants.py` with the frozensets (intents, stages, event names, feedback types, reasons). Import those from APIs and tests — do not scatter string literals.

---

## 11. Frontend (this repo only)

### 11.1 Chat — `ServiceRequestChat.tsx` / `MessageBubble.tsx`

1. **Thumbs** on assistant `MessageBubble` when `traceId` is set. Two icon buttons. POST turn feedback. Hide after success. Do not show on user bubbles or the loading placeholder.
2. **Usefulness card** after the SR preview/success when `workflow_complete` or `action_result === "success"`. New component `frontend/components/chatbot/FlowUsefulnessCard.tsx`. Local state: don’t re-prompt if already submitted or skipped this session (`sessionStorage` key `sr-usefulness:{sessionId}`).
3. **Heartbeat:** `setInterval` 30s while `document.visibilityState === 'visible'` and `sessionId` is set. Send `client_active_delta_ms` = elapsed since last tick (or since last visible). Pause when hidden. On `pagehide`, `POST .../end` with `keepalive: true` **only if** you are tearing down (not on every hide — that would mark idle users ended). **Rule:** `pagehide` → end; `visibility=hidden` → stop heartbeats but do **not** end (user may switch tabs for 30s). Idle timeout on the server handles abandoned tabs.

### 11.2 Admin — AI Success page

New route: `frontend/app/admin/agent-success/page.tsx`.

Sections (no new chart library required; tables + the existing card style in `MetricsCards`):

1. Scorecard cards: SR completion rate, usefulness rate, feedback coverage, cost per successful SR, p95 latency, idle abandoned, total tokens, total cost (window default 7d, date pickers).
2. Funnel table (step, count, conversion from previous).
3. Drop-off by stage.
4. Per-user table (from `/metrics/users`).
5. Feedback reasons table.

Link it from the observability page header (“AI Success”). Do not remove the existing trace explorer.

Extend `frontend/lib/api/observability-client.ts` and `frontend/lib/types/observability.ts`.

---

## 12. Success scorecard (product contract, not code)

Surface these targets on the admin page as **reference copy** (constants in the UI, not enforced):

| KPI | Pilot target |
|-----|----------------|
| SR completion rate (create-intent sessions) | ≥ 60% |
| Usefulness (`USEFUL` / all `FLOW_USEFUL`) | ≥ 70% |
| Feedback coverage on submitted SRs | ≥ 30% |
| Graph + tool error rate | < 2% |
| p95 turn latency | < 8000 ms |

**Pass:** completion and usefulness both ≥ target, errors < 2%.  
**Investigate:** completion high, usefulness low.  
**Fail:** high abandon at confirm/create, low usefulness, unbounded cost per SR.

Segment mentally by `active_agent` (handover vs fit-out vs FAQ vs notifications) — the funnel API already filters on `agent`.

---

## 13. Files to add / change

### New

| File | Purpose |
|------|---------|
| `backend/alembic/versions/007_agent_analytics.py` | Schema |
| `backend/app/observability/analytics_constants.py` | Enums / frozensets |
| `backend/app/observability/analytics_emit.py` | Fail-safe wrapper around the event repo |
| `backend/app/observability/analytics_queries.py` | Live SQL for summary / users / funnel / feedback |
| `backend/app/observability/llm_capture.py` | `capture_complete_json` for FAQ + response-gen |
| `backend/app/observability/repositories/analytics_event_repo.py` | Fail-safe emit |
| `backend/app/api/routes/session_analytics.py` **or** methods on `chat.py` | heartbeat, end, session feedback — prefer `app/api/routes/session_analytics.py` mounted next to chat |
| `backend/app/observability/api/metrics.py` | extend + new routes (same module is OK if it stays readable; split `metrics_users.py` if >400 lines) |
| `frontend/components/chatbot/FlowUsefulnessCard.tsx` | CSAT / useful |
| `frontend/app/admin/agent-success/page.tsx` | Dashboard |
| `backend/tests/unit/test_analytics_events.py` | emit + constants + fail-safe |
| `backend/tests/unit/test_session_heartbeat.py` | clamp, idle, ownership, session clocks |
| `backend/tests/unit/test_metrics_analytics.py` | `ratio`, `parse_window`, funnel keys |
| `backend/tests/unit/test_llm_capture.py` | FAQ / response-gen token capture |
| `backend/tests/integration/test_session_analytics_endpoints.py` | heartbeat / end / session feedback / metrics HTTP |
| `backend/tests/eval/analytics_eval.py` | **offline contract eval** (required; no network) |
| `backend/tests/eval/run_analytics_eval.py` | optional live HTTP eval against a running backend |
| `frontend` tests only if existing chatbot e2e pattern makes a cheap mock test easy — do not add a flaky Playwright suite as a requirement |

### Modify

| File | Change |
|------|--------|
| `app/db/models.py` | columns + `AnalyticsEvent` |
| `app/db/repositories/chat_session_repo.py` | updatable fields, `touch`, `expire_idle`, `add_active_ms` |
| `app/observability/repositories/feedback_repo.py` | nullable trace, session_id, upsert FLOW_USEFUL |
| `app/observability/api/feedback.py` | persist `user_id`; thumbs idempotency |
| `app/observability/schemas/api_models.py` | FeedbackResponse extras |
| `app/observability/trace_manager.py` | optional emit `sr_submitted` / lifecycle from tool/finish — **or** emit from orchestration reading tool repo; pick one place and document it in a comment |
| `app/services/chat_orchestration_service.py` | touch session; emit field_corrected / flow_cancelled; session_started |
| `app/agents/graph/nodes/faq/faq_node.py` | `capture_llm_call` |
| `app/agents/graph/nodes/shared/response_generation_node.py` | `capture_llm_call` |
| `app/core/config.py` + `.env.example` | idle / heartbeat settings |
| `app/main.py` | include session analytics router |
| `frontend/components/chatbot/MessageBubble.tsx` | thumbs |
| `frontend/components/chatbot/ServiceRequestChat.tsx` | heartbeat, usefulness card, end on pagehide |
| `frontend/lib/api/chat-client.ts` | heartbeat / end / session feedback helpers |
| `frontend/lib/api/observability-client.ts` | new metrics GETs |
| `frontend/app/layout.tsx` | nav link to AI Success |
| `frontend/app/admin/agent-observability/page.tsx` | link to AI Success |
| `frontend/components/observability/MetricsCards.tsx` | optional extra cards; must not break if new keys missing |

---

## 14. Tests (required)

Unit, no network/DB unless the file is already an integration test.

- Session repo: can update `last_activity_at` / `client_active_ms`; rejects unknown fields still.
- Heartbeat clamp: delta 999999 → max; negative → 0. Min-interval skips the increment but still refreshes `last_activity_at`.
- Idle expiry: stale `last_activity_at` gets `ended_at` + `session_ended` (`IDLE_TIMEOUT`); recent does not.
- Feedback upsert: two FLOW_USEFUL POSTs → one row, latest label.
- Thumbs idempotency: two TURN_THUMBS for the same trace+user → one row (label may update).
- Funnel helper: create-intent without `sr_submitted` does not count as submitted (`ratio(0, n) == 0`).
- `faq_node` / `response_generation_node`: `capture_complete_json` → `capture_llm_call` invoked with token ints (mock TraceManager).
- Metrics summary: window default last 7 days UTC; `from`/`to`/`agent`/`user_id` accepted. Dedicated analytics routes may 4xx; chat turns never 500 because of analytics.

Do not “fix” unrelated handover eval failures.

### 14.1 Eval tests (ticket 3713 — required)

Offline contract eval, same shape as the indexer eval (`tests/eval/indexer_eval.py`): a PASS/FAIL table, no database, no LLM, no network.

```
cd service-request-chatbot/backend
PYTHONPATH=$(pwd) python -m tests.eval.analytics_eval
# also collected by pytest as test_analytics_eval_contract
```

**Must assert:**

| Check | Why |
|-------|-----|
| Exact product event names (`session_started`, `session_ended`, `sr_submitted`, `sr_lifecycle_completed`, `field_corrected`, `flow_cancelled`) | Admin KPIs join on these strings |
| End reasons only `IDLE_TIMEOUT` / `CLIENT_ENDED` | Clients must not send idle |
| Feedback types `TURN_THUMBS`, `FLOW_USEFUL`, `CSAT`, `ADMIN_REVIEW`, `AUTOMATED_EVAL` (+ legacy `"rating"` mapped on write) | Do not mix admin stars with user CSAT |
| Thumbs labels `THUMBS_UP`/`THUMBS_DOWN`; useful labels `USEFUL`/`NOT_USEFUL` | UI + SQL filters |
| Not-useful reasons `WRONG_LEASE`, `WRONG_FIELDS`, `TOO_MANY_STEPS`, `CONFUSING`, `OTHER` | Reason table on AI Success |
| `CREATE_SR_INTENTS` includes handover, RDD, three fit-out confirms, work permit | Funnel denominator |
| Terminal stages `{SR_COMPLETED, HANDED_OVER_COMPLETED}` | Lifecycle completed fallback |
| `clamp_active_delta_ms(-1, max)=0`, overflow → max | Heartbeat cannot inflate session time |
| `ratio(n, 0) == 0`; create-intent without submit → 0% completion | North-star math |
| `parse_window(None, None)` is ~7 days, tz-aware | Default admin window |
| Alembic file `007` revises `006`; adds `last_activity_at`, `client_active_ms`, `analytics_events`, nullable `trace_id`, `ck_agent_feedback_target` | Schema contract |
| `faq_node.py` and `response_generation_node.py` call `capture_complete_json` | Token honesty |
| Orchestration emits session/field/cancel events; TraceManager emits `sr_submitted` / `sr_lifecycle_completed` | Funnel numerators |
| Frontend: `FlowUsefulnessCard.tsx`, `/admin/agent-success`, heartbeat + `pagehide` end, `TURN_THUMBS` on `MessageBubble` | Product UI |

Optional live runner (skips if the server is down — does **not** fail CI):

```
PYTHONPATH=$(pwd) python -m tests.eval.run_analytics_eval --base-url http://localhost:8000
```

Live checks: heartbeat 404 for unknown session, client `IDLE_TIMEOUT` → 422, metrics summary/users/funnel/feedback 200, funnel has the six step keys. Do **not** add this as a new `run_eval.py` tier; handover scenarios stay unchanged.

### 14.2 pytest command (this ticket)

From `service-request-chatbot/backend`:

```
pytest tests/unit/test_analytics_events.py \
       tests/unit/test_session_heartbeat.py \
       tests/unit/test_metrics_analytics.py \
       tests/unit/test_llm_capture.py \
       tests/unit/test_chat_session_repo.py \
       tests/unit/test_agent_feedback_repo.py \
       tests/integration/test_observability_endpoints.py \
       tests/integration/test_session_analytics_endpoints.py \
       tests/eval/analytics_eval.py
```


---

## 15. Implementation order (do not skip)

1. Alembic 007 + ORM + session repo + constants + analytics emit (fail-safe).
2. `capture_llm_call` on faq + response generation (token honesty).
3. Orchestration hooks: touch, session_started, field_corrected, flow_cancelled.
4. `sr_submitted` / `sr_lifecycle_completed` hooks.
5. Heartbeat + end HTTP + idle expiry.
6. Feedback: user_id on trace feedback; session feedback upsert.
7. Metrics GETs with window filters; fix summary `Any` import.
8. Frontend chat: thumbs, usefulness, heartbeat, pagehide end.
9. Admin AI Success page.
10. Tests + offline eval (`python -m tests.eval.analytics_eval`).

---

## 16. Out of scope / follow-ups (do not build)

- `analytics_user_daily` materialized rollup
- App Insights custom metrics
- Cost alerts / quotas
- Platform-widget (`cenomi-frontend-2`) thumbs/CSAT — they will call the same three chat analytics endpoints
- `% of platform SRs created via chatbot` (needs a platform-side count or a tag on create payload)
- Changing LiteLLM / cost_calculator

---

## 17. Done when

- `alembic upgrade head` applies `007` on an empty and on a dest-shaped DB
- A chat turn from a real user increments `agent_llm_calls` for FAQ and response-gen as well as supervisor/extraction
- Heartbeat increases `client_active_ms`; after idle timeout the session has `end_reason=IDLE_TIMEOUT`
- Submitting thumbs and FLOW_USEFUL writes `agent_feedback` with `user_id`
- `/api/v1/observability/metrics/users` returns per-user tokens/cost
- `/api/v1/observability/metrics/funnel` returns the six steps
- Admin `/admin/agent-success` renders the scorecard from those APIs
- Chat UI never blocks SR submit on feedback
- Graph tests that do not touch analytics still pass
- `python -m tests.eval.analytics_eval` prints all PASS

This document is the contract. If implementation and this spec diverge, update the spec in the same PR.

---

## 18. Shipped on `feature/3713-ai-agent-success-metrics`

Section 2 is the **pre-implementation** snapshot of `dev` @ `4f2dc58`. After this branch:

| Item | Where |
|------|--------|
| Alembic `007` | `backend/alembic/versions/007_agent_analytics.py` |
| Session clocks | `chat_sessions.last_activity_at`, `last_heartbeat_at`, `ended_at`, `end_reason`, `client_active_ms` |
| Product events | `analytics_events` via `AnalyticsEventRepository` + fail-safe `emit_analytics` |
| Session HTTP | `POST /api/chat/sessions/{id}/heartbeat\|end\|feedback` (`session_analytics.py`) |
| Metrics HTTP | `GET /api/v1/observability/metrics/{summary,users,funnel,feedback}` with `from`/`to`/`agent`/`user_id` |
| Token honesty | `capture_complete_json` in `faq_node` and `response_generation_node` |
| Funnel events | orchestration: `session_started` / `field_corrected` / `flow_cancelled`; TraceManager: `sr_submitted` / `sr_lifecycle_completed` (deduped) |
| Chat UI | thumbs on assistant bubbles; `FlowUsefulnessCard` after `workflow_complete` or `action_result=success`; 30s heartbeat while visible; `pagehide` → end; hidden tab does **not** end |
| Admin | `/admin/agent-success` scorecard + funnel + drop-off + per-user + reasons; nav link from Observability |
| Idle close | `expire_idle` on heartbeat and on metrics reads (`ANALYTICS_IDLE_TIMEOUT_SECONDS`, default 600) |
| Eval | `tests/eval/analytics_eval.py` (offline, required); `tests/eval/run_analytics_eval.py` (live, skip if down) |

Settings (`.env.example`):

- `ANALYTICS_IDLE_TIMEOUT_SECONDS=600`
- `ANALYTICS_HEARTBEAT_MIN_INTERVAL_SECONDS=15`
- `ANALYTICS_MAX_ACTIVE_DELTA_MS=120000`

