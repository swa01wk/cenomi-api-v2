# AI Command Center — Full Plan (ticket 3784)

**Reconciles three inputs into one plan:**

| Source | What it gives |
|---|---|
| *Cenomi AI Analytics Backend Readiness & Dashboard Reference (Updated)* — client doc | The four sections, their READY/STRONG/PARTIAL rating, the deferrals (Mall / Business Function / Channel / User Type; Hours & Cost Saved), the naming rule (Answer Rate ≠ Response Accuracy) |
| `AI_ANALYTICS_DATA_AND_KPI_AUDIT.md` — team audit | 82-KPI matrix (22 A · 28 B · 11 C · 21 D), data dictionary, P0–P3 backlog |
| `AI_ANALYTICS_PHASE1_IMPLEMENTATION_PLAN.md` — team plan | Workstreams A–D, 5-week timeline, acceptance criteria |
| Code verification 2026-09-15/16 (this document, §1) | What actually runs on `feature/agent-analytics-feedbacks-3784` — backend from `dev` @ `580063e`, frontend rv 2.41 @ `d502cb5bc` and dev 2.38 @ `69e629875` |

Companion: [`ai-command-center-3784-dev-plan.md`](./ai-command-center-3784-dev-plan.md) holds the line-by-line gap tables per client section. This document is the plan of record.

---

## 1. Corrections to the audit and Phase 1 plan (from code)

The audit is accurate on schema and queries. Six points change after reading the write paths and the production frontend:

| # | Audit / plan says | Code says | Effect on plan |
|---|---|---|---|
| C-1 | P0-1 "verify `session_started` emission — not confirmed" | **Emitted.** `chat_orchestration_service.py:329` on every new session. | Close P0-1 as verified; keep the regression test (D-2). |
| C-2 | P0-2 "`field_corrected` not emitted; add to `FieldCorrectionService.apply_correction()`" | **Emitted.** `chat_orchestration_service.py:336` whenever the request body `corrected_fields` is non-empty (keys only). The tenant widget does send `corrected_fields` on the turn payload. | Close P0-2 as verified. Correction Rate is real, but counts *request-level* corrections, not per-field service calls — document that. |
| C-3 | `sr_submitted` "READY — clear, unambiguous" | **Never emitted.** `trace_manager.py:362` fires only when `tool_type == "SERVICE_REQUEST_CREATE"`; every submission node passes `tool_type="API"` (`api_submission_node.py:48`, `rdd_api_submission_node.py:47`, `fm_api_submission_node.py:43`). `_count_sr_submitted` silently falls back to `service_request_drafts.sr_id`, which ignores `agent`/`user_id` filters. | **New P0-0.** One-line fix + backfill from `audit action='service_request.created'`. Blocks SRs Created, SR Submission Rate, Cost per SR, Feedback Coverage, funnel step 5, Tasks/day, Productivity by agent, Automation mix. |
| C-4 | Flow Usefulness, CSAT, thumbs, session clocks listed as **A — implemented** | Implemented server-side, **but the production widget (`cenomi-frontend-2`) never calls** heartbeat, `/end`, `POST /chat/sessions/{id}/feedback` or `TURN_THUMBS`. Only `POST /api/v2/chat/feedback` (stars). So `usefulness_rate`, `csat_avg`, `client_active_ms`, `idle_abandoned`, `feedback_coverage` are 0/null for every real user. | **New task C-8** (widget signals). Until it ships, hide those cards or badge them "no data yet". |
| C-5 | Tool Reliability "READY" | `capture_tool_call` exists at **four** call sites only (lease lookup, handover create, RDD report/final-approve, FM approve/progress). Work-permit submission, fit-out confirmation, upload, notifications go uncaptured. | **New task A-7.** Tool reliability is partial until then — label the card "platform APIs (handover flows)". |
| C-6 | Mall / User Type "D — not on traces; only login-gated users have roles" | `AuthContext` carries `mall_names`, `unique_property_ids`, `roles`, `is_global_admin`, `tenant_id` from the JWT **for every platform-auth user** on every request (`security.py:59-64`). Nothing persists them. | P1-1 / P1-2 drop from *Medium* to *Low*: stamp from `AuthContext` at `start_trace`. Recommend pulling the **write side** into Phase 1 (migration 010) so history exists by the time Phase 2 builds the filters. |

