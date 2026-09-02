# Frontend plan — Sales Data Agent + Notification Agent

**Share this file with `cenomi-frontend`.** It is the implementation brief for Tenant Central / Admin copilot. Backend and AI work is already in this repo; official UI is not.

| | |
|---|---|
| **Audience** | Frontend (Tenant Central + Admin portal) |
| **Repo to change** | `cenomi-frontend` |
| **Do not ship** | `cenomi-api-v2/service-request-chatbot/sales-data-agent/` (port 3001) — copy cards and action wiring from it, restyle to Cenomi chat CSS |
| **Full cutover / backend** | [`sales-data-agent-integration-guide.md`](./sales-data-agent-integration-guide.md) |
| **Product behaviour** | [`sales_data_agent_PRD.md`](./sales_data_agent_PRD.md) |
| **Handover Notification Agent (same graph)** | [`../../docs/specs/3520-notification-agent-integration.md`](../../docs/specs/3520-notification-agent-integration.md) |

---

## Status (read this first)

| Layer | Status | What frontend should assume |
|---|---|---|
| Sales Data Agent (LangGraph) | **Done** | Same `POST /api/v2/chat/service-request` as RDD/handover |
| Notification Agent list + Open | **Done** | Send `active_notification_id`; graph routes sales rows to the Sales Data Agent |
| `cenomi-api` inbox tool `GET /notifications/agent/sales-data` | **Done** | AI calls this; frontend never calls it directly |
| `cenomi-api` notification **create** | **Already existed** | Do not add a notification writer in FE or AI |
| Official `cenomi-frontend` | **Not started** | This document is the remaining work |

Frontend does **not**:

- Call `cenomi-api` GET/POST/PATCH `/sales-data` from the copilot (the AI does that with the user’s `ai_token`)
- Create or update notification rows
- Map JWT roles (`BILLING_CONTACT` → `TENANT_CSV_UPLOADER`). The AI backend already accepts **both** demo aliases and live platform names
- Mint a second token. Reuse the existing RDD mint: `POST /auth/ai-session-token` → `sessionStorage` key `sr-chatbot-ai-session-token`

---

## Architecture (frontend hops only)

```text
Bell / inbox card  ──Open──►  FAB / Copilot
                                  │
                                  │ 1. portal cookie → POST /auth/ai-session-token  (already wired)
                                  │ 2. POST /api/v2/chat/service-request
                                  │      Authorization: Bearer <ai_token>
                                  │      active_notification_id  OR  typed message / action
                                  │
                                  ▼
                         Help Agent graph
                    ┌─────────────┴─────────────┐
                    │                           │
           Notification Agent          Sales Data Agent
           list / open / explain       CSV, typed entry, review,
                                       supporting doc, admin review
```

CSV bytes go to the **AI**, not to `cenomi-api`:

```text
POST {NEXT_PUBLIC_AI_API_URL}/api/v2/sales-data/upload
POST {NEXT_PUBLIC_AI_API_URL}/api/v2/sales-data/supporting-doc
```

Then a chat turn with `action: csv_uploaded` / `doc_uploaded` and `corrected_fields.csv_session_id` / `doc_session_id`.

---

## Two IDs (easy to get wrong)

Sales-data notifications carry **two** identifiers. Mixing them sends the user into handover `sr_status_sync` (“couldn’t find SR”).

| Field | What it is | Send as Open `id`? |
|---|---|---|
| Notification row UUID (`notification_id` / bell `id`) | One bell row | **Never** |
| `text_content.id` / inbox `lease_code` | PMS tenant / lease code, e.g. `t0091324` | **Yes** — this is `active_notification_id.id` |
| Inbox `sr_id` | Always `null` on sales rows | Do not use |
| Handover Open `id` | Service request number | Only when `kind === "service_request"` |

Bell listing today already puts the lease code in `inquiryId` for `SALES_DATA` (`handleNotificationNavigation` → `/dashboard/salesdata?...&lease_code={inquiryId}`). Keep using that value as Open `id`.

