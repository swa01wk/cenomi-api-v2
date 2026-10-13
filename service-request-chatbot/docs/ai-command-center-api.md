# AI Command Center API (ticket 3784)

Contract for the four sections of the AI Command Center. KPI definitions (K-numbers) are in
[`ai-command-center-full-plan.md`](./ai-command-center-full-plan.md) §3; the queries are in
`backend/app/observability/repositories/command_center_repo.py`; the routes in
`backend/app/api/routes/command_center.py`.

| Endpoint | Section |
|---|---|
| `GET /api/v1/dashboard/ai-command-center/overview` | AI Dashboard — executive health |
| `GET /api/v1/dashboard/ai-command-center/usage` | AI Analytics — usage & engagement |
| `GET /api/v1/dashboard/ai-command-center/performance` | AI Performance — quality & reliability |
| `GET /api/v1/dashboard/ai-command-center/productivity` | AI Productivity — automation & efficiency |

## Request

Headers — same as `/api/v1/dashboard/assistant-admin`: `x-internal-api-token: <NEXT_PUBLIC_AI_API_KEY>`.

| Param | Sections | Notes |
|---|---|---|
| `from`, `to` | all | ISO-8601. Default: trailing 30 days ending now (to the minute). `from` must be before `to`; max 365 days → `422` otherwise |
| `agent` | all | e.g. `rdd_handover_agent`, `work_permit_agent`, `help_agent` |
| `tz` | all | Viewer's IANA zone, e.g. `Asia/Riyadh`. Per-day series (`date` keys) are calendar days in this zone. Default `UTC`; unknown zone → `422` |
| `max_score` | performance | comment feed: only ratings ≤ this (1–5) |
| `page`, `page_size` | performance (comments), productivity (SR log) | `page_size` ≤ 100, default 20 |

Responses are cached in-process for 60 s per (section, window, agent, paging).

## Response rules

- Envelope `{ "success": true, "data": { … } }`; errors `{ "detail": "…" }` with `422` / `500`.
- **Ratio** = `{ "value", "numerator", "denominator" }`. `value` is `null` when `denominator` is 0 — render "no data", never 0%.
- Daily series (`answer_trend`, `daily`, `thumbs_trend`, `latency_trend`, `tasks_daily`, `rating_trend`) list only days with data; the page fills every day of the window (counts → 0, rates and times → a gap).
- **Headline with delta** = `{ "value", "previous", "delta" }` against the same-length window before (`window.previous`).
- **Answer Rate** always carries `coverage` (classified ÷ questions); **LLM cost** carries `pricing_coverage` (priced calls ÷ calls). Show both beside the number.
- Labels: `answer_rate` is **"AI Answer Rate"**, never "Response Accuracy". `sr_submission_rate` (K20) and `sr_completion_rate` (K21) are different numbers.
- **Thumbs** are counted against the voted reply's trace: the window, `agent` and business function mean "replies given in this window". `thumbs_up_rate` = 👍 ÷ (👍 + 👎); `thumbs_participation` = replies with a vote ÷ replies (turns with assistant text). `source` is where the reply sat: `help_answer` (no workflow stage), `sr_step` (inside an SR flow), `sr_created` (SR created / completed stage).
- `by_channel` = `[{channel, questions, sessions, users}]` per surface (`copilot_rail`, `copilot_panel`, `msp_help`, `unknown`). `by_mall` / `by_role` = `{items: [{key, questions, sessions, users}], not_recorded_questions, multiple_questions}`. Malls are attributed only for users with a single mall (the token lists every mall a user may access); multi-mall users are `multiple_questions`. Roles are expanded. Recorded from migration 010 onward.

## Known data limits

- **SR creator (platform):** `cenomi-api` migration `1789482400` adds `service_request.created_via` (`USER` default, `AI_ASSISTANT`). The AI backend sends `created_via: "AI_ASSISTANT"` on every create (handover, work permit, fit-out). Until that platform build is deployed the field is ignored, and `srs_created` here stays event-based.
- **Channel:** the tenant widget sends `context.channel` = `copilot_rail` (docked Copilot) or `copilot_panel` (floating chat); MSP help turns record `msp_help`. Older traces and other clients record `unknown`.