Two smaller ones:

- **Approval time is derivable today** for work permits: `agent_traces.created_at` of the `WP_CREATED → …` transition vs the `→ WP_APPROVED | WP_REJECTED` transition (`work_permit_schema.py:70-72`). The audit marks "Average Approval Time" as D; it is B for WP. Handover FM/RDD approvals are in `service_request_chat_audit_logs` (`sr.fm.approved`, `sr.rdd.final_approved`) with timestamps. **No rejection is audited anywhere.**
- **The dashboard the client sees is the tenant app**, gated by `VIEW_ASSISTANT_ANALYTICS` (frontend-only). Its API `/api/v1/dashboard/assistant-admin/*` is protected by `x-internal-api-token` alone — the Phase 1 plan's "BFF in the frontend calling `/analytics/*`" would hit `get_auth_context` (JWT) instead. Decide the auth model once (§4.1).

---

## 2. Scope

### 2.1 Phase 1 — data-backed Command Center (this ticket)

1. **Trust fixes (P0):** `sr_submitted` emission + backfill (C-3); classification coverage shown beside Answer Rate (P0-3); regression tests for `session_started` / `field_corrected` (already emitted — lock them in).
2. **Write-side instrumentation that is cheap now and expensive later:** dimensions on traces (mall, role, channel — C-6), tool capture on the four missing paths (C-5), `sr_approved` / `sr_rejected` events. Read-side segmentation stays Phase 2, per the client doc.
3. **One API contract for the four sections** with `from` / `to` / `agent`, every ratio carrying its denominator.
4. **Derivable KPIs** (SQL only): new users, repeat-user rate, feedback participation, positive/negative %, containment, automation mix, WP approval/rejection, failure reasons, P50/P99, tasks per day, productivity by agent.
5. **Four UI sections** in the tenant app, **on both frontend cuts**, replacing the current two-tab page; date range + agent filter; glossary tooltips; "Response Accuracy" → "Answer Rate".
6. **Widget signals** (heartbeat, end, FLOW_USEFUL) so the session-time and usefulness cards stop reading zero.
7. Reconciliation script, runbook, sign-off checklist.

### 2.2 Phase 2 — segmentation and HITL analytics

Usage by Mall / User Type / Channel (read side of the Phase 1 stamps), Business Function mapping from `service_category`, approval wait-time from the new events, agent/prompt version tracking, `agent_metrics_daily` rollup if latency demands.

### 2.3 Not in scope (do not show as data)

Hours Saved, Cost Saved, Annualised Value, Manual Effort / Processing Time / Touchpoint Reduction (no baselines); Response Accuracy / Task Accuracy (no ground truth); Guardrail failure, timeout, retry rates, A2A, agent availability (no telemetry). Phase 3 candidates: eval harness (P3-1), assumptions config for *estimated* savings with disclaimer (P3-2), `policy_violation` event from the injection guard (P3-4, low effort).

---

## 3. KPI definitions — single source of truth

Every card, chart and tooltip uses these names and formulas. "Question" = trace whose `lower(trim(input_message))` ∉ {'', 'none'}. All windows are `[from, to)` on `created_at` / `occurred_at` UTC.

