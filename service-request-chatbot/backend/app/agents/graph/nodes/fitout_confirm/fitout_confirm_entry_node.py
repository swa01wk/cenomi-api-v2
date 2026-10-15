"""Entry node for the Fitout Confirmation SR workflow.

Responsibilities
----------------
- Act as the first node for every turn where active_agent is one of the three fitout agents
  (rdd_fitout_start_agent, rdd_fitout_end_agent, rdd_trading_confirmation_agent).
- Detect confirmation responses (yes/no) when confirmation_status == "PENDING".
- Handle explicit UI action overrides (confirm, cancel_sr, confirm_cancel_sr, …).
- Detect explicit cancel / workflow-switch phrases and clear state.
- Stage-pin on first turn: "CREATE_SR" or "APPROVE_SR" based on sr_id in backend_refs.
- Stale-clear when starting a new request after a terminal stage.

Non-responsibilities
--------------------
- MUST NOT collect or validate form fields.
- MUST NOT call the LLM.
- MUST NOT submit service requests.
"""

from __future__ import annotations

import re
from typing import Any

import structlog

from app.agents.graph.state import ServiceRequestState
from app.agents.schemas.fitout_confirm_schema import TERMINAL_STAGES
from app.agents.services.service_request_api_service import get_service_request_api_service
from app.agents.services.sr_release import released_backend_refs
from app.observability.decorators import trace_node

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Who may raise each fitout confirmation SR
# ---------------------------------------------------------------------------

# Creation only. Approving an existing confirmation SR is a different action,
# governed by the platform's own operation assignment rather than this map.
_CREATE_ROLES_BY_AGENT: dict[str, frozenset[str]] = {
    # Trading Confirmation is raised by the mall side.
    "rdd_trading_confirmation_agent": frozenset(
        {"MALL_MANAGER", "OPERATIONS", "ADMIN"}
    ),
    # Fitout Start / End confirmations are raised by RDD.
    "rdd_fitout_start_agent": frozenset({"DD_ENGINEER", "ADMIN"}),
    "rdd_fitout_end_agent": frozenset({"DD_ENGINEER", "ADMIN"}),
}

_AGENT_SR_LABELS: dict[str, str] = {
    "rdd_trading_confirmation_agent": "Trading Confirmation",
    "rdd_fitout_start_agent": "Fitout Start Confirmation",
    "rdd_fitout_end_agent": "Fitout End Confirmation",
}

_ROLE_LABELS: dict[str, str] = {
    "MALL_MANAGER": "Mall Manager",
    "OPERATIONS": "Operations",
    "DD_ENGINEER": "RDD Project Manager",
    "FM_MANAGER": "FM Manager",
    "ADMIN": "Admin",
}

_FITOUT_APPROVE_INTENTS: frozenset[str] = frozenset(
    {
        "APPROVE_FITOUT_CONFIRM_FOS",
        "APPROVE_FITOUT_CONFIRM_END",
        "APPROVE_FITOUT_CONFIRM_TRADING",
    }
)


def _role_names(state: ServiceRequestState) -> set[str]:
    auth = state.get("auth_context")
    return {str(r).upper() for r in (getattr(auth, "roles", None) or ())}


def _allowed_roles_sentence(allowed: frozenset[str]) -> str:
    """"Mall Manager and Operations" — Admin is left out; it is a superuser."""
    names = [_ROLE_LABELS.get(r, r.title()) for r in sorted(allowed) if r != "ADMIN"]
    if len(names) > 1:
        return ", ".join(names[:-1]) + " and " + names[-1]
    return names[0] if names else "another role"


