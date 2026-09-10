# Notification Lifecycle — Full Rollout Plan (post-pilot)

**Status:** planned — execute after pilot soak (see §7 gates)
**Prerequisite reading:** [`notification-lifecycle-and-archival-plan.md`](notification-lifecycle-and-archival-plan.md)
(the mechanism), [`../notification-lifecycle-implementation.md`](../notification-lifecycle-implementation.md)
(what shipped).

## 0. The rollout switch

As of `dc3bcbcb3`, the pilot boundary is **one exported constant**:

```go
// notification/repository/postgres/notification_lifecycle_repository.go
const LifecycleScopeSQL = `n.sr_sub_category IN (
    'HANDOVER', 'FIT_OUT_CONFIRM_FOS', 'FIT_OUT_CONFIRM_END', 'FIT_OUT_CONFIRM_TRADING')`
```

The operation hook, all four reconciler rules and both sweeps concatenate this
same constant, so the boundary cannot drift between statements. For
**SR-workflow categories, rollout = extending this list** plus the per-category
homework in §2. Non-SR types (§3–§5) need new anchors, not just the list.

Everything upstream is already category-agnostic:
- `source_operation_id` is stamped on new SR notifications of **every**
  category today (V2 explicitly, V1 via the repository resolver) — inert
  outside the list, but it means history is already accumulating. The longer
  a category waits, the smaller its legacy backfill.
- `entity_type` / `entity_id` / `sr_sub_category` are derived for every SR
  notification.
- The read filters, archive table, scheduler, and `include_archived` view are
  shared infrastructure.

## 1. The landscape, measured (production copy, 13.5M rows)

| notification_type | total | actionable | read | targeting |
|---|---|---|---|---|
| SERVICE_REQUEST | 6.62M | 489,636 | 1.25M | mixed |
| SALES_DATA | 6.40M | **6,400,973 (100%)** | 418,915 | ~all role-targeted |
| LEASE_DOCUMENT | 318,486 | 309,236 | 23,452 | all user-targeted |
| ANNOUNCEMENT_DETAILS | 77,167 | 0 | 5,116 | user |
| EXPIRING_LEASE | 76,262 | 0 | 2,989 | user |
| AMENDMENT / LETTER / EVENT / ENQUIRY / EXPIRING_DOCUMENT_* | ~3,100 | 0 | — | mixed |

Actionable SERVICE_REQUEST rows by category: **work permit ≈ 445k (91%)**,
then marketing_content (2.3k), inquiry (2.3k), operations checklists (3.7k),
HSE observation (1.6k), fit-out drawings (2.1k), discrepancy (1.1k), company
documents (0.7k). Two spellings coexist (`work permit` / `work_permit`,
`fit_out_drawing` / `fit out drawing`) — `canonicalSubCategory` already
normalises both to one enum form.

## 2. Wave 1 — remaining SR-workflow categories

**Mechanism: identical to the pilot.** These SRs have
`service_request_operation` rows, so the operation hook, the write-time
resolver, and all four reconciler rules apply unchanged.

Order within the wave (small → big, so verification precedes volume):

| Step | Categories | Actionable rows |
|---|---|---|
| 1a | operations checklists, HSE, discrepancy, fit-out drawings, company documents, inquiry, marketing_content | ~14k |
| 1b | **work permit** (all 7 sub-categories) | ~445k |

Per-category homework before its list entry lands:

1. **Key audit** — enumerate `text_content->>'key'` values and find any
   analogue of `FIT_OUT_APPROVED` (a notification whose ask outlives an SR
   status the terminal rule would treat as done). Each becomes a carve-out in
   `queryReconcileLegacyTerminal`.
2. **Terminal-status audit** — work permits may close through statuses the
   pilot never sees (EXPIRED, CANCELLED, CLOSED). Extend the terminal rule's
   status list per category, or key it on a shared "terminal" classification
   rather than a literal IN list.
3. **Dry-run the four reconciler rules** scoped to the candidate category on
   the production copy (rolled back), and eyeball a sample of what closes —
   the same verification the pilot got.
4. **Backfill note:** legacy work-permit rows have NULL `sr_sub_category`
   (only new writes get it). Closing historical rows needs a one-time
   targeted backfill of the routing columns first — same shape as migration
   `1787529601`, sized ~490k rows (~9 min at the measured ~900 rows/s), or
   skip history and let only post-deploy rows carry the lifecycle.

**Decision to take:** whether Wave 1 backfills history or is
forward-only-per-category. Recommendation: forward-only for work permit
(volume, and permits expire naturally), full backfill for the small 1a set.

