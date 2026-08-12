# Release Notes — Cenomi RDD Chatbot

---

## v1.5.0 — August 12, 2026 (Unreleased)

> **Theme:** Multi-provider LLM adapter, pluggable embedding registry, and dynamic per-trace cost tracking.

### What's new

#### Multi-Provider LLM Adapter

The gateway layer is now fully provider-agnostic. A new `LLMProviderRegistry` dispatches to any registered provider at startup; changing provider requires only an env-var change — no code change.

| Provider | Class | Env var |
|---|---|---|
| OpenAI | `OpenAIGateway` | `LLM_PROVIDER=openai` |
| Azure OpenAI | `AzureOpenAIGateway` | `LLM_PROVIDER=azure_openai` |
| Vertex AI (Gemini) | `VertexAIGateway` | `LLM_PROVIDER=vertex_ai` |

- `LLMProviderProtocol` + `BaseLLMGateway` define the contract — future providers implement one interface
- `LLMResult` dataclass carries `content`, `model`, `usage` (prompt/completion/total tokens), and `provider` name
- `supervisor_node` and `field_extraction_node` now read `provider_name` from the gateway instead of hardcoding `"openai"`

#### Pluggable Embedding Registry

`EmbeddingProviderRegistry` replaces the inline if/else in `integrations/factory.py`. Each existing provider (`OpenAIEmbeddingProvider`, `AzureOpenAIEmbeddingProvider`) gains a `from_settings()` classmethod. Vertex AI embeddings added as a new provider (`VertexAIEmbeddingProvider`, `google-genai` backed).

#### Dynamic Cost Tracking

`cost_calculator.py` delegates to `litellm.completion_cost()` — tracks token spend without vendoring pricing tables. `trace_manager.py` automatically calls `calculate_llm_cost()` at the end of every trace turn and accumulates per-trace `total_tokens` and `total_cost_usd` counters.

#### New dependencies

| Package | Version constraint | Extra |
|---|---|---|
| `litellm` | `>=1.40.0,<2.0.0` | core |
| `google-genai` | `>=1.0.0` | `[vertex_ai]` optional |

Install with Vertex AI support: `pip install '.[vertex_ai]'`

#### New env vars (`.env.example` updated)

```
LLM_PROVIDER=openai                  # openai | azure_openai | vertex_ai
AZURE_OPENAI_LLM_DEPLOYMENT=...      # Azure deployment name for the chat model
VERTEX_AI_PROJECT=...
VERTEX_AI_LOCATION=us-central1
VERTEX_AI_MODEL=gemini-1.5-pro
EMBEDDING_PROVIDER=openai            # openai | azure_openai | vertex_ai
```

### Known issues (non-blocking)

| # | Location | Issue |
|---|---|---|
| 1 | `azure_openai_provider.py`, `openai_provider.py` | `stream=True` flag accepted but response read as non-stream — `AttributeError` if any caller passes `stream=True` (currently no callers do) |
| 2 | `vertex_ai_provider.py` | `tools` kwarg silently ignored |
| 3 | `azure_openai_provider.py` | `AZURE_AI_ENDPOINT` / `AZURE_AI_API_KEY` shared with embedding provider — no separate config for LLM vs embedding Azure resources |

---

### New files

```
Added
  service-request-chatbot/backend/app/agents/llm/base.py
  service-request-chatbot/backend/app/agents/llm/registry.py
  service-request-chatbot/backend/app/agents/llm/providers/__init__.py
  service-request-chatbot/backend/app/agents/llm/providers/openai_provider.py
  service-request-chatbot/backend/app/agents/llm/providers/azure_openai_provider.py
  service-request-chatbot/backend/app/agents/llm/providers/vertex_ai_provider.py
  service-request-chatbot/backend/app/observability/cost_calculator.py
  service-request-chatbot/backend/app/integrations/embeddings/registry.py
  service-request-chatbot/backend/app/integrations/embeddings/vertex_ai_embed.py
  service-request-chatbot/backend/tests/unit/test_llm_adapters.py
  service-request-chatbot/backend/tests/unit/test_embedding_adapters.py

Modified
  backend/app/agents/llm/gateway.py              ← dispatches via LLMProviderRegistry
  backend/app/core/config.py                     ← LLM_PROVIDER, VERTEX_AI_*, EMBEDDING_PROVIDER
  backend/app/observability/trace_manager.py     ← per-trace token/cost accumulators
  backend/app/integrations/factory.py            ← delegates to EmbeddingProviderRegistry
  backend/app/integrations/embeddings/openai_embed.py     ← from_settings() classmethod
  backend/app/integrations/embeddings/azure_openai_embed.py ← from_settings() classmethod
  backend/app/agents/graph/nodes/supervisor_node.py       ← provider_name from gateway
  backend/app/agents/graph/nodes/shared/field_extraction_node.py ← same provider_name fix
  backend/app/main.py                            ← duplicate obs_metrics router removed
  backend/pyproject.toml                         ← litellm + google-genai dependencies
  backend/.env.example                           ← new env vars documented
```

