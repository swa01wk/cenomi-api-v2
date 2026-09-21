"""Record a platform API call from inside a graph node.

The submission nodes that run under ``@trace_node`` have no run of their own;
the decorator injects the node's run id as ``_trace_node_run_id``.  This helper
attaches the call to that run so ``agent_tool_calls`` — and the tool
reliability numbers built on it — cover every flow, not only the ones that
open a dedicated tool run.

Never raises: a tracing failure must not change the node's outcome.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from app.observability.redaction import redact

logger = logging.getLogger(__name__)


def _to_uuid(value: Any) -> UUID | None:
    if value is None:
        return None
    if isinstance(value, UUID):
        return value
    try:
        return UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        return None


async def record_tool_call(
    state: dict[str, Any],
    *,
    tool_name: str,
    tool_type: str,
    success: bool,
    latency_ms: int | None,
    error: str | None = None,
    status_code: int | None = None,
    request_payload: dict[str, Any] | None = None,
    response_payload: dict[str, Any] | None = None,
) -> None:
    """Persist one platform call against the current node's run."""
    trace_manager = state.get("trace_manager")
    trace_id = _to_uuid(state.get("trace_id"))
    run_id = _to_uuid(state.get("_trace_node_run_id"))
    if trace_manager is None or trace_id is None or run_id is None:
        return
    try:
        await trace_manager.capture_tool_call(
            trace_id=trace_id,
            run_id=run_id,
            tool_name=tool_name,
            tool_type=tool_type,
            request_payload=redact(request_payload or {}),
            response_payload=response_payload,
            status_code=status_code,
            success=success,
            latency_ms=latency_ms,
            error_message=error,
        )
    except Exception:
        logger.warning("record_tool_call failed tool=%s", tool_name, exc_info=True)


async def record_sr_api_call(
    state: dict[str, Any],
    *,
    tool_name: str,
    tool_type: str,
    request_payload: dict[str, Any] | None,
    result: Any,
) -> None:
    """Persist one Service Request API call (a ``ServiceRequestCreationResult``)."""
    error = getattr(result, "error", None)
    await record_tool_call(
        state,
        tool_name=tool_name,
        tool_type=tool_type,
        success=error is None and getattr(result, "sr_id", None) is not None,
        latency_ms=getattr(result, "latency_ms", None),
        error=error,
        status_code=getattr(result, "status_code", None),
        request_payload=request_payload,
        response_payload=getattr(result, "response_payload", None),
    )


async def record_sr_decision(
    state: dict[str, Any],
    *,
    approved: bool,
    sr_id: str | None,
    flow: str,
    stage: str | None,
) -> None:
    """Emit ``sr_approved`` / ``sr_rejected`` for a reviewer decision."""
    trace_manager = state.get("trace_manager")
    trace_id = _to_uuid(state.get("trace_id"))
    if trace_manager is None or trace_id is None:
        return
    try:
        await trace_manager.emit_sr_decision(
            trace_id,
            approved=approved,
            sr_id=sr_id,
            flow=flow,
            stage=stage,
            actor_roles=_actor_roles(state.get("auth_context")),
        )
    except Exception:
        logger.warning("record_sr_decision failed flow=%s", flow, exc_info=True)


def _actor_roles(auth_context: Any) -> list[str]:
    roles = getattr(auth_context, "roles", None)
    if not roles:
        return []
    return sorted(str(r) for r in roles)