---

## Work tickets (do in this order)

### FE-1 — Types and chat payload

**Files**

- `libs/api/src/lib/srChatbot/srChatbotApi.ts`

**Do**

1. Add UI types from Appendix A (at least the ones in the first milestone). Unknown `ui.type` currently renders **nothing** in `SrChatUiRenderer.tsx` (markdown still shows).
2. Add chat `action` union members from Appendix B.
3. Add to `ServiceRequestChatTurnRequest`:

```ts
active_notification_id?: {
  id: string;
  sub_category?: string;
  key?: string;
  message?: string;
  period_hint?: string;
  notification_type?: string;
} | null;
```

4. **Put it on the wire.** `postServiceRequestChatTurn` today builds a fixed payload and **drops unknown fields**. If you only add the TypeScript field, Open still never reaches the AI.

```ts
if (body.active_notification_id) {
  payload.active_notification_id = body.active_notification_id;
}
```

5. Keep `corrected_fields` as `Record<string, unknown>` (`csv_session_id`, `doc_session_id`, `rows`, `year_month`, `lease_code`). Do **not** put `notification_context` here for Open — that is the Notification Agent’s job.

Empty `message` is valid when `active_notification_id` (or `sr_id`) is set.

---

### FE-2 — `openNotification` next to `openExistingSr`

**Files**

- `libs/ui-web/src/lib/components/serviceRequestChatbot/useServiceRequestChat.ts`
- `libs/ui-web/src/lib/components/serviceRequestChatbot/serviceRequestAgenticRegistry.ts`
- `libs/ui-web/src/lib/components/AIChatWidget/AIChatWidget.tsx` (`cenomi-fab:open-sr` listener)

Handover already has `openExistingSr(srId)`: new session, `message: ''`, `sr_id`. Add the sales/handover Open twin:

```ts
await openNotification({
  id: leaseCode,                 // text_content.id — not the row UUID
  sub_category: 'SALES_DATA',
  key: 'SALES_DATA_PENDING',     // template key
  message: englishBody,          // text_content.en — backend parses the month
  notification_type: 'SALES_DATA',
});
```

Rules:

- Force a **new** session (`session_id: null` / `forceNewSession: true`).
- Wait until `ai_token` is ready (same as RDD).
- Do not wait for the user to type.
- For `kind === "sales_data"`, `id` is `lease_code`. For `kind === "service_request"`, keep using `sr_id` (handover). Do not reuse `openExistingSr` for sales rows.

Registry helper (mirror `openCopilotInSrMode`):

```ts
openCopilotForNotification(ref)
// setFabOpen(true) + dispatch CustomEvent('cenomi-fab:open-sr', { detail: { activeNotificationId: ref } })
```

Optional: when `state.intent` is `UPLOAD_SALES_DATA` / `ENTER_SALES_DATA_MANUAL` / `REVIEW_SALES_DATA`, `router.push('/dashboard/salesdata')` so listing + copilot sit together. The listing page stays the classic UI.

---

### FE-3 — Bell Open (`handleNotificationNavigation`)

**File:** `libs/ui-web/src/lib/utils/Util.ts` (~line 2800)

**Today** (`NotificationTypeEnum.SALES_DATA`): listing only

```text
/dashboard/salesdata?page=1&limit=10&lease_code={inquiryId}&section=…&from_notification=true
```

**Target:** if `NEXT_PUBLIC_AI_API_URL` is set, also open the copilot via FE-2. Keep listing as fallback when the AI URL is unset.

You must thread **`text_content.en`** and **`text_content.key`** into this helper. Today callers pass `inquiryId` / `category` / `key`; confirm `key` is the template (`SALES_DATA_PENDING`, …) and that the English body is available. Without `message` or `key`, Open still works (agent lists PENDING months) but the first card is less specific.

Suggested branch (keep listing `from_notification=true` so date filters still clear):

