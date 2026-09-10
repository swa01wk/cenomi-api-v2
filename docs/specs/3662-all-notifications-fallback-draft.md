# DRAFT — Covering *all* notifications in the agent (not just the 5 actionable flows)

**Status:** draft for discussion, not scheduled. Written 26 Aug 2026.
**Related:** `docs/specs/3520-notification-agent-integration.md` (the shipped actionable path).

---

## The gap

Today the notification agent only understands five SR sub-categories
(`HANDOVER`, `FIT_OUT_CONFIRM_HO/FOS/END/TRADING`) because those are the only
tools the platform exposes and the only workflows the chatbot has agents for.

Every other notification the tenant platform sends — sales data, documents
expiring, leases, invoices, offers, work permits, announcements — is invisible
to the agent. A user who asks "what needs my attention?" gets a partial answer,
which is worse than no answer because it looks complete.

## Proposal (the "explain + redirect" tier)

Two tiers of notification handling, chosen by whether the chatbot has an agent
for that notification's workflow:

| Tier | When | What the agent does |
|------|------|---------------------|
| **Actionable** (today) | Sub-category maps to a registered agent | Opens the SR and drives the workflow to completion in chat |
| **Explain + redirect** (new) | Everything else | Explains the notification in plain language, then offers a deep-link button to the right platform page — the user completes it manually |

The second tier deliberately does **not** try to act. It answers
"what is this and what do I do about it?", then hands off. That is honest about
the agent's limits and still removes the main friction (decoding the
notification and finding the right screen).

### Why this is the right call

- **Coverage without new agents.** Every notification becomes explainable on day
  one; building a workflow agent per category would take quarters.
- **No false capability.** The agent never implies it can do something it can't.
  A redirect button is a clear boundary.
- **The redirect already exists.** The FE has `handleNotificationNavigation` +
  `navigationMap` (`libs/ui-web/src/lib/utils/Util.ts`), used by both the header
  dropdown and the detail modal. The agent's button can reuse the same mapping —
  no new routing logic.
- **Graceful growth.** When a new workflow agent ships, its sub-category moves
  from tier 2 to tier 1 by adding one registry entry; nothing else changes.

## Sketch of the work

### Platform (cenomi-api-2)
A generic agent endpoint is needed — the current five are hard-coded per
sub-category:

```
GET /notifications/agent/all?user_id=&unread=&page=&limit=
```

Same auth (`x-internal-api-token`), same envelope, same role-intersection
logic, but no `sub_category` filter and **no** `action_required = TRUE` clamp
(or make it a query param — informational notifications matter here). Should
return `text_content.category` / `sub_category` / `key` so the chatbot can
classify each row.

### Chatbot (cenomi-api-v2)
- `notification_inbox_service`: add `fetch_all_inbox()` alongside the
  sub-category fan-out; keep the existing typed path for tier 1.
- New classifier: `notification_key → tier`. Tier 1 when
  `lookup_agent(category, sub_category)` resolves; tier 2 otherwise.
- Grouping for tier 2 by `text_content.category` (SALES_DATA, LEASE_DOCUMENTS,
  INVOICE, …) rather than SR sub-category.
- Explanation: the FAQ/RAG stack already answers "what does this mean" — feed
  the notification's `en`/`ar` body plus its `key` into the existing FAQ node
  rather than writing a second LLM path. The nine handover keys we catalogued
  (`FIT_OUT_HANDOVER_REPORT_REQUIRED_DD` etc.) are good seed content for the
  index.
- `response_ui`: extend `notification_list` rows with
  `actionable: boolean` and, for tier 2, a `redirect` object
  (`{ notification_type, entity_id, category, key, … }`) — exactly the
  arguments `handleNotificationNavigation` already takes.

### Frontend (cenomi-frontend-2)
- `NotificationListCard`: render tier-2 rows with a secondary
  "Open in platform" button instead of the primary "Open" (which stays for
  tier 1). Wire it to `handleNotificationNavigation` via the existing
  `navigationMap`, plus an "Explain this" action that sends
  `viewing_notification_id` + snapshot (the Flow C layer that already shipped).
- Chips: add an "All notifications" chip next to the three role-gated ones.

---