def _refuse_unauthorised_create(state: ServiceRequestState) -> dict[str, Any] | None:
    """Refuse a confirmation SR the caller's role may not raise, or None to allow.

    Checked at the workflow boundary, before the lease lookup ever runs.
    Without it an RDD Project Manager asking for a Trading Confirmation was
    told their role did not allow it and then walked through the entire form
    anyway — lease suggestions, guided-mode choice, guideline link — building
    a request the platform would refuse at submit.
    """
    agent = str(state.get("active_agent") or "")
    allowed = _CREATE_ROLES_BY_AGENT.get(agent)
    if not allowed:
        return None

    # Only creation is gated here. An SR already on the platform means the
    # caller opened someone else's request to approve it.
    backend_refs = state.get("backend_refs") or {}
    if backend_refs.get("sr_id") or state.get("workflow_stage") == "APPROVE_SR":
        return None
    # Anything not an approve is on its way to creating one — including a turn
    # the supervisor could not classify. Listing the create intents instead
    # left that turn ungated, and the node stage-pins it to CREATE_SR anyway.
    if str(state.get("intent") or "") in _FITOUT_APPROVE_INTENTS:
        return None

    roles = _role_names(state)
    # An unresolved role stays permissive, matching every other entry guard —
    # they deny only when a role is actually present.
    if not roles or roles & allowed:
        return None

    label = _AGENT_SR_LABELS.get(agent, "confirmation")
    log.info(
        "fitout_confirm_entry.create_denied_for_role",
        agent=agent,
        roles=sorted(roles),
    )
    patch = _clear_all_state()
    patch["response_message"] = (
        f"Creating a {label} service request isn't available for your role — "
        f"{_allowed_roles_sentence(allowed)} raise these. "
        "I can still help you with handover service requests, or answer "
        "questions about the platform."
    )
    patch["response_ui"] = {"type": "message", "verbatim": True}
    patch["response_decorations"] = {}
    return patch

# ---------------------------------------------------------------------------
# Phrase sets
# ---------------------------------------------------------------------------

_CONFIRM_PHRASES: frozenset[str] = frozenset(
    {
        "yes", "yep", "yeah", "yup", "confirm", "confirmed", "submit",
        "proceed", "correct", "looks good", "that's correct", "thats correct",
        "go ahead", "approve", "ok", "okay", "sure", "absolutely", "agree",
        "update and approve",
    }
)

_REJECT_PHRASES: frozenset[str] = frozenset(
    {
        "no", "nope", "nah", "cancel", "change", "update", "edit",
        "modify", "wrong", "incorrect", "not right", "that's wrong",
        "thats wrong", "fix", "correct it", "i want to change", "let me change",
    }
)

_CANCEL_WORKFLOW_PHRASES: frozenset[str] = frozenset(
    {
        "start over", "restart", "new request", "different request",
        "begin again", "reset",
    }
)

_CANCEL_SR_PHRASES: frozenset[str] = frozenset(
    {
        "cancel", "cancel request", "cancel sr", "cancel this request",
        "cancel the request", "i want to cancel", "please cancel", "abort",
    }
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _clear_all_state() -> dict:
    """Return a state patch that zeros all workflow-related fields after cancel."""
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
        "documents": [],
        # Cancelling wipes the draft, so any per-turn pointer into it is stale.
        # Leaving active_field set sent the very next turn to field_extraction
        # (see _waiting_short_circuit_target), which discarded the "request has
        # been cleared" reply and re-asked for the lease code — the draft really
        # was cleared, but the user only saw the question come back.
        "active_field": None,
        "correction_pending_field": None,
        "response_ui": {"cancelled": True},
        "status": "WAITING_FOR_USER",
    }


_MILESTONE_ACK_KEY = "_milestone_confirmed_ack"

_CONTINUE_MILESTONE_PHRASES: frozenset[str] = frozenset(
    {
        "continue",
        "proceed",
        "go ahead",
        "revise",
        "keep going",
        "yes",
        "yep",
        "yeah",
        "ok",
        "okay",
    }
)

_CLEARED_REQUEST_MESSAGE = (
    "The request has been cleared. Would you like to continue?"
)


def _ack_milestone(state: ServiceRequestState) -> dict[str, Any]:
    collected = dict(state.get("collected_data") or {})
    collected[_MILESTONE_ACK_KEY] = True
    log.info("fitout_confirm_entry.continue_milestone")
    return {
        "collected_data": collected,
        "confirmation_status": None,
        "status": "IN_PROGRESS",
    }


def _clear_draft_for_milestone_cancel() -> dict[str, Any]:
    patch = _clear_all_state()
    patch["response_ui"] = {
        "type": "message",
        "verbatim": True,
        "reset_conversation": True,
    }
    patch["response_message"] = _CLEARED_REQUEST_MESSAGE
    return patch


