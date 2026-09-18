---
name: Dynamic LLM Provider UI
overview: Replace the static LLM provider selection with a DB-backed admin UI. Go backend encrypts the active provider identity and sends it as an opaque token per invocation. AI service decrypts the token and uses credentials populated in its environment variables (injected by infrastructure from Key Vault) to instantiate the correct provider.
todos:
  - id: go-db-migration
    content: Go backend — create llm_provider_configs table migration + seed 3 provider rows
    status: pending
  - id: go-routes
    content: "Go backend — 2 admin routes: GET list, PUT activate"
    status: pending
  - id: go-encrypt-and-pass
    content: "Go backend — AES-256-GCM encrypt {provider_name, model_name}; cache ciphertext; append llm_token to every AI service invocation payload"
    status: pending
  - id: ai-crypto
    content: "AI service — create app/agents/llm/crypto.py: AES-256-GCM decrypt llm_token → {provider_name, model_name}"
    status: pending
  - id: registry-selection
    content: AI service — add get_provider_by_name() to LLMProviderRegistry to instantiate providers using env credentials based on token
    status: pending
  - id: gateway-dynamic
    content: "AI service — add get_gateway_for_token(llm_token) to gateway.py: decrypt → select provider → build gateway"
    status: pending
  - id: invoke-endpoint
    content: AI service — update chat/invoke endpoint schema to accept optional llm_token field
    status: pending
  - id: frontend-page
    content: "Frontend — build admin UI page: provider table with activate/status toggles"
    status: pending
  - id: deps
    content: "AI service — add cryptography to pyproject.toml"
    status: pending
isProject: false
---

# Dynamic LLM Provider Configuration Architecture

## Design Constraints (from discussion)

- Fixed providers: `openai`, `azure_openai`, `vertex_ai` (as populated in DB/Env)
- Fixed model per provider — not user-editable (prevents reasoning breakage)
- One active provider at a time
- **AI service has NO direct DB access** — all DB interactions go through Go backend APIs
- **No shared Redis** — Go backend and AI service have fully independent infrastructure
- **No plaintext provider info in payload** — Go backend sends an AES-256-GCM encrypted token; AI service decrypts it
- **No Key Vault access at runtime** — Required credentials (API keys, endpoints) are fetched from Azure Key Vault by the deployment infrastructure and injected into the AI Service's environment variables.
- Provider switching takes effect **immediately** on next request
- `.env` fallback preserved for local development

---

## System Overview

```
Admin UI (Next.js)
      │ GET /admin/llm-providers
      │ PUT /admin/llm-providers/{name}/activate
      ▼
Go Backend  ──── DB: llm_provider_configs (owns all reads/writes)
      │      └── own cache (active provider identity, invalidated on change)
      │      └── LLM_PAYLOAD_ENCRYPTION_KEY (env, own secrets manager)
      │
      │  encrypt({provider_name, model_name}) → llm_token
      │
      │  POST /invoke  { query, session_id, llm_token: "eyABCxyz..." }
      ▼
Python AI Service
      ├── LLM_PAYLOAD_ENCRYPTION_KEY (env)
      ├── LLM Credentials (env variables populated by infra from Key Vault)
      ├── crypto.py: AES-256-GCM decrypt llm_token → {provider_name, model_name}
      └── gateway.py: get_gateway_for_token(llm_token) → builds provider instance
```

**Key principle:** The payload carries only an encrypted identity token — no provider names in plaintext. AI service independently resolves the active provider using the decrypted identity and instantiates it using its environment variables.

---

## Layer 1 — Database (Go Backend Owned)

**New table: `llm_provider_configs`** — managed entirely by Go backend. Migration and seeding live in Go backend's tooling.

```
Column                 | Type        | Notes
-----------------------|-------------|--------------------------------------------------
id                     | UUID PK     |
provider_name          | TEXT UNIQUE | 'openai' | 'azure_openai' | 'vertex_ai'
model_name             | TEXT        | Fixed at seed time, never user-editable
is_active              | BOOL        | True = currently serving (max 1 at a time)
display_config         | JSONB       | Non-sensitive display info for UI (optional)
updated_at             | TIMESTAMPTZ |
```

