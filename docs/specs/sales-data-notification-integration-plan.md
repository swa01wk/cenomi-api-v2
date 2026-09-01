# Sales Data Agent × Notifications — Integration Plan

**Status:** proposal — for stakeholder approval before execution
**Inputs:** [`sales-data-agent-integration-guide.md`](sales-data-agent-integration-guide.md) (§8 is the
notification section; this plan executes and extends it),
[`notification-lifecycle-full-rollout-plan.md`](notification-lifecycle-full-rollout-plan.md) (Wave 2),
and the production-copy data survey below.
**Repos:** `cenomi-frontend-2` (deep-link), `cenomi-api-2` (contract field +
lifecycle), `cenomi-api-v2` (agent already implements the handoff; inbox
service if Workstream C is approved).

---

## 1. What the data says (production copy, measured)

| Key | Rows | Read | Targeting | Meaning |
|---|---|---|---|---|
| `SALES_DATA_PENDING` | **6,378,955** | 418k | role-targeted, `target_type=TENANT` | tenant: upload month X |
| `SALES_DATA_SUBMITTED` | 4,324 | 366 | role 30 (`SALES_DATA_TEAM`) + perms, `target_type=ADMIN`, has `lease_id`+`group_id` | admin: review upload |
| `SALES_DATA_APPROVED` | 17,694 | 55 | user-targeted, `TENANT` | informational — **wrongly flagged `action_required=true`** |

Payload facts that shape everything below:

- `text_content.id` is the **lease code** (`t00xxxxx`), not a numeric id.
- The **period exists only inside the EN/AR sentence** ("as of month August") —
  the guide confirms this (§8.1 "Not stored"). Every consumer (deep-link,
  lifecycle close-out) must either parse text or get a contract field.
- `NEED_REUPLOAD` / supporting / audited keys exist in the writer
  (`notifyUserOnSalesDataUpdate`) but have near-zero volume on this copy.
- The close-out anchor is clean: `cenomi.sales_data` carries `lease_id`,
  `lease_code`, `sales_period_start/end`, `status`, `approved_at` — but only
  **280k** rows vs 6.4M notifications: the monthly re-nag fan-out means most
  PENDING notifications are duplicates of the same lease+month ask.

---

## 2. Scope — three workstreams

| | Workstream | Guide basis | Decision needed? |
|---|---|---|---|
| **A** | Bell/modal click → copilot with `notification_context` | §8, verbatim | no — this is the guide's mandate |
| **B** | Structured period on new notifications | §8.1 gap | small — additive contract field |
| **C** | Sales-data lifecycle (auto-clear + archival, Wave 2) | our rollout plan | yes — scope & rules below |
| — | Copilot "Needs your action" tab for sales data | not in guide | **deferred** — see §6 |

Out of scope here (owned by the agent cutover, guide §4–§7): RBAC role
mapping, `SALES_DATA_API_BASE_URL` cutover, chat UI cards, CSV cache
hardening.

---

## 3. Workstream A — bell click opens the agent (frontend)

Per guide §8.2–8.6. All in `cenomi-frontend-2`.

1. **`openCopilotInSalesDataMode(seed)`** in `serviceRequestAgenticRegistry.ts`,
   modelled on `openCopilotInSrMode()`; `AIChatWidget` listens and, once the
   AI token is ready, fires **one** seeded first turn:

   ```json
   { "message": "I need to submit sales data from my notification.",
     "corrected_fields": { "notification_context": {
       "lease_code": "<text_content.id>",
       "period_hint": "month August",
       "source": "notification" } } }
   ```

2. **`handleNotificationNavigation`** (`Util.ts`) `SALES_DATA` branch: when
   `NEXT_PUBLIC_AI_API_URL` is configured → open copilot (optionally still
   route to `/dashboard/salesdata` for split view); otherwise keep today's
   listing navigation as the fallback. Requires threading `text_content.en`
   (or a pre-parsed `periodHint`) into the handler — today's signature lacks
   the body.

3. **`extractSalesDataPeriodHint(en)`** — the guide's regex starter
   (`as of\s+(month\s+)?([A-Za-z]+(?:\s+\d{4})?)`) as a tested helper. Parse
   failure is fine: send `lease_code` alone and the agent lists PENDING
   months. When Workstream B ships, prefer the structured field over parsing.

4. **Key gating (phase 1):** deep-link only tenant keys — `SALES_DATA_PENDING`
   and the `NEED_*` family. Admin `SALES_DATA_SUBMITTED` keeps listing
   navigation (guide §8.6); `SALES_DATA_APPROVED` is informational and never
   deep-links. Mirror the pilot's lesson: gate on
   `notification_type === 'SALES_DATA'` + key, never on display text.

5. **Reuse, don't fork:** the AI-token mint, `sr-chatbot` session plumbing and
   the expiry-refresh fix from TC-3721 all apply as-is. The stale case is
   already handled agent-side (no PENDING row → stale copy, guide §8.4.4) —
   staging rows 11–12 of the guide's matrix verify it.

6. **Tests:** unit specs for the period parser and the gating helper (same
   shape as `notificationTypes.spec.ts` from the pilot); i18n keys for any
   new chips.

---

## 4. Workstream B — structured period on new notifications (platform)

One additive change in `cenomi-api-2`'s sales-data notification writers
(`notifyUserOnSalesDataUpdate` + the sync cron): include the period in
`text_content` —

