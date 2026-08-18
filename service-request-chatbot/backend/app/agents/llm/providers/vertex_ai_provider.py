"""Google Cloud Vertex AI adapter implementation for chat completions."""

from __future__ import annotations

import json
import os
import time
from typing import Any

import structlog

from app.agents.llm.base import BaseLLMGateway, LLMResult
from app.core.config import settings

log = structlog.get_logger(__name__)

try:
    from google import genai
    from google.genai import types as genai_types
except ImportError:
    genai = None
    genai_types = None


class VertexAIGateway(BaseLLMGateway):
    """Async Google Cloud Vertex AI chat completion adapter using the google-genai SDK."""

    def __init__(
        self,
        *,
        model: str,
        project: str | None = None,
        location: str = "us-central1",
        credentials_path: str | None = None,
        temperature: float = 0.0,
    ) -> None:
        super().__init__(model=model, provider_name="vertex_ai")
        if genai is None or genai_types is None:
            raise RuntimeError(
                "Google Cloud Vertex AI SDK is not installed. Please install 'google-genai' "
                "to use LLM_PROVIDER=vertex_ai."
            )

        self._temperature = temperature
        self._project = project
        self._location = location

        if credentials_path:
            os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = credentials_path

        client_kwargs: dict[str, Any] = {"vertexai": True}
        if project:
            client_kwargs["project"] = project
        if location:
            client_kwargs["location"] = location

        self._client = genai.Client(**client_kwargs)

    @classmethod
    def from_settings(cls) -> VertexAIGateway:
        """Instantiate using application ``Settings``."""
        return cls(
            model=settings.llm_model,
            project=settings.vertex_ai_project,
            location=settings.vertex_ai_location,
            credentials_path=settings.vertex_ai_credentials_path,
            temperature=0.0,
        )

    async def generate(
        self,
        messages: list[dict[str, Any]],
        *,
        response_format: dict[str, Any] | None = None,
        tools: list[dict[str, Any]] | None = None,
        stream: bool = False,
        temperature: float = 0.0,
        **kwargs: Any,
    ) -> LLMResult:
        """Execute chat completion against Google Cloud Vertex AI."""
        if genai is None or genai_types is None:
            raise RuntimeError("google-genai SDK is not installed.")

        start = time.monotonic()
        effective_temp = temperature if temperature != 0.0 else self._temperature

        # Separate system messages from conversation messages
        system_instructions: list[str] = []
        contents: list[genai_types.Content] = []

        for m in messages:
            role = m.get("role", "user")
            text = m.get("content", "")
            if role == "system":
                system_instructions.append(text)
            elif role == "assistant":
                contents.append(
                    genai_types.Content(
                        role="model",
                        parts=[genai_types.Part.from_text(text=text)],
                    )
                )
            else:
                contents.append(
                    genai_types.Content(
                        role="user",
                        parts=[genai_types.Part.from_text(text=text)],
                    )
                )

        config_kwargs: dict[str, Any] = {"temperature": effective_temp}
        if system_instructions:
            config_kwargs["system_instruction"] = "\n\n".join(system_instructions)
        if response_format and response_format.get("type") == "json_object":
            config_kwargs["response_mime_type"] = "application/json"

        config = genai_types.GenerateContentConfig(**config_kwargs)

        response = await self._client.aio.models.generate_content(
            model=self._model,
            contents=contents,
            config=config,
        )
        latency_ms = int((time.monotonic() - start) * 1000)

        raw_content = response.text or ""
        input_tokens = 0
        output_tokens = 0
        if response.usage_metadata:
            input_tokens = getattr(response.usage_metadata, "prompt_token_count", 0) or 0
            output_tokens = getattr(response.usage_metadata, "candidates_token_count", 0) or 0

        parsed: dict[str, Any] | None = None
        if response_format and response_format.get("type") == "json_object":
            try:
                parsed = json.loads(raw_content or "{}")
            except json.JSONDecodeError:
                raise

        return LLMResult(
            content=raw_content,
            parsed=parsed,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency_ms,
            raw=response,
        )