| # | KPI (display name) | Formula | Tables | Class |
|---|---|---|---|---|
| K1 | Active AI Users | distinct `user_id` with ≥1 question | agent_traces | A |
| K2 | New AI Users | users whose first-ever trace falls in window | agent_traces | B |
| K3 | Repeat-User Rate | users with ≥2 sessions in window ÷ K1 | agent_traces | B |
| K4 | AI Interactions | count of questions | agent_traces | A |
| K5 | Sessions | distinct `session_id` | agent_traces | A |
| K6 | Turns per Session | K4 ÷ K5 (real questions only) | agent_traces | A |
| K7 | Session Duration | last − first `chat_messages.created_at` per session, 30-min idle cutoff. *Active time* (`client_active_ms`) shown only after C-8 ships | chat_messages, chat_sessions | A |
| K8 | AI Answer Rate | `ANSWERED ÷ classified` questions (PARTIAL in denominator only). Always shown with **Classification Coverage** = classified ÷ questions | agent_traces.answer_status | A |
| K9 | Escalation Rate | `(UNANSWERED + FAILED) ÷ classified`. Tooltip: "questions the assistant could not answer — there is no hand-off to a person" | agent_traces | A |
| K10 | Containment Rate | sessions with zero UNANSWERED/FAILED questions ÷ K5 | agent_traces | B |
| K11 | Average User Rating | `avg(score)` where `feedback_type='USER_RATING'`; CSAT % = avg ÷ 5 | agent_feedback | A |
| K12 | Positive / Negative Feedback % | score ≥4 / ≤2 ÷ rated turns | agent_feedback | A |
| K13 | Feedback Participation | sessions with ≥1 USER_RATING ÷ K5 | agent_feedback, agent_traces | B |
| K14 | Flow Usefulness | `USEFUL ÷ (USEFUL + NOT_USEFUL)` — hidden until C-8 | agent_feedback | A* |
| K15 | Latency P50 / P95 / P99 | `percentile_cont` on `total_latency_ms` | agent_traces | A/B |
| K16 | Tool Reliability | `(calls − failed) ÷ calls` per `tool_name`; overall = weighted | agent_tool_calls | A (partial until A-7) |
| K17 | LLM Parse Failure Rate | `parse_failures ÷ calls` per prompt | agent_llm_calls | A |
| K18 | LLM Cost | `sum(estimated_cost)`; always with **Cost Coverage** = priced ÷ calls | agent_llm_calls | A |
| K19 | SRs Created via AI | distinct `(session_id, sr_id)` in `sr_submitted` events | analytics_events | A after P0-0 |
| K20 | SR Submission Rate | sessions with `sr_submitted` ÷ sessions with ≥1 trace whose intent ∈ `CREATE_SR_INTENTS` | analytics_events, agent_traces | A after P0-0 |
| K21 | SR Completion Rate | sessions with `sr_lifecycle_completed` ÷ sessions with `sr_submitted` | analytics_events | A |
| K22 | Cost per SR | Σ `estimated_cost` of traces in submitted sessions ÷ K19 | agent_traces, analytics_events | A after P0-0 |
| K23 | Automation Mix | per submitted session: **automated** = no UNANSWERED/FAILED turn and no `field_corrected`; **assisted** = otherwise; **failed** = create-intent session with no `sr_submitted` | agent_traces, analytics_events | B |
| K24 | Human Intervention Rate | assisted ÷ (automated + assisted) | as K23 | B |
| K25 | Correction Rate | sessions with ≥1 `field_corrected` ÷ sessions with `sr_submitted` | analytics_events | A (verified) |
| K26 | Approval / Rejection Rate | Phase 1: WP from `workflow_stage_after` transitions to `WP_APPROVED` / `WP_REJECTED`; handover FM/RDD from audit `sr.fm.approved`, `sr.rdd.final_approved`. Phase 2: `sr_approved` / `sr_rejected` events for all flows | agent_traces, audit_logs → analytics_events | B → A |
| K27 | Approval Time (WP) | `created_at(→WP_APPROVED\|WP_REJECTED)` − `created_at(→WP_CREATED)` per session | agent_traces | B |
| K28 | Tasks per Day | K19 + approvals + rejections per UTC day | analytics_events | B |
| K29 | Productivity by Agent | K19, K21, K22 grouped by `active_agent` | as above | B |
| K30 | Failure Reasons | `error_message` on FAILED traces + failed tool calls, grouped by normalised prefix (first 80 chars) | agent_traces, agent_tool_calls | B |
| K31 | Top Intents | `group by intent` on questions | agent_traces | A |
| K32 | Workflow Funnel | `sessions_started → create_intent → draft_started → preview_or_confirm → sr_submitted → lifecycle_completed` (existing `compute_funnel`) | mixed | A |

Naming rules (from the client doc, enforced in i18n): **"AI Answer Rate"** never "Response Accuracy"; **"SR Submission Rate"** (K20) and **"SR Completion Rate"** (K21) are different numbers and both get shown; **"Escalation Rate"** keeps its tooltip.

---

## 4. Target architecture

### 4.1 One contract, one auth model

New router `app/api/routes/command_center.py`, mounted at `/api/v1/dashboard/ai-command-center` beside `dashboard.py`:

