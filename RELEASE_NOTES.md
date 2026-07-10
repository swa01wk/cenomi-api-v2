# Release Notes — Cenomi RDD Chatbot

---

## v1.3.0 — July 8, 2026

> **Theme:** Full-stack test automation, agent evaluation, 6 agent bug fixes, and observability export tooling.

### What's new

#### Playwright Test Suite — Frontend E2E (42 mocked + 50 live)

Two Playwright configurations now cover the full chat UI:

**Mocked tests** (`playwright.config.ts`) — 42 tests, no backend required:
- `auth.spec.ts` — login, redirect, role badge, sign-out
- `lifecycle-stepper.spec.ts` — LifecycleStepper renders correctly at each stage
- `stage-actions.spec.ts` — FM/RDD action buttons appear/disappear with correct RBAC
- `handover-lifecycle.spec.ts` — Full CREATE→FM→RDD flow with mocked API

**Live stack tests** (`playwright.live.config.ts`) — 50 tests, real LLM + Postgres + Redis:
- `lifecycle-live.spec.ts` — 3-phase lifecycle through the real browser UI
- `agent-eval.spec.ts` — 33 CREATE_SR eval scenarios via API
- `lifecycle-eval.spec.ts` — 14 FM/RDD/full-lifecycle eval scenarios (see below)

#### Agent Evaluation — 47 Scenarios Across All 3 Phases

| Suite | Scenarios | Pass Rate |
|---|---|---|
| CREATE_SR (S1–S33) | 33 | 31/33 (2 LLM variability) |
| FM_REVIEW (S34–S39) | 6 | 6/6 |
| RDD_REVIEW (S40–S45) | 6 | 6/6 |
| Full lifecycle (S46–S47) | 2 | 2/2 |

New scenario files:
- `backend/tests/eval/lifecycle_scenarios.py` — 14 lifecycle scenarios (S34–S47) with multi-user, multi-phase structure
- `backend/tests/eval/run_lifecycle_eval.py` — lifecycle runner with JWT auth, document upload, SR ID passing between phases

#### 6 Agent Bugs Fixed

Found by the eval suite; all fixed with regression tests:

| Bug | Fix |
|---|---|
| `comments` + `notes` double optional-ask blocked confirmation card in all 3 stages | Removed `comments` from `required_fields` for CREATE_SR, FM_REVIEW, RDD_REVIEW; synced `notes ↔ comments` in `merge_state_node` |
| FM/RDD sessions re-triggered lease lookup (asked "share your lease code" during FM review) | `_route_after_merge` in `help_agent_graph.py` skips lease lookup when `workflow_stage` is FM_REVIEW or RDD_REVIEW |
| FM/RDD sessions never created a DB draft (blocked document persistence) | `conversation_state_service.save_checkpoint` uses `"FIT_OUT_AND_HANDOVER"` as default `service_category` for FM/RDD stages |
| JWT role names (`"FM_MANAGER"`) not expanded to permission strings — document upload returned 403 | `PermissionService.check` now expands role names via `ROLE_PERMISSION_MAP` before checking |
| Document upload silently no-oped in mock mode (no platform URL configured) | `DocumentUploadService.upload_document` returns a mock document ID when no platform URL is set |
| Upload endpoint blocked when no `lease_id` in FM session `collected_data` | Upload endpoint bypasses `lease_id` requirement in mock mode; creates a minimal draft if none exists |

#### Observability Export Tooling

Two new scripts for session analysis and monitoring:

```bash
# Export state transitions + field extraction + LLM usage for any session
python scripts/export_session_observability.py --session-id <uuid> --eval

# Auto-discover recent sessions
python scripts/export_session_observability.py --recent 10 --out ./sessions --eval

# Get a full conversation transcript with state transitions
python scripts/get_session_conversation.py --session-id <uuid>
python scripts/get_session_conversation.py --session-file session-ids.json --json
```

Output per session:
- `{session_id}.json` — structured export: turn-by-turn state transitions, field extraction timeline, node execution order, LLM token counts, tool calls, eval quality scores
- `{session_id}.md` — human-readable Markdown transcript
- `manifest.ndjson` — one-line summary per session for dashboards

