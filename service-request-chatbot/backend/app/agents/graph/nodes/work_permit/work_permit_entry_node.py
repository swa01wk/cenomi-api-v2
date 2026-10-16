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

from app.agents.graph.nodes.work_permit.work_permit_confirmation_node import (
    build_wp_review_card_suggestions,
)
from app.agents.graph.nodes.work_permit.work_permit_review_context import (
    build_wp_review_context,
)
from app.agents.graph.state import ServiceRequestState
from app.agents.services.work_permit_review_summary import compose_wp_history_sentence
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
from app.agents.services.conversation_state_service import PENDING_REVIEW_REFS
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
    ctx = build_wp_review_context(state, workflow_stage=workflow_stage)
    return frozenset(a.id for a in resolve_actions(ctx))


_QUESTION_RE = re.compile(
    r"\?\s*$|^(who|what|when|where|which|why|how|is|are|was|were|does|do|did|"
    r"can|could|will|would|has|have|tell\s+me|explain)\b",
    re.IGNORECASE,
)


def _review_actions_line(state: ServiceRequestState, workflow_stage: str) -> str:
    """The reviewer's real options, never create wording ("yes", "confirm and submit")."""
    ctx = build_wp_review_context(state, workflow_stage=workflow_stage)
    labels = [f"**{a.label}**" for a in resolve_actions(ctx)]
    if not labels:
        return "Ask me anything about this request."
    joined = labels[0] if len(labels) == 1 else f"{', '.join(labels[:-1])}, or {labels[-1]}"
    return (
        f"You can {joined} this Work Permit using the buttons below, "
        "or ask me anything about the request."
    )


def _review_fallback(
    state: ServiceRequestState, workflow_stage: str, user_message: str
) -> dict[str, Any]:
    """Unmatched message on an open review: answer a question, else list the actions.

    Keeps the review chips on the reply (the widget replaces the chip footer
    every turn) and never touches the pending confirmation.
    """
    ctx = build_wp_review_context(state, workflow_stage=workflow_stage)
    decorations = {"suggestions": build_wp_review_card_suggestions(state, subtype=ctx.subtype)}
    actions_line = _review_actions_line(state, workflow_stage)
    if _QUESTION_RE.search(user_message.strip()):
        backend_refs: dict[str, Any] = dict(state.get("backend_refs") or {})
        history = compose_wp_history_sentence(
            backend_refs.get("sr_history"), backend_refs.get("sr_creator")
        )
        hint = "Answer the reviewer's question about this Work Permit from the request details."
        if history:
            hint += f" Request history: {history}"
        return {
            "status": "WAITING_FOR_USER",
            "response_message": f"{hint} Close with: {actions_line}",
            "response_decorations": decorations,
        }
    return {
        "status": "WAITING_FOR_USER",
        "response_message": actions_line,
        "response_ui": {"type": "message", "message": actions_line, "verbatim": True},
        "response_decorations": decorations,
    }


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


# A button click sends its id (or label) as the message; neither is a comment.
_ACTION_ECHOES: frozenset[str] = frozenset(
    a.casefold()
    for a in (
        *_APPROVE_ACTIONS,
        *_REJECT_SR_ACTIONS,
        *_MORE_INFO_ACTIONS,
        *_ASSIGN_ACTIONS,
        *_EXTEND_ACTIONS,
        *_CANCEL_PERMIT_ACTIONS,
        "Approve",
        "Reject",
        "Ask for more info",
        "Assign to",
        "Extend",
        "Cancel permit",
    )
)
_CANCEL_PROMPT_PHRASES: frozenset[str] = frozenset(
    {"cancel", "never mind", "nevermind", "stop"}
)
_CANCEL_CHIP: dict[str, str] = {"label": "Cancel", "value": "cancel"}

