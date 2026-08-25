"""Deterministic intent classification for the notification agent.

LLM is not required for common list / detail / mark-read phrases.  Viewing
context (page the user is on) resolves deictic references so the UI can later
send ``viewing_notification_id`` without the user repeating the ID in chat.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from app.agents.services.viewing_context import (
    extract_notification_id,
    references_viewing_notification,
    references_viewing_service_request,
    resolve_notification_id,
    viewing_ids_from_state,
)

NotificationIntent = Literal[
    "LIST_NOTIFICATIONS",
    "GET_NOTIFICATION_DETAIL",
    "MARK_NOTIFICATION_READ",
]

NOTIFICATION_INTENTS: frozenset[str] = frozenset(
    {
        "LIST_NOTIFICATIONS",
        "GET_NOTIFICATION_DETAIL",
        "MARK_NOTIFICATION_READ",
    }
)

NOTIFICATION_ACTION_OVERRIDES: frozenset[str] = frozenset(
    {
        "mark_notification_read",
        "mark_read",
        "open_notification",
    }
)

_LIST_PATTERN = re.compile(
    r"\b(list|show|view|see|display|get|open|check)\b.*\b(notifications?|alerts?|inbox)\b",
    re.IGNORECASE,
)
_LIST_PHRASES = (
    "my notifications",
    "my alerts",
    "notification inbox",
    "what notifications",
    "any notifications",
    "unread notifications",
)
_MARK_READ_PATTERN = re.compile(
    r"\b(mark|set)\b.*\b(read|seen|done)\b"
    r"|\b(dismiss|acknowledge|ack)\b"
    r"|\b(i('ve| have)\s+read)\b",
    re.IGNORECASE,
)


def is_list_notifications_message(message: str) -> bool:
    lower = (message or "").lower().strip()
    if not lower:
        return False
    if extract_notification_id(lower):
        return False
    if _LIST_PATTERN.search(lower):
        return True
    return any(phrase in lower for phrase in _LIST_PHRASES)


def is_mark_notification_read_message(
    message: str,
    viewing_notification_id: str | None = None,
) -> bool:
    if not _MARK_READ_PATTERN.search(message or ""):
        return False
    if extract_notification_id(message):
        return True
    if viewing_notification_id and (
        references_viewing_notification(message) or "this" in (message or "").lower()
    ):
        return True
    return False


def classify_notification_intent(
    message: str,
    viewing_notification_id: str | None = None,
) -> NotificationIntent | None:
    """Return a notification intent when it can be decided without an LLM call."""
    if is_mark_notification_read_message(message, viewing_notification_id):
        return "MARK_NOTIFICATION_READ"
    if is_list_notifications_message(message):
        return "LIST_NOTIFICATIONS"
    if extract_notification_id(message):
        return "GET_NOTIFICATION_DETAIL"
    if viewing_notification_id and references_viewing_notification(message):
        return "GET_NOTIFICATION_DETAIL"
    if viewing_notification_id and not (message or "").strip():
        return "GET_NOTIFICATION_DETAIL"
    return None


def classify_notification_intent_from_state(state: dict[str, Any]) -> NotificationIntent | None:
    """Classify from graph state, including UI ``action_override`` and viewing IDs."""
    action = (state.get("action_override") or "").strip()
    viewing_notification_id, _viewing_sr = viewing_ids_from_state(state)
    if action in {"mark_notification_read", "mark_read"}:
        return "MARK_NOTIFICATION_READ"
    if action == "open_notification":
        return "GET_NOTIFICATION_DETAIL"
    return classify_notification_intent(
        state.get("user_message") or "",
        viewing_notification_id,
    )


def should_bypass_for_notification_context(state: dict[str, Any]) -> bool:
    """True when load/supervisor session continuity must yield to notification context.

    Used so a mid-handover session still handles "explain this notification"
    when the UI sends viewing context, instead of swallowing the turn in the
    active SR agent.
    """
    if classify_notification_intent_from_state(state) is not None:
        return True
    viewing_notification_id, viewing_sr_id = viewing_ids_from_state(state)
    message = state.get("user_message") or ""
    if viewing_sr_id and references_viewing_service_request(message):
        # Generic "explain this" with a notification in view belongs to
        # the notification agent, not SR status.
        if viewing_notification_id and references_viewing_notification(message):
            return True
        if classify_notification_intent(message, viewing_notification_id) is not None:
            return True
        return True
    return False


def resolved_notification_id_from_state(state: dict[str, Any]) -> str | None:
    viewing_notification_id, _viewing_sr = viewing_ids_from_state(state)
    return resolve_notification_id(state.get("user_message") or "", viewing_notification_id)
