# AI Command Center — Dev Document & Plan (ticket 3784)

**Input:** *Cenomi AI Analytics Backend Readiness & Dashboard Reference (Updated)* — the client document.
**Audited on:** 2026-09-15, backend `feature/agent-analytics-feedbacks-3784` (cut from `dev` @ `580063e`), frontend `feature/agent-analytics-feedbacks-3784` (rv 2.41 @ `d502cb5bc`) and `-dev` (dev 2.38 @ `69e629875`).

The client doc proposes four dashboard sections — **AI Dashboard** (executive), **AI Analytics** (usage & engagement), **AI Performance** (quality & reliability), **AI Productivity** (automation & efficiency) — and rates them READY / STRONG / STRONG / PARTIAL. This document checks each claim against the code that actually exists, lists the gaps, and proposes what to build.

---

## 1. Executive summary

The client doc's overall position is right: most of the Command Center can be built from data already stored. Four things it does not say, which change the plan:

| # | Finding | Impact |
|---|---|---|
| 1 | **Two analytics stacks exist side by side.** `/api/v1/analytics/*` + `/api/v1/dashboard/assistant-admin/*` (star ratings, `answer_status`, 30-day default) and `/api/v1/observability/metrics/*` (thumbs, FLOW_USEFUL, CSAT, session clocks, funnel events, 7-day default). They disagree on the definition of *SR completion*, *success rate*, *session duration* and *feedback*. | The Command Center must pick one definition per KPI and expose it once. |
| 2 | **The production widget (tenant app) never calls the session-analytics endpoints.** No heartbeat, no `/end`, no `FLOW_USEFUL`/`CSAT`, no thumbs. Only 1–5 star ratings via `POST /api/v2/chat/feedback`. | `client_active_ms`, `idle_abandoned`, `usefulness_rate`, `feedback_coverage`, `csat_avg` are **0 / null for every real user**. Those KPIs cannot be shown until the widget is wired. |
| 3 | **`sr_submitted` events never fire.** `TraceManager` emits it only when `tool_type == "SERVICE_REQUEST_CREATE"`, but every submission node passes `tool_type="API"` (`api_submission_node.py:48`, `rdd_api_submission_node.py:47`, `fm_api_submission_node.py:43`). The metrics layer silently falls back to `service_request_drafts.sr_id`. | *SRs created via AI*, *cost per successful SR*, *feedback coverage*, funnel step 5 are computed from a fallback that ignores the `agent`/`user_id` filters. One-line fix + backfill. |
| 4 | **Tool-call capture covers 4 call sites only** (lease lookup, handover create, RDD report/final-approve, FM approve/progress). Work-permit submission, fit-out confirmation, document upload and notifications are not captured. | "Tool Reliability" is a partial view; the doc marks it READY. |

Plus the naming rule the doc insists on — *Answer Rate must not be labelled Response Accuracy* — is currently violated by the live tenant dashboard: `assistantAdmin.responseAccuracy = "Response Accuracy"` (`translation.json:4744`).

---

## 2. What exists today

### 2.1 Storage (Postgres, Alembic head `009`)

| Table | Analytics-relevant columns | Written by |
|---|---|---|
| `agent_traces` | `session_id, user_id, active_agent, intent, service_category, sub_category, workflow_stage_before/after, input_message, output_message, status (SUCCESS/FAILED), answer_status (ANSWERED/PARTIAL/UNANSWERED/FAILED), total_latency_ms, total_token_count, estimated_cost, metadata_, created_at` | `TraceManager.start_trace/finish_trace` per chat turn |
| `agent_runs` | `run_name, run_type, node_name, status, latency_ms` | every graph node |
| `agent_llm_calls` | `provider, model, prompt_name, input/output/total_tokens, latency_ms, estimated_cost (litellm; NULL when model unpriced), parse_success` | supervisor, field extraction, FAQ, response generation |
| `agent_tool_calls` | `tool_name, tool_type, status_code, success, latency_ms, error_message` | 4 call sites (see §1.4) |
| `agent_feedback` | `trace_id (nullable), session_id, user_id, message_id, source (help_answer/sr_created), sr_id, feedback_type, score, label, comment, metadata_` | `USER_RATING` (tenant stars), `TURN_THUMBS`/`FLOW_USEFUL`/`CSAT` (in-repo UI only), `ADMIN_REVIEW`, `AUTOMATED_EVAL` |
| `chat_sessions` | `active_agent, intent, workflow_stage, status, last_activity_at, last_heartbeat_at, ended_at, end_reason (IDLE_TIMEOUT/CLIENT_ENDED), client_active_ms` | orchestration touch; heartbeat/end routes (unused in prod) |
| `analytics_events` | `event_name, properties, user_id, session_id, trace_id, occurred_at` | `session_started`, `field_corrected`, `flow_cancelled` (orchestration); `session_ended` (idle expiry / client end); `sr_lifecycle_completed` (finish_trace); `sr_submitted` (**never — bug**) |
| `service_request_chat_audit_logs` | `action, actor_user_id, session_id` | `turn.started/completed/failed`, `security.injection_attempt`, `service_request.created` (handover only), `sr.fm.approved`, `sr.fm.progress_saved`, `sr.rdd.report_submitted`, `sr.rdd.final_approved`, `feedback.submitted` |

