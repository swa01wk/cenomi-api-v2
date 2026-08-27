"""Open-notification node — the reconciled spec's ``open_notification_node``.

A routing shim with no business logic (spec §2.1): it turns the UI's
``active_notification_id`` into the open-SR seed and hands off to the EXISTING
``sr_status_sync`` pipeline, which fetches the live SR, maps operations to a
workflow stage, and lands in fm_review_entry / rdd_review_entry /
fitout_confirm_entry.

Contract:
- ``state["active_notification_id"]`` is a dict ``{id, sub_category?}`` or a
  bare string (treated as the SR id directly — spec fallback).
- ``id`` is the SR id from the platform notification's ``text_content.id``,
  never the notification row id.
- ``sub_category`` maps to an agent via :data:`SUB_CATEGORY_TO_AGENT`; unmapped
  values (FIT_OUT_CONFIRM_HO is decommissioned) seed the sr_id only and let
  sr_status_sync re-derive from the SR payload.
- A live session's ``active_agent`` is never clobbered.
- Missing sr_id after all fallbacks → plain error message turn.
"""

from __future__ import annotations

from typing import Any

import structlog

from app.agents.graph.state import ServiceRequestState
from app.agents.registry import SERVICE_REQUEST_AGENT_REGISTRY, lookup_agent
from app.observability.decorators import trace_node

log = structlog.get_logger(__name__)

# Spec §2.1 export — derived from the agent registry so there is exactly one
# source of truth for sub-category routing. FIT_OUT_CONFIRM_HO is absent
# (decommissioned).
SUB_CATEGORY_TO_AGENT: dict[str, str] = {
    sub: cfg["agent_name"]
    for sub, cfg in SERVICE_REQUEST_AGENT_REGISTRY.get("FIT_OUT_AND_HANDOVER", {}).items()
}


def _normalise_ref(raw: Any) -> dict[str, Any] | None:
    """Coerce the state value to ``{id, sub_category?}``; bare string → sr_id."""
    if isinstance(raw, str) and raw.strip():
        return {"id": raw.strip()}
    if isinstance(raw, dict) and str(raw.get("id") or "").strip():
        return {
            "id": str(raw["id"]).strip(),
            "sub_category": str(raw.get("sub_category") or "").strip().upper() or None,
        }
    return None


def build_open_notification_seed(
    notification_ref: dict[str, Any],
    *,
    session_active_agent: str | None,
) -> dict[str, Any]:
    """State updates for an OPEN_NOTIFICATION turn.

    Maps the notification's ``sub_category`` to an agent via the registry when
    possible; unmapped sub-categories seed nothing extra — ``sr_status_sync``
    re-derives sub_category from the SR payload, and a state-level value would
    incorrectly win its cascade.  A live session's ``active_agent`` is never
    clobbered.
    """
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
    """LangGraph node: seed the open-SR handoff and continue to sr_status_sync."""
    ref = _normalise_ref(state.get("active_notification_id"))
    if ref is None:
        log.warning("open_notification.missing_sr_id")
        return {
            "intent": "OPEN_NOTIFICATION",
            "response_message": (
                "I couldn't work out which service request that notification "
                "refers to. Please open it from the notifications page, or "
                "tell me the service request number."
            ),
            "response_ui": {"type": "message"},
            "response_decorations": {},
            "status": "WAITING_FOR_USER",
        }

    updates = build_open_notification_seed(
        ref, session_active_agent=state.get("active_agent")
    )
    backend_refs = dict(state.get("backend_refs") or {})
    backend_refs["sr_id"] = ref["id"]
    backend_refs["opened_from_notification"] = True
    updates["backend_refs"] = backend_refs
    log.info(
        "open_notification.seeded",
        sr_id=ref["id"],
        sub_category=ref.get("sub_category"),
        seeded_agent=updates.get("active_agent"),
    )
    return updates
