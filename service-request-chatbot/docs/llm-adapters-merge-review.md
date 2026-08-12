# `feat/llm-adapters` → `dev` Merge Review Report

**Date**: 2026-08-10
**Branch**: `origin/feat/llm-adapters`
**Target**: `origin/dev`

---

## TL;DR

The four LLM adapter features are solid and ready to land. The branch also carries **stale copies** of fixes from `fix/handover-agent-remaining-gaps` and `fix/staging` that are already in `dev` — these create the only real merge risk (25 files, ~479 lines of conflicting context). A direct merge is **not recommended**; see the Merge Strategy section.

---

## What This Branch Delivers (Items 1–4)

### 1. Multi-Provider LLM Adapter System

**New files** under `service-request-chatbot/backend/app/agents/llm/`:

```
llm/
├── base.py          — LLMResult dataclass · LLMProviderProtocol · BaseLLMGateway
├── gateway.py       — Refactored: backward-compat dispatch facade (replaces OpenAI-only class)
├── registry.py      — LLMProviderRegistry: factory map + singleton cache
└── providers/
    ├── __init__.py
    ├── openai_provider.py       — OpenAIGateway
    ├── azure_openai_provider.py — AzureOpenAIGateway
    └── vertex_ai_provider.py   — VertexAIGateway
```

#### Architecture

```
Existing callers  ──►  gateway.py (LLMGateway.from_settings)
                              │
                              ▼
                   LLMProviderRegistry
                   (class-level factory + singleton cache)
                    │            │            │
                    ▼            ▼            ▼
             OpenAIGateway  AzureOpenAI  VertexAIGateway
                    \            |            /
                     \           |           /
                      ▼          ▼          ▼
                        BaseLLMGateway
                        complete_json() wrapper
                              │
                              ▼
                         LLMResult
                   (content · parsed · input_tokens
                    output_tokens · latency_ms · raw)
```

#### Key Points

- **`LLMResult`** is a frozen dataclass — all providers return a uniform object.
- **`BaseLLMGateway`** implements `complete_json()` once; all three providers inherit it and only need to implement `generate()`.
- **`gateway.py`** is fully backward-compatible: existing callers using `LLMGateway.from_settings()`, `get_default_gateway()`, `set_default_gateway()`, and the `complete_json(system, user) → (dict, int, int, int)` signature work unchanged.
- Selecting a provider is one env var change: `LLM_PROVIDER=openai` (default) / `azure_openai` / `vertex_ai`.

#### Issues in This Layer

| # | Issue | Severity | Fix |
|---|---|---|---|
| A | `stream=True` passed to SDK but response is read as a non-stream object — crashes if any caller ever sets `stream=True` | Medium (dormant) | Remove `stream` param or implement async accumulation |
| B | `VertexAIGateway.__init__` does not validate `VERTEX_AI_PROJECT` at startup (embedding provider does) | Low | Add the same `RuntimeError` guard as `VertexAIEmbeddingProvider` |
| C | `OpenAIGateway.from_settings()` passes `""` for a null API key instead of raising early | Low (same as old dev behavior) | Match the embedding provider: raise `RuntimeError` on missing key |
| D | `LLMGateway(LLMProviderProtocol)` inherits a Protocol but implements none of its methods — it is a factory namespace, not an instance | Informational | Rename to `LLMGatewayFactory` or move `from_settings()` to a module-level function |

---

### 2. Embedding Provider Registry

**New / modified** under `service-request-chatbot/backend/app/integrations/embeddings/`:

| File | Change |
|---|---|
| `registry.py` | **New** — `EmbeddingProviderRegistry` (mirrors `LLMProviderRegistry` exactly) |
| `vertex_ai_embed.py` | **New** — `VertexAIEmbeddingProvider` via `google-genai` SDK |
| `openai_embed.py` | Added `from_settings()` classmethod with key-presence guard |
| `azure_openai_embed.py` | Added `from_settings()` classmethod with endpoint/key guard |
| `factory.py` | `get_embedding_provider()` now delegates to `EmbeddingProviderRegistry` |

Provider selected via `EMBEDDING_PROVIDER` env var (`openai` / `azure_openai` / `vertex_ai`). Factory returns `None` gracefully when credentials are absent — `faq_node` falls back to static prompt without crashing.

#### Issues in This Layer

