"""Entry boundary for the RDD_REVIEW workflow stage.

Responsibilities
----------------
- Validate that the caller has the DD_ENGINEER role.
- Handle ``action_override`` values specific to RDD_REVIEW:
    - ``submit_rdd_report`` → set ``backend_refs["rdd_action"] = "submit"``
    - ``cancel_update``     → reset to response_generation
    - ``upload_document``   → short-circuit (handled by upload route)
- Handle confirm / PENDING text confirmation (same gap FM already fixed).
- Fall through to shared field_extraction pipeline when no action_override.

Non-responsibilities
--------------------
- MUST NOT call the LLM.
- MUST NOT submit or build the payload.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import structlog

from app.agents.graph.state import ServiceRequestState
from app.observability.decorators import trace_node

log = structlog.get_logger(__name__)
logger = logging.getLogger(__name__)

_RDD_ALLOWED_ROLES: frozenset[str] = frozenset({"DD_ENGINEER"})

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
        "ok",
        "okay",
        "go ahead",
        "looks good",
        "please submit",
        "yes please",
        "yes, please submit",
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
        "wait",
        "not yet",
        "don't submit",
        "do not submit",
    }
)


def _contains_phrase(message: str, phrases: frozenset[str]) -> bool:
    text = (message or "").strip().lower()
    if not text:
        return False
    for phrase in phrases:
        if phrase in text:
            return True
    return bool(re.fullmatch(r"(yes|y|ok|okay)[.!]?", text))


@trace_node("rdd_review_entry", "AGENT")
async def rdd_review_entry_node(state: ServiceRequestState) -> dict[str, Any]:
    """Parse RDD_REVIEW entry intent and validate role.

    Reads from state
    ----------------
    ``action_override``  — UI-initiated action.
    ``backend_refs``     — checked for ``user_role``.

    Writes to state
    ---------------
    ``backend_refs["rdd_action"]``  — ``"submit"`` when set via action_override.
    ``status``                      — ``"WAITING_FOR_USER"`` on role denial.
    ``response_message``            — set when short-circuiting.
    """
    action_override: str | None = state.get("action_override")
    backend_refs: dict[str, Any] = dict(state.get("backend_refs") or {})
    user_role: str | None = backend_refs.get("user_role")
    confirmation_status: str | None = state.get("confirmation_status")
    user_message: str = state.get("user_message") or ""

    # ── 0. Role guard ──────────────────────────────────────────────────────
    if user_role and user_role.upper() not in _RDD_ALLOWED_ROLES:
        log.warning("rdd_review_entry.role_denied", user_role=user_role)
        return {
            "status": "WAITING_FOR_USER",
            "response_message": (
                "You do not have permission to perform RDD review actions. "
                "This stage requires the RDD Project Manager role."
            ),
        }

    # ── 1. Explicit UI actions ─────────────────────────────────────────────
    # Continue chip after the mandatory RDD upload (Meeting Report or
    # Rejected) is present — advance to date collection. Just clears the
    # WAITING_FOR_USER status so the next turn routes through
    # field_extraction → missing_field and asks for actual_handover_date.
    if action_override == "continue_rdd_upload":
        log.info("rdd_review_entry.action_continue_upload")
        return {"status": "IN_PROGRESS"}

    if action_override == "submit_rdd_report":
        log.info("rdd_review_entry.action_submit")
        backend_refs["rdd_action"] = "submit"
        return {"backend_refs": backend_refs}

    if action_override == "approve_rdd_final":
        log.info("rdd_review_entry.action_final_approve")
        backend_refs["rdd_action"] = "final_approve"
        return {"backend_refs": backend_refs}

    # Type-first RDD upload selection chips.
    _DOC_TYPE_ACTIONS: dict[str, tuple[str, str | None]] = {
        "select_rdd_doc_meeting": ("DR_SR_HANDOVER_REPORT", None),
        "select_rdd_doc_rejected": ("SR_REJECTED_HANDOVER_REPORT", "DISAPPROVED"),
        "select_rdd_doc_other": ("SR_HANDOVER_OTHER", None),
    }
    if action_override in _DOC_TYPE_ACTIONS:
        doc_type, auto_status = _DOC_TYPE_ACTIONS[action_override]
        log.info("rdd_review_entry.select_doc_type", doc_type=doc_type)
        collected = dict(state.get("collected_data") or {})
        collected["rdd_upload_doc_type"] = doc_type
        if auto_status:
            collected["document_status"] = auto_status
        elif doc_type != "DR_SR_HANDOVER_REPORT":
            # Other: clear any stale meeting status
            collected.pop("document_status", None)
        return {"collected_data": collected}

    _STATUS_ACTIONS: dict[str, str] = {
        "select_rdd_status_approved": "APPROVED",
        "select_rdd_status_approved_with_changes": "APPROVED_WITH_CHANGES",
    }
    if action_override in _STATUS_ACTIONS:
        status_val = _STATUS_ACTIONS[action_override]
        log.info("rdd_review_entry.select_document_status", status=status_val)
        collected = dict(state.get("collected_data") or {})
        collected["document_status"] = status_val
        return {"collected_data": collected}

    # ── Single-turn confirm actions — set action AND mark CONFIRMED in one turn ──
    # Required because rdd_action is not persisted across turns; a generic
    # "confirm" on Turn N+1 would find rdd_action empty and loop back to
    # field_extraction instead of reaching the payload builder.
    if action_override == "confirm_rdd_submit":
        log.info("rdd_review_entry.action_confirm_rdd_submit")
        backend_refs["rdd_action"] = "submit"
        return {"backend_refs": backend_refs, "confirmation_status": "CONFIRMED"}

    if action_override == "confirm_rdd_final_approve":
        log.info("rdd_review_entry.action_confirm_rdd_final_approve")
        backend_refs["rdd_action"] = "final_approve"
        return {"backend_refs": backend_refs, "confirmation_status": "CONFIRMED"}

    # ConfirmationCard falls back to action=confirm when confirmAction missing.
    # Treat that as RDD report submit so Confirm & Submit works.
    if action_override == "confirm":
        log.info("rdd_review_entry.action_confirm_generic")
        backend_refs["rdd_action"] = backend_refs.get("rdd_action") or "submit"
        return {"backend_refs": backend_refs, "confirmation_status": "CONFIRMED"}

    if action_override in ("cancel", "cancel_rdd"):
        log.info("rdd_review_entry.action_cancel")
        return {
            "confirmation_status": "REJECTED",
            "status": "WAITING_FOR_USER",
            "response_message": "Action cancelled. What would you like to do next?",
        }

    if action_override == "cancel_update":
        log.info("rdd_review_entry.action_cancel")
        return {
            "status": "WAITING_FOR_USER",
            "response_message": "Update cancelled. What would you like to do next?",
        }

    if action_override == "upload_document":
        log.info("rdd_review_entry.action_upload_document")
        return {
            "status": "WAITING_FOR_USER",
            "response_message": "Please use the upload interface to attach the RDD handover report.",
        }

    # ── 2. Text confirmation while card is PENDING ─────────────────────────
    if confirmation_status == "PENDING":
        if _contains_phrase(user_message, _CONFIRM_PHRASES):
            log.info("rdd_review_entry.confirmation_accepted_text")
            backend_refs["rdd_action"] = backend_refs.get("rdd_action") or "submit"
            return {
                "backend_refs": backend_refs,
                "confirmation_status": "CONFIRMED",
            }
        if _contains_phrase(user_message, _REJECT_PHRASES):
            log.info("rdd_review_entry.confirmation_rejected_text")
            return {
                "confirmation_status": "REJECTED",
                "status": "WAITING_FOR_USER",
                "response_message": (
                    "No problem — what would you like to change? "
                    "Please tell me which field to update."
                ),
            }

    # ── 3. No action override — fall through to field_extraction ──────────
    return {}
