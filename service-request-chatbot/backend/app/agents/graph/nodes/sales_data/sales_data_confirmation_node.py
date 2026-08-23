"""Sales Data Confirmation Node.

Builds the structured SALES_DATA_REVIEW message that the frontend renders
as an editable table (SalesDataReviewCard).

The table shows:
  - lease_code (read-only)
  - sales_period_start / sales_period_end (read-only, formatted)
  - sales_amt (editable input field)

The user can edit sales_amt values and then click Submit, which fires a
CONFIRM_CSV_SUBMISSION action turn with the updated rows.

Sets workflow_stage = AWAITING_TABLE_CONFIRM.
"""

from __future__ import annotations

import json
from typing import Any

import structlog

from app.agents.graph.state import ServiceRequestState
from app.observability.decorators import trace_node

log = structlog.get_logger(__name__)


def _format_period(start: str, end: str) -> str:
    """Format a period range as a human-readable string."""
    try:
        from datetime import date
        s = date.fromisoformat(start)
        e = date.fromisoformat(end)
        if s.month == e.month and s.year == e.year:
            return s.strftime("%B %Y")
        return f"{s.strftime('%b %d')} – {e.strftime('%b %d, %Y')}"
    except (ValueError, AttributeError):
        return f"{start} – {end}"


@trace_node("sales_data_confirmation", "AGENT")
async def sales_data_confirmation_node(state: ServiceRequestState) -> dict[str, Any]:
    """Build the SALES_DATA_REVIEW message for the editable review table."""
    backend_refs: dict = dict(state.get("backend_refs") or {})
    csv_rows: list[dict] = backend_refs.get("csv_rows") or []
    filename: str = backend_refs.get("csv_filename", "upload.csv")

    if not csv_rows:
        return {
            "status": "WAITING_FOR_USER",
            "response_message": "No rows to review. Please upload your CSV file.",
        }

    # Build display rows for the table
    display_rows = []
    for row in csv_rows:
        display_rows.append({
            "lease_code": row["lease_code"],
            "lease_id": row.get("lease_id"),
            "period": _format_period(
                row["sales_period_start"], row["sales_period_end"]
            ),
            "sales_period_start": row["sales_period_start"],
            "sales_period_end": row["sales_period_end"],
            "sales_amt": row["sales_amt"],
            "current_status": row.get("current_status", "PENDING"),
        })

    row_count = len(display_rows)
    log.info("sales_data_confirmation.built", row_count=row_count, filename=filename)

    message_text = (
        f"✅ **{row_count} row(s) validated** from **{filename}**.\n\n"
        "Please review the table below. You can edit the **Sales Amount (SAR)** column "
        "before submitting.\n\n"
        f"*All {row_count} rows will be submitted together — if any row has an error, "
        "the entire batch will be rejected.*"
    )

    return {
        "workflow_stage": "AWAITING_TABLE_CONFIRM",
        "status": "WAITING_FOR_USER",
        "response_message": message_text,
        "response_ui": {
            "type": "sales_data_review",
            "rows": display_rows,
            "filename": filename,
            "row_count": row_count,
            "message": message_text,
        },
    }
