"""Supporting Document Confirmation Node.

Shows file name + target row details, then waits for the tenant to confirm
before PATCH.
"""

from __future__ import annotations

import re
from typing import Any

import structlog

from app.agents.graph.state import ServiceRequestState
from app.agents.graph.nodes.sales_data.supporting_doc_upload_node import _prompt_for_file
from app.agents.schemas.sales_data_schema import ASK_UPLOAD_LABELS, STATUS_LABELS
from app.observability.decorators import trace_node

log = structlog.get_logger(__name__)

_CONFIRM_PHRASES: frozenset[str] = frozenset(
    {"yes", "confirm", "confirmed", "submit", "ok", "okay", "proceed", "looks good"}
)
_REJECT_PHRASES: frozenset[str] = frozenset(
    {"no", "cancel", "change", "edit", "wrong", "incorrect", "modify"}
)


def _contains_phrase(text: str, phrases: frozenset[str]) -> bool:
    lowered = text.lower()
    return any(bool(re.search(r"\b" + re.escape(p) + r"\b", lowered)) for p in phrases)


@trace_node("supporting_doc_confirmation", "AGENT")
async def supporting_doc_confirmation_node(state: ServiceRequestState) -> dict[str, Any]:
    """Show a confirmation card or parse yes/no."""
    collected: dict = dict(state.get("collected_data") or {})
    user_message: str = state.get("user_message") or ""
    action_override: str | None = state.get("action_override")
    workflow_stage: str | None = state.get("workflow_stage")
    corrected_fields: dict = dict(state.get("corrected_fields") or {})

    comment = (
        corrected_fields.get("comment_text") or collected.get("comment_text") or ""
    ).strip()
    if comment:
        collected["comment_text"] = comment

    invalid_amount = False
    if "sales_amt" in corrected_fields and corrected_fields.get("sales_amt") is not None:
        try:
            amt = float(corrected_fields["sales_amt"])
        except (TypeError, ValueError):
            invalid_amount = True
        else:
            # A corrected amount is genuine fresh tenant input (they're
            # editing the figure as part of this reupload), so it gets the
            # same sanity check the CSV/manual-entry paths already apply —
            # a silently-accepted zero or negative amount would otherwise
            # reach cenomi-api's PATCH unchecked.
            if amt <= 0:
                invalid_amount = True
            else:
                collected["sales_amt"] = amt

    def _missing_comment_response() -> dict[str, Any]:
        prompt = _confirm_ui(collected, comment_missing=True)
        prompt["collected_data"] = collected
        return prompt

    def _invalid_amount_response() -> dict[str, Any]:
        return {
            "collected_data": collected,
            "workflow_stage": "AWAITING_DOC_CONFIRM",
            "status": "WAITING_FOR_USER",
            "response_message": (
                "The sales amount must be greater than 0. Please enter a valid amount."
            ),
            "response_ui": {"type": "message", "verbatim": True},
        }

    if action_override in ("confirm", "confirm_supporting_doc"):
        if invalid_amount:
            return _invalid_amount_response()
        if not (collected.get("comment_text") or "").strip():
            log.info("supporting_doc_confirmation.comment_required")
            return _missing_comment_response()
        log.info("supporting_doc_confirmation.confirmed_by_action")
        return {
            "collected_data": collected,
            "confirmation_status": "CONFIRMED",
            "workflow_stage": "DOC_SUBMITTING",
            "status": "IN_PROGRESS",
        }

    if action_override in ("cancel", "cancel_supporting_doc"):
        return _resume_upload_after_cancel(
            state,
            collected,
            "Upload cancelled — the file was not submitted. Attach a different PDF when you are ready.",
        )

    if workflow_stage == "AWAITING_DOC_CONFIRM" and user_message:
        if _contains_phrase(user_message, _CONFIRM_PHRASES):
            if invalid_amount:
                return _invalid_amount_response()
            if not (collected.get("comment_text") or "").strip():
                return _missing_comment_response()
            log.info("supporting_doc_confirmation.confirmed_by_text")
            return {
                "collected_data": collected,
                "confirmation_status": "CONFIRMED",
                "workflow_stage": "DOC_SUBMITTING",
                "status": "IN_PROGRESS",
            }
        if _contains_phrase(user_message, _REJECT_PHRASES):
            return _resume_upload_after_cancel(
                state,
                collected,
                "Upload cancelled. Attach a different file when you are ready.",
            )

    ui = _confirm_ui(collected)
    ui["collected_data"] = collected
    return ui


