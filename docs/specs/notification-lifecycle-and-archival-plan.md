# Notification lifecycle + archival — design plan

**Status:** DELIVERED 2026-08-31 — all four phases shipped on `cenomi-api-2` `feature/TC-3520-notification-agent` (`1441214ac`, `6c27dd623`, `234755f73`, `b1daf4256`) + FE tab hiding (`56e162cdf`) + chatbot mock parity (`fe67633`)
**Pilot scope:** `HANDOVER` and the fit-out confirmation sub-categories
(`FIT_OUT_CONFIRM_FOS`, `FIT_OUT_CONFIRM_END`, `FIT_OUT_CONFIRM_TRADING`) —
i.e. exactly the notifications the notification agent reads today.
**Repos touched:** `cenomi-api-2` (owns the table and every write path),
`cenomi-api-v2` (agent read path), `cenomi-frontend-2` (tabs + counts).

---

## 1. What exists today

Read before designing, because three of the assumptions behind the original
idea do not hold against the current data.

### 1.1 The table

`cenomi.notifications` — **13.5M rows, 9.5 GB**. Relevant columns:

| Column | Meaning |
|---|---|
| `notification_id` | uuid PK |
| `target_user_id` | set → the row belongs to one person |
| `target_role_id` | set (and `target_user_id` null) → the row is a **shared role queue item** |
| `target_permissions` | text[] — a third targeting mode |
| `read_at` | **one timestamp on the row**, whoever the row serves |
| `action_required` | boolean |
| `group_id` | ties a fan-out together; reading one member marks the whole group read |
| `entity_type` / `entity_id` / `sr_sub_category` | TC-3520 routing columns |
| `text_content` | display payload; `key` carries the event that raised it |

### 1.2 How "read" works

- `PATCH /notifications` → `notificationUsecase.Update` → `SET read_at = now()`
  with a WHERE built from the caller's user/role/permission scope.
- If the row has a `group_id`, `UpdateGroupReadTime` then marks **every row in
  the group** read.
- Frontend calls it from two places, and they disagree:
  - `NotificationDetailModal` marks read on open, unconditionally.
  - the header bell marks read on click **only when `!action_required`** —
    actionable rows are deliberately left unread.
- The chatbot never marks anything read: `notification_entry_node` returns
  `{"type": "notification_action", "action": "mark_read"}` and the UI performs
  the mutation.

### 1.3 What `action_required` actually means

It is **not** a notification taxonomy. In
`servicerequest/usecase/v2/service_request_usecase.go` it is set from the
recipient's relationship to the workflow step:

- assignee of the current `IN_PROGRESS` operation → `action_required = true`
- configured `notify_roles` (observers) → `action_required = false`

Lease activation applies the same idea by hand: `MALL_MANAGER` gets `true`,
RDD PM and Leasing Admin get `false`.

So the field already distinguishes *actor* from *observer*. What it cannot
express is **"the actor has since acted"** — which is the entire feature.

---

## 2. The three findings that shape the design

### Finding 1 — the premise is real and measurable

Actionable notifications in the pilot scope, joined to their service request:

| SR status | actionable notifications | still unread |
|---|---|---|
| REPORT_SUBMITTED | 1,786 | 1,266 |
| APPROVED | 594 | 127 |
| IN_PROCESS | 310 | 41 |
| COMPLETED | 234 | 90 |
| DOCUMENT_UPLOADED | 41 | 0 |
| ACTION_REQUIRED | 40 | 18 |

**828 action-required notifications point at service requests that are already
APPROVED or COMPLETED**, 217 of them still unread. Separately, of 207
lease-activation "create the handover" notifications, **56 already have their
handover SR — and 55 of those are still sitting unread** in a Mall Manager's
queue. (Only 36 of those SRs postdate the notification and genuinely close it —
see the timing guard in §3.4; the other 20 are stale SRs from earlier lease
terms.) The inbox is asking people to do work that is already done.

### Finding 2 — "read → archive" is unsafe on most rows

`read_at` is a single column on a row that is frequently shared by a whole
role:

