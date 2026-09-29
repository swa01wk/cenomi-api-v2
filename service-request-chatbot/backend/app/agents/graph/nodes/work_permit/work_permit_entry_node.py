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
from app.agents.schemas.work_permit_schema import (
    STAGE_LABELS,
    WP_REVIEW_STAGES,
    WP_TERMINAL_STAGES,
    in_progress_roles,
)
from app.agents.services.permission_service import PermissionService
from app.agents.services.sr_release import released_backend_refs
from app.observability.decorators import trace_node
from app.types.chat import AuthContext

log = structlog.get_logger(__name__)

_permission_service = PermissionService()

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

# Chip actions that write directly into collected_data (skip field_extraction).
_WP_TYPE_ACTIONS: dict[str, str] = {
    "select_wp_type_construction_hot": "CONSTRUCTION_HOT",
    "select_wp_type_construction_cold": "CONSTRUCTION_COLD",
    "select_wp_type_construction_roof_access": "CONSTRUCTION_ROOF_ACCESS",
    "select_wp_type_maintenance_hot": "MAINTENANCE_HOT",
    "select_wp_type_maintenance_cold": "MAINTENANCE_COLD",
    "select_wp_type_maintenance_roof_access": "MAINTENANCE_ROOF_ACCESS",
    "select_wp_type_operations": "OPERATIONS",
}
_WP_DEPARTMENT_ACTIONS: dict[str, str] = {
    "select_wp_department_leasing": "leasing",
    "select_wp_department_finance": "finance",
    "select_wp_department_operations": "operations",
}
WP_DIRECT_SET_CHIP_ACTIONS: frozenset[str] = frozenset(
    set(_WP_TYPE_ACTIONS) | set(_WP_DEPARTMENT_ACTIONS)
)


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


def _deny_review_action(state: ServiceRequestState, workflow_stage: str) -> dict[str, Any] | None:
    """If caller cannot act on this hop, return a denial patch; else None.

    No auth_context → allow (unit tests / shadow), matching confirmation + PATCH.
    """
    auth: AuthContext | None = state.get("auth_context")  # type: ignore[assignment]
    if auth is None:
        return None
    backend_refs: dict[str, Any] = dict(state.get("backend_refs") or {})
    if _permission_service.can_approve_work_permit(
        auth,
        workflow_stage=workflow_stage,
        in_progress_roles=in_progress_roles(backend_refs.get("sr_operations")),
    ):
        return None
    label = STAGE_LABELS.get(workflow_stage, "this review hop")
    log.warning(
        "work_permit_entry.role_denied",
        workflow_stage=workflow_stage,
        roles=sorted(auth.roles),
    )
    return {
        "status": "WAITING_FOR_USER",
        "confirmation_status": None,
        "confirmation_required": False,
        "response_message": (
            f"You do not have permission to approve or reject this Work Permit hop "
            f"({label}). This stage is assigned to another reviewer."
        ),
        "response_ui": {"type": "message"},
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
    if action_override in _WP_TYPE_ACTIONS:
        value = _WP_TYPE_ACTIONS[action_override]
        log.info("work_permit_entry.action_select_wp_type", value=value)
        collected = dict(state.get("collected_data") or {})
        collected["work_permit_type"] = value
        collected["_wp_type_confirmed"] = True
        return {
            "collected_data": collected,
            "sub_category": value,
            "status": "IN_PROGRESS",
        }

    if action_override in _WP_DEPARTMENT_ACTIONS:
        value = _WP_DEPARTMENT_ACTIONS[action_override]
        log.info("work_permit_entry.action_select_wp_department", value=value)
        collected = dict(state.get("collected_data") or {})
        collected["department"] = value
        return {"collected_data": collected, "status": "IN_PROGRESS"}

    if action_override in _APPROVE_ACTIONS:
        log.info("work_permit_entry.action_override_confirm", review=is_review)
        if is_review:
            denied = _deny_review_action(state, workflow_stage)
            if denied is not None:
                return denied
            return _set_wp_action(state, "approve")
        return {"confirmation_status": "CONFIRMED", "status": "IN_PROGRESS"}

    if action_override in _REJECT_SR_ACTIONS and is_review:
        log.info("work_permit_entry.action_override_reject_sr")
        denied = _deny_review_action(state, workflow_stage)
        if denied is not None:
            return denied
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
            denied = _deny_review_action(state, workflow_stage)
            if denied is not None:
                return denied
            return _set_wp_action(state, "reject")

        if _contains_phrase(user_message, _CONFIRM_PHRASES):
            log.info("work_permit_entry.confirmation_accepted", review=is_review)
            if is_review:
                denied = _deny_review_action(state, workflow_stage)
                if denied is not None:
                    return denied
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
            "backend_refs": released_backend_refs(state),
            "documents": [],
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
