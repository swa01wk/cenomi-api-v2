# Sales Data Agent — Official Integration Guide

**Audience:** backend (`cenomi-api`), frontend (`cenomi-frontend`), AI chatbot (`cenomi-api-v2/service-request-chatbot`), and notification / copilot owners.

**Share with frontend developers:** the remaining official-portal work is in **[`frontend-sales-data-notification-agent.md`](./frontend-sales-data-notification-agent.md)** (tickets, files, Open contract, cards, uploads). This document is the full cutover playbook (AI + `cenomi-api` + FE).

**Status of this repo:** the Sales Data Agent and Notification Agent (list + Open → `sales_data_entry`) are implemented. Live inbox uses `GET /notifications/agent/sales-data`. Official **`cenomi-frontend` is not wired yet** (bell still listing-only; `srChatbotApi.ts` does not send `active_notification_id`). Product behaviour lives in [`sales_data_agent_PRD.md`](./sales_data_agent_PRD.md) and [`backend/docs/sales_data_detailed_scope_and_flow.md`](../backend/docs/sales_data_detailed_scope_and_flow.md). Notification-agent architecture (handover path) lives in [`../../docs/specs/3520-notification-agent-integration.md`](../../docs/specs/3520-notification-agent-integration.md).

**Reference implementation (copy from, do not ship):** [`sales-data-agent/`](../sales-data-agent/) on port `3001`. Treat that Next.js app as the working UI contract.

---

## 0. Read this first

1. The agent **does not create new sales-data periods**. It only updates rows that already exist in `cenomi-api` (`PENDING` / `NEED_*`).
2. The Sales Data Agent **does not send notifications**. `cenomi-api` already fires them inside `notifyUserOnSalesDataUpdate` / the sales-data sync cron. The **Notification Agent** (same AI graph) turns a bell/inbox Open into a first chat turn. Frontend work is **click → `active_notification_id`**, not a new notification writer.
3. Live sales-data HTTP must use the **user’s AI session JWT**, forwarded as `Authorization: Bearer`. Do not use the S2S `POST /cenomi-ai/login` token for GET/POST/PATCH `/sales-data` or `PUT /files`.
4. Platform JWT `roles` are names like `BILLING_CONTACT` / `LEASING_MANAGER` / `SALES_DATA_TEAM`. The chatbot supervisor and inbox skip-list now accept **both** those platform names **and** the demo aliases (`TENANT_CSV_UPLOADER`, `SALES_DATA_APPROVER`). Frontend must **not** rewrite JWT roles. (Appending aliases in `IssueAISessionToken` remains optional.)
5. Keep the mock (`:8001`) for isolated agent UI work. Staging/prod must point `SALES_DATA_API_BASE_URL` at **`cenomi-api`**, never at a second uvicorn AI process on `8001`. Locally, if Go already occupies `:8000`, run the AI on **`:8002`**.

---

## 1. Architecture

```mermaid
flowchart LR
  subgraph portals["cenomi-frontend"]
    Tenant["Tenant Central"]
    Admin["Admin portal"]
    Bell["Notification bell"]
    FAB["AIChatWidget / Copilot"]
  end

  subgraph ai["cenomi-api-v2 AI backend — Help Agent graph"]
    Chat["POST /api/v2/chat/service-request"]
    OpenN["open_notification_node"]
    ListN["list_notifications_node"]
    SDEntry["sales_data_entry"]
    CsvUp["POST /api/v2/sales-data/upload"]
    Graph["Sales Data LangGraph nodes"]
  end

  subgraph platform["cenomi-api"]
    Token["POST /auth/ai-session-token"]
    SD["GET/POST/PATCH /api/v1/sales-data"]
    Files["PUT /files"]
    Tpl["GET /api/v1/sales-data/template"]
    NotifDB["notificationRepo.Store"]
    Inbox["GET /notifications/agent/*"]
  end

  Bell -->|"Open"| FAB
  Tenant -->|"show my notifications"| FAB
  Admin --> FAB
  FAB -->|"portal cookie"| Token
  Token -->|"ai_token"| FAB
  FAB -->|"Bearer + active_notification_id"| Chat
  Chat --> OpenN
  Chat --> ListN
  ListN -->|"x-internal-api-token"| Inbox
  OpenN -->|"SALES_DATA: notification_context"| SDEntry
  OpenN -->|"HANDOVER: sr_id"| Graph
  SDEntry --> Graph
  FAB -->|"CSV multipart"| CsvUp
  CsvUp --> Graph
  Graph -->|"same Bearer"| SD
  Graph -->|"same Bearer"| Files
  Graph -->|"same Bearer"| Tpl
  SD -->|"on success"| NotifDB
```

| Hop | Who | What |
|---|---|---|
| Portal session | `cenomi-frontend` | Existing cookie / SSO. Unchanged. |
| AI session JWT | `cenomi-api` `POST /auth/ai-session-token` | Same mint used by the RDD / help copilot. |
| Chat turn | AI `POST /api/v2/chat/service-request` | Natural language + `action` + `corrected_fields`. Empty `message` is valid when `active_notification_id` is set. |
| Notification Open | `open_notification_node` | Handover: `id` = SR number → `sr_status_sync`. Sales data: `id` = lease code → `sales_data_entry`. |
| Notification list | `list_notifications_node` | Fans out to platform `/notifications/agent/...` (mock includes Sales Data). |
| CSV bytes | AI `POST /api/v2/sales-data/upload` | Parsed into an in-memory/Redis cache; **not** posted to `cenomi-api` yet. |
| Layer 2 + submit | `SalesDataApiClient` | `GET` then `POST` `/api/v1/sales-data` with the user’s Bearer. |
| Supporting doc | AI cache then `PUT /files` then `PATCH /api/v1/sales-data` | `document_id` + tenant response status. |
| Notification **create** | `cenomi-api` only | Written in the same DB transaction as POST/PATCH success. The AI never calls `notificationRepo.Store`. |

The **Notification Agent** is not a separate deployable. It is three nodes in the Help Agent graph:

| Node | File | Job |
|---|---|---|
| `list_notifications` | `backend/app/agents/graph/nodes/notification/list_notifications_node.py` | `"show my notifications"` → `ui.type = notification_list` |
| `open_notification` | `.../open_notification_node.py` | UI Open → domain agent (RDD **or** Sales Data) |
| `notification` | `.../notification_entry_node.py` | Explain / mark-read (viewing context) |

Handoff helpers: `backend/app/agents/services/notification_sales_data.py`.