| Sub-category | rows | role-targeted | user-targeted |
|---|---|---|---|
| HANDOVER | 2,751 | **2,326** | 425 |
| FIT_OUT_CONFIRM_TRADING | 208 | 208 | 0 |
| FIT_OUT_CONFIRM_END | 161 | 161 | 0 |
| FIT_OUT_CONFIRM_FOS | 120 | 120 | 0 |

And a role is not one person:

| Role | active holders |
|---|---|
| OPERATIONS | 176 |
| FM_MANAGER | 73 |
| MALL_MANAGER | 49 |
| DD_ENGINEER | 33 |

So on ~93% of pilot rows, "read" means *somebody* in the role opened it.
Today that already (wrongly) zeroes the unread badge for the other 72 FM
managers. If reading also **moves the row out of the table**, their
notification is gone for good. Archiving on read cannot be applied
indiscriminately.

### Finding 3 — the pilot has almost no read-only notifications

| | rows |
|---|---|
| action-required | 3,238 |
| informational | **2** |

The read → archive rule has essentially nothing to act on inside the pilot.
**The action-taken rule carries the whole feature.** Read-only archival matters
later, for sales-data / document-expiry / lead notifications, not here.

### Also worth knowing

- `text_content.key` is the event that raised the row
  (`FIT_OUT_APPROVED`, `SERVICE_REQUEST_IN_PROGRESS`,
  `FIT_OUT_HANDOVER_REPORT_REQUIRED_DD`, `ACTIVATED_LEASE_NOTIFICATION`, …).
  It is the only existing signal for *which step* a notification belongs to.
- There is a house precedent for archival: `service_request_archive_recent`
  (91–365 days) and `service_request_archive_cold` (>365 days), with the list
  queries falling back to them. That is **age**-based; ours is
  **lifecycle**-based, but the naming and the `LIKE … INCLUDING ALL` pattern
  should match.
- The agent service already drops groups with a zero count
  (`notification_inbox_service.py`), so the "hide empty groups" requirement is
  half-done server-side.

---

## 3. Proposed design

### 3.1 Lifecycle column

Add to `cenomi.notifications`:

```sql
action_state TEXT   -- 'NO_ACTION' | 'ACTION_REQUIRED' | 'ACTION_DONE'
```

Derived at insert time from the existing flag, so no call site changes:
`action_required = false → NO_ACTION`, `true → ACTION_REQUIRED`. `action_required`
stays as-is for the ~40 existing consumers; `action_state` is the new truth.

`ACTION_DONE` is **terminal and transient** — it is the marker the archiver
picks up. That split is the important part of this design:

> **The state change is synchronous. The physical move is asynchronous.**

The moment an SR advances, its stale notifications flip to `ACTION_DONE` and
every read path stops returning them. Whether the rows have physically moved
yet is a storage detail. This means correctness never depends on the archiver
running, and the archiver can be turned off without breaking anything.

### 3.2 The missing link: which step raised this row

Add:

```sql
source_operation_id BIGINT   -- service_request_operation this notification came from
```

Set at write time in the same code that already resolves the recipients from
`IN_PROGRESS` operations. With it, "action done" is exact and generic:

> a notification is done when its `source_operation_id` is no longer `IN_PROGRESS`.

Without it we are forever pattern-matching on `text_content.key`, and a
notification for workflow level 2 is indistinguishable from one for level 4 on
the same SR. **This is the highest-value column in the proposal.** Lease
activation has no operation, so it keeps an explicit rule (below).

### 3.3 Archive table

```sql
CREATE TABLE cenomi.notifications_archive
    (LIKE cenomi.notifications INCLUDING DEFAULTS INCLUDING STORAGE);

ALTER TABLE cenomi.notifications_archive
    ADD COLUMN archived_at     TIMESTAMP NOT NULL DEFAULT now(),
    ADD COLUMN archive_reason  TEXT,   -- ACTION_DONE | READ | SUPERSEDED | EXPIRED
    ADD COLUMN archived_by     BIGINT; -- the user whose action triggered it
```

