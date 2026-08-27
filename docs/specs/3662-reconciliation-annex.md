# Reconciliation Annex — Notification Agent (TC-3662)

**To:** author of `notification-agent-integration-reconciled.md`
**From:** implementation (cenomi-api-v2 `feature/notifications-agent-integration`,
cenomi-frontend-2 `feature/TC-3662-notification-agent-chat`)
**Date:** 26 August 2026

The implementation is now aligned with the reconciled spec. This annex records
(1) what changed to align, (2) three places where the spec needs correction
against production reality, (3) answers to the spec's §9 open questions, and
(4) shipped behaviour beyond the spec, for absorption into its next revision.

---

## 1 · Changes made to align

- **Two-node architecture (spec §2)** — implemented as specified:
  - `agents/graph/nodes/notification/list_notifications_node.py` — filters
    (via `notification_intent.parse_notification_list_filters`), MSP fan-out,
    normalisation, grouping, `notification_list` payload.
  - `agents/graph/nodes/notification/open_notification_node.py` — routing shim:
    `active_notification_id` (dict **or bare string** = SR id) →
    `backend_refs.sr_id` + `active_agent` → edge to `sr_status_sync`.
    Exports `SUB_CATEGORY_TO_AGENT`, derived from the existing agent registry
    so there is one source of truth. Missing sr_id → plain error turn
    (routed to response_generation so the message is not swallowed).
  - Graph: `LIST_NOTIFICATIONS → list_notifications → save_state`;
    `OPEN_NOTIFICATION → open_notification → sr_status_sync → fm/rdd/fitout entry`.
  - `state.py` gained `active_notification_id: dict | str | None`.
  - The deictic layer (explain / mark-read, Flow C) stays in the separate
    context node — untouched, as the spec says.
- **Group payload (spec §4.2)** — added `service_category` and
  `action_required_count` (computed backend-side; FE renders it, never
  recomputes). Rows now carry `sub_category`; the FE Open handler reads the
  row, falling back to the group.
- **Per-row `action_required` badge + header "(N pending)" badge** rendered in
  `NotificationListCard`.
- **Supervisor prompt** — LIST_NOTIFICATIONS examples extended with the spec's
  phrases ("what's pending for me", "what needs my approval", "show pending
  actions", "get all notifications", "action required only"). These route via
  the LLM path when the deterministic regex misses them.
- **Bare-string `active_notification_id`** accepted at the API (`chat.py`
  validator) per spec §2.1 fallback.

## 2 · Spec corrections needed (production evidence)

### 2.1 §4.3 role audience table is stale

Measured on the production-copy DB (`cenomi.notifications` joined to
`cenomi.service_request`, action-required only):

| Sub-category | target_role_id distribution |
|---|---|
| FIT_OUT_CONFIRM_TRADING | 104 × role 11 (MALL_MANAGER), 104 × role 89 (OPERATIONS), **0 × role 12 (DD)** |
| HANDOVER | roles 12/20/11/89 + others — includes **87 × OPERATIONS** |
| FIT_OUT_CONFIRM_FOS / END | role 12 (DD) only |

The spec's "DD → all five, MM → HANDOVER only" would return **zero trading
rows forever** and hide OPERATIONS' handover inbox. Shipped audience (both the
Go handler and the chatbot):

| Role | Groups |
|---|---|
| MALL_MANAGER | HANDOVER, FIT_OUT_CONFIRM_TRADING |
| FM_MANAGER | HANDOVER |
| OPERATIONS | HANDOVER, FIT_OUT_CONFIRM_TRADING |
| DD_ENGINEER | HANDOVER, FIT_OUT_CONFIRM_FOS, FIT_OUT_CONFIRM_END |
| ADMIN / unknown | all (platform intersects server-side) |

### 2.2 FIT_OUT_CONFIRM_HO is decommissioned (product decision, 26 Aug)

Removed from the chatbot's endpoints, filters, labels, chips, and maps. The
platform tool endpoint still exists but is no longer called; the spec's
`_SUB_CATEGORY_NORMALISE` / `SUB_CATEGORY_TO_AGENT` / test-checklist HO entries
should be dropped. (Zero HO rows existed in the DB.)

### 2.3 Group label wording

Kept "Fit-out Start Confirmation" / "Fit-out End Confirmation" /
"Trading Confirmation" (not the spec's "Confirm Fit-Out Start" etc.) — they
match the platform's existing `FIT_OUT_CONFIRM_*` display names in
`translation.json`, and the FE localises via i18n keys anyway.

## 3 · Answers to spec §9 open questions

| Question | Answer |
|---|---|
| Row-level `sub_category` in MSP response? | Effectively yes — each TC-3520 endpoint is scoped to one sub-category, so the service stamps the canonical value on every row. (`text_content.sub_category` itself is a humanized display string — "fit out confirm trading" — never match on it.) |
| `action_required` boolean or string? | Boolean (`notifications.action_required`, Postgres bool). |
| Pagination? | Yes — all five endpoints take `page` + `limit` (≤50). View-more paging is already shipped end-to-end (`notification_list_more` request; `page`/`has_more` per group). |
| Tenant-targeted rows on the internal-token path? | Excluded — the handler intersects the caller's `tenant_contacts` roles with the tool audience and filters on `target_user_id = user OR target_role_id = ANY(intersected)`. |
| FIT_OUT_CONFIRM_HO constant? | Moot — decommissioned. |

## 4 · Shipped beyond the spec (for the next revision)

- **Explain** per row → `notification_detail_card` with a View-SR button
  (deictic Flow C made actionable).
- **View-more paging** per group (fresh card per page in the chat).
- **Lease-activation tier 0**: `ACTIVATED_LEASE_NOTIFICATION` rows are
  `kind: "lease_activation"` (its `text_content.id` is a **lease id** — the
  overloaded-id hazard), rendered with **Create Service Request** which seeds
  the handover CREATE flow with the lease code. Entry points: chat card, bell
  dropdown, detail modal.
- **SR-page redirect on Open** (split-screen with the docked Copilot), and
  new-SR-page redirect on Create.
- **Role-gated Copilot chips** (Handover / Fit-out / Trading notifications).
- `message_ar` per row; unread filter; humanized status labels.
- **Platform**: `notifications.entity_type/entity_id/sr_sub_category` columns
  (write-time derivation, no backfill; read path is column-first with a legacy
  join fallback) — this is what makes lease-activation rows visible to the
  agent tools and removes the `bigint = text` join failure and false-join
  hazard permanently.
