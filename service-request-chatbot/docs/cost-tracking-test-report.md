# Dynamic Cost Tracking — Test Report

**Date:** 2026-08-04  
**Module:** `app/observability/cost_calculator.py`  
**Pricing Source:** LiteLLM bundled pricing (`litellm>=1.40.0`)  
**Token counts used:** Small turn = 500 input / 150 output · Large turn = 5000 input / 1000 output

---

## OpenAI

| Model | Input Tokens | Output Tokens | Cost (USD) | Status |
|---|---|---|---|---|
| `gpt-4o-mini` | 500 | 150 | $0.000165 | ✅ |
| `gpt-4o` | 500 | 150 | $0.002750 | ✅ |
| `gpt-4-turbo` | 500 | 150 | $0.009500 | ✅ |
| `gpt-3.5-turbo` | 500 | 150 | $0.000475 | ✅ |
| `gpt-4o-mini` *(large turn)* | 5000 | 1000 | $0.001350 | ✅ |

---

## Azure OpenAI

> Internal provider name `azure_openai` → translated to `azure` for LiteLLM lookup.  
> `LLM_MODEL` must be the canonical model name (e.g. `gpt-4o-mini`), not the Azure deployment name.

| Model | Input Tokens | Output Tokens | Cost (USD) | Status |
|---|---|---|---|---|
| `gpt-4o-mini` | 500 | 150 | $0.000182 | ✅ |
| `gpt-4o` | 500 | 150 | $0.002750 | ✅ |
| `gpt-4-turbo` | 500 | 150 | $0.009500 | ✅ |
| `gpt-4o-mini` *(large turn)* | 5000 | 1000 | $0.001485 | ✅ |

> **Note:** Azure prices for `gpt-4o-mini` are marginally higher than OpenAI direct ($0.000182 vs $0.000165) — this is accurate and reflects Azure's pricing tier.

---

## Vertex AI (Google Cloud)

> Gemini 2.0+ models resolve directly via `vertex_ai` provider key.  
> Gemini 1.5 models fall back to `gemini` provider key (identical pricing, older models deprecated from vertex_ai registry).

| Model | Input Tokens | Output Tokens | Cost (USD) | Resolution | Status |
|---|---|---|---|---|---|
| `gemini-2.5-flash` | 500 | 150 | $0.000525 | Direct | ✅ |
| `gemini-2.5-pro` | 500 | 150 | $0.002125 | Direct | ✅ |
| `gemini-2.0-flash` | 500 | 150 | $0.000110 | Direct | ✅ |
| `gemini-1.5-flash` | 500 | 150 | $0.0000375 | Fallback (`gemini`) | ✅ |
| `gemini-1.5-pro` | 500 | 150 | `NULL` | Not in registry | ⚠️ |

> **`gemini-1.5-pro` note:** Not present in LiteLLM's pricing registry for either `vertex_ai` or `gemini` provider in this version. Cost stored as `NULL` — not a crash, not $0. Resolves on next `pip upgrade litellm`.

---

## Edge Cases

| Scenario | Provider | Model | Cost (USD) | Status |
|---|---|---|---|---|
| Fake/unknown model | `openai` | `gpt-5-turbo-fake` | `NULL` | ✅ Correct |
| Custom Azure deployment name | `azure_openai` | `my-custom-deploy` | `NULL` | ✅ Correct |

> Both unknown models return `NULL` as expected — never `$0`. DB records them as cost-unknown, not free.  
> For the Azure custom deployment case: set `LLM_MODEL` to the canonical model name and leave `AZURE_OPENAI_LLM_DEPLOYMENT` unset, or matching the canonical name.

---

## Cost Comparison — Same Model Across Providers

| Model | OpenAI | Azure OpenAI | Delta |
|---|---|---|---|
| `gpt-4o-mini` (500/150 tokens) | $0.000165 | $0.000182 | +10% Azure premium |
| `gpt-4o` (500/150 tokens) | $0.002750 | $0.002750 | Identical |
| `gpt-4-turbo` (500/150 tokens) | $0.009500 | $0.009500 | Identical |

---

## Scale Estimate (Indicative)

> Based on `gpt-4o-mini` pricing — the default model in this project.

| Volume | Avg tokens/turn | Estimated cost |
|---|---|---|
| 100 turns/day | 650 total | ~$0.017/day |
| 1,000 turns/day | 650 total | ~$0.17/day |
| 10,000 turns/day | 650 total | ~$1.70/day |

---

## Summary

| Provider | Models Tested | All Priced | Notes |
|---|---|---|---|
| OpenAI | 4 | ✅ 4/4 | Full coverage |
| Azure OpenAI | 3 | ✅ 3/3 | Canonical name required in `LLM_MODEL` |
| Vertex AI | 5 | ✅ 4/5 | `gemini-1.5-pro` missing from LiteLLM registry |
| Unknown models | 2 | — | Correctly returns `NULL`, no crash |

**Overall: cost tracking is working correctly for all production-relevant models.**
