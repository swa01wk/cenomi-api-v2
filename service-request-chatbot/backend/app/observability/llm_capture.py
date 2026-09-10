"""Capture a gateway.complete_json() result onto the active trace.

Fail-safe: never raises into the calling node.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID

import structlog

log = structlog.get_logger(__name__)


def _extract_uuid(value: Any) -> UUID | None:
    if value is None:
        return None
    try:
        return value if isinstance(value, UUID) else UUID(str(value))
    except (ValueError, AttributeError):
        return None


async def capture_complete_json(
    state: dict[str, Any],
    *,
    run_name: str,
    prompt_name: str,
    gateway: Any,
    input_tokens: int,
    output_tokens: int,
    latency_ms: int,
    structured_output: dict[str, Any] | None = None,
    parse_success: bool = True,
    parse_error: str | None = None,
) -> None:
    """Open an LLM child run and persist token/cost for a complete_json call."""
    tm = state.get("trace_manager")
    trace_id = _extract_uuid(state.get("trace_id"))
    if tm is None or trace_id is None:
        return
    node_run_id = _extract_uuid(state.get("_trace_node_run_id"))
    try:
        llm_run_id = await tm.start_run(
            trace_id=trace_id,
            run_name=run_name,
            run_type="LLM",
            parent_run_id=node_run_id,
        )
    except Exception:
        log.warning("llm_capture.start_run.failed", run_name=run_name, exc_info=True)
        return
    if llm_run_id is None:
        return
    try:
        await tm.capture_llm_call(
            trace_id=trace_id,
            run_id=llm_run_id,
            provider=getattr(gateway, "provider_name", "openai"),
            model=getattr(gateway, "model", None),
            temperature=Decimal("0"),
            prompt_name=prompt_name,
            prompt_version="1.0",
            input_tokens=input_tokens or None,
            output_tokens=output_tokens or None,
            total_tokens=(input_tokens + output_tokens) or None,
            latency_ms=latency_ms,
            structured_output=structured_output or {},
            parse_success=parse_success,
            parse_error=parse_error,
        )
        await tm.finish_run(
            run_id=llm_run_id,
            output=structured_output or {},
            status="SUCCESS" if parse_success else "FAILED",
            error_message=parse_error,
        )
    except Exception:
        log.warning("llm_capture.capture_llm_call.failed", run_name=run_name, exc_info=True)
