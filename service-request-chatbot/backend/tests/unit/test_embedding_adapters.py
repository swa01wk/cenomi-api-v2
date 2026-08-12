"""Unit tests for multi-provider embedding adapters and registry."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.integrations.embeddings.azure_openai_embed import AzureOpenAIEmbeddingProvider
from app.integrations.embeddings.openai_embed import OpenAIEmbeddingProvider
from app.integrations.embeddings.registry import EmbeddingProviderRegistry


# ---------------------------------------------------------------------------
# OpenAI embedding provider
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_openai_embed_success():
    """OpenAIEmbeddingProvider.embed() returns a vector on success."""
    mock_data = MagicMock()
    mock_data.embedding = [0.1, 0.2, 0.3]
    mock_response = MagicMock()
    mock_response.data = [mock_data]

    with patch("app.integrations.embeddings.openai_embed.AsyncOpenAI") as mock_cls:
        mock_client = MagicMock()
        mock_client.embeddings.create = AsyncMock(return_value=mock_response)
        mock_cls.return_value = mock_client

        provider = OpenAIEmbeddingProvider(api_key="test-key", model="text-embedding-3-small")
        result = await provider.embed("hello world")

    assert result == [0.1, 0.2, 0.3]


@pytest.mark.asyncio
async def test_openai_embed_api_error():
    """OpenAIEmbeddingProvider.embed() returns None on APIError."""
    from openai import APIError
    import httpx

    with patch("app.integrations.embeddings.openai_embed.AsyncOpenAI") as mock_cls:
        mock_client = MagicMock()
        mock_client.embeddings.create = AsyncMock(
            side_effect=APIError("quota exceeded", request=httpx.Request("POST", "https://api.openai.com"), body=None)
        )
        mock_cls.return_value = mock_client

        provider = OpenAIEmbeddingProvider(api_key="test-key")
        result = await provider.embed("hello")

    assert result is None


@pytest.mark.asyncio
async def test_openai_embed_unexpected_error():
    """OpenAIEmbeddingProvider.embed() returns None on unexpected exceptions."""
    with patch("app.integrations.embeddings.openai_embed.AsyncOpenAI") as mock_cls:
        mock_client = MagicMock()
        mock_client.embeddings.create = AsyncMock(side_effect=RuntimeError("network down"))
        mock_cls.return_value = mock_client

        provider = OpenAIEmbeddingProvider(api_key="test-key")
        result = await provider.embed("hello")

    assert result is None


# ---------------------------------------------------------------------------
# Azure OpenAI embedding provider
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_azure_openai_embed_success():
    """AzureOpenAIEmbeddingProvider.embed() returns a vector on success."""
    mock_data = MagicMock()
    mock_data.embedding = [0.4, 0.5, 0.6]
    mock_response = MagicMock()
    mock_response.data = [mock_data]

    with patch("app.integrations.embeddings.azure_openai_embed.AsyncAzureOpenAI") as mock_cls:
        mock_client = MagicMock()
        mock_client.embeddings.create = AsyncMock(return_value=mock_response)
        mock_cls.return_value = mock_client

        provider = AzureOpenAIEmbeddingProvider(
            azure_endpoint="https://test.openai.azure.com",
            api_key="test-azure-key",
            model="text-embedding-3-small",
        )
        result = await provider.embed("azure test")

    assert result == [0.4, 0.5, 0.6]


@pytest.mark.asyncio
async def test_azure_openai_embed_api_error():
    """AzureOpenAIEmbeddingProvider.embed() returns None on APIError."""
    from openai import APIError
    import httpx

    with patch("app.integrations.embeddings.azure_openai_embed.AsyncAzureOpenAI") as mock_cls:
        mock_client = MagicMock()
        mock_client.embeddings.create = AsyncMock(
            side_effect=APIError("auth error", request=httpx.Request("POST", "https://test.openai.azure.com"), body=None)
        )
        mock_cls.return_value = mock_client

        provider = AzureOpenAIEmbeddingProvider(
            azure_endpoint="https://test.openai.azure.com",
            api_key="test-key",
        )
        result = await provider.embed("test")

    assert result is None


# ---------------------------------------------------------------------------
# Vertex AI embedding provider
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_vertex_ai_embed_success():
    """VertexAIEmbeddingProvider.embed() returns a vector on success."""
    from app.integrations.embeddings.vertex_ai_embed import VertexAIEmbeddingProvider, _genai

    if _genai is None:
        pytest.skip("google-genai SDK not installed")

    mock_embedding = MagicMock()
    mock_embedding.values = [0.7, 0.8, 0.9]
    mock_response = MagicMock()
    mock_response.embeddings = [mock_embedding]

    with patch("app.integrations.embeddings.vertex_ai_embed._genai") as mock_genai:
        mock_client = MagicMock()
        mock_client.aio.models.embed_content = AsyncMock(return_value=mock_response)
        mock_genai.Client.return_value = mock_client

        provider = VertexAIEmbeddingProvider(
            model="text-embedding-005",
            project="test-project",
            location="us-central1",
        )
        result = await provider.embed("vertex test")

    assert result == [0.7, 0.8, 0.9]


@pytest.mark.asyncio
async def test_vertex_ai_embed_error():
    """VertexAIEmbeddingProvider.embed() returns None on SDK exception."""
    from app.integrations.embeddings.vertex_ai_embed import VertexAIEmbeddingProvider, _genai

    if _genai is None:
        pytest.skip("google-genai SDK not installed")

    with patch("app.integrations.embeddings.vertex_ai_embed._genai") as mock_genai:
        mock_client = MagicMock()
        mock_client.aio.models.embed_content = AsyncMock(
            side_effect=Exception("Vertex AI error")
        )
        mock_genai.Client.return_value = mock_client

        provider = VertexAIEmbeddingProvider(
            model="text-embedding-005",
            project="test-project",
        )
        result = await provider.embed("fail")

    assert result is None


def test_vertex_ai_embed_missing_sdk():
    """VertexAIEmbeddingProvider raises RuntimeError when google-genai is absent."""
    with patch("app.integrations.embeddings.vertex_ai_embed._genai", None):
        from app.integrations.embeddings.vertex_ai_embed import VertexAIEmbeddingProvider

        with pytest.raises(RuntimeError, match="google-genai"):
            VertexAIEmbeddingProvider(model="text-embedding-005", project="proj")


def test_vertex_ai_embed_missing_project():
    """VertexAIEmbeddingProvider raises RuntimeError when VERTEX_AI_PROJECT is absent."""
    from app.integrations.embeddings.vertex_ai_embed import VertexAIEmbeddingProvider, _genai

    if _genai is None:
        pytest.skip("google-genai SDK not installed")

    with patch("app.integrations.embeddings.vertex_ai_embed._genai") as mock_genai:
        mock_genai.Client.return_value = MagicMock()
        with pytest.raises(RuntimeError, match="VERTEX_AI_PROJECT"):
            VertexAIEmbeddingProvider(model="text-embedding-005", project=None)


# ---------------------------------------------------------------------------
# EmbeddingProviderRegistry
# ---------------------------------------------------------------------------


def test_registry_register_and_get():
    """Verify provider registration and singleton caching."""
    EmbeddingProviderRegistry.clear_cache()
    dummy = OpenAIEmbeddingProvider(api_key="x")
    EmbeddingProviderRegistry.register_provider("test_openai", lambda: dummy)

    assert "test_openai" in EmbeddingProviderRegistry.list_providers()
    instance1 = EmbeddingProviderRegistry.get_provider("test_openai")
    instance2 = EmbeddingProviderRegistry.get_provider("test_openai")
    assert instance1 is instance2
    assert instance1 is dummy


def test_registry_unknown_provider():
    """Verify looking up an unknown provider raises a helpful ValueError."""
    with pytest.raises(ValueError, match="Unknown embedding provider 'no_such_provider'"):
        EmbeddingProviderRegistry.get_provider("no_such_provider")


def test_registry_clear_cache():
    """clear_cache() removes all cached instances but keeps factories."""
    EmbeddingProviderRegistry.register_provider("cache_test", lambda: OpenAIEmbeddingProvider(api_key="y"))
    EmbeddingProviderRegistry.get_provider("cache_test")
    EmbeddingProviderRegistry.clear_cache()
    # Re-fetching should call the factory again (new instance)
    instance = EmbeddingProviderRegistry.get_provider("cache_test")
    assert isinstance(instance, OpenAIEmbeddingProvider)


def test_registry_re_registration_clears_cached_instance():
    """Re-registering a provider evicts its cached singleton."""
    call_count = 0

    def factory():
        nonlocal call_count
        call_count += 1
        return OpenAIEmbeddingProvider(api_key="z")

    EmbeddingProviderRegistry.register_provider("reregister_test", factory)
    EmbeddingProviderRegistry.get_provider("reregister_test")
    assert call_count == 1

    EmbeddingProviderRegistry.register_provider("reregister_test", factory)
    EmbeddingProviderRegistry.get_provider("reregister_test")
    assert call_count == 2


# ---------------------------------------------------------------------------
# Factory integration
# ---------------------------------------------------------------------------


def test_factory_returns_openai_provider(monkeypatch):
    """get_embedding_provider() returns OpenAIEmbeddingProvider when provider=openai."""
    import app.integrations.factory as factory_mod

    factory_mod._embedding_provider = None
    EmbeddingProviderRegistry.clear_cache()

    monkeypatch.setattr("app.core.config.settings.embedding_provider", "openai")
    monkeypatch.setattr("app.core.config.settings.openai_api_key", "sk-test")
    monkeypatch.setattr("app.core.config.settings.embedding_model", "text-embedding-3-small")
    monkeypatch.setattr("app.core.config.settings.embedding_base_url", None)

    provider = factory_mod.get_embedding_provider()
    assert isinstance(provider, OpenAIEmbeddingProvider)
    factory_mod._embedding_provider = None


def test_factory_returns_azure_provider(monkeypatch):
    """get_embedding_provider() returns AzureOpenAIEmbeddingProvider when provider=azure_openai."""
    import app.integrations.factory as factory_mod

    factory_mod._embedding_provider = None
    EmbeddingProviderRegistry.clear_cache()

    monkeypatch.setattr("app.core.config.settings.embedding_provider", "azure_openai")
    monkeypatch.setattr("app.core.config.settings.azure_ai_endpoint", "https://test.azure.com")
    monkeypatch.setattr("app.core.config.settings.azure_ai_api_key", "key")
    monkeypatch.setattr("app.core.config.settings.embedding_model", "text-embedding-3-small")
    monkeypatch.setattr("app.core.config.settings.azure_ai_api_version", "2024-02-15-preview")

    provider = factory_mod.get_embedding_provider()
    assert isinstance(provider, AzureOpenAIEmbeddingProvider)
    factory_mod._embedding_provider = None


def test_factory_returns_none_on_missing_credentials(monkeypatch):
    """get_embedding_provider() returns None gracefully when credentials are absent."""
    import app.integrations.factory as factory_mod

    factory_mod._embedding_provider = None
    EmbeddingProviderRegistry.clear_cache()

    monkeypatch.setattr("app.core.config.settings.embedding_provider", "openai")
    monkeypatch.setattr("app.core.config.settings.openai_api_key", None)
    monkeypatch.setattr("app.core.config.settings.embedding_model", "text-embedding-3-small")
    monkeypatch.setattr("app.core.config.settings.embedding_base_url", None)

    provider = factory_mod.get_embedding_provider()
    assert provider is None
    factory_mod._embedding_provider = None


def test_factory_returns_none_on_unknown_provider(monkeypatch):
    """get_embedding_provider() returns None for an unknown EMBEDDING_PROVIDER value."""
    import app.integrations.factory as factory_mod

    factory_mod._embedding_provider = None

    monkeypatch.setattr("app.core.config.settings.embedding_provider", "unknown_llm_provider")

    provider = factory_mod.get_embedding_provider()
    assert provider is None
    factory_mod._embedding_provider = None
