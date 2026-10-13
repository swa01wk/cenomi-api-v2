"""Notification inbox read API.

``GET /api/v1/notifications/inbox`` — a passive read for UI surfaces that show
the action-required inbox without holding a conversation (the Copilot welcome
panel, TC-3662).

Why this is not a chat turn: every ``POST /chat/service-request`` creates or
appends a ChatSession, persists a user + assistant message, and opens a trace.
A panel widget fetching on open would pollute transcripts and burn traces for
data it only wants to render.  This route touches no session state — it reads
the JWT for the platform ``user_id`` and roles, calls the same
``notification_inbox_service`` the chat node uses, and serialises with the same
shared payload helpers, so both surfaces return identical rows.
"""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.agents.services.notification_inbox_service import (
    NOTIFICATION_SUB_CATEGORIES,
    NotificationListFilters,
    get_notification_inbox_service,
)
from app.agents.services.notification_payload import serialize_groups
from app.core.security import get_auth_context
from app.types.chat import AuthContext

log = structlog.get_logger(__name__)

router = APIRouter()

# Subjects that carry no platform user id — the platform endpoints require one.
_ANONYMOUS_SUBJECTS = frozenset({"anonymous", "unauthenticated", "chat_user", ""})

_MAX_LIMIT = 50


class NotificationInboxResponse(BaseModel):
    """Grouped action-required notifications for the signed-in user."""

    groups: list[dict[str, Any]] = Field(
        default_factory=list,
        description=(
            "Non-empty groups in fixed display order. Each carries "
            "service_category, sub_category, label, total_count, "
            "action_required_count, notifications[], page, has_more."
        ),
    )
    partial_errors: dict[str, str] = Field(
        default_factory=dict,
        description="Sub-categories whose platform call failed; others still render.",
    )


@router.get(
    "/notifications/inbox",
    response_model=NotificationInboxResponse,
    summary="Action-required notification inbox (read-only)",
    description=(
        "Role-scoped inbox for panel surfaces. Returns the same group/row shape "
        "as the chat agent's notification_list UI payload, without starting a "
        "chat turn."
    ),
)
async def get_notification_inbox(
    sub_category: str | None = Query(
        default=None,
        description=(
            "Restrict to one group (HANDOVER, FIT_OUT_CONFIRM_FOS, "
            "FIT_OUT_CONFIRM_END, FIT_OUT_CONFIRM_TRADING, FITOUT_PROGRESS, SALES_DATA). "
            "Omit for all groups the caller's roles allow."
        ),
    ),
    page: int = Query(default=1, ge=1, description="1-based page."),
    limit: int = Query(default=5, ge=1, le=_MAX_LIMIT, description="Rows per group."),
    unread: bool | None = Query(default=None, description="Filter by read state."),
    auth: AuthContext = Depends(get_auth_context),
) -> NotificationInboxResponse:
    user_id = (auth.subject_id or "").strip()
    if user_id in _ANONYMOUS_SUBJECTS:
        # No platform user id — nothing to scope the inbox to.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="A signed-in platform account is required to read notifications.",
        )

    requested: tuple[str, ...] | None = None
    if sub_category:
        canonical = sub_category.strip().upper().replace(" ", "_").replace("-", "_")
        if canonical not in NOTIFICATION_SUB_CATEGORIES:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    "Unknown sub_category. Expected one of: "
                    + ", ".join(NOTIFICATION_SUB_CATEGORIES)
                ),
            )
        requested = (canonical,)

    service = get_notification_inbox_service()
    result = await service.fetch_inbox(
        user_id=user_id,
        roles=auth.roles,
        filters=NotificationListFilters(sub_categories=requested, unread=unread),
        limit=limit,
        page=page,
    )

    if not result.success:
        log.warning("notifications.inbox.unavailable", error=result.error)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="The notifications service is unavailable. Please try again.",
        )

    return NotificationInboxResponse(
        groups=serialize_groups(result),
        partial_errors=result.partial_errors,
    )