def _cancelled_patch(sr_id: Any) -> dict[str, Any]:
    """State patch for a confirmed cancel — clears the chat draft *and* the form.

    ``reset_conversation`` is what makes the left-hand Service Request form
    empty out: the client answers it by calling ``resetFormFromChat()`` and
    dropping every draft field. Without it the form kept the cancelled
    request's lease, company and dates on screen while the assistant said the
    SR had been cancelled — the two panes disagreeing about whether the
    request still existed. The handover flow has always sent it; all three
    fitout confirmations were sending only ``cancelled``, which shows the
    banner and touches nothing else.
    """
    patch = _clear_all_state()
    patch["workflow_stage"] = "CANCELLED"
    patch["response_ui"] = {
        "type": "message",
        "verbatim": True,
        "reset_conversation": True,
        "cancelled": True,
        "workflow_stage": "CANCELLED",
    }
    # A request being created has no number yet — the SR is only raised on
    # submit. Naming one anyway printed "Service Request **N/A** has been
    # cancelled", which reads as a failure to look the number up rather than as
    # a draft that never became an SR.
    patch["response_message"] = (
        f"Service Request **{sr_id}** has been cancelled. "
        "What would you like to do next?"
        if sr_id
        else "The request has been cancelled and the form cleared. "
        "What would you like to do next?"
    )
    return patch


def _contains_phrase(message: str, phrases: frozenset[str]) -> bool:
    lowered = message.lower()
    return any(
        bool(re.search(r"\b" + re.escape(phrase) + r"\b", lowered))
        for phrase in phrases
    )


# ---------------------------------------------------------------------------
# Node
# ---------------------------------------------------------------------------