```ts
} else if (NotificationTypeEnum.SALES_DATA === type) {
  const leaseCode = inquiryId; // text_content.id
  if (process.env.NEXT_PUBLIC_AI_API_URL?.trim()) {
    openCopilotForNotification({
      id: leaseCode,
      sub_category: 'SALES_DATA',
      key: textContentKey,
      message: englishBody,
      notification_type: 'SALES_DATA',
    });
  }
  const baseUrl = destination.format(inquiryId, setSalesDataSection(category));
  const sep = baseUrl.includes('?') ? '&' : '?';
  router.push(`${baseUrl}${sep}from_notification=true`);
}
```

**Admin `SALES_DATA_SUBMITTED`:** Open with `key: 'SALES_DATA_SUBMITTED'` → agent intent `REVIEW_SALES_DATA`. Phase 1 may keep listing-only for admin submit notifications if you only deep-link tenant PENDING / NEED_*.

---

### FE-4 — Copilot inbox cards (`notification_list`)

Same Help Agent graph as handover (TC-3520). If `notification_list` is not rendered yet, sales rows will look like markdown only.

On Open from a list card:

| `row.kind` | `active_notification_id.id` |
|---|---|
| `sales_data` | `row.lease_code` |
| `service_request` | `row.sr_id` |

Passive panel (no chat turn, same row shape):

```text
GET {NEXT_PUBLIC_AI_API_URL}/api/v1/notifications/inbox?sub_category=SALES_DATA
Authorization: Bearer <ai_token>
```

Sales row contract (AI already serializes this):

```json
{
  "kind": "sales_data",
  "notification_id": "<uuid — display only>",
  "sr_id": null,
  "lease_code": "t0091324",
  "sub_category": "SALES_DATA",
  "key": "SALES_DATA_PENDING",
  "message": "Please submit sales data as of month June for …",
  "action_required": true
}
```

---

### FE-5 — Render sales-data cards in `SrChatUiRenderer`

**File:** `libs/ui-web/src/lib/components/serviceRequestChatbot/SrChatUiRenderer.tsx`

Port from `cenomi-api-v2/service-request-chatbot/sales-data-agent/components/chatbot/` (behaviour, not Next styling). Switch on `ui.type`:

| `ui.type` | Port from | User action to emit |
|---|---|---|
| `sales_data_upload_prompt` | dropzone + template button | CSV → upload API then `csv_uploaded` |
| `pending_months_list` | chips per lease/month | typed month **or** CSV drop |
| `other_months_reminder` | proceed / switch chips (max 4) | `proceed_other_months` / `switch_pending_month` |
| `period_mismatch_warning` | two CTAs | `confirm_period_mismatch` / `cancel_period_mismatch` |
| `sales_data_review` | `SalesDataReviewCard.tsx` | `CONFIRM_CSV_SUBMISSION` + `rows` |
| `manual_entry_confirm` | `ManualEntryConfirmCard.tsx` | `confirm_manual_entry` / `cancel_manual_entry` |
| `csv_validation_error` / `lease_validation_error` | error table | re-upload |
| `submission_success` / `submission_error` | `SubmissionResultCard.tsx` | optional re-upload |
| `status_summary` | `StatusSummaryCard.tsx` | — |
| `admin_sales_data_list` | `components/AdminRowMenu.tsx` (not under `chatbot/`) | follow-up turns |
| `admin_confirm` | confirm/cancel | `confirm_admin_action` / `cancel_admin` |
| supporting-doc confirm | `SupportingDocConfirmCard.tsx` | `confirm_supporting_doc` + comment |
| `notification_list` | TC-3520 | Open → FE-2 |
| `notification_detail_card` | TC-3520 | optional explain-this |

**Action wiring source of truth:** `sales-data-agent/components/chatbot/SalesDataChat.tsx`  
**TypeScript shapes:** `sales-data-agent/lib/types/chat.ts`

Cards with `verbatim` / `ui.message` from the agent: **do not rewrite** the copy.

---

### FE-6 — CSV and supporting-doc attach

**File:** `libs/ui-web/src/lib/components/serviceRequestChatbot/ChatFileUpload.tsx`

