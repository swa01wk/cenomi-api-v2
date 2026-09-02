"""embed_batch contract on providers (stub clients) and the embed_texts helper."""

from __future__ import annotations

import pytest

from app.indexer.embedder import EmbeddingError, embed_texts
from tests.unit.indexer.conftest import StubEmbeddingProvider


class _FakeDatum:
    def __init__(self, index: int, embedding: list[float]) -> None:
        self.index = index
        self.embedding = embedding


class _FakeEmbeddingsAPI:
    def __init__(self, fail: bool = False, shuffle: bool = False) -> None:
        self.fail = fail
        self.shuffle = shuffle
        self.calls: list[list[str]] = []

    async def create(self, *, input: list[str] | str, model: str):
        texts = [input] if isinstance(input, str) else input
        self.calls.append(list(texts))
        if self.fail:
            raise RuntimeError("boom")
        data = [_FakeDatum(i, [float(i), float(len(t))]) for i, t in enumerate(texts)]
        if self.shuffle:
            data = list(reversed(data))

        class _Resp:
            pass

        resp = _Resp()
        resp.data = data
        return resp


def _provider(cls, fake):
    provider = cls.__new__(cls)  # skip __init__ (no real client/creds)
    provider._model = "text-embedding-3-small"

    class _Client:
        embeddings = fake

    provider._client = _Client()
    return provider


class TestOpenAIProvider:
    @pytest.mark.asyncio
    async def test_batch_order_preserved_even_if_api_shuffles(self) -> None:
        from app.integrations.embeddings.openai_embed import OpenAIEmbeddingProvider
        provider = _provider(OpenAIEmbeddingProvider, _FakeEmbeddingsAPI(shuffle=True))
        result = await provider.embed_batch(["a", "bb", "ccc"])
        assert [v[0] for v in result] == [0.0, 1.0, 2.0]  # input order

    @pytest.mark.asyncio
    async def test_empty_list(self) -> None:
        from app.integrations.embeddings.openai_embed import OpenAIEmbeddingProvider
        provider = _provider(OpenAIEmbeddingProvider, _FakeEmbeddingsAPI())
        assert await provider.embed_batch([]) == []

    @pytest.mark.asyncio
    async def test_failure_returns_none_not_partial(self) -> None:
        from app.integrations.embeddings.openai_embed import OpenAIEmbeddingProvider
        provider = _provider(OpenAIEmbeddingProvider, _FakeEmbeddingsAPI(fail=True))
        assert await provider.embed_batch(["a", "b"]) is None

    @pytest.mark.asyncio
    async def test_embed_delegates_to_batch(self) -> None:
        from app.integrations.embeddings.openai_embed import OpenAIEmbeddingProvider
        fake = _FakeEmbeddingsAPI()
        provider = _provider(OpenAIEmbeddingProvider, fake)
        vector = await provider.embed("hello")
        assert vector == [0.0, 5.0]
        assert fake.calls == [["hello"]]


class TestAzureProvider:
    @pytest.mark.asyncio
    async def test_batch_and_empty(self) -> None:
        from app.integrations.embeddings.azure_openai_embed import (
            AzureOpenAIEmbeddingProvider,
        )
        provider = _provider(AzureOpenAIEmbeddingProvider, _FakeEmbeddingsAPI())
        result = await provider.embed_batch(["x", "yy"])
        assert len(result) == 2
        assert await provider.embed_batch([]) == []

    @pytest.mark.asyncio
    async def test_failure_none(self) -> None:
        from app.integrations.embeddings.azure_openai_embed import (
            AzureOpenAIEmbeddingProvider,
        )
        provider = _provider(AzureOpenAIEmbeddingProvider, _FakeEmbeddingsAPI(fail=True))
        assert await provider.embed_batch(["x"]) is None


class TestEmbedTexts:
    @pytest.mark.asyncio
    async def test_batching_and_order(self) -> None:
        provider = StubEmbeddingProvider()
        texts = [f"t{i}" for i in range(120)]
        vectors = await embed_texts(provider, texts, source="test", batch_size=50)
        assert len(vectors) == 120
        assert len(provider.calls) == 3  # 50 + 50 + 20
        # First component encodes len(text) — verify order end-to-end.
        assert vectors[0][0] == 2.0 and vectors[99][0] == 3.0

    @pytest.mark.asyncio
    async def test_empty(self) -> None:
        assert await embed_texts(StubEmbeddingProvider(), [], source="t") == []

    @pytest.mark.asyncio
    async def test_failed_batch_raises(self) -> None:
        with pytest.raises(EmbeddingError):
            await embed_texts(
                StubEmbeddingProvider(fail=True), ["a"], source="t", batch_size=10
            )
