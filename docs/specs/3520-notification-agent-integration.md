# Notification Agent Integration (TC-3520)

How the notification agent works end-to-end on the backend, and the exact frontend
changes that light it up.

- **Backend:** `cenomi-api-v2 / service-request-chatbot`, branch
  `feature/notifications-agent-integration` (commit `be60f98`)
- **Frontend:** `cenomi-frontend-2`, branch `tenants-platofrm-v2`
- **Platform API:** `cenomi-api-2`, branch `feature/TC-3520-notification-agent`
  (`docs/NOTIFICATION_AGENT_TOOLS_SPEC.md`)
- **Date:** 25 August 2026

---

## 1 · The three flows

The agent handles notifications through three distinct paths. The first two are the
product spec; the third is an additive layer that already existed.

### Flow A — List the inbox *(backend done)*

User types *"show my notifications"* (or a filtered variant). No LLM call is made —
the supervisor classifies it deterministically.

```
user message ──▶ POST /api/chat/service-request { message }
  ├─ notification_intent.py classifies LIST_NOTIFICATIONS (regex, no LLM)
  ├─ graph routes to notification_entry_node
  ├─ node parses NL filters ("unread", "handover", "FOS", "trading"…)
  ├─ notification_inbox_service fans out over the platform's five
  │  TC-3520 endpoints (x-internal-api-token, user_id from JWT),
  │  skipping endpoints outside the user's role audience
  └─ response_ui { type: "notification_list", groups[] } ──▶ UI renders
```

Each group is one SR sub-category; each row carries `sr_id` — the service-request id
from the platform's `text_content.id` — which is everything the UI needs to open it.

### Flow B — Open a notification into its workflow *(backend done)*

User taps **Open** on a row (in the chat card or the notifications page). The turn
carries no text.

```
tap Open ──▶ POST /api/chat/service-request
  { session_id: null, message: "",
    active_notification_id: { id: "11711", sub_category: "HANDOVER" } }
  ├─ orchestration seeds backend_refs.sr_id = "11711",
  │  intent = OPEN_NOTIFICATION, active_agent via registry
  │  (HANDOVER → rdd_handover_agent, FOS/END/TRADING → fitout agents)
  ├─ graph routes straight to sr_status_sync  (no LLM, no supervisor)
  ├─ sync fetches the SR, maps operations → stage (e.g. FM_REVIEW)
  └─ lands in fm_review_entry / rdd_review_entry / fitout_confirm_entry
     ──▶ the SAME review cards the chat already renders today
```

From the frontend's perspective this behaves exactly like the existing
`openExistingSr` deep-link — the response is a normal review-stage turn with
`sr_preview_card` / `document_requirement` / `confirmation_card` UI. Nothing new to
render after the open.

### Flow C — Deictic chat on the open notification *(backend done, optional for UI)*

When a notification detail view is open, the UI may attach viewing context to each
turn. Then *"explain this"* / *"mark this as read"* resolve without the user
repeating an ID. This layer already shipped and is unchanged — send
`viewing_notification_id` + a `viewing_notification` snapshot when convenient; skip
it for v1 if time is short.

---

## 2 · API contract

### Request — new field on the chat turn

```json
POST /api/chat/service-request
{
  "session_id": null,
  "user_id": "…",
  "message": "",
  "active_notification_id": {
    "id": "11711",
    "sub_category": "HANDOVER"
  }
}
```

- `session_id: null` — an open starts a new session.
- `message: ""` — empty is valid when `active_notification_id` is present.
- `active_notification_id.id` — **SR id** from the notification's `text_content.id`.
- `sub_category` — optional but recommended (seeds the correct agent up front).

The MSP widget path (`/api/v1/chat`) accepts the same object inside
`context.active_notification_id`.

### Response — the one new UI type to render

```json
"ui": {
  "type": "notification_list",
  "message": "You have 4 action-required notifications: …",
  "filters": { "unread": true, "sub_categories": ["HANDOVER"] },
  "groups": [
    {
      "sub_category": "HANDOVER",
      "label": "Handover",
      "total_count": 2,
      "notifications": [
        {
          "notification_id": "ntf-…",
          "sr_id": "11711",
          "message": "Handover SR 11711 requires your review.",
          "created_at": "2026-08-20T09:00:00Z",
          "read": false,
          "action_required": true,
          "lease_id": "501",
          "unique_property_id": "3691"
        }
      ]
    }
  ]
}
```

- `notification_id` — platform notification row id (display only).
- `sr_id` — pass back as `active_notification_id.id` on Open.

> **Group order is fixed:** HANDOVER → FIT_OUT_CONFIRM_HO → FOS → END → TRADING.
> Only non-empty groups are sent. An empty inbox returns `groups: []` with a
> friendly `message`; a platform outage returns plain `type: "message"` — both
> render with zero extra work.

