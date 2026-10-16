"""Entry node for the Ceiling Inspection create flow.

Responsibilities
----------------
- First node on every create turn where ``active_agent`` is
  ``rdd_ceiling_inspection_agent``.
- Refuse the turn when the caller cannot create (only Mall Manager, FM Manager
  and RDD PM raise a ceiling inspection), before any lease lookup runs.
- Take RDD PM's draft-report status chip (Approved / Approved with changes)
  during create; the confirmation node asks for the report and its status.
- Choose the sub-category from the user's words — "open ceiling" / "OCI" or
  "close ceiling" / "CCI". Plain "ceiling inspection" is asked once, never guessed.
- Seed ``inspection_done_by`` from the platform form's own field value, so the
  summary shows what the form shows. It is never asked.
- Confirmation (yes / no / cancel) parsing, same contract as the fitout agents.
- Stage-pin a fresh request to ``CREATE_CEILING_INSPECTION_SR``.

Review and acknowledgement are not handled here — ``sr_status_sync`` routes an
opened SR to ``ceiling_review_entry`` or ``ceiling_ack_entry``.

Non-responsibilities
--------------------
- MUST NOT collect or validate form fields.
- MUST NOT call the LLM.
- MUST NOT submit service requests.
"""

from __future__ import annotations

from typing import Any

import structlog

# The confirm / cancel vocabulary and the state-clearing patches are shared
# with the fitout confirmation agents so both flows answer "yes", "cancel" and
# "start over" identically.
from app.agents.graph.nodes.fitout_confirm.fitout_confirm_entry_node import (
    _CANCEL_SR_PHRASES,
    _CANCEL_WORKFLOW_PHRASES,
    _CONFIRM_PHRASES,
    _REJECT_PHRASES,
    _cancelled_patch,
    _clear_all_state,
    _contains_phrase,
)
from app.agents.graph.state import ServiceRequestState
from app.agents.schemas.ceiling_schema import (
    AGENT_NAME,
    INTENT_CREATE,
    SERVICE_CATEGORY,
    STAGE_CREATE,
    SUB_CATEGORY_CCI,
    SUB_CATEGORY_OCI,
    SUB_CATEGORY_UNRESOLVED,
    TERMINAL_STAGES,
    can_create,
    detect_sub_category,
    is_resolved_sub_category,
    sub_category_label,
)
from app.agents.services.ceiling_inspection_done_by import resolve_inspection_done_by
from app.agents.services.service_request_api_service import get_service_request_api_service
from app.observability.decorators import trace_node

log = structlog.get_logger(__name__)

# The user's first message, held while we ask which inspection they mean, so
# the lease code in "raise a ceiling inspection for lease t0105712" is not lost.
_PENDING_MESSAGE_KEY = "_ceiling_pending_message"

_SELECT_SUBCAT_ACTIONS: dict[str, str] = {
    "select_ceiling_oci": SUB_CATEGORY_OCI,
    "select_ceiling_cci": SUB_CATEGORY_CCI,
}

_CONFIRM_ACTIONS: frozenset[str] = frozenset(
    {"confirm", "confirm_create_sr", "confirm_create_ceiling_inspection"}
)

_SUBCAT_QUESTION = (
    "Is this an **Open Ceiling Inspection** or a **Close Ceiling Inspection**?"
)


def _ask_sub_category(state: ServiceRequestState) -> dict[str, Any]:
    collected = dict(state.get("collected_data") or {})
    # Keep the first message only — a re-ask must not overwrite it with "hmm".
    if not collected.get(_PENDING_MESSAGE_KEY):
        collected[_PENDING_MESSAGE_KEY] = state.get("user_message") or ""
    return {
        "active_agent": AGENT_NAME,
        "service_category": SERVICE_CATEGORY,
        "sub_category": SUB_CATEGORY_UNRESOLVED,
        "workflow_stage": STAGE_CREATE,
        "collected_data": collected,
        "status": "WAITING_FOR_USER",
        "active_field": None,
        "response_message": _SUBCAT_QUESTION,
        "response_ui": {
            "type": "text_question",
            "field": "sub_category",
            "message": _SUBCAT_QUESTION,
            "verbatim": True,
        },
        "response_decorations": {
            "suggestions": {
                "kind": "labeled_options",
                "options": [
                    {
                        "label": "Open Ceiling Inspection",
                        "value": "Open Ceiling Inspection",
                        "action": "select_ceiling_oci",
                    },
                    {
                        "label": "Close Ceiling Inspection",
                        "value": "Close Ceiling Inspection",
                        "action": "select_ceiling_cci",
                    },
                ],
            }
        },
    }