- `sr_submitted` fired for no flow before this ticket; history was rebuilt with `backend/scripts/backfill_analytics_events.py`. Run it once per environment after deploying (dry run by default, `--apply` to write; idempotent).
- Tool reliability covers lease lookup, handover create, FM, RDD, work-permit and fit-out calls. Upload and notification calls are not captured yet.
- Reviewer decisions (`approvals`, `rejections`, `decision_times`) exist for handover FM/RDD from history; work-permit and fit-out decisions only from this release on.
- Session active time, usefulness and CSAT stay empty until the tenant widget sends heartbeat / session feedback (plan task C-8).

## Response shapes

Arrays show their first element only. Values are from a local database, for illustration.

### `overview`

```json
{
  "window": {
    "from": "2026-08-01T00:00:00+00:00",
    "to": "2026-09-16T00:00:00+00:00",
    "agent": null,
    "previous": {
      "from": "2026-06-16T00:00:00+00:00",
      "to": "2026-08-01T00:00:00+00:00"
    }
  },
  "kpis": {
    "active_users": {
      "value": 16,
      "previous": 2,
      "delta": 14
    },
    "interactions": {
      "value": 3365,
      "previous": 19,
      "delta": 3346
    },
    "srs_created": {
      "value": 68,
      "previous": 0,
      "delta": 68
    },
    "answer_rate": {
      "value": 0.9949,
      "numerator": 3315,
      "denominator": 3332,
      "previous": 1.0,
      "coverage": {
        "value": 0.9902,
        "numerator": 3332,
        "denominator": 3365
      }
    },
    "avg_rating": {
      "value": 4.5,
      "previous": null,
      "delta": null,
      "ratings": 14
    },
    "thumbs_up_rate": {
      "value": 0.75,
      "numerator": 6,
      "denominator": 8,
      "previous": null
    },
    "sr_completion_rate": {
      "value": 0.3824,
      "numerator": 26,
      "denominator": 68
    },
    "p50_latency_ms": {
      "value": 1922,
      "previous": 2682,
      "delta": -760
    },
    "p95_latency_ms": {
      "value": 7093,
      "previous": 5318,
      "delta": 1775
    },
    "llm_cost": {
      "total_usd": 5.72433,
      "total_tokens": 24362116,
      "llm_calls": 5051,
      "pricing_coverage": 0.3865
    },
    "tool_reliability": {
      "value": 0.9745,
      "numerator": 726,
      "denominator": 745
    },
    "escalation_rate": {
      "value": 0.0051,
      "numerator": 17,
      "denominator": 3332,
      "previous": 0.0
    }
  },
  "usage_trend": [
    {
      "date": "2026-08-04",
      "turns": 237,
      "answered": 235,
      "not_answered": 2,
      "answered_rate": 0.9916,
      "sessions": 51,
      "avg_latency_ms": 4790,
      "ratings": 0,
      "avg_rating": null
    }
  ],
  "answer_trend": [
    {
      "date": "2026-08-04",
      "questions": 173,
      "classified": 173,
      "answered": 173,
      "not_answered": 0
    }
  ],
  "hourly_sessions": [
    0
  ],
  "sr_funnel": [
    {
      "stage": "CREATE_SR",
      "stage_label": "Collecting details",
      "sessions": 415,
      "turns": 3529
    }
  ],
  "agent_health": [
    {
      "agent": "rdd_handover_agent",
      "agent_label": "RDD Handover Agent",
      "questions": 2746,
      "classified": 2722,
      "answered": 2719,
      "not_answered": 3,
      "p95_latency_ms": 6731,
      "cost": 5.051651,
      "avg_rating": 5.0,
      "ratings": 6,
      "thumbs_up": 4,
      "thumbs_down": 1,
      "tool_calls": 692,
      "tool_failures": 19
    }
  ],
  "tool_reliability": [
    {
      "tool": "lease_tenant_api",
      "tool_label": "Lease lookup",
      "calls": 593,
      "failed": 14,
      "success_rate": 0.9764,
      "avg_ms": 121,
      "p95_ms": 364
    }
  ]
}
```

### `usage`

