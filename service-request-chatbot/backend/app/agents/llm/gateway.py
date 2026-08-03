"""Multi-provider LLM gateway dispatcher and backward-compatibility layer.

Re-exports ``LLMGateway`` as a protocol-compatible reference whose
``from_settings()`` factory and ``get_default_gateway()`` helper resolve the
active LLM adapter via ``LLMProviderRegistry``.
"""

from __future__ import annotations

from typing import Any

from app.agents.llm.base import LLMProviderProtocol, LLMResult
from app.core.config import settings

# ---------------------------------------------------------------------------
# Module-level singleton (replaced in tests via set_default_gateway)
# ---------------------------------------------------------------------------

_default_gateway: LLMGateway | None = None


class LLMGateway(LLMProviderProtocol):
    """Protocol / base reference for multi-provider LLM adapters.

    Exposes ``from_settings()`` for backward compatibility with existing callers
    and tests that expect ``LLMGateway.from_settings()``.
    """

    @classmethod
    def from_settings(cls) -> LLMGateway:
        """Instantiate the active LLM provider adapter using application ``Settings``."""
        from app.agents.llm.registry import LLMProviderRegistry

        return LLMProviderRegistry.get_provider(settings.llm_provider)  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Singleton helpers
# ---------------------------------------------------------------------------


def get_default_gateway() -> LLMGateway:
    """Return the process-level ``LLMGateway`` singleton, creating it on first call."""
    global _default_gateway
    if _default_gateway is None:
        _default_gateway = LLMGateway.from_settings()
    return _default_gateway


def set_default_gateway(gateway: LLMGateway | None) -> None:
    """Override the singleton — used by tests to inject a mock gateway."""
    global _default_gateway
    _default_gateway = gateway