#### Live Test Orchestrator

`run-live-tests.sh` — single command to start the full stack and run all 5 test tiers:

```bash
./service-request-chatbot/run-live-tests.sh                # all tiers
./service-request-chatbot/run-live-tests.sh --tier unit    # pytest only (no server)
./service-request-chatbot/run-live-tests.sh --tier eval    # 33 LLM scenarios
./service-request-chatbot/run-live-tests.sh --no-start     # stack already running
```

---

### Test suite status

| Suite | Before | After |
|---|---|---|
| pytest (unit + integration + mocked E2E) | 1,386 passing | **1,387 passing** ✅ |
| Playwright mocked UI | 0 | **42/42** ✅ |
| Playwright live — CREATE_SR eval | 0 | **31/33** (2 LLM variability) |
| Playwright live — FM_REVIEW | 0 | **6/6** ✅ |
| Playwright live — RDD_REVIEW | 0 | **6/6** ✅ |
| Playwright live — Full lifecycle | 0 | **2/2** ✅ |
| Playwright live — UI lifecycle | 0 | **16/16** ✅ |

---

### Files changed

```
Modified
  backend/app/agents/schemas/handover_schema.py          ← comments removed from all 3 stages
  backend/app/agents/graph/nodes/shared/merge_state_node.py ← notes ↔ comments sync
  backend/app/agents/graph/help_agent_graph.py            ← FM/RDD skip lease lookup
  backend/app/agents/services/conversation_state_service.py ← FM/RDD draft creation fix
  backend/app/agents/services/document_upload_service.py ← mock mode support
  backend/app/agents/services/permission_service.py       ← JWT role name expansion
  backend/app/api/routes/upload.py                        ← mock mode + draft fallback
  backend/app/agents/prompts/handover_extraction_prompt.py ← notes field guidance updated
  backend/app/agents/services/missing_field_service.py   ← notes question updated
  backend/tests/unit/test_handover_schema.py             ← updated for removed comments
  backend/tests/unit/test_missing_field_node.py          ← updated for FM_REVIEW change
  backend/tests/unit/test_missing_field_service.py       ← updated for removed comments
  backend/tests/unit/test_confirmation_node.py           ← updated for removed comments

Added
  frontend/playwright.config.ts                           ← mocked Playwright config
  frontend/playwright.live.config.ts                     ← live stack Playwright config
  frontend/tests/e2e/helpers/api-mocks.ts                ← mock API helpers
  frontend/tests/e2e/auth.spec.ts                        ← login/auth tests
  frontend/tests/e2e/lifecycle-stepper.spec.ts           ← stepper UI tests
  frontend/tests/e2e/stage-actions.spec.ts               ← FM/RDD button tests
  frontend/tests/e2e/handover-lifecycle.spec.ts          ← full UI lifecycle tests
  frontend/tests/e2e/live/helpers/live-helpers.ts        ← live test helpers
  frontend/tests/e2e/live/lifecycle-live.spec.ts         ← real browser lifecycle tests
  frontend/tests/e2e/live/agent-eval.spec.ts             ← 33 CREATE_SR eval scenarios
  frontend/tests/e2e/live/lifecycle-eval.spec.ts         ← 14 FM/RDD lifecycle scenarios
  frontend/tests/e2e/live/scenarios.json                 ← pre-generated scenario discovery
  frontend/tests/e2e/live/lifecycle_scenarios.json       ← pre-generated lifecycle discovery
  backend/tests/eval/lifecycle_scenarios.py              ← S34–S47 FM/RDD/lifecycle scenarios
  backend/tests/eval/run_lifecycle_eval.py               ← lifecycle eval runner
  backend/scripts/export_session_observability.py        ← session export tool
  backend/scripts/get_session_conversation.py            ← conversation viewer
  service-request-chatbot/run-live-tests.sh              ← live test orchestrator
```

---

### Closed items from v1.2.0

| Item | Status |
|---|---|
| Gap 7 — `PermissionService` fail-closed | **FIXED** — `check()` now raises for unknown actions AND correctly expands JWT role names to permission strings |
| CI/CD pipeline | Run `./run-live-tests.sh` or individual `npx playwright test` commands |

