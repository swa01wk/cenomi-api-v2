"""Entry node for the Work Permit SR workflow (create + review hops).

Responsibilities
----------------
- Parse confirmation responses (yes/no) when confirmation_status is PENDING.
- Handle explicit UI action overrides (confirm, approve, reject, cancel,
  ask-for-more-info, assign-to, extend, cancel-permit).
- Collect required inputs (comment / role / duration) before confirming.
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
from app.agents.services.action_resolver import (
    WP_ACTION_APPROVE,
    WP_ACTION_ASSIGN,
    WP_ACTION_CANCEL_PERMIT,
    WP_ACTION_EXTEND,
    WP_ACTION_MORE_INFO,
    WP_ACTION_REJECT,
    ServiceRequestContext,
    resolve_actions,
    wp_assign_to_options,
)
from app.agents.services.permission_service import PermissionService
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
        WP_ACTION_APPROVE,
        "approve",
        "approve_wp",
    }
)
_REJECT_SR_ACTIONS: frozenset[str] = frozenset({WP_ACTION_REJECT, "reject"})
_MORE_INFO_ACTIONS: frozenset[str] = frozenset({WP_ACTION_MORE_INFO, "ask_more_info"})
_ASSIGN_ACTIONS: frozenset[str] = frozenset({WP_ACTION_ASSIGN, "assign_to"})
_EXTEND_ACTIONS: frozenset[str] = frozenset({WP_ACTION_EXTEND, "extend"})
_CANCEL_PERMIT_ACTIONS: frozenset[str] = frozenset(
    {WP_ACTION_CANCEL_PERMIT, "cancel_permit"}
)

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

_DURATION_RE = re.compile(
    r"(?P<n>\d+)\s*(?P<u>hour|hours|hr|hrs|day|days)\b",
    re.IGNORECASE,
)


def _contains_phrase(message: str, phrases: frozenset[str]) -> bool:
    lowered = message.lower()
    return any(bool(re.search(r"\b" + re.escape(p) + r"\b", lowered)) for p in phrases)


def _deny_review_action(state: ServiceRequestState, workflow_stage: str) -> dict[str, Any] | None:
    """If caller cannot act on this hop, return a denial patch; else None."""
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
            f"You do not have permission to act on this Work Permit hop "
            f"({label}). This stage is assigned to another reviewer."
        ),
        "response_ui": {"type": "message"},
    }


def _legal_action_ids(state: ServiceRequestState, workflow_stage: str) -> frozenset[str]:
    auth: AuthContext | None = state.get("auth_context")  # type: ignore[assignment]
    backend_refs: dict[str, Any] = dict(state.get("backend_refs") or {})
    user_roles: frozenset[str] = frozenset()
    is_admin = False
    if auth is not None:
        user_roles = frozenset(str(r).upper() for r in auth.roles)
        is_admin = bool(auth.is_global_admin) or "ADMIN" in user_roles
    history_raw = backend_refs.get("sr_history") or []
    history = tuple(h for h in history_raw if isinstance(h, dict))
    assign_raw = backend_refs.get("assign_to_options") or []
    assign_opts = tuple(
        {"label": str(o.get("label") or ""), "value": str(o.get("value") or "")}
        for o in assign_raw
        if isinstance(o, dict) and str(o.get("value") or "").strip()
    )
    ext = backend_refs.get("work_permit_extension")
    ctx = ServiceRequestContext(
        sr_type="WORK_PERMIT",
        subtype=str(state.get("sub_category") or "") or None,
        workflow_stage=workflow_stage,
        assigned_roles=frozenset(in_progress_roles(backend_refs.get("sr_operations"))),
        user_roles=user_roles,
        platform_status=str(backend_refs.get("platform_sr_status") or "") or None,
        request_history=history,
        work_permit_extension=ext if isinstance(ext, dict) else None,
        assign_to_options=assign_opts,
        is_actor=True,
        is_admin=is_admin,
    )
    return frozenset(a.id for a in resolve_actions(ctx))


def _normalize_review_action(action_override: str | None) -> str | None:
    if not action_override:
        return None
    if action_override in _APPROVE_ACTIONS and action_override != "confirm_create_wp":
        return "approve"
    if action_override in _REJECT_SR_ACTIONS:
        return "reject"
    if action_override in _MORE_INFO_ACTIONS:
        return "action_required"
    if action_override in _ASSIGN_ACTIONS:
        return "assign_to"
    if action_override in _EXTEND_ACTIONS:
        return "extend"
    if action_override in _CANCEL_PERMIT_ACTIONS:
        return "cancel_permit"
    return None


def _comment_from_state(state: ServiceRequestState, user_message: str) -> str:
    collected = state.get("collected_data") or {}
    for key in ("comments", "notes", "comment"):
        val = str(collected.get(key) or "").strip()
        if val:
            return val
    return (user_message or "").strip()


def _parse_duration(text: str) -> dict[str, int] | None:
    match = _DURATION_RE.search(text or "")
    if not match:
        # bare number → hours (form default unit for most subtypes)
        bare = re.fullmatch(r"\s*(\d+)\s*", text or "")
        if bare:
            return {"extension_duration_in_hours": int(bare.group(1))}
        return None
    n = int(match.group("n"))
    unit = match.group("u").lower()
    if unit.startswith("d"):
        return {"extension_duration_in_days": n}
    return {"extension_duration_in_hours": n}


def _ask_for_input(
    state: ServiceRequestState,
    *,
    pending: str,
    message: str,
    suggestions: dict[str, Any] | None = None,
) -> dict[str, Any]:
    backend_refs = dict(state.get("backend_refs") or {})
    backend_refs["wp_pending_action"] = pending
    ui: dict[str, Any] = {"type": "message", "message": message}
    if suggestions:
        ui["suggestions"] = suggestions
    return {
        "backend_refs": backend_refs,
        "confirmation_status": "PENDING",
        "confirmation_required": True,
        "status": "WAITING_FOR_USER",
        "response_message": message,
        "response_ui": ui,
        "response_decorations": {"suggestions": suggestions} if suggestions else {},
    }


def _begin_or_complete_review_action(
    state: ServiceRequestState,
    *,
    workflow_stage: str,
    action_key: str,
    user_message: str = "",
) -> dict[str, Any]:
    """Validate permission, collect required inputs, then set CONFIRMED."""
    denied = _deny_review_action(state, workflow_stage)
    if denied is not None:
        return denied

    legal = _legal_action_ids(state, workflow_stage)
    id_for_legal = {
        "approve": WP_ACTION_APPROVE,
        "reject": WP_ACTION_REJECT,
        "action_required": WP_ACTION_MORE_INFO,
        "assign_to": WP_ACTION_ASSIGN,
        "extend": WP_ACTION_EXTEND,
        "cancel_permit": WP_ACTION_CANCEL_PERMIT,
    }.get(action_key)
    # Approve/reject always legal for an actor; extend bar swaps the menu.
    if id_for_legal and id_for_legal not in legal and action_key not in {
        "approve",
        "reject",
    }:
        # Still allow approve/reject aliases when extension bar is showing? No —
        # form replaces the bar. Reject stale clicks.
        if legal and id_for_legal not in legal:
            log.warning(
                "work_permit_entry.stale_action",
                action=action_key,
                legal=sorted(legal),
            )
            return {
                "status": "WAITING_FOR_USER",
                "confirmation_status": "PENDING",
                "response_message": (
                    "That action is not available on this hop right now. "
                    "Please choose one of the options shown."
                ),
                "response_ui": {"type": "message"},
            }

    collected = dict(state.get("collected_data") or {})
    backend_refs = dict(state.get("backend_refs") or {})
    comment = _comment_from_state(state, user_message)

    if action_key in {"reject", "action_required"}:
        if not comment:
            prompt = (
                "I'll send that back. What should I tell them?"
                if action_key == "action_required"
                else "I'll decline this permit. What reason should I include?"
            )
            return _ask_for_input(state, pending=action_key, message=prompt)
        collected["comments"] = comment
        backend_refs["wp_action"] = action_key
        backend_refs.pop("wp_pending_action", None)
        return {
            "collected_data": collected,
            "backend_refs": backend_refs,
            "confirmation_status": "CONFIRMED",
            "status": "IN_PROGRESS",
        }

    if action_key == "assign_to":
        role = str(
            backend_refs.get("wp_assign_to")
            or collected.get("assign_to")
            or ""
        ).strip()
        # Message may be the role value from a chip.
        if not role and user_message.strip():
            candidate = user_message.strip()
            opts = wp_assign_to_options(
                ServiceRequestContext(
                    sr_type="WORK_PERMIT",
                    subtype=None,
                    workflow_stage=workflow_stage,
                    assigned_roles=frozenset(),
                    user_roles=frozenset(),
                    assign_to_options=tuple(
                        o
                        for o in (backend_refs.get("assign_to_options") or [])
                        if isinstance(o, dict)
                    ),
                )
            )
            for opt in opts:
                if candidate.upper() == opt["value"].upper() or candidate.lower() == opt[
                    "label"
                ].lower():
                    role = opt["value"]
                    break
            if not role and candidate.isupper() and "_" in candidate:
                role = candidate
        if not role:
            opts = wp_assign_to_options(
                ServiceRequestContext(
                    sr_type="WORK_PERMIT",
                    subtype=None,
                    workflow_stage=workflow_stage,
                    assigned_roles=frozenset(),
                    user_roles=frozenset(),
                )
            )
            return _ask_for_input(
                state,
                pending="assign_to",
                message="Who should take this?",
                suggestions={
                    "kind": "labeled_options",
                    "options": [
                        {"label": o["label"], "value": o["value"]} for o in opts
                    ],
                },
            )
        backend_refs["wp_action"] = "assign_to"
        backend_refs["wp_assign_to"] = role
        backend_refs.pop("wp_pending_action", None)
        if comment:
            collected["comments"] = comment
        return {
            "collected_data": collected,
            "backend_refs": backend_refs,
            "confirmation_status": "CONFIRMED",
            "status": "IN_PROGRESS",
        }

    if action_key == "extend":
        duration = backend_refs.get("wp_extension_duration")
        if not isinstance(duration, dict):
            duration = _parse_duration(user_message)
        if not duration:
            return _ask_for_input(
                state,
                pending="extend",
                message=(
                    "They've asked to extend it. How long should I approve — "
                    "for example 2 hours or 1 day?"
                ),
                suggestions={
                    "kind": "labeled_options",
                    "options": [
                        {"label": "1 Hour", "value": "1 hour"},
                        {"label": "2 Hours", "value": "2 hours"},
                        {"label": "4 Hours", "value": "4 hours"},
                        {"label": "1 Day", "value": "1 day"},
                    ],
                },
            )
        if not comment:
            backend_refs["wp_extension_duration"] = duration
            backend_refs["wp_pending_action"] = "extend"
            return {
                "backend_refs": backend_refs,
                "confirmation_status": "PENDING",
                "status": "WAITING_FOR_USER",
                "response_message": "Got it. Any comment to include with the extension?",
                "response_ui": {"type": "message"},
            }
        backend_refs["wp_action"] = "extend"
        backend_refs["wp_extension_duration"] = duration
        backend_refs.pop("wp_pending_action", None)
        collected["comments"] = comment
        return {
            "collected_data": collected,
            "backend_refs": backend_refs,
            "confirmation_status": "CONFIRMED",
            "status": "IN_PROGRESS",
        }

    if action_key == "cancel_permit":
        if not comment:
            return _ask_for_input(
                state,
                pending="cancel_permit",
                message=(
                    "They've asked to cancel. I'll confirm the cancellation — "
                    "what comment should I include?"
                ),
            )
        backend_refs["wp_action"] = "cancel_permit"
        backend_refs.pop("wp_pending_action", None)
        collected["comments"] = comment
        return {
            "collected_data": collected,
            "backend_refs": backend_refs,
            "confirmation_status": "CONFIRMED",
            "status": "IN_PROGRESS",
        }

    # approve — comment optional
    if comment:
        collected["comments"] = comment
    backend_refs["wp_action"] = "approve"
    backend_refs.pop("wp_pending_action", None)
    return {
        "collected_data": collected,
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
    backend_refs: dict[str, Any] = dict(state.get("backend_refs") or {})
    pending = str(backend_refs.get("wp_pending_action") or "").strip()

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

    review_action = _normalize_review_action(action_override)
    if review_action and is_review:
        log.info("work_permit_entry.action_override_review", action=review_action)
        return _begin_or_complete_review_action(
            state,
            workflow_stage=workflow_stage,
            action_key=review_action,
            user_message=user_message,
        )

    if action_override in _APPROVE_ACTIONS and not is_review:
        log.info("work_permit_entry.action_override_confirm", review=False)
        return {"confirmation_status": "CONFIRMED", "status": "IN_PROGRESS"}

    if action_override in ("cancel", "cancel_create_wp"):
        log.info("work_permit_entry.action_override_cancel")
        return {
            "confirmation_status": "REJECTED",
            "status": "IN_PROGRESS",
            "response_message": "No problem — what would you like to change?",
        }

    # ── 0b. Complete a pending review input (reason / role / duration) ─────
    if is_review and pending and confirmation_status == "PENDING":
        log.info("work_permit_entry.pending_input", pending=pending)
        return _begin_or_complete_review_action(
            state,
            workflow_stage=workflow_stage,
            action_key=pending,
            user_message=user_message,
        )

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
            return _begin_or_complete_review_action(
                state,
                workflow_stage=workflow_stage,
                action_key="reject",
                user_message=user_message,
            )

        if is_review and _contains_phrase(user_message, frozenset({"more info", "ask for more", "action required"})):
            return _begin_or_complete_review_action(
                state,
                workflow_stage=workflow_stage,
                action_key="action_required",
                user_message=user_message,
            )

        if _contains_phrase(user_message, _CONFIRM_PHRASES):
            log.info("work_permit_entry.confirmation_accepted", review=is_review)
            if is_review:
                return _begin_or_complete_review_action(
                    state,
                    workflow_stage=workflow_stage,
                    action_key="approve",
                    user_message=user_message,
                )
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
                + (
                    " You can also approve, ask for more info, assign, or reject."
                    if is_review
                    else ""
                )
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
