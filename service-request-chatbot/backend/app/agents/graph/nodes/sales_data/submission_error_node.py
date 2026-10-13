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

import os
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
    # Shared with the CSV path (sales_data_api_submission_node) — the wording
    # must not assume "your CSV file" when this came from typed manual entry.
    is_manual = backend_refs.get("submission_source") == "manual"

    log.warning(
        "submission_error.reported",
        error_type=error_type,
        error_count=len(errors),
        submission_source=backend_refs.get("submission_source") or "csv",
    )

    # Non-prod: append the real exception/status text already captured by the
    # submission node instead of leaving the user with only a generic message.
    is_dev = os.getenv("ENVIRONMENT", "development") == "development"
    real_detail = errors[0].get("message") if errors else None
    dev_detail = (
        f"\n\n_(Dev detail: {real_detail})_" if is_dev and real_detail else ""
    )

    retry_line = (
        "Please share the details again when you are ready."
        if is_manual
        else "Your file has been kept — select **Yes, please submit** again when you are ready."
    )
    reupload_phrase = "re-enter the details" if is_manual else "re-upload your CSV file"
    reupload_or_check_phrase = (
        "try again or check the **Sales Data listing page** for the latest status"
        if is_manual
        else "re-upload your file or check the **Sales Data listing page** for the latest status"
    )

    if error_type == "network":
        message = (
            "**Submission failed** — a connectivity issue prevented the data "
            "from being saved. Please try again in a moment.\n\n"
            + retry_line
            + dev_detail
        )
    elif error_type == "server_error":
        # A clean 5xx from cenomi-api itself (request reached the server,
        # server errored) — distinct from "network" (request never got a
        # response) so the wording doesn't misleadingly blame connectivity.
        message = (
            "**Submission failed** — the sales data service encountered an error "
            "while saving your data. Please try again in a moment, or contact "
            "support if this keeps happening.\n\n"
            + retry_line
            + dev_detail
        )
    elif error_type == "stale_state" or _is_stale_state_error(errors):
        message = (
            "**Submission not completed** — one or more of these leases were updated "
            "elsewhere while your submission was being processed.\n\n"
            "**No rows were saved**; a submission is saved only when every row is accepted.\n\n"
            f"Please {reupload_or_check_phrase}."
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
            "**Submission failed** — the following row(s) could not be accepted:\n\n"
            f"{error_text}\n\n"
            "**No rows were saved**; a submission is saved only when every row is accepted.\n\n"
            f"Please correct the issues and {reupload_phrase}."
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
            # See submission_success_node.py — without this the LLM rewrite
            # still runs on this terminal-ish path and can vary or soften
            # wording that needs to stay precise and consistent.
            "verbatim": True,
        },
    }
