"""Entry node for the Work Permit SR workflow (create + review hops).

Responsibilities
----------------
- Parse confirmation responses (yes/no) when confirmation_status is PENDING.
- Handle explicit UI action overrides (confirm, approve, reject, cancel).
- Detect cancel/restart phrases and clear workflow state.
- Pin CREATE_WORK_PERMIT on first create turn; review stages come from status sync.

Non-responsibilities
--------------------
- Does not collect or validate form fields.
- Does not call the LLM.
- Does not submit service requests.
"""

from __future__ import annotations

import re
from typing import Any

import structlog

from app.agents.graph.state import ServiceRequestState
from app.agents.schemas.work_permit_schema import WP_REVIEW_STAGES, WP_TERMINAL_STAGES
from app.observability.decorators import trace_node

log = structlog.get_logger(__name__)

_CONFIRM_PHRASES: frozenset[str] = frozenset(
    {
        "yes", "yep", "yeah", "yup", "confirm", "confirmed", "submit",
        "proceed", "correct", "looks good", "that's correct", "thats correct",
        "go ahead", "ok", "okay", "sure", "absolutely", "agree", "approve",
    }
)

_REJECT_PHRASES: frozenset[str] = frozenset(
    {
        "no", "nope", "nah", "cancel", "change", "update", "edit",
        "modify", "wrong", "incorrect", "fix",
    }
)

_SR_REJECT_PHRASES: frozenset[str] = frozenset(
    {
        "reject", "decline", "refuse", "not approved", "do not approve",
        "don't approve", "dont approve",
    }
)

_CANCEL_WORKFLOW_PHRASES: frozenset[str] = frozenset(
    {"start over", "restart", "new request", "different request", "begin again", "reset"}
)

_APPROVE_ACTIONS: frozenset[str] = frozenset(
    {
        "confirm",
        "confirm_create_wp",
        "confirm_approve_wp",
        "approve",
        "approve_wp",
    }
)
_REJECT_SR_ACTIONS: frozenset[str] = frozenset({"reject_wp", "reject"})


def _contains_phrase(message: str, phrases: frozenset[str]) -> bool:
    lowered = message.lower()
    return any(bool(re.search(r"\b" + re.escape(p) + r"\b", lowered)) for p in phrases)


def _set_wp_action(state: ServiceRequestState, action: str) -> dict[str, Any]:
    backend_refs = dict(state.get("backend_refs") or {})
    backend_refs["wp_action"] = action
    return {
        "backend_refs": backend_refs,
        "confirmation_status": "CONFIRMED",
        "status": "IN_PROGRESS",
    }


@trace_node("work_permit_entry", "AGENT")
async def work_permit_entry_node(state: ServiceRequestState) -> dict[str, Any]:
    """Parse confirmation / cancel / approve / reject at the WP workflow boundary."""
    user_message: str = state.get("user_message") or ""
    confirmation_status: str | None = state.get("confirmation_status")
    action_override: str | None = state.get("action_override")  # type: ignore[assignment]
    workflow_stage: str = state.get("workflow_stage") or ""
    is_review = workflow_stage in WP_REVIEW_STAGES

    # ── 0. Explicit UI action ──────────────────────────────────────────────
    if action_override in _APPROVE_ACTIONS:
        log.info("work_permit_entry.action_override_confirm", review=is_review)
        if is_review:
            return _set_wp_action(state, "approve")
        return {"confirmation_status": "CONFIRMED", "status": "IN_PROGRESS"}

    if action_override in _REJECT_SR_ACTIONS and is_review:
        log.info("work_permit_entry.action_override_reject_sr")
        return _set_wp_action(state, "reject")

    if action_override in ("cancel", "cancel_create_wp"):
        log.info("work_permit_entry.action_override_cancel")
        return {
            "confirmation_status": "REJECTED",
            "status": "IN_PROGRESS",
            "response_message": "No problem — what would you like to change?",
        }

    # ── 1. Workflow cancel / restart ───────────────────────────────────────
    if _contains_phrase(user_message, _CANCEL_WORKFLOW_PHRASES):
        log.info("work_permit_entry.workflow_cancel")
        return {
            "active_agent": None,
            "intent": None,
            "service_category": None,
            "sub_category": None,
            "workflow_stage": None,
            "confirmation_status": None,
            "confirmation_required": False,
            "collected_data": {},
            "extracted_fields": {},
            "missing_fields": [],
            "validation_errors": [],
            "selected_lease": None,
            "lease_matches": [],
            "backend_refs": {},
            "response_ui": {},
            "status": "WAITING_FOR_USER",
            "response_message": "Sure — I've cleared everything. What would you like to do next?",
        }

    # ── 2. Confirmation response parsing ──────────────────────────────────
    if confirmation_status == "PENDING":
        if is_review and _contains_phrase(user_message, _SR_REJECT_PHRASES):
            log.info("work_permit_entry.review_rejected")
            return _set_wp_action(state, "reject")

        if _contains_phrase(user_message, _CONFIRM_PHRASES):
            log.info("work_permit_entry.confirmation_accepted", review=is_review)
            if is_review:
                return _set_wp_action(state, "approve")
            return {"confirmation_status": "CONFIRMED", "status": "IN_PROGRESS"}

        if _contains_phrase(user_message, _REJECT_PHRASES):
            log.info("work_permit_entry.confirmation_rejected")
            return {
                "confirmation_status": "REJECTED",
                "status": "IN_PROGRESS",
                "response_message": "No problem — what would you like to change?",
            }

        return {
            "status": "WAITING_FOR_USER",
            "response_message": (
                "Please reply with 'yes' to confirm and submit, "
                "or tell me what you'd like to change."
                + (" To reject this hop, reply 'reject'." if is_review else "")
            ),
        }

    # Clear a previous terminal so a new CREATE can start in-session.
    if (
        state.get("intent") == "CREATE_WORK_PERMIT_SR"
        and workflow_stage in WP_TERMINAL_STAGES
    ):
        log.info("work_permit_entry.stale_sr_cleared", workflow_stage=workflow_stage)
        return {
            "workflow_stage": "CREATE_WORK_PERMIT",
            "confirmation_status": None,
            "confirmation_required": False,
            "collected_data": {},
            "extracted_fields": {},
            "missing_fields": [],
            "validation_errors": [],
            "selected_lease": None,
            "lease_matches": [],
            "backend_refs": {},
            "response_ui": {},
            "status": "IN_PROGRESS",
        }

    if state.get("intent") == "APPROVE_WORK_PERMIT_SR" and not (
        state.get("backend_refs") or {}
    ).get("sr_id"):
        return {
            "status": "WAITING_FOR_USER",
            "response_message": (
                "Open an existing Work Permit from your inbox to review and approve it."
            ),
        }

    # First create turn — initialize workflow stage.
    if not workflow_stage:
        return {"workflow_stage": "CREATE_WORK_PERMIT", "status": "IN_PROGRESS"}

    return {"status": "IN_PROGRESS"}