# action_key → (action id, label, prompt, comment required)
_COMMENT_PROMPTS: dict[str, tuple[str, str, str, bool]] = {
    "approve": (
        WP_ACTION_APPROVE,
        "Approve",
        "I'll approve this permit. What comment should I include?",
        True,
    ),
    "action_required": (
        WP_ACTION_MORE_INFO,
        "Ask for more info",
        "I'll send this back to the tenant. What should I tell them?",
        True,
    ),
    "reject": (
        WP_ACTION_REJECT,
        "Reject",
        "I'll reject this permit. What reason should I include?",
        True,
    ),
}


def _reply_text(user_message: str) -> str:
    """The user's typed reply, or ``""`` when it is a clicked action's echo."""
    text = (user_message or "").strip()
    return "" if text.casefold() in _ACTION_ECHOES else text


def _ask_for_comment(state: ServiceRequestState, action_key: str) -> dict[str, Any]:
    action_id, label, prompt, required = _COMMENT_PROMPTS[action_key]
    comment_for: dict[str, Any] = {
        "action_id": action_id,
        "label": label,
        "required": required,
    }
    return _ask_for_input(
        state,
        pending=action_key,
        message=prompt,
        suggestions={"kind": "labeled_options", "options": [_CANCEL_CHIP]},
        ui_extra={"comment_for": comment_for, "verbatim": True},
    )


def _clear_pending(backend_refs: dict[str, Any]) -> None:
    for key in PENDING_REVIEW_REFS:
        backend_refs.pop(key, None)


def _mark_pending(
    backend_refs: dict[str, Any], state: ServiceRequestState, pending: str
) -> None:
    backend_refs["wp_pending_action"] = pending
    backend_refs["wp_pending_stage"] = state.get("workflow_stage") or ""
    backend_refs["wp_pending_sr_id"] = str(backend_refs.get("sr_id") or "")


def _pending_is_current(backend_refs: dict[str, Any], workflow_stage: str) -> bool:
    """A prompt only answers on the stage and SR it was asked on."""
    return str(backend_refs.get("wp_pending_stage") or "") == workflow_stage and str(
        backend_refs.get("wp_pending_sr_id") or ""
    ) == str(backend_refs.get("sr_id") or "")


def _cancel_pending_prompt(state: ServiceRequestState) -> dict[str, Any]:
    backend_refs = dict(state.get("backend_refs") or {})
    _clear_pending(backend_refs)
    backend_refs.pop("wp_review_comment", None)
    message = "No problem, the permit is still waiting for your review."
    return {
        "backend_refs": backend_refs,
        "confirmation_status": "PENDING",
        "confirmation_required": True,
        "status": "WAITING_FOR_USER",
        "response_message": message,
        "response_ui": {"type": "message", "message": message, "verbatim": True},
        "response_decorations": {},
    }


