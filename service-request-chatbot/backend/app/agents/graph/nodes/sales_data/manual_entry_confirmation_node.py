"""Manual Entry Confirmation Node.

Presents a confirmation card for the user to review and confirm their
typed sales data before submission.
"""

from __future__ import annotations

import re
from typing import Any

import structlog

from app.agents.graph.nodes.sales_data.sales_data_entry_node import (
    _looks_like_typed_amount,
    _mentions_lease_code,
)
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


def _looks_like_new_entry(message: str) -> bool:
    """True when the message is introducing a fresh lease/amount rather than
    answering yes/no to the pending confirm card — e.g. "i want to submit
    sales for t0106028 for dec 2024 - 12280 SAR" contains "submit" (a
    _CONFIRM_PHRASES match) but is actually a brand-new request that should
    reset state, not a stale-session auto-confirm of the previous lease."""
    return bool(_mentions_lease_code(message)) and bool(_looks_like_typed_amount(message))


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
        patch: dict[str, Any] = {
            "confirmation_status": "CONFIRMED",
            "workflow_stage": "SUBMITTING",
            "status": "IN_PROGRESS",
        }
        # The confirm card's amount is editable, so a correction typed there
        # arrives with the confirm action. Without this it was dropped and
        # the original figure got submitted — the same contract the CSV
        # review table already uses for its edited rows.
        corrected: dict = state.get("corrected_fields") or {}
        if corrected.get("sales_amt") is not None:
            try:
                amount = float(corrected["sales_amt"])
            except (TypeError, ValueError):
                amount = None
            if amount is not None and amount > 0:
                patch["collected_data"] = {**collected, "sales_amt": amount}
                log.info(
                    "manual_entry_confirmation.amount_corrected",
                    sales_amt=amount,
                )
        return patch

    if action_override in ("cancel", "cancel_manual_entry"):
        return {
            "confirmation_status": "REJECTED",
            "workflow_stage": "MANUAL_ENTRY",
            "status": "WAITING_FOR_USER",
            "response_message": "Submission cancelled — nothing was submitted. What would you like to change: the lease, month or amount?",
            "response_ui": {"type": "message", "verbatim": True, "message": "Submission cancelled — nothing was submitted. What would you like to change: the lease, month or amount?"},
        }

    # ── Text-based confirm ─────────────────────────────────────────────────
    # sales_data_entry_node (the top-level dispatcher) already recognises a
    # freshly-typed lease/amount and routes it through manual_entry_extraction
    # + manual_entry_lease_validation before it ever reaches here, refreshing
    # `collected` with the new lease. So a message that both matches
    # _CONFIRM_PHRASES ("...submit...") AND reads as a new entry has already
    # had its new data merged into `collected` by the time we get here — show
    # that new card below rather than reading the phrase match as a yes/no
    # reply to the card already on screen.
    if (
        workflow_stage == "AWAITING_MANUAL_CONFIRM"
        and user_message
        and not _looks_like_new_entry(user_message)
    ):
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
                # Keep the entry so "change the amount to 13,000" just works.
                "response_message": "Submission cancelled — nothing was submitted. What would you like to change: the lease, month or amount?",
                "response_ui": {"type": "message", "verbatim": True, "message": "Submission cancelled — nothing was submitted. What would you like to change: the lease, month or amount?"},
            }

    # ── Show confirmation card ─────────────────────────────────────────────
    lease_code = collected.get("lease_code", "")
    start = collected.get("sales_period_start", "")
    end = collected.get("sales_period_end", "")
    sales_amt = collected.get("sales_amt", 0)
    brand = (collected.get("brand") or "").strip()
    property_name = (collected.get("property") or "").strip()
    try:
        amt_display = f"SAR {float(sales_amt):,.2f}"
    except (ValueError, TypeError):
        amt_display = str(sales_amt)

    # The field/value data is sent as a structured `fields` array (below) for
    # the card to render as a real table — the bubble text stays short prose
    # so it doesn't duplicate that table as a raw markdown pipe-table string.
    # The card below renders Confirm / Cancel buttons, so telling the user to
    # "Reply yes / no" described controls that aren't the ones in front of
    # them. Typing still works — this just stops advertising it as the way.
    message = "Please check these details before I submit them."
    fields = [
        {"key": "lease_code", "label": "Lease Code", "value": lease_code},
        {"key": "brand", "label": "Brand", "value": brand or "—"},
        {"key": "property", "label": "Property", "value": property_name or "—"},
        {"key": "period", "label": "Period", "value": f"{start} – {end}"},
        # `value` stays the raw number so the card's amount input can bind to
        # it; `display` carries the formatted "SAR 50,001.00" for the
        # read-only rendering.
        {
            "key": "sales_amt",
            "label": "Sales Amount",
            "value": sales_amt,
            "display": amt_display,
        },
    ]

    return {
        "confirmation_status": "PENDING",
        "workflow_stage": "AWAITING_MANUAL_CONFIRM",
        "status": "WAITING_FOR_USER",
        "response_message": message,
        "response_ui": {
            "type": "manual_entry_confirm",
            "lease_code": lease_code,
            "brand": brand,
            "property": property_name,
            "sales_period_start": start,
            "sales_period_end": end,
            "sales_amt": sales_amt,
            "fields": fields,
            "verbatim": True,
            "message": message,
        },
    }