Today: PDF/JPEG/PNG only → `POST /api/v2/upload` (SR documents). CSV must **not** go there.

| Last `ui.type` / stage | Accept | POST |
|---|---|---|
| `sales_data_upload_prompt`, `pending_months_list`, `other_months_reminder`, `period_mismatch_warning`, `csv_validation_error`, `lease_validation_error`, or `ui.show_file_upload` | `.csv` | `/api/v2/sales-data/upload` |
| Supporting-doc (`AWAITING_DOC_UPLOAD` or UI asking for a file) | PDF/JPEG/PNG | `/api/v2/sales-data/supporting-doc` |
| Otherwise | existing | `/api/v2/upload` |

Multipart field name: `file`. Optional form field: `session_id` = **chat** session id when you already have one.

After CSV:

```ts
await postServiceRequestChatTurn({
  session_id: sessionId,
  user_id: String(userId),
  message: `Uploaded ${filename}`,
  action: 'csv_uploaded',
  corrected_fields: { csv_session_id: result.sessionId },
}, aiToken);
```

After supporting doc:

```ts
action: 'doc_uploaded',
corrected_fields: { doc_session_id: result.sessionId }
```

If the upload API returns a new id, that id is what you send as `csv_session_id` / `doc_session_id`.

Template download: keep existing portal `GET /api/v1/sales-data/template` (`salesdataApi.ts`) with the **portal** session. Wire the copilot “Download template” button to that. Do not invent a second template endpoint.

---

### FE-7 — Chip payloads (copy as-is)

Always send a non-empty `message` when `action` is set (Open is the exception).

Review Submit (`sales_data_review`) — only `sales_amt` is editable:

```ts
{
  action: 'CONFIRM_CSV_SUBMISSION',
  message: 'Confirmed — please submit.',
  corrected_fields: {
    rows: [{
      lease_code: 'LC-1001',
      lease_id: 1,
      sales_period_start: '2026-08-01',
      sales_period_end: '2026-08-31',
      sales_amt: 100000,
    }],
  },
}
```

Pending-month chips:

```ts
{ action: 'proceed_other_months', message: 'Proceed with August' }
{ action: 'switch_pending_month', message: 'Switch to July 2026',
  corrected_fields: { year_month: '2026-07', lease_code: 'LC-1001' } }
```

Period mismatch:

```ts
{ action: 'confirm_period_mismatch', message: 'Submit this file' }
{ action: 'cancel_period_mismatch', message: 'Upload a different file' }
```

Manual:

```ts
{ action: 'confirm_manual_entry', message: 'Confirmed — please submit.' }
{ action: 'cancel_manual_entry', message: 'Cancel — let me change the details.' }
```

---

### FE-8 — i18n

Add EN/AR for chip labels (`Proceed`, `Switch`, `Submit this file`, `Download template`). Agent `response_message` is English; display `ui.message` as returned.

No new public env if RDD copilot already works (`NEXT_PUBLIC_AI_API_URL`, `NEXT_PUBLIC_API_BASE_PATH`).

---

## Do not “simplify” these product rules

1. **PENDING reminder gate** — after the user names a month, wait for proceed/switch. Do not show the CSV dropzone on the reminder card.
2. **Other months** = same lease + `PENDING` only. Never NEED_REUPLOAD / supporting / audited in that chip list.
3. **Switch chips** — at most 4; then “and N more — type the month”.
4. **Same month on multiple leases** — list leases; do not guess.
5. **Brand/Property** — blank CSV cells are filled from the platform; wrong names block submit. Review shows **platform** spelling.
6. **Sales-data Open `id`** — always the lease code. Never the bell UUID, never an SR number.

---

## Appendix A — `ui.type` values