| # | Issue | Severity |
|---|---|---|
| E | `AZURE_AI_ENDPOINT` / `AZURE_AI_API_KEY` are shared by both the LLM adapter and the embedding provider. Organisations with separate Azure resources for chat vs. embeddings cannot configure them independently. | Low (deployment concern) |
| F | Class-level mutable dicts in both registries (`_factories`, `_instances`) make parallel test execution (e.g. `pytest-xdist`) susceptible to cross-test state leakage. `clear_cache()` only evicts `_instances`, not `_factories`. | Low (test concern only) |

---

### 3. Dynamic Cost Tracking

**New file**: `service-request-chatbot/backend/app/observability/cost_calculator.py`

- Pure function: `calculate_llm_cost(provider, model, input_tokens, output_tokens) → Decimal | None`
- Delegates entirely to `litellm.cost_per_token()` — no manual pricing tables, no startup registration
- Returns `None` for unknown models; never returns `Decimal('0')` for an unrecognised model
- Provider name translation lives in one place (`_PROVIDER_MAP`): `azure_openai` → `azure`
- Vertex AI fallback: retries under the `gemini` provider key for Gemini 1.5-series models not listed under `vertex_ai` in litellm's pricing data
- Pricing updates: `pip install --upgrade litellm` + redeploy — no code changes needed

**Modified**: `app/observability/trace_manager.py`
- `capture_llm_call()` auto-computes cost via `calculate_llm_cost()` if the caller does not supply it
- Per-trace token and cost accumulators (`_trace_token_totals`, `_trace_cost_totals`) populate the `total_token_count` and `estimated_cost` rollup written by `finish_trace()`

---

### 4. New Dependencies

| Package | Constraint | Required? | Why |
|---|---|---|---|
| `litellm` | `>=1.40.0,<2.0.0` | **Core** — always installed | `cost_per_token()` for cost tracking |
| `google-genai` | `>=1.0.0` | **Optional** — `[vertex_ai]` extra | Vertex AI LLM + embedding SDK |

Existing deployments on `LLM_PROVIDER=openai` (the default) are unaffected — `google-genai` is never imported unless `LLM_PROVIDER=vertex_ai` or `EMBEDDING_PROVIDER=vertex_ai`.

The `uv.lock` is updated (+3,613 lines) to capture the full resolved dependency graph including `litellm` and its transitive dependencies.

---

### New Environment Variables

| Variable | Default | Purpose |
|---|---|---|
| `LLM_PROVIDER` | `openai` | LLM backend: `openai`, `azure_openai`, `vertex_ai` |
| `AZURE_OPENAI_LLM_DEPLOYMENT` | *(none — falls back to `LLM_MODEL`)* | Azure deployment name for chat completions |
| `EMBEDDING_PROVIDER` | `openai` | Embedding backend: `openai`, `azure_openai`, `vertex_ai` |
| `VERTEX_AI_PROJECT` | *(none)* | GCP project ID (shared by LLM and embedding when both use Vertex) |
| `VERTEX_AI_LOCATION` | `us-central1` | GCP region |
| `VERTEX_AI_CREDENTIALS_PATH` | *(none)* | Path to GCP service account JSON (optional; defaults to ADC) |
| `VERTEX_AI_EMBEDDING_MODEL` | `text-embedding-005` | Vertex AI embedding model name |

All existing variables (`OPENAI_API_KEY`, `LLM_MODEL`, `LLM_BASE_URL`, `AZURE_AI_ENDPOINT`, `AZURE_AI_API_KEY`, `EMBEDDING_MODEL`, etc.) are unchanged.

---

### Test Coverage for Items 1–4

| Test file | Lines | Covers |
|---|---|---|
| `tests/unit/test_llm_adapters.py` | 184 | `BaseLLMGateway`, `LLMProviderRegistry`, `OpenAI/Azure/Vertex generate()` |
| `tests/unit/test_embedding_adapters.py` | 319 | `OpenAI/Azure/Vertex embed()`, `EmbeddingProviderRegistry`, factory |

Both files use proper `AsyncMock` / `patch` isolation. Vertex AI tests skip cleanly when `google-genai` is not installed.

---

## Merge Risk: Stale Bundled Fixes

The branch accumulated fixes from `fix/handover-agent-remaining-gaps` and `fix/staging` that are **already present in `dev`**. These changes touch **25 files** (+479 lines) that have since diverged in `dev`:

