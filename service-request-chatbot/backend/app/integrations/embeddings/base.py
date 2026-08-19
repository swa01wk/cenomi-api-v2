"""Abstract embedding provider interface."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Protocol for all embedding backends.

    Implementations must be async and return ``None`` on failure so callers
    can treat a ``None`` result as a fallback signal without catching exceptions.
    """

    async def embed(self, text: str) -> list[float] | None:
        """Embed *text* and return the vector, or ``None`` on error."""
        ...

    async def embed_batch(self, texts: list[str]) -> list[list[float]] | None:
        """Embed *texts* and return vectors preserving input order.

        Contract (indexer ticket 3547):
        - Empty input list → ``[]``.
        - Any failure → ``None`` for the whole batch — never a partial result,
          so callers can retry the batch without diffing which texts succeeded.
        """
        ...
