"""Notification agent — list / explain / mark-read.

- ``response_ui.type = notification_list`` — action-required inbox fetched from
  the platform's TC-3520 agent endpoints, grouped by SR sub-category.  Each row
  carries ``sr_id`` so the UI can open it via ``active_notification_id``.
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
from app.agents.services.notification_inbox_service import (
    SUB_CATEGORY_LABELS,
    NotificationInboxResult,
    get_notification_inbox_service,
)
from app.agents.services.notification_intent import (
    classify_notification_intent_from_state,
    parse_notification_list_filters,
    resolved_notification_id_from_state,
)
from app.agents.services.review_decorations_service import build_inbox_decorations
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

    parts = [f"{title} ({notification_id})."]
    if status:
        parts.append(f"Status: {status}.")
    if body:
        parts.append(body if body.endswith(".") else f"{body}.")
    if related:
        parts.append(f"Related service request: {related}.")
    return " ".join(parts)


def _group_summary(result: NotificationInboxResult) -> str:
    parts = [
        f"{group.total_count} {SUB_CATEGORY_LABELS.get(group.sub_category, group.sub_category)}"
        for group in result.groups
    ]
    return ", ".join(parts)


def _serialize_groups(result: NotificationInboxResult) -> list[dict[str, Any]]:
    return [
        {
            "sub_category": group.sub_category,
            "label": SUB_CATEGORY_LABELS.get(group.sub_category, group.sub_category),
            "total_count": group.total_count,
            "notifications": [
                {
                    "notification_id": item.notification_id,
                    "sr_id": item.sr_id,
                    "message": item.message,
                    "created_at": item.created_at,
                    "read": bool(item.read_at),
                    "action_required": item.action_required,
                    "lease_id": item.lease_id,
                    "unique_property_id": item.unique_property_id,
                }
                for item in group.items
            ],
        }
        for group in result.groups
    ]


async def _list_response(state: ServiceRequestState) -> dict[str, Any]:
    auth: AuthContext | None = state.get("auth_context")  # type: ignore[assignment]
    decorations = build_inbox_decorations(auth) if auth is not None else {}

    user_id = (auth.subject_id if auth is not None else "") or ""
    if not user_id or user_id in ("anonymous", "unauthenticated", "chat_user"):
        return {
            "intent": "LIST_NOTIFICATIONS",
            "response_message": (
                "I can't load your notifications without a signed-in platform "
                "account. Please sign in and try again."
            ),
            "response_ui": {"type": "message"},
            "status": "WAITING_FOR_USER",
        }

    filters = parse_notification_list_filters(state.get("user_message") or "")
    service = get_notification_inbox_service()
    roles = auth.roles if auth is not None else frozenset()
    inbox = await service.fetch_inbox(user_id=user_id, roles=roles, filters=filters)

    if not inbox.success:
        log.warning("notification_entry.list_failed", error=inbox.error)
        return {
            "intent": "LIST_NOTIFICATIONS",
            "response_message": (
                "Sorry — I couldn't load your notifications right now. "
                "Please try again in a moment."
            ),
            "response_ui": {"type": "message"},
            "status": "WAITING_FOR_USER",
        }

    applied_filters: dict[str, Any] = {}
    if filters.unread is not None:
        applied_filters["unread"] = filters.unread
    if filters.sub_categories:
        applied_filters["sub_categories"] = list(filters.sub_categories)

    if not inbox.groups:
        message = (
            "You have no action-required notifications matching that filter."
            if applied_filters
            else "You're all caught up — no action-required notifications."
        )
    else:
        message = (
            f"You have {inbox.total} action-required "
            f"notification{'s' if inbox.total != 1 else ''}: {_group_summary(inbox)}. "
            "Open one to continue its workflow."
        )
    if inbox.partial_errors:
        message += " (Some notification sources were unavailable.)"

    result: dict[str, Any] = {
        "intent": "LIST_NOTIFICATIONS",
        "response_message": message,
        "response_ui": {
            "type": "notification_list",
            "message": message,
            "filters": applied_filters,
            "groups": _serialize_groups(inbox),
        },
        "status": "WAITING_FOR_USER",
    }
    if decorations:
        result["response_decorations"] = decorations
    return result


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
        props = {
            "found": True,
            "notification_id": notification_id,
            "title": snapshot.get("title") or snapshot.get("subject"),
            "body": snapshot.get("body")
            or snapshot.get("message")
            or snapshot.get("description"),
            "status": snapshot.get("status"),
            "related_sr_id": snapshot.get("related_sr_id") or snapshot.get("sr_id"),
            "message": message,
        }
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
        return await _list_response(state)
    if intent == "MARK_NOTIFICATION_READ":
        return _mark_read_response(state)
    return _detail_response(state)
