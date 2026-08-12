"""Standard OpenAI embedding provider.

Uses ``openai.AsyncOpenAI`` with a configurable ``base_url`` so it works for
both the public OpenAI API and any OpenAI-compatible endpoint (e.g. Azure AI
Foundry in compatibility mode).  Mirrors the ``LLMGateway`` pattern.
"""

from __future__ import annotations

import structlog
from openai import AsyncOpenAI, APIError

log = structlog.get_logger(__name__)


class OpenAIEmbeddingProvider:
    """Embed text using the standard OpenAI embeddings API."""

    @classmethod
    def from_settings(cls) -> OpenAIEmbeddingProvider:
        """Instantiate using application ``Settings``."""
        from app.core.config import settings

        if not settings.openai_api_key:
            raise RuntimeError(
                "OPENAI_API_KEY is required when EMBEDDING_PROVIDER=openai."
            )
        return cls(
            api_key=settings.openai_api_key,
            model=settings.embedding_model,
            base_url=settings.embedding_base_url,
        )

    def __init__(
        self,
        api_key: str,
        model: str = "text-embedding-3-small",
        base_url: str | None = None,
    ) -> None:
        self._model = model
        client_kwargs: dict = {"api_key": api_key}
        if base_url:
            client_kwargs["base_url"] = base_url
        self._client = AsyncOpenAI(**client_kwargs)

    async def embed(self, text: str) -> list[float] | None:
        """Return the embedding vector for *text*, or ``None`` on error."""
        try:
            response = await self._client.embeddings.create(
                input=text,
                model=self._model,
            )
            return response.data[0].embedding
        except APIError as exc:
            log.warning(
                "openai_embed.api_error",
                model=self._model,
                status_code=getattr(exc, "status_code", None),
                error=str(exc),
            )
            return None
        except Exception as exc:
            log.warning("openai_embed.unexpected_error", model=self._model, error=str(exc))
            return None
