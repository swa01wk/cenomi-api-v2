"""Submission Error Node.

Handles all-or-nothing backend 400 responses from POST /api/v1/sales-data.

Two error types:
  1. data_error  — genuine field mismatch (wrong dates, bad amount)
  2. stale_state — rows were valid at Layer 2 time but the backend row
                   changed status between validation and submission
                   (concurrent upload via portal or API).
  3. network     — connectivity failure

Both paths route back to UPLOAD_INTENT so the user can re-upload.
"""

from __future__ import annotations

from typing import Any

import structlog

from app.agents.graph.state import ServiceRequestState
from app.observability.decorators import trace_node

log = structlog.get_logger(__name__)


def _is_stale_state_error(errors: list[dict]) -> bool:
    """Heuristic: if the error code is 'no_pending_row', the row changed status."""
    return any(e.get("code") == "no_pending_row" for e in errors)


@trace_node("submission_error", "AGENT")
async def submission_error_node(state: ServiceRequestState) -> dict[str, Any]:
    """Surface submission failure to the user and route back to re-upload."""
    backend_refs: dict = state.get("backend_refs") or {}
    errors: list[dict] = backend_refs.get("submission_errors") or []
    error_type: str = backend_refs.get("submission_error_type", "data")

    log.warning(
        "submission_error.reported",
        error_type=error_type,
        error_count=len(errors),
    )

    if error_type == "network":
        message = (
            "⚠️ **Submission failed** — A connectivity issue prevented the data "
            "from being saved. Please try again in a moment.\n\n"
            "Your CSV has been retained — click Submit again when you're ready."
        )
    elif error_type == "server_error":
        # A clean 5xx from cenomi-api itself (request reached the server,
        # server errored) — distinct from "network" (request never got a
        # response) so the wording doesn't misleadingly blame connectivity.
        message = (
            "⚠️ **Submission failed** — The sales data service ran into an error "
            "while saving your data. Please try again in a moment, or contact "
            "support if this keeps happening.\n\n"
            "Your CSV has been retained — click Submit again when you're ready."
        )
    elif error_type == "stale_state" or _is_stale_state_error(errors):
        message = (
            "⚠️ **Submission rejected** — One or more leases in your file were updated "
            "by another channel while we were processing your submission.\n\n"
            "**None of your rows were saved** (the backend applies an all-or-nothing transaction).\n\n"
            "Please re-upload your file or check the **Sales Data listing page** for the latest status."
        )
    else:
        error_lines = []
        for e in errors:
            idx = e.get("index", "?")
            msg = e.get("message", "Unknown error")
            field = e.get("field", "")
            # idx defaults to "?" when cenomi-api's error entry has no
            # "index" field — int(idx) + 1 then crashes the whole turn with
            # a 500 instead of just showing the row label as "?".
            try:
                row_label = str(int(idx) + 1)
            except (TypeError, ValueError):
                row_label = str(idx)
            error_lines.append(
                f"• Row {row_label} ({field}): {msg}"
            )

        error_text = "\n".join(error_lines) if error_lines else "No details provided."
        message = (
            "❌ **Submission failed** — The backend rejected the following row(s):\n\n"
            f"{error_text}\n\n"
            "**None of your rows were saved** because all rows must be valid for the "
            "submission to succeed.\n\n"
            "Please correct the issues and re-upload your CSV file."
        )

    return {
        "workflow_stage": "UPLOAD_INTENT",
        "status": "WAITING_FOR_USER",
        "response_message": message,
        "response_ui": {
            "type": "submission_error",
            "error_type": error_type,
            "errors": errors,
            "message": message,
            "show_reupload": True,
        },
    }