def _resolved_patch(
    state: ServiceRequestState, sub_category: str, inspection_done_by: int
) -> dict[str, Any]:
    """Pin the sub-category and hand the turn to the shared field pipeline."""
    collected = dict(state.get("collected_data") or {})
    pending = str(collected.pop(_PENDING_MESSAGE_KEY, "") or "").strip()
    collected["inspection_done_by"] = inspection_done_by
    patch: dict[str, Any] = {
        "active_agent": AGENT_NAME,
        "intent": INTENT_CREATE,
        "service_category": SERVICE_CATEGORY,
        "sub_category": sub_category,
        "workflow_stage": STAGE_CREATE,
        "collected_data": collected,
        "status": "IN_PROGRESS",
    }
    # Replay the held first message with this answer so extraction still sees
    # the lease code the user gave before we asked which inspection.
    if pending:
        reply = str(state.get("user_message") or "").strip()
        patch["user_message"] = f"{pending}\n{reply}" if reply else pending
    log.info("ceiling_entry.sub_category_resolved", sub_category=sub_category)
    return patch


def _refuse_create(state: ServiceRequestState) -> dict[str, Any]:
    log.info("ceiling_entry.create_denied_for_role")
    patch = _clear_all_state()
    patch["response_message"] = (
        "Only a Mall Manager, FM Manager or RDD Project Manager can raise a "
        "ceiling inspection, so your role cannot raise one. I can still help you "
        "review a ceiling inspection assigned to you, or answer questions about "
        "the platform."
    )
    patch["response_ui"] = {"type": "message", "verbatim": True}
    patch["response_decorations"] = {}
    return patch


async def _cancel_on_platform(sr_id: Any) -> None:
    try:
        await get_service_request_api_service().patch_service_request(
            sr_id, {"status": "CANCELLED"}
        )
        log.info("ceiling_entry.sr_cancelled_on_platform", sr_id=sr_id)
    except Exception:
        log.warning("ceiling_entry.cancel_patch_failed", sr_id=sr_id, exc_info=True)


def _cancel_prompt(sr_id: Any) -> dict[str, Any]:
    message = (
        f"Are you sure you want to cancel Service Request **{sr_id}**? "
        "Reply **yes** to confirm, or **no** to go back."
        if sr_id
        else "Are you sure you want to cancel this request? "
        "Reply **yes** to confirm, or **no** to go back to the review."
    )
    return {
        "confirmation_status": "CANCEL_PENDING",
        "status": "WAITING_FOR_USER",
        "response_message": message,
    }


_STILL_ACTIVE = {
    "confirmation_status": None,
    "status": "IN_PROGRESS",
    "response_message": (
        "No problem — your request is still active. What would you like to do next?"
    ),
}