Do **not** build a second LangGraph. Do **not** have `cenomi-api` call the AI. Frontend opens the copilot and POSTs `active_notification_id` (same contract as handover).

---

## 2. What you are integrating (capabilities)

The agent already does all of the following against mock or live `cenomi-api`:

| Capability | Tenant | Admin (`SALES_DATA_APPROVE`) |
|---|---|---|
| CSV upload (Cenomi template or legacy headers) | yes | no |
| Typed single-row entry | yes | no |
| PENDING-month reminder / switch (same lease only) | yes | n/a |
| Brand / Property fill or identity mismatch | yes | n/a |
| Asked-month vs file-month warning | yes | n/a |
| Supporting / audited / re-upload file | if `UPLOAD_SALES_DATA` | n/a |
| Review list, approve, ask upload | no | yes |
| Status check | yes | yes |

Statuses the agent must match **exactly** (platform strings, not labels):

`PENDING`, `SUBMITTED`, `APPROVED`, `NEED_REUPLOAD`, `NEED_UPLOAD_SUPPORTING_DOCUMENT`, `NEED_UPLOAD_AUDITED_SALES`, `RE_UPLOADED`, `UPLOADED_SUPPORTING_DOCUMENT`, `UPLOADED_AUDITED_SALES`.

Identity for PATCH (never `sales_data_id`):

```text
(lease_id, lease_code, sales_period_start, sales_period_end)
```

POST `/sales-data` body is a **JSON array**. All-or-nothing. Each item:

```json
{
  "lease_code": "LC-1001",
  "sales_period_start": "2026-08-01",
  "sales_period_end": "2026-08-31",
  "sales_amt": 100000
}
```

Dates on the wire are **`YYYY-MM-DD`**. The official CSV template uses **`DD-MM-YYYY`**; the AI parser converts.

---

## 3. Integration checklist (do in this order)

| # | Workstream | Blocking? | Outcome |
|---|---|---|---|
| 4 | Map platform permissions → chatbot roles | **Done in chatbot** (platform names accepted). Optional: also stamp aliases on the JWT | Supervisor allows tenant/admin sales intents |
| 5 | Point AI backend at live `cenomi-api` | **Yes** for staging | GET/POST/PATCH/files hit real data |
| 6 | Harden AI backend for multi-instance | Strongly recommended | CSV cache survives reload / replica |
| 7 | Frontend chat UI + CSV/doc upload | **Yes — remaining** | See [`frontend-sales-data-notification-agent.md`](./frontend-sales-data-notification-agent.md) |
| 8 | Frontend: Open → `active_notification_id` | **Yes — remaining** | Bell + inbox cards; Notification Agent already implemented |
| 9 | `cenomi-api` AI-JWT allow-list for GET/PATCH `/sales-data` | **In source**; restart the running Go binary | Layer 2 against dump/staging |
| 10 | Staging verification matrix | **Yes** | Sign-off before prod |

Frontend-only tickets: [`frontend-sales-data-notification-agent.md`](./frontend-sales-data-notification-agent.md). Steps 4–10 below are the detailed procedures.

---

## 4. Auth and RBAC (must do before any live GET)

### 4.1 Tokens — do not mix them

This is the same model as handover / RDD. See [`lease-lookup-bearer-auth-plan.md`](./lease-lookup-bearer-auth-plan.md).

| Token | Issuer | Consumer | Use for sales data? |
|---|---|---|---|
| Portal session cookie | Tenant Central / Admin | `POST /auth/ai-session-token` | Indirect only |
| **AI session JWT** (`ai_token`) | `cenomi-api` `POST /auth/ai-session-token` | Chatbot inbound **and** forwarded to `/sales-data` and `/files` | **Yes — this is the one** |
| S2S platform Bearer | `POST /cenomi-ai/login` + `x-internal-api-token` | Chatbot `PlatformApiClient` (SR create/patch) | **No** |
| MSP `x-internal-api-token` | Shared secret | Chatbot machine auth | Not a user identity |

Frontend already mints the AI token (RDD copilot):

```ts
// cenomi-frontend/libs/ui-web/.../useServiceRequestChat.ts
POST ${NEXT_PUBLIC_API_BASE_PATH}/auth/ai-session-token
credentials: 'include'
→ json.data.ai_token   // store in sessionStorage key sr-chatbot-ai-session-token
```

Every sales-data chat turn and every CSV/doc upload **must** send:

```http
Authorization: Bearer <ai_token>
```

Secrets that must match (already required for RDD):

| Chatbot env | `cenomi-api` env |
|---|---|
| `JWT_SECRET_KEY` | `AI_SESSION_TOKEN_SECRET` |
| `JWT_ALGORITHM=HS256` | HS256 |

Set `RBAC_ENFORCE=true` in staging/prod so missing/invalid JWT is `401`, not anonymous.

### 4.2 Chatbot roles vs platform permissions

Chatbot `ROLE_PERMITTED_INTENTS` (`backend/app/agents/schemas/help_agent_schema.py`):

| Chatbot role in JWT `roles` | Intents |
|---|---|
| `TENANT_CSV_UPLOADER` | `UPLOAD_SALES_DATA`, `ENTER_SALES_DATA_MANUAL`, `CHECK_SALES_DATA_STATUS`, `UPLOAD_SUPPORTING_DOCUMENT` |
| `TENANT_DOC_UPLOADER` | same tenant set (supporting docs are still gated later by platform `PUT /files` / `UPLOAD_SALES_DATA`) |
| `SALES_DATA_APPROVER` | `REVIEW_SALES_DATA`, `APPROVE_SALES_DATA`, `CHECK_SALES_DATA_STATUS` |
| `ADMIN` / `is_global_admin` | all intents |

Platform permissions (frontend enums / `cenomi.permissions`):

| Permission | Typical actors | Maps to chatbot role |
|---|---|---|
| `SALES_DATA_UPDATE` | Billing Contact, Leasing Manager, Company Admin | `TENANT_CSV_UPLOADER` |
| `SALES_DATA_UPDATE` **and** `UPLOAD_SALES_DATA` (and usually `REUPLOAD_SALES_DATA`) | Company Admin | also `TENANT_DOC_UPLOADER` |
| `SALES_DATA_APPROVE` | Sales Data Team (`SALES_DATA_TEAM`, role id 30) | `SALES_DATA_APPROVER` |

`IssueAISessionToken` still stamps **platform role names** in `roles`. The chatbot now treats `BILLING_CONTACT`, `LEASING_MANAGER`, other company-level contacts, and `SALES_DATA_TEAM` as equivalent to the demo aliases. Frontend must forward the JWT as-is.

