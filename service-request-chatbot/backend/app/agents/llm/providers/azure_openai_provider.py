"""Azure OpenAI adapter implementation for general and JSON-mode chat completions."""

from __future__ import annotations

import json
import time
from typing import Any

from openai import AsyncAzureOpenAI
import structlog

from app.agents.llm.base import BaseLLMGateway, LLMResult
from app.core.config import settings

log = structlog.get_logger(__name__)


class AzureOpenAIGateway(BaseLLMGateway):
    """Async Azure OpenAI chat completion adapter."""

    def __init__(
        self,
        *,
        model: str,
        azure_endpoint: str,
        api_key: str,
        api_version: str = "2024-02-15-preview",
        temperature: float = 0.0,
        timeout: float | None = None,
        max_retries: int = 2,
    ) -> None:
        super().__init__(model=model, provider_name="azure_openai")
        self._temperature = temperature
        # The SDK treats an explicit ``timeout=None`` as "never time out", so the
        # argument is only forwarded when set.
        client_kwargs: dict[str, Any] = {"max_retries": max_retries}
        if timeout is not None:
            client_kwargs["timeout"] = timeout
        self._client = AsyncAzureOpenAI(
            azure_endpoint=azure_endpoint,
            api_key=api_key,
            api_version=api_version,
            **client_kwargs,
        )

    @classmethod
    def from_settings(cls) -> AzureOpenAIGateway:
        """Instantiate using application ``Settings``."""
        deployment = settings.azure_openai_llm_deployment or settings.llm_model
        return cls(
            model=deployment,
            azure_endpoint=settings.azure_ai_endpoint or "",
            api_key=settings.azure_ai_api_key or "",
            api_version=settings.azure_ai_api_version,
            temperature=0.0,
            timeout=settings.azure_openai_request_timeout_seconds,
            max_retries=settings.azure_openai_max_retries,
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
        """Execute chat completion against Azure OpenAI."""
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
                raise

        return LLMResult(
            content=raw_content,
            parsed=parsed,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency_ms,
            raw=response,
        )
