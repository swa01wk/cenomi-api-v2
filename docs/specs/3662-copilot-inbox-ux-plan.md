# Copilot Welcome Inbox — UX/UI Plan (TC-3662)

**Status:** plan for review — not implemented.
**Surface:** `SrCopilotPanel` welcome state only (`libs/ui-web/src/lib/components/AIChatWidget/SrCopilotPanel.tsx`).
The floating FAB `ChatPanel` welcome is unchanged.

## Problem

The welcome state currently spends its whole first screen on two notification
chips and four FAQ starters. The chips are *queries*, not *answers*: the user
must click, wait for a chat turn, and read a card to find out whether anything
needs them. For a Mall Manager or RDD PM opening the assistant, the first
question is always **"what needs me right now?"** — the panel should answer it
before being asked.

## Proposal

Replace the two chips with a **live action inbox** in the welcome body:
role-gated tabs, five rows visible, load-more in place.

```
┌──────────────────────────────────┐
│  ◐  AI Assistant                 │  ← compact header (logo 56px, was 80)
│     Ask me anything…             │
├──────────────────────────────────┤
│  NEEDS YOUR ACTION          ⟳    │  ← section label + refresh
│  ┌────┬─────────┬────────┬─────┐ │
│  │All⁷│Handover⁵│Fit-out²│Trad.│ │  ← tabs, role-gated, count badges
│  └────┴─────────┴────────┴─────┘ │
│  ● Mall Manager submitted the    │  ← row: 2-line clamp
│    handover request for Zara…    │
│    SR 320965 · 2d ago   Explain  │  ← meta + secondary action
│  ─────────────────────────────── │
│  ○ Contract DUMMY-CUST-001 is    │
│    now active. Initiate handover │
│    Lease DUMMY-CUST-001 · 5h     │
│  … 3 more rows …                 │
│         [ Load 5 more ]          │  ← in place, no chat turn
│         Open full inbox in chat →│  ← escape hatch to the card
├──────────────────────────────────┤
│  ASK ABOUT THE PLATFORM          │
│  How do I submit a service req…  │  ← starters demoted below
│  How do I view and manage my le… │
└──────────────────────────────────┘
```

### Tabs (role-gated, by enum name — never role id)

| Tab | Groups it covers | Visible to |
|---|---|---|
| All | every eligible group | only when ≥ 2 tabs qualify |
| Handover | `HANDOVER` | MALL_MANAGER, FM_MANAGER, OPERATIONS, DD_ENGINEER |
| Fit-out | `FIT_OUT_CONFIRM_FOS` + `FIT_OUT_CONFIRM_END` | DD_ENGINEER |
| Trading | `FIT_OUT_CONFIRM_TRADING` | MALL_MANAGER, OPERATIONS |

Derived from the existing `notificationChipsForRoles()` audience map in
`agenticSrAccess.ts` — one source of truth with the chips it replaces. A user
with one eligible group (FM Manager) sees **no tab bar**, just the list. A user
with none sees no section at all — the starters move back up.

Count badges use the backend's `action_required_count` (never recomputed FE-side).

### Rows

Reuse the visual language of `NotificationListCard` so the welcome list and the
chat card read as the same component family:

- **Whole row is the primary action.** Service-request rows → open the SR
  (`active_notification_id` + redirect to the SR page). Lease-activation rows
  (`kind: "lease_activation"`) → seed the handover CREATE flow with the lease code.
  No two-button-per-row layout — at 320–400px it crowds out the message.
- **Message** clamped to 2 lines (`-webkit-line-clamp`), Arabic body when locale is `ar`.
- **Meta line**: `SR 320965` or `Lease DUMMY-CUST-001` · relative time · **Explain**
  as a small ghost text button (stops propagation).
- **Unread** = filled dot at the row start; read = hollow. Cheaper than a badge
  at this density, and the whole list is action-required by definition.

### Load more

`Load 5 more` appends the next page **into the same list** — no chat turn, no
new card. After ~20 rows, swap it for `Open full inbox in chat →`, which sends
the normal list message so the user gets the full card with its own paging.
Rationale: the panel is a launcher, not a full inbox; deep browsing belongs in
the transcript (or the notifications page).

