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

from app.agents.graph.nodes.sales_data.period_utils import (
    format_year_month,
    year_month_from_start,
)
from app.agents.graph.state import ServiceRequestState
from app.agents.services.sales_data_api_client import (
    SalesDataApiClient,
    partition_rows_by_lease_access,
)
from app.agents.services.sales_data_csv import (
    normalize_sales_data_rows,
    parse_sales_period_date,
)
from app.api.routes.sales_data_upload import get_uploaded_csv
from app.core.request_context import get_current_bearer_token
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
        amt_reason = "missing"
        if not amt_str:
            amt_fixable = True
        else:
            try:
                amount = float(amt_str.replace(",", ""))
                if amount <= 0:
                    amt_fixable = True
                    amt_reason = "not_positive"
            except ValueError:
                row_errors.append(
                    f"Sales amount '{amt_str}' is not a valid number"
                )

        if row_errors:
            format_errors.append({"row": i, "lease_code": lease_code, "errors": row_errors})
        elif amt_fixable:
            amt_missing_rows.append({
                "row": i,
                "lease_code": lease_code,
                "reason": amt_reason,
                "value": amt_str,
            })

    return format_errors, amt_missing_rows


async def _lease_check(
    rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(lease issues, rows that pass) — never blocks on an API failure."""
    try:
        client = SalesDataApiClient(bearer_token=get_current_bearer_token())
        backend_rows = await client.get_sales_data(disable_pagination=True)
    except Exception:
        log.warning("csv_validation.lease_check_failed", exc_info=True)
        return [], []
    result = partition_rows_by_lease_access(rows, backend_rows)
    issues: list[dict[str, Any]] = []
    for row in result["no_access"]:
        issues.append({"lease_code": row.get("lease_code"), "text": "is not linked to your account"})
    for row in result["no_pending_row"]:
        ym = year_month_from_start(str(row.get("sales_period_start") or ""))
        month = format_year_month(ym) if ym else "this period"
        issues.append({
            "lease_code": row.get("lease_code"),
            "text": f"has no pending sales data for {month} (it may already have been submitted)",
        })
    for row in result.get("identity_mismatch") or []:
        issues.append({"lease_code": row.get("lease_code"), "text": str(row.get("_error") or "brand or property does not match the lease")})
    return issues, result["valid"]


def _file_report(
    filename: str,
    amt_rows: list[dict[str, Any]],
    lease_issues: list[dict[str, Any]],
    ready_rows: list[dict[str, Any]],
) -> str:
    """One summary of everything wrong with the file, plus what is fine."""
    lines: list[str] = []
    for issue in lease_issues:
        lines.append(f"- **{issue['lease_code']}** {issue['text']}")
    lease_problem = {str(i["lease_code"]).lower() for i in lease_issues}
    for row in amt_rows:
        if str(row.get("lease_code") or "").lower() in lease_problem:
            continue
        if row.get("reason") == "not_positive":
            lines.append(
                f"- **{row['lease_code']}**: the sales amount ({row.get('value')}) "
                "must be greater than 0"
            )
        else:
            lines.append(f"- **{row['lease_code']}**: the sales amount is missing")
    count = len(lines)
    head = (
        f"I checked **{filename}**. {count} row{'s' if count != 1 else ''} "
        f"need{'s' if count == 1 else ''} attention:"
    )
    ok_codes = [
        str(r.get("lease_code"))
        for r in ready_rows
        if str(r.get("lease_code") or "").lower()
        not in {str(a.get("lease_code") or "").lower() for a in amt_rows}
    ]
    tail = (
        f"\n\nReady to submit: {', '.join(f'**{c}**' for c in ok_codes)}."
        if ok_codes
        else ""
    )
    return head + "\n" + "\n".join(lines) + tail


def _verbatim_reply(message: str) -> dict[str, Any]:
    """Re-upload prompt shown exactly as written (no LLM rewrite)."""
    return {
        "status": "WAITING_FOR_USER",
        "workflow_stage": "UPLOAD_INTENT",
        "response_message": message,
        # csv_validation_error card with no row errors = the re-attach control.
        "response_ui": {
            "type": "csv_validation_error",
            "errors": [],
            "show_file_upload": True,
            "verbatim": True,
            "message": message,
        },
    }


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
        return _verbatim_reply(
            "I couldn't find the uploaded CSV file. "
            "Please upload your CSV file again using the attachment button."
        )

    rows: list[dict] = normalize_sales_data_rows(cached["rows"])
    filename: str = cached["filename"]

    log.info("csv_validation.start", filename=filename, row_count=len(rows))

    if not rows:
        return _verbatim_reply(
            f"The file **{filename}** appears to be empty. "
            "Please upload a CSV with at least one data row."
        )

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
                f"**Validation failed** for **{filename}** — {len(format_errors)} row(s) have errors:\n\n"
                f"{error_text}\n\n"
                "Please correct the file and upload it again. "
                "All rows must be valid before submission."
            ),
            "response_ui": {
                "type": "csv_validation_error",
                "filename": filename,
                "errors": format_errors,
                "message": f"Validation failed — {len(format_errors)} row(s) have errors. Please fix and re-upload.",
                # Exact row numbers/errors must reach the user unparaphrased.
                "verbatim": True,
            },
        }

    # ── Every row lacks an amount → the blank downloaded template ─────────
    # Never treat it as data or offer to "submit everything" — nothing was filled in.
    if amt_missing_rows and len(amt_missing_rows) == len(rows):
        log.info("csv_validation.blank_template", row_count=len(rows))
        return _verbatim_reply(
            f"**{filename}** has no sales amounts filled in — it looks like the blank "
            "template. Please enter the **Sales Amount** for each lease in the file "
            "and upload it again.\n\n"
            "Nothing has been submitted."
        )

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

        # Check every row against the tenant's leases *before* asking for
        # amounts — otherwise a lease that isn't theirs (or isn't pending) is
        # only discovered after they have typed the missing figures.
        lease_issues, ready_rows = await _lease_check(clean_rows)
        report = _file_report(filename, amt_missing_rows, lease_issues, ready_rows)
        if lease_issues:
            log.info(
                "csv_validation.lease_issues_with_missing_amt",
                lease_issues=len(lease_issues),
                amt_issues=len(amt_missing_rows),
            )
            message = (
                f"{report}\n\n"
                "Please correct the file and upload it again. "
                "Nothing has been submitted."
            )
            return {
                "status": "WAITING_FOR_USER",
                "workflow_stage": "UPLOAD_INTENT",
                "response_message": message,
                "response_ui": {
                    "type": "csv_validation_error",
                    "errors": [],
                    "show_file_upload": True,
                    "verbatim": True,
                    "message": message,
                },
            }

        first = next((r["lease_code"] for r in amt_missing_rows if r.get("lease_code")), "LC-1234")
        log.info("csv_validation.fixable_missing_amt", missing_count=len(amt_missing_rows))
        message = (
            f"{report}\n\n"
            "Please share the correct amounts, for example: "
            f'"{first} is SAR 85,000". I will then submit the whole file.'
        )
        return {
            "backend_refs": backend_refs,
            "workflow_stage": "CSV_CORRECTION",
            "status": "WAITING_FOR_USER",
            "response_message": message,
            "response_ui": {
                "type": "message",
                # The exact list of leases needing an amount, and the example
                # phrasing, must reach the user unparaphrased — an LLM rewrite
                # here previously produced a different tone/example every time.
                "verbatim": True,
                "message": message,
            },
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
