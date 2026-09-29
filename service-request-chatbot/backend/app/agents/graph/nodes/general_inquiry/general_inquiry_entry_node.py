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

from app.agents.graph.nodes.general_inquiry.general_inquiry_confirmation_node import (
    handle_gi_optional_upload,
)
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

_REVIEW_ACTIONS: dict[str, str] = {
    "confirm_approve_gi": "approve",
    "reject_gi": "reject",
    "ask_more_info_gi": "ask_more_info",
    "reassign_gi": "reassign",
}

_REASSIGN_ROLES: tuple[tuple[str, str], ...] = (
    ("Mall Manager", "MALL_MANAGER"),
    ("Operations", "OPERATIONS"),
    ("FM Manager", "FM_MANAGER"),
    ("Leasing", "LEASING"),
    ("Legal", "LEGAL"),
)


def _pending_action(state: ServiceRequestState) -> str:
    """The review step waiting on a comment.

    ``backend_refs`` is rebuilt from the draft as ``sr_id`` only, so a pending
    reject stored there disappears on the next turn and the comment is treated
    as a new message. The draft's ``collected_data`` is what actually persists.
    """
    refs = state.get("backend_refs") or {}
    collected = state.get("collected_data") or {}
    return str(refs.get("gi_pending_action") or collected.get("gi_pending_action") or "")


def _review_ready(state: ServiceRequestState, gi_action: str, comment: str) -> dict[str, Any]:
    backend_refs = dict(state.get("backend_refs") or {})
    collected = dict(state.get("collected_data") or {})
    backend_refs["gi_action"] = gi_action
    backend_refs.pop("gi_pending_action", None)
    collected["gi_action"] = gi_action
    collected.pop("gi_pending_action", None)
    if comment:
        collected["review_comment"] = comment
    if gi_action == "reassign" and comment:
        collected["assign_to"] = comment
        collected["review_comment"] = ""
    return {
        "backend_refs": backend_refs,
        "collected_data": collected,
        "confirmation_status": "CONFIRMED",
        "status": "IN_PROGRESS",
        "workflow_stage": "GI_CRT_REVIEW",
        "active_agent": "general_inquiry_agent",
        "active_field": None,
    }


def _ask(state: ServiceRequestState, pending: str, message: str, suggestions: dict[str, Any] | None = None) -> dict[str, Any]:
    backend_refs = dict(state.get("backend_refs") or {})
    collected = dict(state.get("collected_data") or {})
    backend_refs["gi_pending_action"] = pending
    collected["gi_pending_action"] = pending
    ui: dict[str, Any] = {"type": "message", "verbatim": True, "message": message}
    if suggestions:
        ui["suggestions"] = suggestions
    return {
        "backend_refs": backend_refs,
        "collected_data": collected,
        "confirmation_status": None,
        "status": "WAITING_FOR_USER",
        "workflow_stage": state.get("workflow_stage") or "GI_CRT_REVIEW",
        "response_message": message,
        "response_ui": ui,
        # Free-text guard: the comment must not be reclassified as a new request.
        "active_field": "review_comment",
    }


@trace_node("general_inquiry_entry", "AGENT")
async def general_inquiry_entry_node(state: ServiceRequestState) -> dict[str, Any]:
    """Boundary between chat and the General Inquiry workflow.

    Sets ``workflow_stage`` to ``CREATE_SR`` on first entry.
    Detects confirmation / rejection / cancel phrases.
    """
    raw_message = (state.get("user_message") or "").strip()
    msg = raw_message.lower()
    updates: dict[str, Any] = {}
    backend_refs: dict[str, Any] = dict(state.get("backend_refs") or {})
    stage = state.get("workflow_stage") or ""

    # ── Review actions (Customer Relations Team) ──────────────────────────
    action = state.get("action_override") or ""
    pending = _pending_action(state)
    if stage == "GI_CRT_REVIEW" or action in _REVIEW_ACTIONS or pending:
        if pending and raw_message and action not in _REVIEW_ACTIONS:
            return _review_ready(state, pending, raw_message)
        if action == "confirm_approve_gi":
            return _review_ready(state, "approve", "")
        if action == "reject_gi":
            return _ask(
                state,
                "reject",
                "Please share a comment for rejecting this inquiry.",
            )
        if action == "ask_more_info_gi":
            return _ask(
                state,
                "ask_more_info",
                "What information should I request from the requester?",
            )
        if action == "reassign_gi":
            return _ask(
                state,
                "reassign",
                "Which role should this inquiry be reassigned to?",
                {
                    "kind": "labeled_options",
                    "options": [
                        {"label": label, "value": role}
                        for label, role in _REASSIGN_ROLES
                    ],
                },
            )
        if pending:
            return {}

    # Optional document. Upload shows the card; Skip continues to confirmation.
    upload_gate = handle_gi_optional_upload(
        state,
        action=action,
        user_message=raw_message,
    )
    if upload_gate is not None:
        return upload_gate

    # ── First-entry: set stage ────────────────────────────────────────────
    if not state.get("workflow_stage"):
        updates["workflow_stage"] = "CREATE_SR"

    # ── Action overrides from UI ──────────────────────────────────────────
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
