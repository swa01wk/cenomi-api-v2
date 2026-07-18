# cenomi-api-v2

A monorepo for the **Cenomi Service Request Chatbot** — a conversational AI platform that lets mall tenants submit and manage service requests (Handover/RDD, Work Permit) through natural language, backed by a LangGraph-orchestrated FastAPI service and a Next.js 15 admin/chat frontend.

> **Design principle:** LLMs understand and propose — application code validates, authorises, persists, and submits.

---

## Repository layout

```
cenomi-api-v2/
├── service-request-chatbot/          Main application (backend + frontend + docs)
│   ├── backend/                      FastAPI + LangGraph Python 3.11 API
│   │   ├── Dockerfile                Production multi-stage image
│   │   └── run_lifecycle_tests.sh    68-test lifecycle test runner
│   ├── frontend/                     Next.js 15 (App Router) chat & admin UI
│   │   └── Dockerfile                Production standalone image
│   ├── docs/                         24 architecture, API, agent, testing, and ops documents
│   ├── docker-compose.yml            PostgreSQL 16 + Redis 7 for local dev
│   └── docker-compose.prod.yml       Full production stack (app + infra)
│
├── REPORT.md                         UAT readiness report (Help Agent + RDD lifecycle)
├── HANDOVER.md                       Full developer handover document
├── STAKEHOLDER_REPORT.md             Stakeholder-facing project report
└── RELEASE_NOTES.md                  Version history and change log
```

For the full application README (quick-start, API reference, env vars, feature list), see [`service-request-chatbot/README.md`](service-request-chatbot/README.md).

---

## Technology stack

| Layer | Technologies |
|-------|-------------|
| Backend runtime | Python 3.11+, FastAPI, Uvicorn, Pydantic v2 |
| Agent / LLM | LangGraph, OpenAI SDK (OpenAI-compatible endpoints) |
| Database | PostgreSQL 16, SQLAlchemy 2 async + asyncpg, Alembic |
| Cache | Redis 7 |
| Frontend | Next.js 15.3, React 19, TypeScript 5, Tailwind CSS 3 |
| Dev tooling | pytest, pytest-asyncio, ruff, mypy, ESLint |
| Infrastructure | Docker Compose (dev: Postgres + Redis), production Dockerfiles for backend + frontend |

---

## Architecture overview

```
Browser (Next.js 15)
  └─▶ POST /api/chat/service-request
        └─▶ ChatOrchestrationService.process_turn
              ├─ Session load / injection scan
              ├─ LangGraph graph.ainvoke (stateless per request)
              │    supervisor → handover/registry → extraction → merge
              │    → lease lookup → validation → confirmation
              │    → payload build → SR API submission → response gen
              │    → save state
              └─ TraceManager → Postgres (sessions, drafts, messages, traces)
```

**State strategy:** The graph is stateless per HTTP turn; all conversation state is persisted to and restored from Postgres via `ConversationStateService`.

---

## Quick start

### Prerequisites

- Docker (for Postgres + Redis)
- Python 3.11+
- Node.js 18+

### 1 — Infrastructure

```bash
cd service-request-chatbot
docker compose up -d        # Postgres 16 on :5432, Redis 7 on :6379
```

### 2 — Backend

```bash
cd service-request-chatbot/backend
python3.11 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install --upgrade pip
pip install -e ".[dev]"
cp .env.example .env        # fill in OPENAI_API_KEY at minimum
alembic upgrade head        # run all DB migrations
python scripts/seed_users.py   # optional: seed role-based test users
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

| URL | Purpose |
|-----|---------|
| `http://localhost:8000/docs` | Interactive API docs (Swagger) |
| `http://localhost:8000/redoc` | ReDoc API reference |
| `http://localhost:8000/api/v1/health` | Health check |

### 3 — Frontend

```bash
cd service-request-chatbot/frontend
cp .env.example .env.local  # defaults to http://localhost:8000
npm install
npm run dev
```

| URL | Purpose |
|-----|---------|
| `http://localhost:3000/login` | Login (test users seeded above) |
| `http://localhost:3000/service-request-chat` | Tenant chat interface |
| `http://localhost:3000/admin/agent-observability` | Trace explorer (admin) |

### 4 — Tests

