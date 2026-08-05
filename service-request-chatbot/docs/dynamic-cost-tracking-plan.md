# Dynamic LLM Cost Tracking — Implementation Plan

**Feature:** Automatic, zero-maintenance cost calculation for every LLM call recorded in the
observability pipeline, reflected in `agent_llm_calls` and `agent_traces` tables in PostgreSQL.

**Status:** Ready to implement  
**PRD Reference:** `docs/llm_adapters_prd.md` — FR-4.5 (Observability) and FR-4.6 (Dynamic Cost)  
**Created:** 2026-08-04  
**Last Updated:** 2026-08-04

---

## 1. Problem Statement

The current observability pipeline captures token counts (`input_tokens`, `output_tokens`) and
provider/model metadata for every LLM call, but `estimated_cost` is **never computed** — it
is always `NULL` in both `agent_llm_calls` and `agent_traces`. This means:

- The metrics endpoint (`/api/v1/observability/metrics`) always returns `total_cost: 0`
- There is no visibility into per-turn or per-session spend
- Pricing constants buried in `docs/client-demo-script.md` go stale whenever OpenAI/Azure/Vertex
  AI change their rates

### Root Cause

`TraceManager.capture_llm_call()` accepts an `estimated_cost` parameter, but every caller
(`supervisor_node`, `field_extraction_node`) passes `None` for it. No pricing lookup logic
exists anywhere in the backend code.

### Non-Goals

- We will **not** create a DB pricing table that admins must manually update
- We will **not** register models at app startup — provider and model will become
  **user-controlled via UI in a future release**, so cost calculation must work dynamically
  per-call with whatever `(provider, model)` is active at that moment
- We will **not** change any graph node files (except the one-line `field_extraction_node`
  provider bug fix mandated by PRD FR-4.5)
- We will **not** introduce the LiteLLM proxy server or any external pricing API network call
- We will **not** add any new environment variables beyond what is already in the PRD

---

## 2. Chosen Approach — Per-Call `litellm.cost_per_token()`

**[LiteLLM](https://github.com/BerriAI/litellm)** is an open-source Python library (41k+ GitHub
stars) that ships with a bundled `model_prices_and_context_window.json` containing pricing for
100+ models across OpenAI, Azure OpenAI, Google Vertex AI, Anthropic, AWS Bedrock, and more.

### Why Per-Call (Not Startup Registration)

The future roadmap includes **UI-controlled provider and model selection** — users will be able
to switch LLM provider and model at runtime without a server restart. This means:

- The active provider and model are **not known at startup**
- Any startup-time registration (e.g. `litellm.register_model()`) would be stale the moment
  a user switches provider via UI
- Cost calculation must be a **pure function of `(provider, model, input_tokens, output_tokens)`**
  resolved at the time each LLM call is recorded

`litellm.cost_per_token()` is exactly this — a pure function with no startup dependency,
executing in < 0.05 ms from bundled JSON data. It works correctly regardless of how or when
the active provider/model was selected.

### Why LiteLLM

| Concern | DB pricing table | LiteLLM per-call |
|---|---|---|
| Pricing update process | Manual DB `UPDATE` | `pip install --upgrade litellm` |
| Supports dynamic provider/model switching | Needs schema redesign | Works out of the box — pure function |
| Server restart needed for new model | Yes | No |
| Unknown model | Silently wrong | Returns `None` (cost stays `NULL`) |
| Supports all providers | Only what you seed | OpenAI, Azure, Vertex AI, 100+ others |

### Key API

```python
from litellm import cost_per_token

# Pure function — no startup dependency, no global state
prompt_cost, completion_cost = cost_per_token(
    model="gpt-4o-mini",
    prompt_tokens=423,
    completion_tokens=81,
    custom_llm_provider="openai",   # or "azure", "vertex_ai"
)
total_cost = prompt_cost + completion_cost   # e.g. 0.000112 USD
```

---

## 3. Provider Name Standardization

This section is the canonical reference. Three naming layers exist and must never be confused.

### 3.1 Canonical Provider Name Table

