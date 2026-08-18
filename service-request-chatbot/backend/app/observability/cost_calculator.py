"""LLM cost calculator — pure litellm delegation.

All cost accounting is delegated 100% to ``litellm.cost_per_token()``.
No manual pricing formulas, no startup registration, no global state.

This module is a **pure function** of ``(provider, model, input_tokens, output_tokens)``.
It carries no dependency on application settings or server startup state, making it
fully compatible with future UI-driven dynamic provider/model selection where the active
model can change at runtime without a server restart.

Pricing updates
---------------
Run ``pip install --upgrade litellm`` and redeploy.  No code changes, no DB updates,
no server restart required.  LiteLLM releases patch versions for pricing changes
typically within 24-48 hours of a provider announcement.

Provider name translation
-------------------------
Internal gateway code and ``LLM_PROVIDER`` env use ``"azure_openai"``.
LiteLLM expects ``"azure"``.  ``_PROVIDER_MAP`` is the **single** place this
translation lives — do not duplicate it elsewhere.
"""

from __future__ import annotations

from decimal import Decimal

import structlog

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Provider name translation
# ---------------------------------------------------------------------------
# Maps internal gateway.provider_name / LLM_PROVIDER env value
# → litellm custom_llm_provider key.
#
#   Internal          LiteLLM
#   ──────────────    ────────────
#   "openai"      →   "openai"
#   "azure_openai"→   "azure"      ← only difference
#   "vertex_ai"   →   "vertex_ai"
#
_PROVIDER_MAP: dict[str, str] = {
    "openai": "openai",
    "azure_openai": "azure",
    "vertex_ai": "vertex_ai",
}


def calculate_llm_cost(
    provider: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
) -> Decimal | None:
    """Return total USD cost for an LLM call, or ``None`` if the model is unknown.

    Delegates entirely to ``litellm.cost_per_token()`` — no manual pricing
    formulas.  Executes in < 0.05 ms from LiteLLM's bundled JSON pricing data
    with zero network overhead.

    Args:
        provider:      Internal provider name from ``gateway.provider_name``.
                       One of: ``'openai'``, ``'azure_openai'``, ``'vertex_ai'``.
                       Unknown values are passed through to LiteLLM as-is.
        model:         Model name as reported by ``gateway.model`` at call time.
                       For OpenAI/Vertex AI this is the canonical model name
                       (e.g. ``'gpt-4o-mini'``, ``'gemini-2.5-flash'``).
                       For Azure it is the deployment name or ``LLM_MODEL`` fallback.
        input_tokens:  Number of prompt/input tokens consumed.
        output_tokens: Number of completion/output tokens generated.

    Returns:
        ``Decimal`` cost in USD (e.g. ``Decimal('0.000112')``), or ``None``
        if the model/provider combination is not in LiteLLM's pricing data.

        ``None`` means **"cost could not be determined"**, not "this call was free".
        ``Decimal('0')`` is **never** returned for an unknown model.
    """
    if not model or input_tokens < 0 or output_tokens < 0:
        return None

    litellm_provider = _PROVIDER_MAP.get(provider, provider)

    try:
        # Deferred import — avoids adding litellm to the module import graph at
        # startup time.  The first call pays the import cost; subsequent calls hit
        # the already-loaded module cache.
        from litellm import cost_per_token

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

    except Exception as primary_exc:
        # ── Vertex AI fallback ────────────────────────────────────────────
        # LiteLLM's vertex_ai pricing only covers Gemini 2.0+ models.
        # Older models (gemini-1.5-flash, gemini-1.5-pro) are listed under
        # the "gemini" provider key with identical pricing.  Try that before
        # giving up so that 1.5-series models still get accurate cost tracking.
        if litellm_provider == "vertex_ai":
            try:
                from litellm import cost_per_token as _cpt

                prompt_cost, completion_cost = _cpt(
                    model=model,
                    prompt_tokens=input_tokens,
                    completion_tokens=output_tokens,
                    custom_llm_provider="gemini",
                )
                total = Decimal(str(prompt_cost + completion_cost))
                log.debug(
                    "llm_cost.calculated_via_gemini_fallback",
                    provider=provider,
                    model=model,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cost_usd=str(total),
                )
                return total
            except Exception:
                pass  # fall through to None below

        # NotFoundError for unregistered/unknown models — not a bug.
        # Cost stays NULL in DB.  Resolves automatically when:
        #   (a) pip upgrade litellm adds the model to its pricing registry, OR
        #   (b) user selects a known model via the UI in a future release.
        log.debug(
            "llm_cost.model_not_found",
            provider=provider,
            litellm_provider=litellm_provider,
            model=model,
            reason=str(primary_exc),
        )
        return None