| type | Typical stage |
|---|---|
| `message` | generic (already handled) |
| `sales_data_upload_prompt` | upload prompt |
| `pending_months_list` | pick a PENDING month |
| `other_months_reminder` | proceed / switch |
| `period_mismatch_warning` | file month ≠ asked month |
| `csv_validation_error` | Layer 1 fail |
| `lease_validation_error` | Layer 2 fail |
| `sales_data_review` | confirm CSV |
| `manual_entry_confirm` | confirm typed row |
| `submission_success` / `submission_error` | after POST |
| `status_summary` | status check |
| `admin_sales_data_list` / `admin_confirm` / `admin_action_success` | admin |
| supporting-doc confirm (see demo `SupportingDocConfirmCard`) | `AWAITING_DOC_CONFIRM` |
| `notification_list` | inbox |
| `notification_detail_card` | explain-this (optional) |

---

## Appendix B — `action` values

| action | When |
|---|---|
| `csv_uploaded` | After AI CSV upload |
| `CONFIRM_CSV_SUBMISSION` | Review Submit |
| `confirm_period_mismatch` / `cancel_period_mismatch` | Month mismatch card |
| `proceed_other_months` / `switch_pending_month` | Reminder card |
| `confirm_manual_entry` / `cancel_manual_entry` | Manual card |
| `doc_uploaded` | After supporting-doc upload API |
| `confirm_supporting_doc` / `cancel_supporting_doc` | Doc confirm (comment required) |
| `confirm_admin_action` / `cancel_admin` | Admin confirm |
| `cancel` | Generic cancel |

---

## Appendix C — Intent after Open (backend; FE only sends `key`)

| `key` | Agent intent |
|---|---|
| `SALES_DATA_PENDING`, `SALES_DATA_NEED_REUPLOAD`, default | `UPLOAD_SALES_DATA` |
| `SALES_DATA_NEED_UPLOAD_SUPPORTING_DOCUMENT`, `SALES_DATA_NEED_UPLOAD_AUDITED_SALES` | `UPLOAD_SUPPORTING_DOCUMENT` |
| `SALES_DATA_SUBMITTED` | `REVIEW_SALES_DATA` |

---

## How to test before cards exist

Backend/AI can be exercised with curl (no Tenant Central):

```http
POST {NEXT_PUBLIC_AI_API_URL}/api/v2/chat/service-request
Authorization: Bearer <ai_token>
Content-Type: application/json

{
  "session_id": null,
  "user_id": "<platform user id>",
  "message": "",
  "active_notification_id": {
    "id": "t0091324",
    "sub_category": "SALES_DATA",
    "key": "SALES_DATA_PENDING",
    "message": "Please submit sales data as of month June for …",
    "notification_type": "SALES_DATA"
  }
}
```

Expect `active_agent: sales_data_agent` and a sales `ui.type` — **not** handover preview.

Demo UI on `:3001` is a visual reference only. Its radio login is **not** a portal AI session token.

---

## Portal files to touch (checklist)

| File | Change |
|---|---|
| `libs/api/src/lib/srChatbot/srChatbotApi.ts` | Types, actions, **serialize** `active_notification_id` |
| `libs/ui-web/.../useServiceRequestChat.ts` | `openNotification`; pass ref through `sendTurn` |
| `libs/ui-web/.../serviceRequestAgenticRegistry.ts` | `openCopilotForNotification` |
| `libs/ui-web/src/lib/components/AIChatWidget/AIChatWidget.tsx` | Handle event `detail.activeNotificationId` |
| `libs/ui-web/src/lib/utils/Util.ts` | Bell `SALES_DATA` → Open + listing |
| Bell list call site of `handleNotificationNavigation` | Pass `text_content.en` + template `key` |
| `libs/ui-web/.../SrChatUiRenderer.tsx` | Sales + `notification_list` cards |
| `libs/ui-web/.../ChatFileUpload.tsx` | CSV / supporting-doc routes |
| i18n JSON | Chip labels |

Copy-from (do not import the Next app):

```text
cenomi-api-v2/service-request-chatbot/sales-data-agent/components/chatbot/SalesDataChat.tsx
cenomi-api-v2/service-request-chatbot/sales-data-agent/lib/types/chat.ts
```