Indexes: `(target_user_id, archived_at DESC)`, `(target_role_id, archived_at DESC)`,
`(entity_type, entity_id)`, `(sr_sub_category, archived_at DESC)`.

Deliberately **not** partitioned or split warm/cold on day one — the pilot moves
~3k rows. Revisit when it is the whole table.

The move is one atomic statement, never a read-then-write:

```sql
WITH moved AS (
    DELETE FROM cenomi.notifications
     WHERE notification_id = ANY($1)
 RETURNING *
)
INSERT INTO cenomi.notifications_archive
SELECT *, now(), $2, $3 FROM moved;
```

### 3.4 Transition rules

| Notification | Raised when | Becomes ACTION_DONE when |
|---|---|---|
| `ACTIVATED_LEASE_NOTIFICATION` (entity = LEASE) | lease activates; MM must raise the handover | a `HANDOVER` SR exists for that `lease_id` **with `sr.created_at >= notification.created_at`** — an SR from an earlier lease term predates the notification and is not the response to it. Measured: 20 of 56 "SR exists" matches are such stale SRs and must NOT close the notification. At the creation hook the predicate holds trivially (every existing notification predates the just-created SR); the reconciler and backfill must apply it explicitly. SR-targeted rows need no timing guard — they reference their exact SR by `entity_id`. |
| any SR notification with `source_operation_id` | the operation went IN_PROGRESS | that operation leaves IN_PROGRESS |
| SR notification without it (legacy rows) | — | the SR reaches a terminal status (APPROVED / COMPLETED / REJECTED) — **except `FIT_OUT_APPROVED`**, which needs the tenant-acceptance step recorded (§6.2): an APPROVED SR with an unaccepted report is still actionable |
| `NO_ACTION`, **user-targeted** | informational | `read_at` set → archive with reason `READ` |
| `NO_ACTION`, **role-targeted** | informational | `read_at` set → archive with reason `READ` (shared-queue, §6.3) |

Hook points, all in `cenomi-api-2`:

1. SR operation transition — the code that already writes the *next* level's
   notifications also closes out the previous level's.
2. Handover SR creation — closes the lease-activation notification for that lease.
3. `notificationUsecase.Update` (mark read) — `NO_ACTION` rows, user- and
   role-targeted alike (shared-queue, §6.3).
4. A reconciler job — catches drift and does the one-time backfill (§4).

The reconciler is not optional. Notifications are written from several places
including background jobs, and any missed hook silently resurrects the current
problem.

### 3.5 The shared-read problem — resolved: shared-queue

Decision (§6.3): keep the single `read_at` column with its existing
whole-role semantics. A role queue is a team inbox — one holder reading a row
hides it for the whole role. Archival of role-targeted rows still triggers on
**action** for `ACTION_REQUIRED` rows (action is a globally true fact); for
`NO_ACTION` role-targeted rows, read → archive now applies too, same as
user-targeted ones. Per-user read receipts are out of scope; revisit only if
shared-queue behaviour is reported as a problem in practice.

### 3.6 Hiding read notifications from the agent

`AND read_at IS NULL AND action_state <> 'ACTION_DONE'` in
`buildAgentSubCategoryFilters`. Shared-queue semantics apply (§6.3): once any
holder of the role opens a row, it stops appearing in the agent inbox for
everyone in that role.

### 3.7 Hiding empty groups in the agent view

- Backend already omits zero-count groups.
- The Copilot tabs are derived from **roles**, not counts
  (`notificationTabsForRoles`), so a tab can still render empty. Fix: intersect
  the role-eligible tabs with the counts from the single unfiltered fetch —
  drop zero-count tabs, drop "All" when one tab remains (existing rule), hide
  the whole section at zero. Same for `NOTIFICATION_CHIPS`.

---

## 4. Backfill — delivered by the reconciler, not a migration

As built, there is no separate backfill migration: the phase-3 reconciler's
rules ARE the backfill rules (terminal-status with the `FIT_OUT_APPROVED`
carve-out; lease rule with the timing guard), so its **first scheduled run**
flips the stale rows and the sweep archives them. Dry-run against the
production copy confirmed the exact predicted counts: **36** lease
activations (the 20 stale-SR matches correctly left open) + **828** legacy
terminal rows, in ~1.2s. One implementation instead of two to keep in sync.