```json
{
  "window": {
    "from": "2026-08-01T00:00:00+00:00",
    "to": "2026-09-16T00:00:00+00:00",
    "agent": null,
    "previous": {
      "from": "2026-06-16T00:00:00+00:00",
      "to": "2026-08-01T00:00:00+00:00"
    }
  },
  "kpis": {
    "active_users": 16,
    "new_users": 14,
    "repeat_user_rate": {
      "value": 0.5,
      "numerator": 8,
      "denominator": 16
    },
    "interactions": 3365,
    "interactions_per_user": {
      "value": 210.3125,
      "numerator": 3365,
      "denominator": 16
    },
    "sessions": 816,
    "turns_per_session": 4.12,
    "avg_session_seconds": 226,
    "median_session_seconds": 39,
    "containment_rate": {
      "value": 0.9885,
      "numerator": 689,
      "denominator": 697
    }
  },
  "daily": [
    {
      "date": "2026-08-04",
      "turns": 237,
      "answered": 235,
      "not_answered": 2,
      "answered_rate": 0.9916,
      "sessions": 51,
      "avg_latency_ms": 4790,
      "ratings": 0,
      "avg_rating": null
    }
  ],
  "by_agent": [
    {
      "agent": "rdd_handover_agent",
      "agent_label": "RDD Handover Agent",
      "turns": 4927,
      "answered": 4869,
      "failed": 0,
      "answered_rate": 0.9882,
      "avg_latency_ms": 2970
    }
  ],
  "by_intent": [
    {
      "intent": "CREATE_RDD_SERVICE_REQUEST",
      "count": 2213
    }
  ],
  "workflow_funnel": [
    {
      "stage": "CREATE_SR",
      "stage_label": "Collecting details",
      "sessions": 415,
      "turns": 3529
    }
  ],
  "hourly_sessions": [
    0
  ],
  "top_questions": [
    {
      "question": "i want to create a handover service request",
      "count": 194,
      "not_answered": 0
    }
  ],
  "top_users": [
    {
      "user_id": "e7cc848c-70de-56d7-9b2c-ee92076d4d66",
      "questions": 2634,
      "sessions": 435,
      "unanswered": 14,
      "last_active_at": "2026-09-15T10:59:26.573071Z",
      "avg_rating": 5.0,
      "ratings_count": 3
    }
  ],
  "by_mall": [],
  "by_role": [],
  "by_channel": []
}
```

### `performance`