### States

| State | Treatment |
|---|---|
| Loading | 3 shimmer rows (respect `prefers-reduced-motion` → static tint) |
| Empty (tab) | "You're all caught up here." + muted check glyph |
| Empty (all tabs) | Section hidden entirely; starters take the space |
| Error | One line: "Couldn't load notifications." + `Retry` ghost button — never a blocking state |
| No role access | Section not rendered |

## Data — a read endpoint, not a chat turn

**This is the one architectural decision to sign off.** The welcome list must
not fetch through `POST /chat/service-request`: every chat turn creates or
appends to a `ChatSession`, persists user + assistant messages, and opens a
trace. A passive panel widget doing that on every open would pollute the
transcript and burn traces.

Add a small read-only route to the chatbot backend:

```
GET /api/v1/notifications/inbox?sub_category=HANDOVER&page=1&limit=5
→ { groups: [ { service_category, sub_category, label, total_count,
                action_required_count, notifications: [...], page, has_more } ] }
```

- Auth: the existing `get_auth_context` dependency — `user_id` from the JWT
  subject, roles from the JWT claim (same inputs the node uses).
- Implementation: calls `get_notification_inbox_service().fetch_inbox(...)` —
  the service already exists and is role-audience aware.
- **Extract the serializer**: move `_row_payload` / `_serialize_groups` out of
  `list_notifications_node.py` into `app/agents/services/notification_payload.py`
  so the node and the route emit byte-identical payloads. The node keeps the
  conversational wrapper (message text, filters echo).
- FE: a thin `getNotificationInbox()` in `libs/api/src/lib/srChatbot/` — not the
  chat hook, since it never touches session state.

Fetch on panel open and on tab change; cache per tab for the panel's lifetime;
refresh button re-fetches the active tab. No polling.

## Files

| File | Change |
|---|---|
| `app/api/routes/notifications.py` | **New** — the read route |
| `app/agents/services/notification_payload.py` | **New** — shared serializer |
| `app/agents/graph/nodes/notification/list_notifications_node.py` | Import the shared serializer |
| `libs/api/src/lib/srChatbot/notificationInboxApi.ts` | **New** — `getNotificationInbox()` |
| `libs/ui-web/.../AIChatWidget/CopilotInbox.tsx` | **New** — tabs + rows + load-more + states |
| `libs/ui-web/.../AIChatWidget/SrCopilotPanel.tsx` | Replace the chip row with `<CopilotInbox>`; compact the header |
| `libs/ui-web/.../AIChatWidget/SrCopilot.css` | Section, tabs, rows, skeleton |
| `libs/i18n/.../{enUS,ar}/translation.json` | Tab labels, section label, load-more, empty/error strings |

Removed: `NOTIFICATION_CHIPS` rendering in the welcome (the audience map stays —
`CopilotInbox` derives its tabs from it).

## Open questions for you

1. **Unread vs action-required**: every row here is action-required. Should the
   default tab also filter to *unread* only, or show all action-required
   (current behaviour)? Recommendation: all action-required, unread dot for
   scanning.
2. **Row count**: 5 as specified. At 400px that is roughly one full screen with
   tabs + starters below — confirm that starters staying visible matters, or
   whether the inbox should own the whole first screen.
3. **Ordering**: newest-first today. Unread-first would put the actionable ones
   on top but needs an `ORDER BY read_at NULLS FIRST` on the platform query.
4. Should the section also appear in the **FAB `ChatPanel`** welcome, or stay
   Copilot-only as now?

## Verification

- Role probes: MM → All/Handover/Trading; FM → no tabs, handover list; DD →
  All/Handover/Fit-out; a role with none → section hidden.
- 320px and 720px panel widths; `ar` locale (RTL tabs + Arabic bodies).
- Loading, empty, error, and load-more-to-exhaustion paths.
- Open + Create rows land in the same flows as the chat card (regression: the
  SR-page redirect and the seeded create prompt).