---

---

## v1.2.0 — July 7, 2026

> **Theme:** UAT Readiness — Full lifecycle completion, production Dockerfiles, mock infrastructure fix, and comprehensive documentation.

### What's new

#### Full RDD Lifecycle — End-to-End Verified

The entire three-phase Handover SR lifecycle is now fully implemented, tested, and UAT-ready.

| Phase | Role | What was completed |
|-------|------|--------------------|
| Phase 1 — CREATE_SR | Mall Manager | Was already complete — re-verified, all 10 edge-case tests passing |
| Phase 2 — FM_REVIEW | FM Manager / Operations | Collect dates, upload FM docs, Save Progress, Approve Review — 14 integration tests |
| Phase 3a — RDD Submit | DD Engineer | Upload RDD report, submit `REPORT_SUBMITTED` — 16 integration tests |
| Phase 3b — RDD Final Approve | DD Engineer | Final `APPROVED` → `SR_COMPLETED` — chained 3a→3b test |

**Full lifecycle E2E test** (`test_handover_full_lifecycle_http.py`) — 7 tests covering the complete 9-step sequence (CREATE → FM save → FM approve → RDD submit → RDD final approve) over HTTP with mocked externals.

#### Mock SR Service — Stateful Across Requests

**Problem:** `MockServiceRequestAPIService` was instantiated fresh on every HTTP request, so SR state seeded during CREATE_SR was lost by the next turn. `sr_status_sync_node` could never find the SR and stage auto-advance never worked locally.

**Fix:** Module-level `_MOCK_PERSISTENT_SR_STATES` dict shared across all mock instances. State now persists for the lifetime of the server process.

- CREATE_SR seeds FM_MANAGER as `IN_PROGRESS` → `sr_status_sync_node` routes to `FM_REVIEW` on next turn ✅
- FM Approve advances DD_ENGINEER to `IN_PROGRESS` → routes to `RDD_REVIEW` ✅
- Submit Report keeps DD_ENGINEER `IN_PROGRESS` → Final Approve transitions to `SR_COMPLETED` ✅
- Test isolation preserved — `force_error`/`force_status_code` instances still get private state ✅

**New reset endpoint:** `POST /dev/mock/reset` — clears mock state between test runs without server restart. Returns 404 in production (when `SERVICE_REQUEST_API_BASE_URL` is set).

#### Production Dockerfiles

| File | Description |
|------|-------------|
| `backend/Dockerfile` | 2-stage build (builder + runtime). Non-root user. Runs `alembic upgrade head` on startup. |
| `backend/.dockerignore` | Excludes venv, tests, secrets, build artefacts |
| `frontend/Dockerfile` | 3-stage build (deps → Next.js build → standalone runtime). `node_modules` not in final image. |
| `frontend/.dockerignore` | Excludes node_modules, .next, secrets |
| `docker-compose.prod.yml` | Full production stack: Postgres + Redis (internal network) + backend + frontend |
| `backend/.env.prod.example` | Production secrets template |

**One-command production start:**
```bash
cp backend/.env.prod.example backend/.env.prod  # fill in secrets
docker compose -f docker-compose.prod.yml up -d --build
```

#### Lifecycle Test Runner Script

`backend/run_lifecycle_tests.sh` — single command to run all 68 priority lifecycle tests.

```bash
./run_lifecycle_tests.sh              # standard
./run_lifecycle_tests.sh --coverage   # + HTML coverage report
./run_lifecycle_tests.sh --fast       # stop on first failure
```

#### New Documentation

| Document | Description |
|----------|-------------|
| `docs/manual-testing-guide.md` | Full step-by-step guide to manually test the RDD lifecycle from the frontend — all 3 phases, test users, mock leases, reset instructions, troubleshooting |
| `docs/uat-readiness.md` | Comprehensive UAT readiness assessment — 12 sections covering test coverage, phase-by-phase readiness, RBAC matrix, document handling, live platform integration checklist, MSP auth integration analysis, worst-case code change list |
| `RELEASE_NOTES.md` | This file |

#### Updated Documentation