**Dimensions not stored anywhere:** mall, business function/role, channel, user type. `AuthContext` carries `roles`, `mall_names`, `unique_property_ids`, `tenant_id`, `is_global_admin` on every request but none of it reaches `agent_traces` or `chat_sessions`. User names are resolved at read time from the platform DB (`user_directory.py`, uuid5 reverse index).

### 2.2 Read APIs

| Router | Endpoints | Auth | Window | Notes |
|---|---|---|---|---|
| `dashboard.py` → `/api/v1/dashboard/assistant-admin` | `GET /` (24h/7d/30d blocks, summary strip, top questions, top users, recent conversations), `/conversations`, `/conversations/{id}`, `/insights?days=` | `x-internal-api-token` only — **no user-level RBAC on the backend**; the `VIEW_ASSISTANT_ANALYTICS` gate is frontend-only | fixed periods | What the tenant app renders. ~20 SQL queries per load. |
| `analytics.py` → `/api/v{1,2}/analytics` | `/overview`, `/questions`, `/sessions/{id}`, `/feedback`, `/engagement`, `/insights` | `get_auth_context` | `from`/`to`/`agent`/`user_id`, default 30d | Same repository as dashboard; richer (daily series, by_agent, p95). Not consumed by any frontend today. |
| `metrics.py` → `/api/v{1,2}/observability/metrics` | `/summary`, `/users`, `/funnel`, `/feedback` | none | `from`/`to`/`agent`/`user_id`, default 7d | Spec-3713 product KPIs. Consumed only by the in-repo `/admin/agent-success` page. `/users` is N+1 (5 queries per row). |

### 2.3 Frontends

| | Tenant app `cenomi-frontend-2` (production) | In-repo `service-request-chatbot/frontend` (dev/QA) |
|---|---|---|
| Dashboard | `/dashboard/assistant-admin` — Overview tab (6 KPI cards, questions & accuracy trend lines, sessions-by-hour bars, top users, ratings card, top questions, recent conversations) + Agent health tab (slowest node, weakest API, token hog, node latency, tool reliability, token spend, workflow funnel). Conversations list + transcript pages. Hand-rolled SVG charts. | `/admin/agent-observability` (trace explorer + 6 cards) and `/admin/agent-success` (8 scorecard cards, funnel, drop-off, per-user, not-useful reasons, pilot targets). Tables only. |
| Feedback UX | `ChatFeedbackStars` — 1–5 stars + comment under every help answer (`source=help_answer`) and once after an SR is created (`source=sr_created`). | Thumbs (`TURN_THUMBS`) on every assistant bubble; `FlowUsefulnessCard` (Yes/No/Skip + 5 reason chips) after `workflow_complete`; 30 s heartbeat; `pagehide` → `/end`. CSAT type exists but nothing sends it. |
| Filters | 24h / 7d / 30d buttons only. No date range, no agent filter. | Date range on agent-success; no agent filter. |
| rv vs dev cut | Analytics files differ **only by Prettier formatting** (print-width). Dev additionally has `formActionSync` (unrelated). | n/a |

---

## 3. Gap analysis against the client document

Legend — ✅ available now · 🟡 derivable (query missing or definition needs fixing) · 🟠 needs instrumentation · ❌ needs data the backend does not hold.

### 3.1 AI Dashboard — Executive Command Center (doc: READY)

