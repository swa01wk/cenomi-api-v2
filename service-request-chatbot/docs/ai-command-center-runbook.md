# AI Command Center — Runbook (ticket 3784)

Operating guide for the AI Command Center: how to deploy it, where every number
comes from, how to check the numbers, and what they cannot tell you.

- Plan of record and KPI definitions (K-numbers): [`ai-command-center-full-plan.md`](./ai-command-center-full-plan.md)
- API contract with response shapes: [`ai-command-center-api.md`](./ai-command-center-api.md)

## 1. Deploy

Order does not matter — each piece degrades safely without the others — but the
dashboard is only complete once all are out.

| Step | Where | Command / action | Safe without the others? |
|---|---|---|---|
| 1 | AI backend DB | `alembic upgrade head` → migrations **010** (trace dimensions) and **011** (one thumbs vote per user per reply) | Yes |
| 2 | Platform DB (`cenomi-api`) | migration **1789482400** (`service_request.created_via`) via the migration runner | Yes — the AI backend's `created_via` field is ignored until then |
| 3 | AI backend | deploy `feature/agent-analytics-feedbacks-3784` | Yes |
| 4 | Platform API | deploy `feature/agent-analytics-feedbacks-3784` (rv) / `-dev` | Needs step 2 first — the SR fetch reads `created_via` |
| 5 | Tenant frontend | deploy `feature/agent-analytics-feedbacks-3784` (rv) / `-dev` | Yes — the page shows "—" for anything the backend lacks |
| 6 | AI backend | `.venv/bin/python scripts/backfill_analytics_events.py` (dry run), then `--apply` | Once per environment; idempotent |
| 7 | AI backend | `.venv/bin/python scripts/backfill_platform_created_via.py` (dry run), then `--apply` — marks SRs the assistant already created as `AI_ASSISTANT` on the platform | After step 2; once per environment; idempotent |
| 8 | AI backend | `.venv/bin/python scripts/reconcile_command_center.py --base-url <api> --days 30` | Exit code = mismatches |

Access: the page is gated on `VIEW_ASSISTANT_ANALYTICS` in the tenant app, and
the API checks the same permission. It requires `x-internal-api-token`, and a
caller whose token carries a role without the permission (platform
`roles.permission_list`) gets 403. Callers without a token pass in shadow mode
and get 401 when `RBAC_ENFORCE` is on. The page sends the chat widget's AI
session token. The platform roles are read from `CENOMI_DATABASE_URL` and cached
for five minutes.

## 2. Where each number comes from

All sections: `GET /api/v1/dashboard/ai-command-center/<section>?from&to&agent`.
Queries live in `backend/app/observability/repositories/command_center_repo.py`
(`CommandCenterRepository`) and its base `analytics_repo.py` (`AnalyticsRepository`).
A *question* is a trace whose input the user typed (silent refresh turns excluded).

### AI Dashboard — `overview`

| Card | Field | Query | Tables |
|---|---|---|---|
| Active AI users | `kpis.active_users` | `user_activity` | agent_traces |
| AI interactions | `kpis.interactions` | `turn_totals.questions` | agent_traces |
| SRs created via AI | `kpis.srs_created` | `sr_outcomes` | analytics_events (`sr_submitted`) |
| AI answer rate (+ coverage) | `kpis.answer_rate` | `turn_totals` | agent_traces.answer_status |
| Average user rating | `kpis.avg_rating` | `rating_totals` | agent_feedback (`USER_RATING`) |
| Thumbs-up rate | `kpis.thumbs_up_rate` | `thumbs_totals` | agent_feedback (`TURN_THUMBS`) joined to agent_traces |
| SR completion rate | `kpis.sr_completion_rate` | `sr_outcomes` | analytics_events (`sr_lifecycle_completed`, matched on sr_id) |
| P50 / P95 response time | `kpis.p50_latency_ms`, `kpis.p95_latency_ms` | `latency_percentiles` | agent_traces.total_latency_ms |
| LLM cost (+ pricing coverage) | `kpis.llm_cost` | `turn_totals`, `llm_usage` | agent_traces, agent_llm_calls |
| Tool reliability | `kpis.tool_reliability` | `tool_health` | agent_tool_calls |
| Escalation rate | `kpis.escalation_rate` | `turn_totals` | agent_traces.answer_status |
| Interactions / answer-rate trend | `answer_trend` | `answer_trend` | agent_traces |
| SR workflow funnel | `sr_funnel` | `workflow_funnel` | agent_traces.workflow_stage_after |
| Tool reliability table | `tool_reliability` | `tool_health` | agent_tool_calls |
| Agent health | `agent_health` | `agent_scorecard` | agent_traces, agent_feedback, agent_tool_calls |