| Endpoint | Section | Payload |
|---|---|---|
| `GET /overview` | AI Dashboard | K1 K4 K19 K8(+coverage) K11 K21 K15 K18(+coverage) K16 K9 as `{value, numerator, denominator, delta_vs_previous}`; `usage_trend[]`, `answer_trend[]` (with coverage), `hourly[]`, `sr_funnel`, `agent_health[]`, `tool_reliability[]`, `recent_conversations[]`, `top_questions[]` |
| `GET /usage` | AI Analytics | K1 K2 K3 K4 K5 K6 K7 K10; `daily[]`, `by_agent[]`, `by_intent[]`, `workflow_funnel[]`, `automation_mix_daily[]`, `top_questions[]`, `top_users[]`; `by_mall[]`, `by_role[]`, `by_channel[]` present but empty until Phase 2 |
| `GET /performance` | AI Performance | K8 K11 K12 K13 K9 K16 K17 K15 K14; `answer_status_mix`, `answer_trend[]`, `rating_distribution`, `rating_trend[]`, `node_latency[]`, `tool_health[]`, `failure_reasons[]`, `agent_scorecard[]`, `comments` (paginated, `max_score`) |
| `GET /productivity` | AI Productivity | K19 K20 K21 K23 K24 K25 K26 K27 K22 K28; `sr_trend[]`, `automation_mix[]`, `by_agent[]`, `funnel`, `not_useful_reasons[]`, `sr_log` (paginated) |

Common query: `from`, `to` (ISO-8601, default trailing 30 d, max 365 d), `agent`; `/performance` adds `max_score`, `page`; `/productivity` adds `service_category`, `page`.

Auth: `x-internal-api-token` (as today) **plus** a JWT permission check for `VIEW_ASSISTANT_ANALYTICS` when a Bearer token is present — the tenant app already mints an AI session token for chat, reuse it for the dashboard. Envelope `{success, data}` / `{success, errors}` so `assistantAdminGet` works unchanged.

Implementation: reuse `AnalyticsRepository` (analytics_repo.py) and `analytics_queries.py`; add new methods to the repository; each section endpoint runs its queries under one `asyncio.gather` and a 60-s in-process TTL cache keyed on `(section, from, to, agent)`. `/dashboard/assistant-admin/*` and `/analytics/*` stay as they are until the new page replaces the old one in both cuts; then `/dashboard/assistant-admin` is removed.

### 4.2 Write-side changes (append-only, fail-safe like `TraceManager`)

| Change | Where | Migration |
|---|---|---|
| `sr_submitted` fires on create | `api_submission_node.py` → `tool_type=TOOL_TYPE_SR_CREATE` | — |
| Dimensions on traces | `agent_traces.channel TEXT`, `user_roles JSONB`, `mall_names JSONB`, `property_ids JSONB`, `is_global_admin BOOL`; `TraceManager.start_trace(dimensions=…)` from `AuthContext`; `channel` from new optional `context.channel` on the chat request (`copilot_rail` \| `copilot_panel` \| `msp_help`) | `010_trace_dimensions.py` |
| Tool capture | work-permit + fit-out submission nodes, upload route, notification inbox calls | — |
| `sr_approved` / `sr_rejected` events | `ALLOWED_EVENT_NAMES` + emit from FM, RDD, WP, fit-out submission nodes with `{sr_id, flow, stage, actor_role}`; audit rows for the rejection paths that have none | — |
| Backfill | script: `sr_submitted` from `audit action='service_request.created'`; `sr_approved` from `sr.fm.approved` / `sr.rdd.final_approved` | one-off |

---

## 5. Workstreams and tasks

IDs keep the Phase 1 plan's numbering; new items are marked ★. Every backend task ships with unit tests; every endpoint with an integration test; every FE task with jest + `tsc` on both cuts.

### Workstream A — backend trust and API completeness

