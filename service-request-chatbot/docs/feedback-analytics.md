# Agent Feedback & Analytics

End-user star ratings (1–5 + comment) under every AI answer and after every
AI-created SR, the turn-outcome classification behind the success/failure
ratios, and the analytics API and dashboard built on top.

Delivered across two repos:

| Repo | Branch | What |
|---|---|---|
| `cenomi-api-v2` | `feature/agent-feedback-analytics` | migration 007, feedback endpoint, `answer_status`, analytics API, assistant-admin dashboard API |
| `cenomi-frontend-2` | `feature/agent-feedback-analytics` | star row in the Copilot, ratings card + unanswered signal on the dashboard |

## What changed in this repo

### 1. Every chat response now carries durable turn identity

| Surface | New fields |
|---|---|
| `POST /api/v2/chat/service-request` (and `/api/chat/service-request`) | `message_id` — persisted `chat_messages.id` of the assistant reply. `trace_id` was already returned. |
| `POST /api/v1/chat` (MSP help, sync) | `trace_id` (additive). `message_id` is now the **persisted** assistant message id, no longer a throwaway UUID. |
| `POST /api/v1/chat/stream` (MSP help, SSE) | `done` event now includes `trace_id`. |

### 2. New endpoint — `POST /chat/feedback`

Mounted at `/api/chat/feedback`, `/api/v1/chat/feedback`, `/api/v2/chat/feedback`
(the frontend should use **`/api/v2/chat/feedback`**, same base as the chat turn).

Headers — identical to the chat turn call:

- `x-internal-api-token: <NEXT_PUBLIC_AI_API_KEY>`
- `Authorization: Bearer <ai session token>` — **required**: anonymous /
  unauthenticated subjects get `401`. Ratings are always attributable to the
  signed-in user, same rule as the SR flow.

Request body:

```json
{
  "trace_id": "d3b0…",            // required — from the chat response
  "rating": 5,                     // required — integer 1..5
  "comment": "answered perfectly", // optional, ≤ 2000 chars
  "source": "help_answer",         // "help_answer" | "sr_created"
  "message_id": "9f2a…",          // optional — chat response message_id
  "sr_id": "11711"                 // optional — send with source=sr_created
}
```

Response `201`:

```json
{
  "feedback": { "id": "…", "trace_id": "…", "user_id": "…", "session_id": "…",
                 "source": "help_answer", "score": 5, "comment": "…",
                 "feedback_type": "USER_RATING", "created_at": "…" },
  "created": true    // false when an existing rating for this turn was updated
}
```

Semantics:

- **Idempotent per (trace, user)** — re-submitting replaces the stars/comment
  (DB partial unique index `ux_agent_feedback_user_rating`).
- `user_id`, `session_id` are derived server-side (JWT + trace) — never sent
  by the client.
- `403` when the trace belongs to another user; `404` for an unknown trace.
- Never starts a chat turn, never touches session state, never opens a trace
  (same pattern as `GET /api/v1/notifications/inbox`).

### 3. Turn outcome classification (`agent_traces.answer_status`)

Every finished trace is now stamped `ANSWERED | PARTIAL | UNANSWERED | FAILED`:

- `faq_node` judges its own grounding: fallback reply → `UNANSWERED`,
  RAG-grounded → `ANSWERED`, static-prompt-only answer → `PARTIAL`.
- Supervisor `UNKNOWN` intent (clarification ask) → `UNANSWERED`.
- Graph/injection failures → `FAILED` (stamped by `fail_trace`).
- Everything else → `ANSWERED`.

### 4. Token/cost capture completed

`faq_node` and `response_generation_node` now record their LLM calls via
`capture_llm_call` (they previously discarded token counts), so per-trace
`total_token_count` / `estimated_cost` and the metrics summary reflect the
help agent's real usage.

### 5. Metrics summary extended (additive)

`GET /api/v1/observability/metrics/summary` now also returns:

```
answered_traces, partial_traces, unanswered_traces, answered_rate,
ratings_count, avg_rating
```

### 6. Admin feedback endpoint hardened

`POST /api/observability/traces/{trace_id}/feedback` now resolves the reviewer
from the JWT and records `user_id` + `session_id` on the row.

### 7. Migration

`alembic upgrade head` → revision `007_feedback_analytics`:
`agent_feedback` + `session_id, message_id, source, sr_id, metadata` and the
partial unique index; `agent_traces` + `answer_status` (indexed).

