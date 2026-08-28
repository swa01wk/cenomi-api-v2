"""Page-aware viewing context — resolve deictic references without repeating IDs.

The UI repo will later send ``viewing_notification_id`` / ``viewing_service_request_id``
on each chat turn when the user is on a notification or SR detail page.  This
module is the backend contract those fields plug into.

Rules (same as the mosaic-poc):
- An explicit ID in the message always wins over page viewing context.
- Viewing context is used only for deictic phrases ("this notification",
  "what's the status?", "explain this").
- Unrelated messages do not inherit the viewed entity.
"""

from __future__ import annotations

import re
from typing import Any

from app.agents.services.sr_reference_parser import parse_sr_reference

_NOTIFICATION_ID_PATTERN = re.compile(
    r"\b(?:NTF|NOTIF(?:ICATION)?)-?(\d+)\b",
    re.IGNORECASE,
)
_NOTIFICATION_UUID_PATTERN = re.compile(
    r"\b([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\b",
    re.IGNORECASE,
)

_VIEWING_NOTIFICATION_PATTERN = re.compile(
    r"\b(this|the|current)\b.*\b(notification|alert|inbox\s+item)\b"
    r"|\b(explain|describe|summarize|tell me about)\b.*\b(notification|alert)\b"
    r"|\b(what('s| is)|how('s| is))\b.*\b(notification|alert)\b"
    r"|\b(status|details|summary)\b.*\b(of|for)\b.*\b(this|the|current)\b.*\b(notification|alert)\b",
    re.IGNORECASE,
)

_VIEWING_SR_PATTERN = re.compile(
    r"\b(this|the|current)\b.*\b(sr|service\s+request|request|handover)\b"
    r"|\b(explain|describe|summarize|tell me about)\b.*\b(sr|service\s+request|handover)\b"
    r"|\b(what('s| is)|how('s| is))\b.*\b(status|priority|description|details)\b"
    r"|\b(status|details|summary)\b.*\b(of|for)\b.*\b(this|the|current)\b",
    re.IGNORECASE,
)

_VIEWING_NOTIFICATION_PHRASES = (
    "what's this about",
    "what is this about",
    "tell me more",
    "explain this",
    "describe this",
    "what is this",
    "what's this",
)

_VIEWING_SR_PHRASES = (
    "what's the status",
    "what is the status",
    "any updates",
    "tell me more",
)

_GENERIC_EXPLAIN_PATTERN = re.compile(
    r"^\s*(explain|describe|summarize)\s+(this|it|that)\s*\.?\s*$",
    re.IGNORECASE,
)

_GENERIC_STATUS_PATTERN = re.compile(
    r"^\s*(what('s| is)\s+the\s+status|any\s+updates)\s*\??\s*$",
    re.IGNORECASE,
)


def normalize_notification_id(raw_id: str) -> str:
    """Canonicalise ``NTF-12`` / ``ntf000012`` → ``NTF-000012``; leave UUIDs as-is."""
    text = raw_id.strip()
    uuid_match = _NOTIFICATION_UUID_PATTERN.fullmatch(text)
    if uuid_match:
        return uuid_match.group(1).lower()
    match = _NOTIFICATION_ID_PATTERN.search(text)
    if not match:
        return text
    return f"NTF-{int(match.group(1)):06d}"


def extract_notification_id(message: str) -> str | None:
    match = _NOTIFICATION_ID_PATTERN.search(message or "")
    if match:
        return f"NTF-{int(match.group(1)):06d}"
    uuid_match = _NOTIFICATION_UUID_PATTERN.search(message or "")
    if uuid_match:
        return uuid_match.group(1).lower()
    return None


def references_viewing_notification(message: str) -> bool:
    """True when the user is referring to the notification they are viewing."""
    text = (message or "").strip()
    if not text:
        return False
    if _VIEWING_NOTIFICATION_PATTERN.search(text):
        return True
    if _GENERIC_EXPLAIN_PATTERN.search(text):
        return True
    lower = text.lower()
    return any(phrase in lower for phrase in _VIEWING_NOTIFICATION_PHRASES)


def references_viewing_service_request(message: str) -> bool:
    """True when the user is referring to the SR they are currently viewing."""
    text = (message or "").strip()
    if not text:
        return False
    if _VIEWING_SR_PATTERN.search(text):
        return True
    if _GENERIC_STATUS_PATTERN.search(text):
        return True
    lower = text.lower()
    return any(phrase in lower for phrase in _VIEWING_SR_PHRASES)


def resolve_notification_id(
    message: str,
    viewing_notification_id: str | None = None,
) -> str | None:
    """Resolve a notification ID from explicit mention or page viewing context."""
    explicit = extract_notification_id(message)
    if explicit:
        return explicit
    if viewing_notification_id and (
        not message.strip()
        or references_viewing_notification(message)
        or "this" in message.lower()
    ):
        return normalize_notification_id(viewing_notification_id)
    return None


def resolve_service_request_id(
    message: str,
    viewing_service_request_id: str | None = None,
    *,
    allow_standalone: bool = False,
) -> str | None:
    """Resolve an SR ID from explicit mention or page viewing context."""
    explicit = parse_sr_reference(message, allow_standalone=allow_standalone)
    if explicit:
        return explicit
    if viewing_service_request_id and references_viewing_service_request(message):
        return str(viewing_service_request_id).strip()
    return None


def viewing_ids_from_state(state: dict[str, Any]) -> tuple[str | None, str | None]:
    """Return ``(viewing_notification_id, viewing_service_request_id)`` from graph state."""
    msp = state.get("msp_context") if isinstance(state.get("msp_context"), dict) else {}
    snapshot = state.get("viewing_notification")
    if not isinstance(snapshot, dict):
        snapshot = msp.get("viewing_notification") if isinstance(msp, dict) else None
    if not isinstance(snapshot, dict):
        snapshot = {}

    notification_id = (
        state.get("viewing_notification_id")
        or msp.get("viewing_notification_id")
        or snapshot.get("id")
        or snapshot.get("notification_id")
    )
    service_request_id = (
        state.get("viewing_service_request_id")
        or msp.get("viewing_service_request_id")
        or snapshot.get("related_sr_id")
        or snapshot.get("sr_id")
    )
    if notification_id:
        notification_id = str(notification_id).strip() or None
    if service_request_id:
        service_request_id = str(service_request_id).strip() or None
    return notification_id, service_request_id
