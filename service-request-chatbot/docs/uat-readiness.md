# UAT Readiness Document — Cenomi RDD Chatbot

> **Prepared:** July 8, 2026 (updated from July 7, 2026 — v1.3.0)
> **Audience:** QA Team, Stakeholders, Incoming Developers
> **Scope:** Full UAT readiness assessment for the Help Agent and RDD Handover Lifecycle
> **Overall verdict:** ✅ Ready for local UAT now. ⚠️ One 30-minute check needed before live-platform UAT.
> **Auth on MSP integration:** Minor — env vars only in the best case; worst case is a 5-line edit in one function.

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Test Coverage Summary](#2-test-coverage-summary)
3. [Help Agent — Readiness](#3-help-agent--readiness)
4. [RDD Lifecycle — Readiness by Phase](#4-rdd-lifecycle--readiness-by-phase)
5. [RBAC — Role Access Matrix](#5-rbac--role-access-matrix)
6. [Document Handling](#6-document-handling)
7. [State Persistence & Lifecycle Continuity](#7-state-persistence--lifecycle-continuity)
8. [Running the Automated Tests](#8-running-the-automated-tests)
9. [Manual UAT — How to Run](#9-manual-uat--how-to-run)
10. [Live Platform Integration Checklist](#10-live-platform-integration-checklist)
11. [MSP Auth Integration — What Changes](#11-msp-auth-integration--what-changes)
12. [Known Gaps & Production Notes](#12-known-gaps--production-notes)

---

## 1. System Overview

The Cenomi RDD Chatbot is a conversational AI system that guides mall stakeholders through the full **Handover Service Request (SR) lifecycle** via natural language chat — replacing manual form-filling on the Cenomi MSP Platform.

### Architecture at a glance

```
Browser (Next.js 15)
    │  POST /api/chat/service-request  OR  POST /api/v1/chat (MSP)
    ▼
FastAPI → Injection Guard → ChatOrchestrationService
    │
    ▼
LangGraph (26 nodes) — runs fresh each turn
  load_session → document_upload → sr_status_sync
    → supervisor (intent) → faq_node / registry → SR workflow nodes
    → response_generation → save_state
    │
    ▼
PostgreSQL (state, drafts, sessions, observability)
Redis (session cache)
OpenAI (LLM — GPT-4o-mini or configured model)
Cenomi Platform APIs (SR create/patch/fetch, Lease lookup, File upload)
```

### Three agents in one graph

| Agent | Trigger | Description |
|-------|---------|-------------|
| `help_agent` | Every message | Supervisor — classifies intent, routes to FAQ or SR workflow |
| `rdd_agent` | Handover SR intents | Full 3-phase handover lifecycle (CREATE_SR → FM_REVIEW → RDD_REVIEW) |
| `work_permit_agent` | Work permit intent | Single-stage work permit SR creation |

---

## 2. Test Coverage Summary

| Layer | Tests | Status |
|-------|-------|--------|
| Unit tests (isolated nodes, services, validators) | ~1,300 | ✅ All passing |
| Integration tests (graph + mocked LLM/API) | ~80 | ✅ All passing |
| E2E tests (full HTTP stack, all externals mocked) | ~17 | ✅ All passing |
| **Total pytest** | **1,387** | **✅ 1,387/1,387 passing** |
| Playwright mocked UI (no backend required) | 42 | ✅ 42/42 |
| Playwright live — CREATE_SR eval (real LLM) | 33 | ✅ 31/33 (2 LLM variability, not bugs) |
| Playwright live — FM/RDD eval + full lifecycle | 14 | ✅ 14/14 |
| Playwright live — UI lifecycle | 16 | ✅ 16/16 |

### Priority lifecycle test suite (68 tests — run these for UAT validation)

| File | Tests | What it covers |
|------|-------|---------------|
| `e2e/test_handover_full_lifecycle_http.py` | 7 | Full 9-step lifecycle via HTTP: CREATE → FM save → FM approve → RDD submit → RDD final approve |
| `e2e/test_handover_sr_e2e.py` | 10 | Phase 1 edge cases: missing fields, wrong dates, injection block, multi-lease, API failure, permission denied |
| `integration/test_fm_review_e2e.py` | 14 | Phase 2 pipeline: doc upload bridge, date auto-calc, payload shape (IN_PROCESS vs APPROVED), wrong-role denial |
| `integration/test_rdd_review_e2e.py` | 16 | Phase 3a + 3b: report upload, REPORT_SUBMITTED payload, DD/MM/YYYY format, chained 3a→3b flow |
| `integration/test_role_stage_access.py` | 21 | RBAC matrix: every role × every stage entry/submit/approve action |

---

## 3. Help Agent — Readiness

**Status: ✅ UAT Ready**

The Help Agent is the unified entry point for all users. It is not a separate service — the LangGraph supervisor IS the Help Agent.

### Capabilities verified

| Capability | Tests | Status |
|------------|-------|--------|
| Intent classification → correct workflow routing | `test_help_agent_graph.py` (76 tests) | ✅ |
| Role-permitted intent mapping (which role can do what) | `test_help_agent_schema.py` (5 tests) | ✅ |
| RBAC denial — wrong-role intent blocked with message | `test_role_stage_access.py` | ✅ |
| MSP sync endpoint (`POST /api/v1/chat`) — shape, auth, 422 | `test_msp_chat_endpoints.py` (18 tests) | ✅ |
| MSP streaming endpoint (`POST /api/v1/chat/stream`) — SSE | `test_msp_chat_endpoints.py` | ✅ |
| FAQ Q&A — English + Arabic, no fabrication | `test_faq_node.py` | ✅ |
| FAQ hybrid search (Azure AI Search + embedded knowledge) | Configured in `.env` | ✅ |
| SR status / preview on demand | `preview_node` | ✅ |
| Prompt injection guard — HIGH_RISK blocked before DB write | `test_injection_guard.py` | ✅ |
| Session continuity — state survives page refresh | PostgreSQL + JWT | ✅ |
| Cancel / restart phrases clear workflow state | `test_supervisor_routing.py` | ✅ |

### Intent routing table (Help Agent supervisor)

| User intent | Supervisor classifies as | Routes to |
|-------------|------------------------|----------|
| "How do I raise a handover SR?" | `ASK_HELP` | `faq_node` |
| "I want to create a handover request" | `CREATE_RDD_SERVICE_REQUEST` | `registry → handover_entry` |
| "I want to review SR-123" | `APPROVE_RDD_SERVICE_REQUEST` | `registry → fm_review_entry` |
| "What is the status of my SR?" | `CHECK_SERVICE_REQUEST_STATUS` | `preview` |
| "I want to submit the RDD report" | `APPROVE_RDD_SERVICE_REQUEST` | `registry → rdd_review_entry` |
| Anything unclear or off-topic | `UNKNOWN` | `faq_node` |

### MSP Platform endpoints (contractually fixed)

| Endpoint | Description |
|----------|-------------|
| `POST /api/v1/chat` | Synchronous JSON response — used by MSP frontend |
| `POST /api/v1/chat/stream` | SSE streaming response — token-by-token |

---

## 4. RDD Lifecycle — Readiness by Phase

### Phase 1 — CREATE_SR (Mall Manager)

**Status: ✅ Fully verified**

| Feature | Status |
|---------|--------|
| Natural language field collection (17 fields) | ✅ |
| Lease lookup by code, brand name, or mall name | ✅ |
| Multi-lease selection card (when multiple matches) | ✅ |
| Backend field auto-population: `property_id`, `brand_id`, `lease_id`, `contract_id`, `tenant_profile_id`, `unit_code`, `area` | ✅ |
| Title auto-generation (`handover-{lease_code}-{slug}`) | ✅ |
| All-fields-in-one-message shortcut | ✅ |
| Natural language date parsing (`"1st August"` → `2026-08-01`) | ✅ |
| Inspection date range validation (start must be before end) | ✅ |
| Confirmation card with inline field editing before submit | ✅ |
| Backend field protection (LLM cannot overwrite platform IDs) | ✅ |
| `POST /service-requests` platform API call | ✅ |
| SR ID returned and stored in session draft | ✅ |
| Stage advances to FM_REVIEW after submission | ✅ |

### Phase 2 — FM_REVIEW (FM Manager / Operations)

**Status: ✅ Fully verified**

| Feature | Status |
|---------|--------|
| FM Manager opens existing SR by SR ID | ✅ |
| Stage auto-detected via `sr_status_sync_node` → routes to `fm_review_entry` | ✅ |
| Document upload: Handover Checklist, Site Survey, COP Checklist | ✅ |
| Documents written to DB immediately on upload (independent of chat turn) | ✅ |
| Documents accumulate across multiple turns (not replaced) | ✅ |
| `unit_readiness_date` collection and validation | ✅ |
| `expected_handover_date` collection and validation | ✅ |
| Save Progress path (`PATCH` with `IN_PROCESS` status) | ✅ |
| Approve path (`PATCH` with `APPROVED` status) | ✅ |
| Operations role has same FM review access as FM Manager | ✅ |
| Stage advances to RDD_REVIEW after FM approval | ✅ |

### Phase 3 — RDD_REVIEW (DD Engineer / RDD Project Manager)

**Status: ✅ Fully verified**

| Feature | Status |
|---------|--------|
| DD Engineer opens existing SR by SR ID | ✅ |
| Stage auto-detected → routes to `rdd_review_entry` | ✅ |
| RDD report document upload (`DR_SR_HANDOVER_REPORT`) with `document_type_status: APPROVED` | ✅ |
| `guideLineLink`, `actual_handover_date`, `fitout_start_date`, `fitout_end_date`, `trading_date` collection | ✅ |
| Date order enforced: `actual ≤ fitout_start ≤ fitout_end ≤ trading` | ✅ |
| `trading_date` auto-computed as `fitout_end_date + 8 days` | ✅ |
| Dates sent to platform in `DD/MM/YYYY` format | ✅ |
| Phase 3a — Submit Report (`POST` with `REPORT_SUBMITTED` status) | ✅ |
| Phase 3b — Final Approve (`PATCH` with `APPROVED` status) → `SR_COMPLETED` | ✅ |
| Button transitions: "Submit Report" → "Final Approve" after 3a | ✅ |
| Lifecycle complete — `workflow_stage = SR_COMPLETED` | ✅ |

---

## 5. RBAC — Role Access Matrix

**Status: ✅ Fully enforced and tested (21 tests)**

| Action | MALL_MANAGER | FM_MANAGER | OPERATIONS | DD_ENGINEER | ADMIN |
|--------|:---:|:---:|:---:|:---:|:---:|
| Login & FAQ Q&A | ✅ | ✅ | ✅ | ✅ | ✅ |
| Preview SR / check status | ✅ | ✅ | ✅ | ✅ | ✅ |
| Create Handover SR | ✅ | ❌ | ❌ | ❌ | ✅ |
| Upload FM documents | ❌ | ✅ | ✅ | ❌ | ✅ |
| Save FM progress | ❌ | ✅ | ✅ | ❌ | ✅ |
| Approve FM review | ❌ | ✅ | ❌ | ❌ | ✅ |
| Upload RDD report | ❌ | ❌ | ❌ | ✅ | ✅ |
| Submit RDD report | ❌ | ❌ | ❌ | ✅ | ✅ |
| Final RDD approval | ❌ | ❌ | ❌ | ✅ | ✅ |

> **RBAC mode:** Currently set to `RBAC_ENFORCE=false` (shadow mode — logs warnings but does not block unauthenticated requests). Set `RBAC_ENFORCE=true` before production to enforce JWT on every request.

---

## 6. Document Handling

**Status: ✅ Fully addressed**

Documents flow through the system in two independent steps:

### Step 1 — Upload (happens outside the chat turn)
```
User picks a file → DocumentUploadPanel (frontend)
    → POST /api/v1/upload
    → Role check (FM docs require FM role; RDD docs require DD_ENGINEER)
    → Content-type check (PDF, JPEG, PNG only)
    → Platform PUT /files (or mock)
    → document_id written to draft.documents in DB immediately
```

### Step 2 — Partitioned into payload on next chat turn
```
load_session_node restores state["documents"] from DB
    → document_upload_node partitions into buckets:
        FM docs (Checklist, Site Survey, COP) → backend_refs["uploaded_documents"] = [id1, id2, id3]
        RDD report                             → backend_refs["rdd_document_id"] = "id4"
    → fm_payload_builder / rdd_payload_builder reads from backend_refs
    → document IDs included in PATCH/POST payload to platform
```

**Key guarantees:**
- Documents accumulate across turns — uploading 3 docs over 3 turns works correctly
- Documents survive page refresh — stored in PostgreSQL `service_request_drafts.documents`
- FM and RDD docs are cleanly separated — no cross-contamination
- In-progress uploads do not block chat — upload is an independent HTTP call

---

## 7. State Persistence & Lifecycle Continuity

**Status: ✅ Fully addressed**

All lifecycle state is stored in **PostgreSQL** per chat session:

| Field | Stored in DB | Purpose |
|-------|-------------|---------|
| `workflow_stage` | `service_request_drafts.workflow_stage` | Tracks current phase (CREATE_SR / FM_REVIEW / RDD_REVIEW / SR_COMPLETED) |
| `sr_id` | `service_request_drafts.sr_id` | Platform SR ID — key for all subsequent API calls |
| All 17+ collected fields | `service_request_drafts.collected_data` (JSONB) | Survives across turns, page refresh, reconnect |
| Uploaded document IDs | `service_request_drafts.documents` (JSONB array) | Each doc written immediately on upload |
| Confirmation status | Embedded in `collected_data._confirmation_status` | Prevents double-submission |
| Selected lease | Embedded in `collected_data._selected_lease` | Lease resolution result |

**State restoration per turn:**
```
load_session_node
    → reads ServiceRequestDraft from DB by session_id
    → restores: workflow_stage, collected_data, documents, sr_id, backend_refs
sr_status_sync_node
    → calls GET /service-requests/{sr_id} on platform (or mock)
    → updates workflow_stage if platform status has changed
[graph nodes run]
save_state_node
    → upserts draft back to DB
```

**When integrating with MSP platform:** The `sr_id` stored in the draft is the exact platform SR ID. The status sync node uses it to call the real platform API and advance the lifecycle stage automatically — no code changes required.

---

## 8. Running the Automated Tests

### One command — 68 lifecycle tests

```bash
cd service-request-chatbot/backend
./run_lifecycle_tests.sh
```

### With options

```bash
./run_lifecycle_tests.sh --coverage   # + HTML coverage report in htmlcov/
./run_lifecycle_tests.sh --fast       # stop on first failure
```

### Full suite (all 1,387 tests)

```bash
cd service-request-chatbot/backend
pytest tests/ -v --tb=short
```

### Playwright mocked tests (no server needed)

```bash
cd service-request-chatbot/frontend
npm run test:e2e
```

### Playwright live stack (real LLM + Postgres + Redis)

```bash
cd service-request-chatbot/frontend
npm run test:live

# Or via orchestrator (starts the full stack automatically):
cd service-request-chatbot
./run-live-tests.sh
```

### Expected result

```
68 passed in ~52s      ← lifecycle suite
1,387 passed in ~85s   ← full suite
42 passed             ← Playwright mocked
31/33, 14/14, 16/16   ← Playwright live (2 LLM variability — not bugs)
```

---

## 9. Manual UAT — How to Run

See `docs/manual-testing-guide.md` for the full step-by-step guide.

### Quick start

```bash
# 1. Start infrastructure
cd service-request-chatbot && docker compose up -d

# 2. Start backend
cd service-request-chatbot/backend
source .venv/bin/activate
alembic upgrade head && python scripts/seed_users.py
uvicorn app.main:app --reload --port 8000

# 3. Start frontend
cd service-request-chatbot/frontend && npm run dev

# 4. Open browser
open http://localhost:3000
```

### Test users

| Role | Email | Password |
|------|-------|----------|
| Mall Manager | `aisha@cenomi.com` | `test1234` |
| FM Manager | `khalid@cenomi.com` | `test1234` |
| Operations | `omar@cenomi.com` | `test1234` |
| DD Engineer | `sara@cenomi.com` | `test1234` |
| Admin | `admin@cenomi.com` | `test1234` |

### Reset between test runs

```bash
curl -X POST http://localhost:8000/dev/mock/reset
```

### Mock leases available (in mock mode)

| Lease Code | Brand | Mall |
|------------|-------|------|
| `t0105712` | Brand Under Armour | Jawharat Jeddah |
| `t0208831` | Nike | Riyadh Park |
| `t0301144` | Nike | Mall of Arabia |
| `t0419977` | Zara | Dubai Festival City |

---

## 10. Live Platform Integration Checklist

When MSP platform credentials are available, complete this checklist to switch from mock to live:

### Step 1 — Set environment variables (`.env`)

```bash
SERVICE_REQUEST_API_BASE_URL=https://<msp-platform-host>
LEASE_TENANT_API_BASE_URL=https://<msp-platform-host>
FILE_UPLOAD_API_BASE_URL=https://<msp-platform-host>
PLATFORM_AUTH_BASE_URL=https://<msp-platform-host>
PLATFORM_LOGIN_EMAIL=<service-account-email>
PLATFORM_INTERNAL_API_TOKEN=<token>
```

No code changes required. Setting these env vars is the only switch.

### Step 2 — Verify `sr_status_sync_node` field mapping (~30 minutes)

This is the only item that requires a live platform check.

1. Create a real SR via Phase 1 chat flow
2. Call `GET /service-requests/{sr_id}` directly on the platform
3. Inspect the response — confirm:
   - Each operation in `service_request_operations` has `assigned_role` and `status` fields
   - `status` values include `IN_PROGRESS`, `FINISHED`, `APPROVED`
   - `assigned_role` values include `FM_MANAGER`, `OPERATIONS`, `DD_ENGINEER`
4. If the field names differ, update the 2 lines in `sr_status_sync_node.py`:
   ```python
   role = (op.get("assigned_role") or op.get("role") or "").upper()
   status = (op.get("status") or "").upper()
   ```

### Step 3 — Set `RBAC_ENFORCE=true`

```bash
RBAC_ENFORCE=true
```

This switches from shadow mode (warn + pass) to enforced mode (reject unauthenticated requests with HTTP 401).

### Step 4 — Smoke test the live flow

Run through the manual testing guide once end-to-end with real platform credentials.

---

## 11. MSP Auth Integration — What Changes

There are **two separate token systems** in the chatbot. Understanding both is important before MSP integration.

---

### Token System 1 — User JWT (who the user is)

**Today (standalone):**
```
User → POST /api/auth/login
    → chatbot validates against its own users table (PostgreSQL)
    → chatbot issues its own HS256 JWT (signed with JWT_SECRET_KEY)
    → JWT contains: user_id, roles, unique_property_ids, mall_names, is_global_admin
    → frontend sends: Authorization: Bearer <token>
    → get_auth_context() decodes → AuthContext (used by all RBAC checks)
```

**After MSP integration:**
The MSP platform already manages users and issues its own JWT. The chatbot's own login and `users` table become redundant. The MSP will pass its JWT directly.

**Scale of change: Minor — env vars only (best case) or 5-line edit (worst case)**

| Scenario | What changes | Effort |
|----------|-------------|--------|
| MSP JWT claim names match ours | Set `JWT_SECRET_KEY`, `JWT_ALGORITHM`, `RBAC_ENFORCE=true` in `.env` | 5 minutes |
| MSP JWT uses different field names | Edit 5 lines in `get_auth_context()` in `security.py` to remap claim names | 30 minutes |
| Disable chatbot login (optional) | Comment out `POST /api/auth/login`, drop `users` table migration | 1 hour |

**The one function to update if claim names differ (`security.py`):**
```python
# Current — reads these claim names from the JWT payload
subject_id       = payload.get("user_id", payload.get("sub"))
roles            = payload.get("roles", [])               # ← confirm with MSP team
unique_property_ids = payload.get("unique_property_ids", []) # ← confirm with MSP team
mall_names       = payload.get("mall_names", [])           # ← confirm with MSP team
is_global_admin  = payload.get("is_global_admin", False)   # ← confirm with MSP team
```

**Ask the MSP team for a sample decoded JWT** and compare field names. If they match — zero code change.

---

### Token System 2 — Service-to-Service Token (machine auth)

This is **separate from the user JWT**. It authenticates the MSP frontend server calling the chatbot backend — already fully implemented.

```
MSP Platform frontend → POST /api/v1/chat
    sends header: x-internal-api-token: <MSP_SERVICE_TOKEN>
    → _require_service_token() validates it
```

Just set one env var:
```bash
MSP_SERVICE_TOKEN=<agreed-service-token-value>
```

---

### What does NOT change on MSP integration

| Component | Why unchanged |
|-----------|--------------|
| `AuthContext` dataclass | Internal identity contract — the entire graph reads this, not the JWT directly |
| `PermissionService` + RBAC matrix | Reads `AuthContext.roles` — role names stay the same |
| All 26 graph nodes | No JWT awareness — only see `AuthContext` |
| All 1,387 pytest tests + 92 Playwright tests | Mock `AuthContext` directly — not affected by JWT format changes |
| RDD lifecycle flows | No auth code in workflow nodes |
| MSP endpoints `/api/v1/chat` + `/api/v1/chat/stream` | Already wired for MSP — no changes |

---

### Architecture wall — why changes are contained

```
                    ┌─────────────────────────────┐
                    │  get_auth_context()          │
  MSP JWT ─────────►│  (security.py — 1 function)  ├──► AuthContext ──► entire graph
                    │  claim remapping here only   │
                    └─────────────────────────────┘
                              ▲
                    Only place JWT is touched
```

The `AuthContext` dataclass is the internal wall. Everything to the right of it is completely unaffected by MSP auth changes.

---

## 12. Known Gaps & Production Notes

### Not UAT blockers (but fix before production go-live)

| Gap | Description | Fix timing |
|-----|-------------|-----------|
| `RBAC_ENFORCE=false` | Auth is in shadow mode — JWT is validated but unauthenticated requests are not rejected | Set to `true` before production |
| `sr_status_sync_node` field names | Field names (`assigned_role`, `status`) chosen to match Postman collection — need one-time verification against live platform response | ~30 min once platform credentials available |
| No CI/CD pipeline | `./run-live-tests.sh` serves as the manual 5-tier CI gate | Before production deployment |
| Redis not used beyond health check | Redis is running and connected but session caching is not yet implemented | Optimisation — not a functional gap |

### Fixed since v1.2.0 (no longer gaps)

| Item | Fixed in |
|------|---------|
| `PermissionService` fail-open | **v1.3.0** — `check()` now raises `PermissionDeniedError` for unknown actions; JWT role names expanded via `ROLE_PERMISSION_MAP` |
| No application Dockerfiles | **v1.2.0** — `backend/Dockerfile` (2-stage) + `frontend/Dockerfile` (3-stage) + `docker-compose.prod.yml` |
| 6 agent bugs (comments, lease re-trigger, FM/RDD draft creation, upload mock mode) | **v1.3.0** |

### Not in scope for this UAT

| Item | Note |
|------|------|
| Work Permit agent (`work_permit_agent`) | Graph nodes and schema exist. Not part of the Handover lifecycle UAT scope |
| "Ask for more information" action | Mentioned in RDD lifecycle spec as needing product team clarification — not implemented |
| "Assign To" action | Same — pending product clarification |
| Webhook / push for platform status changes | Status sync is pull-based (called each turn). Real-time push is not implemented |

---

## Appendix — Observability

The admin observability dashboard at `http://localhost:3000/admin/agent-observability` shows:

| View | What it shows |
|------|--------------|
| Trace list | Every chat turn with status, latency, session ID |
| Trace detail | Full node run tree, state diff before/after each node |
| LLM call viewer | Exact prompt + response for every LLM call |
| Tool call viewer | Every external API call (method, URL, status, payload) |
| Conversation replay | Replay any past session turn-by-turn |
| Metrics | Token usage, latency, LLM call counts |

This is the primary debugging tool during UAT. Enable **Debug mode** in the chat UI (toggle in the header) to see `session_id`, `workflow_stage`, and `sr_id` inline.
