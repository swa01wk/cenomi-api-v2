"""Sales Data Payload Builder Node.

Reads the (possibly edited) rows from the action_payload sent by the frontend
after the user clicks Submit in the SalesDataReviewCard, and builds the
JSON array payload for POST /api/v1/sales-data.

Expected action_payload from frontend:
  {
    "rows": [
      {
        "lease_code": "LC-1001",
        "sales_period_start": "2026-08-01",
        "sales_period_end": "2026-08-31",
        "sales_amt": 85000.0
      },
      ...
    ]
  }

Stores the built payload in backend_refs["submit_payload"].
"""

from __future__ import annotations

import json
import logging
from typing import Any

import structlog

from app.agents.graph.state import ServiceRequestState
from app.observability.decorators import trace_node

log = structlog.get_logger(__name__)
logger = logging.getLogger(__name__)


@trace_node("sales_data_payload_builder", "AGENT")
async def sales_data_payload_builder_node(state: ServiceRequestState) -> dict[str, Any]:
    """Build the POST /api/v1/sales-data payload from confirmed rows."""
    backend_refs: dict = dict(state.get("backend_refs") or {})

    # The frontend sends updated rows in corrected_fields or as the action payload
    corrected_fields: dict | None = state.get("corrected_fields")
    action_override: str | None = state.get("action_override")

    # Try to get rows from corrected_fields (frontend inline edits)
    edited_rows: list[dict] | None = None
    if corrected_fields and "rows" in corrected_fields:
        edited_rows = corrected_fields["rows"]
    elif corrected_fields and isinstance(corrected_fields, list):
        edited_rows = corrected_fields

    # Fall back to csv_rows stored in backend_refs
    if not edited_rows:
        edited_rows = backend_refs.get("csv_rows") or []

    if not edited_rows:
        log.error("sales_data_payload_builder.no_rows")
        return {
            "status": "WAITING_FOR_USER",
            "response_message": (
                "I couldn't find the rows to submit. "
                "Please re-upload your CSV file."
            ),
            "workflow_stage": "UPLOAD_INTENT",
        }

    # Build clean payload — only the fields the backend expects
    payload: list[dict] = []
    for row in edited_rows:
        amt_raw = row.get("sales_amt")
        try:
            amt = float(str(amt_raw).replace(",", "")) if amt_raw is not None else 0.0
        except (ValueError, TypeError):
            amt = 0.0

        payload.append({
            "lease_code": str(row.get("lease_code", "")).strip(),
            "sales_period_start": str(row.get("sales_period_start", "")).strip(),
            "sales_period_end": str(row.get("sales_period_end", "")).strip(),
            "sales_amt": amt,
        })

    backend_refs["submit_payload"] = payload
    log.info("sales_data_payload_builder.built", row_count=len(payload))

    return {
        "backend_refs": backend_refs,
        "workflow_stage": "SUBMITTING",
        "status": "IN_PROGRESS",
    }
