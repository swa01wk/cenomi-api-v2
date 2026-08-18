"""OpenAI adapter implementation for general and JSON-mode chat completions."""

from __future__ import annotations

import json
import time
from typing import Any

import openai
import structlog

from app.agents.llm.base import BaseLLMGateway, LLMResult
from app.core.config import settings

log = structlog.get_logger(__name__)


class OpenAIGateway(BaseLLMGateway):
    """Async OpenAI chat completion adapter."""

    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        base_url: str | None = None,
        temperature: float = 0.0,
    ) -> None:
        super().__init__(model=model, provider_name="openai")
        self._temperature = temperature
        self._client = openai.AsyncOpenAI(
            api_key=api_key,
            base_url=base_url or None,
        )

    @classmethod
    def from_settings(cls) -> OpenAIGateway:
        """Instantiate using application ``Settings``."""
        return cls(
            model=settings.llm_model,
            api_key=settings.openai_api_key or "",
            base_url=settings.llm_base_url,
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
        """Execute chat completion against the OpenAI API."""
        start = time.monotonic()
        effective_temp = temperature if temperature != 0.0 else self._temperature

        create_kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "temperature": effective_temp,
        }
        if response_format is not None:
            create_kwargs["response_format"] = response_format
        if tools is not None:
            create_kwargs["tools"] = tools
        if stream:
            create_kwargs["stream"] = True

        create_kwargs.update(kwargs)

        response = await self._client.chat.completions.create(**create_kwargs)
        latency_ms = int((time.monotonic() - start) * 1000)

        raw_content = response.choices[0].message.content or ""
        usage = response.usage
        input_tokens = usage.prompt_tokens if usage else 0
        output_tokens = usage.completion_tokens if usage else 0

        parsed: dict[str, Any] | None = None
        if response_format and response_format.get("type") == "json_object":
            try:
                parsed = json.loads(raw_content or "{}")
            except json.JSONDecodeError:
                # Caller will handle or propagate JSONDecodeError if expected
                raise

        return LLMResult(
            content=raw_content,
            parsed=parsed,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency_ms,
            raw=response,
        )