| Layer | OpenAI | Azure OpenAI | Vertex AI |
|---|---|---|---|
| **`LLM_PROVIDER` env var** | `openai` | `azure_openai` | `vertex_ai` |
| **`gateway.provider_name`** (reported by adapter) | `openai` | `azure_openai` | `vertex_ai` |
| **LiteLLM `custom_llm_provider`** | `openai` | `azure` | `vertex_ai` |

> **The only translation needed:** internal `azure_openai` → LiteLLM `azure`.
> `_PROVIDER_MAP` in `cost_calculator.py` is the **single** place this translation lives.

### 3.2 Model Name Behaviour Per Provider

| Provider | What `gateway.model` returns | LiteLLM lookup behaviour |
|---|---|---|
| OpenAI | Canonical name from `LLM_MODEL` (e.g. `gpt-4o-mini`) | Direct match ✅ |
| Azure OpenAI | `AZURE_OPENAI_LLM_DEPLOYMENT` if set, else `LLM_MODEL` | See Section 7 ↓ |
| Vertex AI | Canonical name from `LLM_MODEL` (e.g. `gemini-2.5-flash`) | Direct match ✅ |

> **Future dynamic model selection:** When users pick provider and model via UI, `gateway.model`
> will return the canonical model name they selected (e.g. `gpt-4o-mini`, `gemini-2.5-flash`).
> `cost_per_token()` will resolve these directly — no special handling required.

### 3.3 PRD-Defined Env Variable Reference (No New Vars Added)

Per `llm_adapters_prd.md` Section 5.1 — complete standardized env reference:

| Variable | Provider | Default | Description |
|---|---|---|---|
| `LLM_PROVIDER` | All | `openai` | Adapter: `"openai"` \| `"azure_openai"` \| `"vertex_ai"` |
| `LLM_MODEL` | All | `gpt-4o-mini` | Canonical model name (e.g. `gpt-4o-mini`, `gemini-2.5-flash`) |
| `OPENAI_API_KEY` | OpenAI | — | OpenAI API key |
| `LLM_BASE_URL` | OpenAI | — | Optional custom base URL |
| `AZURE_AI_ENDPOINT` | Azure | — | Azure resource URL |
| `AZURE_AI_API_KEY` | Azure | — | Azure API key |
| `AZURE_AI_API_VERSION` | Azure | `2024-02-15-preview` | Azure REST API version |
| `AZURE_OPENAI_LLM_DEPLOYMENT` | Azure | *(falls back to `LLM_MODEL`)* | Azure deployment name |
| `VERTEX_AI_PROJECT` | Vertex AI | — | GCP project ID |
| `VERTEX_AI_LOCATION` | Vertex AI | `us-central1` | GCP region |
| `VERTEX_AI_CREDENTIALS_PATH` | Vertex AI | — | Optional service account JSON path |

> **No new env vars introduced by this plan.** Zero config changes required.

---

## 4. Architecture Overview

### Data Flow After Implementation

```
[Any HTTP turn — provider/model resolved dynamically per request]
    │
    ▼
Graph node calls capture_llm_call(provider, model, input_tokens, output_tokens)
    │
    ▼  [inside TraceManager — calling nodes unchanged]
calculate_llm_cost(provider, model, input_tokens, output_tokens)
    │
    ├── translate provider: "azure_openai" → "azure" (via _PROVIDER_MAP)
    ├── litellm.cost_per_token(model, prompt_tokens, completion_tokens, custom_llm_provider)
    │       ↳ reads bundled pricing JSON — < 0.05 ms, no network, no global state
    │       ↳ if model not found → returns None (never crashes, never returns 0)
    ▼
agent_llm_calls.estimated_cost = <computed USD cost>    ← stored per call
_trace_cost_totals[trace_id] += cost                    ← in-memory accumulator
_trace_token_totals[trace_id] += total_tokens           ← in-memory accumulator
    │
    ▼  [at finish_trace()]
agent_traces.estimated_cost = sum of all LLM calls in this turn
agent_traces.total_token_count = sum of all tokens in this turn
    │
    ▼
/api/v1/observability/metrics → total_cost reflects real numbers
```

### Why In-Memory Accumulators (Not a DB Query)