---

## v1.4.1 — July 22, 2026 (Unreleased)

> **Theme:** Lease eligibility reasons (`handover_exists` / `activation_pending`) + multi-file document uploads.

### What's new

- Empty lease-lookup responses honour platform `reason` instead of always saying “not found”
- `ui.type = existing_handover` with SR deep link when a handover already exists
- Multi-file document upload with coalesced chat refresh
- Eval tracking: **S63**, **S64** (Tier 2), **L173** (Tier 6) — see `docs/CHANGELOG.md` and `docs/fix-notes/lease_eligibility_and_multifile_upload.md`

---

## v1.4.0 — July 18, 2026

> **Theme:** Agent–UI contract hardening, inspection-time handling, cancel SR workflow, field intent disambiguation, and expanded eval suite (99 scenarios, 7 tiers).

### What's new

#### Cancel SR Workflow

Full cancel path implemented end-to-end for Mall Manager:

- `action_override = "cancel_sr"` triggers a CANCEL_PENDING confirmation turn
- On `confirm_cancel_sr` the agent calls `PATCH /service-requests/{sr_id}` with `status: CANCELLED`
- Emits `response_ui.cancelled = true` + `workflow_stage = "CANCELLED"` so the UI can close the SR form
- Text-based cancel phrases (`_CANCEL_SR_PHRASES`) detected and handled via `supervisor_node`
- Full state cleared on cancellation (collected_data, backend_refs, documents)
- 5 new unit tests in `test_handover_entry_cancel.py`

#### Inspection Time Field (`inspectionTime`)

- `inspectionTime` added as a required field in `CREATE_SR_STAGE` (after `startDate`)
- Extraction prompt updated to accept `HH:MM` (24-hour)
- `merge_state_node._to_date_only()` no longer strips the time component from datetime-type fields
- Default: current time at the moment of SR creation (not hardcoded 9:00 AM)
- Payload builder combines `startDate` + `inspectionTime` into an ISO 8601 timestamp before sending to the platform
- 6 new unit tests in `test_date_time_handling.py`

#### Field Intent Disambiguation (Notes / description / comments)

- `_remap_extracted_to_active_field()` in `merge_state_node` extended from date-only to **all user-supplied fields** — when the agent last asked for e.g. `inspection_done_by` and the LLM extracts the user's reply as `notes`, the value is now rebound to the correct field
- `description` removed from `OPTIONAL_FIELDS` — always asked; extraction prompt now prefers `description` when `active_field == "description"`; `HANDOVER_FIELD_QUESTIONS["description"]` is distinct from notes
- `comments` re-added to `required_fields` for **all three stages** (CREATE_SR, FM_REVIEW, RDD_REVIEW) with a dedicated question; accepted as an empty string
- 10 new unit tests in `test_description_notes_comments.py`

#### UI Contract Signals

All agent state signals are now correctly emitted so the frontend can react without polling:

| Signal | Node | Value | Frontend action |
|---|---|---|---|
| `response_ui.cancelled` | `handover_entry_node` | `true` | Close SR form |
| `response_ui.stage_complete` | `fm_api_submission_node` (approve) | `true` | Hide FM Update/Save buttons |
| `response_ui.stage_complete` | `api_submission_node` (create) | `false` | n/a |
| `response_ui.workflow_complete` | `rdd_api_submission_node` (final approve) | `true` | Hide all RDD buttons |
| `confirmation_status` after CREATE_SR | `api_submission_node` | `null` | Prevents CONFIRMED leaking into FM_REVIEW |

#### MM Document Upload Ordering

`review_decorations_service.py` gate updated: optional MM upload offer fires only after all required fields (including newly-added `comments`) are collected and the SR is submitted. No upload prompt mid-flow.

#### Supervisor & Routing Improvements

- `supervisor_node` improved intent classification for edge cases (partial brand/mall names, post-SR intents)
- Routing functions updated to handle `SR_CREATED` → `FM_REVIEW` stage detection more reliably
- `sr_status_sync_node` field name handling updated for platform API alignment

#### Expanded Eval Suite — 99 scenarios across 7 tiers