| KPI card (doc) | Status | Source / definition to use | Gap |
|---|---|---|---|
| Active AI Users | 🟡 | `count(distinct agent_traces.user_id)` in window — exists as `turn_totals.unique_users` on `/analytics/overview`, not on the dashboard endpoint | expose |
| Total AI Interactions | ✅ | `turn_totals.questions` (real questions; silent sync turns excluded) | — |
| SRs Created via AI | 🟠 | `analytics_events.sr_submitted` — **never emitted** (§1.3). Fallback: `audit action='service_request.created'` (handover only) or `drafts.sr_id` | fix tool_type; decide whether fit-out / work-permit flows count as "created" (they PATCH existing SRs) |
| AI Answer Rate | ✅ | `answered / classified` real questions (`answer_status`) | **rename** UI label from "Response Accuracy" → "Answer Rate" (EN + AR) |
| Average User Rating | ✅ | `avg(agent_feedback.score) where feedback_type='USER_RATING'` | — |
| SR Completion Rate | 🟡 | Two definitions in code: `sr_funnel.completion_rate` = SRs created ÷ sessions touched by a workflow agent; `metrics/summary.sr_completion_rate` = submitted sessions ÷ create-intent sessions. | pick one (recommend create-intent denominator, event numerator) |
| P95 Response Time | 🟡 | `percentile_cont(0.95)` on `total_latency_ms` — exists on `/analytics/overview` and `/metrics/summary`; dashboard endpoint returns avg only | expose; add p50/p99 |
| LLM Cost Estimate | ✅ with caveat | `sum(agent_llm_calls.estimated_cost)`; `cost_coverage` says what share of calls were priced. Azure deployment names may be unpriced → **show coverage next to cost** | — |
| Tool Reliability | 🟠 | `tool_health` from `agent_tool_calls` — only lease lookup + handover/RDD/FM API calls captured | capture work-permit, fit-out, upload, notification calls |
| Escalation Rate | ✅ with caveat | `(UNANSWERED + FAILED) / classified`. There is **no human-handoff mechanism** in the product; this is "share of questions the agent could not answer". Tooltip must say so. | — |
| Visuals: usage trend, answer trend, SR funnel, agent health table, tool reliability table | ✅ | `daily_series`, `metrics/funnel`, `by_agent`, `tool_health` | — |

### 3.2 AI Analytics — Usage & Engagement (doc: STRONG)

| Metric (doc) | Status | Notes |
|---|---|---|
| Active users, total interactions, usage by agent, usage over time, total conversations, conversation length (turns/session), workflow usage, workflow execution metrics | ✅ | `turn_totals`, `agent_breakdown`, `daily_series`, `engagement`, `workflow_funnel`, `node_latency` |
| New users | 🟡 | first-seen = `min(created_at) per user_id` over all traces; needs one query (users whose first trace falls in the window) |
| Avg interactions per user, repeat-user rate | 🟡 | trivial from `turn_totals` / per-user session counts; not exposed |
| Top intents, intent distribution | 🟡 | `agent_traces.intent` is stored; only `top_questions` (free text) is queried today. Add `group by intent`. |
| Session duration | 🟡 | three clocks exist (wall = last−first message; active = `client_active_ms`; agent = Σ latency). Active is **always 0** in prod (§1.2). Show wall clock; label it. |
| Containment | 🟡 | define as `1 − escalation_rate` (sessions with zero UNANSWERED/FAILED turns) — `recent_sessions.resolved` already computes the per-session form |
| Usage by Mall / Business Function / Channel / User Type | 🟠 | **Doc says defer.** Cheap to start collecting now: stamp `mall_names`, `roles`, `is_global_admin` from `AuthContext` and a `channel` value onto each trace. Dashboards can segment once data accumulates. Recommended in Phase 1 so history exists by the time the client asks. |

### 3.3 AI Performance — Quality & Reliability (doc: STRONG)

| Metric (doc) | Status | Notes |
|---|---|---|
| Answer rate, avg rating, feedback comments, agent success (by agent), LLM parse failures, node latency, workflow failure rate | ✅ | `turn_totals`, `rating_totals`, `feedback_comments`, `agent_breakdown`, `llm_usage`, `node_latency`, `failed_traces` |
| Positive / negative feedback | 🟡 | define on stars: ≥4 positive, ≤2 negative, 3 neutral (`rating_distribution` exists). Thumbs are not collected in prod. |
| Feedback participation | 🟡 | `ratings_count / questions` (help) and `sr_created ratings / SRs created` — one query |
| Tool success/failure | 🟠 | partial capture (§1.4) |
| P50 / P95 / P99 latency | 🟡 | p95 only; add p50/p99 to `latency_percentile` |
| Failure reasons | 🟡 | `agent_traces.error_message` / `agent_runs.error_message` grouped — no query today. Group by a normalised prefix (first 80 chars). |
| Escalation | ✅ | see §3.1 caveat |
| Naming | ❗ | "Response Accuracy" label must become "Answer Rate" |