`finish_trace()` is called once at the end of every turn. Rather than querying
`agent_llm_calls` to sum up costs (an extra DB round-trip), `TraceManager` maintains
lightweight `dict` accumulators keyed by `trace_id`. They are initialized in `start_trace()`
and cleaned up (via `pop()`) in both `finish_trace()` and `fail_trace()` — no memory leak.

---

## 5. Files Changed

### New Files

| File | Purpose |
|---|---|
| `app/observability/cost_calculator.py` | Pure function `calculate_llm_cost()` — wraps `litellm.cost_per_token()` with provider name normalization and safe error handling. No startup state, no global registration. |

### Modified Files

| File | Change |
|---|---|
| `pyproject.toml` | Add `litellm>=1.40.0,<2.0.0` to `dependencies` |
| `app/observability/trace_manager.py` | Import `calculate_llm_cost`; auto-compute cost in `capture_llm_call()`; add `_trace_token_totals` and `_trace_cost_totals` per-trace accumulators; flush totals in `finish_trace()`; clean up in `fail_trace()` |
| `app/agents/graph/nodes/shared/field_extraction_node.py` | Fix pre-existing bug (PRD FR-4.5): replace hardcoded `provider="openai"` with `getattr(gateway, "provider_name", "openai")` |

### Unchanged Files (Zero Touch)

- `app/core/config.py` — no new env vars
- `app/main.py` — no startup hooks needed
- `app/agents/graph/nodes/supervisor_node.py` — already passes `getattr(gateway, "provider_name", "openai")` correctly
- `app/agents/graph/nodes/shared/response_generation_node.py`
- `app/agents/graph/nodes/faq/faq_node.py`
- `app/services/chat_orchestration_service.py`
- `app/db/models.py` — `estimated_cost` columns already exist
- All Alembic migrations — no schema changes needed
- All frontend files

---

## 6. Detailed Implementation

### 6.1 `pyproject.toml`

```toml
dependencies = [
    ...
    "litellm>=1.40.0,<2.0.0",
]
```

Pin `<2.0.0` to guard against breaking API changes while still receiving all patch-level
pricing updates automatically on `pip upgrade litellm`.

---

### 6.2 `app/observability/cost_calculator.py` (NEW)

```python
"""LLM cost calculator — pure litellm delegation (PRD FR-4.6).

All cost accounting is delegated 100% to litellm.cost_per_token().
No manual pricing formulas, no startup registration, no global state.

Works correctly with dynamic provider/model switching — the function
is a pure computation of (provider, model, input_tokens, output_tokens)
with no dependency on startup configuration or server state.

Pricing updates: pip install --upgrade litellm
"""

from __future__ import annotations

from decimal import Decimal

import structlog

log = structlog.get_logger(__name__)

# ─── Provider name translation ────────────────────────────────────────────────
# Maps internal gateway.provider_name → litellm custom_llm_provider key.
# This is the SINGLE canonical translation point. Do not duplicate elsewhere.
#
#   Internal (env / gateway)  →  LiteLLM
#   "openai"                  →  "openai"
#   "azure_openai"            →  "azure"     ← only difference
#   "vertex_ai"               →  "vertex_ai"
#
_PROVIDER_MAP: dict[str, str] = {
    "openai":       "openai",
    "azure_openai": "azure",
    "vertex_ai":    "vertex_ai",
}


def calculate_llm_cost(
    provider: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
) -> Decimal | None:
    """Return total USD cost for an LLM call, or None if the model is unknown.

    Pure function — no side effects, no startup dependency, no global state.
    Safe to call with any (provider, model) combination at any time, making it
    compatible with future UI-driven dynamic provider/model selection.

    Args:
        provider:      Internal provider name from gateway.provider_name.
                       One of: 'openai', 'azure_openai', 'vertex_ai'.
        model:         Model name as reported by gateway.model at call time.
        input_tokens:  Number of prompt/input tokens consumed.
        output_tokens: Number of completion/output tokens generated.

    Returns:
        Decimal cost in USD (e.g. Decimal('0.000112')), or None if the
        model/provider combination is not in LiteLLM's bundled pricing data.

        None means "cost unknown", NOT "this call was free".
        Decimal('0') is never returned for an unknown model.
    """
    if not model or input_tokens < 0 or output_tokens < 0:
        return None

    litellm_provider = _PROVIDER_MAP.get(provider, provider)

    try:
        from litellm import cost_per_token  # deferred — avoids import-time overhead

        prompt_cost, completion_cost = cost_per_token(
            model=model,
            prompt_tokens=input_tokens,
            completion_tokens=output_tokens,
            custom_llm_provider=litellm_provider,
        )
        total = Decimal(str(prompt_cost + completion_cost))
        log.debug(
            "llm_cost.calculated",
            provider=provider,
            litellm_provider=litellm_provider,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=str(total),
        )
        return total

    except Exception as exc:
        # NotFoundError for unregistered/unknown models — not a bug.
        # Cost stays NULL in DB. Will resolve automatically when:
        #   (a) pip upgrade litellm adds the model, OR
        #   (b) user selects a different model via UI
        log.debug(
            "llm_cost.model_not_found",
            provider=provider,
            litellm_provider=litellm_provider,
            model=model,
            reason=str(exc),
        )
        return None
```

