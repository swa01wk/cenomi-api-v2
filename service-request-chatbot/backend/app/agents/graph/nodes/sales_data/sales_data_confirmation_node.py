"""Sales Data Confirmation Node.

Builds the structured SALES_DATA_REVIEW message that the frontend renders
as an editable table (SalesDataReviewCard).

The table shows:
  - lease_code, brand, property (read-only)
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
        message = "No rows to review. Please upload your CSV file."
        return {
            "status": "WAITING_FOR_USER",
            "response_message": message,
            "response_ui": {"type": "message", "verbatim": True, "message": message},
        }

    # Build display rows for the table
    display_rows = []
    for row in csv_rows:
        display_rows.append({
            "lease_code": row["lease_code"],
            "lease_id": row.get("lease_id"),
            "brand": row.get("brand") or "",
            "property": row.get("property") or "",
            "company": row.get("company") or "",
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

    # Tenant-facing copy: no batch/rejection mechanics. The old trailing
    # "*All N rows will be submitted together — if any row has an error, the
    # entire batch will be rejected.*" read as developer notes, not guidance
    # for the person uploading; errors are reported per row anyway if the
    # submit fails, so warning about it up front only caused doubt.
    rows_label = "row" if row_count == 1 else "rows"
    message_text = (
        f"**{filename}** is ready to submit — **{row_count} {rows_label}**.\n\n"
        "Review the details below. You can edit the **Sales Amount (SAR)** "
        "before submitting."
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
            # Deterministic wording — the filename and row count must reach
            # the user unparaphrased, and an LLM rewrite reintroduced
            # "Good news!"-style variance on every re-render.
            "verbatim": True,
        },
    }