### AI Analytics — `usage`

| Card | Field | Query | Tables |
|---|---|---|---|
| Active / new users, repeat rate | `kpis.active_users`, `new_users`, `repeat_user_rate` | `user_activity` | agent_traces |
| Sessions, questions per session, session length | `kpis.sessions`, `turns_per_session`, `avg_session_seconds` | `turn_totals`, `engagement` | agent_traces, chat_messages |
| Containment rate | `kpis.containment_rate` | `containment` | agent_traces |
| Usage over time / by hour | `daily`, `hourly_sessions` | `daily_series`, `hourly_sessions` | agent_traces |
| Usage by agent / top intents | `by_agent`, `by_intent` | `agent_breakdown`, `intent_distribution` | agent_traces |
| Usage by channel | `by_channel` | `channel_distribution` | agent_traces.channel |
| Usage by business function | `by_function` | `function_distribution` | agent_traces.sub_category |
| Usage by mall | `by_mall` | `claim_distribution` | service_request_drafts (lease mall), else agent_traces.mall_names when the user has one mall |
| Usage by role | `by_role` | `claim_distribution` | agent_traces.user_roles |
| Workflow usage, top questions, top users | `workflow_funnel`, `top_questions`, `top_users` | same-name methods | agent_traces, agent_feedback |

### AI Performance — `performance`

| Card | Field | Query | Tables |
|---|---|---|---|
| Answer / escalation rate, status mix | `kpis.answer_rate`, `escalation_rate`, `answer_status_mix` | `turn_totals` | agent_traces |
| Rating, positive / negative, by source | `kpis.avg_rating`, `positive`, `negative`, `ratings_by_source` | `rating_totals` | agent_feedback |
| Feedback participation | `kpis.feedback_participation` | `feedback_participation` | agent_traces, agent_feedback |
| Thumbs-up rate, vote participation, 👍 / 👎 by source | `kpis.thumbs_up_rate`, `thumbs_participation`, `thumbs` | `thumbs_totals` | agent_feedback (`TURN_THUMBS`), agent_traces |
| Thumbs trend / by business function | `thumbs_trend`, `thumbs_by_function` | `thumbs_daily`, `thumbs_by_function` | agent_feedback, agent_traces |
| Down-voted replies (with reason, details) | `downvoted_replies` | `downvoted_replies` | agent_feedback, agent_traces |
| Why replies got 👎 | `downvote_reasons` | `downvote_reasons` | agent_feedback (`metadata.reason`, `comment`) |
| LLM parse failures, token spend | `kpis.llm_parse_failure_rate`, `llm_usage` | `llm_usage` | agent_llm_calls |
| Latency P50 / P95 / P99 | `kpis.latency_ms` | `latency_percentiles` | agent_traces |
| Response time trend (daily P50 / P95) | `latency_trend` | `latency_daily` | agent_traces |
| Node latency | `node_latency` | `node_latency` | agent_runs |
| Failure reasons | `failure_reasons` | `failure_reasons` | agent_traces, agent_tool_calls |
| Agent scorecard | `agent_scorecard` | `agent_scorecard` | agent_traces, agent_feedback, agent_tool_calls |
| Feedback comments | `comments` | `feedback_comments` | agent_feedback |

### AI Productivity — `productivity`

| Card | Field | Query | Tables |
|---|---|---|---|
| SRs created, submission / completion rate | `kpis.srs_created`, `sr_submission_rate`, `sr_completion_rate` | `sr_outcomes` | analytics_events, agent_traces |
| Automated / assisted, intervention, correction | `kpis.automated_srs`, `human_intervention_rate`, `correction_rate`, `automation_mix` | `automation_mix` | analytics_events, agent_traces |
| Approvals, rejections, approval rate | `kpis.approvals`, `rejections`, `approval_rate`, `decisions_by_flow` | `sr_outcomes` | analytics_events (`sr_approved` / `sr_rejected`) |
| Time to decision / per review step | `decision_times`, `decision_step_times` | same-name methods | analytics_events |
| AI share of SRs | `kpis.ai_share_of_srs`, `platform_srs_by_function` | `platform_stats.ai_share_of_service_requests` | platform `service_request.created_via` |
| Productivity by business function | `by_function` | `productivity_by_function` | analytics_events, agent_traces, service_request_drafts |
| Cost per SR | `kpis.cost_per_sr_usd` | `submitted_cost` | agent_traces, analytics_events |
| Tasks per day | `tasks_daily` | `tasks_daily` | analytics_events |
| Productivity by agent | `by_agent` | `productivity_by_agent` | analytics_events, agent_traces, chat_sessions |
| SR log | `sr_log` | `sr_log` | analytics_events, chat_sessions |