### Who sees what

| Role                      | Groups they can receive                            |
| ------------------------- | -------------------------------------------------- |
| Mall Manager / FM Manager | HANDOVER only                                      |
| DD Engineer (RDDPM)       | All five                                           |
| Admin / unknown roles     | All five (platform intersects server-side)         |

---

## 3 · Frontend integration plan

Grounded in the actual repo — the heavy machinery already exists. Four steps,
roughly in order.

### Step 1 — API layer: types and request field

`libs/api/src/lib/srChatbot/srChatbotApi.ts`

- Add `'notification_list'` to `SrChatbotUiType` (line 4).
- Add payload types: `SrChatbotNotificationRow`, `SrChatbotNotificationGroup`, and
  `groups?` / `filters?` on `SrChatbotUiPayload`.
- Add `ActiveNotificationRef { id: string; sub_category?: string }` and
  `active_notification_id?: ActiveNotificationRef | null` to
  `ServiceRequestChatTurnRequest` (line 94), and forward it in the POST body next to
  `sr_id` (~line 343).

### Step 2 — Chat hook: an `openNotification` action

`libs/ui-web/src/lib/components/serviceRequestChatbot/useServiceRequestChat.ts`

- Mirror `openExistingSr` (line 916): `resetSession()` then
  `sendTurn({ message: '', activeNotificationId: ref, forceNewSession: true, showUserBubble: false })`.
- Thread `activeNotificationId` through `sendTurn`'s params into the request body.
- Export `openNotification` from the hook's return (alongside `openExistingSr`,
  line 1070) so both the chat card and the notifications page can call it.

### Step 3 — Render the list card

`libs/ui-web/src/lib/components/serviceRequestChatbot/`

- New `NotificationListCard.tsx`: groups as labelled sections (use `label` from the
  payload), rows with message, relative time, an unread dot when `read === false`,
  and an **Open** button per row →
  `openNotification({ id: row.sr_id, sub_category: group.sub_category })`.
  Disable Open when `sr_id` is null.
- Add the `ui.type === 'notification_list'` branch in `SrChatUiRenderer.tsx` (follow
  the `lease_selection` branch at line 45 as the pattern) and pass the handler down
  from `AgenticChatShell` the same way `onSelectLease` flows.
- i18n: strings through the existing `t()` prop; add EN + AR keys.

### Step 4 — Wire the existing notifications UI

`libs/ui-web/src/lib/components/notifications/`

- `NotificationRow.text_content` already carries `id` (the SR id) and `sub_category`
  (`notificationTypes.ts` line 9) — no new data needed.
- Add an **Open in assistant** action to `NotificationDetailModal.tsx` and/or the
  row actions in `AllNotificationsListing.tsx`: open the chat FAB and call
  `openNotification({ id: text_content.id, sub_category: text_content.sub_category })`.
  This mirrors how SR deep-links already trigger `openExistingSr`.
- Optional (Flow C): while the detail modal is open, include
  `viewing_notification_id` and a `viewing_notification` snapshot
  (`{ id, title: text_content.en, body: text_content.message, related_sr_id: text_content.id }`)
  on chat turns so "explain this" works.

---

## 4 · Test checklist

- **List:** "show my notifications" as DD Engineer → five groups; as Mall Manager →
  HANDOVER only. "unread handover notifications" → filtered card echoing `filters`.
- **Open from chat card:** Open on a HANDOVER row as FM → FM review card appears
  (same UI as opening from the SR list today).
- **Open from notifications page:** Open in assistant on a FOS notification as DD →
  fit-out start confirmation flow.
- **Mid-workflow safety:** open a notification while an SR draft is in progress →
  the draft's agent is not clobbered; sync drives the routing.
- **Empty & failure:** no notifications → "all caught up" message; platform down →
  plain apology message, no broken card.
- **Backend env for local dev:** `NOTIFICATIONS_USE_MOCK=true` serves deterministic
  fixtures (SRs 11711/11712 + one per confirm type) with no platform dependency.
  Production needs `PLATFORM_INTERNAL_API_TOKEN` set (already used for platform
  login).

---

## 5 · Gotchas

- `active_notification_id.id` is the **SR id** (`text_content.id`), never the
  notification row id. The row id is `notification_id` and is display-only for now.
- The list UI type is `notification_list` — the earlier stub name
  `notification_list_card` is gone; nothing shipped against it.
- `FIT_OUT_CONFIRM_HO` has no dedicated chat agent yet; opening one still works
  (the backend re-derives routing from the SR payload) but verify with one real HO
  SR during integration.
- Pagination is v1-scoped: page 1, 10 rows per group, with `total_count` available
  if the card wants a "+N more · view all" link to the notifications page.
- There is no mark-read platform endpoint on this mux —
  `notification_action / mark_read` responses still mean the UI performs the
  mutation through its existing notifications API.
