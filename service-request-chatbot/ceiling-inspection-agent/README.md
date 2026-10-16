# Ceiling Inspection Agent — Demo Frontend

A small Next.js demo for `rdd_ceiling_inspection_agent` (Open / Close Ceiling
Inspection). Runs on **port 3002** and talks only to the AI backend (port 8000).
Built the same way as `sales-data-agent/`.

```
ceiling-inspection-agent/  (port 3002)
    ↕  POST /api/chat/service-request, POST /api/v1/upload,
    ↕  GET /api/v1/lookups/*, GET /api/v1/notifications/inbox
backend/  AI backend (port 8000) — LangGraph, in-memory mock Service Request API
```

The screen is split like the platform: the **Open / Close Ceiling Inspection
form** in the centre and the **assistant** docked on the right.

- **Chat → form.** Every turn returns `form_state`; the form fills as the agent
  collects fields, the field being asked gets a gold ring, and the form locks
  once the SR is submitted (review and acknowledgement happen in the chat).
- **Form → chat.** Picking the inspection type and a lease starts the agent.
  After that, a lease change is sent as `selected_lease_id`, and a text or date
  edit goes back silently as `corrected_fields` when you leave the field.
- **Dropdowns list only what you can access.** Company and Lease / Brand / Mall
  come from `/api/v1/lookups/*`, scoped by your JWT's properties. The demo
  tenant has two companies (Under Armour, Nike).
- **Notifications.** The bell shows your action-required inspections. In mock
  mode the backend records one each time the SR moves to a new party: create →
  RDD Project Manager, level 1 → FM Manager, level 2 → tenant. Opening one
  loads the SR into both the form and the chat.

## Run it

1. **AI backend in mock mode** — add to `backend/.env`:

   ```
   SERVICE_REQUEST_USE_MOCK=true
   LEASE_LOOKUP_USE_MOCK=true
   NOTIFICATIONS_USE_MOCK=true
   CORS_ORIGINS=http://localhost:3000,http://localhost:3002
   ```

   ```bash
   cd ../backend
   python scripts/seed_users.py          # adds the demo users below (re-run after pulling: it updates them)
   uvicorn app.main:app --reload --port 8000
   ```

2. **This app**

   ```bash
   cp .env.example .env.local
   npm install
   npm run dev                            # http://localhost:3002
   ```

## Demo script

Sign in as each actor in turn (**Switch role** in the header). Every user's
password is `test1234`.

| Step | Role | User | What to do |
|---|---|---|---|
| 1 | Tenant (AUTHORIZED_SIGNATORY) | tenant.signatory@cenomi.com | In the form pick **Open Ceiling Inspection**, company **Nike**, a lease → the assistant starts. Type the title in the form, answer the rest in chat (or the form) → **Confirm & Submit** |
| 2 | RDD Project Manager (DD_ENGINEER) | sara@cenomi.com | Bell → open the request (form + chat) → upload the report (any PDF) → Approved / Approved with changes → **Confirm** |
| 3 | FM Manager | khalid@cenomi.com | Bell → open → **Confirm** (approve) → the SR becomes `REPORT_SUBMITTED` |
| 4 | Tenant (same user as step 1) | tenant.signatory@cenomi.com | Bell → open → **Acknowledge report** → complete; every bell is empty |

Also worth showing:

- "Raise a ceiling inspection for lease t0105712" — the agent asks Open or Close, it never guesses.
- Sign in as the RDD Project Manager and try to raise one — refused (no `CAN_RAISE_HANDOVER_SR`).
- Whether a Mall Manager may also raise it is still open. The agent's RBAC allows it (PRD §3.2);
  the demo only uses the tenant.
- Open the SR as the FM Manager before level 1 is done — the agent says it is with the RDD Project Manager.
- **Ask for more information** at either level — the SR goes back as `ACTION_REQUIRED`.

## Notes

- **My requests** is kept in this browser's localStorage: the ids of requests
  raised here. It is not a platform list.
- Mock SRs and mock notifications live in the AI backend's memory — restarting
  it clears both; use **Clear** on the list to match.
- The ceiling notification rows are mock-only. The platform sends ceiling
  notifications (`notifyActionOnHandoverInspectionSR`), but it has no
  `/notifications/agent/*` tool for them yet, so the live inbox adapter skips
  these sub-categories.