```bash
cd service-request-chatbot/backend

# Full pytest suite (1,387 tests — unit + integration + mocked E2E)
pytest

# Priority lifecycle suite only (68 tests — fastest UAT gate)
./run_lifecycle_tests.sh

# With coverage
./run_lifecycle_tests.sh --coverage
```

#### Playwright E2E tests (frontend)

```bash
cd service-request-chatbot/frontend

# Mocked tests — no server needed (42 tests)
npm run test:e2e

# Live stack tests — requires running backend + frontend (50 tests, real LLM)
npm run test:live

# Full live suite via orchestrator (starts stack automatically)
cd service-request-chatbot
./run-live-tests.sh
```

#### Agent evaluation (47 scenarios)

```bash
cd service-request-chatbot/backend

# All 33 CREATE_SR scenarios (real LLM)
python -m tests.eval.run_eval --verbose

# FM_REVIEW + RDD_REVIEW + full lifecycle scenarios (S34–S47)
python -m tests.eval.run_lifecycle_eval --verbose

# Export session observability for evaluation
python scripts/export_session_observability.py --recent 5 --eval
```

---

## Key environment variables

### Backend (`backend/.env`)

| Variable | Required | Default / notes |
|----------|----------|-----------------|
| `DATABASE_URL` | Yes | `postgresql+asyncpg://postgres:postgres@localhost:5432/service_request_chatbot` |
| `OPENAI_API_KEY` | Yes (prod) | LLM API key |
| `REDIS_URL` | No | `redis://localhost:6379/0` |
| `JWT_SECRET_KEY` | Prod | Change before deploying |
| `RBAC_ENFORCE` | No | `false` — shadow/anonymous mode for local dev |
| `SERVICE_REQUEST_API_BASE_URL` | No | Leave empty for mock mode |
| `LEASE_TENANT_API_BASE_URL` | No | Leave empty for mock mode |
| `LLM_MODEL` | No | `gpt-4o-mini` |
| `LLM_BASE_URL` | No | Override for Azure/OpenAI-compatible endpoints |
| `AZURE_SEARCH_ENDPOINT` / `AZURE_SEARCH_API_KEY` | RAG only | Azure AI Search integration |

See [`backend/.env.example`](service-request-chatbot/backend/.env.example) for the full list.

### Frontend (`frontend/.env.local`)

| Variable | Default |
|----------|---------|
| `NEXT_PUBLIC_API_BASE_URL` | `http://localhost:8000` |

---

## Key API endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/v1/health` | Liveness check |
| `GET` | `/api/v1/ready` | Readiness check (DB + Redis) |
| `POST` | `/api/auth/login` | JWT login |
| `GET` | `/api/auth/me` | Current user |
| `POST` | `/api/chat/service-request` | Primary chat turn endpoint |
| `POST` | `/api/v1/chat` | MSP help agent (sync JSON) |
| `POST` | `/api/v1/chat/stream` | MSP help agent (SSE pseudo-streaming) |
| `POST` | `/api/v1/upload` | File attachment upload |
| `GET` | `/api/observability/traces` | Agent trace list |
| `GET` | `/api/v1/observability/metrics/summary` | Aggregated metrics |

All routes are also available under the `/api/v2/` prefix.

---

## Test users (after seeding)

| Email | Role |
|-------|------|
| `aisha@cenomi.com` | `MALL_MANAGER` |
| `khalid@cenomi.com` | `FM_MANAGER` |
| `omar@cenomi.com` | `OPERATIONS` |
| `sara@cenomi.com` | `DD_ENGINEER` |
| `admin@cenomi.com` | `ADMIN` |

Default password for all seeded users: **`test1234`**

---

## Implemented features