**DB constraint:** partial unique index `WHERE is_active = TRUE` — enforced at DB level.

**Seed data (Go migration):**
```
provider_name  | model_name          | is_active
openai         | gpt-5.4-mini        | false
azure_openai   | gpt-5.4-mini        | true
vertex_ai      | gemini-2.5-pro      | false
```

---

## Layer 2 — Go Backend Routes + Encryption

All routes require admin role.

- `GET /admin/llm-providers` — returns all rows with `is_active` and `display_config`.
- `PUT /admin/llm-providers/{name}/activate` — sets `is_active=true` for this row, `false` for all others. **Invalidates Go cache immediately.**

**Go backend's own active-provider cache** (own Redis or `sync.Map`):
```
Key:   "llm:active_provider"
Value: { provider_name, model_name }
Invalidated: on PUT activate
```

**Encrypting the token for each invocation:**
```go
// Go backend — on every invocation to AI service
plaintext := json.Marshal(map[string]string{
    "provider_name": activeConfig.ProviderName,  // e.g. "azure_openai"
    "model_name":    activeConfig.ModelName,     // e.g. "gpt-4.5-mini"
})
llmToken := aesGCMEncrypt(plaintext, LLM_PAYLOAD_ENCRYPTION_KEY)

// POST to AI service
{ "query": "...", "session_id": "...", "llm_token": llmToken }
```

Go backend caches the same ciphertext until the active provider changes — no re-encryption on every request.

---

## Layer 3 — Python AI Service Changes

AI service has no DB access and no Key Vault access. It decrypts the token, looks up the requested provider, and initializes it using credentials from its environment variables.

### 3a. New env var

```
LLM_PAYLOAD_ENCRYPTION_KEY=<base64_32_bytes>   # same value as Go backend, set independently
```

Added to `app/core/config.py` as `llm_payload_encryption_key: str | None`.

Additionally, the AI Service environment will contain the necessary credentials (e.g., `AZURE_OPENAI_API_KEY`, `OPENAI_API_KEY`) as injected by the infrastructure.

### 3b. New file: `app/agents/llm/crypto.py`

```python
import base64, json
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

def decrypt_llm_token(token: str, key_b64: str) -> dict:
    """AES-256-GCM decrypt llm_token → {provider_name, model_name}."""
    raw = base64.b64decode(token)
    nonce, ciphertext = raw[:12], raw[12:]
    key = base64.b64decode(key_b64)
    plaintext = AESGCM(key).decrypt(nonce, ciphertext, None)
    return json.loads(plaintext)
```

Local dev fallback: if `LLM_PAYLOAD_ENCRYPTION_KEY` is unset, skip decryption and fall back to `settings.llm_provider` from env.

### 3c. Refactor: `app/agents/llm/gateway.py`

Add `get_gateway_for_token()` alongside the existing `get_default_gateway()`:

```python
_gateway_cache: dict[str, LLMProviderProtocol] = {}  # keyed on provider_name

async def get_gateway_for_token(llm_token: str) -> LLMProviderProtocol:
    config = decrypt_llm_token(llm_token, settings.llm_payload_encryption_key)
    provider_name = config["provider_name"]
    model_name = config["model_name"]

    if provider_name not in _gateway_cache:
        instance = LLMProviderRegistry.get_provider_by_name(
            provider_name, model_name
        )
        _gateway_cache[provider_name] = instance
    return _gateway_cache[provider_name]
```

`get_default_gateway()` stays untouched — local dev and tests use it via env vars.

### 3d. Update chat/invoke endpoint

```python
class InvokeRequest(BaseModel):
    query: str
    session_id: str
    llm_token: str | None = None  # None → local dev fallback to env-based gateway
```

