"""Notification context node — explain / mark-read (Flow C).

The inbox list lives in ``list_notifications_node``; the open handoff in
``open_notification_node`` (reconciled-spec architecture).  This node keeps the
deictic viewing-context layer:

- ``response_ui.type = notification_detail_card`` — explain the viewed item
  (page viewing context).
- ``response_ui.type = notification_action`` / ``action = mark_read`` — UI
  performs the mutation (no platform mark-read endpoint on the agent mux).

The backend resolves *which* notification the user means (explicit ID vs
``viewing_notification_id``) and composes the assistant reply.  It does not
invent notification payloads.
"""

from __future__ import annotations

from typing import Any

import structlog

from app.agents.graph.state import ServiceRequestState
from app.agents.graph.nodes.notification.list_notifications_node import (
    list_notifications_node,
)
from app.agents.services.notification_intent import (
    classify_notification_intent_from_state,
    resolved_notification_id_from_state,
)
from app.agents.services.viewing_context import viewing_ids_from_state
from app.observability.decorators import trace_node
from app.types.chat import AuthContext

log = structlog.get_logger(__name__)


def _snapshot_from_state(state: ServiceRequestState) -> dict[str, Any]:
    snap = state.get("viewing_notification")
    if isinstance(snap, dict) and snap:
        return dict(snap)
    msp = state.get("msp_context") or {}
    nested = msp.get("viewing_notification") if isinstance(msp, dict) else None
    if isinstance(nested, dict) and nested:
        return dict(nested)
    return {}


_UUID_LIKE = __import__("re").compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", __import__("re").IGNORECASE
)


def _summarize_snapshot(notification_id: str, snapshot: dict[str, Any]) -> str:
    title = snapshot.get("title") or snapshot.get("subject") or "Notification"
    body = (
        snapshot.get("body")
        or snapshot.get("message")
        or snapshot.get("description")
        or ""
    ).strip()
    related = snapshot.get("related_sr_id") or snapshot.get("sr_id")
    status = snapshot.get("status")
    if snapshot.get("read") is True:
        status = status or "read"
    elif snapshot.get("read") is False:
        status = status or "unread"

    # Platform row ids are UUIDs — meaningless to the user, so omit them.
    parts = [
        f"{title}." if _UUID_LIKE.match(notification_id) else f"{title} ({notification_id})."
    ]
    if status:
        parts.append(f"Status: {status}.")
    if body:
        parts.append(body if body.endswith(".") else f"{body}.")
    if related:
        parts.append(f"Related service request: {related}.")
    return " ".join(parts)


def _detail_response(state: ServiceRequestState) -> dict[str, Any]:
    notification_id = resolved_notification_id_from_state(state)
    snapshot = _snapshot_from_state(state)
    if not notification_id and snapshot:
        notification_id = str(
            snapshot.get("id") or snapshot.get("notification_id") or ""
        ).strip() or None

    if not notification_id:
        return {
            "intent": "GET_NOTIFICATION_DETAIL",
            "response_message": (
                "Which notification should I look at? You can open it in the "
                "inbox, or give me the notification ID (for example NTF-000012)."
            ),
            "response_ui": {"type": "message"},
            "status": "WAITING_FOR_USER",
        }

    if snapshot:
        message = _summarize_snapshot(notification_id, snapshot)
        related_sr_id = snapshot.get("related_sr_id") or snapshot.get("sr_id")
        props = {
            "found": True,
            "notification_id": notification_id,
            "title": snapshot.get("title") or snapshot.get("subject"),
            "body": snapshot.get("body")
            or snapshot.get("message")
            or snapshot.get("description"),
            "status": snapshot.get("status"),
            "related_sr_id": related_sr_id,
            # Lets the FE's "View Service Request" button route the open
            # precisely (active_notification_id.sub_category).
            "sub_category": snapshot.get("sub_category"),
            "message": message,
        }
        if related_sr_id:
            message += " You can open the related service request to continue its workflow."
            props["message"] = message
    else:
        message = (
            f"You're viewing notification {notification_id}. "
            "Ask me to explain it, check the related service request, "
            "or mark it as read."
        )
        props = {
            "found": False,
            "notification_id": notification_id,
            "message": message,
        }

    return {
        "intent": "GET_NOTIFICATION_DETAIL",
        "response_message": message,
        "response_ui": {
            "type": "notification_detail_card",
            **props,
        },
        "status": "WAITING_FOR_USER",
    }


def _mark_read_response(state: ServiceRequestState) -> dict[str, Any]:
    notification_id = resolved_notification_id_from_state(state)
    if not notification_id:
        return {
            "intent": "MARK_NOTIFICATION_READ",
            "response_message": (
                "Which notification should I mark as read? Open it first, or "
                "include the notification ID (for example NTF-000012)."
            ),
            "response_ui": {"type": "message"},
            "status": "WAITING_FOR_USER",
        }

    message = f"I'll mark notification {notification_id} as read."
    return {
        "intent": "MARK_NOTIFICATION_READ",
        "response_message": message,
        "response_ui": {
            "type": "notification_action",
            "action": "mark_read",
            "notification_id": notification_id,
            "message": message,
        },
        "status": "WAITING_FOR_USER",
    }


@trace_node("notification", "AGENT")
async def notification_entry_node(state: ServiceRequestState) -> dict[str, Any]:
    """LangGraph node: handle notification list / detail / mark-read turns.

    Does not change ``active_agent`` so an in-progress SR workflow is preserved.
    """
    intent = classify_notification_intent_from_state(state) or "GET_NOTIFICATION_DETAIL"
    viewing_notification_id, viewing_sr_id = viewing_ids_from_state(state)
    log.info(
        "notification_entry.classified",
        intent=intent,
        viewing_notification_id=viewing_notification_id,
        viewing_service_request_id=viewing_sr_id,
    )

    if intent == "LIST_NOTIFICATIONS":
        # List turns are normally routed to list_notifications_node directly;
        # delegate defensively so a stray route cannot regress behaviour.
        return await list_notifications_node(state)
    if intent == "MARK_NOTIFICATION_READ":
        result = _mark_read_response(state)
    else:
        result = _detail_response(state)
    # Orchestration pre-seeds the reviewer inbox picker ("View Service
    # Request") into response_decorations before routing. Notification turns
    # render their own card/message — clear it so finalize can't attach the
    # picker button to this response.
    result["response_decorations"] = {}
    return result