### 4.3 Optional: also stamp demo aliases on the JWT

Not required for routing. Useful if logs / eval still look for `TENANT_CSV_UPLOADER`.

**Place:** `cenomi-api` `IssueAISessionToken` (`cenomi_ai/usecase/cenomi_ai_usecase.go`), after `collectSessionPermissions`.

```go
func appendSalesDataChatbotRoles(roles []string, permissionNames []string) []string {
    has := map[string]struct{}{}
    for _, p := range permissionNames {
        has[p] = struct{}{}
    }
    out := append([]string{}, roles...)
    if _, ok := has["SALES_DATA_UPDATE"]; ok {
        out = append(out, "TENANT_CSV_UPLOADER")
    }
    if _, ok1 := has["SALES_DATA_UPDATE"]; ok1 {
        if _, ok2 := has["UPLOAD_SALES_DATA"]; ok2 {
            out = append(out, "TENANT_DOC_UPLOADER")
        }
    }
    if _, ok := has["SALES_DATA_APPROVE"]; ok {
        out = append(out, "SALES_DATA_APPROVER")
    }
    return dedupeStrings(out)
}
```

**Fallback** is no longer needed for routing (chatbot already accepts platform names). Keep the Go helper only if you want demo aliases in logs.

Verify a minted token (jwt.io or `python -c` decode):

```json
{
  "sub": "user:123",
  "user_id": 123,
  "user_type": "TENANT",
  "tenant_profile_id": 101,
  "roles": ["Billing Contact", "TENANT_CSV_UPLOADER"],
  "unique_property_ids": [3041],
  "is_global_admin": false
}
```

Admin Sales Data Team token must contain `"SALES_DATA_APPROVER"`.

### 4.4 Chat entitlement (`agents_permissions`)

`POST /auth/ai-session-token` refuses users with **zero** `allowed_agents` (`ErrAISessionNoChatEntitlement`). Sales Data currently rides the **same Help Agent graph** as FAQ / RDD routing (`allAIAgents()` is still `help`, `rdd`, `tenant_ops`).

Practical rule:

- Any user who already has `HELP_AGENT` (or Super Admin) can open the widget; the supervisor then allows/denies sales intents from `roles`.
- Users who have `SALES_DATA_UPDATE` / `SALES_DATA_APPROVE` but **no** `HELP_AGENT` cannot mint an AI token today.

Decide one of:

1. **Grant `HELP_AGENT`** to every sales-data role that should use the copilot (role-permission admin, no schema change), or
2. Add a dedicated `SALES_DATA_AGENT` permission + `agents_permissions` row. That requires relaxing the CHECK constraint (`grants_agent IN ('help','rdd','tenant_ops')`) **and** teaching the chatbot to honour `allowed_agents` for sales. Prefer option 1 for Phase 1.

---

## 5. Point the AI backend at official `cenomi-api`

### 5.1 Environment

On the **AI backend** (`cenomi-api-v2/service-request-chatbot/backend/.env`):

```bash
# Live Cenomi API origin — NO trailing slash. Same host the portal uses for /api/v1.
# Local example: http://localhost:8080
# Staging example: https://api-staging.example.com
SALES_DATA_API_BASE_URL=https://<cenomi-api-host>

# Must stay aligned with cenomi-api AI_SESSION_TOKEN_SECRET
JWT_SECRET_KEY=<same-as-AI_SESSION_TOKEN_SECRET>
JWT_ALGORITHM=HS256
RBAC_ENFORCE=true

# Include Tenant Central + Admin origins (Nx tenant-web is often :4200)
CORS_ORIGINS=http://localhost:4200,http://localhost:3000,https://tenant-staging.example.com,https://admin-staging.example.com

# Already used for RDD — keep pointing at the same cenomi-api
SERVICE_REQUEST_API_BASE_URL=https://<cenomi-api-host>
LEASE_TENANT_API_BASE_URL=https://<cenomi-api-host>
```

`SalesDataApiClient` (`backend/app/agents/services/sales_data_api_client.py`) calls:

| Method | Path | Notes |
|---|---|---|
| GET | `{SALES_DATA_API_BASE_URL}/api/v1/sales-data` | `disable_pagination=true` for Layer 2 |
| POST | `{SALES_DATA_API_BASE_URL}/api/v1/sales-data` | JSON array, all-or-nothing |
| PATCH | `{SALES_DATA_API_BASE_URL}/api/v1/sales-data` | Single object |
| PUT | `{SALES_DATA_API_BASE_URL}/files` | Supporting docs |
| GET | `{SALES_DATA_API_BASE_URL}/api/v1/sales-data/template` | CSV template bytes |

If `cenomi-api` is reverse-proxied so the public origin already includes `/api/v1`, **do not** set the base to that prefix — the client appends `/api/v1/sales-data` itself. Base must be the host (and optional gateway prefix **before** `/api/v1`).

Add `SALES_DATA_API_BASE_URL` to `.env.example` and the deployment charts when you cut over.

### 5.2 Live GET envelope (already handled)

Client unwraps both shapes via `unwrap_sales_data_rows`:

```json
{ "success": true, "data": { "list": [ /* rows */ ], "total_count": 12 } }
```

Brand / property field aliases (Layer 2 identity):

| Meaning | Accepted keys on a GET row |
|---|---|
| Brand | `brand_name`, `brand` |
| Property / mall | `property_name`, `mall_name`, `property`, `mall` |

Staging smoke (as a tenant with PENDING rows):

```bash
TOKEN="<ai_token>"
BASE="https://<cenomi-api-host>"

curl -sS -H "Authorization: Bearer $TOKEN" \
  "$BASE/api/v1/sales-data?disable_pagination=true" | jq '.data.list[0]'
```

Confirm the first row has `lease_id`, `lease_code`, `sales_period_start`, `sales_period_end`, `status`, and a brand/property name.

### 5.3 CORS and files

`PUT /files` and `/sales-data` are **server-to-server from the AI process**, so browser CORS on `cenomi-api` does not apply to those hops. Browser CORS **does** apply to:

- Tenant Central → AI `POST /api/v2/chat/service-request`
- Tenant Central → AI `POST /api/v2/sales-data/upload`
- Tenant Central → `cenomi-api` `POST /auth/ai-session-token` (already working for RDD)

If the copilot loads but CSV upload fails with a browser CORS error, add the portal origin to chatbot `CORS_ORIGINS` and restart.

### 5.4 What **not** to change in `cenomi-api` (Phase 1)