```python
gateway = await get_gateway_for_token(request.llm_token) if request.llm_token \
          else get_default_gateway()
```

### 3e. Registry: `get_provider_by_name()`

```python
@classmethod
def get_provider_by_name(
    cls, name: str, model: str
) -> LLMProviderProtocol:
    """Build a provider instance using environment settings for credentials."""
    provider_cls = _PROVIDER_CLASS_MAP[name.lower()]
    # Providers will pull their API keys/endpoints from `settings`
    return provider_cls.from_settings(model=model)
```

Note: Providers' `from_settings()` methods can be extended to accept an overriding `model` parameter if they don't already, while continuing to pull credentials from `settings`.

---

## Layer 4 — Admin UI (Next.js)

Single admin page at `/admin/llm-providers`:

```
Provider Management
──────────────────────────────────────────────────────────
Provider       Model              Status          Actions
──────────────────────────────────────────────────────────
OpenAI         gpt-5.4-mini      ● ACTIVE        [ACTIVE]
Azure OpenAI   gpt-5.4-mini      Inactive        [Set Active]
Vertex AI      gemini-2.5-pro    Inactive        [Set Active]
──────────────────────────────────────────────────────────
```

**UI rules:**
- Only one provider can be active at a time.
- Clicking "Set Active" immediately updates the active provider in the Go Backend.
- Model names are read-only labels — no input field ever.
- No credential configuration fields exist since credentials are managed by infrastructure.

---

## What Never Appears in the Payload

| Information | Where it lives | In payload? |
|---|---|---|
| Provider name | Encrypted in `llm_token` | Never plaintext |
| Model name | Encrypted in `llm_token` | Never plaintext |
| API keys / credentials | AI Service Environment | Never |
| Encryption key | Each service's own env | Never |

---

## Cache Flow Summary

| Event | Who acts | Immediate effect |
|---|---|---|
| Provider activated | Go Backend | DB `is_active` flipped; **Go cache invalidated instantly**; new ciphertext generated |
| Next chat invocation | Go Backend | Reads fresh identity from own cache; encrypts; sends `llm_token` |
| AI service receives token | AI Service | Decrypts → provider_name; checks cache by provider_name; initializes using env vars if missing |

**Result:** Provider switch = instant on next request. Zero shared infrastructure between services.

---

## Backward Compatibility

- `from_settings()` on all providers stays untouched, possibly extended to accept a `model` parameter.
- `get_default_gateway()` stays untouched
- If `llm_token` absent from payload (local dev, tests) → falls back to `settings.llm_provider` from `.env`
- No `.env` changes needed for local dev

---

## Files Touched

**Go Backend (separate repo — Go team):**
- New migration: `create_llm_provider_configs` + seed 3 rows
- New handler: `admin_llm_providers.go` (2 routes: list, activate)
- New DB query layer for `llm_provider_configs`
- Own cache (`sync.Map` or Redis) for active provider identity
- AES-256-GCM encrypt helper using `LLM_PAYLOAD_ENCRYPTION_KEY`
- Invoke/chat handler: append `llm_token` to AI service request

**Python AI Service (this repo):**
- [`backend/app/core/config.py`](service-request-chatbot/backend/app/core/config.py) — add `llm_payload_encryption_key` field
- [`backend/app/agents/llm/gateway.py`](service-request-chatbot/backend/app/agents/llm/gateway.py) — add `get_gateway_for_token(llm_token)`
- [`backend/app/agents/llm/registry.py`](service-request-chatbot/backend/app/agents/llm/registry.py) — add `get_provider_by_name()`
- `backend/app/agents/llm/crypto.py` — **new file**: AES-256-GCM decrypt
- `backend/app/api/routes/` — update chat/invoke endpoint request schema
- [`backend/pyproject.toml`](service-request-chatbot/backend/pyproject.toml) — add `cryptography`

**Frontend (this repo):**
- `frontend/src/app/admin/llm-providers/page.tsx` — **new file**: admin page