@trace_node("fitout_confirm_entry", "AGENT")
async def fitout_confirm_entry_node(state: ServiceRequestState) -> dict[str, Any]:
    """Parse user intent at the boundary of the fitout confirmation workflow."""
    user_message: str = state.get("user_message") or ""
    confirmation_status: str | None = state.get("confirmation_status")
    action_override: str | None = state.get("action_override")  # type: ignore[assignment]

    # ── Role guard — before anything that could start collecting ───────────
    refusal = _refuse_unauthorised_create(state)
    if refusal is not None:
        return refusal

    if action_override == "continue_milestone":
        return _ack_milestone(state)

    if action_override == "cancel_milestone":
        log.info("fitout_confirm_entry.action_override_cancel_milestone")
        return _clear_draft_for_milestone_cancel()

    if state.get("confirmation_status") == "MILESTONE_PENDING":
        if _contains_phrase(user_message, _CONTINUE_MILESTONE_PHRASES):
            return _ack_milestone(state)
        if _contains_phrase(user_message, _CANCEL_SR_PHRASES):
            log.info("fitout_confirm_entry.milestone_cancel_by_text")
            return _clear_draft_for_milestone_cancel()
        return {
            "status": "WAITING_FOR_USER",
            "confirmation_status": "MILESTONE_PENDING",
            "response_message": (
                "Please tap **Continue** to revise the existing date, "
                "or **Cancel** to pick a different lease."
            ),
            "response_ui": {"type": "message", "verbatim": True},
            "response_decorations": {
                "suggestions": {
                    "kind": "labeled_options",
                    "options": [
                        {"label": "Cancel", "value": "cancel", "action": "cancel_milestone"},
                        {"label": "Continue", "value": "continue", "action": "continue_milestone"},
                    ],
                }
            },
        }

    # ── 0. Explicit UI action — takes priority over text parsing ───────────
    if action_override in (
        "confirm",
        "confirm_create_sr",
        "confirm_approve_sr",
        "confirm_create_fitout_confirm",
        "approve",
        "update_and_approve",
    ):
        log.info("fitout_confirm_entry.action_override_confirm", action=action_override)
        # Clear the stale WAITING_FOR_USER left over from the turn that rendered
        # the confirmation card, so routing cannot short-circuit back to
        # response_generation and replay the previous question.
        return {"confirmation_status": "CONFIRMED", "status": "IN_PROGRESS"}

    if action_override == "update":
        # Update-only: PATCH with status IN_PROCESS (stays in APPROVE_SR).
        log.info("fitout_confirm_entry.action_override_update_only")
        backend_refs = dict(state.get("backend_refs") or {})
        backend_refs["fitout_action"] = "update_only"
        return {
            "backend_refs": backend_refs,
            "confirmation_status": "CONFIRMED",
        }

    if action_override in ("cancel_sr", "cancel", "cancel_create_sr"):
        log.info("fitout_confirm_entry.action_override_cancel_sr")
        backend_refs: dict[str, Any] = state.get("backend_refs") or {}
        sr_id = backend_refs.get("sr_id")
        if sr_id:
            message = (
                f"Are you sure you want to cancel Service Request **{sr_id}**? "
                "This will mark it as cancelled on the platform. "
                "Reply **yes** to confirm, or **no** to go back."
            )
        else:
            message = (
                "Are you sure you want to cancel this request? "
                "This will clear the current details. "
                "Reply **yes** to confirm, or **no** to go back to the review."
            )
        return {
            "confirmation_status": "CANCEL_PENDING",
            "status": "WAITING_FOR_USER",
            "response_message": message,
        }

    if action_override == "confirm_cancel_sr":
        log.info("fitout_confirm_entry.action_override_confirm_cancel_sr")
        backend_refs = state.get("backend_refs") or {}
        sr_id = backend_refs.get("sr_id")
        if sr_id:
            try:
                svc = get_service_request_api_service()
                await svc.patch_service_request(sr_id, {"status": "CANCELLED"})
                log.info("fitout_confirm_entry.sr_cancelled_on_platform", sr_id=sr_id)
            except Exception:
                log.warning(
                    "fitout_confirm_entry.cancel_patch_failed",
                    sr_id=sr_id,
                    exc_info=True,
                )
        return _cancelled_patch(sr_id)

    if action_override == "decline_cancel_sr":
        log.info("fitout_confirm_entry.action_override_decline_cancel_sr")
        return {
            "confirmation_status": None,
            "status": "IN_PROGRESS",
            "response_message": (
                "No problem — your request is still active. "
                "What would you like to do next?"
            ),
        }

    if action_override == "cancel_update":
        log.info("fitout_confirm_entry.action_override_cancel_update")
        return {
            "status": "WAITING_FOR_USER",
            "response_message": "Update cancelled. What would you like to do next?",
        }

    # ── 1. CANCEL_PENDING text confirmation handling ───────────────────────
    if state.get("confirmation_status") == "CANCEL_PENDING":
        if _contains_phrase(user_message, _CONFIRM_PHRASES):
            log.info("fitout_confirm_entry.cancel_confirmed_by_text")
            backend_refs = state.get("backend_refs") or {}
            sr_id = backend_refs.get("sr_id")
            if sr_id:
                try:
                    svc = get_service_request_api_service()
                    await svc.patch_service_request(sr_id, {"status": "CANCELLED"})
                    log.info("fitout_confirm_entry.sr_cancelled_on_platform", sr_id=sr_id)
                except Exception:
                    log.warning(
                        "fitout_confirm_entry.cancel_patch_failed",
                        sr_id=sr_id,
                        exc_info=True,
                    )
            return _cancelled_patch(sr_id)

        if _contains_phrase(user_message, _REJECT_PHRASES):
            log.info("fitout_confirm_entry.cancel_declined_by_text")
            return {
                "confirmation_status": None,
                "status": "IN_PROGRESS",
                "response_message": (
                    "No problem — your request is still active. "
                    "What would you like to do next?"
                ),
            }

        return {
            "status": "WAITING_FOR_USER",
            "response_message": (
                "Please reply **yes** to confirm cancellation, or **no** to keep the request."
            ),
        }

    # ── 2. Workflow cancel / restart ───────────────────────────────────────
    if _contains_phrase(user_message, _CANCEL_SR_PHRASES):
        log.info("fitout_confirm_entry.text_cancel_sr", user_message=user_message[:120])
        backend_refs = state.get("backend_refs") or {}
        sr_id = backend_refs.get("sr_id")
        if sr_id:
            message = (
                f"Are you sure you want to cancel Service Request **{sr_id}**? "
                "Reply **yes** to confirm, or **no** to go back."
            )
        else:
            message = (
                "Are you sure you want to cancel this request? "
                "Reply **yes** to confirm, or **no** to go back to the review."
            )
        return {
            "confirmation_status": "CANCEL_PENDING",
            "status": "WAITING_FOR_USER",
            "response_message": message,
        }

    if _contains_phrase(user_message, _CANCEL_WORKFLOW_PHRASES):
        log.info("fitout_confirm_entry.workflow_cancel", user_message=user_message[:120])
        patch = _clear_all_state()
        patch["response_message"] = (
            "Sure — I've cleared everything. What would you like to do next?"
        )
        # "I've cleared everything" has to include the form. Without
        # reset_conversation the client keeps every draft field, so the lease,
        # company and dates of the abandoned request stayed on screen.
        patch["response_ui"] = {
            "type": "message",
            "verbatim": True,
            "reset_conversation": True,
        }
        return patch

    # ── 3. Confirmation response parsing ──────────────────────────────────
    if confirmation_status == "PENDING":
        if _contains_phrase(user_message, _CONFIRM_PHRASES):
            log.info("fitout_confirm_entry.confirmation_accepted")
            # Clear stale WAITING_FOR_USER (see action_override branch above).
            return {"confirmation_status": "CONFIRMED", "status": "IN_PROGRESS"}

        if _contains_phrase(user_message, _REJECT_PHRASES):
            log.info("fitout_confirm_entry.confirmation_rejected")
            return {
                "confirmation_status": "REJECTED",
                "status": "IN_PROGRESS",
            }

        log.info("fitout_confirm_entry.confirmation_ambiguous", user_message=user_message[:120])
        return {
            "response_message": (
                "Please reply with 'yes' to confirm and submit, "
                "or tell me what you'd like to change."
            ),
        }

    # ── 4. Stale-clear for terminal stages or previous failure ────────────
    _CREATE_INTENTS: frozenset[str] = frozenset(
        {
            "CREATE_FITOUT_CONFIRM_FOS",
            "CREATE_FITOUT_CONFIRM_END",
            "CREATE_FITOUT_CONFIRM_TRADING",
        }
    )
    if state.get("intent") in _CREATE_INTENTS:
        _should_clear = state.get("workflow_stage") in TERMINAL_STAGES or state.get("status") == "FAILED"
        if _should_clear:
            log.info(
                "fitout_confirm_entry.stale_sr_cleared",
                workflow_stage=state.get("workflow_stage"),
            )
            # Partial clear: reset workflow state but keep active_agent and pin
            # workflow_stage to CREATE_SR so that _route_after_fitout_confirm_entry
            # continues to field_extraction and processes the new request in this
            # same turn.  Full _clear_all_state() sets status=WAITING_FOR_USER which
            # would short-circuit to response_generation without processing the message.
            return {
                "workflow_stage": "CREATE_SR",
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
                "active_field": None,
            }

    # ── 5. Stage-pin on first turn ─────────────────────────────────────────
    _FITOUT_AGENTS: frozenset[str] = frozenset(
        {"rdd_fitout_start_agent", "rdd_fitout_end_agent", "rdd_trading_confirmation_agent"}
    )
    if state.get("active_agent") in _FITOUT_AGENTS and not state.get("workflow_stage"):
        backend_refs = state.get("backend_refs") or {}
        if backend_refs.get("sr_id"):
            log.info("fitout_confirm_entry.routing_to_approve_sr")
            return {"workflow_stage": "APPROVE_SR"}
        intent = state.get("intent") or ""
        _APPROVE_INTENTS: frozenset[str] = frozenset(
            {"APPROVE_FITOUT_CONFIRM_FOS", "APPROVE_FITOUT_CONFIRM_END", "APPROVE_FITOUT_CONFIRM_TRADING"}
        )
        if intent in _APPROVE_INTENTS:
            log.info("fitout_confirm_entry.routing_to_approve_sr_via_intent")
            return {"workflow_stage": "APPROVE_SR"}
        log.info("fitout_confirm_entry.routing_to_create_sr")
        return {"workflow_stage": "CREATE_SR"}

    return {}