## Tier 0 — `ACTIVATED_LEASE_NOTIFICATION` (the missing front door)

Found 26 Aug while auditing the data. This is the **Mall Manager's most
important actionable** — it is where the handover lifecycle *starts* — and it is
currently invisible to the agent for a structural reason.

**Payload** (634 rows; 206 `action_required=true` for Mall Managers, 428 false
for Leasing Admin / RDDPM):

```json
{ "key": "ACTIVATED_LEASE_NOTIFICATION",
  "type": "ACTIVATED_LEASE_NOTIFICATION",
  "id": "273341",                 // ← LEASE id, not an SR id
  "sub_category": "HANDOVER",     // ← enum-cased, unlike SR notifications
  "category": "FIT_OUT_AND_HANDOVER",
  "en": "Contract t0112426 is now active. Please initiate the handover of the
         lease by creating a Fit-Out & Handover Service Request." }
```

Written by `lease/usecase/lease_sync_usecase.go:1193` — one row per recipient,
so `target_role_id` is NULL and targeting is per `target_user_id`.

### Why the agent can't see it

The TC-3520 tools `INNER JOIN service_request ON service_request_id = text_content.id`.
For this notification there **is no SR yet** — that is the entire point of the
message. Measured against the DB:

- 609 of 634 rows *do* join, purely because a lease id collides with an
  unrelated `service_request_id`
- **0** of those SRs belong to the notification's lease
- **0** are `sub_category = HANDOVER` (samples land on `MALL_CLOSING_CHECKLIST`)

So it is filtered out — no wrong rows leak into the tools today, but the most
valuable actionable never appears.

### The `text_content.id` overload (dangerous)

`id` means different entities depending on `notification_type`:

| `notification_type` | `text_content.id` | `text_content.sub_category` |
|---|---|---|
| `SERVICE_REQUEST` | service request id | humanized: `"handover"`, `"fit out confirm trading"` |
| `ACTIVATED_LEASE_NOTIFICATION` | **lease id** | enum: `"HANDOVER"` |

This bit the first cut of the FE gate: matching on `sub_category` alone matched
*only* the lease notification (the sole enum-cased value) and missed every real
SR notification, then sent a lease id as an SR id — opening an unrelated SR.
Fixed in `notificationTypes.ts` by requiring
`notification_type === 'SERVICE_REQUEST'` and normalizing the humanized
sub-category. **Any future consumer must apply the same two rules.**

### What tier 0 should do

Not "open the SR" — there isn't one. The right action is **start creating it**,
which the chatbot already supports (`rdd_handover_agent`, `CREATE_SR`, keyed on
lease). Proposed contract: a new body field parallel to `active_notification_id`

```json
{ "active_lease_notification": { "lease_id": "273341", "sub_category": "HANDOVER" } }
```

seeding intent `CREATE_RDD_SERVICE_REQUEST` with the lease pre-resolved via the
existing lease-lookup service — so the Mall Manager goes from notification to a
pre-filled handover draft in one click. This is higher value than the entire
tier-2 fallback and should probably ship first.

Platform-side it needs either a sixth tool
(`GET /notifications/agent/lease-activation`, no SR join, filtered on
`key = 'ACTIVATED_LEASE_NOTIFICATION'`) or a `LEFT JOIN` variant of the existing
handover tool that also returns lease-scoped rows.

## Open questions

1. Should tier 2 include **non**-action-required notifications, or stay
   action-required only? (Leaning: include them, but sort action-required first
   and default the chip to action-required.)
2. Grouping key for tier 2 — `category` is coarse (SALES_DATA covers many
   states). Might need `category` + `key` for a useful label.
3. Volume: a user can have thousands of notifications. Pagination and a
   "show more" affordance become mandatory, unlike the current top-10-per-group.
4. Explanation quality depends on the RAG index actually containing content per
   notification `key`. Needs an audit of which keys have documentation.
5. Arabic: `text_content.ar` exists per notification — prefer it over an
   LLM translation when the user's locale is `ar`.

## What we are NOT proposing

- Building workflow agents for sales data / documents / invoices. That is a
  per-workflow project, not part of this.
- Letting the agent mutate anything in tier 2. Explain and redirect only.