## 3. Wave 2 — SALES_DATA (the storage prize)

**Half the table.** 6.4M rows, every one `action_required = TRUE` — the flag
is meaningless here and must not be trusted. No workflow operations exist, so
the pilot's hooks don't apply; this wave needs its own anchor.

- **Ask:** "upload sales for lease L, period P". **Done:** a corresponding
  `sales_data` record exists / is approved for that lease + period.
- **Plumbing:** derive `entity_type='LEASE'`, `entity_id=lease_id` at write
  time (already possible — rows carry `lease_id`), and record the **period**
  the ask refers to. The period lives in the notification text today; a
  reliable rule needs it as data → either a `text_content` contract field or
  reuse of an existing column. *Investigation task, not yet designed.*
- **Close-out hook:** on sales-data upload/approval (single usecase choke
  point in `sales_data/usecase`), flip matching notifications — the
  lease-activation hook pattern, including its timing guard analogue (an old
  upload must not close a new period's ask).
- **Reminder chains:** the scheduler re-nags monthly. A newer ask for the
  same lease+period supersedes the older one → close the older with reason
  `SUPERSEDED`. This alone likely retires the majority of the 6.4M.
- **Sweep at this volume:** 500-row batches × 200/run moves 100k per
  half-hour — ~32 hours of scheduler time for a full historical sweep.
  Acceptable as a slow burn; if not, a one-off batched off-hours job using
  the same atomic move. Plan a `VACUUM (ANALYZE)` after the bulk moves —
  deleting ~6M rows leaves dead space the autovacuum will otherwise chase
  for days.

## 4. Wave 3 — LEASE_DOCUMENT

309k actionable, all user-targeted. Ask: upload/renew a lease document.
Done: the document exists / is no longer expiring — anchored on the
document-management tables, closed at the document-upload usecase plus a
reconciler rule. Same SUPERSEDED logic for the `EXPIRING_DOCUMENT_{30,7,3,2,1}`
reminder ladder (a later reminder supersedes every earlier one for the same
document). Design after Wave 2 proves the non-SR anchor pattern.

## 5. Wave 4 — purely informational types

ANNOUNCEMENT, EXPIRING_LEASE, AMENDMENT, LETTER, EVENT, ENQUIRY: zero
actionable rows. Only two rules apply:

- **read → archive** (reason `READ`) — the pilot's read sweep with these
  types added to its scope predicate (the sweep is currently keyed on
  `sr_sub_category`; Wave 4 needs a type-based scope arm instead).
- **expiry → archive** (reason `EXPIRED`) — announcements/events past their
  end date, expiring-lease notices past the lease decision date. Needs the
  end date as data; where it is only in display text, settle for read+age
  (e.g. read OR older than N months).

Low value, near-zero risk — schedule it whenever convenient; it is also the
first wave that shrinks `un_read_notifications_count` noise for tenants.

## 6. Cross-cutting work (applies to every wave)

- **Scope predicate evolution:** `LifecycleScopeSQL` speaks sub-categories.
  Waves 2–4 scope by `notification_type` instead → evolve the constant into a
  small shared builder (`lifecycleScope()` returning the SQL fragment) the
  hook/reconciler/sweeps all call, so the boundary stays single-sourced.
- **Bell-query relief (observed during the pilot):** `GET /notifications`
  bitmap-scans ~212k role-targeted rows per Mall Manager poll, ×3 queries.
  Waves 1–2 archive most of that mass; measure again after Wave 2 and only
  then decide on a dedicated index.
- **Counts:** unread badges will drop sharply per wave — flag to QA so it is
  not reported as data loss. `include_archived=true` covers verification.
- **Monitoring:** the scheduler already logs
  `reconciled_to_done / archived_done / archived_read` per run; watch these
  spike-then-settle per wave.
- **Archive growth:** permanent retention means the archive absorbs ~10M+
  rows by Wave 4. Revisit partitioning (`archived_at` monthly) when it
  crosses ~5 GB — not before.

## 7. Rollout gates

1. **Pilot soak** (current state): 1–2 weeks of manual + production-shadow
   use of handover/fit-out. Exit criteria: no wrongly-closed notification
   reported; scheduler logs clean; bell/AI counts match expectation.
2. Wave 1a → 1 week soak → Wave 1b (work permit).
3. Wave 2 after its period-anchoring design is reviewed (separate spec —
   the §3 investigation tasks).
4. Waves 3–4 in any order after Wave 2's pattern is proven.

Each wave ships as: scope-list/predicate change + per-category carve-outs +
dry-run evidence on the production copy + one soak week. The mechanism itself
does not change again.