## 8. Analytics API

New router at `/api/v1/analytics` and `/api/v2/analytics`. All four accept
`from`, `to`, `agent`, `user_id`; the window defaults to the trailing 30 days.

| Endpoint | Returns |
|---|---|
| `GET /analytics/overview` | Turn volume, answered/partial/unanswered split, success and failure ratios, SR funnel, avg + p95 latency, tokens and cost, session time, rating summary, a daily series, and a per-agent breakdown |
| `GET /analytics/questions` | Paginated question log with each turn's outcome, latency and rating. `answer_status=UNANSWERED` is the FAQ content-gap report; `include_top=true` adds the most-asked questions |
| `GET /analytics/feedback` | Star distribution, averages by source, plus the paginated comment feed (`max_score=3` for detractors) |
| `GET /analytics/engagement` | Sessions, avg and median session length, turns per session, hourly session histogram, recent conversations |

`GET /api/v1/observability/metrics/summary` also gained `answered_traces`,
`partial_traces`, `unanswered_traces`, `answered_rate`, `ratings_count` and
`avg_rating` (all additive).

## 9. Assistant-admin dashboard API

`GET /api/v1/dashboard/assistant-admin` and `.../conversations` now serve the
exact contract the tenant frontend already renders (`{success, data}`
envelope, `24h`/`7d`/`30d` period keys, 24-slot hourly array, same
conversation row shape), so no rewrite was needed to point it here.

What changed versus the legacy implementation:

| Field | Was | Now |
|---|---|---|
| `accuracy_pct` | `sources_count > 0` groundedness proxy | Real `answer_status = ANSWERED` share |
| `user_satisfaction_csat_pct` | Derived from the same proxy | Real star ratings, as a share of the 5-point scale |
| `escalation_rate_pct` | Hard-coded `0` | Share of turns the agent could not answer |
| `escalated` / `resolved` | Hard-coded `false` | Per-session unanswered turns / SR created |
| `user_name` | Empty string | Resolved from `users`, else a short id label |
| `avg_session_length_min` | Sum of response times | Wall-clock first→last message |

Additive fields for the richer view: `summary_strip.avg_rating`,
`ratings_count`, `rating_distribution`; `top_questions[].not_answered`; and
`agent`, `duration_seconds`, `avg_rating`, `srs_created` per conversation.

## Frontend (cenomi-frontend-2)

Both agents render through one component chain, so one rating row covers both:

- **Star row:** `ChatFeedbackStars.tsx`, mounted in
  `AIChatWidget/SrFabMessages.tsx` — under every assistant answer
  (`source="help_answer"`), and once beside `HandoverSuccessActions` when an
  SR was created (`source="sr_created"`, carrying `sr_id`). Covers the docked
  Copilot rail and the floating panel, since both render `SrFabMessages`.
  Picking a star submits immediately; the comment box then stays open and is
  nudged for ratings ≤ 3.
- **Transport:** `postChatFeedback()` in `srChatbotApi.ts`, called via the
  hook's `submitFeedback` so the AI session token is attached. Rating failures
  are isolated from the conversation — they never touch the chat error banner.
- **Dashboard:** `AssistantRatingCard` (average, count, 1–5 distribution with
  detractor/promoter colouring), an "N unanswered" badge on top questions, and
  a rating column on the conversations table.
- **i18n:** EN + AR keys under `srChatbot*` and `assistantAdmin.*`; the star
  row and card mirror in RTL.

## Metric definitions

- Questions asked = user rows in `chat_messages` (traces with empty input are
  silent refresh turns and are excluded from the question log).
- Answered ratio = `answer_status = 'ANSWERED'` ÷ classified traces; failure
  ratio is `UNANSWERED + FAILED` over the same denominator.
- SR funnel = `service_request.created` audit events ÷ sessions handled by a
  workflow agent.
- Time spent = last − first `chat_messages.created_at` per session, so a
  one-turn session reads ~0 rather than being dropped.
- CSAT = average and distribution of `agent_feedback.score` where
  `feedback_type = 'USER_RATING'`, split by `source`.

## Verification performed

Migrations 001→007 and every analytics query, endpoint, and the feedback
auth/idempotency rules were executed against a live PostgreSQL 18 instance
with seeded multi-user, multi-day data. Backend: 2,304 unit tests pass with
no new failures. Frontend: 20 new tests, no new failures and no new
TypeScript errors.