| Tier | Description | Scenarios | Pass |
|---|---|---|---|
| 0 / 0fm | Smoke | 2 + 1 | 3/3 (100%) |
| 1 | Core S1–15 | 15 | 14/15 (99.3%) |
| 2 | Edge S16–33 | 20 | 18/20 (98.3%) |
| 3 | Battle S34–59 | 22 | 20/22 (97.1%) |
| 4 | FM_REVIEW | 6 | 6/6 (100%) |
| 5 | RDD_REVIEW | 6 | 6/6 (100%) |
| 6 | Full lifecycle | 27 | 18/27 (75.8%) |

Tier 6 failures are concentrated in the cancel flow (2 scenarios still being refined) and full-lifecycle routing sequences that depend on a running stack with seeded state.

---

### New files

```
Added
  service-request-chatbot/backend/tests/unit/test_completion_state_transitions.py
  service-request-chatbot/backend/tests/unit/test_date_time_handling.py
  service-request-chatbot/backend/tests/unit/test_description_notes_comments.py
  service-request-chatbot/backend/tests/unit/test_handover_entry_cancel.py
  service-request-chatbot/backend/tests/unit/test_rdd_workflow_order.py
  service-request-chatbot/backend/tests/unit/test_session_isolation.py
  service-request-chatbot/docs/eval-howto.md
  service-request-chatbot/run_agent_eval.sh

Modified
  backend/app/agents/graph/help_agent_graph.py
  backend/app/agents/graph/nodes/handover/api_submission_node.py
  backend/app/agents/graph/nodes/handover/confirmation_node.py
  backend/app/agents/graph/nodes/handover/fm_api_submission_node.py
  backend/app/agents/graph/nodes/handover/fm_review_entry_node.py
  backend/app/agents/graph/nodes/handover/handover_entry_node.py
  backend/app/agents/graph/nodes/handover/rdd_api_submission_node.py
  backend/app/agents/graph/nodes/shared/lease_lookup_node.py
  backend/app/agents/graph/nodes/shared/load_session_node.py
  backend/app/agents/graph/nodes/shared/merge_state_node.py
  backend/app/agents/graph/nodes/shared/sr_status_sync_node.py
  backend/app/agents/graph/nodes/supervisor_node.py
  backend/app/agents/prompts/handover_extraction_prompt.py
  backend/app/agents/prompts/supervisor_prompt.py
  backend/app/agents/schemas/handover_schema.py
  backend/app/agents/services/document_upload_service.py
  backend/app/agents/services/lease_lookup_service.py
  backend/app/agents/services/missing_field_service.py
  backend/app/agents/services/payload_builder_service.py
  backend/app/agents/services/review_decorations_service.py
  backend/app/agents/services/service_request_api_service.py
  backend/app/api/routes/upload.py
  backend/app/core/config.py
  backend/scripts/seed_users.py
  backend/tests/eval/eval_session.py
  backend/tests/eval/lifecycle_scenarios.py
  backend/tests/eval/run_eval.py
  backend/tests/eval/run_lifecycle_eval.py
  backend/tests/eval/scenarios.py
  backend/tests/integration/test_role_stage_access.py
  backend/tests/integration/test_service_request_graph.py
  + 11 existing unit test files updated for new schema/behaviour
```

---

### Frontend-only pending items (agent already emits correct signals)

All 7 remaining items are **frontend binding gaps** — the agent emits the correct data, the UI just needs to consume it:

| # | Item | Agent signal |
|---|---|---|
| 1 | Unit Code not populated on SR form | `draft_preview.unit_codes` |
| 2 | Title not shown on SR form | `draft_preview.title` |
| 3 | Notes not reflected on form | `draft_preview.notes` |
| 4 | Cancel SR button + form close | `response_ui.cancelled = true` |
| 5 | FM Update button removed after approval | `response_ui.stage_complete = true` |
| 6 | RDD Confirm button shown after upload | `ui.type = "confirmation_card"` |
| 7 | RDD buttons removed after SR_COMPLETED | `response_ui.workflow_complete = true` |

See [`docs/fix-notes/fix_verification_status.md`](docs/fix-notes/fix_verification_status.md) for the full signal reference.

---

### Open backend items

| Item | Note |
|---|---|
| S32 — Multiple SRs in same session | `sr_status_sync_node` routes to FM_REVIEW after first SR completed; blocks SR2 creation |
| S35/S37 — Cancel confirmation card | Cancel CANCEL_PENDING → confirmation card UI signal needs refinement in eval fixture |
| Gap 6 — `sr_status_sync_node` field names | 30-minute check against live platform `GET /service-requests/{sr_id}` response |

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
