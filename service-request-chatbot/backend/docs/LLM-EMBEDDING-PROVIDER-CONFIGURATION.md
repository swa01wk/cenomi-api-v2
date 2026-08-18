# LLM & Embedding Provider Configuration Guide

This guide explains how to configure the AI provider backend for the Service Request Chatbot API.
All configuration is done exclusively through environment variables in the `.env` file.

---

## Table of Contents

1. [How Provider Selection Works](#1-how-provider-selection-works)
2. [Choosing the Right Provider](#2-choosing-the-right-provider)
3. [LLM Provider Configuration](#3-llm-provider-configuration)
   - [OpenAI](#31-openai--openai-compatible)
   - [Azure OpenAI (classic)](#32-azure-openai-classic)
   - [Azure AI Foundry (new-style)](#33-azure-ai-foundry-new-style-openaicompatible-endpoint)
   - [Google Vertex AI](#34-google-vertex-ai)
4. [Embedding Provider Configuration](#4-embedding-provider-configuration)
   - [OpenAI](#41-openai--openai-compatible)
   - [Azure OpenAI (classic)](#42-azure-openai-classic)
   - [Azure AI Foundry (new-style)](#43-azure-ai-foundry-new-style)
   - [Google Vertex AI](#44-google-vertex-ai)
   - [Disable RAG / No Embeddings](#45-disable-rag--no-embeddings)
5. [Search Provider Configuration](#5-search-provider-configuration)
6. [Full Example Configurations](#6-full-example-configurations)
7. [Common Mistakes](#7-common-mistakes)
8. [Environment Variable Reference](#8-environment-variable-reference)

---

## 1. How Provider Selection Works

Two independent environment variables control which backend is used:

| Variable | Controls | Default |
|---|---|---|
| `LLM_PROVIDER` | Which AI model handles chat completions | `openai` |
| `EMBEDDING_PROVIDER` | Which AI model generates text embeddings for RAG search | `openai` |

These two providers are **completely independent** — you can mix and match freely.
For example: Azure AI Foundry for LLM + Vertex AI for embeddings.

```
LLM_PROVIDER=azure_openai    # chat completions → Azure
EMBEDDING_PROVIDER=vertex_ai # embeddings → Google Vertex AI
```

---

## 2. Choosing the Right Provider

| Your Setup | `LLM_PROVIDER` | `EMBEDDING_PROVIDER` |
|---|---|---|
| Standard OpenAI API key | `openai` | `openai` |
| Azure OpenAI Service (`*.openai.azure.com`) | `azure_openai` | `azure_openai` |
| Azure AI Foundry (`*.services.ai.azure.com/openai/v1/`) | `openai` + `LLM_BASE_URL` | `openai` + `EMBEDDING_BASE_URL` |
| Google Cloud Vertex AI | `vertex_ai` | `vertex_ai` |

> **Important — Azure AI Foundry vs Azure OpenAI Service:**
> These are two different Azure products with different URL patterns:
>
> - **Azure OpenAI Service** → endpoint ends in `.openai.azure.com` → use `azure_openai`
> - **Azure AI Foundry** → endpoint contains `.services.ai.azure.com/openai/v1/` → use `openai` with `LLM_BASE_URL`
>
> Using `azure_openai` with an AI Foundry endpoint will result in `404 Resource Not Found` errors
> because the SDK constructs deployment-based paths (`/openai/deployments/<name>/`) which do not
> exist on the Foundry endpoint.

---

## 3. LLM Provider Configuration

### 3.1 OpenAI / OpenAI-Compatible

Use this for:
- Standard OpenAI API (`api.openai.com`)
- **Azure AI Foundry** (`*.services.ai.azure.com/openai/v1/`)
- Any third-party OpenAI-compatible endpoint

```dotenv
LLM_PROVIDER=openai
LLM_MODEL=gpt-4o-mini            # model name as returned by the provider
OPENAI_API_KEY=sk-...            # your API key (also used as Bearer token for Foundry)
LLM_BASE_URL=                    # leave blank for standard OpenAI; set for Foundry / custom endpoints
LLM_CONFIDENCE_THRESHOLD=0.6     # optional — routing confidence threshold (0.0–1.0)
```

**For Azure AI Foundry specifically**, set `LLM_BASE_URL` to the base of the `/openai/v1/` path:

```dotenv
LLM_PROVIDER=openai
LLM_MODEL=gpt-5.4-mini
OPENAI_API_KEY=<your-foundry-api-key>
LLM_BASE_URL=https://<resource>.services.ai.azure.com/openai/v1
```

---

### 3.2 Azure OpenAI (classic)

Use this when your Azure endpoint ends in `.openai.azure.com`.

```dotenv
LLM_PROVIDER=azure_openai
LLM_MODEL=gpt-4o                              # used as deployment name if AZURE_OPENAI_LLM_DEPLOYMENT is not set
AZURE_OPENAI_LLM_DEPLOYMENT=my-deployment     # optional — explicit deployment name (overrides LLM_MODEL)
AZURE_AI_ENDPOINT=https://<instance>.openai.azure.com
AZURE_AI_API_KEY=<your-azure-api-key>
AZURE_AI_API_VERSION=2024-02-15-preview       # API version for your deployment
LLM_CONFIDENCE_THRESHOLD=0.6
```

> `AZURE_OPENAI_LLM_DEPLOYMENT` is optional. When omitted, `LLM_MODEL` is used as the deployment name.
> If your deployment name differs from the model name, always set `AZURE_OPENAI_LLM_DEPLOYMENT` explicitly.

---

### 3.3 Azure AI Foundry (new-style, OpenAI-compatible endpoint)

Azure AI Foundry exposes models through an OpenAI-compatible REST API. The endpoint URL contains
`/openai/v1/`. Use the **`openai` provider** with a custom `LLM_BASE_URL`:

```dotenv
LLM_PROVIDER=openai
LLM_MODEL=gpt-5.4-mini
OPENAI_API_KEY=<your-foundry-api-key>
LLM_BASE_URL=https://<resource>.services.ai.azure.com/openai/v1
```

How to build `LLM_BASE_URL` from the endpoint shown in Azure AI Studio:

```
Full endpoint shown in Studio:
  https://<resource>.services.ai.azure.com/openai/v1/responses

Remove the trailing path to get the base URL:
  https://<resource>.services.ai.azure.com/openai/v1   ← use this as LLM_BASE_URL
```

---

### 3.4 Google Vertex AI

Requires the `google-genai` Python package. Install with:
```bash
uv sync --extra vertex_ai
```

```dotenv
LLM_PROVIDER=vertex_ai
LLM_MODEL=gemini-2.5-flash               # Vertex AI model name
VERTEX_AI_PROJECT=my-gcp-project         # GCP project ID
VERTEX_AI_LOCATION=us-central1           # GCP region (default: us-central1)
VERTEX_AI_CREDENTIALS_PATH=              # optional — path to service account JSON key file
                                         # leave blank to use Application Default Credentials (ADC)
```

---

## 4. Embedding Provider Configuration

### 4.1 OpenAI / OpenAI-Compatible

```dotenv
EMBEDDING_PROVIDER=openai
EMBEDDING_MODEL=text-embedding-3-small   # model name
# OPENAI_API_KEY is reused from the LLM section above
EMBEDDING_BASE_URL=                      # leave blank for standard OpenAI; set for Foundry / custom endpoints
```

**For Azure AI Foundry:**

```dotenv
EMBEDDING_PROVIDER=openai
EMBEDDING_MODEL=text-embedding-3-small
# OPENAI_API_KEY is reused from the LLM section
EMBEDDING_BASE_URL=https://<resource>.services.ai.azure.com/openai/v1
```

---

### 4.2 Azure OpenAI (classic)

```dotenv
EMBEDDING_PROVIDER=azure_openai
EMBEDDING_MODEL=text-embedding-3-small
# AZURE_AI_ENDPOINT, AZURE_AI_API_KEY, AZURE_AI_API_VERSION are reused from the LLM section
# (when LLM is also azure_openai and both use the same Azure resource)
```

If the embedding resource is **different** from the LLM resource, set the `AZURE_AI_*` vars to the
embedding resource's credentials. Note that `AZURE_AI_*` vars are shared — if LLM and embeddings
use different Azure resources, contact your infrastructure team for a custom configuration.

---

### 4.3 Azure AI Foundry (new-style)

Same as section 4.1 — use `EMBEDDING_PROVIDER=openai` with `EMBEDDING_BASE_URL`:

```dotenv
EMBEDDING_PROVIDER=openai
EMBEDDING_MODEL=text-embedding-3-small
EMBEDDING_BASE_URL=https://<resource>.services.ai.azure.com/openai/v1
```

---

### 4.4 Google Vertex AI

Requires the `google-genai` package (same as LLM Vertex AI).

```dotenv
EMBEDDING_PROVIDER=vertex_ai
VERTEX_AI_EMBEDDING_MODEL=text-embedding-005   # Vertex AI embedding model name (default: text-embedding-005)
# VERTEX_AI_PROJECT, VERTEX_AI_LOCATION, VERTEX_AI_CREDENTIALS_PATH
# are shared with the LLM section if both use Vertex AI
```

---

### 4.5 Disable RAG / No Embeddings

Leave `EMBEDDING_PROVIDER` unset (or set it to `openai` without `OPENAI_API_KEY`). The FAQ node
falls back to a static prompt silently — no errors are raised.

```dotenv
# Simply do not set any of the embedding or search vars.
# The chatbot works normally; only FAQ/RAG-assisted responses are unavailable.
```

---

## 5. Search Provider Configuration

Embeddings are only useful when paired with a vector search backend. Currently the only supported
search backend is **Azure AI Search**.

```dotenv
SEARCH_PROVIDER=azure_search
AZURE_SEARCH_ENDPOINT=https://<instance>.search.windows.net
AZURE_SEARCH_API_KEY=<your-search-admin-key>
AZURE_SEARCH_INDEX_NAME=cenomi-help-index
```

> If `SEARCH_PROVIDER` is not set or the credentials are missing, the search layer is skipped and
> the chatbot degrades gracefully to static FAQ responses.

---

## 6. Full Example Configurations

### Example A — Standard OpenAI (LLM + Embeddings)

```dotenv
LLM_PROVIDER=openai
LLM_MODEL=gpt-4o-mini
OPENAI_API_KEY=sk-...
LLM_CONFIDENCE_THRESHOLD=0.6

EMBEDDING_PROVIDER=openai
EMBEDDING_MODEL=text-embedding-3-small

SEARCH_PROVIDER=azure_search
AZURE_SEARCH_ENDPOINT=https://my-search.search.windows.net
AZURE_SEARCH_API_KEY=...
AZURE_SEARCH_INDEX_NAME=cenomi-help-index
```

---

### Example B — Azure OpenAI Service (LLM + Embeddings, same resource)

```dotenv
LLM_PROVIDER=azure_openai
LLM_MODEL=gpt-4o
AZURE_OPENAI_LLM_DEPLOYMENT=gpt-4o-deployment
AZURE_AI_ENDPOINT=https://my-instance.openai.azure.com
AZURE_AI_API_KEY=...
AZURE_AI_API_VERSION=2024-02-15-preview
LLM_CONFIDENCE_THRESHOLD=0.6

EMBEDDING_PROVIDER=azure_openai
EMBEDDING_MODEL=text-embedding-3-small
# AZURE_AI_ENDPOINT / AZURE_AI_API_KEY / AZURE_AI_API_VERSION reused above

SEARCH_PROVIDER=azure_search
AZURE_SEARCH_ENDPOINT=https://my-search.search.windows.net
AZURE_SEARCH_API_KEY=...
AZURE_SEARCH_INDEX_NAME=cenomi-help-index
```

---

### Example C — Azure AI Foundry (LLM + Embeddings, same resource)

```dotenv
LLM_PROVIDER=openai
LLM_MODEL=gpt-5.4-mini
OPENAI_API_KEY=<foundry-api-key>
LLM_BASE_URL=https://<resource>.services.ai.azure.com/openai/v1
LLM_CONFIDENCE_THRESHOLD=0.6

EMBEDDING_PROVIDER=openai
EMBEDDING_MODEL=text-embedding-3-small
# OPENAI_API_KEY reused above
EMBEDDING_BASE_URL=https://<resource>.services.ai.azure.com/openai/v1

SEARCH_PROVIDER=azure_search
AZURE_SEARCH_ENDPOINT=https://my-search.search.windows.net
AZURE_SEARCH_API_KEY=...
AZURE_SEARCH_INDEX_NAME=cenomi-help-index
```

---

### Example D — Google Vertex AI (LLM + Embeddings)

```dotenv
LLM_PROVIDER=vertex_ai
LLM_MODEL=gemini-2.5-flash
VERTEX_AI_PROJECT=my-gcp-project
VERTEX_AI_LOCATION=us-central1
VERTEX_AI_CREDENTIALS_PATH=/secrets/service-account.json
LLM_CONFIDENCE_THRESHOLD=0.6

EMBEDDING_PROVIDER=vertex_ai
VERTEX_AI_EMBEDDING_MODEL=text-embedding-005
# VERTEX_AI_PROJECT / VERTEX_AI_LOCATION / VERTEX_AI_CREDENTIALS_PATH reused above

SEARCH_PROVIDER=azure_search
AZURE_SEARCH_ENDPOINT=https://my-search.search.windows.net
AZURE_SEARCH_API_KEY=...
AZURE_SEARCH_INDEX_NAME=cenomi-help-index
```

---

### Example E — Mixed: Azure AI Foundry (LLM) + No RAG

```dotenv
LLM_PROVIDER=openai
LLM_MODEL=gpt-5.4-mini
OPENAI_API_KEY=<foundry-api-key>
LLM_BASE_URL=https://<resource>.services.ai.azure.com/openai/v1
LLM_CONFIDENCE_THRESHOLD=0.6

# No EMBEDDING_PROVIDER or SEARCH vars — RAG disabled, static FAQ fallback used.
```

---

## 7. Common Mistakes

| Symptom | Likely Cause | Fix |
|---|---|---|
| `404 Resource Not Found` on Azure | Using `azure_openai` provider with an AI Foundry endpoint | Switch to `LLM_PROVIDER=openai` + set `LLM_BASE_URL` (see §3.3) |
| `404` with correct Azure OpenAI endpoint | `LLM_MODEL` doesn't match the deployment name | Set `AZURE_OPENAI_LLM_DEPLOYMENT` to the exact deployment name from Azure AI Studio |
| Embedding returns `None`, RAG silent | `OPENAI_API_KEY` or Azure credentials missing | Check that credentials are set and non-empty |
| `RuntimeError: google-genai SDK not installed` | `vertex_ai` selected but package missing | Run `uv sync --extra vertex_ai` |
| `RuntimeError: VERTEX_AI_PROJECT is required` | `VERTEX_AI_PROJECT` not set | Add `VERTEX_AI_PROJECT=<your-gcp-project-id>` |
| Embeddings work but search returns empty | `SEARCH_PROVIDER` / `AZURE_SEARCH_*` vars missing | Fill in all three `AZURE_SEARCH_*` vars |

---

## 8. Environment Variable Reference

### Always Required

| Variable | Description | Default |
|---|---|---|
| `LLM_PROVIDER` | LLM backend selector: `openai`, `azure_openai`, `vertex_ai` | `openai` |
| `LLM_MODEL` | Model or deployment name for chat completions | `gpt-4o-mini` |
| `LLM_CONFIDENCE_THRESHOLD` | Minimum routing confidence (0.0–1.0) | `0.6` |
| `EMBEDDING_PROVIDER` | Embedding backend: `openai`, `azure_openai`, `vertex_ai` | `openai` |
| `EMBEDDING_MODEL` | Embedding model name | `text-embedding-3-small` |

### OpenAI / OpenAI-Compatible

| Variable | Used by | Description |
|---|---|---|
| `OPENAI_API_KEY` | LLM + Embedding | API key (also works as Bearer token for Foundry endpoints) |
| `LLM_BASE_URL` | LLM | Custom base URL; leave blank for standard `api.openai.com` |
| `EMBEDDING_BASE_URL` | Embedding | Custom base URL for embeddings; leave blank for standard OpenAI |

### Azure OpenAI (classic — `*.openai.azure.com`)

| Variable | Used by | Description |
|---|---|---|
| `AZURE_AI_ENDPOINT` | LLM + Embedding | Azure OpenAI resource endpoint (base URL, no trailing path) |
| `AZURE_AI_API_KEY` | LLM + Embedding | Azure OpenAI API key |
| `AZURE_AI_API_VERSION` | LLM + Embedding | API version, e.g. `2024-02-15-preview` |
| `AZURE_OPENAI_LLM_DEPLOYMENT` | LLM only | Explicit deployment name; overrides `LLM_MODEL` when set |

### Google Vertex AI

| Variable | Used by | Description |
|---|---|---|
| `VERTEX_AI_PROJECT` | LLM + Embedding | GCP project ID |
| `VERTEX_AI_LOCATION` | LLM + Embedding | GCP region (default: `us-central1`) |
| `VERTEX_AI_CREDENTIALS_PATH` | LLM + Embedding | Path to service account JSON; blank = use ADC |
| `VERTEX_AI_EMBEDDING_MODEL` | Embedding only | Vertex AI embedding model name (default: `text-embedding-005`) |

### Azure AI Search (RAG)

| Variable | Description |
|---|---|
| `SEARCH_PROVIDER` | Set to `azure_search` to enable RAG |
| `AZURE_SEARCH_ENDPOINT` | Azure AI Search resource endpoint |
| `AZURE_SEARCH_API_KEY` | Azure AI Search admin or query key |
| `AZURE_SEARCH_INDEX_NAME` | Name of the search index |
