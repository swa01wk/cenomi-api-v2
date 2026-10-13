"""Sales Data Payload Builder Node.

Reads the (possibly edited) rows from the action_payload sent by the frontend
after the user clicks Submit in the SalesDataReviewCard, and builds the
JSON array payload for POST /api/v1/sales-data.

Expected action_payload from frontend:
  {
    "rows": [
      {
        "lease_code": "<LEASE_CODE>",
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
from app.agents.services.sales_data_api_client import (
    SalesDataApiClient,
    partition_rows_by_lease_access,
)
from app.core.request_context import get_current_bearer_token
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
    invalid_leases: list[str] = []
    for row in edited_rows:
        amt_raw = row.get("sales_amt")
        try:
            amt = float(str(amt_raw).replace(",", "")) if amt_raw is not None else None
        except (ValueError, TypeError):
            amt = None

        # A parse failure/blank was silently coerced to 0.0 here, and a
        # negative edited-in-the-review-table amount wasn't checked at all —
        # both submitted straight through to cenomi-api. Reject the whole
        # batch instead (mirrors csv_validation_node's `amount <= 0` rule)
        # so the admin never sees a negative/zero sales figure to approve.
        if amt is None or amt <= 0:
            invalid_leases.append(str(row.get("lease_code", "")).strip() or "?")
            continue

        payload.append({
            "lease_code": str(row.get("lease_code", "")).strip(),
            "sales_period_start": str(row.get("sales_period_start", "")).strip(),
            "sales_period_end": str(row.get("sales_period_end", "")).strip(),
            "sales_amt": amt,
        })

    if invalid_leases:
        log.warning(
            "sales_data_payload_builder.invalid_amount",
            leases=invalid_leases,
        )
        return {
            "status": "WAITING_FOR_USER",
            "response_message": (
                "**Sales amount must be greater than 0** for: "
                + ", ".join(f"**{code}**" for code in invalid_leases)
                + ". Please correct the amount and submit again."
            ),
            "workflow_stage": "AWAITING_TABLE_CONFIRM",
        }

    # Re-check every row still matches a PENDING/NEED_REUPLOAD record right
    # before POSTing. lease_validation_node (Layer 2) already did this check
    # once, but that was at review-table build time — the row could have
    # been edited since, or (more commonly) the underlying record could have
    # been consumed/changed by another party (approved, re-uploaded elsewhere,
    # or the pending window closed) in the time between then and Submit.
    # Without this, the tenant sees the row marked "ready to submit" and only
    # learns it's stale from a raw 400 after clicking Submit.
    token = get_current_bearer_token()
    client = SalesDataApiClient(bearer_token=token)
    try:
        backend_rows = await client.get_sales_data(disable_pagination=True)
    except Exception as exc:
        log.error("sales_data_payload_builder.revalidate_failed", exc_info=True)
        return {
            "status": "WAITING_FOR_USER",
            "workflow_stage": "AWAITING_TABLE_CONFIRM",
            "response_message": (
                "I couldn't re-check your submission against the latest "
                "records. Please try **Yes, please submit** again in a moment."
            ),
        }

    result = partition_rows_by_lease_access(payload, backend_rows)
    stale = (
        (result.get("no_access") or [])
        + (result.get("no_pending_row") or [])
        + (result.get("identity_mismatch") or [])
    )
    if stale:
        stale_codes = [str(r.get("lease_code", "")).strip() or "?" for r in stale]
        log.warning(
            "sales_data_payload_builder.stale_before_submit",
            leases=stale_codes,
        )
        return {
            "status": "WAITING_FOR_USER",
            "workflow_stage": "UPLOAD_INTENT",
            "response_message": (
                "**This can no longer be submitted** — the Lease Code and "
                "sales period for "
                + ", ".join(f"**{code}**" for code in stale_codes)
                + " no longer match a pending sales record (it may have "
                "already been submitted, approved, or re-uploaded elsewhere). "
                "Please check the Sales Data listing page for the current "
                "status, or re-upload if you still need to submit this data."
            ),
        }

    backend_refs["submit_payload"] = payload
    log.info("sales_data_payload_builder.built", row_count=len(payload))

    return {
        "backend_refs": backend_refs,
        "workflow_stage": "SUBMITTING",
        "status": "IN_PROGRESS",
    }