def _resume_upload_after_cancel(
    state: ServiceRequestState,
    collected: dict[str, Any],
    prefix: str,
) -> dict[str, Any]:
    """Drop the cancelled file and restore the PDF attach prompt."""
    remaining = {
        k: v
        for k, v in collected.items()
        if k not in {"document_id", "filename", "doc_session_id"}
    }
    refs = dict(state.get("backend_refs") or {})
    refs.pop("doc_session_id", None)
    prompt = _prompt_for_file(
        {
            "lease_id": remaining.get("lease_id"),
            "lease_code": remaining.get("lease_code"),
            "sales_period_start": remaining.get("sales_period_start"),
            "sales_period_end": remaining.get("sales_period_end"),
            "status": remaining.get("current_status"),
            "comment_text": remaining.get("admin_comment"),
            "sales_amt": remaining.get("sales_amt"),
        }
    )
    message = f"{prefix}\n\n{prompt['response_message']}"
    prompt["response_message"] = message
    prompt["response_ui"]["message"] = message
    prompt["collected_data"] = remaining
    prompt["backend_refs"] = refs
    prompt["confirmation_status"] = "REJECTED"
    prompt["intent"] = "UPLOAD_SUPPORTING_DOCUMENT"
    return prompt


def _confirm_ui(collected: dict[str, Any], *, comment_missing: bool = False) -> dict[str, Any]:
    lease_code = collected.get("lease_code", "")
    start = collected.get("sales_period_start", "")
    end = collected.get("sales_period_end", "")
    filename = collected.get("filename", "document")
    current_status = collected.get("current_status", "")
    target_status = collected.get("target_status", "RE_UPLOADED")
    current_label = STATUS_LABELS.get(current_status, current_status)
    target_label = STATUS_LABELS.get(target_status, target_status)
    ask_label = ASK_UPLOAD_LABELS.get(current_status, "Document")
    amt = collected.get("sales_amt")

    # The card (SalesDataSupportingDocConfirmCard.tsx) already renders
    # lease/period/file/amount/status as an actual table from response_ui's
    # fields below, and the Confirm/Cancel buttons make "reply yes/no"
    # instructions redundant — so both messages stay short. But they must
    # differ: this used to show "A comment is required" even on the very
    # first view of the card, before the tenant had done anything wrong.
    if comment_missing:
        message = (
            "A comment is required before I can submit. Add a short note "
            "(for example: *POS report attached*) and confirm again."
        )
    else:
        message = "Add a short comment (for example: *POS report attached*), then confirm."

    return {
        "confirmation_status": "PENDING",
        "workflow_stage": "AWAITING_DOC_CONFIRM",
        "status": "WAITING_FOR_USER",
        "response_message": message,
        "response_ui": {
            "type": "supporting_doc_confirm",
            # Without this, response_generation's LLM rewrite is free to
            # paraphrase this confirmation/error text — observed producing a
            # fabricated "issue with the sales period date format, please
            # re-enter the start date" out of what was really just a missing
            # comment_text, sending the tenant down a dead-end asking for a
            # date the flow never needed at all.
            "verbatim": True,
            "lease_code": lease_code,
            "lease_id": collected.get("lease_id"),
            "sales_period_start": start,
            "sales_period_end": end,
            "filename": filename,
            # Lets the card offer "View document" for the file just uploaded.
            "document_id": collected.get("document_id"),
            "current_status": current_status,
            "current_status_label": current_label,
            "target_status": target_status,
            "target_status_label": target_label,
            "ask_label": ask_label,
            "sales_amt": amt,
            "comment_text": collected.get("comment_text") or "",
            "comment_required": True,
            "message": message,
        },
    }
