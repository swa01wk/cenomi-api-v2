"""Submission Success Node — shown after a successful POST /api/v1/sales-data."""

from __future__ import annotations

from typing import Any

import structlog

from app.agents.graph.nodes.sales_data.period_utils import (
    format_year_month,
    year_month_from_start,
)
from app.agents.graph.state import ServiceRequestState
from app.observability.decorators import trace_node

log = structlog.get_logger(__name__)

_MAX_LISTED = 5


def _describe(row: dict[str, Any]) -> str:
    parts = [f"lease **{row.get('lease_code', '')}**"]
    ym = year_month_from_start(str(row.get("sales_period_start") or ""))
    if ym:
        parts.append(format_year_month(ym))
    try:
        parts.append(f"SAR {float(row.get('sales_amt')):,.2f}")
    except (TypeError, ValueError):
        pass
    return ", ".join(parts)


def _submitted_period(rows: list[dict[str, Any]]) -> dict[str, str]:
    """Widest sales period across the submitted rows, as bare ISO dates.

    The Sales Data listing filters ``sales_period_start >= from`` AND
    ``sales_period_end <= to``, so the range has to span every row — the
    earliest start and the latest end — or a multi-month upload would come
    back partly empty.
    """
    starts = sorted(
        str(r.get("sales_period_start") or "")[:10] for r in rows
    )
    ends = sorted(str(r.get("sales_period_end") or "")[:10] for r in rows)
    starts = [s for s in starts if s]
    ends = [e for e in ends if e]
    if not starts or not ends:
        return {}
    return {
        "sales_period_start": starts[0],
        "sales_period_end": ends[-1],
    }


def _success_message(rows: list[dict[str, Any]]) -> str:
    """One line with the key details — same shape as the handover success."""
    if len(rows) == 1:
        return f"Your sales data has been submitted for review — {_describe(rows[0])}."
    listed = "\n".join(f"- {_describe(r)}" for r in rows[:_MAX_LISTED])
    more = len(rows) - _MAX_LISTED
    if more > 0:
        listed += f"\n- and {more} more"
    return f"Your sales data has been submitted for review — {len(rows)} entries:\n{listed}"


@trace_node("submission_success", "AGENT")
async def submission_success_node(state: ServiceRequestState) -> dict[str, Any]:
    """Surface a success confirmation card."""
    backend_refs: dict = state.get("backend_refs") or {}
    submitted_rows: list[dict] = backend_refs.get("submitted_rows") or []
    row_count = len(submitted_rows)

    message = _success_message(submitted_rows)

    log.info("submission_success.reported", row_count=row_count)

    return {
        "workflow_stage": "SALES_DATA_COMPLETE",
        "status": "COMPLETED",
        "response_message": message,
        "response_ui": {
            "type": "submission_success",
            "row_count": row_count,
            "lease_codes": sorted({str(r.get("lease_code") or "") for r in submitted_rows} - {""}),
            # Period actually submitted, so "View Sales Data" can filter the
            # listing to it. The listing defaults to the last 60 days, which
            # hides any submission for an older month (e.g. September 2025)
            # behind "No data found".
            **_submitted_period(submitted_rows),
            # Without this, response_generation's LLM rewrite still runs even
            # on this terminal status ("use the message as-is but still
            # attempt LLM rewrite for a warmer tone") — producing a different
            # over-familiar paraphrase every time ("Great news!", "Awesome,
            # all done!", ...) instead of one consistent, professional
            # confirmation.
            "verbatim": True,
            "message": message,
        },
    }