### 3.4 AI Productivity — Automation & Efficiency (doc: PARTIAL)

| Metric (doc) | Status | Notes |
|---|---|---|
| SRs created via AI | 🟠 | §1.3 |
| Fully automated vs assisted | 🟡 | needs a definition. Proposal: *assisted* = session has ≥1 `field_corrected` event or the SR was finished on the form (`form_action_done` action, dev cut only). *Automated* = SR submitted with none of those. |
| Workflow completion | ✅ | `sr_lifecycle_completed` events / terminal stages |
| Approvals / rejections | 🟠 | Audit has `sr.fm.approved`, `sr.rdd.final_approved`, `sr.rdd.report_submitted`. **No rejection audit anywhere**, and work-permit approve/reject and fit-out confirmations are not audited. Add `sr_approved` / `sr_rejected` analytics events. |
| Tasks completed per day | 🟡 | `daily_series` + per-day count of `sr_submitted` / approvals |
| Productivity by agent | 🟡 | SRs and approvals grouped by `active_agent` |
| LLM token cost, cost per successful SR | ✅ | `total_cost`, `cost_per_successful_sr` (numerator depends on §1.3 fix) |
| Hours Saved, Cost Saved, Annualised Value, Manual Effort Reduction, Processing Time Saved, Human Touchpoint Reduction | ❌ | need baselines (manual minutes per SR type, hourly cost) the backend does not hold. Optional Phase 4: an `analytics_assumptions` config (env/JSON) and cards badged *"estimate — based on configured assumptions"*. Doc says not data-backed; agree, do not ship as fact. |

---

## 4. Proposed design

### 4.1 One contract for the Command Center

