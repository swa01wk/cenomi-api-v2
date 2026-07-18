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
- Detect explicit cancel / workflow-switch phrases and clear ``active_agent``
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


def _clear_all_state() -> dict:
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
        "response_ui": {"cancelled": True},
        "status": "WAITING_FOR_USER",
    }


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
    """CREATE_SR / SR_CREATED optional MM upload offer/upload wait. Returns patch or None."""
    stage = state.get("workflow_stage") or "CREATE_SR"
    if stage != "SR_CREATED":
        return None
    if state.get("confirmation_status") == "PENDING":
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

    # ── 0. Explicit UI action — takes priority over text parsing ───────────
    if action_override in ("confirm", "confirm_create_sr"):
        log.info("handover_entry.action_override_confirm")
        return {"confirmation_status": "CONFIRMED"}

    # When the confirmation card is shown (PENDING) and the user clicks the generic
    # "cancel" button (not "cancel_sr"), treat it as rejecting the confirmation card
    # rather than cancelling the entire SR.  This preserves collected_data so the
    # user can make corrections without losing their answers.
    if action_override == "cancel" and confirmation_status == "PENDING":
        log.info("handover_entry.cancel_rejects_pending_confirmation")
        return {"confirmation_status": "REJECTED", "status": "IN_PROGRESS"}

    # cancel_sr: request confirmation before clearing state
    if action_override in ("cancel_sr", "cancel", "cancel_create_sr"):
        log.info("handover_entry.action_override_cancel_sr")
        backend_refs: dict[str, Any] = state.get("backend_refs") or {}
        sr_id = backend_refs.get("sr_id")
        if not sr_id:
            # No SR created yet — clear state immediately
            patch = _clear_all_state()
            patch["response_message"] = (
                "Your handover request has been cancelled. "
                "What would you like to do next?"
            )
            return patch
        # SR exists — ask for confirmation before cancelling
        return {
            "confirmation_status": "CANCEL_PENDING",
            "status": "WAITING_FOR_USER",
            "response_message": (
                f"Are you sure you want to cancel Service Request **{sr_id}**? "
                "This will mark it as cancelled on the platform. "
                "Reply **yes** to confirm, or **no** to go back."
            ),
        }

    # confirm_cancel_sr: user confirmed — patch platform + clear state
    if action_override == "confirm_cancel_sr":
        log.info("handover_entry.action_override_confirm_cancel_sr")
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
        patch["response_ui"] = {"cancelled": True, "workflow_stage": "CANCELLED"}
        patch["response_message"] = (
            f"Service Request **{sr_id or 'N/A'}** has been cancelled. "
            "What would you like to do next?"
        )
        return patch

    # decline_cancel_sr: user chose not to cancel — restore previous state
    if action_override == "decline_cancel_sr":
        log.info("handover_entry.action_override_decline_cancel_sr")
        return {
            "confirmation_status": None,
            "status": "IN_PROGRESS",
            "response_message": (
                "No problem — your request is still active. "
                "What would you like to do next?"
            ),
        }

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
            patch["response_ui"] = {"cancelled": True, "workflow_stage": "CANCELLED"}
            patch["response_message"] = (
                f"Service Request **{sr_id or 'N/A'}** has been cancelled. "
                "What would you like to do next?"
            )
            return patch

        if _contains_phrase(user_message, _REJECT_PHRASES):
            log.info("handover_entry.cancel_declined_by_text")
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
    # _CANCEL_WORKFLOW_PHRASES are for full restart; _CANCEL_SR_PHRASES trigger
    # the SR-cancel confirmation flow same as the UI cancel_sr action.
    if _contains_phrase(user_message, _CANCEL_SR_PHRASES):
        log.info(
            "handover_entry.text_cancel_sr",
            user_message=user_message[:120],
        )
        backend_refs = state.get("backend_refs") or {}
        sr_id = backend_refs.get("sr_id")
        if not sr_id:
            patch = _clear_all_state()
            patch["response_message"] = (
                "Your handover request has been cancelled. "
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
        log.info(
            "handover_entry.workflow_cancel",
            user_message=user_message[:120],
        )
        # Clear ALL workflow-specific state so the next turn starts with a
        # completely clean slate.
        patch = _clear_all_state()
        patch["response_message"] = (
            "Sure — I've cleared everything. What would you like to do next?"
        )
        patch["response_ui"] = {}  # restart doesn't set cancelled=True
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

        # Ambiguous — re-show the confirmation card on the next response_generation pass.
        log.info(
            "handover_entry.confirmation_ambiguous",
            user_message=user_message[:120],
        )
        return {
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
    # If a previous SR reached SR_CREATED / SR_COMPLETED / CANCELLED, a fresh
    # CREATE_RDD_SERVICE_REQUEST intent must begin with a clean slate.  The
    # stale sr_id in backend_refs would otherwise cause sr_status_sync_node to
    # load the old SR and route directly into FM_REVIEW.
    if state.get("intent") == "CREATE_RDD_SERVICE_REQUEST":
        terminal_stages = {"SR_CREATED", "SR_COMPLETED", "CANCELLED"}
        if state.get("workflow_stage") in terminal_stages:
            log.info(
                "handover_entry.stale_sr_cleared",
                workflow_stage=state.get("workflow_stage"),
            )
            return _clear_all_state()

    if state.get("active_agent") == "rdd_agent" and not state.get("workflow_stage"):
        return {"workflow_stage": "CREATE_SR"}

    return {}