```json
{
  "window": {
    "from": "2026-08-01T00:00:00+00:00",
    "to": "2026-09-16T00:00:00+00:00",
    "agent": null,
    "previous": {
      "from": "2026-06-16T00:00:00+00:00",
      "to": "2026-08-01T00:00:00+00:00"
    }
  },
  "kpis": {
    "answer_rate": {
      "value": 0.9949,
      "numerator": 3315,
      "denominator": 3332
    },
    "classification_coverage": {
      "value": 0.9902,
      "numerator": 3332,
      "denominator": 3365
    },
    "escalation_rate": {
      "value": 0.0051,
      "numerator": 17,
      "denominator": 3332
    },
    "avg_rating": 4.5,
    "ratings": 14,
    "positive": {
      "value": 0.8571,
      "numerator": 12,
      "denominator": 14
    },
    "negative": {
      "value": 0.0714,
      "numerator": 1,
      "denominator": 14
    },
    "neutral": {
      "value": 0.0714,
      "numerator": 1,
      "denominator": 14
    },
    "feedback_participation": {
      "value": 0.0187,
      "numerator": 13,
      "denominator": 697
    },
    "thumbs_up_rate": {
      "value": 0.75,
      "numerator": 6,
      "denominator": 8
    },
    "thumbs_participation": {
      "value": 0.004,
      "numerator": 8,
      "denominator": 2141
    },
    "tool_reliability": {
      "value": 0.9745,
      "numerator": 726,
      "denominator": 745
    },
    "llm_parse_failure_rate": {
      "value": 0.0026,
      "numerator": 13,
      "denominator": 5051
    },
    "latency_ms": {
      "p50": 2480,
      "p95": 7093,
      "p99": 14920
    },
    "failed_turns": {
      "value": 0.0,
      "numerator": 0,
      "denominator": 5765
    }
  },
  "answer_status_mix": {
    "answered": 3315,
    "partial": 0,
    "unanswered": 14,
    "failed": 3,
    "unclassified": 33
  },
  "answer_trend": [
    {
      "date": "2026-08-04",
      "questions": 173,
      "classified": 173,
      "answered": 173,
      "not_answered": 0
    }
  ],
  "rating_distribution": {
    "1": 1,
    "2": 0,
    "3": 1,
    "4": 1,
    "5": 11
  },
  "ratings_by_source": {
    "help_answer": {
      "count": 8,
      "avg_rating": 4.12
    },
    "sr_created": {
      "count": 6,
      "avg_rating": 5.0
    }
  },
  "thumbs": {
    "up": 6,
    "down": 2,
    "by_source": {
      "help_answer": { "up": 4, "down": 1 },
      "sr_step": { "up": 1, "down": 1 },
      "sr_created": { "up": 1, "down": 0 }
    }
  },
  "latency_trend": [
    { "date": "2026-09-15", "p50": 2060, "p95": 5708, "turns": 226 }
  ],
  "thumbs_trend": [
    { "date": "2026-09-16", "up": 6, "down": 2 }
  ],
  "thumbs_by_function": [
    { "function": "handover", "replies": 1765, "up": 4, "down": 2 }
  ],
  "downvote_reasons": [
    { "reason": "INCORRECT_OR_INCOMPLETE", "votes": 3, "with_comment": 2 },
    { "reason": "none", "votes": 1, "with_comment": 0 }
  ],
  "downvoted_replies": [
    {
      "trace_id": "f7d842a3-b00c-4ba5-8fc3-8b290eb37878",
      "session_id": "05fa42da-58d8-43de-9f75-18636c56f33c",
      "agent": "help_agent",
      "agent_label": "Help Agent",
      "question": "What documents do I need for a handover?",
      "answer": "First 300 characters of the reply…",
      "source": "help_answer",
      "reason": "INCORRECT_OR_INCOMPLETE",
      "comment": "The lease date is wrong",
      "down_votes": 1,
      "replied_at": "2026-09-16T10:12:03Z"
    }
  ],
  "rating_trend": [
    {
      "date": "2026-08-04",
      "ratings": 0,
      "avg_rating": null
    }
  ],
  "agent_scorecard": [
    {
      "agent": "rdd_handover_agent",
      "agent_label": "RDD Handover Agent",
      "questions": 2746,
      "classified": 2722,
      "answered": 2719,
      "not_answered": 3,
      "p95_latency_ms": 6731,
      "cost": 5.051651,
      "avg_rating": 5.0,
      "ratings": 6,
      "tool_calls": 692,
      "tool_failures": 19
    }
  ],
  "node_latency": [
    {
      "node": "service_request_api.create_service_request",
      "node_label": "Create service request",
      "calls": 71,
      "avg_ms": 2373,
      "p95_ms": 18347,
      "max_ms": 31557,
      "failed": 3
    }
  ],
  "tool_health": [
    {
      "tool": "lease_tenant_api",
      "tool_label": "Lease lookup",
      "calls": 593,
      "failed": 14,
      "success_rate": 0.9764,
      "avg_ms": 121,
      "p95_ms": 364
    }
  ],
  "llm_usage": {
    "by_prompt": [
      {
        "prompt": "handover_extraction_prompt",
        "prompt_label": "Reading details from the message",
        "model": "gpt-4o-mini",
        "calls": 1742,
        "tokens": 11161670,
        "avg_tokens": 6407,
        "avg_ms": 1577,
        "parse_failures": 0,
        "parse_failure_rate": 0.0,
        "cost": 0.097791,
        "priced_calls": 96,
        "token_share": 0.4582
      }
    ],
    "total_calls": 5051,
    "total_tokens": 24362116,
    "cost_coverage": 0.3865
  },
  "failure_reasons": [
    {
      "source": "lease_tenant_api",
      "source_label": "Lease lookup",
      "reason": "HTTP 401",
      "count": 7,
      "last_seen": "2026-09-02T09:19:46.501326Z"
    }
  ],
  "comments": {
    "items": [
      {
        "id": "e21814c9-ca50-46f8-95d9-cf9ed4a18a62",
        "trace_id": "489b8c32-8f67-4398-931b-2712279513f2",
        "session_id": "c593bc33-17af-40fc-80ca-29c714b3d100",
        "user_id": "5eb141a9-7177-50ef-a872-aa9c24c77dfc",
        "rating": 4,
        "comment": "ok",
        "source": "help_answer",
        "sr_id": null,
        "agent": null,
        "question": "How do I submit a service request?",
        "created_at": "2026-09-07T10:37:11.337889Z"
      }
    ],
    "total": 12,
    "page": 1,
    "page_size": 20,
    "has_next": false
  }
}
```

### `productivity`