Add a single router `app/api/routes/command_center.py` mounted at `/api/v1/dashboard/ai-command-center` (same `{success, data}` envelope and `x-internal-api-token` dependency as `dashboard.py`, so the tenant app's existing `assistantAdminGet` helper works unchanged):

| Endpoint | Section | Payload (summary) |
|---|---|---|
| `GET /overview` | AI Dashboard | 10 KPI cards + `usage_trend[]`, `answer_trend[]`, `sr_funnel`, `agent_health[]`, `tool_reliability[]` |
| `GET /usage` | AI Analytics | users (active/new/repeat), interactions, `by_agent[]`, `by_intent[]`, `daily[]`, engagement (sessions, turns/session, wall duration, containment), `workflow_usage[]`, `hourly[]`; `by_mall[]`, `by_role[]`, `by_channel[]` returned once dimensions exist (empty arrays before) |
| `GET /performance` | AI Performance | answer split, ratings (avg, positive/negative/neutral, participation), escalation, `by_agent[]`, `tool_health[]`, `llm_parse[]`, latency `{p50,p95,p99}`, `node_latency[]`, failure rate, `failure_reasons[]`, `comments[]` (paginated) |
| `GET /productivity` | AI Productivity | SRs created (total, by agent, by day), automated vs assisted, completion, approvals/rejections, tasks/day, tokens + cost, cost per SR, `cost_coverage` |

All four accept `from`, `to`, `agent` (ISO-8601; default trailing 30 days, capped at 365). Every ratio ships with its denominator (`{value, numerator, denominator}`) so the UI can show *n* next to a percentage, following the existing `accuracy_sample_size` / `accuracy_coverage_pct` pattern.

Implementation reuses `AnalyticsRepository` (`analytics_repo.py`) and `analytics_queries.py`; new query methods go into `AnalyticsRepository`. Definitions are written once in `analytics_constants.py` docstrings and mirrored in this document (§5).

Keep `/dashboard/assistant-admin/*` untouched for the current page; retire it after the Command Center replaces it in both FE cuts.

### 4.2 Instrumentation fixes (backend, small, high value)

1. **`sr_submitted` fix** — pass `tool_type=TOOL_TYPE_SR_CREATE` from `api_submission_node.py` (create) and keep `"API"`/`SERVICE_REQUEST_PATCH` for PATCH nodes; add a one-off backfill script that inserts `sr_submitted` from `service_request_chat_audit_logs.action='service_request.created'` for history. Unit test asserting the emit fires.
2. **Dimensions on traces** — migration `010`: `agent_traces.channel TEXT`, `user_roles JSONB`, `mall_names JSONB`, `is_global_admin BOOL` (indexed `channel`). `TraceManager.start_trace(..., dimensions=...)` fed from `AuthContext` in `chat_orchestration_service`. `channel` from a new optional `context.channel` in `ServiceRequestChatRequest` (`copilot_rail` | `copilot_panel` | `msp_help` | `unknown`); MSP help chat stamps `msp_help`.
3. **Tool capture completeness** — `capture_tool_call` in `work_permit_api_submission_node`, `fitout_confirm_api_submission_node`, upload route, notification inbox calls. Use the existing `service_request_api.*` tool-name convention so `display_labels.tool_label` keeps working.
4. **Approval / rejection events** — add `sr_approved`, `sr_rejected` to `ALLOWED_EVENT_NAMES` with `{sr_id, flow, stage, actor_role}`; emit from FM, RDD, work-permit and fit-out submission nodes on success. Also add audit rows for the rejection paths that have none.
5. **Latency percentiles** — `latency_percentile` already parameterised; call for 0.5/0.95/0.99.

### 4.3 Frontend (tenant app — both cuts)

- Replace the 2-tab page with a 4-section Command Center under `/dashboard/assistant-admin/` (`overview`, `usage`, `performance`, `productivity`) — new components in `libs/ui-web/src/lib/components/assistantAdmin/commandCenter/`, reusing `AssistantLineChart`, `AssistantBarChart`, `MeterRow`, stat-card and token styles. Add a horizontal-bar primitive for agent/intent rankings.
- Date range picker (from/to, max 365 d) + agent select, replacing the 24h/7d/30d buttons; presets kept as shortcuts.
- Rename `assistantAdmin.responseAccuracy` → "Answer Rate" (EN/AR); tooltip copy for Escalation ("questions the assistant could not answer") and Cost ("priced calls: n %").
- Wire the widget to the session-analytics contract so Usefulness/CSAT/session clocks stop reading 0: `postSessionHeartbeat` (30 s while visible), `postSessionEnd` on `pagehide`, and a `FLOW_USEFUL` Yes/No/Skip card after `workflow_complete` in `SrFabMessages` (next to the existing `sr_created` stars). Contract already exists on the backend (`session_analytics.py`).
- Keep `VIEW_ASSISTANT_ANALYTICS` as the page gate; add a backend check (JWT role/permission) on the Command Center router so the API is not internal-token-only.
- Every change applied to **both** `feature/agent-analytics-feedbacks-3784` (rv) and `-dev`; the two cuts differ only by Prettier config in these files, so apply the patch semantically and run each tree's own `prettier` + `jest` + `tsc -p libs/ui-web/tsconfig.lib.json`.

### 4.4 Performance

The current dashboard endpoint issues ~20 queries per load and `/metrics/users` is N+1. For four sections with a free date range:
- Phase 1: consolidate per section (one CTE-based query per widget group; per-user table as a single grouped query with LEFT JOINs) and add a 60 s in-process TTL cache keyed on `(section, from, to, agent)`.
- Phase 3 (only if p95 API latency > 2 s on prod data): `agent_metrics_daily` rollup (migration `011`) refreshed by the existing indexer job container — no new cron container, matching the 3713 constraint.

---

## 5. KPI definitions (single source of truth)

| KPI | Formula | Table(s) |
|---|---|---|
| Question | trace whose `lower(trim(input_message))` ∉ {'', 'none'} | `agent_traces` |
| Answer Rate | `ANSWERED ÷ classified` questions (PARTIAL in denominator only) | `agent_traces.answer_status` |
| Escalation Rate | `(UNANSWERED + FAILED) ÷ classified` — "could not answer", not human handoff | same |
| Containment | `1 − escalation` at session level: sessions with no UNANSWERED/FAILED turn ÷ sessions | same |
| Active users | distinct `user_id` with ≥1 question in window | `agent_traces` |
| New users | users whose first-ever trace is in window | `agent_traces` |
| Repeat-user rate | users with ≥2 sessions in window ÷ active users | `agent_traces` |
| Session (wall) duration | `last − first chat_messages.created_at` per session, 30-min idle cutoff | `chat_messages` |
| SRs created via AI | distinct `(session_id, sr_id)` in `sr_submitted` events | `analytics_events` |
| SR completion rate | sessions with `sr_submitted` ÷ sessions with ≥1 trace whose intent ∈ `CREATE_SR_INTENTS` | `analytics_events`, `agent_traces` |
| Lifecycle completion | sessions with `sr_lifecycle_completed` ÷ sessions with `sr_submitted` | `analytics_events` |
| Approvals / rejections | count of `sr_approved` / `sr_rejected` events, by flow | `analytics_events` (new) |
| Automated vs assisted | assisted = submitted session with ≥1 `field_corrected` or `form_action_done`; automated = remainder | `analytics_events` |
| Avg rating / CSAT % | `avg(score)` where `feedback_type='USER_RATING'`; CSAT % = avg ÷ 5 | `agent_feedback` |
| Positive / negative feedback | score ≥4 / ≤2 | `agent_feedback` |
| Feedback participation | rated turns ÷ questions (help); rated SRs ÷ SRs created (SR) | `agent_feedback`, `agent_traces` |
| Usefulness | `USEFUL ÷ (USEFUL + NOT_USEFUL)` FLOW_USEFUL rows — shown only once the widget sends it | `agent_feedback` |
| Latency p50/p95/p99 | `percentile_cont` on `total_latency_ms` | `agent_traces` |
| Tool reliability | `(calls − failed) ÷ calls` per `tool_name` | `agent_tool_calls` |
| LLM cost | `sum(estimated_cost)`; always shown with `cost_coverage = priced ÷ calls` | `agent_llm_calls` |
| Cost per successful SR | Σ `estimated_cost` of traces in submitted sessions ÷ SRs created | `agent_traces`, `analytics_events` |
| Tasks per day | `sr_submitted + sr_approved + sr_rejected` per UTC day | `analytics_events` |

---

## 6. Delivery plan

| Phase | Scope | Repo(s) | Est. |
|---|---|---|---|
| **0 — Fix & agree** | `sr_submitted` tool_type fix + backfill; rename "Response Accuracy" → "Answer Rate" (both FE cuts); sign off KPI definitions (§5) with the client | BE, FE×2 | 1 d |
| **1 — Instrumentation** | migration 010 (dimensions), trace stamping, tool capture on the 4 missing paths, `sr_approved`/`sr_rejected` events, p50/p99; unit tests + `analytics_eval` additions | BE | 2–3 d |
| **2 — Command Center API** | `command_center.py` (4 endpoints), new repository queries (new users, repeat rate, intent distribution, failure reasons, approvals, automated/assisted, participation), consolidated queries + TTL cache, backend RBAC check; integration tests | BE | 4–5 d |
| **3 — Command Center UI** | 4 sections, date range + agent filter, charts, tooltips, EN/AR i18n, jest specs; mirrored to rv + dev | FE×2 | 5–6 d |
| **4 — Widget signals** | heartbeat / end / FLOW_USEFUL in `SrFabMessages` + `useServiceRequestChat`; unlocks Usefulness, CSAT, active time, idle-abandon cards | FE×2 | 1.5 d |
| **5 — Optional** | `agent_metrics_daily` rollup if latency demands; savings estimates behind configured assumptions | BE | 2–3 d |

Phases 1 and 2 can run in parallel with 3 against a mocked contract (extend `assistantAdminMockData.ts`).

**Verification per phase:** backend `pytest tests/unit tests/integration` (+ `python -m tests.eval.analytics_eval`), probe the new endpoints against the local Postgres with seeded multi-day data; frontend `jest` + `tsc` on each cut (baselines ≈115 rv / ≈113 dev errors), then a live check of the page on `:3000` against the local backend on `:8100`. E2E only at push time.

---

## 7. Open questions for the client / product

1. Do fit-out confirmations and work-permit decisions count as "SRs created via AI", or only handover/RDD creates? (Affects the headline card.)
2. Is "Escalation Rate" acceptable as *unanswered share*, or should the copilot gain an explicit "talk to a human" action that we then count?
3. Which user attribute is "Business Function" — JWT `roles`, or something from the platform `users` table?
4. Channel taxonomy — is docked rail vs floating panel a channel, or is MSP help vs tenant copilot the split?
5. Should the Command Center replace the current assistant-admin page or sit beside it during the pilot?
