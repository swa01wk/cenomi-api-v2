"""Unit tests for capture_complete_json (FAQ / response-gen token honesty)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from app.observability.llm_capture import capture_complete_json


async def test_capture_complete_json_invokes_trace_manager() -> None:
    tm = MagicMock()
    tm.start_run = AsyncMock(return_value=uuid4())
    tm.capture_llm_call = AsyncMock()
    tm.finish_run = AsyncMock()
    gateway = MagicMock(provider_name="azure_openai", model="gpt-4o")
    trace_id = uuid4()
    state = {"trace_manager": tm, "trace_id": trace_id}

    await capture_complete_json(
        state,
        run_name="faq_llm_call",
        prompt_name="faq_prompt",
        gateway=gateway,
        input_tokens=12,
        output_tokens=34,
        latency_ms=90,
        structured_output={"message": "ok"},
    )

    tm.start_run.assert_awaited_once()
    tm.capture_llm_call.assert_awaited_once()
    kwargs = tm.capture_llm_call.await_args.kwargs
    assert kwargs["input_tokens"] == 12
    assert kwargs["output_tokens"] == 34
    assert kwargs["total_tokens"] == 46
    assert kwargs["latency_ms"] == 90
    assert kwargs["prompt_name"] == "faq_prompt"
    tm.finish_run.assert_awaited_once()


async def test_capture_complete_json_noop_without_trace_manager() -> None:
    await capture_complete_json(
        {},
        run_name="x",
        prompt_name="y",
        gateway=MagicMock(),
        input_tokens=1,
        output_tokens=1,
        latency_ms=1,
    )


async def test_capture_complete_json_swallows_capture_errors() -> None:
    tm = MagicMock()
    tm.start_run = AsyncMock(return_value=uuid4())
    tm.capture_llm_call = AsyncMock(side_effect=RuntimeError("boom"))
    tm.finish_run = AsyncMock()
    await capture_complete_json(
        {"trace_manager": tm, "trace_id": uuid4()},
        run_name="response_generation_llm_call",
        prompt_name="response_generation",
        gateway=MagicMock(),
        input_tokens=1,
        output_tokens=2,
        latency_ms=3,
    )
    tm.finish_run.assert_not_awaited()


def test_faq_and_response_gen_call_capture_complete_json() -> None:
    from pathlib import Path

    backend = Path(__file__).resolve().parents[2] / "app" / "agents" / "graph" / "nodes"
    faq = (backend / "faq" / "faq_node.py").read_text()
    resp = (backend / "shared" / "response_generation_node.py").read_text()
    assert "capture_complete_json" in faq
    assert "capture_complete_json" in resp
    assert faq.count("await capture_complete_json(") >= 1
    assert resp.count("await capture_complete_json(") >= 1