**Backend nodes / services / routes (10 files):**
- `app/agents/graph/nodes/handover/confirmation_node.py`
- `app/agents/graph/nodes/handover/fm_review_entry_node.py`
- `app/agents/graph/nodes/handover/lease_lookup_node.py`
- `app/agents/graph/nodes/shared/lease_lookup_node.py`
- `app/agents/graph/nodes/shared/merge_state_node.py`
- `app/agents/graph/nodes/shared/field_extraction_node.py`
- `app/agents/graph/nodes/shared/response_generation_node.py`
- `app/agents/graph/nodes/supervisor_node.py`
- `app/agents/services/lease_lookup_service.py`
- `app/api/routes/upload.py`

**Frontend (4 files):**
- `frontend/components/chatbot/DocumentUploadPanel.tsx`
- `frontend/components/chatbot/ExistingHandoverCard.tsx`
- `frontend/components/chatbot/ServiceRequestChat.tsx`
- `frontend/lib/api/upload-client.ts`
- `frontend/lib/types/chat.ts`

**Tests (5 files):**
- `tests/unit/test_confirmation_node.py`
- `tests/unit/test_fm_nodes.py`
- `tests/unit/test_handover_entry_gate_edge_cases.py`
- `tests/unit/test_lease_lookup_node.py`
- `tests/unit/test_merge_state_node.py`

**Docs / metadata (6+ files):**
- `RELEASE_NOTES.md`, `docs/CHANGELOG.md`, `changes.log`, `docs/ai-developer-context.md`, `docs/specs/handover-agent-remaining-gaps-spec.md`, `run_agent_eval.sh`, eval files

A direct `git merge` of `feat/llm-adapters` → `dev` will produce conflicts in all of these. The LLM adapter files themselves will merge cleanly.

---

## Merge Strategy Recommendation

**Do not merge the full branch directly.** Instead, cherry-pick only the LLM adapter commits onto `dev`:

### Commits to cherry-pick (LLM adapter work only)

```bash
# From the branch's exclusive commits, only these three are pure LLM adapter work:
git cherry-pick 4b4052d   # LLM Adapters for azure_openai vertex_ai (Aug 3)
git cherry-pick 6e9decd   # added dynamic cost calculation
git cherry-pick 8fe2b0e   # added embeddings
```

> Verify each commit's diff before cherry-picking to confirm it contains only the files listed in the File Reference Map below.

### Alternative: create a clean PR from scratch

Extract only the LLM adapter files into a new branch off `dev` — avoids any risk of picking up stale fix content.

---

## File Reference Map (LLM Adapter Files Only)

| File | Status | Purpose |
|---|---|---|
| `app/agents/llm/base.py` | New | `LLMResult`, `LLMProviderProtocol`, `BaseLLMGateway` |
| `app/agents/llm/gateway.py` | Refactored | Backward-compat facade; delegates to registry |
| `app/agents/llm/registry.py` | New | `LLMProviderRegistry` with factory + singleton cache |
| `app/agents/llm/providers/__init__.py` | New | Package marker |
| `app/agents/llm/providers/openai_provider.py` | New | `OpenAIGateway` |
| `app/agents/llm/providers/azure_openai_provider.py` | New | `AzureOpenAIGateway` |
| `app/agents/llm/providers/vertex_ai_provider.py` | New | `VertexAIGateway` |
| `app/integrations/embeddings/registry.py` | New | `EmbeddingProviderRegistry` |
| `app/integrations/embeddings/vertex_ai_embed.py` | New | `VertexAIEmbeddingProvider` |
| `app/integrations/embeddings/openai_embed.py` | Modified | Added `from_settings()` |
| `app/integrations/embeddings/azure_openai_embed.py` | Modified | Added `from_settings()` |
| `app/integrations/factory.py` | Modified | Delegates to `EmbeddingProviderRegistry` |
| `app/observability/cost_calculator.py` | New | Pure litellm cost delegation |
| `app/observability/trace_manager.py` | Modified | Auto cost compute; per-trace accumulators |
| `app/core/config.py` | Modified | New LLM / embedding / Vertex AI config fields |
| `backend/.env.example` | Modified | Documents all new env vars |
| `backend/pyproject.toml` | Modified | `litellm` core dep; `google-genai` optional extra |
| `backend/uv.lock` | Modified | Full dependency graph update |
| `tests/unit/test_llm_adapters.py` | New | 184 lines — LLM adapter unit tests |
| `tests/unit/test_embedding_adapters.py` | New | 319 lines — Embedding adapter unit tests |
