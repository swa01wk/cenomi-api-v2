# Notification Lifecycle & Archival — Implementation Report

**Status:** all four phases delivered, 2026-08-31
**Design:** [`docs/specs/notification-lifecycle-and-archival-plan.md`](specs/notification-lifecycle-and-archival-plan.md)
**Changelog:** `cenomi-api-2/docs/CHANGE_LOG_NOTIFICATION_AGENT.md` (2026-08-31 entry)

| Phase | Commit(s) | Repo |
|---|---|---|
| 1 — Lifecycle columns + archive table | `1441214ac` | `cenomi-api-2` |
| 2 — Synchronous hooks + read filters | `6c27dd623` · `56e162cdf` (FE tabs) · `fe67633` (chatbot mock) | all three |
| 3 — Archival scheduler + reconciler | `234755f73` | `cenomi-api-2` |
| 4 — History view (`include_archived`) | `b1daf4256` | `cenomi-api-2` |

---

## 1. Scope — what is and is not covered

**The pilot covers exactly the notifications the AI assistant reads: the
FIT_OUT_AND_HANDOVER family.** That is `HANDOVER` **plus the three fit-out
confirmations** — `FIT_OUT_CONFIRM_FOS`, `FIT_OUT_CONFIRM_END`,
`FIT_OUT_CONFIRM_TRADING` — and the lease-activation notification that starts
the handover flow. It is *not* handover-only, and it is *not* everything:

| Behaviour | Scope |
|---|---|
| **Physical archival** (rows moved to `notifications_archive`) | **Pilot only** — every sweep statement carries `sr_sub_category IS NOT NULL`, which matches only the four sub-categories above. Sales-data, document-expiry, lead and other notifications are never touched. |
| **Reconciler** (lease rule, legacy terminal rule) | **Pilot only** — same guard. |
| Lease-activation close-out (handover SR created → notification done) | Handover by nature — `ACTIVATED_LEASE_NOTIFICATION` only. |
| Operation-completion hook (step done → its notifications flip) | **Pilot only** — the hook (and the reconciler's drift rule) carry the same `sr_sub_category IS NOT NULL` guard as the sweeps. `source_operation_id` is still stamped on new SR notifications of every category (V2 explicitly; V1 via the repository resolver matching target role/user against the SR's IN_PROGRESS operations), but outside the pilot the stamp is inert routing data: nothing is flipped, hidden, or archived until a category joins the rollout. |
| Read filters on `GET /notifications` | Table-wide by construction, but only rows that ever reach `ACTION_DONE` are affected — and only pilot rows ever do. Every other category stays fully visible. |

Legacy rows of other categories (13.5M-row table) are never flipped, never
hidden, never moved. Extending the pilot later = widening the
`sr_sub_category` guard, nothing more.

---

## 2. The problem, measured

`action_required` said *"this recipient owes an action"* but nothing could ever
say *"the action has since been taken."* Against the production copy:

- **828** action-required notifications pointed at service requests already
  APPROVED or COMPLETED (217 still unread).
- Of 207 lease-activation *"create the handover"* notifications, **56** already
  had their handover SR — **55 of them still unread** in Mall Manager queues.
- The pilot held 3,238 actionable rows and only **2** informational ones — so
  the action-taken rule, not the read rule, carries the feature.
- 93% of pilot rows are **role-targeted** (one row serves e.g. all 73 FM
  Managers), which is why "archive on read" alone would have destroyed
  notifications for entire teams.

---

## 3. The model

Two columns on `cenomi.notifications`:

```
action_state          NO_ACTION | ACTION_REQUIRED | ACTION_DONE
source_operation_id   the service_request_operation that raised the row
```

- `action_state` is derived from `action_required` at insert time — no write
  site changed. `ACTION_DONE` is set **only** by the lifecycle hooks, never at
  insert.
- `source_operation_id` makes "done" exact: *a notification is done when its
  operation leaves IN_PROGRESS.* Without it, a level-2 and a level-4
  notification on the same SR are indistinguishable.

The core invariant:

> **The state change is synchronous. The physical move is asynchronous.**

The instant an action is taken, the row flips to `ACTION_DONE` inside the same
SQL statement as the action itself, and every exposing API filters
`action_state IS DISTINCT FROM 'ACTION_DONE'` — so the row vanishes
immediately. Whether the archiver has physically moved it yet is a storage
detail. The scheduler is a safety net, not a dependency: switch it off and
nothing is wrong, storage just lags.

