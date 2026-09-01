"""CSV Validation Node — Layer 1.

Performs format-level validation on the uploaded CSV rows stored in
the in-memory file cache by the ``/api/v2/sales-data/upload`` route.

Layer 1 checks (no API calls):
  1. CSV rows present in session cache
  2. Required headers present (already checked by upload route)
  3. No empty required fields per row (lease code, start date, end date)
  4. sales_amt: must be a positive number (> 0)
  5. Date format: Start date / End date accept DD-MM-YYYY or YYYY-MM-DD
  6. Future date: sales_period_start must NOT be after today
  7. Brand / Property are optional at Layer 1 (legacy CSVs omit them).
     Layer 2 compares them to the lease on GET /sales-data when present.
  8. Hybrid rejection strategy:
     - Format / date errors (unfixable without re-upload) → full atomic rejection.
     - Missing / zero sales_amt only → store rows in backend_refs["csv_rows_all"]
       and route to CSV_CORRECTION so the user can provide the values in chat.

On success: stores validated rows in backend_refs["csv_rows"] and advances
workflow_stage to CSV_LAYER2_VALIDATION.

On fixable (sales_amt missing): stores rows in backend_refs["csv_rows_all"]
and advances workflow_stage to CSV_CORRECTION.

On unfixable error: returns WAITING_FOR_USER with a formatted error message.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

import structlog

from app.agents.graph.state import ServiceRequestState
from app.agents.services.sales_data_csv import (
    normalize_sales_data_rows,
    parse_sales_period_date,
)
from app.api.routes.sales_data_upload import get_uploaded_csv
from app.observability.decorators import trace_node

log = structlog.get_logger(__name__)
logger = logging.getLogger(__name__)

REQUIRED_FIELDS = [
    "lease_code",
    "sales_period_start",
    "sales_period_end",
    "sales_amt",
]


def _clean_row(row: dict, *, amount: Any) -> dict:
    return {
        "lease_code": (row.get("lease_code") or "").strip(),
        "brand": (row.get("brand") or "").strip(),
        "property": (row.get("property") or "").strip(),
        "sales_period_start": (row.get("sales_period_start") or "").strip(),
        "sales_period_end": (row.get("sales_period_end") or "").strip(),
        "sales_amt": amount,
    }


def _validate_rows(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """Return (format_errors, amt_missing_rows).

    format_errors: rows with unfixable issues (bad date format, future date,
    empty lease/dates). amt_missing_rows: rows whose ONLY problem is
    a missing/zero sales_amt. These can be corrected conversationally.
    Brand and Property are optional.
    """
    today = date.today()
    format_errors: list[dict] = []
    amt_missing_rows: list[dict] = []

    for i, row in enumerate(rows, start=1):
        row_errors: list[str] = []

        lease_code = (row.get("lease_code") or "").strip()
        start_str = (row.get("sales_period_start") or "").strip()
        end_str = (row.get("sales_period_end") or "").strip()
        amt_str = str(row.get("sales_amt") or "").strip()

        if not lease_code:
            row_errors.append("Lease code is empty")
        if not start_str:
            row_errors.append("Start date is empty")
        if not end_str:
            row_errors.append("End date is empty")

        start_iso = parse_sales_period_date(start_str) if start_str else None
        end_iso = parse_sales_period_date(end_str) if end_str else None
        if start_str and not start_iso:
            row_errors.append(
                f"Start date '{start_str}' is not a valid date "
                "(use DD-MM-YYYY or YYYY-MM-DD)"
            )
        if end_str and not end_iso:
            row_errors.append(
                f"End date '{end_str}' is not a valid date "
                "(use DD-MM-YYYY or YYYY-MM-DD)"
            )

        if start_iso:
            try:
                start_date = date.fromisoformat(start_iso)
                if start_date > today:
                    row_errors.append(
                        "Period cannot be in the future. "
                        "Please use the dates from the downloaded template."
                    )
            except ValueError:
                pass
            row["sales_period_start"] = start_iso
        if end_iso:
            row["sales_period_end"] = end_iso

        amt_fixable = False
        if not amt_str:
            amt_fixable = True
        else:
            try:
                amount = float(amt_str.replace(",", ""))
                if amount <= 0:
                    amt_fixable = True
            except ValueError:
                row_errors.append(
                    f"Sales amount '{amt_str}' is not a valid number"
                )

        if row_errors:
            format_errors.append({"row": i, "lease_code": lease_code, "errors": row_errors})
        elif amt_fixable:
            amt_missing_rows.append({"row": i, "lease_code": lease_code})

    return format_errors, amt_missing_rows


@trace_node("csv_validation", "AGENT")
async def csv_validation_node(state: ServiceRequestState) -> dict[str, Any]:
    """Layer 1 CSV validation — format, sanity, future-date, zero-amount checks."""
    backend_refs: dict = dict(state.get("backend_refs") or {})
    corrected_fields: dict = dict(state.get("corrected_fields") or {})

    # The upload endpoint caches the CSV under the upload's own UUID (which may
    # differ from the DB session UUID).  The frontend passes it via corrected_fields
    # so we always use the exact key the upload stored under.
    csv_session_id: str | None = (
        corrected_fields.get("csv_session_id")
        or backend_refs.get("csv_session_id")
        or state.get("session_id")
    )

    # Retrieve CSV from cache
    cached = get_uploaded_csv(csv_session_id or "") if csv_session_id else None
    if not cached:
        log.warning("csv_validation.no_csv_found", csv_session_id=csv_session_id)
        return {
            "status": "WAITING_FOR_USER",
            "response_message": (
                "I couldn't find the uploaded CSV file. "
                "Please upload your CSV file again using the attachment button."
            ),
            "workflow_stage": "UPLOAD_INTENT",
        }

    rows: list[dict] = normalize_sales_data_rows(cached["rows"])
    filename: str = cached["filename"]

    log.info("csv_validation.start", filename=filename, row_count=len(rows))

    if not rows:
        return {
            "status": "WAITING_FOR_USER",
            "response_message": (
                f"The file **{filename}** appears to be empty. "
                "Please upload a CSV with at least one data row."
            ),
            "workflow_stage": "UPLOAD_INTENT",
        }

    format_errors, amt_missing_rows = _validate_rows(rows)

    # ── Unfixable format/date errors → require re-upload ─────────────────
    if format_errors:
        error_lines = []
        for e in format_errors:
            lc = e["lease_code"] or f"Row {e['row']}"
            for msg in e["errors"]:
                error_lines.append(f"• Row {e['row']} ({lc}): {msg}")

        error_text = "\n".join(error_lines)
        log.info("csv_validation.failed_unfixable", error_count=len(format_errors))
        return {
            "status": "WAITING_FOR_USER",
            "workflow_stage": "UPLOAD_INTENT",
            "response_message": (
                f"❌ **Validation failed** for **{filename}** — {len(format_errors)} row(s) have errors:\n\n"
                f"{error_text}\n\n"
                "Please correct the file and upload it again. "
                "All rows must be valid before submission."
            ),
            "response_ui": {
                "type": "csv_validation_error",
                "filename": filename,
                "errors": format_errors,
                "message": f"Validation failed — {len(format_errors)} row(s) have errors. Please fix and re-upload.",
            },
        }

    # ── Only missing sales_amt → store rows, ask conversationally ─────────
    if amt_missing_rows:
        # Normalise rows (strip whitespace, keep amt as-is for correction node to patch)
        clean_rows: list[dict] = [
            _clean_row(row, amount=row.get("sales_amt") or None) for row in rows
        ]

        backend_refs["csv_rows_all"] = clean_rows
        backend_refs["csv_filename"] = filename
        if csv_session_id:
            backend_refs["csv_session_id"] = csv_session_id

        examples = [
            f'"{r["lease_code"]} is SAR 85,000"'
            for r in amt_missing_rows[:2]
            if r.get("lease_code")
        ]
        example = " and ".join(examples) if examples else '"LC-1234 is SAR 85,000"'
        missing_list = ", ".join(f"**{r['lease_code']}**" for r in amt_missing_rows)
        log.info("csv_validation.fixable_missing_amt", missing_count=len(amt_missing_rows))
        return {
            "backend_refs": backend_refs,
            "workflow_stage": "CSV_CORRECTION",
            "status": "WAITING_FOR_USER",
            "response_message": (
                f"I checked **{filename}** and {len(amt_missing_rows)} row(s) need a sales amount: "
                f"{missing_list}.\n\n"
                "Please tell me the amounts — for example: "
                f"{example} — "
                "and I'll submit everything for you without re-uploading."
            ),
        }

    # ── All rows valid → normalise and proceed to Layer 2 ─────────────────
    clean_rows = [
        _clean_row(
            row,
            amount=float(str(row["sales_amt"]).replace(",", "").strip()),
        )
        for row in rows
    ]

    backend_refs["csv_rows"] = clean_rows
    backend_refs["csv_rows_all"] = clean_rows
    backend_refs["csv_filename"] = filename
    if csv_session_id:
        backend_refs["csv_session_id"] = csv_session_id

    log.info("csv_validation.passed", row_count=len(clean_rows))
    return {
        "backend_refs": backend_refs,
        "workflow_stage": "CSV_LAYER2_VALIDATION",
        "status": "IN_PROGRESS",
    }
