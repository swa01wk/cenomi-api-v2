# Change Log — Notification Agent (chatbot backend)

**Scope:** the notification agent in this repo (`cenomi-api-v2`,
`service-request-chatbot/backend`) — the inbox the assistant reads, the chat
turns it answers, and the read API the Copilot panel calls.

Companion logs: the platform Go API is tracked in `cenomi-api-2` at
`docs/CHANGE_LOG_NOTIFICATION_AGENT.md`; the tenant web UI in
`cenomi-frontend-2` at `docs/CHANGE_LOG_NOTIFICATION_AGENT.md`.

**Tickets:** TC-3520 (platform tools) · TC-3662 (assistant integration)

**Format:** newest change on top. Each entry names the files and **why** the
change was made.

---

## 2026-08-26 · Honest counts and page ranges

**Commit:** `cc4e3d1`

Two counting defects that the new panel made visible.

- **`action_required_count` was per page, not per group.** It summed
  `group.items`, which only ever holds the rows of the page just fetched — so
  the badge said 5 while "load more" kept producing rows. The platform tools
  filter to `action_required = TRUE` server-side, so every row in a group
  qualifies and `total_count` *is* the outstanding figure. Handover now badges
  598, not 5. (`notification_payload.py`)
- **Page ranges.** `NotificationGroup` gained the `limit` it was fetched with,
  so a card can render "11–20 of 598" instead of restating the page length —
  which looked frozen at "10/598" as the user paged.
  (`notification_inbox_service.py`, `notification_payload.py`)

---

## 2026-08-26 · Spec alignment, inbox read API, UX fixes

**Commit:** `c7375d1`

Aligned to the reconciled spec and hardened against production data.

### Architecture — two nodes, per reconciled spec §2

Notification handling was one node with the open-handoff seeded in
orchestration. It is now split the way the spec describes:

| File | Role |
|---|---|
| `nodes/notification/list_notifications_node.py` | filters → MSP fan-out → grouped `notification_list` payload |
| `nodes/notification/open_notification_node.py` | routing shim: `active_notification_id` → `backend_refs.sr_id` + `active_agent`, then the existing `sr_status_sync` pipeline |
| `nodes/notification/notification_entry_node.py` | keeps only the deictic explain / mark-read layer |

`SUB_CATEGORY_TO_AGENT` is derived from the agent registry rather than
redeclared, so sub-category routing has one source of truth.
`active_notification_id` moved into graph state, and a bare string is accepted
as the SR id (spec fallback). A missing SR id returns a plain message turn
routed to `response_generation`, so the error reaches the user instead of being
swallowed by an sr-less sync.

### New read API — `GET /api/v1/notifications/inbox`

**Why:** the Copilot welcome panel renders the inbox on open. Doing that
through `POST /chat/service-request` would create or append a `ChatSession`,
persist a user and assistant message, and open a trace on every panel open —
pollution for data that is only being displayed. The new route reads the JWT
for `user_id` and roles, calls the same `notification_inbox_service`, and
touches no session state. `notification_payload.py` shares the serializer with
the chat node so both surfaces return byte-identical rows.
(`api/routes/notifications.py`, `agents/services/notification_payload.py`)

### Payload

- Groups carry `service_category` and `action_required_count`; every row
  carries `sub_category` (the row is authoritative for the Open handler),
  `message_ar`, and `kind`.
- `kind: "lease_activation"` marks `ACTIVATED_LEASE_NOTIFICATION` rows, whose
  `text_content.id` is a **lease id**. Surfacing it as `sr_id` would open an
  unrelated service request; those rows carry the lease and drive a create
  flow instead.
- View-more paging (`notification_list_more`) end to end.

### Fixes

- **`FIT_OUT_CONFIRM_HO` decommissioned** (product decision) — removed from
  endpoints, filters, labels and maps.
- **Audience corrected to match the scheduler:** trading targets
  `MALL_MANAGER`/`OPERATIONS` (it was `DD_ENGINEER`, which could never return a
  row), and handover gained `OPERATIONS`. Matched by role **enum name**, never
  role id.
- **Raw enum codes no longer reach users.** `preview_node` was interpolating
  the raw status into the LLM instruction, which the model quoted verbatim
  ("with the status 'REPORT_SUBMITTED'"). Instructions and stage labels now go
  through `format_enum_label`, which also falls back to title-casing any
  unmapped `SCREAMING_SNAKE` code so a new platform status can never leak.
  (`display_formatters.py`, `preview_node.py`, `sr_status_sync_node.py`)

---

## 2026-08-25 · MSP inbox fetch, grouped list, OPEN_NOTIFICATION handoff

**Commit:** `be60f98` · doc: `12ef7d3`

The product path the earlier viewing-context work did not cover.

- **`notification_inbox_service.py`** — Abstract/Http/Mock triad over the
  platform's TC-3520 agent endpoints. `x-internal-api-token` auth, `user_id`
  from the JWT claim, role-audience fan-out with `asyncio.gather`, tolerant
  envelope unwrapping (`text_content` arrives as a dict *or* a JSON string),
  and partial-failure tolerance. `NOTIFICATIONS_USE_MOCK=true` for local dev.
- **Grouped list** — `response_ui.type = "notification_list"` with `groups[]`,
  each row carrying the `sr_id` the UI opens. NL filters for unread and
  sub-category.
- **`OPEN_NOTIFICATION`** — `active_notification_id` on `/chat` and the MSP
  context seeds the SR id and agent, then rides the existing
  `sr_status_sync → fm/rdd/fitout entry` pipeline. No new workflow code.
- **Two merge-introduced bugs fixed:** `process_turn` declared the `viewing_*`
  params but never forwarded them, raising `NameError` on every real turn; and
  the notification bypass in `_route_after_load` ignored the free-text guard,
  so an SR description mentioning "alerts" was hijacked into the inbox.

---

## Known limits

- Pagination is page-based per group; there is no cursor.
- No mark-read endpoint exists on the platform agent mux, so
  `notification_action / mark_read` still means *the UI* performs the mutation.
- `FIT_OUT_CONFIRM_HO` is decommissioned rather than supported.