---

### 6.3 `app/observability/trace_manager.py` — Five Targeted Changes

#### Change 1 — Import at top of file

```python
from app.observability.cost_calculator import calculate_llm_cost
```

#### Change 2 — `__init__()` — add per-trace accumulators

```python
def __init__(self, session: AsyncSession) -> None:
    # ... existing repo assignments ...
    self._active_trace_id: UUID | None = None
    self._run_start_times: dict[UUID, float] = {}
    self._trace_start_times: dict[UUID, float] = {}

    # Per-trace accumulators — keyed by trace_id UUID.
    # Initialized in start_trace(), cleared by pop() in finish_trace()/fail_trace().
    self._trace_token_totals: dict[UUID, int] = {}
    self._trace_cost_totals: dict[UUID, Decimal] = {}
```

#### Change 3 — `start_trace()` — initialize accumulators

```python
async def start_trace(self, ...) -> UUID | None:
    try:
        row = await self._trace_repo.create(...)
        self._active_trace_id = row.id
        self._trace_start_times[row.id] = time.monotonic()

        # Initialize per-trace accumulators for this turn
        self._trace_token_totals[row.id] = 0
        self._trace_cost_totals[row.id] = Decimal("0")

        log.info("trace.started", trace_id=str(row.id))
        return row.id
    except Exception:
        log.exception("trace_manager.start_trace.failed")
        return None
```

#### Change 4 — `capture_llm_call()` — auto-compute cost + accumulate

```python
async def capture_llm_call(
    self,
    trace_id: UUID,
    run_id: UUID,
    provider: str | None = None,
    model: str | None = None,
    temperature: Decimal | None = None,
    prompt_name: str | None = None,
    prompt_version: str | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    total_tokens: int | None = None,
    latency_ms: int | None = None,
    estimated_cost: Decimal | None = None,  # callers may pass explicit cost; we compute if None
    structured_output: dict[str, Any] | None = None,
    parse_success: bool | None = None,
    parse_error: str | None = None,
) -> None:
    try:
        # ── Auto-compute cost via litellm if caller did not provide it ──────
        # Works with any (provider, model) pair — no startup registration needed.
        # Compatible with future UI-driven dynamic provider/model switching.
        if estimated_cost is None and provider and model:
            if input_tokens is not None and output_tokens is not None:
                estimated_cost = calculate_llm_cost(
                    provider=provider,
                    model=model,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                )
        # ────────────────────────────────────────────────────────────────────

        await self._llm_repo.create(
            trace_id=trace_id,
            run_id=run_id,
            provider=provider,
            model=model,
            temperature=temperature,
            prompt_name=prompt_name,
            prompt_version=prompt_version,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            latency_ms=latency_ms,
            estimated_cost=estimated_cost,
            structured_output=structured_output,
            parse_success=parse_success,
            parse_error=parse_error,
        )

        # ── Accumulate into per-trace totals for finish_trace() rollup ──────
        if total_tokens:
            self._trace_token_totals[trace_id] = (
                self._trace_token_totals.get(trace_id, 0) + total_tokens
            )
        if estimated_cost is not None:
            self._trace_cost_totals[trace_id] = (
                self._trace_cost_totals.get(trace_id, Decimal("0")) + estimated_cost
            )
        # ────────────────────────────────────────────────────────────────────

    except Exception:
        log.exception(
            "trace_manager.capture_llm_call.failed",
            trace_id=str(trace_id),
            run_id=str(run_id),
            model=model,
        )
```