- **Full RDD Handover lifecycle** — CREATE_SR → FM_REVIEW → RDD_REVIEW → SR_COMPLETED, all three phases tested (99 eval scenarios, 7 tiers, ~1,400 unit tests passing)
- **Work Permit SR flow** — conversational single-stage creation
- **MSP Help Agent** — LangGraph-backed supervisor + FAQ answering via Azure AI Search RAG, SSE streaming
- **Lease lookup** — external API or built-in stateful mock (e.g. lease `t0105712`)
- **Field extraction, validation, merge, and missing-field re-prompting**
- **Inspection time field** — `inspectionTime` collected and preserved through to ISO 8601 payload timestamp
- **Field intent disambiguation** — active-field remapping for all user fields (not just dates); prevents `inspection_done_by` from being written to `notes`
- **Confirmation gate** — keyword + action_override guard before any outbound SR call
- **Cancel SR workflow** — `cancel_sr` intent path with CANCEL_PENDING → platform PATCH `status=CANCELLED` → emits `cancelled=true`
- **Document upload** — platform `PUT /files` integration; FM + RDD document buckets tracked across turns; mock mode supported
- **MM optional upload** — offered only after SR submission and all required fields collected
- **SR status sync** — `sr_status_sync_node` auto-advances lifecycle stage from platform `service_request_operations`
- **UI contract signals** — `stage_complete`, `workflow_complete`, `cancelled` emitted in `response_ui` for frontend state binding
- **JWT authentication + RBAC** — shadow mode or fully enforced; role names expanded to permissions; 21 RBAC tests
- **Injection guard** — scans every inbound user message before DB write
- **Full observability** — per-turn traces, LLM/tool call records, state snapshots, diffs, metrics, user feedback
- **Session observability export** — `export_session_observability.py` produces structured JSON + Markdown reports with state transitions, field extraction timeline, LLM token usage, and eval scores
- **Admin trace explorer** — replay, LLM call drill-down, metrics dashboard
- **Production Docker images** — multi-stage Dockerfiles for backend (FastAPI) and frontend (Next.js standalone)
- **Playwright test suite** — 42 mocked UI tests + 50 live-stack tests covering all 3 lifecycle phases with real LLM calls

---

## Documentation

### Architecture & Design

| Document | Description |
|----------|-------------|
| [`service-request-chatbot/README.md`](service-request-chatbot/README.md) | Full application README |
| [`service-request-chatbot/docs/architecture.md`](service-request-chatbot/docs/architecture.md) | System design, DB schema, integrations |
| [`service-request-chatbot/docs/agent-design-complete.md`](service-request-chatbot/docs/agent-design-complete.md) | Authoritative LangGraph agent design reference |
| [`service-request-chatbot/docs/help-agent-pipeline.md`](service-request-chatbot/docs/help-agent-pipeline.md) | Help Agent pipeline, taxonomy, RBAC, FAQ RAG |
| [`service-request-chatbot/docs/rdd-agent.md`](service-request-chatbot/docs/rdd-agent.md) | RDD agent lifecycle architecture (3-phase) |
| [`service-request-chatbot/docs/handover-workflow.md`](service-request-chatbot/docs/handover-workflow.md) | Handover CREATE_SR sequence and schema |
| [`service-request-chatbot/docs/extensibility-guide.md`](service-request-chatbot/docs/extensibility-guide.md) | Adding new agents and workflow stages |
| [`service-request-chatbot/docs/api-reference.md`](service-request-chatbot/docs/api-reference.md) | Full HTTP API reference |
| [`service-request-chatbot/docs/database.md`](service-request-chatbot/docs/database.md) | Database schema, models, migrations |
| [`service-request-chatbot/docs/security-guardrails.md`](service-request-chatbot/docs/security-guardrails.md) | Injection guard, confirmation guards, submission guards |
| [`service-request-chatbot/docs/observability.md`](service-request-chatbot/docs/observability.md) | Observability data model and API |

### Testing

| Document | Description |
|----------|-------------|
| [`service-request-chatbot/docs/testing-strategy.md`](service-request-chatbot/docs/testing-strategy.md) | Testing approach and pyramid overview |
| [`service-request-chatbot/docs/agent-testing-guide.md`](service-request-chatbot/docs/agent-testing-guide.md) | Complete agent test guide — unit through eval |
| [`service-request-chatbot/docs/test-automation-lifecycle.md`](service-request-chatbot/docs/test-automation-lifecycle.md) | Full lifecycle test automation (pytest + Playwright + eval) |
| [`service-request-chatbot/docs/e2e-lifecycle-test-scenarios.md`](service-request-chatbot/docs/e2e-lifecycle-test-scenarios.md) | Master E2E scenario reference with state assertions |
| [`service-request-chatbot/docs/e2e-user-testing-guide.md`](service-request-chatbot/docs/e2e-user-testing-guide.md) | Role-by-role UAT walkthrough (4 roles) |
| [`service-request-chatbot/docs/chatbot-test-queries.md`](service-request-chatbot/docs/chatbot-test-queries.md) | Exhaustive chat input catalogue (happy path, edge, RBAC) |
| [`service-request-chatbot/docs/faq-rag-testing.md`](service-request-chatbot/docs/faq-rag-testing.md) | FAQ node RAG pipeline test cases |
| [`service-request-chatbot/docs/uat-readiness.md`](service-request-chatbot/docs/uat-readiness.md) | UAT readiness assessment and MSP integration checklist |

