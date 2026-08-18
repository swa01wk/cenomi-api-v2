"""Config-driven singleton factory for embedding and search providers.

Follows the same pattern as ``LLMGateway`` / ``get_default_gateway()``:
- One function per provider type, lazily creating and caching the singleton.
- Returns ``None`` when the required credentials are absent so callers can
  degrade gracefully without raising at import time.
- ``close_integrations()`` is called from the FastAPI lifespan shutdown hook
  to release all open ``httpx.AsyncClient`` connections.
"""

from __future__ import annotations

import structlog

from app.core.config import settings
from app.integrations.embeddings.base import EmbeddingProvider
from app.integrations.search.base import SearchRepository

log = structlog.get_logger(__name__)

# Module-level singleton cache
_embedding_provider: EmbeddingProvider | None = None
_search_repository: SearchRepository | None = None


def get_embedding_provider() -> EmbeddingProvider | None:
    """Return the configured ``EmbeddingProvider`` singleton, or ``None``.

    Provider selection is driven by ``EMBEDDING_PROVIDER`` env var:
    - ``"openai"`` (default) — ``OpenAIEmbeddingProvider`` using ``OPENAI_API_KEY``
    - ``"azure_openai"`` — ``AzureOpenAIEmbeddingProvider`` using ``AZURE_AI_*`` vars
    - ``"vertex_ai"`` — ``VertexAIEmbeddingProvider`` using ``VERTEX_AI_*`` vars

    Returns ``None`` when required credentials are absent so ``faq_node``
    falls back to the static prompt without error.
    """
    global _embedding_provider
    if _embedding_provider is not None:
        return _embedding_provider

    from app.integrations.embeddings.registry import EmbeddingProviderRegistry

    provider = settings.embedding_provider.lower()
    try:
        _embedding_provider = EmbeddingProviderRegistry.get_provider(provider)
        log.info("integrations.factory.embed_created", provider=provider)
    except (ValueError, RuntimeError) as exc:
        log.debug("integrations.factory.embed_skip", provider=provider, reason=str(exc))
        return None

    return _embedding_provider


def get_search_repository() -> SearchRepository | None:
    """Return the configured ``SearchRepository`` singleton, or ``None``.

    Provider selection is driven by ``SEARCH_PROVIDER`` env var:
    - ``"azure_search"`` (default) — ``AzureSearchRepository``

    Returns ``None`` when required credentials are absent or the embedding
    provider could not be initialised.
    """
    global _search_repository
    if _search_repository is not None:
        return _search_repository

    provider = settings.search_provider.lower()

    if provider == "azure_search":
        if (
            not settings.azure_search_endpoint
            or not settings.azure_search_api_key
            or not settings.azure_search_index_name
        ):
            log.debug(
                "integrations.factory.search_skip",
                reason="AZURE_SEARCH_ENDPOINT / AZURE_SEARCH_API_KEY / AZURE_SEARCH_INDEX_NAME not set",
            )
            return None

        embedding_provider = get_embedding_provider()
        if embedding_provider is None:
            log.debug(
                "integrations.factory.search_skip",
                reason="embedding provider unavailable",
            )
            return None

        from app.integrations.search.azure_search import AzureSearchRepository
        _search_repository = AzureSearchRepository(
            endpoint=settings.azure_search_endpoint,
            api_key=settings.azure_search_api_key,
            index_name=settings.azure_search_index_name,
            embedding_provider=embedding_provider,
        )
        log.info(
            "integrations.factory.search_created",
            provider="azure_search",
            index_name=settings.azure_search_index_name,
        )

    else:
        log.warning("integrations.factory.search_unknown_provider", provider=provider)

    return _search_repository


async def close_integrations() -> None:
    """Close all open HTTP clients.  Called from the FastAPI lifespan shutdown."""
    global _search_repository, _embedding_provider

    if _search_repository is not None:
        try:
            await _search_repository.close()
        except Exception as exc:
            log.warning("integrations.factory.close_error", error=str(exc))
        _search_repository = None

    # Embedding providers manage their own SDK clients — no explicit close needed.
    _embedding_provider = None
    log.info("integrations.factory.closed")