#### Change 5 — `finish_trace()` — flush accumulated totals to `agent_traces`

```python
async def finish_trace(
    self,
    trace_id: UUID,
    output_message: str,
    final_state: dict[str, Any] | None = None,
    status: str = "SUCCESS",
    active_agent: str | None = None,
    intent: str | None = None,
    service_category: str | None = None,
    sub_category: str | None = None,
    workflow_stage_before: str | None = None,
    workflow_stage_after: str | None = None,
    total_token_count: int | None = None,
    estimated_cost: Decimal | None = None,
) -> None:
    try:
        start = self._trace_start_times.pop(trace_id, None)
        latency_ms = int((time.monotonic() - start) * 1000) if start else None

        # ── Read and clear per-trace accumulators ──────────────────────────
        accumulated_tokens = self._trace_token_totals.pop(trace_id, 0) or None
        accumulated_cost = self._trace_cost_totals.pop(trace_id, Decimal("0"))
        accumulated_cost = accumulated_cost if accumulated_cost > Decimal("0") else None
        # ──────────────────────────────────────────────────────────────────

        await self._trace_repo.complete(
            trace_id,
            status=status,
            output_message=output_message,
            active_agent=active_agent,
            intent=intent,
            service_category=service_category,
            sub_category=sub_category,
            workflow_stage_before=workflow_stage_before,
            workflow_stage_after=workflow_stage_after,
            total_latency_ms=latency_ms,
            # Explicit caller values take precedence; fall back to accumulators
            total_token_count=total_token_count or accumulated_tokens,
            estimated_cost=estimated_cost or accumulated_cost,
        )
        ...
    except Exception:
        log.exception("trace_manager.finish_trace.failed", trace_id=str(trace_id))
```

Also update `fail_trace()` — clean up accumulators even on failure:

```python
async def fail_trace(self, trace_id: UUID, ...) -> None:
    try:
        ...
        # Clean up accumulators — cost is not written for failed traces
        self._trace_token_totals.pop(trace_id, None)
        self._trace_cost_totals.pop(trace_id, None)
        ...
    except Exception:
        log.exception("trace_manager.fail_trace.failed", trace_id=str(trace_id))
```

---

### 6.4 `field_extraction_node.py` — Fix Pre-existing Provider Bug (PRD FR-4.5)

Current code (BUG — always records `"openai"` regardless of actual provider):

```python
await tm.capture_llm_call(
    ...
    provider="openai",    # ← hardcoded — wrong for Azure OpenAI and Vertex AI
    model=gateway.model,
    ...
)
```

Fixed (one-line change — reads actual provider from gateway):

```python
await tm.capture_llm_call(
    ...
    provider=getattr(gateway, "provider_name", "openai"),   # ← correct for all providers
    model=gateway.model,
    ...
)
```

This fix is required for correct cost calculation — `calculate_llm_cost("openai", "gpt-4o-mini", ...)`
and `calculate_llm_cost("azure_openai", "gpt-4o-mini", ...)` resolve to different LiteLLM
provider keys and potentially different pricing tiers.

---

## 7. Azure Deployment Name Handling (Current & Future)

### Current Static Config (No Special Handling Needed)

Per the PRD env table, `AZURE_OPENAI_LLM_DEPLOYMENT` is optional and falls back to `LLM_MODEL`
when not set. There are two practical scenarios:

| Scenario | `AZURE_OPENAI_LLM_DEPLOYMENT` | `gateway.model` | LiteLLM lookup | Cost result |
|---|---|---|---|---|
| Not set (recommended) | *(not set)* | `LLM_MODEL` value (e.g. `gpt-4o-mini`) | `azure/gpt-4o-mini` | ✅ Accurate |
| Set to canonical name | `gpt-4o-mini` | `gpt-4o-mini` | `azure/gpt-4o-mini` | ✅ Accurate |
| Set to custom name | `my-gpt4o-prod` | `my-gpt4o-prod` | Not found | `NULL` stored |

