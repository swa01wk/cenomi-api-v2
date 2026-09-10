"""Google Cloud Vertex AI embedding provider.

Uses the ``google-genai`` SDK in Vertex AI mode (``vertexai=True``), mirroring
the pattern established by ``VertexAIGateway`` in the LLM layer.

Selected when ``EMBEDDING_PROVIDER=vertex_ai``.  Requires:
- ``VERTEX_AI_PROJECT``  (or a Workload Identity / ADC environment)
- ``VERTEX_AI_LOCATION`` (default ``us-central1``)
- ``VERTEX_AI_CREDENTIALS_PATH`` (optional — path to GCP service account JSON)
- ``VERTEX_AI_EMBEDDING_MODEL`` (default ``text-embedding-005``)
"""

from __future__ import annotations

import os

import structlog

log = structlog.get_logger(__name__)

try:
    from google import genai as _genai
except ImportError:
    _genai = None  # type: ignore[assignment]


class VertexAIEmbeddingProvider:
    """Embed text using Google Cloud Vertex AI text embedding models."""

    def __init__(
        self,
        *,
        model: str = "text-embedding-005",
        project: str | None = None,
        location: str = "us-central1",
        credentials_path: str | None = None,
    ) -> None:
        if _genai is None:
            raise RuntimeError(
                "Google Cloud Vertex AI SDK is not installed. "
                "Run 'pip install google-genai' to use EMBEDDING_PROVIDER=vertex_ai."
            )
        if not project:
            raise RuntimeError(
                "VERTEX_AI_PROJECT is required when EMBEDDING_PROVIDER=vertex_ai."
            )

        self._model = model

        if credentials_path:
            os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = credentials_path

        self._client = _genai.Client(
            vertexai=True,
            project=project,
            location=location,
        )

    @classmethod
    def from_settings(cls) -> VertexAIEmbeddingProvider:
        """Instantiate using application ``Settings``."""
        from app.core.config import settings

        return cls(
            model=settings.vertex_ai_embedding_model,
            project=settings.vertex_ai_project,
            location=settings.vertex_ai_location,
            credentials_path=settings.vertex_ai_credentials_path,
        )

    async def embed(self, text: str) -> list[float] | None:
        """Return the embedding vector for *text*, or ``None`` on error."""
        batch = await self.embed_batch([text])
        return batch[0] if batch else None

    async def embed_batch(self, texts: list[str]) -> list[list[float]] | None:
        """Return one vector per text, preserving order; ``None`` on any error.

        Uses the provider's native list input — ``embed_content`` accepts a
        list of contents and returns one embedding per item in order.
        """
        if not texts:
            return []
        try:
            response = await self._client.aio.models.embed_content(
                model=self._model,
                contents=texts,
            )
            embeddings = [list(e.values) for e in response.embeddings]
            if len(embeddings) != len(texts):
                log.warning(
                    "vertex_ai_embed.length_mismatch",
                    model=self._model,
                    expected=len(texts),
                    got=len(embeddings),
                )
                return None
            return embeddings
        except Exception as exc:
            log.warning(
                "vertex_ai_embed.error",
                model=self._model,
                batch_size=len(texts),
                error=str(exc),
            )
            return None
