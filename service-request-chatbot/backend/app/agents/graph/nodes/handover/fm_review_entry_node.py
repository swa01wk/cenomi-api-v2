"""Entry boundary for the FM_REVIEW workflow stage.

Responsibilities
----------------
- Validate that the caller has the FM_REVIEW role (FM_MANAGER or OPERATIONS).
- Handle ``action_override`` values that are specific to FM_REVIEW:
    - ``save_fm_progress``  → set fm_action = "save_progress"
    - ``approve_fm_review`` → set fm_action = "approve"
    - ``reject_fm_review``  → set status = WAITING_FOR_USER with rejection message
    - ``cancel_update``     → reset to response_generation
    - ``upload_document``   → short-circuit (handled by upload route)
- When no action_override is present, fall through to the shared field_extraction
  pipeline (same as handover_entry) so text-based input is parsed normally.

Non-responsibilities
--------------------
- MUST NOT call the LLM.
- MUST NOT submit or build the payload.
- MUST NOT collect or validate form fields.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import structlog

from app.agents.graph.state import ServiceRequestState
from app.agents.services.review_decorations_service import (
    build_document_upload_ask_response,
    is_fm_review_ready,
)
from app.observability.decorators import trace_node

log = structlog.get_logger(__name__)
logger = logging.getLogger(__name__)

# Roles authorised to act in FM_REVIEW
_FM_ALLOWED_ROLES: frozenset[str] = frozenset({"FM_MANAGER", "OPERATIONS"})

# Text confirm phrases when confirmation_status is PENDING (card shown without
# a stage-specific confirmAction, or user typed instead of clicking).
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
        "approve",
        "ok",
        "okay",
        "sure",
        "go ahead",
        "looks good",
    }
)

_REJECT_PHRASES: frozenset[str] = frozenset(
    {
        "no",
        "nope",
        "cancel",
        "change",
        "edit",
        "modify",
        "wrong",
        "incorrect",
    }
)


def _contains_phrase(message: str, phrases: frozenset[str]) -> bool:
    lowered = message.lower()
    return any(
        bool(re.search(r"\b" + re.escape(phrase) + r"\b", lowered))
        for phrase in phrases
    )


@trace_node("fm_review_entry", "AGENT")
async def fm_review_entry_node(state: ServiceRequestState) -> dict[str, Any]:
    """Parse FM_REVIEW entry intent and validate role.

    Reads from state
    ----------------
    ``action_override``  — UI-initiated action (takes priority over text).
    ``backend_refs``     — checked for ``user_role`` to enforce stage permissions.

    Writes to state
    ---------------
    ``backend_refs["fm_action"]`` — ``"save_progress"`` or ``"approve"`` when set.
    ``status``                    — ``"WAITING_FOR_USER"`` on role denial or rejection.
    ``response_message``          — human-readable message when short-circuiting.
    """
    action_override: str | None = state.get("action_override")
    backend_refs: dict[str, Any] = dict(state.get("backend_refs") or {})
    user_role: str | None = backend_refs.get("user_role")
    user_message: str = state.get("user_message") or ""
    confirmation_status: str | None = state.get("confirmation_status")

    # ── 0. Role guard ──────────────────────────────────────────────────────
    if user_role and user_role.upper() not in _FM_ALLOWED_ROLES:
        log.warning(
            "fm_review_entry.role_denied",
            user_role=user_role,
        )
        return {
            "status": "WAITING_FOR_USER",
            "response_message": (
                "You do not have permission to perform FM review actions. "
                "This stage requires FM Manager or Operations role."
            ),
        }

    # ── 1. Explicit UI actions ─────────────────────────────────────────────
    if action_override == "save_fm_progress":
        log.info("fm_review_entry.action_save_progress")
        backend_refs["fm_action"] = "save_progress"
        return {"backend_refs": backend_refs, "status": "IN_PROGRESS"}

    if action_override == "approve_fm_review":
        collected = dict(state.get("collected_data") or {})
        documents = list(state.get("documents") or [])
        if not is_fm_review_ready(collected, documents):
            log.info("fm_review_entry.approve_blocked_missing_docs")
            return build_document_upload_ask_response(state)
        log.info("fm_review_entry.action_approve")
        backend_refs["fm_action"] = "approve"
        return {"backend_refs": backend_refs, "status": "IN_PROGRESS"}

    # ── Single-turn confirm actions — set action AND mark CONFIRMED in one turn ──
    # Required because fm_action is not persisted across turns by the state
    # service; a generic "confirm" on Turn N+1 would find fm_action empty and
    # loop back to field_extraction instead of reaching the payload builder.
    if action_override == "confirm_fm_save":
        log.info("fm_review_entry.action_confirm_fm_save")
        backend_refs["fm_action"] = "save_progress"
        return {"backend_refs": backend_refs, "confirmation_status": "CONFIRMED", "status": "IN_PROGRESS"}

    if action_override in ("confirm_fm_approve", "confirm"):
        collected = dict(state.get("collected_data") or {})
        documents = list(state.get("documents") or [])
        if not is_fm_review_ready(collected, documents):
            log.info("fm_review_entry.confirm_approve_blocked_missing_docs")
            return build_document_upload_ask_response(state)
        log.info(
            "fm_review_entry.action_confirm_approve",
            action=action_override,
        )
        backend_refs["fm_action"] = "approve"
        return {"backend_refs": backend_refs, "confirmation_status": "CONFIRMED", "status": "IN_PROGRESS"}

    if action_override in ("cancel", "cancel_fm", "cancel_update"):
        # Soft cancel: drop pending confirmation / staged fm_action only.
        # Do NOT clear collected_data, documents, sr_id, or workflow_stage —
        # that felt like a full workflow reload and forced re-collection.
        log.info("fm_review_entry.action_cancel", action=action_override)
        backend_refs.pop("fm_action", None)
        message = (
            "Update cancelled. Your FM review progress is still saved. "
            "What would you like to do next?"
            if action_override == "cancel_update"
            else (
                "Action cancelled. Your FM review progress is still saved. "
                "You can save progress, approve, or continue editing."
            )
        )
        return {
            "confirmation_status": "REJECTED",
            "status": "WAITING_FOR_USER",
            "backend_refs": backend_refs,
            "response_message": message,
        }

    if action_override == "reject_fm_review":
        # Platform reject PATCH contract is not wired yet — keep as explicit stub.
        log.info("fm_review_entry.action_reject")
        return {
            "status": "WAITING_FOR_USER",
            "response_message": (
                "Reject via the AI assistant is not available yet. "
                "Please use the Action menu on the Service Request form to reject, "
                "or ask the product team to enable AI reject once the platform "
                "reject API contract is confirmed."
            ),
        }

    if action_override == "upload_document":
        log.info("fm_review_entry.action_upload_document")
        return {
            "status": "WAITING_FOR_USER",
            "response_message": "Please use the upload interface to attach FM review documents.",
        }

    # ── 2. Text confirmation while card is PENDING ─────────────────────────
    # FM turns route through fm_review_entry (not handover_entry), so yes/submit
    # must be handled here or the card loops forever.
    if confirmation_status == "PENDING":
        if _contains_phrase(user_message, _CONFIRM_PHRASES):
            collected = dict(state.get("collected_data") or {})
            documents = list(state.get("documents") or [])
            if not is_fm_review_ready(collected, documents):
                log.info("fm_review_entry.pending_confirm_blocked_missing_docs")
                return build_document_upload_ask_response(state)
            log.info("fm_review_entry.confirmation_accepted_text")
            # User confirmed the review card via the primary Submit button /
            # "yes" text — this always means Approve. Save Progress is a
            # separate explicit chip (`save_fm_progress` / `confirm_fm_save`).
            # Overwrite any stale fm_action from a prior turn so the payload
            # builder + api_submission take the approve branch.
            backend_refs["fm_action"] = "approve"
            return {
                "backend_refs": backend_refs,
                "confirmation_status": "CONFIRMED",
                "status": "IN_PROGRESS",
            }
        if _contains_phrase(user_message, _REJECT_PHRASES):
            log.info("fm_review_entry.confirmation_rejected_text")
            return {
                "confirmation_status": "REJECTED",
                "status": "IN_PROGRESS",
            }

    # ── 3. No action override — fall through to field_extraction ──────────
    return {}