> **Recommendation for the custom name scenario:** Set `LLM_MODEL` to the underlying canonical
> model name (e.g. `gpt-4o-mini`) and leave `AZURE_OPENAI_LLM_DEPLOYMENT` unset or set it
> to the same canonical name. Azure will still route to your named deployment because the
> `AzureOpenAIGateway` uses `AZURE_OPENAI_LLM_DEPLOYMENT` for the API call — the `LLM_MODEL`
> value is only used as the pricing lookup fallback by LiteLLM.

### Future Dynamic Model Selection via UI

When users can pick provider and model via the UI:

- The UI will present canonical model names (`gpt-4o-mini`, `gpt-4o`, `gemini-2.5-flash`, etc.)
- The selected model name will be passed directly to the gateway at call time
- `gateway.model` will return that canonical name
- `cost_per_token(model="gpt-4o-mini", custom_llm_provider="azure")` resolves directly ✅
- **No code change needed** — `calculate_llm_cost()` already handles this correctly because it
  is a pure function with no startup dependency

---

## 8. DB Impact (No Migration Required)

Both target columns already exist. No Alembic migration is needed.

| Table | Column | Type | Status Before | Status After |
|---|---|---|---|---|
| `agent_llm_calls` | `estimated_cost` | `NUMERIC(12,6)` | always `NULL` | USD cost per call |
| `agent_traces` | `estimated_cost` | `NUMERIC(12,6)` | always `NULL` | sum of all calls in turn |
| `agent_traces` | `total_token_count` | `INTEGER` | always `NULL` | sum of all tokens in turn |

The existing metrics query in `app/observability/api/metrics.py` already uses
`SUM(estimated_cost)` — it returns real numbers immediately with no further changes.

---

## 9. Provider Coverage After Implementation

| Provider | `gateway.provider_name` | LiteLLM key used | Coverage |
|---|---|---|---|
| OpenAI | `openai` | `openai` | ✅ All GPT-4o, GPT-4, GPT-3.5 variants |
| Azure OpenAI | `azure_openai` | `azure` | ✅ When `gateway.model` is a canonical model name |
| Vertex AI | `vertex_ai` | `vertex_ai` | ✅ All Gemini 1.0/1.5/2.0 Pro, Flash |
| Any unknown model | any | any | `NULL` stored — never crashes, never returns `$0` |

---

## 10. Updating Pricing in Future

When any provider changes their pricing:

```bash
# In your Dockerfile / CI / deployment pipeline:
pip install --upgrade litellm

# Redeploy — updated pricing takes effect on the next request.
# No code change. No DB update. No admin action. No server restart.
```

LiteLLM releases patch versions for pricing updates, typically within 24–48 hours of a
provider announcement.

---

## 11. Implementation Checklist

- [ ] **Step 1** — Add `litellm>=1.40.0,<2.0.0` to `pyproject.toml` dependencies
- [ ] **Step 2** — Create `app/observability/cost_calculator.py`
  - [ ] `_PROVIDER_MAP` dict: `"azure_openai" → "azure"`, others pass-through
  - [ ] `calculate_llm_cost(provider, model, input_tokens, output_tokens) -> Decimal | None`
  - [ ] Deferred `import litellm` inside function body (no startup overhead)
  - [ ] Returns `None` on any exception (never crashes, never returns `Decimal("0")` for unknown models)
  - [ ] Structured `log.debug` on both success and model-not-found paths
- [ ] **Step 3** — Edit `app/observability/trace_manager.py`
  - [ ] Add `from app.observability.cost_calculator import calculate_llm_cost`
  - [ ] Add `_trace_token_totals: dict[UUID, int]` and `_trace_cost_totals: dict[UUID, Decimal]` to `__init__`
  - [ ] Initialize both accumulators in `start_trace()` when row is created
  - [ ] Auto-compute cost + accumulate in `capture_llm_call()`
  - [ ] Flush accumulated totals in `finish_trace()` via `pop()` before `trace_repo.complete()`
  - [ ] Discard accumulators via `pop()` in `fail_trace()`