@trace_node("ceiling_entry", "AGENT")
async def ceiling_entry_node(state: ServiceRequestState) -> dict[str, Any]:
    """Parse the user's turn at the boundary of the ceiling inspection create flow."""
    user_message: str = state.get("user_message") or ""
    action: str = str(state.get("action_override") or "")
    confirmation_status = state.get("confirmation_status")
    backend_refs: dict[str, Any] = state.get("backend_refs") or {}
    sr_id = backend_refs.get("sr_id")
    workflow_stage = state.get("workflow_stage")

    # ── Role guard — before the lease lookup or any question ──────────────
    creating = not sr_id and workflow_stage in (None, STAGE_CREATE)
    if creating and not can_create(state.get("auth_context")):
        return _refuse_create(state)

    # ── Explicit UI actions ────────────────────────────────────────────────
    if action in _CONFIRM_ACTIONS:
        return {"confirmation_status": "CONFIRMED", "status": "IN_PROGRESS"}
    if action in ("cancel_sr", "cancel", "cancel_create_sr"):
        return _cancel_prompt(sr_id)
    if action == "confirm_cancel_sr":
        if sr_id:
            await _cancel_on_platform(sr_id)
        return _cancelled_patch(sr_id)
    if action == "decline_cancel_sr":
        return dict(_STILL_ACTIVE)

    # ── CANCEL_PENDING text handling ───────────────────────────────────────
    if confirmation_status == "CANCEL_PENDING":
        if _contains_phrase(user_message, _CONFIRM_PHRASES):
            if sr_id:
                await _cancel_on_platform(sr_id)
            return _cancelled_patch(sr_id)
        if _contains_phrase(user_message, _REJECT_PHRASES):
            return dict(_STILL_ACTIVE)
        return {
            "status": "WAITING_FOR_USER",
            "response_message": (
                "Please reply **yes** to confirm cancellation, or **no** to keep the request."
            ),
        }

    # ── Text cancel / restart ──────────────────────────────────────────────
    if _contains_phrase(user_message, _CANCEL_SR_PHRASES):
        return _cancel_prompt(sr_id)
    if _contains_phrase(user_message, _CANCEL_WORKFLOW_PHRASES):
        patch = _clear_all_state()
        patch["response_message"] = (
            "Sure — I've cleared everything. What would you like to do next?"
        )
        patch["response_ui"] = {
            "type": "message",
            "verbatim": True,
            "reset_conversation": True,
        }
        return patch

    # ── Confirmation card reply ────────────────────────────────────────────
    if confirmation_status == "PENDING":
        if _contains_phrase(user_message, _CONFIRM_PHRASES):
            return {"confirmation_status": "CONFIRMED", "status": "IN_PROGRESS"}
        if _contains_phrase(user_message, _REJECT_PHRASES):
            return {"confirmation_status": "REJECTED", "status": "IN_PROGRESS"}
        return {
            "response_message": (
                "Please reply with 'yes' to confirm and submit, "
                "or tell me what you'd like to change."
            ),
            "status": "WAITING_FOR_USER",
        }

    # ── A new create after a finished one starts clean ────────────────────
    if (
        state.get("intent") == INTENT_CREATE
        and (workflow_stage in TERMINAL_STAGES or state.get("status") == "FAILED")
    ):
        log.info("ceiling_entry.stale_request_cleared", workflow_stage=workflow_stage)
        fresh: ServiceRequestState = {  # type: ignore[typeddict-item]
            **dict(state),
            "workflow_stage": None,
            "collected_data": {},
            "backend_refs": {},
            "documents": [],
        }
        cleared = {
            "confirmation_status": None,
            "confirmation_required": False,
            "extracted_fields": {},
            "missing_fields": [],
            "validation_errors": [],
            "selected_lease": None,
            "lease_matches": [],
            "backend_refs": {},
            "documents": [],
            "response_ui": {},
            "active_field": None,
        }
        resolved = detect_sub_category(user_message)
        if resolved is None:
            return {**cleared, **_ask_sub_category(fresh)}
        inspection_done_by = await resolve_inspection_done_by(resolved)
        return {**cleared, **_resolved_patch(fresh, resolved, inspection_done_by)}

    # ── Sub-category: resolve once, then the pipeline takes over ──────────
    # On the first turn only the user's own words count. The supervisor LLM may
    # have filled a sub-category for a bare "ceiling inspection"; that is a
    # guess, and the PRD asks rather than guesses.
    current = state.get("sub_category")
    first_turn = not workflow_stage and not sr_id
    if first_turn or not is_resolved_sub_category(current):
        from_action = _SELECT_SUBCAT_ACTIONS.get(action)
        resolved = from_action or detect_sub_category(user_message)
        if resolved is None:
            return _ask_sub_category(state)
        inspection_done_by = await resolve_inspection_done_by(resolved)
        return _resolved_patch(state, resolved, inspection_done_by)

    # ── Stage-pin a fresh request ─────────────────────────────────────────
    if not workflow_stage:
        collected = dict(state.get("collected_data") or {})
        collected["inspection_done_by"] = await resolve_inspection_done_by(current)
        log.info(
            "ceiling_entry.routing_to_create",
            sub_category=current,
            label=sub_category_label(current),
        )
        return {"workflow_stage": STAGE_CREATE, "collected_data": collected}

    return {}