### Lifecycle transitions

| Notification | Becomes ACTION_DONE when |
|---|---|
| Lease activation (*"create the handover"*) | a **non-DRAFT** `HANDOVER` SR exists for that lease, **created at or after the notification** — the timing guard. An SR from an earlier lease term predates the notification and is not the response to it; 20 of 56 "SR exists" matches were exactly that and stay open. A DRAFT is action *started*, not taken; the reconciler closes it when the draft is submitted. |
| SR notification with `source_operation_id` | that operation leaves IN_PROGRESS |
| Legacy SR notification (no operation link) | the SR reaches APPROVED / COMPLETED / REJECTED — **except `FIT_OUT_APPROVED`**, which asks the *tenant* to accept the handover report: RDD approval raises it, so only COMPLETED / REJECTED closes it |
| Informational (`NO_ACTION`) rows | `read_at` set → archived with reason `READ`. Shared-queue by decision: one role-holder reading hides/archives it for the whole role. |

---

## 4. What each phase implemented

### Phase 1 — columns and archive table (`cenomi-api-2`)

- Migration `1787616200`: the two columns, a partial index for the sweep's
  work queue (`WHERE action_state='ACTION_DONE'`), a partial index for
  close-out lookups (`WHERE source_operation_id IS NOT NULL`), and a closing
  `ANALYZE` (without fresh stats the planner seq-scanned 13.5M rows: measured
  ~8.5 s vs ~1.5 ms).
- Migration `1787616201`: `notifications_archive` = `LIKE notifications` +
  `archived_at`, `archive_reason` (`ACTION_DONE` | `READ`), `archived_by`.
  **Retention is permanent** — nothing ever leaves it.
- `deriveNotificationLifecycle()` fills both columns on all three write paths
  (`Store`, `UpdateNotification`, `BulkInsertNotification`) with caller-set
  values winning — the same pattern as the TC-3520 entity columns.
