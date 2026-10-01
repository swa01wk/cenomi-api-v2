"""Deterministic intent classification for the notification agent.

LLM is not required for common list / detail / mark-read phrases.  Viewing
context (page the user is on) resolves deictic references so the UI can later
send ``viewing_notification_id`` without the user repeating the ID in chat.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from app.agents.services.notification_inbox_service import NotificationListFilters
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


def _is_seeded_open_turn(state: dict[str, Any]) -> bool:
    """True on an OPEN_NOTIFICATION handoff turn (must reach open_notification/sync)."""
    if state.get("active_notification_id") and (state.get("intent") or "") == "OPEN_NOTIFICATION":
        return True
    backend_refs = state.get("backend_refs") or {}
    return bool(backend_refs.get("opened_from_notification"))


def classify_notification_intent_from_state(state: dict[str, Any]) -> NotificationIntent | None:
    """Classify from graph state, including UI ``action_override`` and viewing IDs."""
    if _is_seeded_open_turn(state):
        # OPEN_NOTIFICATION turns carry an authoritative SR seed and must reach
        # sr_status_sync — never the notification side-path.
        return None
    if isinstance(state.get("notification_list_more"), dict):
        return "LIST_NOTIFICATIONS"
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
    if _is_seeded_open_turn(state):
        return False
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


# ---------------------------------------------------------------------------
# NL list filters
# ---------------------------------------------------------------------------

_UNREAD_PATTERN = re.compile(r"\bunread\b|\bnot\s+read\b|\bnew\s+notifications?\b", re.IGNORECASE)

# Sub-category keyword cues.  Bare "end" / "ho" are collision-prone, so they
# require a fit-out cue in the same message; the long forms match on their own.
_SUB_CATEGORY_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("HANDOVER", re.compile(r"\bhand[\s-]?over\b", re.IGNORECASE)),
    (
        "FIT_OUT_CONFIRM_FOS",
        re.compile(r"\bfos\b|\bfit[\s-]?out\s+start\b", re.IGNORECASE),
    ),
    (
        "FIT_OUT_CONFIRM_END",
        re.compile(r"\bfit[\s-]?out\s+(end|complet\w*)\b", re.IGNORECASE),
    ),
    ("FIT_OUT_CONFIRM_TRADING", re.compile(r"\btrading\b", re.IGNORECASE)),
    (
        "OPEN_CEILING_INSPECTION",
        re.compile(r"\bopen\s+ceiling\b|\boci\b", re.IGNORECASE),
    ),
    (
        "CLOSE_CEILING_INSPECTION",
        re.compile(r"\bclose[ds]?\s+ceiling\b|\bcci\b", re.IGNORECASE),
    ),
    ("SALES_DATA", re.compile(r"\bsales[\s-]?data\b", re.IGNORECASE)),
    (
        "CONSTRUCTION_HOT",
        re.compile(
            r"\bconstruction\s+hot\b|\bhot\s+work\b(?!\s+maintenance)",
            re.IGNORECASE,
        ),
    ),
    (
        "CONSTRUCTION_COLD",
        re.compile(
            r"\bconstruction\s+cold\b|\bcold\s+work\b(?!\s+maintenance)",
            re.IGNORECASE,
        ),
    ),
    (
        "CONSTRUCTION_ROOF_ACCESS",
        re.compile(
            r"\bconstruction\s+roof\b|\broof\s+access\b(?!\s+maintenance)",
            re.IGNORECASE,
        ),
    ),
    (
        "MAINTENANCE_HOT",
        re.compile(r"\bmaintenance\s+hot\b", re.IGNORECASE),
    ),
    (
        "MAINTENANCE_COLD",
        re.compile(r"\bmaintenance\s+cold\b", re.IGNORECASE),
    ),
    (
        "MAINTENANCE_ROOF_ACCESS",
        re.compile(r"\bmaintenance\s+roof\b", re.IGNORECASE),
    ),
    (
        "OPERATIONS",
        re.compile(r"\boperations?\s+(work\s+)?permit\b|\bpermit\s+operations?\b", re.IGNORECASE),
    ),
)


# Bare "fit-out notifications" means every confirmation flow, not one stage.
_GENERIC_FITOUT_PATTERN = re.compile(r"\bfit[\s-]?out\b", re.IGNORECASE)
_FITOUT_CONFIRM_SUBS = (
    "FIT_OUT_CONFIRM_FOS",
    "FIT_OUT_CONFIRM_END",
)

# Bare "ceiling inspection notifications" (the Copilot chip) means both.
_GENERIC_CEILING_PATTERN = re.compile(r"\bceiling\b", re.IGNORECASE)
_CEILING_SUBS = (
    "OPEN_CEILING_INSPECTION",
    "CLOSE_CEILING_INSPECTION",
)

# Bare "work permit notifications" expands to all seven WP subtypes.
_GENERIC_WORK_PERMIT_PATTERN = re.compile(r"\bwork[\s-]?permits?\b", re.IGNORECASE)
_WORK_PERMIT_SUBS = (
    "CONSTRUCTION_HOT",
    "CONSTRUCTION_COLD",
    "CONSTRUCTION_ROOF_ACCESS",
    "MAINTENANCE_HOT",
    "MAINTENANCE_COLD",
    "MAINTENANCE_ROOF_ACCESS",
    "OPERATIONS",
)


def parse_notification_list_filters(message: str) -> NotificationListFilters:
    """Derive inbox filters from a LIST_NOTIFICATIONS message.

    Returns ``sub_categories=None`` (all eligible) when no category keyword is
    present.  "Handover confirmation" wins over plain "handover".
    """
    text = (message or "").strip()
    if not text:
        return NotificationListFilters()

    unread = True if _UNREAD_PATTERN.search(text) else None

    matched: list[str] = []
    for sub, pattern in _SUB_CATEGORY_PATTERNS:
        if pattern.search(text):
            matched.append(sub)
    # Generic "fit-out notifications" (the Copilot chip) with no stage keyword
    # expands to all three confirmation flows.
    if _GENERIC_FITOUT_PATTERN.search(text) and not any(
        sub in matched for sub in _FITOUT_CONFIRM_SUBS
    ):
        matched.extend(_FITOUT_CONFIRM_SUBS)
    # Generic "ceiling inspection notifications" expands to open and close when
    # neither was named.
    if _GENERIC_CEILING_PATTERN.search(text) and not any(
        sub in matched for sub in _CEILING_SUBS
    ):
        matched.extend(_CEILING_SUBS)
    # Generic "work permit notifications" expands to all WP subtypes when no
    # more specific WP keyword already matched.
    if _GENERIC_WORK_PERMIT_PATTERN.search(text) and not any(
        sub in matched for sub in _WORK_PERMIT_SUBS
    ):
        matched.extend(_WORK_PERMIT_SUBS)

    return NotificationListFilters(
        sub_categories=tuple(matched) or None,
        unread=unread,
    )


def resolved_notification_id_from_state(state: dict[str, Any]) -> str | None:
    viewing_notification_id, _viewing_sr = viewing_ids_from_state(state)
    return resolve_notification_id(state.get("user_message") or "", viewing_notification_id)
