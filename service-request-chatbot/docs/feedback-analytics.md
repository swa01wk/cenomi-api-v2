# Agent Feedback & Analytics — Backend Contract (Phase 0–1)

Ticket scope: end-user star ratings (1–5 + comment) under every AI answer and
after every AI-created SR, plus the turn-outcome classification the analytics
layer reports on. This doc is the contract to share with the
`cenomi-frontend-2` team.

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

## Frontend integration (cenomi-frontend-2)

Both chat surfaces render through one component, so one rating row covers
both agents:

- **Attach point:** `libs/ui-web/src/lib/components/AIChatWidget/SrFabMessages.tsx`
  — inside the `messages.map(...)` block, after `SrChatUiRenderer`, for
  `msg.role === 'assistant'`. `msg.traceId` is already stored per message
  (`useServiceRequestChat.ts`); also store the new `message_id` from the
  response alongside it.
- **Help answers (`source: "help_answer"`):** show the 1–5 star row under
  every assistant answer. Comment box expands after a star is picked; nudge
  for a comment when rating ≤ 3. Collapse to a "Thanks for your feedback"
  chip once submitted (`created`/update both count).
- **SR created (`source: "sr_created"`):** show once, next to
  `HandoverSuccessActions` (the "View Service Request" card, i.e. when
  `isComplete && createdSrId`), rating the whole flow. Send
  `sr_id: createdSrId` and the trace id of the final turn.
- **API layer:** add `submitChatFeedback()` beside the turn call in
  `libs/api/src/lib/srChatbot/srChatbotApi.ts` — same
  `getAiApiBaseUrl()` + `aiRequestHeaders(...)` helpers, POST
  `/api/v2/chat/feedback`.
- **i18n:** add EN/AR keys (e.g. `srChatbotRatePrompt`,
  `srChatbotRateThanks`, `srChatbotRateCommentPlaceholder`); star row must
  mirror in RTL.

## Metric definitions the analytics build on (Phase 3–4)

- Questions asked = user rows in `chat_messages`.
- Answered ratio = `answer_status = 'ANSWERED'` ÷ classified traces.
- SR funnel success = `service_request.created` audit events ÷ SR flows
  started.
- Time spent = last − first `chat_messages.created_at` per session
  (30-min idle cutoff), aggregated per user.
- CSAT = avg/distribution of `agent_feedback.score` where
  `feedback_type = 'USER_RATING'`, split by `source`.