### Who writes the data

| Data | Written by |
|---|---|
| Traces, answer status, channel / role / mall | `ChatOrchestrationService` → `TraceManager.start_trace` / `finish_trace` |
| `sr_submitted` | `TraceManager.capture_tool_call` when `tool_type = SERVICE_REQUEST_CREATE` succeeds |
| `sr_approved` / `sr_rejected` | FM, RDD, work-permit, fit-out submission nodes → `record_sr_decision` |
| `sr_lifecycle_completed` | `TraceManager.finish_trace` on `workflow_complete` or a terminal stage |
| Tool calls | lease lookup, handover create, FM, RDD nodes; work-permit, fit-out, notification inbox via `record_tool_call`; document uploads as `document_uploaded` events |
| Star ratings | tenant widget → `POST /api/v2/chat/feedback` |
| 👍 / 👎 on every reply, 👎 reason and details | tenant widget → `PUT /api/v2/chat/feedback/thumbs` |
| Usefulness, heartbeat, session end | tenant widget → `POST /api/v2/chat/sessions/{id}/feedback | heartbeat | end` |
| `created_via` on the SR | AI backend create call → platform `POST /service-requests` |

## 3. Checking the numbers

- **Reconcile:** `scripts/reconcile_command_center.py --base-url <api> --days 30 [--agent <key>]`
  compares 17 headline figures with independent SQL. Exit code = mismatches.
  The API caches each section for 60 s — rerun after a minute if data just changed.
- **Unit / contract:** `pytest tests/unit/test_command_center.py tests/unit/test_analytics_instrumentation_3784.py`
  and `PYTHONPATH=$(pwd) python -m tests.eval.analytics_eval` (all PASS).
- **Frontend:** `jest libs/ui-web/src/lib/components/assistantAdmin` in the tenant app.

## 4. Known limits — read before quoting a number

| Figure | Limit |
|---|---|
| AI answer rate | Share of classified questions the agent answered without a fallback — **not accuracy**; nothing checks answers against the truth. Read with its coverage. |
| Escalation rate | Questions the agent could not answer. There is no hand-off to a person. |
| LLM cost | Calls to a model without a known price add nothing. Read with pricing coverage. |
| Usage by mall | The SR draft's lease mall when there is one; otherwise the user's own mall only if the token lists exactly one. Help chats from users covering several malls are a separate count. |
| AI share of SRs | Counts only the assistant's flows (handover, fit-out start / end, trading). SRs created before `created_via` existed are only marked after `backfill_platform_created_via.py`. |
| Usage by channel / mall / role | Recorded from migration 010 onward; older traces are "not recorded". |
| Approvals / rejections | Handover FM / RDD history was backfilled; work-permit and fit-out decisions exist only from this release. |
| Thumbs | Counted by the day of the voted reply, not the day of the vote. Only from the widget release that shows the thumbs; one vote per user per reply, so a cleared vote leaves no trace. |
| Session active time, usefulness | Only from sessions opened after the widget release (heartbeat, usefulness prompt). |
| Tool reliability | Document uploads are counted only when tied to a chat session. |
| SR completion | Counts SRs whose final stage was reached through the assistant; completion done only on the platform is not seen. |
| Hours / cost saved, accuracy | Not available — no baselines or ground truth in the data. Do not show. |

## 5. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| SRs created = 0 but SRs exist | Backfill not run in this environment | Run `backfill_analytics_events.py --apply` |
| A card shows "—" | Nothing measured in the window (denominator 0) | Widen the window or pick another agent |
| "low coverage" chip on answer rate | Many questions predate answer classification | Expected for old windows; narrow to recent dates |
| Section returns 500 | Query error — see `command_center.build_failed` in logs | Reproduce with `reconcile_command_center.py`; check migration 010 ran |
| Platform SR fetch fails after deploy | Platform API deployed before migration 1789482400 | Run the platform migration |
| Numbers lag a fresh SR | 60 s section cache | Wait a minute |