Do **not** add new sales-data REST endpoints for the agent. Do **not** move notification creation into the chatbot.

Optional later (only if staging shows it):

- POST `/sales-data` lease-access checks weaker than GET (PRD note). If a tenant can POST a lease they cannot GET, that is a platform bug — do not paper over it in the agent.
- Bulk PATCH. Admin review currently PATCHes **one row at a time** on purpose (per-row notifications + Yardi).

---

## 6. AI backend production hardening (this repo)

### 6.1 CSV / supporting-doc cache

`POST /api/v2/sales-data/upload` and `POST /api/v2/sales-data/supporting-doc` store bytes in a **process-local dict**. That breaks if:

- uvicorn `--reload` restarts between upload and the next chat turn
- more than one AI replica handles the follow-up turn
- the next turn hits a different pod

**Before staging with >1 replica:** move `_FILE_CACHE` in `sales_data_upload.py` and the supporting-doc twin to Redis (`REDIS_URL` is already in config) keyed by `session_id`, TTL ~1 hour. The chat turn looks up `corrected_fields.csv_session_id` / `doc_session_id`.

Until then, run **one** AI replica and tell testers not to wait many minutes between dropzone and Submit.

### 6.2 Chat routes to use from official frontend

Official SR copilot already calls:

```text
POST {NEXT_PUBLIC_AI_API_URL}/api/v2/chat/service-request
```

Sales data uses **that same route**. Demo `:3001` still calls `/api/chat/service-request` (unversioned duplicate). Production FE should stay on **v2**.

CSV / docs (AI, not `cenomi-api`):

```text
POST {NEXT_PUBLIC_AI_API_URL}/api/v2/sales-data/upload
POST {NEXT_PUBLIC_AI_API_URL}/api/v2/sales-data/supporting-doc
```

Multipart field name: `file`. Optional form field: `session_id`.

### 6.3 Template download

Options (pick one):

1. **Portal already has it** — keep `GET /api/v1/sales-data/template` from `salesdataApi.ts` with the portal session (not the AI token, unless `cenomi-api` AI middleware allows it). Simplest.
2. **Proxy via AI** — chatbot calls `SalesDataApiClient.get_template()` with the forwarded Bearer and streams CSV. Use this if the template must appear inside the copilot without a second origin.

The upload prompt UI sets `show_template_download: true`. Wire the button to whichever option you chose.

---

## 7. Official frontend integration (`cenomi-frontend`)

**Hand this section (and §8) to FE as a file:** [`frontend-sales-data-notification-agent.md`](./frontend-sales-data-notification-agent.md). That brief is the ticket list; the subsections below are the same plan kept next to the backend cutover.

Reuse the existing FAB / Copilot (`AIChatWidget`, `useServiceRequestChat`, `postServiceRequestChatTurn`). Do not mount the demo Next app.

### 7.1 Env

Tenant Central and Admin already need:

```bash
NEXT_PUBLIC_AI_API_URL=https://<ai-backend-host>
NEXT_PUBLIC_API_BASE_PATH=/api   # or whatever prefixes /auth/ai-session-token
```

No new public env is required if RDD copilot already works on that portal.

### 7.2 Types and actions

**File:** `libs/api/src/lib/srChatbot/srChatbotApi.ts`

1. Extend `SrChatbotUiType` with the sales-data UI types (Appendix A) **and** `notification_list` / `notification_detail_card` if those are not wired yet (TC-3520).
2. Extend `SrChatbotChatAction` with the sales-data actions (Appendix B).
3. Add `active_notification_id?: { id: string; sub_category?: string; key?: string; message?: string; period_hint?: string; notification_type?: string }` on `ServiceRequestChatTurnRequest` (backend already accepts it; official `srChatbotApi.ts` may still omit it).
4. Keep sending `corrected_fields` as a free-form object — `csv_session_id`, `doc_session_id`, `rows` already flow through. Prefer **not** to put `notification_context` here for Open; use `active_notification_id` so the Notification Agent owns the handoff.

### 7.3 Render cards in the existing chat bubble

**File:** `libs/ui-web/src/lib/components/serviceRequestChatbot/SrChatUiRenderer.tsx`

Today unknown `ui.type` returns `null` (markdown message still shows). Port these demo components into `libs/ui-web` (or a `salesDataChatbot/` folder) and switch on `ui.type`:

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
| `admin_sales_data_list` | `AdminRowMenu.tsx` | follow-up turns |
| `admin_confirm` | confirm/cancel | `confirm_admin_action` / `cancel_admin` |
| supporting-doc confirm | `SupportingDocConfirmCard.tsx` | `confirm_supporting_doc` + comment |
| `notification_list` | TC-3520 inbox cards | Open → `openNotification(ref)` — sales rows use `lease_code` as `id` |
| `notification_detail_card` | TC-3520 detail | optional explain-this |

Demo sources (copy behaviour, restyle to Cenomi chat CSS):

```text
cenomi-api-v2/service-request-chatbot/sales-data-agent/components/chatbot/
  SalesDataChat.tsx              # action wiring (source of truth)
  SalesDataReviewCard.tsx
  ManualEntryConfirmCard.tsx
  SupportingDocConfirmCard.tsx
  FileUploadButton.tsx
  StatusSummaryCard.tsx
  SubmissionResultCard.tsx
```

### 7.4 CSV and supporting-doc upload from ChatInput

**File:** `ChatFileUpload.tsx` currently allows only PDF/JPEG/PNG and posts to `/api/v2/upload` (SR documents).

Add a sales-data-aware attach path:

1. If `ui.show_file_upload` or last `ui.type` is `sales_data_upload_prompt` / `pending_months_list` / `other_months_reminder` / `period_mismatch_warning` / `csv_validation_error` / `lease_validation_error` → accept `.csv` and `POST /api/v2/sales-data/upload`.
2. If last stage is supporting-doc (`AWAITING_DOC_UPLOAD` or `ui` asking for a file) → accept PDF/JPEG/PNG and `POST /api/v2/sales-data/supporting-doc`.
3. Otherwise keep the existing SR upload.

After CSV upload succeeds:

```ts
await postServiceRequestChatTurn({
  session_id: sessionId,
  user_id: String(userId),
  message: `Uploaded ${filename}`,
  action: 'csv_uploaded',
  corrected_fields: { csv_session_id: result.sessionId },
}, aiToken);
```

After supporting-doc upload:

```ts
action: 'doc_uploaded',
corrected_fields: { doc_session_id: result.sessionId }
```

