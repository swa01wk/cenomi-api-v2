# PRD: Embedding Provider Registry & Vertex AI Embedding Adapter

**Status:** Draft  
**Date:** 2026-08-10  
**Area:** `service-request-chatbot/backend` — RAG / Embeddings layer

---

## 1. Background

The LLM layer (`app/agents/llm/`) supports three providers — `openai`, `azure_openai`, and `vertex_ai` — via a formal `LLMProviderRegistry` pattern: each provider is registered with a factory function, singletons are cached, and the active provider is resolved from `LLM_PROVIDER` env var at startup.

The embedding layer (`app/integrations/embeddings/`) was built later and only covers two of the three providers:

| Provider | LLM adapter | Embedding adapter |
|---|---|---|
| `openai` | ✅ | ✅ |
| `azure_openai` | ✅ | ✅ |
| `vertex_ai` | ✅ | ❌ missing |

Selection is also done via a plain `if/else` block in `factory.py` rather than a registry, making it harder to extend with new providers.

---

## 2. Goals

1. **Implement `VertexAIEmbeddingProvider`** — a Vertex AI embedding adapter that uses the `google-genai` SDK (`text-embedding-005` or a configurable model), matching the error-handling and interface conventions of the two existing embedding providers.
2. **Introduce `EmbeddingProviderRegistry`** — a registry class mirroring `LLMProviderRegistry` exactly (register/get/list/clear_cache) so providers can be added without touching `factory.py`.
3. **Refactor `factory.py`** — replace the `if/else` block in `get_embedding_provider()` with a registry lookup, and update docstrings/comments to include `vertex_ai`.
4. **Extend config** — allow `vertex_ai` as a valid value for `EMBEDDING_PROVIDER`; reuse the existing `VERTEX_AI_*` vars (same as the LLM side) plus a new `VERTEX_AI_EMBEDDING_MODEL` env var.
5. **Update `.env.example`** — add Option D (Vertex AI embeddings) alongside existing A/B/C options.
6. **Add unit tests** — `tests/unit/test_embedding_adapters.py` covering the registry, all three providers, and the factory.

---

## 3. Non-Goals

- No changes to the search layer (`AzureSearchRepository`).
- No changes to the LLM adapter layer.
- No infrastructure/deployment changes.
- Not adding a fourth search provider.

---

## 4. Detailed Design

### 4.1 New file: `app/integrations/embeddings/vertex_ai_embed.py`

Uses `google-genai` SDK in Vertex AI mode (same import pattern as `VertexAIGateway`):

```
VertexAIEmbeddingProvider(
    model: str = "text-embedding-005",
    project: str | None = None,
    location: str = "us-central1",
    credentials_path: str | None = None,
)
```

- Calls `client.aio.models.embed_content(model=..., contents=[text])` async.
- Returns `list[float]` on success, `None` on any SDK or network error.
- Soft-imports `google.genai`; raises `RuntimeError` at init time if SDK absent (same pattern as `VertexAIGateway`).
- Has a `@classmethod from_settings()` factory.

### 4.2 New file: `app/integrations/embeddings/registry.py`

`EmbeddingProviderRegistry` — structurally identical to `LLMProviderRegistry`:

```python
class EmbeddingProviderRegistry:
    _factories: dict[str, Callable[[], EmbeddingProvider]] = {}
    _instances: dict[str, EmbeddingProvider] = {}

    register_provider(name, factory)
    get_provider(name) -> EmbeddingProvider        # raises ValueError if unknown
    list_providers() -> list[str]
    clear_cache()
```

Built-in registrations at module load:
- `"openai"` → `OpenAIEmbeddingProvider.from_settings()`
- `"azure_openai"` → `AzureOpenAIEmbeddingProvider.from_settings()`
- `"vertex_ai"` → `VertexAIEmbeddingProvider.from_settings()`

`from_settings()` class methods are also added to `OpenAIEmbeddingProvider` and `AzureOpenAIEmbeddingProvider` for uniform registration.

### 4.3 Updated `app/integrations/factory.py`

`get_embedding_provider()` becomes:

```python
def get_embedding_provider() -> EmbeddingProvider | None:
    global _embedding_provider
    if _embedding_provider is not None:
        return _embedding_provider
    try:
        from app.integrations.embeddings.registry import EmbeddingProviderRegistry
        _embedding_provider = EmbeddingProviderRegistry.get_provider(settings.embedding_provider)
    except (ValueError, RuntimeError) as exc:
        log.debug("integrations.factory.embed_skip", reason=str(exc))
        return None
    return _embedding_provider
```

