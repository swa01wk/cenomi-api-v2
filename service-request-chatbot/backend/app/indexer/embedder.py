"""Batched embedding helper for the indexer.

Wraps the app's ``EmbeddingProvider`` (same model/provider as the faq_node
read path — a model mismatch silently destroys retrieval) with:
- batching (``INDEXER_EMBED_BATCH_SIZE``, default 50)
- bounded concurrency (``INDEXER_MAX_CONCURRENCY``, default 4)
- order preservation
- token accounting via tiktoken for cost logs
"""

from __future__ import annotations

import asyncio
import time

import structlog

from app.core.config import settings
from app.indexer.transform.chunker import count_tokens
from app.integrations.embeddings.base import EmbeddingProvider

log = structlog.get_logger(__name__)


class EmbeddingError(RuntimeError):
    """A batch failed or returned the wrong number of vectors."""


async def embed_texts(
    provider: EmbeddingProvider,
    texts: list[str],
    *,
    source: str = "",
    batch_size: int | None = None,
    max_concurrency: int | None = None,
) -> list[list[float]]:
    """Embed *texts* preserving order; raise ``EmbeddingError`` on any failure.

    Failure is all-or-nothing per batch (the provider contract returns ``None``
    rather than partial results), and any failed batch fails the whole call so
    the caller never upserts documents with missing vectors.
    """
    if not texts:
        return []

    batch_size = batch_size or settings.indexer_embed_batch_size
    max_concurrency = max_concurrency or settings.indexer_max_concurrency

    batches: list[list[str]] = [
        texts[i : i + batch_size] for i in range(0, len(texts), batch_size)
    ]
    semaphore = asyncio.Semaphore(max_concurrency)
    results: list[list[list[float]] | None] = [None] * len(batches)

    async def _run(index: int, batch: list[str]) -> None:
        async with semaphore:
            t0 = time.monotonic()
            vectors = await provider.embed_batch(batch)
            elapsed_ms = int((time.monotonic() - t0) * 1000)
            log.info(
                "indexer.embed_batch",
                source=source,
                batch_index=index,
                batch_size=len(batch),
                ms=elapsed_ms,
                ok=vectors is not None,
            )
            results[index] = vectors

    await asyncio.gather(*(_run(i, b) for i, b in enumerate(batches)))

    vectors_out: list[list[float]] = []
    for index, (batch, vectors) in enumerate(zip(batches, results)):
        if vectors is None:
            raise EmbeddingError(
                f"embed_batch failed for source={source!r} batch={index} "
                f"({len(batch)} texts)"
            )
        if len(vectors) != len(batch):
            raise EmbeddingError(
                f"embed_batch length mismatch for source={source!r} batch={index}: "
                f"expected {len(batch)}, got {len(vectors)}"
            )
        vectors_out.extend(vectors)

    total_tokens = sum(count_tokens(t) for t in texts)
    log.info(
        "indexer.embed_done",
        source=source,
        texts=len(texts),
        tokens=total_tokens,
        calls=len(batches),
    )
    return vectors_out