`session_id` on the multipart form must be the **chat** session id when you already have one, so the cache key matches the next turn. If the upload API returns a new id, **that** id is what you put in `csv_session_id`.

### 7.5 Review Submit payload

When the tenant clicks Submit on `sales_data_review`, send edited amounts (only `sales_amt` is editable):

```ts
{
  action: 'CONFIRM_CSV_SUBMISSION',
  message: 'Confirmed — please submit.',
  corrected_fields: {
    rows: [
      {
        lease_code: 'LC-1001',
        lease_id: 1,
        sales_period_start: '2026-08-01',
        sales_period_end: '2026-08-31',
        sales_amt: 100000
      }
    ]
  }
}
```

Pending-month chips:

```ts
{ action: 'proceed_other_months', message: 'Proceed with August' }

{ action: 'switch_pending_month',
  message: 'Switch to July 2026',
  corrected_fields: { year_month: '2026-07', lease_code: 'LC-1001' } }
```

Period mismatch:

```ts
{ action: 'confirm_period_mismatch', message: 'Submit this file' }
{ action: 'cancel_period_mismatch', message: 'Upload a different file' }
```

Manual confirm:

```ts
{ action: 'confirm_manual_entry', message: 'Confirmed — please submit.' }
{ action: 'cancel_manual_entry', message: 'Cancel — let me change the details.' }
```

### 7.6 Open copilot in sales-data mode

**File:** `serviceRequestAgenticRegistry.ts`

Add `openCopilotInSalesDataMode(seed?: SalesDataChatSeed)` modelled on `openCopilotInSrMode()`:

- `setFabOpen(true)` / `copilotOpen`
- `window.dispatchEvent(new CustomEvent('cenomi-fab:open-sr', { detail: seed }))`

**File:** `AIChatWidget.tsx` / `useServiceRequestChat.ts`

Handover already has `openExistingSr(srId)`. Add `openNotification(ref)` that:

1. Forces a **new** session (`session_id: null`).
2. After `ai_token` is ready, POSTs one turn with `message: ""` and `active_notification_id: ref` (see §8.4). Do not wait for the user to type.
3. For sales-data rows (`kind === "sales_data"`), `ref.id` is `lease_code`, **not** `notification_id` and **not** `sr_id`.

`openCopilotInSalesDataMode(seed)` can dispatch `cenomi-fab:open-sr` with `detail.activeNotificationId` so the widget calls `openNotification`.

Optional: when `state.intent` on a response is `UPLOAD_SALES_DATA` / `ENTER_SALES_DATA_MANUAL` / `REVIEW_SALES_DATA`, `router.push('/dashboard/salesdata')` so listing + copilot sit in split view. Listing page itself stays the classic UI.

### 7.7 ChatInput allow-list for CSV

Extend accept + MIME checks. Do not send CSV to `/api/v2/upload` (SR file pipeline).

### 7.8 i18n

Add EN/AR strings for chip labels (`Proceed`, `Switch`, `Submit this file`, `Download template`). Agent `response_message` is currently English; `verbatim: true` on several cards means **do not rewrite** those messages in `response_generation`. Display `ui.message` as returned.

---

## 8. Notification Agent ↔ Sales Data Agent

**Backend status: done.** Sales-data Open and list are implemented on the Notification Agent. Remaining work is official frontend (send `active_notification_id`). Live inbox uses `GET /notifications/agent/sales-data` (same auth as handover).

### 8.1 Who does what

| Layer | Responsibility |
|---|---|
| `cenomi-api` | **Creates** notification rows (`notifyUserOnSalesDataUpdate`, sales-data sync cron). Never calls the AI. |
| Notification Agent (AI graph) | **Lists** action-required inbox; **opens** a row into the correct domain agent. Never writes notifications. Never calls `POST /sales-data`. |
| Sales Data Agent | After Open, validates/submits via `GET`/`POST`/`PATCH /sales-data`. |
| Frontend | Bell / inbox card Open → copilot + `POST /api/v2/chat/service-request` with `active_notification_id`. |

```text
cenomi-api writes SALES_DATA_PENDING (and NEED_* / SUBMITTED)
        ↓
Tenant sees bell / Copilot inbox
        ↓
FE: openNotification({ id: lease_code, sub_category: "SALES_DATA", key, message })
        ↓
AI: open_notification_node
        ↓  (sales data — no SR id)
sales_data_entry  +  notification_context { lease_code, period_hint, source }
```

Handover Open on the same node still uses `id` = SR number and continues to `sr_status_sync`. Do not mix those `id` meanings.

### 8.2 Two different IDs

| Field | What it is | Send as Open `id`? |
|---|---|---|
| Row `id` / DB `notification_id` | Unique bell-row UUID (`NotificationRow.id`) | **No** |
| `text_content.id` | Lease code (PMSTenantCode), e.g. `LC-1001` or `t0105712` | **Yes** — this is `active_notification_id.id` |
| `lease_id` (row) | Numeric lease PK | Optional extra; not required for Open |

### 8.3 What `cenomi-api` already creates (do not duplicate)

| Event | Template key | Target |
|---|---|---|
| Sync cron: missing period | `SALES_DATA_PENDING` | Tenant contacts, type `SALES_DATA`, category `PENDING` |
| Tenant POST submit | `SALES_DATA_SUBMITTED` | Role `SALES_DATA_TEAM` (id 30) |
| Admin PATCH ask re-upload | `SALES_DATA_NEED_REUPLOAD` | Tenant, `ActionRequired: true` |
| Admin PATCH supporting doc | `SALES_DATA_NEED_UPLOAD_SUPPORTING_DOCUMENT` | Tenant |
| Admin PATCH audited | `SALES_DATA_NEED_UPLOAD_AUDITED_SALES` | Tenant |
| Admin approve | `SALES_DATA_APPROVED` | Tenant |
| Tenant re-upload / doc / audited | `SALES_DATA_REUPLOADED` / `SALES_DATA_UPLOADED_SUPPORTING_DOCUMENT` / `SALES_DATA_UPLOADED_AUDITED_SALES_DATA` | Sales data team |

Templates: `cenomi-api/internal/config/notification.json`.

PENDING `text_content` (typical):

```json
{
  "key": "SALES_DATA_PENDING",
  "type": "SALES_DATA",
  "category": "PENDING",
  "id": "<PMSTenantCode / lease_code>",
  "en": "Please submit sales data as of month August for Brand@Mall.",
  "ar": "..."
}
```

**Not stored:** ISO `sales_period_start` / `end`. Period is only in the EN/AR sentence. Numeric `lease_id` **is** on the notification row.

