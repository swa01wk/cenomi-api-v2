"""Manual Entry Extraction Node.

Uses the LLM to extract lease_code, sales_amt, and period_hint from the
user's free-text message, OR reads from notification_context if the session
was started from a notification deep-link (source == "notification").

After extraction, converts period_hint to actual YYYY-MM-DD dates using
a deterministic period resolver (no LLM needed for date math).
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date, timedelta
from typing import Any

import structlog

from app.agents.graph.state import ServiceRequestState
from app.agents.llm.gateway import get_default_gateway as get_llm_gateway
from app.agents.prompts.sales_data_manual_entry_prompt import (
    SALES_DATA_MANUAL_ENTRY_SYSTEM_PROMPT,
)
from app.observability.decorators import trace_node

log = structlog.get_logger(__name__)
logger = logging.getLogger(__name__)

# Month name → number
_MONTH_MAP = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4,
    "jun": 6, "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

# Quarter → months
_QUARTER_MAP = {
    "q1": (1, 3), "q2": (4, 6), "q3": (7, 9), "q4": (10, 12),
}


def _resolve_period(hint: str) -> tuple[str | None, str | None]:
    """Convert a period hint string to (start_date, end_date) in YYYY-MM-DD."""
    hint_lower = hint.lower().strip()
    today = date.today()
    year = today.year

    # Try to extract year from hint (e.g. "August 2026")
    year_match = re.search(r"\b(20\d{2})\b", hint_lower)
    if year_match:
        year = int(year_match.group(1))

    # Quarter (Q1-Q4)
    quarter_match = re.search(r"\bq([1-4])\b", hint_lower)
    if quarter_match:
        q = f"q{quarter_match.group(1)}"
        start_month, end_month = _QUARTER_MAP[q]
        start = date(year, start_month, 1)
        # Last day of end_month
        if end_month == 12:
            end = date(year, 12, 31)
        else:
            end = date(year, end_month + 1, 1) - timedelta(days=1)
        return start.isoformat(), end.isoformat()

    # Month name
    for month_name, month_num in _MONTH_MAP.items():
        if month_name in hint_lower:
            start = date(year, month_num, 1)
            if month_num == 12:
                end = date(year, 12, 31)
            else:
                end = date(year, month_num + 1, 1) - timedelta(days=1)
            return start.isoformat(), end.isoformat()

    # Explicit date range (2026-08-01 to 2026-08-31)
    date_range_match = re.search(
        r"(\d{4}-\d{2}-\d{2})\s*(?:to|-|through)\s*(\d{4}-\d{2}-\d{2})", hint_lower
    )
    if date_range_match:
        return date_range_match.group(1), date_range_match.group(2)

    return None, None


@trace_node("manual_entry_extraction", "AGENT")
async def manual_entry_extraction_node(state: ServiceRequestState) -> dict[str, Any]:
    """Extract lease_code, period, and sales_amt from free text or notification context."""
    user_message: str = state.get("user_message") or ""
    collected: dict = dict(state.get("collected_data") or {})
    backend_refs: dict = dict(state.get("backend_refs") or {})

    # ── Notification context shortcut ─────────────────────────────────────
    notification_context: dict | None = backend_refs.get("notification_context")
    if notification_context and notification_context.get("source") == "notification":
        lease_code = notification_context.get("lease_code")
        period_hint = notification_context.get("period_hint", "")
        start, end = _resolve_period(period_hint)

        if lease_code:
            collected["lease_code"] = lease_code
        if start:
            collected["sales_period_start"] = start
            collected["sales_period_end"] = end

        # Clear notification context after first use
        backend_refs.pop("notification_context", None)
        backend_refs["notification_context"] = None

        log.info(
            "manual_entry_extraction.notification_context",
            lease_code=lease_code,
            period=f"{start}–{end}",
        )
        return {
            "collected_data": collected,
            "backend_refs": backend_refs,
            "workflow_stage": "MANUAL_ENTRY",
            "status": "IN_PROGRESS",
        }

    # ── LLM extraction from free text ─────────────────────────────────────
    if not user_message.strip():
        return {
            "status": "WAITING_FOR_USER",
            "response_message": (
                "Please tell me the sales details. For example:\n"
                '"Sales for August, lease LC-1234, amount SAR 85,000."'
            ),
        }

    try:
        gateway = get_llm_gateway()
        history = state.get("conversation_history") or []
        messages = [
            {"role": "system", "content": SALES_DATA_MANUAL_ENTRY_SYSTEM_PROMPT},
            *history,
            {"role": "user", "content": user_message},
        ]
        response = await gateway.generate(messages=messages, temperature=0.0)
        raw = response.content.strip()
        # Strip markdown fences
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
        parsed = json.loads(raw)
        fields = parsed.get("fields", {})
    except Exception as exc:
        log.warning("manual_entry_extraction.llm_failed", exc_info=True)
        fields = {}

    # Apply extracted fields to collected_data
    updated = False
    if "lease_code" in fields:
        val = fields["lease_code"].get("value", "")
        if val:
            collected["lease_code"] = str(val).upper().strip()
            updated = True

    if "sales_amt" in fields:
        val = fields["sales_amt"].get("value")
        if val is not None:
            try:
                collected["sales_amt"] = float(str(val).replace(",", ""))
                updated = True
            except (ValueError, TypeError):
                pass

    if "period_hint" in fields:
        hint = fields["period_hint"].get("value", "")
        if hint:
            start, end = _resolve_period(hint)
            log.info(
                "manual_entry_extraction.period_hint_resolved",
                hint=hint,
                start=start,
                end=end,
            )
            if start:
                collected["sales_period_start"] = start
                collected["sales_period_end"] = end
                updated = True

    log.info(
        "manual_entry_extraction.result",
        extracted=list(fields.keys()),
        collected=list(collected.keys()),
        sales_period_start=collected.get("sales_period_start"),
        sales_period_end=collected.get("sales_period_end"),
        lease_code=collected.get("lease_code"),
        user_message=user_message,
    )

    return {
        "collected_data": collected,
        "backend_refs": backend_refs,
        "workflow_stage": "MANUAL_ENTRY",
        "status": "IN_PROGRESS",
    }
