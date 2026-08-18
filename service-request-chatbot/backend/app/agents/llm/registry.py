"""Provider registry for multi-provider LLM adapters.

Maps provider names (e.g. 'openai', 'azure_openai', 'vertex_ai') to factory
functions and manages cached singletons.  Enables zero-touch registration of
custom or third-party providers.
"""

from __future__ import annotations

from typing import Callable

import structlog

from app.agents.llm.base import LLMProviderProtocol

log = structlog.get_logger(__name__)


class LLMProviderRegistry:
    """Registry managing LLM adapter factories and cached singletons."""

    _factories: dict[str, Callable[[], LLMProviderProtocol]] = {}
    _instances: dict[str, LLMProviderProtocol] = {}

    @classmethod
    def register_provider(
        cls,
        name: str,
        factory: Callable[[], LLMProviderProtocol],
    ) -> None:
        """Register a factory function for provider *name*."""
        normalized = name.lower().strip()
        cls._factories[normalized] = factory
        # Clear any cached instance if a provider is re-registered
        cls._instances.pop(normalized, None)
        log.debug("llm_registry.registered", provider=normalized)

    @classmethod
    def get_provider(cls, name: str) -> LLMProviderProtocol:
        """Return the singleton instance for provider *name*, instantiating on first call."""
        normalized = name.lower().strip()

        if normalized in cls._instances:
            return cls._instances[normalized]

        if normalized not in cls._factories:
            available = sorted(cls._factories.keys())
            raise ValueError(
                f"Unknown LLM provider '{name}'. Available providers: {available}"
            )

        factory = cls._factories[normalized]
        instance = factory()
        cls._instances[normalized] = instance
        log.info("llm_registry.created_singleton", provider=normalized)
        return instance

    @classmethod
    def list_providers(cls) -> list[str]:
        """Return a sorted list of registered provider names."""
        return sorted(cls._factories.keys())

    @classmethod
    def clear_cache(cls) -> None:
        """Clear all cached adapter singletons (useful in unit tests when settings change)."""
        cls._instances.clear()
        log.debug("llm_registry.cache_cleared")


def _register_builtin_providers() -> None:
    """Register the built-in providers (OpenAI, Azure OpenAI, Google Vertex AI)."""

    def _create_openai() -> LLMProviderProtocol:
        from app.agents.llm.providers.openai_provider import OpenAIGateway
        return OpenAIGateway.from_settings()

    def _create_azure_openai() -> LLMProviderProtocol:
        from app.agents.llm.providers.azure_openai_provider import AzureOpenAIGateway
        return AzureOpenAIGateway.from_settings()

    def _create_vertex_ai() -> LLMProviderProtocol:
        from app.agents.llm.providers.vertex_ai_provider import VertexAIGateway
        return VertexAIGateway.from_settings()

    LLMProviderRegistry.register_provider("openai", _create_openai)
    LLMProviderRegistry.register_provider("azure_openai", _create_azure_openai)
    LLMProviderRegistry.register_provider("vertex_ai", _create_vertex_ai)


# Ensure built-in providers are registered when the module is imported
_register_builtin_providers()