### 8.4 Three Notification Agent flows (sales data)

Same three flows as handover (TC-3520), with a sales-data branch on Open.

**Flow A — List inbox** (implemented)

User: `"show my notifications"` / `"show sales data notifications"`.

- Deterministic intent `LIST_NOTIFICATIONS` → `list_notifications_node`.
- Mock adapter returns a **Sales Data** group. Live HTTP calls `GET {platform}/notifications/agent/sales-data` (same envelope as handover; `x-internal-api-token`).
- Audience skip: demo aliases **and** live platform names (`BILLING_CONTACT`, `LEASING_MANAGER`, `SALES_DATA_TEAM`, …). Unknown JWT roles call every endpoint (platform intersects server-side).
- Each sales row:

```json
{
  "kind": "sales_data",
  "notification_id": "ntf-sd-001",
  "sr_id": null,
  "lease_code": "LC-1001",
  "sub_category": "SALES_DATA",
  "key": "SALES_DATA_PENDING",
  "message": "Please submit sales data as of month August for Brand@Mall.",
  "action_required": true
}
```

On Open, pass `lease_code` as `active_notification_id.id`, `key`, and `message`.

Passive panel (no chat turn): `GET /api/v1/notifications/inbox?sub_category=SALES_DATA` — same row shape.

**Flow B — Open into Sales Data Agent** (implemented)

UI tap Open, empty message, **new session**:

```http
POST /api/v2/chat/service-request
Authorization: Bearer <ai_token>
Content-Type: application/json

{
  "session_id": null,
  "user_id": "<platform user id>",
  "message": "",
  "active_notification_id": {
    "id": "LC-1001",
    "sub_category": "SALES_DATA",
    "key": "SALES_DATA_PENDING",
    "message": "Please submit sales data as of month August for Brand@Mall.",
    "notification_type": "SALES_DATA"
  }
}
```

| Field | Required | Meaning |
|---|---|---|
| `id` | **Yes** | Lease code (`text_content.id`). **Not** the notification row id. |
| `sub_category` | **Yes** for sales | `SALES_DATA` or a template key (`SALES_DATA_PENDING`, …) |
| `key` | Recommended | Chooses upload vs supporting-doc vs admin review |
| `message` | Recommended | EN body — backend extracts `period_hint` |
| `period_hint` | Optional | If the UI already parsed it, skip regex |
| `notification_type` | Optional | `SALES_DATA` |

`open_notification_node` then:

1. Detects sales-data via `sub_category` / `key` / `notification_type` (`notification_sales_data.py`).
2. Parses `period_hint` from `message` if omitted (`as of month August` / `as of August 2026`).
3. Seeds `backend_refs.notification_context` + `corrected_fields.notification_context`.
4. Sets `active_agent = sales_data_agent` (does **not** set `sr_id`).
5. Graph routes to `sales_data_entry`, **not** `sr_status_sync`.

Intent after Open:

| `key` / sub_category | Intent | Next stage |
|---|---|---|
| `SALES_DATA_PENDING`, `SALES_DATA_NEED_REUPLOAD`, default | `UPLOAD_SALES_DATA` | Pending-month gate / upload prompt |
| `SALES_DATA_NEED_UPLOAD_SUPPORTING_DOCUMENT`, `SALES_DATA_NEED_UPLOAD_AUDITED_SALES` | `UPLOAD_SUPPORTING_DOCUMENT` | Doc select / attach |
| `SALES_DATA_SUBMITTED` | `REVIEW_SALES_DATA` | Admin list |

What the Sales Data Agent does next:

1. Resolves PENDING / NEED_* rows for that lease (and period if parseable).
2. Other **PENDING** months on the **same lease** → `other_months_reminder` (wait for proceed/switch).
3. Unique month → `sales_data_upload_prompt` (CSV or typed amount).
4. No matching row → stale-notification copy + listing link.

The demo app’s `corrected_fields.notification_context` first turn still works. Prefer `active_notification_id` so Open matches handover.

**Flow C — Explain this** (optional)

`viewing_notification_id` + snapshot on later turns. Unchanged from TC-3520.

### 8.5 Period hint (backend already parses)

Frontend **does not** have to extract the month if it sends `message` (the EN body). Helpers in `notification_sales_data.extract_sales_period_hint` understand:

- `as of month August`
- `as of August 2026`
- `for month August`

If parse fails, still send `id` (lease code). The agent lists PENDING months (`pending_months_list`).

### 8.6 Official frontend (still to do)

Ticket list for FE: [`frontend-sales-data-notification-agent.md`](./frontend-sales-data-notification-agent.md) (FE-3).

Today `handleNotificationNavigation` for `SALES_DATA` only opens the listing:

```text
/dashboard/salesdata?page=1&limit=10&lease_code={text_content.id}&section=…&from_notification=true
```

**Target:** keep listing as fallback when `NEXT_PUBLIC_AI_API_URL` is unset. Otherwise open the copilot and call the Notification Agent Open path.

```ts
} else if (NotificationTypeEnum.SALES_DATA === type) {
  const leaseCode = inquiryId; // text_content.id — lease code, NOT notification row id
  const aiConfigured = Boolean(process.env.NEXT_PUBLIC_AI_API_URL?.trim());

  if (aiConfigured) {
    openNotification({
      id: leaseCode,
      sub_category: 'SALES_DATA',
      key: textContentKey,           // e.g. SALES_DATA_PENDING
      message: englishBody,          // text_content.en
      notification_type: 'SALES_DATA',
    });
    // optional split screen
    const baseUrl = destination.format(inquiryId, setSalesDataSection(category));
    const sep = baseUrl.includes('?') ? '&' : '?';
    router.push(`${baseUrl}${sep}from_notification=true`);
    return;
  }

  const baseUrl = destination.format(inquiryId, setSalesDataSection(category));
  const separator = baseUrl.includes('?') ? '&' : '?';
  router.push(`${baseUrl}${separator}from_notification=true`);
}
```

Thread `text_content.en` and `text_content.key` into `handleNotificationNavigation` (today it has `inquiryId` / `category` / `key` as the template key in some call sites — confirm you pass the **template** key, not a random field).

Copilot inbox card Open: if `row.kind === "sales_data"`, use `row.lease_code` as `id`. If `row.kind === "service_request"`, keep using `row.sr_id` (handover).

Add `openNotification` next to `openExistingSr` in `useServiceRequestChat.ts`. TC-3520 FE for `notification_list` rendering still applies.

