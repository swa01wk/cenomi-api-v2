"""Provider registry for multi-provider embedding adapters.

Maps provider names (e.g. 'openai', 'azure_openai', 'vertex_ai') to factory
functions and manages cached singletons.  Mirrors ``LLMProviderRegistry``
exactly so the two layers stay structurally in sync.
"""

from __future__ import annotations

from typing import Callable

import structlog

from app.integrations.embeddings.base import EmbeddingProvider

log = structlog.get_logger(__name__)


class EmbeddingProviderRegistry:
    """Registry managing embedding adapter factories and cached singletons."""

    _factories: dict[str, Callable[[], EmbeddingProvider]] = {}
    _instances: dict[str, EmbeddingProvider] = {}

    @classmethod
    def register_provider(
        cls,
        name: str,
        factory: Callable[[], EmbeddingProvider],
    ) -> None:
        """Register a factory function for provider *name*."""
        normalized = name.lower().strip()
        cls._factories[normalized] = factory
        cls._instances.pop(normalized, None)
        log.debug("embedding_registry.registered", provider=normalized)

    @classmethod
    def get_provider(cls, name: str) -> EmbeddingProvider:
        """Return the singleton instance for provider *name*, instantiating on first call."""
        normalized = name.lower().strip()

        if normalized in cls._instances:
            return cls._instances[normalized]

        if normalized not in cls._factories:
            available = sorted(cls._factories.keys())
            raise ValueError(
                f"Unknown embedding provider '{name}'. Available providers: {available}"
            )

        factory = cls._factories[normalized]
        instance = factory()
        cls._instances[normalized] = instance
        log.info("embedding_registry.created_singleton", provider=normalized)
        return instance

    @classmethod
    def list_providers(cls) -> list[str]:
        """Return a sorted list of registered provider names."""
        return sorted(cls._factories.keys())

    @classmethod
    def clear_cache(cls) -> None:
        """Clear all cached adapter singletons (useful in unit tests when settings change)."""
        cls._instances.clear()
        log.debug("embedding_registry.cache_cleared")


def _register_builtin_providers() -> None:
    """Register the built-in providers (OpenAI, Azure OpenAI, Google Vertex AI)."""

    def _create_openai() -> EmbeddingProvider:
        from app.integrations.embeddings.openai_embed import OpenAIEmbeddingProvider
        return OpenAIEmbeddingProvider.from_settings()

    def _create_azure_openai() -> EmbeddingProvider:
        from app.integrations.embeddings.azure_openai_embed import AzureOpenAIEmbeddingProvider
        return AzureOpenAIEmbeddingProvider.from_settings()

    def _create_vertex_ai() -> EmbeddingProvider:
        from app.integrations.embeddings.vertex_ai_embed import VertexAIEmbeddingProvider
        return VertexAIEmbeddingProvider.from_settings()

    EmbeddingProviderRegistry.register_provider("openai", _create_openai)
    EmbeddingProviderRegistry.register_provider("azure_openai", _create_azure_openai)
    EmbeddingProviderRegistry.register_provider("vertex_ai", _create_vertex_ai)


# Ensure built-in providers are registered when the module is imported
_register_builtin_providers()