def _confirmed(
    backend_refs: dict[str, Any], *, action_key: str, comment: str
) -> dict[str, Any]:
    backend_refs["wp_action"] = action_key
    if comment:
        backend_refs["wp_review_comment"] = comment
    else:
        backend_refs.pop("wp_review_comment", None)
    duration = backend_refs.get("wp_extension_duration")
    _clear_pending(backend_refs)
    if action_key == "extend" and duration is not None:
        backend_refs["wp_extension_duration"] = duration
    return {
        "backend_refs": backend_refs,
        "confirmation_status": "CONFIRMED",
        "status": "IN_PROGRESS",
    }


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
    ui_extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    backend_refs = dict(state.get("backend_refs") or {})
    _mark_pending(backend_refs, state, pending)
    ui: dict[str, Any] = {"type": "message", "message": message, **(ui_extra or {})}
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
    resuming: bool = False,
) -> dict[str, Any]:
    """Validate permission, collect the comment and other inputs, then set CONFIRMED.

    Starting an action always asks for its comment in chat; only the reply to
    that prompt (``resuming``) is taken as the comment, so a clicked button's
    id, a typed "approve", or an earlier hop's comment never is.
    """
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

    backend_refs = dict(state.get("backend_refs") or {})
    reply = _reply_text(user_message) if resuming else ""
    if resuming and reply.casefold() in _CANCEL_PROMPT_PHRASES:
        return _cancel_pending_prompt(state)

    if action_key in {"approve", "reject", "action_required"}:
        if not reply:
            return _ask_for_comment(state, action_key)
        return _confirmed(backend_refs, action_key=action_key, comment=reply)

    if action_key == "assign_to":
        role = str(
            backend_refs.get("wp_assign_to")
            or (state.get("collected_data") or {}).get("assign_to")
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
        backend_refs["wp_assign_to"] = role
        return _confirmed(backend_refs, action_key="assign_to", comment="")

    if action_key == "extend":
        duration = backend_refs.get("wp_extension_duration")
        has_duration = isinstance(duration, dict)
        if not has_duration:
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
        comment = reply if has_duration else ""
        if not comment:
            backend_refs["wp_extension_duration"] = duration
            _mark_pending(backend_refs, state, "extend")
            return {
                "backend_refs": backend_refs,
                "confirmation_status": "PENDING",
                "status": "WAITING_FOR_USER",
                "response_message": "Got it. Any comment to include with the extension?",
                "response_ui": {"type": "message"},
            }
        backend_refs["wp_extension_duration"] = duration
        return _confirmed(backend_refs, action_key="extend", comment=comment)

    if action_key == "cancel_permit":
        if not reply:
            return _ask_for_input(
                state,
                pending="cancel_permit",
                message=(
                    "They've asked to cancel. I'll confirm the cancellation — "
                    "what comment should I include?"
                ),
            )
        return _confirmed(backend_refs, action_key="cancel_permit", comment=reply)

    return _confirmed(backend_refs, action_key=action_key, comment="")


@trace_node("work_permit_entry", "AGENT")
async def work_permit_entry_node(state: ServiceRequestState) -> dict[str, Any]:
    """Parse confirmation / cancel / approve / reject at the WP workflow boundary."""
    backend_refs: dict[str, Any] = dict(state.get("backend_refs") or {})
    workflow_stage: str = state.get("workflow_stage") or ""
    pending = backend_refs.get("wp_pending_action")
    if not pending or (
        workflow_stage in WP_REVIEW_STAGES and _pending_is_current(backend_refs, workflow_stage)
    ):
        return await _work_permit_entry(state)

    # An abandoned prompt from another hop or SR must not swallow this reply.
    log.info(
        "work_permit_entry.stale_pending_dropped",
        pending=pending,
        pending_stage=backend_refs.get("wp_pending_stage"),
        workflow_stage=workflow_stage,
    )
    _clear_pending(backend_refs)
    result = await _work_permit_entry({**state, "backend_refs": backend_refs})  # type: ignore[typeddict-item]
    return result if "backend_refs" in result else {**result, "backend_refs": backend_refs}


async def _work_permit_entry(state: ServiceRequestState) -> dict[str, Any]:
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
            resuming=True,
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

        if is_review:
            return _review_fallback(state, workflow_stage, user_message)
        return {
            "status": "WAITING_FOR_USER",
            "response_message": (
                "Please reply with 'yes' to confirm and submit, "
                "or tell me what you'd like to change."
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

    # An open SR whose hop status sync could not map must not become a new
    # create on top of the permit the user is reviewing.
    if not workflow_stage and backend_refs.get("sr_id"):
        log.warning(
            "work_permit_entry.unmapped_open_sr",
            sr_id=backend_refs.get("sr_id"),
            in_progress_roles=sorted(in_progress_roles(backend_refs.get("sr_operations"))),
        )
        return {
            "status": "WAITING_FOR_USER",
            "response_message": (
                f"I couldn't determine which review step Work Permit "
                f"{backend_refs.get('sr_id')} is on, so I can't act on it here. "
                "Please use the actions on the service request page."
            ),
            "response_ui": {"type": "message"},
        }

    # First create turn — initialize workflow stage.
    if not workflow_stage:
        return {"workflow_stage": "CREATE_WORK_PERMIT", "status": "IN_PROGRESS"}

    return {"status": "IN_PROGRESS"}
