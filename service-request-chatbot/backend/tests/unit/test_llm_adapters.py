"""Unit tests for multi-provider LLM adapters and registry."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.llm.base import BaseLLMGateway, LLMProviderProtocol, LLMResult
from app.agents.llm.gateway import (
    LLMGateway,
    get_default_gateway,
    set_default_gateway,
)
from app.agents.llm.providers.azure_openai_provider import AzureOpenAIGateway
from app.agents.llm.providers.openai_provider import OpenAIGateway
from app.agents.llm.providers.vertex_ai_provider import VertexAIGateway
from app.agents.llm.registry import LLMProviderRegistry
from app.core.config import settings


class DummyGateway(BaseLLMGateway):
    """Test dummy implementing BaseLLMGateway."""

    def __init__(self, response_content: str = '{"status": "ok"}') -> None:
        super().__init__(model="test-model", provider_name="dummy")
        self.response_content = response_content

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
        parsed = None
        if response_format and response_format.get("type") == "json_object":
            parsed = json.loads(self.response_content)
        return LLMResult(
            content=self.response_content,
            parsed=parsed,
            input_tokens=10,
            output_tokens=20,
            latency_ms=100,
            raw={"dummy": True},
        )


@pytest.mark.asyncio
async def test_base_gateway_complete_json_wrapper():
    """Verify BaseLLMGateway.complete_json() unpacks LLMResult accurately."""
    gateway = DummyGateway('{"intent": "HELP", "confidence": 0.9}')
    parsed, input_tokens, output_tokens, latency_ms = await gateway.complete_json(
        "system prompt", "user prompt"
    )
    assert parsed == {"intent": "HELP", "confidence": 0.9}
    assert input_tokens == 10
    assert output_tokens == 20
    assert latency_ms == 100
    assert gateway.model == "test-model"
    assert gateway.provider_name == "dummy"


def test_registry_registration_and_lookup():
    """Verify provider registration, caching, and clear_cache()."""
    LLMProviderRegistry.clear_cache()
    LLMProviderRegistry.register_provider("dummy_test", lambda: DummyGateway())

    assert "dummy_test" in LLMProviderRegistry.list_providers()
    provider1 = LLMProviderRegistry.get_provider("dummy_test")
    provider2 = LLMProviderRegistry.get_provider("dummy_test")
    assert provider1 is provider2

    LLMProviderRegistry.clear_cache()
    assert isinstance(provider1, LLMProviderProtocol)


def test_registry_unknown_provider():
    """Verify looking up an unknown provider raises a helpful ValueError."""
    with pytest.raises(ValueError, match="Unknown LLM provider 'unknown_provider'"):
        LLMProviderRegistry.get_provider("unknown_provider")


def test_get_and_set_default_gateway():
    """Verify get_default_gateway and set_default_gateway work as expected."""
    dummy = DummyGateway()
    set_default_gateway(dummy)  # type: ignore[arg-type]
    assert get_default_gateway() is dummy
    set_default_gateway(None)


@pytest.mark.asyncio
async def test_openai_gateway_generate():
    """Verify OpenAIGateway formats generate() requests correctly."""
    mock_choice = MagicMock()
    mock_choice.message.content = '{"result": "success"}'
    mock_response = MagicMock()
    mock_response.choices = [mock_choice]
    mock_response.usage.prompt_tokens = 15
    mock_response.usage.completion_tokens = 25

    with patch("openai.AsyncOpenAI") as mock_openai_cls:
        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
        mock_openai_cls.return_value = mock_client

        gateway = OpenAIGateway(model="gpt-4o-mini", api_key="test-key")
        result = await gateway.generate(
            messages=[{"role": "user", "content": "hi"}],
            response_format={"type": "json_object"},
        )
        assert result.parsed == {"result": "success"}
        assert result.input_tokens == 15
        assert result.output_tokens == 25


@pytest.mark.asyncio
async def test_azure_openai_gateway_generate():
    """Verify AzureOpenAIGateway formats generate() requests correctly."""
    mock_choice = MagicMock()
    mock_choice.message.content = '{"azure": "ok"}'
    mock_response = MagicMock()
    mock_response.choices = [mock_choice]
    mock_response.usage.prompt_tokens = 5
    mock_response.usage.completion_tokens = 10

    with patch("app.agents.llm.providers.azure_openai_provider.AsyncAzureOpenAI") as mock_cls:
        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
        mock_cls.return_value = mock_client

        gateway = AzureOpenAIGateway(
            model="my-azure-deployment",
            azure_endpoint="https://test.openai.azure.com",
            api_key="test-azure-key",
        )
        result = await gateway.generate(
            messages=[{"role": "user", "content": "hello"}],
            response_format={"type": "json_object"},
        )
        assert result.parsed == {"azure": "ok"}
        assert result.input_tokens == 5
        assert result.output_tokens == 10


@pytest.mark.asyncio
async def test_vertex_ai_gateway_generate():
    """Verify VertexAIGateway formats generate() requests correctly."""
    from app.agents.llm.providers.vertex_ai_provider import VertexAIGateway, genai

    if genai is None:
        pytest.skip("google-genai SDK not installed")

    mock_response = MagicMock()
    mock_response.text = '{"vertex": "success"}'
    mock_response.usage_metadata.prompt_token_count = 12
    mock_response.usage_metadata.candidates_token_count = 18

    with patch("app.agents.llm.providers.vertex_ai_provider.genai.Client") as mock_cls:
        mock_client = MagicMock()
        mock_client.aio.models.generate_content = AsyncMock(return_value=mock_response)
        mock_cls.return_value = mock_client

        gateway = VertexAIGateway(
            model="gemini-2.5-flash",
            project="test-project",
            location="us-central1",
        )
        result = await gateway.generate(
            messages=[
                {"role": "system", "content": "system prompt"},
                {"role": "user", "content": "user query"},
            ],
            response_format={"type": "json_object"},
        )
        assert result.parsed == {"vertex": "success"}
        assert result.input_tokens == 12
        assert result.output_tokens == 18