| ID | Task | Acceptance | Files |
|---|---|---|---|
| ★ A-0 | `sr_submitted` fires on SERVICE_REQUEST_CREATE; backfill history | Unit test: successful create tool call → one `sr_submitted` per (session, sr_id); backfill idempotent; `_count_sr_submitted` no longer takes the drafts fallback on seeded data | `api_submission_node.py`, `scripts/backfill_sr_events.py`, `tests/unit/test_analytics_events.py` |
| A-1 | Lock `session_started` emission with a regression test | Integration: first turn of a new session → event row | `tests/integration/test_session_analytics_endpoints.py` |
| A-2 | Lock `field_corrected` emission with a regression test; document "request-level" semantics | Turn with `corrected_fields={title:…}` → event with `keys=['title']` | same |
| A-3 | Classification coverage on every Answer Rate payload | `answer_rate = {value, numerator, denominator, coverage}` | `command_center.py` |
| A-4 | Feedback participation (K13) | matches hand SQL on fixture | `analytics_repo.py` |
| A-5 | P50 / P99 (K15) | `latency = {p50, p95, p99}` | `analytics_repo.py`, `command_center.py` |
| A-6 | Section endpoints + TTL cache + RBAC | 4 endpoints, envelope, filters, 401/403 paths, p95 < 2 s on 30-d seeded data | `command_center.py`, `main.py` |
| ★ A-7 | Tool capture on WP / fit-out / upload / notifications | `tool_health` lists ≥ 8 tool names on a full e2e run | 4 nodes/routes |
| ★ A-8 | `sr_approved` / `sr_rejected` events + rejection audit rows | events on approve and reject in all four flows; constants + eval assertions | nodes, `analytics_constants.py`, `analytics_eval.py` |
| ★ A-9 | Migration 010 + dimension stamping | columns exist; a turn with a JWT carrying `mall_names` writes them; anonymous shadow-mode writes NULL | `010_trace_dimensions.py`, `trace_manager.py`, `chat_orchestration_service.py`, `chat.py` |

### Workstream B — derivable metrics (SQL only, in `AnalyticsRepository`)

| ID | KPI | Acceptance |
|---|---|---|
| B-1 | New users (K2) | user with first trace inside window counted once; earlier user excluded |
| B-2 | Repeat-user rate (K3) | fixture: 3 users / 1 repeat → 0.333 |
| B-3 | Automation mix + intervention (K23, K24) | rules in §3 reproduced on fixture sessions |
| B-4 | WP approval / rejection + approval time (K26, K27) | stage-transition fixture matches |
| B-5 | Failure reasons (K30) | top-N grouped, prefix-normalised |
| B-6 | Positive / negative % (K12) | from `rating_distribution` |
| ★ B-7 | Containment (K10), intents (K31), tasks/day (K28), productivity by agent (K29) | each one query, fixture-tested |
| ★ B-8 | Consolidated per-user query (replace N+1 in `compute_users`) | one grouped statement; same output |

### Workstream C — AI Command Center UI (tenant app, both cuts)

| ID | Task | Acceptance |
|---|---|---|
| C-1 | Shell under `/dashboard/assistant-admin/`: 4 section tabs, global date range (from/to, presets 24h/7d/30d, max 365 d) + agent select; filters in the URL | filters persist across sections and reload |
| C-2 | AI Dashboard: 10 KPI cards with delta, 3 charts, 2 tables | matches `/overview` |
| C-3 | AI Analytics: KPIs, agent/intent bars, funnel, automation mix, top questions/users, sessions list → transcript | matches `/usage` |
| C-4 | AI Performance: scorecards, answer-status donut, rating distribution, latency P50/95/99, node & tool bars, failure reasons, comment feed (≤2 stars filter) | matches `/performance` |
| C-5 | AI Productivity: SR trend, automation mix, approvals, cost cards, per-agent table, SR log | matches `/productivity` |
| C-6 | Glossary tooltips: Answer Rate vs accuracy; Submission vs Completion; Escalation; Cost coverage; Tool reliability scope | copy reviewed against §3 |
| C-7 | Empty / low-coverage / no-data states ("no usefulness data yet" until C-8; coverage < 50 % warning) | no card shows 0 % as if measured |
| ★ C-8 | Widget signals: `postSessionHeartbeat` (30 s while visible), `postSessionEnd` on `pagehide`, FLOW_USEFUL Yes/No/Skip card after `workflow_complete` in `SrFabMessages`, `context.channel` on the turn payload | rows appear in `chat_sessions.client_active_ms` and `agent_feedback FLOW_USEFUL` from a real session |
| ★ C-9 | i18n: rename `assistantAdmin.responseAccuracy` → "Answer Rate" (EN/AR); new keys for the four sections | key sets identical on both cuts |
| ★ C-10 | Mirror to `-dev` cut: apply patches semantically, run each tree's Prettier; jest + `tsc` baselines hold (~115 rv / ~113 dev) | both branches pushed with the same change set |

