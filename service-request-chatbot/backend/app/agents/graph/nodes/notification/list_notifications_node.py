"""List-notifications node — the reconciled spec's ``list_notifications_node``.

Responsibilities (spec §2.1):
- NL filter extraction — delegated to
  :func:`app.agents.services.notification_intent.parse_notification_list_filters`
  (the spec's ``_extract_filters`` responsibility).
- MSP fan-out via :mod:`app.agents.services.notification_inbox_service` over the
  TC-3520 platform endpoints (FIT_OUT_CONFIRM_HO decommissioned).
- Row normalisation + grouping in fixed display order, emitted as
  ``response_ui = {"type": "notification_list", "groups": [...]}`` with the
  group-level ``service_category`` / ``action_required_count`` contract fields.
- View-more paging via the structured ``notification_list_more`` request.
"""

from __future__ import annotations

from typing import Any

import structlog

from app.agents.graph.state import ServiceRequestState
from app.agents.services.notification_inbox_service import (
    NOTIFICATION_SUB_CATEGORIES,
    SUB_CATEGORY_LABELS,
    NotificationListFilters,
    get_notification_inbox_service,
)
from app.agents.services.notification_intent import parse_notification_list_filters
from app.agents.services.notification_payload import (
    group_summary as _group_summary,
    serialize_groups as _serialize_groups,
)
from app.observability.decorators import trace_node
from app.types.chat import AuthContext

log = structlog.get_logger(__name__)


async def _list_response(state: ServiceRequestState) -> dict[str, Any]:
    auth: AuthContext | None = state.get("auth_context")  # type: ignore[assignment]

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

    # View-more paging: one group, one specific page (structured request from
    # the card's button — no NL parsing involved).
    list_more = state.get("notification_list_more")
    page = 1
    if isinstance(list_more, dict) and list_more.get("sub_category"):
        sub = str(list_more["sub_category"]).strip().upper()
        if sub not in NOTIFICATION_SUB_CATEGORIES:
            sub = "HANDOVER"
        try:
            page = max(int(list_more.get("page") or 1), 1)
        except (TypeError, ValueError):
            page = 1
        unread = list_more.get("unread")
        filters = NotificationListFilters(
            sub_categories=(sub,),
            unread=unread if isinstance(unread, bool) else None,
        )
    else:
        filters = parse_notification_list_filters(state.get("user_message") or "")
    service = get_notification_inbox_service()
    roles = auth.roles if auth is not None else frozenset()
    inbox = await service.fetch_inbox(
        user_id=user_id, roles=roles, filters=filters, page=page
    )

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
        if page > 1:
            message = "That's everything — no more notifications in this group."
        elif applied_filters:
            message = "You have no action-required notifications matching that filter."
        else:
            message = "You're all caught up — no action-required notifications."
    elif page > 1:
        group = inbox.groups[0]
        start = (page - 1) * 10 + 1
        end = start + len(group.items) - 1
        label = SUB_CATEGORY_LABELS.get(group.sub_category, group.sub_category)
        message = (
            f"Showing {start}–{end} of {group.total_count} {label} notifications."
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
    return result


@trace_node("list_notifications", "AGENT")
async def list_notifications_node(state: ServiceRequestState) -> dict[str, Any]:
    """LangGraph node: render the grouped action-required inbox."""
    result = await _list_response(state)
    # Orchestration pre-seeds the reviewer inbox picker ("View Service
    # Request") into response_decorations before routing — the list card
    # supersedes it, so clear it for finalize.
    result["response_decorations"] = {}
    return result
