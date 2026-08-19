"""Shared fixtures for indexer unit tests — no network, no DB."""

from __future__ import annotations

import pytest


class StubEmbeddingProvider:
    """Deterministic embed_batch: short vectors, order-preserving."""

    def __init__(self, dims: int = 4, fail: bool = False) -> None:
        self.dims = dims
        self.fail = fail
        self.calls: list[list[str]] = []

    async def embed(self, text: str) -> list[float] | None:
        batch = await self.embed_batch([text])
        return batch[0] if batch else None

    async def embed_batch(self, texts: list[str]) -> list[list[float]] | None:
        self.calls.append(list(texts))
        if self.fail:
            return None
        if not texts:
            return []
        # Encode the batch-relative index into the vector so order is testable.
        return [[float(len(t)), float(i)] + [0.0] * (self.dims - 2)
                for i, t in enumerate(texts)]


@pytest.fixture
def stub_provider() -> StubEmbeddingProvider:
    return StubEmbeddingProvider()
