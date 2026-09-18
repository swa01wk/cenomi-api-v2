"""Open-notification node — the reconciled spec's ``open_notification_node``.

A routing shim with no business logic (spec §2.1):

- **FIT_OUT_AND_HANDOVER** — turns ``active_notification_id`` into an open-SR
  seed and hands off to ``sr_status_sync`` (fm / rdd / fitout entry).
- **SALES_DATA** — ``text_content.id`` is a **lease code**, not an SR id.
  Seeds ``backend_refs.notification_context`` and hands off to
  ``sales_data_entry`` (no ``sr_status_sync``).

Contract:
- ``state["active_notification_id"]`` is a dict ``{id, sub_category?}`` or a
  bare string (treated as the SR id directly — spec fallback).
- For SR notifications, ``id`` is ``text_content.id`` (SR number), never the
  notification row id.
- For sales-data notifications, ``id`` is the lease code (PMSTenantCode).
  Optional ``period_hint`` / ``message`` / ``key`` refine the Sales Data Agent
  first turn.
- A live session's ``active_agent`` is never clobbered on the SR path.
  Sales-data opens always set ``sales_data_agent`` (FE starts a new session).
- Missing id after all fallbacks → plain error message turn.
"""

from __future__ import annotations

from typing import Any

import structlog

from app.agents.graph.state import ServiceRequestState
from app.agents.registry import SERVICE_REQUEST_AGENT_REGISTRY, lookup_agent
from app.agents.services.notification_sales_data import (
    SALES_DATA_SUB_CATEGORY,
    build_sales_data_notification_context,
    is_sales_data_notification_ref,
    sales_data_intent_for_ref,
)
from app.observability.decorators import trace_node

log = structlog.get_logger(__name__)

# Spec §2.1 export — derived from the agent registry so there is exactly one
# source of truth for sub-category routing. FIT_OUT_CONFIRM_HO is absent
# (decommissioned).
SUB_CATEGORY_TO_AGENT: dict[str, str] = {
    sub: cfg["agent_name"]
    for sub, cfg in SERVICE_REQUEST_AGENT_REGISTRY.get("FIT_OUT_AND_HANDOVER", {}).items()
}
SUB_CATEGORY_TO_AGENT[SALES_DATA_SUB_CATEGORY] = "sales_data_agent"


def _normalise_ref(raw: Any) -> dict[str, Any] | None:
    """Coerce the state value to ``{id, sub_category?}``; bare string → sr_id."""
    if isinstance(raw, str) and raw.strip():
        return {"id": raw.strip()}
    if isinstance(raw, dict) and str(raw.get("id") or "").strip():
        normalised = {
            key: value
            for key, value in raw.items()
            if key != "sub_category"
        }
        normalised["id"] = str(raw["id"]).strip()
        sub = str(raw.get("sub_category") or "").strip().upper() or None
        normalised["sub_category"] = sub
        return normalised
    return None


def build_open_notification_seed(
    notification_ref: dict[str, Any],
    *,
    session_active_agent: str | None,
) -> dict[str, Any]:
    """State updates for an OPEN_NOTIFICATION turn.

    Sales-data refs seed the Sales Data Agent + ``notification_context``.
    SR refs map ``sub_category`` via the FIT_OUT_AND_HANDOVER registry;
    unmapped sub-categories seed nothing extra — ``sr_status_sync`` re-derives
    sub_category from the SR payload.  A live session's ``active_agent`` is
    never clobbered on the SR path.
    """
    if is_sales_data_notification_ref(notification_ref):
        intent = sales_data_intent_for_ref(notification_ref)
        return {
            "intent": intent,
            "active_agent": "sales_data_agent",
            "service_category": "SALES_DATA",
            "sub_category": SALES_DATA_SUB_CATEGORY,
        }

    updates: dict[str, Any] = {"intent": "OPEN_NOTIFICATION"}
    sub = str(notification_ref.get("sub_category") or "").strip().upper()
    agent_cfg = lookup_agent("FIT_OUT_AND_HANDOVER", sub) if sub else None
    if agent_cfg and not session_active_agent:
        updates["active_agent"] = agent_cfg["agent_name"]
        updates["service_category"] = "FIT_OUT_AND_HANDOVER"
        updates["sub_category"] = sub
    return updates


@trace_node("open_notification", "AGENT")
async def open_notification_node(state: ServiceRequestState) -> dict[str, Any]:
    """LangGraph node: seed the SR or sales-data handoff."""
    ref = _normalise_ref(state.get("active_notification_id"))
    if ref is None:
        log.warning("open_notification.missing_id")
        return {
            "intent": "OPEN_NOTIFICATION",
            "response_message": (
                "I couldn't work out which item that notification refers to. "
                "Please open it from the notifications page, or tell me the "
                "lease code or service request number."
            ),
            "response_ui": {"type": "message"},
            "response_decorations": {},
            "status": "WAITING_FOR_USER",
        }

    updates = build_open_notification_seed(
        ref, session_active_agent=state.get("active_agent")
    )
    backend_refs = dict(state.get("backend_refs") or {})
    backend_refs["opened_from_notification"] = True

    if is_sales_data_notification_ref(ref):
        ctx = build_sales_data_notification_context(ref)
        backend_refs.pop("sr_id", None)
        backend_refs["notification_context"] = ctx
        updates["backend_refs"] = backend_refs
        corrected = dict(state.get("corrected_fields") or {})
        corrected["notification_context"] = ctx
        updates["corrected_fields"] = corrected
        log.info(
            "open_notification.seeded_sales_data",
            lease_code=ctx.get("lease_code"),
            period_hint=ctx.get("period_hint"),
            intent=updates.get("intent"),
        )
        return updates

    backend_refs["sr_id"] = ref["id"]
    updates["backend_refs"] = backend_refs
    log.info(
        "open_notification.seeded",
        sr_id=ref["id"],
        sub_category=ref.get("sub_category"),
        seeded_agent=updates.get("active_agent"),
    )
    return updates
