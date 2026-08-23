"""Manual Entry Confirmation Node.

Presents a confirmation card for the user to review and confirm their
typed sales data before submission.
"""

from __future__ import annotations

import re
from typing import Any

import structlog

from app.agents.graph.state import ServiceRequestState
from app.observability.decorators import trace_node

log = structlog.get_logger(__name__)

_CONFIRM_PHRASES: frozenset[str] = frozenset(
    {"yes", "confirm", "confirmed", "submit", "ok", "okay", "proceed", "looks good", "correct"}
)
_REJECT_PHRASES: frozenset[str] = frozenset(
    {"no", "cancel", "change", "edit", "wrong", "incorrect", "modify"}
)


def _contains_phrase(text: str, phrases: frozenset[str]) -> bool:
    lowered = text.lower()
    return any(bool(re.search(r"\b" + re.escape(p) + r"\b", lowered)) for p in phrases)


@trace_node("manual_entry_confirmation", "AGENT")
async def manual_entry_confirmation_node(state: ServiceRequestState) -> dict[str, Any]:
    """Show a confirmation card or parse the user's yes/no response."""
    collected: dict = state.get("collected_data") or {}
    user_message: str = state.get("user_message") or ""
    action_override: str | None = state.get("action_override")
    confirmation_status: str | None = state.get("confirmation_status")
    workflow_stage: str | None = state.get("workflow_stage")

    # ── Action-based confirm ───────────────────────────────────────────────
    if action_override in ("confirm", "confirm_manual_entry"):
        log.info("manual_entry_confirmation.confirmed_by_action")
        return {
            "confirmation_status": "CONFIRMED",
            "workflow_stage": "SUBMITTING",
            "status": "IN_PROGRESS",
        }

    if action_override in ("cancel", "cancel_manual_entry"):
        return {
            "confirmation_status": "REJECTED",
            "workflow_stage": "MANUAL_ENTRY",
            "status": "WAITING_FOR_USER",
            "response_message": "Cancelled. Please provide the correct details.",
        }

    # ── Text-based confirm ─────────────────────────────────────────────────
    if workflow_stage == "AWAITING_MANUAL_CONFIRM" and user_message:
        if _contains_phrase(user_message, _CONFIRM_PHRASES):
            log.info("manual_entry_confirmation.confirmed_by_text")
            return {
                "confirmation_status": "CONFIRMED",
                "workflow_stage": "SUBMITTING",
                "status": "IN_PROGRESS",
            }
        if _contains_phrase(user_message, _REJECT_PHRASES):
            return {
                "confirmation_status": "REJECTED",
                "workflow_stage": "MANUAL_ENTRY",
                "status": "WAITING_FOR_USER",
                "collected_data": {},
                "response_message": "No problem — let's start over. Please provide the lease code.",
            }

    # ── Show confirmation card ─────────────────────────────────────────────
    lease_code = collected.get("lease_code", "")
    start = collected.get("sales_period_start", "")
    end = collected.get("sales_period_end", "")
    sales_amt = collected.get("sales_amt", 0)
    try:
        amt_display = f"SAR {float(sales_amt):,.2f}"
    except (ValueError, TypeError):
        amt_display = str(sales_amt)

    message = (
        f"Please confirm the following details before submitting:\n\n"
        f"| Field | Value |\n"
        f"|---|---|\n"
        f"| Lease Code | **{lease_code}** |\n"
        f"| Period | **{start} – {end}** |\n"
        f"| Sales Amount | **{amt_display}** |\n\n"
        "Reply **yes** to confirm and submit, or **no** to make changes."
    )

    return {
        "confirmation_status": "PENDING",
        "workflow_stage": "AWAITING_MANUAL_CONFIRM",
        "status": "WAITING_FOR_USER",
        "response_message": message,
        "response_ui": {
            "type": "manual_entry_confirm",
            "lease_code": lease_code,
            "sales_period_start": start,
            "sales_period_end": end,
            "sales_amt": sales_amt,
            "message": message,
        },
    }