**Admin `SALES_DATA_SUBMITTED`:** Open with `key: "SALES_DATA_SUBMITTED"` → `REVIEW_SALES_DATA`. Phase 1 may keep listing-only for admin submit notifications if you only deep-link tenant PENDING / NEED_*.

### 8.7 Files to read

| Piece | Path |
|---|---|
| Open shim | `backend/app/agents/graph/nodes/notification/open_notification_node.py` |
| Sales-data classify / period hint | `backend/app/agents/services/notification_sales_data.py` |
| Inbox HTTP + mock fixture | `backend/app/agents/services/notification_inbox_service.py` |
| List row `kind: sales_data` | `backend/app/agents/services/notification_payload.py` |
| Chat request model | `backend/app/api/routes/chat.py` (`ActiveNotificationRef`) |
| Graph edge after Open | `backend/app/agents/graph/help_agent_graph.py` (`_route_after_open_notification`) |
| Consumer | `backend/app/agents/graph/nodes/sales_data/sales_data_entry_node.py` |
| Panel GET | `backend/app/api/routes/notifications.py` |

---

## 9. Optional `cenomi-api` work

Only if staging proves it:

| Item | Why |
|---|---|
| Append chatbot role aliases in `IssueAISessionToken` | Optional (Step 4.3); chatbot already accepts platform names |
| Grant `HELP_AGENT` to sales-data roles | Token mint (`ErrAISessionNoChatEntitlement` otherwise) |
| AI session middleware: allow `GET`/`PATCH` `/sales-data` (and template) with AI JWT | **Done in source** (`isAgenticHandoverRoute`). Rebuild/restart Go if the running binary still 403s |
| CORS on `cenomi-api` for `/auth/ai-session-token` | Already required for RDD |
| Confirm `disable_pagination=true` returns all tenant rows | Layer 2 completeness |

AI JWT middleware (`ai_session_auth.go`) hydrates `SSOUser` from `user_type` + `tenant_profile_id`. Tenant sales-data GET is scoped by tenant profile. If GET returns empty for a user who sees rows in the classic listing, compare headers: classic listing uses portal JWT; agent uses AI JWT. Fix scoping in `cenomi-api`, not by disabling auth on the chatbot.

---

## 10. Staging verification matrix

Reset nothing on live DB. Use a known tenant profile with PENDING rows (or wait for sync cron).

| # | Actor | Steps | Expected |
|---|---|---|---|
| 1 | Tenant with `SALES_DATA_UPDATE` | Mint AI token; decode JWT | Contains a company-level sales role (`BILLING_CONTACT`, `LEASING_MANAGER`, …) and/or `TENANT_CSV_UPLOADER` |
| 2 | Same | “I want to submit sales data” (no month) | `pending_months_list` or upload prompt; **not** SR handover |
| 3 | Same | Name a month that has other PENDING months on that lease | `other_months_reminder`; dropzone **hidden** until proceed/switch |
| 4 | Same | Proceed, upload Cenomi CSV for that month | `sales_data_review` with platform Brand/Property |
| 5 | Same | CSV Brand wrong name (not case) | `lease_validation_error`; ask re-upload; **not** stale notification |
| 6 | Same | CSV Brand same name, different case | Pass; review shows **platform** spelling |
| 7 | Same | File is a **different** month than asked | `period_mismatch_warning` “Month does not match” |
| 8 | Same | File includes asked month **plus** extras | “Extra months in this file” / Submit this file |
| 9 | Same | Confirm Submit | `POST /sales-data` 200; listing row `SUBMITTED`; Sales Data Team gets `SALES_DATA_SUBMITTED` |
| 10 | Same | Type `sales amount for lease code LC-… for month august 2026 is 100000 SAR` | `manual_entry_confirm` with Brand/Property filled from GET |
| 11 | Tenant | `"show sales data notifications"` | `notification_list` with `kind: sales_data`, `lease_code`, `sr_id: null` |
| 12 | Tenant | Open that row (`active_notification_id`) | Copilot; pending reminder or upload prompt for that lease/month — **not** `sr_status_sync` |
| 13 | Tenant | Bell click PENDING (after FE wiring) | Same as row 12; listing-only if AI URL unset |
| 14 | Tenant | Submit via listing, then Open old notification | Stale copy; no overwrite of `SUBMITTED`/`APPROVED` |
| 15 | Admin `SALES_DATA_APPROVE` | “Review sales data” | `admin_sales_data_list`; Approve / Ask upload |
| 16 | Admin | Approve | PATCH; tenant `SALES_DATA_APPROVED`; Yardi path unchanged |
| 17 | Admin | Ask supporting document | Tenant NEED_* notification; tenant Open + attach PDF; PATCH `UPLOADED_SUPPORTING_DOCUMENT` |
| 18 | Tenant **without** `UPLOAD_SALES_DATA` | Try supporting-doc intent | Denied or platform `PUT /files` fails; Billing Contact still can CSV |
| 19 | Mall Manager without sales perms | “upload sales data” | Denied; RDD intents still work. Inbox does **not** include Sales Data group |
| 20 | Replica / reload | Upload CSV, restart AI, then Submit | Fails until Redis cache (Step 6.1) — known |

Also confirm **classic** `/dashboard/salesdata/upload` still works. The agent is additive.

---

## 11. Local vs staging cutover

### Local (keep mock)

```text
sales-data-agent :3001  →  AI :8000 (or :8002 if Go uses :8000)  →  mock :8001
                           or live cenomi-api :8000 + dump Postgres :6003
```

```bash
# mock (optional)
cd cenomi-api-v2/service-request-chatbot/mock-backend
uvicorn main:app --reload --port 8001

# AI — use :8002 when local cenomi-api already listens on :8000
cd ../backend
# SALES_DATA_API_BASE_URL=http://localhost:8001   # mock
# SALES_DATA_API_BASE_URL=http://localhost:8000   # live Go
uvicorn app.main:app --reload --port 8002
```

If `GET /dev/mock/reset` 404s, port 8001 is **not** mock — kill the extra AI process.

### Staging

```text
Tenant Central / Admin  →  AI (staging)  →  cenomi-api (staging)
```

1. Deploy AI with `SALES_DATA_API_BASE_URL` = staging `cenomi-api`.
2. Confirm RDD copilot still works (same JWT secret).
3. Ship FE notification + UI cards.
4. Run the matrix on a dedicated tenant profile.

### Production

Same as staging. Do not leave `SALES_DATA_API_BASE_URL=http://localhost:8001`. Do not run mock-backend in the cluster.

---