### Operations & Deployment

| Document | Description |
|----------|-------------|
| [`service-request-chatbot/docs/local-development.md`](service-request-chatbot/docs/local-development.md) | Local setup, migrations, troubleshooting |
| [`service-request-chatbot/docs/debugging-guide.md`](service-request-chatbot/docs/debugging-guide.md) | Diagnosing common failure modes |
| [`service-request-chatbot/docs/production-readiness.md`](service-request-chatbot/docs/production-readiness.md) | Production readiness assessment |
| [`service-request-chatbot/docs/azure-deployment.md`](service-request-chatbot/docs/azure-deployment.md) | Azure deployment guide |

### Integration & Stakeholder

| Document | Description |
|----------|-------------|
| [`service-request-chatbot/docs/help-agent-validation.md`](service-request-chatbot/docs/help-agent-validation.md) | Help Agent / MSP integration contract and alignment gap table |
| [`service-request-chatbot/docs/client-demo-script.md`](service-request-chatbot/docs/client-demo-script.md) | End-to-end client demo walkthrough |
| [`service-request-chatbot/docs/eval-howto.md`](service-request-chatbot/docs/eval-howto.md) | Local eval workflow — running all 7 tiers, interpreting results |
| [`docs/reports/REPORT.md`](docs/reports/REPORT.md) | UAT readiness report (Help Agent + RDD lifecycle)
| [`HANDOVER.md`](HANDOVER.md) | Developer handover document |
| [`docs/reports/STAKEHOLDER_REPORT.md`](docs/reports/STAKEHOLDER_REPORT.md) | Stakeholder-facing project report |
| [`RELEASE_NOTES.md`](RELEASE_NOTES.md) | Version history and change log |
| [`docs/CHANGELOG.md`](docs/CHANGELOG.md) | Detailed per-file diff — v1.3.0 → v1.4.0 |

---

## Known gaps / remaining work

### Backend (open)

- **Gap 6 — Platform status sync field names:** `sr_status_sync_node` field mapping needs one-time verification against a live platform `GET /service-requests/{sr_id}` response (~30 min once credentials are available)
- **S32 — Multiple SRs in same session:** After SR1 is submitted, `sr_status_sync_node` routes the session to FM_REVIEW, preventing SR2 from starting; `sr_id` needs to be cleared when a new CREATE intent is detected after completion
- **S35/S37 — Cancel flow confirmation card:** Cancel SR eval scenario fixtures need refinement for the CANCEL_PENDING → confirmation card signal
- **Redis session caching:** Redis is connected but not yet used for caching (performance optimisation only)
- **True SSE streaming:** Current streaming is pseudo (full message as one token event); real token-level streaming requires LangGraph/LLM gateway changes
- **CI/CD pipeline:** `./run-live-tests.sh` is the manual gate; an Azure DevOps / GitHub Actions pipeline is the next step

### Frontend (pending — agent already emits correct signals)

| # | Item | Agent signal to consume |
|---|---|---|
| 1 | Unit Code not populated on SR form | `response.state.draft_preview.unit_codes` |
| 2 | Title not shown on SR form | `response.state.draft_preview.title` |
| 3 | Notes not reflected on form | `response.state.draft_preview.notes` |
| 4 | Cancel SR button + form close | `response.ui.cancelled === true` |
| 5 | FM Update/Save buttons hidden after approval | `response.ui.stage_complete === true` |
| 6 | RDD Confirm button shown after document upload | `response.ui.type === "confirmation_card"` |
| 7 | RDD action buttons hidden after SR_COMPLETED | `response.ui.workflow_complete === true` |

See [`docs/fix-notes/fix_verification_status.md`](docs/fix-notes/fix_verification_status.md) for the full signal reference table.

---

## License

Proprietary / internal — update as appropriate for your organisation.