Charts stay hand-rolled SVG (`AssistantLineChart`, `AssistantBarChart`) plus a new horizontal-bar and donut primitive — no chart library.

### Workstream D — QA, reconciliation, sign-off

| ID | Task | Acceptance |
|---|---|---|
| D-1 | Reconciliation script: each K-number from the API vs raw SQL, 7-d and 30-d windows, on staging | zero deltas |
| D-2 | Regression tests for all six events + the two new ones | `analytics_eval` prints all PASS |
| D-3 | Product sign-off checklist against the client doc's KPI lists; deferred metrics absent | signed |
| D-4 | Runbook: card → endpoint → query → table; known limitations (partial tool capture until A-7, coverage) | in `docs/` |
| ★ D-5 | Performance check: each section p95 < 2 s on 30-d staging data; else trigger P2-5 rollup | numbers recorded |

---

## 6. Timeline (one full-stack squad; BE and FE can overlap from week 2)

| Week | Backend | Frontend | Milestone |
|---|---|---|---|
| 1 | A-0, A-1, A-2, A-3, A-5, A-9 (migration + stamping) | C-9 rename; C-1 shell against mocked contract | P0 trust fixes merged; dimensions accumulating |
| 2 | A-4, B-1…B-8, A-7, A-8 | C-2, C-3 | `/overview` + `/usage` live |
| 3 | A-6 (all four endpoints, cache, RBAC) | C-4, C-5 | all sections render on staging |
| 4 | D-1, D-5, backfill run on staging | C-6, C-7, C-8 | widget signals in prod-like env; reconciliation zero-delta |
| 5 | D-2, D-4 | C-10 mirror + both cuts pushed | D-3 sign-off |

Phase 2 (indicative, after sign-off): read-side Mall / Role / Channel filters (1 wk), approval wait-time from events (0.5 wk), `agent_metrics_daily` only if D-5 fails (1 wk), version tracking (0.5 wk).

---

## 7. Verification

- **Backend loop:** `pytest tests/unit tests/integration` + `python -m tests.eval.analytics_eval`; probes against local Postgres 18 (`:6004`) seeded multi-user/multi-day; live uvicorn `--reload` on `:8100` for endpoint checks.
- **Frontend loop:** `jest` + `tsc -p libs/ui-web/tsconfig.lib.json` on the rv clone and the dev worktree; page check on `:3000` against `:8100` with the `x-internal-api-token`. E2E only at push time.
- **Data honesty checks before sign-off:** `sr_submitted` count equals audit `service_request.created` count for the same window; `client_active_ms > 0` for a session driven from the tenant widget; `FLOW_USEFUL` rows carry `session_id` and `sr_id`; `tool_health` shows work-permit and fit-out tool names.

---

## 8. Risks

| Risk | Mitigation |
|---|---|
| Pre-007 traces skew Answer Rate | coverage shown on every Answer Rate; optional "since 2026-08-31" toggle |
| `/analytics/*` and `/dashboard/*` numbers diverge from the new endpoints during the overlap | D-1 reconciliation; old page removed once C-10 lands on both cuts |
| Widget signals (C-8) slip → usefulness / session-time cards read zero | C-7 no-data state ships first; cards hidden, not zeroed |
| Azure deployment names unpriced in litellm → cost understated | cost coverage beside every cost figure; pricing map for the deployment names in `cost_calculator._PROVIDER_MAP` follow-up |
| Two FE cuts drift (Prettier-only today) | C-10 applies one patch script to both trees; no dev-only commits on rv |
| Dashboard latency with free date ranges | TTL cache + consolidated queries first; D-5 gates the rollup |

---

## 9. Open questions (need answers before week 2)

1. Do fit-out confirmations and work-permit decisions count as "SRs Created via AI" (they PATCH existing SRs), or only handover creates? Affects K19 and the headline card.
2. Escalation Rate as *unanswered share* is acceptable? Or add a "talk to a person" action to the copilot and count that instead?
3. "Business Function" = JWT `roles`, or a field on the platform `users` table? (Determines whether Phase 2 needs a lookup table.)
4. Channel taxonomy: rail vs panel, or tenant copilot vs MSP help?
5. Replace the current assistant-admin page outright, or keep both during the pilot?
6. Auth for the dashboard API: internal token only (as today) or require the AI session JWT with `VIEW_ASSISTANT_ANALYTICS`?