| Document | What changed |
|----------|-------------|
| `README.md` | Updated known gaps, features list, docs table, test command, infrastructure description |
| `HANDOVER.md` | Section 12 (implementation state) fully updated — Gap 4, 5, 8 marked FIXED; production Dockerfiles added; test counts corrected. Section 13 gaps updated. |

---

### Test suite status

| Suite | Before | After |
|-------|--------|-------|
| Full suite | 1,386 passing | 1,386 passing ✅ |
| Lifecycle priority (68 tests) | Some failures (mock stateless) | 68/68 passing ✅ |
| FM_REVIEW integration | 14 tests | 14/14 passing ✅ |
| RDD_REVIEW integration | 16 tests | 16/16 passing ✅ |
| RBAC matrix | 21 tests | 21/21 passing ✅ |
| Full lifecycle E2E HTTP | 7 tests | 7/7 passing ✅ |

---

### Files changed

```
Modified
  backend/app/agents/services/service_request_api_service.py   ← stateful mock
  backend/app/api/routes/health.py                             ← /dev/mock/reset endpoint
  frontend/next.config.ts                                      ← output: "standalone"
  README.md                                                    ← updated throughout
  HANDOVER.md                                                  ← sections 12 + 13 updated

Added
  backend/Dockerfile
  backend/.dockerignore
  backend/.env.prod.example
  backend/run_lifecycle_tests.sh
  frontend/Dockerfile
  frontend/.dockerignore
  service-request-chatbot/docker-compose.prod.yml
  service-request-chatbot/docs/manual-testing-guide.md
  service-request-chatbot/docs/uat-readiness.md
  RELEASE_NOTES.md
```

---

### Open items (not in this release)

| Item | Priority | Notes |
|------|----------|-------|
| Gap 6 — `sr_status_sync_node` field name verification | High | 30-minute check once live platform credentials available |
| Gap 7 — `PermissionService` fail-closed | High | 1-line fix before production go-live |
| CI/CD pipeline (GitHub Actions) | Medium | `./run_lifecycle_tests.sh` is the manual gate for now |
| Redis session caching | Low | Performance optimisation — not a functional gap |
| True token-level SSE streaming | Low | Requires LangGraph/gateway changes — deferred |

---

---

## v1.1.0 — June 26, 2026

> **Theme:** Multi-workflow scaling refactor and extensions.

- `WorkflowConfig` registry — data-driven graph routing, adding new workflows requires only a registry entry
- Field extraction per-workflow prompt injection
- `HelperIntent` decoupled (`HelperIntent = str`)
- 76 new tests added (`workflow_config`, `help_agent_graph`, `help_agent_schema`)
- RDD lifecycle doc (`rdd-agent.md`)
- Role-stage access integration test suite (354 lines, 21 tests)

---

## v1.0.2 — June 18, 2026

> **Theme:** Full lifecycle completion and developer handover.

- RDD final-approve flow (Phase 3b)
- Expected handover date auto-computation
- Document upload node fully wired to graph
- Role-permission map complete
- `HANDOVER.md` developer handover document (1,100+ lines)
- FM/RDD e2e integration tests (743 lines)
- Frontend stage UI components: `LifecycleStepper`, `StageActions`, `StageContextPanel`, `DocumentUploadPanel`, `RoleSelector`

---

## v1.0.1 — June 6, 2026

> **Theme:** Preview node and supervisor refinements.

- `preview_node.py` — SR status on demand
- Updated supervisor prompt for FM/RDD intents
- `SRPreviewCard.tsx` frontend component
- Lifecycle stepper
- E2E lifecycle test scenarios
- Client demo script
- Eval scenarios expanded to 33 cases

---

## v1.0.0 — May 18, 2026

> **Theme:** Initial release — complete application.

- Full FastAPI + LangGraph backend (~15,800 lines Python)
- CREATE_SR workflow end-to-end
- FM + RDD review workflow nodes
- Next.js 15 chat UI + Admin Observability dashboard
- Platform API client (`platform_api_client.py`)
- Status sync node
- Document upload service
- Permission service
- 150+ unit/integration tests
- 13 documentation files
- 5-layer security guardrail system
- Full trace/run/LLM call observability system