---

## 5. Phasing

| Phase | Change | Risk |
|---|---|---|
| 1 | `action_state`, `source_operation_id`, archive table. Write the new columns. **Nothing moves, nothing is filtered.** | none — additive |
| 2 | Set `ACTION_DONE` at the hooks. Every exposing API filters `action_state <> 'ACTION_DONE'`; agent adds `read_at IS NULL`. Empty-group/tab hiding. | low — **the user-visible feature is fully delivered here, with zero rows moved** |
| 3 | New scheduler in `cenomi-api-2`: sweep `ACTION_DONE` + read `NO_ACTION` rows into the archive; drift reconcile. | low — pure storage; reversible |
| 4 | `include_archived=true` on `GET /notifications`. The planned backfill migration was **dropped as redundant**: the phase-3 reconciler applies the same rules (carve-out and timing guard included) on its first run and the sweep moves the 36 + 828 rows — one implementation instead of two to keep in sync. | low — idempotent |

Phases 1–2 are worth doing on their own even if 3 never ships.

---

## 6. Decisions taken (2026-08-31)

1. **Notification history → `include_archived=true`.** `GET /notifications`
   grows an optional flag that unions `notifications_archive` into the list
   (default `false`, so existing callers see no change). Archived rows carry
   `archived_at` / `archive_reason` in the response so the UI can render them
   as history rather than as pending work.
2. **`FIT_OUT_APPROVED` stays actionable until the tenant accepts.** The RDD PM
   approving is what *raises* it; the pending action belongs to the tenant.
   It becomes `ACTION_DONE` only when the tenant's acceptance is recorded
   (its operation leaves `IN_PROGRESS` via the tenant-acceptance step — not
   merely when the SR reaches a terminal status). The legacy fallback rule in
   §3.4 must therefore exclude this key from the "terminal SR status" shortcut:
   an APPROVED SR with an unaccepted handover report is still actionable.
3. **Read-hiding: shared-queue.** Keep the single `read_at` column and its
   existing semantics — any one holder of the role reading it hides the row for
   the whole role, in both the agent view and (on archival of `NO_ACTION`
   rows) the bell. Per-user read receipts (§3.5 phase 5) are dropped from this
   plan; revisit only if the shared-queue behaviour is ever reported as a
   problem.
4. **Retention: the archive is permanent.** Nothing ever leaves
   `notifications_archive`. No TTL, no cold tier for now — revisit only on
   size grounds.
5. **Reconciler: a new scheduler in `cenomi-api-2`.** Its job is the
   *physical* sweep (move `ACTION_DONE` rows into the archive) plus drift
   catch-up. Correctness never waits for it: the `action_state` column is
   written synchronously at the hooks, and **every notification-exposing API
   (`GET /notifications`, the five agent endpoints) filters
   `action_state <> 'ACTION_DONE'`** — so a row is invisible from the moment
   the action is taken, whether or not the sweep has run.

## 7. Where the work lands

**`cenomi-api-2`** (the bulk)
- migrations: `action_state`, `source_operation_id`, `notifications_archive`
- `notification_entity.go` — derive `action_state` alongside the existing columns
- `notification_repository.go` — `ArchiveNotifications`, `MarkActionDone`;
  agent filter gains `action_state` + `read_at IS NULL`
- `servicerequest/usecase/v2` — stamp `source_operation_id`; close out prior
  operations on transition
- `lease/usecase` — close the activation notification when the handover SR is raised
- new archival scheduler (sweep + drift reconcile) + backfill migration
- `GET /notifications?include_archived=true` — unions the archive, rows tagged
  with `archived_at` / `archive_reason`

**`cenomi-api-v2`**
- nothing functional if the platform filters correctly; add `action_state` to
  the row payload so the agent can explain *why* something vanished, and cover
  the new filters in the eval tier

**`cenomi-frontend-2`**
- count-driven tabs/chips (§3.7); optional "Action done" styling if archived
  rows ever become visible
