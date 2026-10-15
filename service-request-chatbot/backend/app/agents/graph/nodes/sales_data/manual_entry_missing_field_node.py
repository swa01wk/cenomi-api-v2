"""Manual Entry Missing Field Node.

Checks collected_data for missing required fields and asks for what is still
needed — conversationally, acknowledging what the user already gave, and
asking for everything missing in one question instead of one field at a time.
"""

from __future__ import annotations

from typing import Any

import structlog

from app.agents.graph.nodes.sales_data.period_utils import (
    format_year_month,
    year_month_from_start,
)
from app.agents.graph.state import ServiceRequestState
from app.agents.schemas.sales_data_schema import MANUAL_ENTRY_REQUIRED_FIELDS
from app.observability.decorators import trace_node

log = structlog.get_logger(__name__)

_MAX_LEASE_SUGGESTIONS = 3


def _format_amount(value: Any) -> str:
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return str(value)
    return f"SAR {amount:,.2f}".replace(".00", "")


def _known_so_far(collected: dict) -> list[str]:
    parts: list[str] = []
    if collected.get("lease_code"):
        parts.append(f"lease **{collected['lease_code']}**")
    ym = year_month_from_start(str(collected.get("sales_period_start") or ""))
    if ym:
        parts.append(f"**{format_year_month(ym)}**")
    if collected.get("sales_amt"):
        parts.append(f"**{_format_amount(collected['sales_amt'])}**")
    return parts


def _lease_suggestions(collected: dict, refs: dict) -> list[str]:
    items = collected.get("_pending_items") or refs.get("pending_items") or []
    seen: list[str] = []
    for item in items:
        code = str(item.get("lease_code") or "")
        if code and code not in seen:
            seen.append(code)
        if len(seen) >= _MAX_LEASE_SUGGESTIONS:
            break
    return seen


def _acknowledgement(known: list[str]) -> str:
    """Brief confirmation of what was just collected — handover-agent style."""
    if not known:
        return ""
    text = known[0] if len(known) == 1 else f"{', '.join(known[:-1])} and {known[-1]}"
    return f"{text[0].upper()}{text[1:]} noted. "


def _build_question(missing: list[str], collected: dict, refs: dict) -> str:
    """One question per turn, confirming what was just collected first."""
    known = _known_so_far(collected)
    ack = _acknowledgement(known)
    field = missing[0]

    if field == "lease_code":
        question = f"{ack}Which lease is this sales data for?"
        suggestions = _lease_suggestions(collected, refs)
        if suggestions:
            question += (
                " Leases with pending sales data include "
                + ", ".join(f"**{code}**" for code in suggestions)
                + "."
            )
    elif field in ("sales_period_start", "sales_period_end"):
        question = f"{ack}Which month is this sales data for?"
    elif collected.get("_amount_rejected"):
        question = (
            f"{ack}The sales amount must be greater than 0. "
            "What is the total sales amount (SAR) for this period?"
        )
    else:
        question = (
            f"{ack}What is the total sales amount (SAR) for this period? "
            "Type it below, or attach a CSV if you have one."
        )

    if not known:
        question += (
            "\n\nYou can also send all the details in one message, for example: "
            '"t0057323, August 2026, 12,280".'
        )
    return question


@trace_node("manual_entry_missing_field", "AGENT")
async def manual_entry_missing_field_node(state: ServiceRequestState) -> dict[str, Any]:
    """Ask the user for whatever required fields are still missing."""
    collected: dict = state.get("collected_data") or {}
    refs: dict = state.get("backend_refs") or {}

    missing = [f for f in MANUAL_ENTRY_REQUIRED_FIELDS if not collected.get(f)]
    if not missing:
        log.info("manual_entry_missing_field.all_collected")
        return {"status": "IN_PROGRESS"}

    question = _build_question(missing, collected, refs)
    field = missing[0]
    log.info("manual_entry_missing_field.asking", field=field, missing=missing)

    # Once the lease and month are known, the only thing left is the figure —
    # and a tenant may just as well have it in a CSV as in their head. The
    # plain text_prompt had no attach control (and no frontend renderer at
    # all), so after tapping a month there was no way to upload a file:
    # "I want to attach the sales data using excel sheet" was answered with
    # "what is the total sales amount?". sales_data_upload_prompt carries the
    # attach control and the message, so typing or attaching both work.
    if field == "sales_amt":
        return {
            "active_field": field,
            "status": "WAITING_FOR_USER",
            "response_message": question,
            "response_ui": {
                "type": "sales_data_upload_prompt",
                "field": field,
                "message": question,
                "show_file_upload": True,
                "show_template_download": True,
                "verbatim": True,
            },
        }

    return {
        "active_field": field,
        "status": "WAITING_FOR_USER",
        "response_message": question,
        "response_ui": {
            "type": "text_prompt",
            "field": field,
            "message": question,
            # Deterministic wording — no LLM rewrite variance.
            "verbatim": True,
        },
    }
