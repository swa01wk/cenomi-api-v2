"""Lease Validation Node — Layer 2.

Performs a single GET /api/v1/sales-data?disable_pagination=true call and
partitions each CSV row into:

  (a) no_access           — lease_code not visible to this user
  (b) no_pending_row      — accessible but no PENDING/NEED_REUPLOAD for this period
  (c) identity_mismatch   — lease/period match, but Brand/Property do not match
                            the platform names on that lease (case/spacing ignored)
  (d) valid               — matching PENDING/NEED_REUPLOAD row; Brand/Property
                            omitted or matching; canonical platform names applied

Layer 2 error handling (atomic rejection):
  - If ALL rows are no_access / no_pending_row → stale_notification_node
  - If ANY row has an identity mismatch (or mixed Layer 2 errors) → ask to
    correct the CSV and re-upload (dropzone stays on)
  - If ALL rows are valid and the CSV month matches (or no month was asked)
    → advance to sales_data_confirmation
  - If ALL rows are valid but the CSV month differs from the month the tenant
    asked to upload → AWAITING_PERIOD_MISMATCH_CONFIRM warning
"""

from __future__ import annotations

import logging
from typing import Any

import structlog

from app.agents.graph.state import ServiceRequestState
from app.agents.graph.nodes.sales_data.period_utils import (
    asked_period_from_state,
    period_mismatch_warning,
)
from app.agents.services.sales_data_api_client import (
    SalesDataApiClient,
    partition_rows_by_lease_access,
)
from app.core.request_context import get_current_bearer_token
from app.observability.decorators import trace_node

log = structlog.get_logger(__name__)
logger = logging.getLogger(__name__)


def _layer2_reupload_payload(errors: list[dict[str, Any]]) -> dict[str, Any]:
    error_lines = [f"• {row.get('lease_code', '')}: {row.get('_error', '')}" for row in errors]
    message = (
        f"❌ **Lease validation failed** — {len(errors)} row(s) cannot be submitted:\n\n"
        + "\n".join(error_lines)
        + "\n\nPlease correct the file and **upload it again**. "
        "All rows must pass validation before the file can be submitted."
    )
    return {
        "status": "WAITING_FOR_USER",
        "workflow_stage": "UPLOAD_INTENT",
        "response_message": message,
        "response_ui": {
            "type": "lease_validation_error",
            "errors": errors,
            "show_file_upload": True,
            "verbatim": True,
            "message": message,
        },
    }


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
    identity = result.get("identity_mismatch") or []
    layer2_errors = no_access + no_pending + identity

    log.info(
        "lease_validation.result",
        valid=len(valid),
        no_access=len(no_access),
        no_pending=len(no_pending),
        identity_mismatch=len(identity),
    )

    # ── All rows fail ──────────────────────────────────────────────────────
    if not valid:
        # Wrong Brand/Property on a real pending lease is a tenant CSV mistake,
        # not a stale notification from the platform / notification agent.
        if identity:
            return _layer2_reupload_payload(layer2_errors)
        backend_refs["layer2_errors"] = layer2_errors
        backend_refs["layer2_stale"] = True
        return {
            "backend_refs": backend_refs,
            "workflow_stage": "STALE_NOTIFICATION",
            "status": "WAITING_FOR_USER",
        }

    # ── Some rows fail ─────────────────────────────────────────────────────
    if layer2_errors:
        return _layer2_reupload_payload(layer2_errors)

    # ── All rows valid ─────────────────────────────────────────────────────
    backend_refs["csv_rows"] = valid  # enriched with lease_id + exact dates
    backend_refs["layer2_errors"] = []

    mismatch = period_mismatch_warning(asked_period_from_state({
        **state,
        "backend_refs": backend_refs,
    }), valid)
    if mismatch:
        backend_refs["period_mismatch"] = mismatch
        backend_refs["asked_period"] = asked_period_from_state({
            **state,
            "backend_refs": backend_refs,
        })
        collected = dict(state.get("collected_data") or {})
        if backend_refs.get("asked_period"):
            collected["_asked_period"] = backend_refs["asked_period"]
        collected["_csv_rows"] = valid
        collected["_csv_filename"] = backend_refs.get("csv_filename", "upload.csv")
        log.info(
            "lease_validation.period_mismatch",
            asked=mismatch.get("asked_year_month"),
            file=mismatch.get("file_year_months"),
        )
        return {
            "backend_refs": backend_refs,
            "collected_data": collected,
            "workflow_stage": "AWAITING_PERIOD_MISMATCH_CONFIRM",
            "status": "WAITING_FOR_USER",
            "response_message": mismatch["message"],
            "response_ui": {
                "type": "period_mismatch_warning",
                "asked_label": mismatch["asked_label"],
                "file_label": mismatch["file_label"],
                "includes_asked_month": bool(mismatch.get("includes_asked_month")),
                "show_file_upload": True,
                "verbatim": True,
                "message": mismatch["message"],
            },
        }

    backend_refs.pop("period_mismatch", None)
    return {
        "backend_refs": backend_refs,
        "workflow_stage": "AWAITING_TABLE_CONFIRM",
        "status": "IN_PROGRESS",
    }
