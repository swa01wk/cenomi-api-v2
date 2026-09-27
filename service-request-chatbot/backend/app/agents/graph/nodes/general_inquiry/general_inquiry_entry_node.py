"""Entry node for the General Inquiry agent.

Responsibilities
----------------
- Parse confirmation responses (yes/no) when ``confirmation_status == "PENDING"``.
- Handle explicit UI action overrides (confirm_submit, cancel_submit).
- Detect cancel / workflow-switch phrases and clear ``active_agent``.
- Pin ``workflow_stage = "CREATE_SR"`` on first entry.

Non-responsibilities
--------------------
- MUST NOT collect or validate form fields.
- MUST NOT call the LLM.
- MUST NOT submit service requests.
"""

from __future__ import annotations

import logging
from typing import Any

import structlog

from app.agents.graph.state import ServiceRequestState
from app.observability.decorators import trace_node

log = structlog.get_logger(__name__)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Phrase sets
# ---------------------------------------------------------------------------

_CONFIRM_PHRASES: frozenset[str] = frozenset(
    {
        "yes", "yep", "yeah", "yup", "confirm", "confirmed", "submit",
        "proceed", "correct", "looks good", "that's correct", "thats correct",
        "go ahead", "ok", "okay", "sure", "absolutely", "agree",
    }
)

_REJECT_PHRASES: frozenset[str] = frozenset(
    {
        "no", "nope", "nah", "cancel", "change", "update", "edit",
        "modify", "wrong", "incorrect", "fix",
    }
)

_CANCEL_PHRASES: frozenset[str] = frozenset(
    {
        "cancel", "start over", "restart", "new request", "never mind",
        "nevermind", "forget it",
    }
)


@trace_node("general_inquiry_entry", "AGENT")
async def general_inquiry_entry_node(state: ServiceRequestState) -> dict[str, Any]:
    """Boundary between chat and the General Inquiry workflow.

    Sets ``workflow_stage`` to ``CREATE_SR`` on first entry.
    Detects confirmation / rejection / cancel phrases.
    """
    msg = (state.get("user_message") or "").strip().lower()
    updates: dict[str, Any] = {}

    # ── First-entry: set stage ────────────────────────────────────────────
    if not state.get("workflow_stage"):
        updates["workflow_stage"] = "CREATE_SR"

    # ── Action overrides from UI ──────────────────────────────────────────
    action = state.get("action_override") or ""
    if action == "confirm_submit":
        updates["confirmation_status"] = "CONFIRMED"
        return updates
    if action in ("cancel_submit", "cancel_sr", "cancel_create_sr"):
        updates["active_agent"] = None
        updates["workflow_stage"] = None
        updates["confirmation_status"] = None
        updates["status"] = "CANCELLED"
        updates["response_message"] = "The inquiry has been cancelled. How can I help you?"
        return updates

    # ── Confirmation gate ─────────────────────────────────────────────────
    confirmation_status = state.get("confirmation_status")
    if confirmation_status == "PENDING":
        if msg in _CONFIRM_PHRASES:
            updates["confirmation_status"] = "CONFIRMED"
            return updates
        if msg in _REJECT_PHRASES or any(p in msg for p in _REJECT_PHRASES):
            updates["confirmation_status"] = "REJECTED"
            return updates

    # ── Cancel / restart detection ────────────────────────────────────────
    if msg in _CANCEL_PHRASES or any(p in msg for p in _CANCEL_PHRASES):
        updates["active_agent"] = None
        updates["workflow_stage"] = None
        updates["confirmation_status"] = None
        updates["status"] = "CANCELLED"
        updates["response_message"] = "The inquiry has been cancelled. How can I help you?"
        return updates

    return updates