- `ActiveOperationRecipients` gained `UserSourceOps` / `RoleSourceOps` so both
  V2 notify paths stamp each recipient with the operation that put it in the
  set (contact-resolved users inherit their role's operation; first-seen wins).
- **V1 coverage** (`1c71b8742`): the V1 usecase — which carries most
  FIT_OUT_AND_HANDOVER traffic across ~60 notification write sites — never
  passes the operation. The repository therefore resolves it at insert time:
  an action-required SERVICE_REQUEST row targeted at exactly the role (or
  user) an IN_PROGRESS operation of that SR is assigned to belongs to that
  operation. Explicit stamps (V2) win; an ambiguous match stamps nothing —
  a wrong link would close a pending notification, a missing one only falls
  back to the reconciler's terminal rule. The lookup runs on the caller's
  transaction so a step opened moments earlier is visible. The reconciler's
  operation rule is NOT EXISTS, so an operation V1 *deletes* (some re-flow
  paths) counts as finished too.

### Phase 2 — hooks and filters (all three repos)

Two repo-level hooks, each a CTE so the action and the close-out are **one
statement, one transaction**, and no caller can bypass them:

- `queryUpdateOperationStatus` (the single choke point for operation status —
  v1 and v2 usecases both funnel through it): status update + flip of every
  notification carrying that operation id.
- `queryCreateServiceRequest` (the single INSERT all create paths use —
  portal, chatbot, schedulers): creating a non-DRAFT HANDOVER SR closes the
  lease's activation notifications. The timing guard holds trivially here —
  every existing notification predates the SR being created this instant.

Read-side:

- `GET /notifications` list + both counts filter out `ACTION_DONE` (NULL on
  pre-migration rows passes, so legacy data is unaffected).
- The five agent endpoints briefly filtered `read_at IS NULL` too; this was
  **reverted the same day**: every agent row is action-required, and reading
  one does not discharge the action — the filter made "Needs your action"
  drop a live lease activation the moment it was opened, while the bell kept
  it. Read now drives only the unread badge (`unread=true` stays available
  as an explicit filter); rows leave the agent inbox on `ACTION_DONE`.
- The mark-read UPDATE deliberately keeps an unfiltered WHERE: filtering it
  would turn a complete-vs-click race into HTTP 403.
- Frontend (`56e162cdf`): Copilot inbox tabs hide when their groups hold
  nothing actionable — a tab that can only say "caught up" is noise. "All"
  survives only while two or more group tabs do; an open tab that empties
  moves focus to the first survivor.
- Chatbot (`fe67633`): the mock inbox mirrors the read-hiding so local dev and
  evals are never more generous than production.

### Phase 3 — scheduler and reconciler (`cenomi-api-2`)

`NotificationLifecycleArchival` — runs at startup, then every 30 minutes:

1. **Reconcile** drift the hooks can miss (out-of-band DB edits, drafts
   submitted later, pre-hook history): the three lifecycle rules above,
   implemented as three UPDATEs. The operation rule uses EXISTS over the
   partial index rather than a join (~1.5 ms vs ~8.5 s, measured).
2. **Sweep**: atomic `DELETE … RETURNING` → `INSERT` into the archive, 500
   rows per batch, `FOR UPDATE SKIP LOCKED`, max 200 batches per run.
   Deliberately **no ON CONFLICT** — a duplicate archive id is a bug that must
   fail loudly, not a row to drop silently.
3. A reconcile failure still sweeps whatever is already marked; a failure in
   one sweep lane doesn't stop the other (unit-tested).

### Phase 4 — history view (`cenomi-api-2`)

`GET /notifications?include_archived=true`:

- Unions the archive into the list and `total_count` (same WHERE clause, both
  branches, merged newest-first) — page ranges stay honest.
- Archived rows carry `archived_at` / `archive_reason` so a UI can render them
  as history rather than pending work.
- Also shows done-but-unswept rows, so history is complete regardless of
  scheduler lag.
- Absent/`false` is byte-identical to the old response; the unread badge stays
  live-only.

The plan's separate backfill migration was **dropped as redundant**: the
reconciler's rules *are* the backfill rules, so its first run flips the stale
rows (dry-run confirmed the exact predicted counts — **36** lease activations
+ **828** legacy terminal) and the sweep archives them. One implementation
instead of two to keep in sync.

---

## 5. How it works at runtime — three walkthroughs

**A. A handover step completes.**
FM Manager approves their level → the platform updates that operation's status
→ *the same SQL statement* flips every notification that operation raised to
`ACTION_DONE` → the rows disappear instantly from the bell, `GET
/notifications`, and all five agent endpoints → within ≤30 min the scheduler
moves them to `notifications_archive` with reason `ACTION_DONE`. Meanwhile the
*next* level's notifications are being written with their own
`source_operation_id`, waiting for their own step to complete.

**B. A lease activates.**
The activation notification asks the Mall Manager to raise the handover SR.
The moment a non-DRAFT `HANDOVER` SR is created for that lease — from the
portal, the chatbot, anywhere — the create statement itself closes the
notification. An old handover SR from a previous lease term does **not** close
it (timing guard). If the MM saves a draft first, the notification stays until
the draft is submitted; the reconciler closes it then.

**C. Someone reads an informational row.**
A `NO_ACTION` row (observer / notify_roles) is marked read → next sweep moves
it to the archive with reason `READ`. Role-targeted rows follow shared-queue
semantics: one holder reading it resolves it for the whole role — the same
semantics the existing single `read_at` column already had.

In every case the row is retrievable afterwards via
`include_archived=true`, forever.

---

## 6. First deploy behaviour

On the first startup after deploy (migrations `1787616200` + `1787616201`
applied, in that order):

1. The scheduler's first run reconciles: ~36 lease-activation + ~828 legacy
   terminal rows flip to `ACTION_DONE` (~1.2 s, measured on the production
   copy).
2. The sweep archives them plus any read informational rows — a few batches,
   seconds.
3. From then on the hooks handle everything at action time and the scheduler
   is pure drift-catching and storage reclaim.

Every mechanism was proven against the production-copy DB in rolled-back
transactions before commit: hook flips, DRAFT non-flip, timing-guard counts,
sweep atomicity and reasons, archive-union list and count.

---

## 7. Out of scope / future

- **Other notification categories** (sales data, documents, leads…): new SR
  workflow rows get `source_operation_id` stamps but are never flipped,
  hidden, or archived. Rollout = widen the `sr_sub_category` guard shared by
  the hook, reconciler and sweeps.
- **Per-user read receipts**: dropped by decision — shared-queue `read_at`
  stands. Revisit only if the whole-role behaviour is reported as a problem.
- **History UI**: the API flag exists; no frontend consumes it yet.
- **Archive partitioning / cold tier**: not needed at pilot volume (~3k rows);
  revisit on size only.