## 12. Feature behaviour the FE must not “simplify”

These are easy to break if the UI skips stages:

1. **PENDING reminder gate** — after the user names a month, wait for proceed/switch. Do not show the CSV dropzone on the reminder card.
2. **Other months = same lease + PENDING only** — never NEED_REUPLOAD / supporting / audited in that chip list.
3. **Switch chips** — show at most 4; then “and N more — type the month”.
4. **August on multiple leases** — list leases; do not guess.
5. **Brand/Property** — blank CSV cells are filled from platform; wrong names block submit.
6. **verbatim cards** — render `ui.message` as-is.
7. **Sales-data Open `id`** — always the lease code (`text_content.id` / `row.lease_code`). Never the bell-row UUID and never an SR number.

---

## Appendix A — `response_ui.type` values

| type | Typical stage |
|---|---|
| `message` | generic |
| `sales_data_upload_prompt` | `UPLOAD_INTENT` |
| `pending_months_list` | `AWAITING_PENDING_MONTHS_CHOICE` |
| `other_months_reminder` | `AWAITING_OTHER_MONTHS_REMINDER` |
| `period_mismatch_warning` | `AWAITING_PERIOD_MISMATCH_CONFIRM` |
| `csv_validation_error` | Layer 1 fail |
| `lease_validation_error` | Layer 2 fail |
| `sales_data_review` | confirm CSV |
| `manual_entry_confirm` | confirm typed row |
| `submission_success` / `submission_error` | after POST |
| `status_summary` | status check |
| `admin_sales_data_list` / `admin_confirm` / `admin_action_success` | admin |
| supporting-doc confirm types (see demo `SupportingDocConfirmCard`) | `AWAITING_DOC_CONFIRM` |
| `notification_list` | inbox list (Notification Agent) |
| `notification_detail_card` | explain-this (optional) |

TypeScript shapes: `sales-data-agent/lib/types/chat.ts`.

---

## Appendix B — chat `action` values

| action | When |
|---|---|
| `csv_uploaded` | After `POST /api/v2/sales-data/upload` |
| `CONFIRM_CSV_SUBMISSION` | Review Submit |
| `confirm_period_mismatch` / `cancel_period_mismatch` | Month mismatch card |
| `proceed_other_months` / `switch_pending_month` | Reminder card |
| `confirm_manual_entry` / `cancel_manual_entry` | Manual card |
| `doc_uploaded` | After supporting-doc upload API |
| `confirm_supporting_doc` / `cancel_supporting_doc` | Doc confirm (comment required) |
| `confirm_admin_action` / `cancel_admin` | Admin confirm |
| `cancel` | Generic cancel |

Always send a non-empty `message` when `action` is set. **Exception:** `message` may be `""` when `active_notification_id` or `sr_id` is present (Open / open-existing-SR).

---

## Appendix C — CSV templates

**Official Cenomi columns** (accepted):

```text
Lease code, Brand, Property, Start date(DD-MM-YYYY), End date(DD-MM-YYYY), Sales amount
```

**Legacy** (still accepted):

```text
lease_code, sales_period_start, sales_period_end, sales_amt
```

Brand and Property optional on upload. Internal dates after parse: `YYYY-MM-DD`.

---

## Appendix D — key source files

| Area | Path |
|---|---|
| HTTP client | `backend/app/agents/services/sales_data_api_client.py` |
| Chat route / `ActiveNotificationRef` | `backend/app/api/routes/chat.py` |
| CSV upload | `backend/app/api/routes/sales_data_upload.py` |
| Open notification (sales + handover) | `backend/app/agents/graph/nodes/notification/open_notification_node.py` |
| Sales-data Open helpers | `backend/app/agents/services/notification_sales_data.py` |
| Inbox list + mock | `backend/app/agents/services/notification_inbox_service.py` |
| List row serializer | `backend/app/agents/services/notification_payload.py` |
| Graph routing after Open | `backend/app/agents/graph/help_agent_graph.py` |
| Sales-data entry | `backend/app/agents/graph/nodes/sales_data/sales_data_entry_node.py` |
| Pending months | `.../pending_months.py`, `pending_months_gate_node.py` |
| RBAC | `backend/app/agents/schemas/help_agent_schema.py` |
| Demo chat wiring | `sales-data-agent/components/chatbot/SalesDataChat.tsx` |
| Demo types | `sales-data-agent/lib/types/chat.ts` |
| **Frontend tickets (share this)** | [`frontend-sales-data-notification-agent.md`](./frontend-sales-data-notification-agent.md) |
| Portal notification nav | `cenomi-frontend/libs/ui-web/src/lib/utils/Util.ts` |
| Portal chat turn | `cenomi-frontend/libs/api/src/lib/srChatbot/srChatbotApi.ts` |
| AI token mint | `cenomi-api/cenomi_ai/usecase/cenomi_ai_usecase.go` |
| Sales notifications (create) | `cenomi-api/sales_data/usecase/sales_data_usecase.go` |
| Notification Agent spec | `cenomi-api-v2/docs/specs/3520-notification-agent-integration.md` |

---

## Appendix E — common failures

| Symptom | Likely cause |
|---|---|
| “I can help with platform questions” / no sales routing | JWT `roles` missing platform sales roles (`BILLING_CONTACT`, `SALES_DATA_TEAM`, …) and demo aliases |
| `401` on GET `/sales-data` from AI logs | Bearer not forwarded, or AI JWT rejected by `cenomi-api` |
| GET 200 empty list, listing page has rows | Tenant profile / AI JWT scope mismatch |
| Upload then “please re-upload” | `csv_session_id` not sent, or in-memory cache lost (reload/replica) |
| PATCH 400 invalid record | Wrong identity tuple or row no longer PENDING/NEED_* |
| Notification opens listing only | `handleNotificationNavigation` not sending `active_notification_id` |
| Open lands in handover / “couldn't find SR” | Sent `notification_id` or an SR number as `id`; sales Open must be the **lease code** |
| Inbox has no Sales Data group (live) | Migration `1787900000` not applied, or user role outside tenant company / `SALES_DATA_TEAM` audience |
| Inbox has no Sales Data group (tenant JWT) | Audience skip (should include platform names); or backfill `sr_sub_category` not applied |
| Brand mismatch on correct mall | GET row uses a field alias the client does not read — add it in `canonical_*` |
| CORS on chat from Tenant Central | `CORS_ORIGINS` missing portal origin |
| Token mint `ErrAISessionNoChatEntitlement` | Role lacks `HELP_AGENT` (or Super Admin) |