```json
{
  "window": {
    "from": "2026-08-01T00:00:00+00:00",
    "to": "2026-09-16T00:00:00+00:00",
    "agent": null,
    "previous": {
      "from": "2026-06-16T00:00:00+00:00",
      "to": "2026-08-01T00:00:00+00:00"
    }
  },
  "kpis": {
    "srs_created": 68,
    "sr_submission_rate": {
      "value": 0.146,
      "numerator": 66,
      "denominator": 452
    },
    "sr_completion_rate": {
      "value": 0.3824,
      "numerator": 26,
      "denominator": 68
    },
    "automated_srs": 65,
    "assisted_srs": 1,
    "human_intervention_rate": {
      "value": 0.0152,
      "numerator": 1,
      "denominator": 66
    },
    "correction_rate": {
      "value": 0.0,
      "numerator": 0,
      "denominator": 66
    },
    "approvals": 51,
    "rejections": 0,
    "approval_rate": {
      "value": 1.0,
      "numerator": 51,
      "denominator": 51
    },
    "cost_per_sr_usd": {
      "value": 0.0206,
      "numerator": 1.401025,
      "denominator": 68
    },
    "llm_cost": {
      "total_usd": 5.72433,
      "total_tokens": 24362116,
      "llm_calls": 5051,
      "pricing_coverage": 0.3865
    }
  },
  "automation_mix": {
    "submitted_sessions": 66,
    "automated": 65,
    "assisted": 1,
    "corrected": 0,
    "failed": 386
  },
  "decisions_by_flow": [
    {
      "flow": "handover_fm",
      "approved": 28,
      "rejected": 0
    }
  ],
  "decision_times": [
    {
      "flow": "handover_fm",
      "srs": 24,
      "avg_hours": 49.92,
      "median_hours": 0.12
    }
  ],
  "tasks_daily": [
    {
      "date": "2026-08-04",
      "created": 4,
      "approved": 0,
      "rejected": 0,
      "total": 4
    }
  ],
  "by_agent": [
    {
      "agent": "rdd_handover_agent",
      "agent_label": "RDD Handover Agent",
      "srs_created": 68,
      "sessions": 66,
      "srs_completed": 26,
      "cost": 1.399381
    }
  ],
  "funnel": [
    {
      "stage": "CREATE_SR",
      "stage_label": "Collecting details",
      "sessions": 415,
      "turns": 3529
    }
  ],
  "sr_log": {
    "items": [
      {
        "session_id": "68aca2fd-23cf-4d44-af9d-17e20cba5496",
        "user_id": "e7cc848c-70de-56d7-9b2c-ee92076d4d66",
        "created_at": "2026-09-15T10:56:48.053153Z",
        "sr_id": "321051",
        "agent": "rdd_handover_agent",
        "agent_label": "RDD Handover Agent",
        "workflow_stage": "SR_CREATED",
        "lifecycle_completed": false,
        "last_decision": null
      }
    ],
    "total": 68,
    "page": 1,
    "page_size": 20,
    "has_next": true
  }
}
```


## Thumbs on replies — `PUT /api/v2/chat/feedback/thumbs`

Written by the tenant widget under every assistant reply (all agents). Same headers as
`POST /chat/feedback` (`x-internal-api-token` + the AI session bearer token).

```json
{ "trace_id": "<chat response trace_id>", "value": "up" | "down" | null, "message_id": "<optional>",
  "reason": "INCORRECT_OR_INCOMPLETE" | "NOT_WHAT_I_ASKED" | "SLOW_OR_BUGGY" | "STYLE_OR_TONE" | "OTHER" | null,
  "comment": "<optional, ≤ 2000 chars>" }
```

- One vote per user per reply (`ux_agent_feedback_turn_thumbs`, migration 011); a new value replaces
  the old one, `null` clears it.
- The widget saves the vote on the click. A 👎 then opens a "Share feedback" panel; submitting it sends
  `value: "down"` again with `reason` and / or `comment`. Both are accepted only with `"down"` (`422`
  otherwise); any later write replaces them, so switching to 👍 drops an earlier reason.
- `200` → `{ "trace_id", "value", "feedback": { … } | null }`; `401` not signed in; `403` another
  user's reply; `404` unknown trace; `422` any other value.
- Stored as `agent_feedback` `feedback_type = TURN_THUMBS`, `label = THUMBS_UP | THUMBS_DOWN`,
  `source` derived from the trace (`help_answer` / `sr_step` / `sr_created`), `metadata.agent`,
  `metadata.reason`, `comment`.
  Audit action `feedback.thumbs`.
