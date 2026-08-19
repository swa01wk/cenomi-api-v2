"""Boundary between free-form chat and the structured handover workflow.

Responsibilities
----------------
- Act as the first node in the handover data pipeline for every turn where
  ``active_agent`` is already set (the supervisor/registry have already run on
  a prior turn).
- Detect the user's confirmation response when ``confirmation_status == "PENDING"``:
    - Explicit YES phrases → set ``confirmation_status = "CONFIRMED"``
    - Explicit NO / change phrases → set ``confirmation_status = "REJECTED"``
    - Ambiguous input → leave confirmation_status unchanged (re-show card)
- Detect Cancel on the review card (and cancel_sr / typed cancel) and ask
  Yes/No before clearing. Yes clears the lease and prompts for a new one;
  No re-shows the review card.
- Detect explicit workflow-switch phrases and clear ``active_agent``
  so the next routing step re-enters through the supervisor.

Non-responsibilities
--------------------
- MUST NOT collect or validate form fields.
- MUST NOT call the LLM.
- MUST NOT submit service requests.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import structlog

from app.agents.graph.state import ServiceRequestState
from app.agents.services.service_request_api_service import get_service_request_api_service
from app.agents.services.review_decorations_service import (
    MM_OPTIONAL_AWAITING_CHOICE,
    MM_OPTIONAL_AWAITING_UPLOAD,
    MM_OPTIONAL_DONE,
    MM_OPTIONAL_UPLOAD_STATUS_KEY,
    build_mm_optional_upload_waiting_response,
)
from app.observability.decorators import trace_node

log = structlog.get_logger(__name__)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Phrase sets — kept intentionally broad so users don't have to type exactly
# ---------------------------------------------------------------------------

_CONFIRM_PHRASES: frozenset[str] = frozenset(
    {
        "yes",
        "yep",
        "yeah",
        "yup",
        "confirm",
        "confirmed",
        "submit",
        "proceed",
        "correct",
        "looks good",
        "that's correct",
        "thats correct",
        "go ahead",
        "approve",
        "ok",
        "okay",
        "sure",
        "absolutely",
        "agree",
    }
)

_REJECT_PHRASES: frozenset[str] = frozenset(
    {
        "no",
        "nope",
        "nah",
        "cancel",
        "change",
        "update",
        "edit",
        "modify",
        "wrong",
        "incorrect",
        "not right",
        "that's wrong",
        "thats wrong",
        "fix",
        "correct it",
        "i want to change",
        "let me change",
    }
)

_CANCEL_WORKFLOW_PHRASES: frozenset[str] = frozenset(
    {
        "start over",
        "restart",
        "new request",
        "different request",
        "begin again",
        "reset",
    }
)

# Signals that indicate the user wants to switch to the "already trading" path (CREATE_HANDED_OVER_SR).
_ALREADY_TRADING_SIGNALS: tuple[str, ...] = (
    "already trading",
    "already traded",
    "already handovered",
    "already open",
    "already handed over",
)

# Signals that indicate the user wants to switch to the standard inspection path (CREATE_SR).
_STANDARD_HANDOVER_SIGNALS: tuple[str, ...] = (
    "standard inspection",
    "standard handover",
    "regular handover",
    "normal handover",
    "initiate handover",
    "new handover",
)

# Backend-derived lease fields preserved when switching paths mid-session.
_LEASE_PRESERVE_KEYS: frozenset[str] = frozenset(
    {
        "tenant_profile_id",
        "property_id",
        "lease_code",
        "lease_id",
        "brand_id",
        "mall",
        "brand",
        "lease",
        "unit_codes",
        "city",
        "contracted_area",
        "lease_brand_mall",
        "contract_id",
    }
)

_CANCEL_SR_PHRASES: frozenset[str] = frozenset(
    {
        "cancel",
        "cancel request",
        "cancel sr",
        "cancel this request",
        "cancel the request",
        "cancel handover",
        "i want to cancel",
        "please cancel",
        "abort",
    }
)

_CONTINUE_OPTIONAL_UPLOAD_PHRASES: frozenset[str] = frozenset(
    {
        "continue",
        "done",
        "skip",
        "next",
        "no thanks",
        "continue without uploading",
        "skip upload",
        "i'm done",
        "im done",
    }
)

_ACCEPT_OPTIONAL_UPLOAD_ACTIONS = frozenset({"accept_optional_upload"})
_SKIP_OPTIONAL_UPLOAD_ACTIONS = frozenset({"skip_optional_upload"})
_CONTINUE_OPTIONAL_UPLOAD_ACTIONS = frozenset({"continue_optional_upload"})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _clear_all_state(*, cancelled_banner: bool = True) -> dict:
    """Return state patch that zeroes all workflow-related fields after cancel."""
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
        "response_ui": (
            {"cancelled": True} if cancelled_banner else {"type": "message", "verbatim": True}
        ),
        "status": "WAITING_FOR_USER",
    }


_CLEARED_REQUEST_MESSAGE = (
    "The handover request has been cleared. Would you like to continue?"
)

_CREATE_HANDOVER_SR_CHIP: dict[str, Any] = {
    "kind": "labeled_options",
    "options": [
        {
            "label": "Create handover SR",
            "value": "I want to create a Handover service request",
            "action": "create_handover_sr",
        }
    ],
}


def _cleared_conversation_ui(*, cancelled: bool = False, workflow_stage: str | None = None) -> dict[str, Any]:
    """UI payload that tells the frontend to wipe chat history and show only this turn."""
    ui: dict[str, Any] = {
        "type": "message",
        "verbatim": True,
        "reset_conversation": True,
        "suggestions": _CREATE_HANDOVER_SR_CHIP,
    }
    if cancelled:
        ui["cancelled"] = True
    if workflow_stage:
        ui["workflow_stage"] = workflow_stage
    return ui


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


def _ack_milestone(state: ServiceRequestState) -> dict[str, Any]:
    """User chose Continue on an already-confirmed milestone — keep the lease."""
    collected = dict(state.get("collected_data") or {})
    collected[_MILESTONE_ACK_KEY] = True
    log.info("handover_entry.continue_milestone")
    return {
        "collected_data": collected,
        "confirmation_status": None,
        "status": "IN_PROGRESS",
    }


def _clear_draft_for_milestone_cancel() -> dict[str, Any]:
    """Cancel on the already-confirmed prompt: wipe draft, no platform PATCH."""
    patch = _clear_all_state(cancelled_banner=False)
    patch["response_ui"] = _cleared_conversation_ui()
    patch["response_message"] = _CLEARED_REQUEST_MESSAGE
    return patch


_CANCEL_YES_NO_SUGGESTIONS: dict[str, Any] = {
    "kind": "labeled_options",
    "options": [
        {"label": "Yes", "value": "yes", "action": "confirm_cancel_sr"},
        {"label": "No", "value": "no", "action": "decline_cancel_sr"},
    ],
}


def _ask_cancel_confirmation(sr_id: str | None) -> dict[str, Any]:
    """Ask Yes/No before clearing a draft or cancelling a submitted SR."""
    if sr_id:
        message = (
            f"Are you sure you want to cancel Service Request **{sr_id}**? "
            "This will mark it as cancelled on the platform. "
            "Reply **yes** to confirm, or **no** to go back."
        )
    else:
        message = (
            "Are you sure you want to cancel this handover request? "
            "This will clear the current lease and all collected details. "
            "Reply **yes** to confirm, or **no** to go back to the review."
        )
    return {
        "confirmation_status": "CANCEL_PENDING",
        "status": "WAITING_FOR_USER",
        "response_message": message,
        "response_ui": {"type": "message", "verbatim": True},
        "response_decorations": {"suggestions": _CANCEL_YES_NO_SUGGESTIONS},
    }


def _restore_preview_after_decline(state: ServiceRequestState) -> dict[str, Any]:
    """Keep collected data and re-show the confirmation card when the user says No."""
    from app.agents.graph.nodes.handover.confirmation_node import _build_confirmation_card
    from app.agents.schemas.handover_schema import OPTIONAL_FIELDS, get_missing_fields

    collected = dict(state.get("collected_data") or {})
    workflow_stage: str = state.get("workflow_stage") or "CREATE_SR"
    backend_refs: dict[str, Any] = dict(state.get("backend_refs") or {})
    missing = get_missing_fields(workflow_stage, collected)
    non_optional_missing = [f for f in missing if f not in OPTIONAL_FIELDS]
    if non_optional_missing:
        return {
            "confirmation_status": None,
            "status": "IN_PROGRESS",
            "response_message": (
                "No problem — your request is still active. Let's continue."
            ),
        }

    card = _build_confirmation_card(collected, workflow_stage, backend_refs)
    card["verbatim"] = True
    return {
        "confirmation_status": "PENDING",
        "confirmation_required": True,
        "status": "WAITING_FOR_USER",
        "response_ui": card,
        "response_message": (
            "No problem — here is your request again. "
            "Review the details and confirm to submit, or cancel."
        ),
        "missing_fields": [],
        "active_field": None,
    }


async def _execute_confirmed_cancel(state: ServiceRequestState) -> dict[str, Any]:
    """Clear the draft (and PATCH the platform SR when one exists) after Yes."""
    backend_refs = state.get("backend_refs") or {}
    sr_id = backend_refs.get("sr_id")
    if sr_id:
        try:
            svc = get_service_request_api_service()
            await svc.patch_service_request(sr_id, {"status": "CANCELLED"})
            log.info("handover_entry.sr_cancelled_on_platform", sr_id=sr_id)
        except Exception:
            log.warning(
                "handover_entry.cancel_patch_failed", sr_id=sr_id, exc_info=True
            )
        patch = _clear_all_state()
        patch["workflow_stage"] = "CANCELLED"
        patch["response_ui"] = _cleared_conversation_ui(
            cancelled=True, workflow_stage="CANCELLED"
        )
        patch["response_message"] = _CLEARED_REQUEST_MESSAGE
        return patch

    patch = _clear_all_state(cancelled_banner=False)
    patch["response_ui"] = _cleared_conversation_ui()
    patch["response_message"] = _CLEARED_REQUEST_MESSAGE
    return patch


def _contains_phrase(message: str, phrases: frozenset[str]) -> bool:
    """Return True if *message* contains any phrase as a whole word/phrase.

    Uses word-boundary matching so short words like "no" do not accidentally
    match inside longer words such as "not", "nothing", or "visible".
    Multi-word phrases (e.g. "let me change") are matched with surrounding
    word boundaries so they are not triggered by sub-phrase occurrences.
    """
    lowered = message.lower()
    return any(
        bool(re.search(r"\b" + re.escape(phrase) + r"\b", lowered))
        for phrase in phrases
    )


def _handle_mm_optional_upload_gate(
    state: ServiceRequestState,
    *,
    user_message: str,
    action_override: str | None,
) -> dict[str, Any] | None:
    """CREATE_SR / CREATE_HANDED_OVER_SR / SR_CREATED MM optional upload gate.

    Handles Continue/skip/upload actions for all three stages so the
    confirmation_node's pre-submission gate (CREATE_SR + CREATE_HANDED_OVER_SR)
    and the post-submission Path A gate (SR_CREATED) all funnel through the
    same status transitions.
    """
    stage = state.get("workflow_stage") or "CREATE_SR"
    if stage not in ("CREATE_SR", "SR_CREATED", "CREATE_HANDED_OVER_SR"):
        return None
    if state.get("confirmation_status") == "PENDING":
        return None
    # SR_CREATED with sr_id → FM/DD opening an existing SR; upload offer is
    # irrelevant. CREATE_SR / CREATE_HANDED_OVER_SR have no sr_id yet.
    backend_refs = state.get("backend_refs") or {}
    if stage == "SR_CREATED" and backend_refs.get("sr_id"):
        return None

    collected = dict(state.get("collected_data") or {})
    mm_status = collected.get(MM_OPTIONAL_UPLOAD_STATUS_KEY)
    if mm_status not in (MM_OPTIONAL_AWAITING_CHOICE, MM_OPTIONAL_AWAITING_UPLOAD):
        return None

    if (
        action_override in _ACCEPT_OPTIONAL_UPLOAD_ACTIONS
        or (
            mm_status == MM_OPTIONAL_AWAITING_CHOICE
            and user_message
            and _contains_phrase(user_message, _CONFIRM_PHRASES)
        )
    ):
        collected[MM_OPTIONAL_UPLOAD_STATUS_KEY] = MM_OPTIONAL_AWAITING_UPLOAD
        log.info("handover_entry.mm_optional_upload_accepted")
        return build_mm_optional_upload_waiting_response(state, collected=collected)

    if (
        action_override in _SKIP_OPTIONAL_UPLOAD_ACTIONS
        or (
            mm_status == MM_OPTIONAL_AWAITING_CHOICE
            and user_message
            and _contains_phrase(user_message, _REJECT_PHRASES)
        )
    ):
        collected[MM_OPTIONAL_UPLOAD_STATUS_KEY] = MM_OPTIONAL_DONE
        log.info("handover_entry.mm_optional_upload_skipped")
        return {
            "collected_data": collected,
            "status": "IN_PROGRESS",
            "response_decorations": {},
            "response_ui": {},
        }

    if (
        action_override in _CONTINUE_OPTIONAL_UPLOAD_ACTIONS
        or (
            mm_status == MM_OPTIONAL_AWAITING_UPLOAD
            and user_message
            and _contains_phrase(user_message, _CONTINUE_OPTIONAL_UPLOAD_PHRASES)
        )
    ):
        collected[MM_OPTIONAL_UPLOAD_STATUS_KEY] = MM_OPTIONAL_DONE
        log.info("handover_entry.mm_optional_upload_continued")
        return {
            "collected_data": collected,
            "status": "IN_PROGRESS",
            "response_decorations": {},
            "response_ui": {},
        }

    # Still waiting — re-emit offer or upload UI; do not advance to missing fields.
    log.info(
        "handover_entry.mm_optional_upload_waiting",
        mm_status=mm_status,
        user_message=user_message[:120],
    )
    return build_mm_optional_upload_waiting_response(state, collected=collected)


# ---------------------------------------------------------------------------
# Node
# ---------------------------------------------------------------------------


@trace_node("handover_entry", "AGENT")
async def handover_entry_node(state: ServiceRequestState) -> dict[str, Any]:
    """Parse user intent at the boundary of the structured handover workflow.

    Reads from state
    ----------------
    ``confirmation_status``  — if PENDING, interpret the user's message as
                               a yes/no confirmation response.
    ``active_agent``         — if set and user wants to restart, clear it.
    ``user_message``         — the raw user input to parse.

    Writes to state
    ---------------
    ``confirmation_status``  — CONFIRMED or REJECTED when a clear signal is
                               detected; unchanged otherwise.
    ``active_agent``         — cleared (None) when the user wants to restart.
    """
    user_message: str = state.get("user_message") or ""
    confirmation_status: str | None = state.get("confirmation_status")
    action_override: str | None = state.get("action_override")  # type: ignore[assignment]

    if action_override == "continue_milestone":
        return _ack_milestone(state)

    if action_override == "cancel_milestone":
        log.info("handover_entry.action_override_cancel_milestone")
        return _clear_draft_for_milestone_cancel()

    if state.get("confirmation_status") == "MILESTONE_PENDING":
        if _contains_phrase(user_message, _CONTINUE_MILESTONE_PHRASES):
            return _ack_milestone(state)
        if _contains_phrase(user_message, _CANCEL_SR_PHRASES) or _contains_phrase(
            user_message, frozenset({"cancel"})
        ):
            log.info("handover_entry.milestone_cancel_by_text")
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
    if action_override in ("confirm", "confirm_create_sr"):
        log.info("handover_entry.action_override_confirm")
        return {"confirmation_status": "CONFIRMED"}

    # Cancel on the review card (and cancel_sr / cancel_create_sr) always asks
    # Yes/No first.  It does not mean "I want to make changes" — that is typed
    # as a field correction while the card is still showing.
    if action_override in ("cancel_sr", "cancel", "cancel_create_sr"):
        log.info("handover_entry.action_override_cancel_sr")
        backend_refs: dict[str, Any] = state.get("backend_refs") or {}
        return _ask_cancel_confirmation(backend_refs.get("sr_id"))

    # confirm_cancel_sr: user confirmed — patch platform + clear state
    if action_override == "confirm_cancel_sr":
        log.info("handover_entry.action_override_confirm_cancel_sr")
        return await _execute_confirmed_cancel(state)

    # decline_cancel_sr: user chose not to cancel — re-show the review card
    if action_override == "decline_cancel_sr":
        log.info("handover_entry.action_override_decline_cancel_sr")
        return _restore_preview_after_decline(state)

    _HANDOVER_TYPE_ACTIONS: dict[str, str] = {
        "select_handover_type_already_trading": "already_trading",
        "select_handover_type_initiate_handover": "initiate_handover",
    }
    if action_override in _HANDOVER_TYPE_ACTIONS:
        value = _HANDOVER_TYPE_ACTIONS[action_override]
        log.info("handover_entry.action_select_handover_type", value=value)
        collected = dict(state.get("collected_data") or {})
        collected["already_handovered"] = value
        patch: dict[str, Any] = {"collected_data": collected, "status": "IN_PROGRESS"}
        if value == "already_trading":
            patch["workflow_stage"] = "CREATE_HANDED_OVER_SR"
            patch["sub_category"] = "HANDED_OVER"
        return patch

    _INSPECTION_DONE_BY_ACTIONS: dict[str, str] = {
        "select_inspection_done_by_fm_manager": "FM_MANAGER",
        "select_inspection_done_by_operations": "OPERATIONS",
    }
    if action_override in _INSPECTION_DONE_BY_ACTIONS:
        value = _INSPECTION_DONE_BY_ACTIONS[action_override]
        log.info("handover_entry.action_select_inspection_done_by", value=value)
        collected = dict(state.get("collected_data") or {})
        collected["inspection_done_by"] = value
        return {"collected_data": collected, "status": "IN_PROGRESS"}

    if action_override == "upload_document":
        log.info("handover_entry.action_override_upload_document")
        return {
            "status": "WAITING_FOR_USER",
            "response_message": "Please use the upload interface to attach your document.",
        }

    if action_override == "cancel_update":
        log.info("handover_entry.action_override_cancel_update")
        return {
            "status": "WAITING_FOR_USER",
            "response_message": "Update cancelled. What would you like to do next?",
        }

    # ── 1. CANCEL_PENDING confirmation handling ────────────────────────────
    if state.get("confirmation_status") == "CANCEL_PENDING":
        if _contains_phrase(user_message, _CONFIRM_PHRASES):
            log.info("handover_entry.cancel_confirmed_by_text")
            return await _execute_confirmed_cancel(state)

        if _contains_phrase(user_message, _REJECT_PHRASES):
            log.info("handover_entry.cancel_declined_by_text")
            return _restore_preview_after_decline(state)

        return {
            "status": "WAITING_FOR_USER",
            "response_message": (
                "Please reply **yes** to confirm cancellation, or **no** to keep the request."
            ),
            "response_ui": {"type": "message", "verbatim": True},
            "response_decorations": {"suggestions": _CANCEL_YES_NO_SUGGESTIONS},
        }

    # ── 2. Workflow cancel / restart ───────────────────────────────────────
    # _CANCEL_WORKFLOW_PHRASES are for full restart; _CANCEL_SR_PHRASES trigger
    # the SR-cancel confirmation flow same as the UI cancel_sr action.
    # Guard: only treat text-based cancel as intentional when there is already
    # some workflow state (collected_data non-empty or workflow_stage set).
    # This prevents packed messages like "Description: Cancel flow test …" from
    # accidentally triggering the cancel path on the very first turn.
    _has_workflow_state = bool(
        state.get("workflow_stage") or state.get("collected_data")
    )
    if _has_workflow_state and _contains_phrase(user_message, _CANCEL_SR_PHRASES):
        log.info(
            "handover_entry.text_cancel_sr",
            user_message=user_message[:120],
        )
        backend_refs = state.get("backend_refs") or {}
        return _ask_cancel_confirmation(backend_refs.get("sr_id"))

    if _contains_phrase(user_message, _CANCEL_WORKFLOW_PHRASES):
        log.info(
            "handover_entry.workflow_cancel",
            user_message=user_message[:120],
        )
        lowered = user_message.lower()

        # Preserve lease-resolution data so the new path doesn't re-ask for lease.
        existing_collected = dict(state.get("collected_data") or {})
        preserved_lease = {
            k: v for k, v in existing_collected.items() if k in _LEASE_PRESERVE_KEYS
        }

        patch = _clear_all_state()
        patch["response_ui"] = {}  # restart doesn't set cancelled=True

        if any(sig in lowered for sig in _ALREADY_TRADING_SIGNALS):
            # User is switching to Path B (Already Trading / CREATE_HANDED_OVER_SR).
            log.info("handover_entry.switch_to_create_handed_over_sr")
            patch["workflow_stage"] = "CREATE_HANDED_OVER_SR"
            patch["active_agent"] = "rdd_handover_agent"
            patch["sub_category"] = "HANDED_OVER"
            if preserved_lease:
                preserved_lease["already_handovered"] = "already_trading"
                patch["collected_data"] = preserved_lease
                patch["selected_lease"] = state.get("selected_lease")
            patch["response_message"] = (
                "Understood — I've cleared the previous request and switched to the "
                "already trading flow. Please share the actual shop handover date, "
                "actual fitout start date, actual fitout end date, and actual trading date."
            )
        elif any(sig in lowered for sig in _STANDARD_HANDOVER_SIGNALS):
            # User is switching back to Path A (standard CREATE_SR).
            log.info("handover_entry.switch_to_create_sr")
            patch["workflow_stage"] = "CREATE_SR"
            patch["active_agent"] = "rdd_handover_agent"
            patch["sub_category"] = "HANDOVER"
            if preserved_lease:
                patch["collected_data"] = preserved_lease
                patch["selected_lease"] = state.get("selected_lease")
            patch["response_message"] = (
                "Understood — I've cleared the already trading request. Starting a fresh "
                "standard inspection handover. Please provide a description, inspection start "
                "date, end date, and who will perform the inspection."
            )
        else:
            # Generic restart with no path hint.
            patch["response_message"] = (
                "Sure — I've cleared everything. What would you like to do next?"
            )

        return patch

    # ── 3. Confirmation response parsing ──────────────────────────────────
    if confirmation_status == "PENDING":
        if _contains_phrase(user_message, _CONFIRM_PHRASES):
            log.info("handover_entry.confirmation_accepted")
            return {"confirmation_status": "CONFIRMED"}

        if _contains_phrase(user_message, _REJECT_PHRASES):
            log.info("handover_entry.confirmation_rejected")
            return {
                "confirmation_status": "REJECTED",
                "status": "IN_PROGRESS",
            }

        # Ambiguous — re-show the confirmation card without running field extraction.
        # Setting status=WAITING_FOR_USER routes _route_after_handover_entry to
        # response_generation, which preserves the existing response_ui (confirmation
        # card) and adds this clarification message.  NOT routing to field_extraction
        # prevents misinterpretation of ambiguous text (e.g. "Please schedule before
        # the public opening" being parsed as a date update and failing validation).
        log.info(
            "handover_entry.confirmation_ambiguous",
            user_message=user_message[:120],
        )
        return {
            "status": "WAITING_FOR_USER",
            "response_message": (
                "Please reply with 'yes' to confirm and submit, "
                "or tell me what you'd like to change."
            ),
        }

    # ── 4. Optional MM upload offer / upload wait (CREATE_SR / SR_CREATED) ────
    # Handles accept/skip/continue actions and re-emits UI while waiting so
    # missing-field questions do not run with a stray upload card.
    mm_gate = _handle_mm_optional_upload_gate(
        state,
        user_message=user_message,
        action_override=action_override,
    )
    if mm_gate is not None:
        return mm_gate

    # ── 5. Clear stale state when starting a new SR after a terminal stage ────
    # If a previous SR reached a terminal stage, a fresh CREATE intent must begin
    # with a clean slate.  The stale sr_id in backend_refs would otherwise cause
    # sr_status_sync_node to load the old SR and misroute the new request.
    #
    # Partial clear (not _clear_all_state): we keep active_agent and pin
    # workflow_stage so _route_after_handover_entry continues to field_extraction
    # in the same turn, processes the user's message, and returns workflow_stage
    # = CREATE_SR (or CREATE_HANDED_OVER_SR) as the test expects.
    # Full _clear_all_state sets active_agent=None and status=WAITING_FOR_USER,
    # which defers the new SR to the NEXT turn via supervisor, causing the current
    # turn to return workflow_stage='' and break multi-SR session tests.
    _CREATE_SR_INTENTS: frozenset[str] = frozenset(
        {"CREATE_RDD_SERVICE_REQUEST", "CREATE_HANDED_OVER_SR"}
    )
    if state.get("intent") in _CREATE_SR_INTENTS:
        terminal_stages = {"SR_CREATED", "SR_COMPLETED", "CANCELLED", "HANDED_OVER_COMPLETED"}
        if state.get("workflow_stage") in terminal_stages:
            log.info(
                "handover_entry.stale_sr_cleared",
                workflow_stage=state.get("workflow_stage"),
            )
            new_stage = (
                "CREATE_HANDED_OVER_SR"
                if state.get("intent") == "CREATE_HANDED_OVER_SR"
                else "CREATE_SR"
            )
            return {
                "workflow_stage": new_stage,
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

    # ── 6. Stage-pin for rdd_handover_agent on first turn ─────────────────────
    if state.get("active_agent") == "rdd_handover_agent" and not state.get("workflow_stage"):
        # Primary signal: supervisor set sub_category from intent classification
        if state.get("sub_category") == "HANDED_OVER":
            log.info("handover_entry.routing_to_create_handed_over_sr")
            return {"workflow_stage": "CREATE_HANDED_OVER_SR"}
        # Secondary signal: mid-session switch via already_handovered field
        already_handovered = (state.get("collected_data") or {}).get("already_handovered", "")
        if already_handovered in ("already_trading", "already_handovered"):
            log.info(
                "handover_entry.routing_to_create_handed_over_sr_via_field",
                already_handovered=already_handovered,
            )
            return {"workflow_stage": "CREATE_HANDED_OVER_SR"}
        return {"workflow_stage": "CREATE_SR"}

    # Clear any stale WAITING_FOR_USER status left over from the previous turn
    # (e.g. missing_field_node asking for notes). Without this, _route_after_handover_entry
    # would see status="WAITING_FOR_USER" and short-circuit to response_generation,
    # bypassing field_extraction → validation → confirmation_node entirely.
    return {"status": "IN_PROGRESS"}
