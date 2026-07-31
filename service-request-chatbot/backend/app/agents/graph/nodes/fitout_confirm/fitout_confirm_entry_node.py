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
from app.observability.decorators import trace_node

log = structlog.get_logger(__name__)

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
        "response_ui": {"cancelled": True},
        "status": "WAITING_FOR_USER",
    }


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

    # ── 0. Explicit UI action — takes priority over text parsing ───────────
    if action_override in ("confirm", "confirm_create_sr", "confirm_approve_sr"):
        log.info("fitout_confirm_entry.action_override_confirm")
        return {"confirmation_status": "CONFIRMED"}

    if action_override == "cancel" and confirmation_status == "PENDING":
        log.info("fitout_confirm_entry.cancel_rejects_pending_confirmation")
        return {"confirmation_status": "REJECTED", "status": "IN_PROGRESS"}

    if action_override in ("cancel_sr", "cancel", "cancel_create_sr"):
        log.info("fitout_confirm_entry.action_override_cancel_sr")
        backend_refs: dict[str, Any] = state.get("backend_refs") or {}
        sr_id = backend_refs.get("sr_id")
        if not sr_id:
            patch = _clear_all_state()
            patch["response_message"] = (
                "Your fitout confirmation request has been cancelled. "
                "What would you like to do next?"
            )
            return patch
        return {
            "confirmation_status": "CANCEL_PENDING",
            "status": "WAITING_FOR_USER",
            "response_message": (
                f"Are you sure you want to cancel Service Request **{sr_id}**? "
                "This will mark it as cancelled on the platform. "
                "Reply **yes** to confirm, or **no** to go back."
            ),
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
        patch = _clear_all_state()
        patch["workflow_stage"] = "CANCELLED"
        patch["response_ui"] = {"cancelled": True, "workflow_stage": "CANCELLED"}
        patch["response_message"] = (
            f"Service Request **{sr_id or 'N/A'}** has been cancelled. "
            "What would you like to do next?"
        )
        return patch

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
            patch = _clear_all_state()
            patch["workflow_stage"] = "CANCELLED"
            patch["response_ui"] = {"cancelled": True, "workflow_stage": "CANCELLED"}
            patch["response_message"] = (
                f"Service Request **{sr_id or 'N/A'}** has been cancelled. "
                "What would you like to do next?"
            )
            return patch

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
        if not sr_id:
            patch = _clear_all_state()
            patch["response_message"] = (
                "Your fitout confirmation request has been cancelled. "
                "What would you like to do next?"
            )
            return patch
        return {
            "confirmation_status": "CANCEL_PENDING",
            "status": "WAITING_FOR_USER",
            "response_message": (
                f"Are you sure you want to cancel Service Request **{sr_id}**? "
                "Reply **yes** to confirm, or **no** to go back."
            ),
        }

    if _contains_phrase(user_message, _CANCEL_WORKFLOW_PHRASES):
        log.info("fitout_confirm_entry.workflow_cancel", user_message=user_message[:120])
        patch = _clear_all_state()
        patch["response_message"] = (
            "Sure — I've cleared everything. What would you like to do next?"
        )
        patch["response_ui"] = {}
        return patch

    # ── 3. Confirmation response parsing ──────────────────────────────────
    if confirmation_status == "PENDING":
        if _contains_phrase(user_message, _CONFIRM_PHRASES):
            log.info("fitout_confirm_entry.confirmation_accepted")
            return {"confirmation_status": "CONFIRMED"}

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

    # ── 4. Stale-clear for terminal stages ────────────────────────────────
    _CREATE_INTENTS: frozenset[str] = frozenset(
        {
            "CREATE_FITOUT_CONFIRM_FOS",
            "CREATE_FITOUT_CONFIRM_END",
            "CREATE_FITOUT_CONFIRM_TRADING",
        }
    )
    if state.get("intent") in _CREATE_INTENTS:
        if state.get("workflow_stage") in TERMINAL_STAGES:
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
                "backend_refs": {},
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