```json
{ "key": "SALES_DATA_PENDING", "id": "t0029135",
  "sales_period_start": "2026-08-01", "sales_period_end": "2026-08-31", ... }
```

and derive the entity columns at insert (the mechanism from TC-3721 —
`deriveNotificationEntity` already runs on every write path):
`entity_type='LEASE'`, `entity_id` resolved from lease code
(`SALES_DATA_SUBMITTED` rows already carry `lease_id` as a column).

Why both consumers want it:

- The FE deep-link sends exact `period_hint` without regex-parsing prose —
  and keeps working if copy/translation changes.
- The lifecycle close-out (Workstream C) needs lease+period as **data**; on
  legacy rows it must parse text once, but every new row is exact.

Display payloads keep the sentence; the fields are additive — no consumer
breaks. This is the same "stop treating `text_content` as a contract" fix
that TC-3520 made for SR notifications.

---

## 5. Workstream C — sales-data notification lifecycle (Wave 2)

Extends TC-3721's mechanism (shipped, pilot-scoped). Sales data has **no
workflow operations**, so it needs its own anchor — the rules:

| Notification | Becomes ACTION_DONE when |
|---|---|
| `SALES_DATA_PENDING` (lease L, period P) | a `sales_data` row for L+P reaches `SUBMITTED` or later, **created/updated at or after the notification** (timing guard, same as handover) |
| `SALES_DATA_PENDING` re-nag | a **newer** PENDING notification exists for the same L+P → older one archived as `SUPERSEDED` — this alone retires the bulk of the 6.4M |
| `SALES_DATA_SUBMITTED` (admin) | that L+P row reaches `APPROVED` / any `NEED_*` (the admin acted) |
| `NEED_*` family | the tenant's corresponding `RE_UPLOADED` / `UPLOADED_*` status lands |
| `SALES_DATA_APPROVED` | **reclassified `NO_ACTION`** (write-site fix + one-line backfill) → archives on read |

Implementation shape (all patterns already exist from the pilot):

1. **Synchronous hook** in the sales-data usecase choke points (POST submit,
   PATCH status change) — same CTE pattern as the SR-create hook: the status
   write and the notification flip are one statement. `notifyUserOnSalesDataUpdate`
   already sits in that transaction, so the hook lands beside it.
2. **Reconciler rules** (drift + legacy): scope predicate extends from
   `sr_sub_category` to a type-aware builder
   (`lifecycleScope()` — planned in the rollout doc §6). Legacy rows parse
   the month from `text_content.en` **once, in the reconciler**, not at read
   time; unparseable rows fall back to the SUPERSEDED rule only.
3. **Scale**: sweeping ~6M rows at 500×200/run takes ~32h of scheduler time —
   acceptable slow burn, or a one-off batched off-hours job reusing the same
   atomic move; `VACUUM (ANALYZE)` after the bulk. (Rollout plan §3.)
4. **Deep-link synergy**: once stale PENDING rows flip, the agent's
   `stale_notification` path becomes rare — users stop clicking asks that
   were already fulfilled, which is the same honesty the pilot bought for
   handover.

---

## 6. Deliberately deferred — sales-data tab in the Copilot inbox

The pilot's Copilot inbox reads the platform's `/notifications/agent/*`
endpoints, which serve **admin** roles by role-id audience. Sales-data
PENDING rows are **tenant**-targeted and scoped by tenant profile — a
sales-data inbox group needs a new platform endpoint with tenant-profile
scoping, chatbot service fan-out, and FE tab work. Real, but independent:
the guide's bell deep-link (Workstream A) delivers the click-to-agent UX
without it. Propose as its own item after A–C prove out. (An admin-side
"Sales review" group for `SALES_DATA_SUBMITTED` — which already carries
role 30 + `lease_id` — would be the cheap first slice if stakeholders want
inbox presence sooner.)

---

## 7. Sequencing, dependencies, decisions

```
A (FE deep-link)  ──────────────►  ships alone; guide §8 mandate
B (period field)  ──────────────►  additive; unblocks exact hints + C's exact rules
C (lifecycle)     ── after B ───►  Wave-2 gate: pilot soak passed (rollout plan §7)
```

**Stakeholder decisions requested:**

1. Approve Workstream A as phase 1 (tenant keys only; admin keeps listing)?
2. Approve the additive `text_content` period fields (B)? Any consumer
   concerns with extra JSON keys?
3. Workstream C scope: full (hooks + SUPERSEDED + reclassify APPROVED +
   legacy backfill) or forward-only (new rows lifecycle, legacy retired by
   SUPERSEDED alone — no text parsing anywhere)? *Recommendation:
   forward-only + SUPERSEDED; the re-nag cadence makes legacy parsing
   unnecessary within one cycle.*
4. Confirm `SALES_DATA_APPROVED` → `NO_ACTION` reclassification (it is a
   confirmation message; 17,694 rows currently pollute "actionable").
5. Defer the Copilot inbox tab (§6) — yes/no?

**Verification:** guide §10 staging matrix rows 9, 11, 12, 14, 15 cover the
notification paths end-to-end; add lifecycle assertions (row 9 → the
SUBMITTED admin notification appears AND the tenant's PENDING for that
month flips; row 14 → SUBMITTED flips on approve). Dry-run evidence on the
production copy before any flip, per the pilot's discipline.

**Effort split:** A = FE only (~small); B = platform, one writer + template
touch (~small); C = platform-heavy (~the pilot's phase-2/3 shape, minus the
already-built infrastructure).