Credential validation moves inside each provider's `from_settings()` / `__init__`, where it raises `RuntimeError` if required vars are absent — factory catches and returns `None`.

### 4.4 Updated `app/core/config.py`

`embedding_provider` description updated to include `vertex_ai`:

```
description="Embedding backend: 'openai' (default), 'azure_openai', or 'vertex_ai'."
```

New field:

```python
vertex_ai_embedding_model: str = Field(
    default="text-embedding-005",
    validation_alias="VERTEX_AI_EMBEDDING_MODEL",
    description="Vertex AI embedding model (only when EMBEDDING_PROVIDER=vertex_ai).",
)
```

All existing `VERTEX_AI_*` vars (`vertex_ai_project`, `vertex_ai_location`, `vertex_ai_credentials_path`) are reused — no new auth vars needed.

### 4.5 Updated `.env.example`

Adds an "Option D" block beneath the existing embedding options:

```dotenv
# Option D — Vertex AI embeddings: set EMBEDDING_PROVIDER=vertex_ai
# Reuses VERTEX_AI_PROJECT / VERTEX_AI_LOCATION / VERTEX_AI_CREDENTIALS_PATH from LLM block.
# VERTEX_AI_EMBEDDING_MODEL=text-embedding-005
```

Also adds `LLM_PROVIDER` options (currently missing from `.env.example`):

```dotenv
# LLM_PROVIDER=openai (default) | azure_openai | vertex_ai
LLM_PROVIDER=openai

# Vertex AI (LLM and/or embedding)
# VERTEX_AI_PROJECT=my-gcp-project
# VERTEX_AI_LOCATION=us-central1
# VERTEX_AI_CREDENTIALS_PATH=/path/to/service-account.json
```

### 4.6 New file: `tests/unit/test_embedding_adapters.py`

Test coverage (all mocked, no network):

| Test | What it verifies |
|---|---|
| `test_openai_embed_success` | `OpenAIEmbeddingProvider.embed()` returns vector |
| `test_openai_embed_api_error` | returns `None` on `APIError` |
| `test_azure_openai_embed_success` | `AzureOpenAIEmbeddingProvider.embed()` returns vector |
| `test_azure_openai_embed_api_error` | returns `None` on `APIError` |
| `test_vertex_ai_embed_success` | `VertexAIEmbeddingProvider.embed()` returns vector (skipped if SDK absent) |
| `test_vertex_ai_embed_error` | returns `None` on SDK exception |
| `test_registry_register_and_get` | singleton caching + re-registration |
| `test_registry_unknown_provider` | raises `ValueError` |
| `test_registry_clear_cache` | clears cached instances |
| `test_factory_openai` | `get_embedding_provider()` returns correct type with env patched |
| `test_factory_azure_openai` | same for `azure_openai` |
| `test_factory_vertex_ai` | same for `vertex_ai` |
| `test_factory_missing_credentials` | returns `None` gracefully |

---

## 5. File Change Summary

| File | Action |
|---|---|
| `app/integrations/embeddings/vertex_ai_embed.py` | **Create** |
| `app/integrations/embeddings/registry.py` | **Create** |
| `app/integrations/embeddings/openai_embed.py` | **Edit** — add `from_settings()` |
| `app/integrations/embeddings/azure_openai_embed.py` | **Edit** — add `from_settings()` |
| `app/integrations/factory.py` | **Edit** — replace if/else with registry |
| `app/core/config.py` | **Edit** — add `vertex_ai_embedding_model`, update description |
| `.env.example` | **Edit** — add Vertex AI embedding block + LLM_PROVIDER docs |
| `tests/unit/test_embedding_adapters.py` | **Create** |

---

## 6. Acceptance Criteria

- [ ] `EMBEDDING_PROVIDER=vertex_ai` with valid `VERTEX_AI_PROJECT` returns a non-None embedding provider.
- [ ] `EMBEDDING_PROVIDER=vertex_ai` with missing `VERTEX_AI_PROJECT` returns `None` gracefully (RAG degrades silently).
- [ ] `EMBEDDING_PROVIDER=openai` and `EMBEDDING_PROVIDER=azure_openai` behave identically to today.
- [ ] Unknown `EMBEDDING_PROVIDER` value logs a warning and returns `None`.
- [ ] All new unit tests pass with `pytest tests/unit/test_embedding_adapters.py`.
- [ ] No existing tests broken.
- [ ] No linter errors.
