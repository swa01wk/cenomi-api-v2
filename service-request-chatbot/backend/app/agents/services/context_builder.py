"""Compact LLM context — never dump full conversation or event history.

Mirrors the mosaic-poc context-awareness contract: every LLM call receives a
small dict of *authoritative* session state, the last few turns, and optional
page viewing context.  Full chat logs and workflow event streams stay out.
"""

from __future__ import annotations

from typing import Any

from app.agents.services.viewing_context import viewing_ids_from_state

MAX_RECENT_MESSAGES = 5

_AUTHORITATIVE_FIELD_KEYS = (
    "lease_code",
    "brand",
    "mall",
    "title",
    "description",
    "priority",
)


def build_agent_context(
    state: dict[str, Any],
    *,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a compact context dict for supervisor / FAQ / notification LLM calls.

    Keys
    ----
    authoritative_state:
        Currently accepted draft fields plus ``sr_id`` when known.
    recent_messages:
        At most ``MAX_RECENT_MESSAGES`` prior turns (oldest first).
    missing_fields / workflow_stage:
        Deterministic workflow hints so the model does not re-ask filled fields.
    viewing_notification_id / viewing_service_request_id / viewing_notification:
        Page-aware context injected by the API layer (UI will send these later).
    """
    collected = dict(state.get("collected_data") or {})
    backend_refs = dict(state.get("backend_refs") or {})
    accepted = {
        key: collected.get(key)
        for key in _AUTHORITATIVE_FIELD_KEYS
        if collected.get(key) not in (None, "", [])
    }
    if backend_refs.get("sr_id"):
        accepted["sr_id"] = backend_refs["sr_id"]

    viewing_notification_id, viewing_service_request_id = viewing_ids_from_state(state)
    snapshot = _viewing_notification_snapshot(state)

    context: dict[str, Any] = {
        "authoritative_state": accepted,
        "recent_messages": _recent_messages(state.get("conversation_history") or []),
        "missing_fields": list(state.get("missing_fields") or []),
        "workflow_stage": state.get("workflow_stage"),
        "user_message": state.get("user_message") or "",
    }
    if viewing_notification_id:
        context["viewing_notification_id"] = viewing_notification_id
    if viewing_service_request_id:
        context["viewing_service_request_id"] = viewing_service_request_id
    if snapshot:
        context["viewing_notification"] = snapshot
    if extra:
        context.update(extra)
    return context


def format_context_for_prompt(context: dict[str, Any]) -> str:
    """Render compact context as prompt lines (no JSON dump of the whole state)."""
    lines: list[str] = []
    stage = context.get("workflow_stage")
    if stage:
        lines.append(f"Workflow stage: {stage}")

    accepted = context.get("authoritative_state") or {}
    if accepted:
        parts = [f"{key}={value}" for key, value in accepted.items()]
        lines.append("Authoritative state: " + ", ".join(parts))

    missing = context.get("missing_fields") or []
    if missing:
        lines.append("Missing fields: " + ", ".join(str(f) for f in missing))

    if context.get("viewing_notification_id"):
        lines.append(f"Viewing notification ID: {context['viewing_notification_id']}")
    if context.get("viewing_service_request_id"):
        lines.append(f"Viewing service request ID: {context['viewing_service_request_id']}")

    snapshot = context.get("viewing_notification") or {}
    if snapshot:
        title = snapshot.get("title") or snapshot.get("subject")
        body = snapshot.get("body") or snapshot.get("message") or snapshot.get("description")
        related = snapshot.get("related_sr_id") or snapshot.get("sr_id")
        lines.append("Viewing notification snapshot:")
        if title:
            lines.append(f"  title: {title}")
        if body:
            lines.append(f"  body: {body}")
        if related:
            lines.append(f"  related_sr_id: {related}")
        status = snapshot.get("status") or snapshot.get("read")
        if status is not None:
            lines.append(f"  status: {status}")

    recent = context.get("recent_messages") or []
    if recent:
        lines.append("Recent conversation (oldest first):")
        for turn in recent:
            role = (turn.get("role") or "unknown").capitalize()
            content = (turn.get("content") or "").strip()
            if content:
                lines.append(f"  {role}: {content}")

    user_message = context.get("user_message") or ""
    if user_message:
        lines.append(f"User message: {user_message}")

    return "\n".join(lines)


def _recent_messages(history: list[dict[str, Any]]) -> list[dict[str, str]]:
    trimmed: list[dict[str, str]] = []
    for turn in history[-MAX_RECENT_MESSAGES:]:
        role = str(turn.get("role") or "unknown")
        content = str(turn.get("content") or "").strip()
        if content:
            trimmed.append({"role": role, "content": content})
    return trimmed


def _viewing_notification_snapshot(state: dict[str, Any]) -> dict[str, Any] | None:
    snap = state.get("viewing_notification")
    if isinstance(snap, dict) and snap:
        return dict(snap)
    msp = state.get("msp_context") or {}
    nested = msp.get("viewing_notification") if isinstance(msp, dict) else None
    if isinstance(nested, dict) and nested:
        return dict(nested)
    return None
