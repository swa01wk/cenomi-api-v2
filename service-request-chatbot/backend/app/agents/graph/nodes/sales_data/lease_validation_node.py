"""Lease Validation Node — Layer 2.

Performs a single GET /api/v1/sales-data?disable_pagination=true call and
partitions each CSV row into one of three buckets:

  (a) no_access       — lease_code not visible to this user
  (b) no_pending_row  — accessible but no PENDING/NEED_REUPLOAD for this period
  (c) valid           — has a matching PENDING or NEED_REUPLOAD row

Layer 2 error handling (atomic rejection):
  - If ALL rows fall into no_access/no_pending_row → stale_notification_node
  - If SOME rows are invalid → show per-row error table, ask user to fix
  - If ALL rows are valid → advance to sales_data_confirmation

On success: enriches backend_refs["csv_rows"] with lease_id and exact dates
from the backend row (needed for exact-match POST payload).
"""

from __future__ import annotations

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


@trace_node("lease_validation", "AGENT")
async def lease_validation_node(state: ServiceRequestState) -> dict[str, Any]:
    """Layer 2 — call GET /api/v1/sales-data and partition rows by access/pending."""
    backend_refs: dict = dict(state.get("backend_refs") or {})
    csv_rows: list[dict] = backend_refs.get("csv_rows") or []

    if not csv_rows:
        log.warning("lease_validation.no_csv_rows")
        return {
            "status": "WAITING_FOR_USER",
            "workflow_stage": "UPLOAD_INTENT",
            "response_message": "No CSV rows found to validate. Please upload your file again.",
        }

    token = get_current_bearer_token()
    client = SalesDataApiClient(bearer_token=token)

    try:
        backend_rows = await client.get_sales_data(disable_pagination=True)
    except Exception as exc:
        import os
        api_url = os.getenv("SALES_DATA_API_BASE_URL", "http://localhost:8001")
        is_dev = os.getenv("ENVIRONMENT", "development") == "development"
        dev_hint = (
            f"\n\n_(Dev: make sure the mock backend is running on `{api_url}` — "
            f"`cd mock-backend && uvicorn main:app --port 8001 --reload`)_"
            if is_dev else ""
        )
        log.error("lease_validation.get_failed", api_url=api_url, error=str(exc))
        return {
            "status": "WAITING_FOR_USER",
            "response_message": (
                "⚠️ I couldn't reach the sales data service right now. "
                "Please try again in a moment — if the issue persists, contact support."
                + dev_hint
            ),
        }

    result = partition_rows_by_lease_access(csv_rows, backend_rows)
    valid = result["valid"]
    no_access = result["no_access"]
    no_pending = result["no_pending_row"]

    log.info(
        "lease_validation.result",
        valid=len(valid),
        no_access=len(no_access),
        no_pending=len(no_pending),
    )

    # ── All rows fail ──────────────────────────────────────────────────────
    if not valid:
        backend_refs["layer2_errors"] = no_access + no_pending
        backend_refs["layer2_stale"] = True
        return {
            "backend_refs": backend_refs,
            "workflow_stage": "STALE_NOTIFICATION",
            "status": "WAITING_FOR_USER",
        }

    # ── Some rows fail ─────────────────────────────────────────────────────
    if no_access or no_pending:
        error_lines = []
        for row in no_access:
            error_lines.append(f"• {row['lease_code']}: {row['_error']}")
        for row in no_pending:
            error_lines.append(f"• {row['lease_code']}: {row['_error']}")

        return {
            "status": "WAITING_FOR_USER",
            "workflow_stage": "UPLOAD_INTENT",
            "response_message": (
                f"❌ **Lease validation failed** — {len(no_access) + len(no_pending)} row(s) cannot be submitted:\n\n"
                + "\n".join(error_lines)
                + "\n\nPlease remove the invalid rows from your CSV and re-upload. "
                "All rows must pass validation before the file can be submitted."
            ),
            "response_ui": {
                "type": "lease_validation_error",
                "errors": no_access + no_pending,
                "message": f"Lease validation failed — {len(no_access) + len(no_pending)} row(s) invalid.",
            },
        }

    # ── All rows valid ─────────────────────────────────────────────────────
    backend_refs["csv_rows"] = valid  # enriched with lease_id + exact dates
    backend_refs["layer2_errors"] = []

    return {
        "backend_refs": backend_refs,
        "workflow_stage": "AWAITING_TABLE_CONFIRM",
        "status": "IN_PROGRESS",
    }