- [ ] **Step 4** — Fix `app/agents/graph/nodes/shared/field_extraction_node.py`
  - [ ] Replace `provider="openai"` with `getattr(gateway, "provider_name", "openai")`
- [ ] **Step 5** — Install and verify: `pip install litellm && pytest tests/unit/`
- [ ] **Step 6** — Run a live chat turn and verify via SQL:
  ```sql
  SELECT model, provider, input_tokens, output_tokens, estimated_cost
  FROM agent_llm_calls ORDER BY created_at DESC LIMIT 5;

  SELECT active_agent, total_token_count, estimated_cost
  FROM agent_traces ORDER BY created_at DESC LIMIT 5;
  ```

---

## 12. Testing

### Unit Tests (`tests/unit/test_cost_calculator.py`)

```python
from decimal import Decimal
from app.observability.cost_calculator import calculate_llm_cost

def test_openai_known_model_returns_positive_decimal():
    cost = calculate_llm_cost("openai", "gpt-4o-mini", 1000, 200)
    assert cost is not None and isinstance(cost, Decimal) and cost > 0

def test_unknown_model_returns_none_not_zero():
    """Unknown models must return None, not Decimal('0')."""
    cost = calculate_llm_cost("openai", "made-up-model-xyz-99", 1000, 200)
    assert cost is None

def test_azure_provider_name_translates_to_azure_key():
    """Internal 'azure_openai' must map to litellm 'azure' key."""
    cost = calculate_llm_cost("azure_openai", "gpt-4o-mini", 1000, 200)
    assert cost is not None and cost > 0

def test_vertex_ai_gemini_model():
    cost = calculate_llm_cost("vertex_ai", "gemini-1.5-flash", 1000, 200)
    assert cost is not None and cost > 0

def test_negative_tokens_returns_none():
    assert calculate_llm_cost("openai", "gpt-4o-mini", -1, 100) is None

def test_empty_model_returns_none():
    assert calculate_llm_cost("openai", "", 100, 50) is None

def test_different_providers_same_model_may_differ():
    """OpenAI and Azure may have different pricing for the same model."""
    openai_cost = calculate_llm_cost("openai", "gpt-4o-mini", 1000, 200)
    azure_cost  = calculate_llm_cost("azure_openai", "gpt-4o-mini", 1000, 200)
    # Both should return a cost (not None)
    assert openai_cost is not None
    assert azure_cost is not None
```

---

## 13. Risks & Mitigations

| Risk | Likelihood | Mitigation |
|---|---|---|
| LiteLLM not yet updated for a newly released model | Low | `cost_per_token` returns `None` → cost stored as `NULL`. Resolves on next `pip upgrade litellm`. User can also switch to a known model via UI in the future. |
| Azure custom deployment name not in LiteLLM pricing | Medium (current) → Low (future) | Currently: `NULL` stored — no crash. Future: UI presents canonical model names, so deployment name issue disappears entirely. |
| Breaking change in `cost_per_token` signature | Very Low | Pinned to `<2.0.0`. Broad `except Exception` in `calculate_llm_cost` — degraded to `NULL`, chatbot unaffected. |
| `litellm` import adds startup latency | Very Low | Import is deferred inside the function body. Zero startup cost. |
| TraceManager accumulator memory leak | Very Low | `pop()` in both `finish_trace()` and `fail_trace()` guarantees cleanup regardless of turn outcome. |

---

## 14. Out of Scope (Future Work)

- **UI-driven dynamic provider and model selection** — this plan is designed to be fully
  compatible with it. When implemented, `calculate_llm_cost()` will work correctly with no
  changes because it is already a stateless per-call pure function.
- **Per-session or per-user cost dashboards** — data will be in DB; dashboards are separate.
- **Budget alerts or cost caps** — can be built on `estimated_cost` data once populated.
- **Embedding cost tracking** — `text-embedding-3-small` calls (RAG FAQ node) are not currently
  routed through `capture_llm_call`. Separate tracking gap.
- **`response_generation_node` and `faq_node` LLM call capture** — these nodes make LLM calls
  but currently discard token counts. Adding `capture_llm_call` to those nodes is independent
  work that will benefit from dynamic cost once this plan is implemented.
