"""Cenomi platform sales-data CSV template parsing and Layer 1 validation."""

from __future__ import annotations

from calendar import monthrange
from datetime import date

import pytest

from app.agents.graph.nodes.sales_data.csv_validation_node import _validate_rows
from app.agents.services.sales_data_csv import (
    PLATFORM_CSV_HEADERS,
    parse_sales_data_csv,
    parse_sales_period_date,
    render_platform_csv,
    template_matrix_from_sales_data,
)
from app.api.routes.sales_data_upload import store_uploaded_csv


PLATFORM_HEADER = (
    "Lease code,Brand,Property,Start date(DD-MM-YYYY),"
    "End date(DD-MM-YYYY),Sales amount"
)


def _current_month() -> tuple[str, str, str, str]:
    today = date.today()
    start = today.replace(day=1)
    end = today.replace(day=monthrange(today.year, today.month)[1])
    return (
        start.strftime("%d-%m-%Y"),
        end.strftime("%d-%m-%Y"),
        start.isoformat(),
        end.isoformat(),
    )


def test_original_downloaded_template_file_headers() -> None:
    from pathlib import Path

    path = Path("/home/ayan/Downloads/salesdatatemplate (3).csv")
    if not path.is_file():
        pytest.skip("original Cenomi template not on disk")
    parsed = parse_sales_data_csv(path.read_text(encoding="utf-8-sig"))
    assert parsed.missing_headers == []
    assert parsed.rows == []


def test_header_only_platform_csv_has_no_missing_columns() -> None:
    parsed = parse_sales_data_csv(PLATFORM_HEADER + "\n")
    assert parsed.missing_headers == []
    assert parsed.rows == []


def test_parse_dd_mm_yyyy_to_iso() -> None:
    text = (
        PLATFORM_HEADER
        + "\nLC-1001,Noon Fashion,Riyadh Park,01-08-2026,31-08-2026,85000\n"
    )
    parsed = parse_sales_data_csv(text)
    assert parsed.missing_headers == []
    assert len(parsed.rows) == 1
    row = parsed.rows[0]
    assert row["lease_code"] == "LC-1001"
    assert row["brand"] == "Noon Fashion"
    assert row["property"] == "Riyadh Park"
    assert row["sales_period_start"] == "2026-08-01"
    assert row["sales_period_end"] == "2026-08-31"
    assert row["sales_amt"] == "85000"


def test_parse_accepts_iso_dates_as_well() -> None:
    text = (
        PLATFORM_HEADER
        + "\nLC-1001,Noon Fashion,Riyadh Park,2026-07-01,2026-07-31,4000\n"
    )
    parsed = parse_sales_data_csv(text)
    assert parsed.rows[0]["sales_period_start"] == "2026-07-01"
    assert parsed.rows[0]["sales_period_end"] == "2026-07-31"


def test_parse_prefers_day_month_year() -> None:
    # 01-02-2026 is 1 Feb, not 2 Jan — same as portal moment(['DD-MM-YYYY', ...]).
    assert parse_sales_period_date("01-02-2026") == "2026-02-01"


def test_brand_and_property_headers_are_optional() -> None:
    text = "Lease code,Property,Start date(DD-MM-YYYY),End date(DD-MM-YYYY),Sales amount\n"
    parsed = parse_sales_data_csv(text)
    assert parsed.missing_headers == []


def test_legacy_four_column_csv_without_brand_property() -> None:
    _, _, start_iso, end_iso = _current_month()
    text = (
        "lease_code,sales_period_start,sales_period_end,sales_amt\n"
        f"LC-1001,{start_iso},{end_iso},85000\n"
    )
    parsed = parse_sales_data_csv(text)
    assert parsed.missing_headers == []
    assert parsed.rows[0]["lease_code"] == "LC-1001"
    assert parsed.rows[0]["brand"] == ""
    assert parsed.rows[0]["property"] == ""
    assert parsed.rows[0]["sales_period_start"] == start_iso
    errors, amt_missing = _validate_rows(parsed.rows)
    assert errors == []
    assert amt_missing == []


def test_legacy_snake_case_headers_still_map() -> None:
    text = (
        "lease_code,brand,property,sales_period_start,sales_period_end,sales_amt\n"
        "LC-1002,Pure Gym,Riyadh Park,2026-08-01,2026-08-31,120000\n"
    )
    parsed = parse_sales_data_csv(text)
    assert parsed.missing_headers == []
    assert parsed.rows[0]["lease_code"] == "LC-1002"
    assert parsed.rows[0]["sales_period_start"] == "2026-08-01"


def test_empty_brand_and_property_are_allowed() -> None:
    start_dd, end_dd, _, _ = _current_month()
    text = (
        PLATFORM_HEADER
        + f"\nLC-1001,,,{start_dd},{end_dd},85000\n"
    )
    parsed = parse_sales_data_csv(text)
    errors, amt_missing = _validate_rows(parsed.rows)
    assert errors == []
    assert amt_missing == []
    assert parsed.rows[0]["brand"] == ""
    assert parsed.rows[0]["property"] == ""


def test_future_period_uses_template_wording() -> None:
    from datetime import timedelta

    future = date.today() + timedelta(days=40)
    start_dd = future.replace(day=1).strftime("%d-%m-%Y")
    end_dd = future.strftime("%d-%m-%Y")
    text = (
        PLATFORM_HEADER
        + f"\nLC-1001,Noon Fashion,Riyadh Park,{start_dd},{end_dd},85000\n"
    )
    parsed = parse_sales_data_csv(text)
    errors, _ = _validate_rows(parsed.rows)
    assert errors
    assert any("cannot be in the future" in msg for msg in errors[0]["errors"])


def test_invalid_date_kept_for_layer1_error() -> None:
    text = (
        PLATFORM_HEADER
        + "\nLC-1001,Noon Fashion,Riyadh Park,32-07-2026,31-07-2026,85000\n"
    )
    parsed = parse_sales_data_csv(text)
    errors, amt_missing = _validate_rows(parsed.rows)
    assert amt_missing == []
    assert errors
    assert any("not a valid date" in msg for msg in errors[0]["errors"])


def test_missing_amount_is_fixable_when_rest_is_valid() -> None:
    start_dd, end_dd, _, _ = _current_month()
    text = (
        PLATFORM_HEADER
        + f"\nLC-1001,Noon Fashion,Riyadh Park,{start_dd},{end_dd},\n"
    )
    parsed = parse_sales_data_csv(text)
    errors, amt_missing = _validate_rows(parsed.rows)
    assert errors == []
    assert len(amt_missing) == 1
    assert amt_missing[0]["lease_code"] == "LC-1001"


def test_valid_platform_row_passes_layer1() -> None:
    start_dd, end_dd, start_iso, _ = _current_month()
    text = (
        PLATFORM_HEADER
        + f"\nLC-1001,Noon Fashion,Riyadh Park,{start_dd},{end_dd},85000\n"
    )
    parsed = parse_sales_data_csv(text)
    errors, amt_missing = _validate_rows(parsed.rows)
    assert errors == []
    assert amt_missing == []
    assert parsed.rows[0]["sales_period_start"] == start_iso


def test_render_platform_csv_matches_cenomi_header() -> None:
    csv_text = render_platform_csv(
        [
            {
                "lease_code": "LC-1001",
                "brand": "Noon Fashion",
                "property": "Riyadh Park",
                "sales_period_start": "2026-08-01",
                "sales_period_end": "2026-08-31",
                "sales_amt": "85000",
            }
        ]
    )
    first_line = csv_text.splitlines()[0]
    assert first_line == ",".join(PLATFORM_CSV_HEADERS)
    assert "01-08-2026" in csv_text
    round_trip = parse_sales_data_csv(csv_text)
    assert round_trip.rows[0]["sales_period_start"] == "2026-08-01"


def test_template_matrix_uses_pending_rows_and_ddmmyyyy() -> None:
    matrix = template_matrix_from_sales_data(
        [
            {
                "lease_code": "LC-1001",
                "brand_name": "Noon Fashion",
                "property_name": "Riyadh Park",
                "sales_period_start": "2026-08-01",
                "sales_period_end": "2026-08-31",
                "status": "PENDING",
                "sales_amt": None,
            },
            {
                "lease_code": "LC-2001",
                "brand_name": "X",
                "property_name": "Y",
                "sales_period_start": "2026-07-01",
                "sales_period_end": "2026-07-31",
                "status": "SUBMITTED",
                "sales_amt": 1,
            },
        ]
    )
    assert matrix[0] == PLATFORM_CSV_HEADERS
    assert matrix[1] == [
        "LC-1001",
        "Noon Fashion",
        "Riyadh Park",
        "01-08-2026",
        "31-08-2026",
        "",
    ]
    assert len(matrix) == 2


@pytest.mark.asyncio
async def test_csv_validation_node_accepts_platform_template() -> None:
    from app.agents.graph.nodes.sales_data.csv_validation_node import (
        csv_validation_node,
    )

    start_dd, end_dd, start_iso, _ = _current_month()
    text = (
        PLATFORM_HEADER
        + f"\nLC-1001,Noon Fashion,Riyadh Park,{start_dd},{end_dd},85000\n"
    )
    parsed = parse_sales_data_csv(text)
    store_uploaded_csv("sess-platform-csv", parsed.rows, "salesdatatemplate.csv", text.encode())
    result = await csv_validation_node(
        {
            "session_id": "sess-platform-csv",
            "corrected_fields": {"csv_session_id": "sess-platform-csv"},
            "backend_refs": {},
        }
    )
    assert result["workflow_stage"] == "CSV_LAYER2_VALIDATION"
    row = result["backend_refs"]["csv_rows"][0]
    assert row["sales_period_start"] == start_iso
    assert row["brand"] == "Noon Fashion"
    assert row["property"] == "Riyadh Park"
    assert row["sales_amt"] == 85000.0


@pytest.mark.asyncio
async def test_csv_validation_node_accepts_legacy_four_column_csv() -> None:
    from app.agents.graph.nodes.sales_data.csv_validation_node import (
        csv_validation_node,
    )

    _, _, start_iso, end_iso = _current_month()
    text = (
        "lease_code,sales_period_start,sales_period_end,sales_amt\n"
        f"LC-1001,{start_iso},{end_iso},85000\n"
    )
    parsed = parse_sales_data_csv(text)
    store_uploaded_csv("sess-legacy-csv", parsed.rows, "old.csv", text.encode())
    result = await csv_validation_node(
        {
            "session_id": "sess-legacy-csv",
            "corrected_fields": {"csv_session_id": "sess-legacy-csv"},
            "backend_refs": {},
        }
    )
    assert result["workflow_stage"] == "CSV_LAYER2_VALIDATION"
    row = result["backend_refs"]["csv_rows"][0]
    assert row["lease_code"] == "LC-1001"
    assert row["sales_period_start"] == start_iso
    assert row["brand"] == ""
    assert row["property"] == ""


@pytest.mark.asyncio
async def test_blank_downloaded_template_is_not_treated_as_data() -> None:
    from app.agents.graph.nodes.sales_data.csv_validation_node import (
        csv_validation_node,
    )

    start_dd, end_dd, _, _ = _current_month()
    text = (
        PLATFORM_HEADER
        + f"\nLC-1001,Noon Fashion,Riyadh Park,{start_dd},{end_dd},\n"
        + f"LC-1002,Noon Fashion,Riyadh Park,{start_dd},{end_dd},\n"
    )
    parsed = parse_sales_data_csv(text)
    store_uploaded_csv("sess-blank-tpl", parsed.rows, "salesdatatemplate.csv", text.encode())
    result = await csv_validation_node(
        {
            "session_id": "sess-blank-tpl",
            "corrected_fields": {"csv_session_id": "sess-blank-tpl"},
            "backend_refs": {},
        }
    )
    assert result["workflow_stage"] == "UPLOAD_INTENT"
    assert "blank template" in result["response_message"]
    assert "Nothing has been submitted" in result["response_message"]
    assert result["response_ui"]["verbatim"] is True
    assert result["response_ui"]["show_file_upload"] is True
    assert "csv_rows_all" not in (result.get("backend_refs") or {})

    # Re-uploading the same blank template says so instead of repeating.
    again = await csv_validation_node(
        {
            "session_id": "sess-blank-tpl",
            "corrected_fields": {"csv_session_id": "sess-blank-tpl"},
            "backend_refs": {},
            "conversation_history": [
                {"role": "assistant", "content": result["response_message"]},
            ],
        }
    )
    assert "still has no sales amounts filled in" in again["response_message"]


@pytest.mark.asyncio
async def test_partially_filled_csv_still_asks_for_missing_amounts() -> None:
    from app.agents.graph.nodes.sales_data.csv_validation_node import (
        csv_validation_node,
    )

    start_dd, end_dd, _, _ = _current_month()
    text = (
        PLATFORM_HEADER
        + f"\nLC-1001,Noon Fashion,Riyadh Park,{start_dd},{end_dd},85000\n"
        + f"LC-1002,Noon Fashion,Riyadh Park,{start_dd},{end_dd},\n"
    )
    parsed = parse_sales_data_csv(text)
    store_uploaded_csv("sess-partial", parsed.rows, "partial.csv", text.encode())
    result = await csv_validation_node(
        {
            "session_id": "sess-partial",
            "corrected_fields": {"csv_session_id": "sess-partial"},
            "backend_refs": {},
        }
    )
    assert result["workflow_stage"] == "CSV_CORRECTION"
    assert "LC-1002" in result["response_message"]


@pytest.mark.asyncio
async def test_csv_reports_every_problem_in_one_pass() -> None:
    """Reported file: foreign lease + negative amount + blank amount + one good row."""
    from unittest.mock import AsyncMock, patch

    from app.agents.graph.nodes.sales_data.csv_validation_node import (
        csv_validation_node,
    )

    text = (
        PLATFORM_HEADER
        + "\nt0111654,Brand Blooming,Haifa Mall,01-08-2026,31-08-2026,10000"
        + "\nt0092927,Brand Blooming,Makkah Mall,01-08-2026,31-08-2026,200000"
        + "\nt0088828,Brand Blooming,Nakheel Mall Dammam,01-08-2026,31-08-2026,-123"
        + "\nt0112235,Brand Blooming,Tala Mall,01-08-2026,31-08-2026,\n"
    )

    def _pending(code: str, mall: str) -> dict:
        return {
            "lease_code": code, "lease_id": hash(code) % 1000, "status": "PENDING",
            "sales_period_start": "2026-08-01", "sales_period_end": "2026-08-31",
            "brand_name": "Brand Blooming", "property_name": mall,
        }

    backend = [
        _pending("t0092927", "Makkah Mall"),
        _pending("t0088828", "Nakheel Mall Dammam"),
        _pending("t0112235", "Tala Mall"),
    ]
    client = AsyncMock()
    client.get_sales_data = AsyncMock(return_value=backend)
    parsed = parse_sales_data_csv(text)
    store_uploaded_csv("sess-mixed", parsed.rows, "salesdatatemplate.csv", text.encode())
    base = "app.agents.graph.nodes.sales_data.csv_validation_node"
    with patch(f"{base}.SalesDataApiClient", return_value=client), patch(
        f"{base}.get_current_bearer_token", return_value="tok"
    ):
        result = await csv_validation_node({
            "session_id": "sess-mixed",
            "corrected_fields": {"csv_session_id": "sess-mixed"},
            "backend_refs": {},
        })

    message = result["response_message"]
    assert "3 rows need attention" in message
    assert "**t0111654** is not linked to your current company" in message
    assert "**t0088828**: the sales amount (-123) must be greater than 0" in message
    assert "**t0112235**: the sales amount is missing" in message
    assert "Ready to submit: **t0092927**" in message
    # A lease problem cannot be fixed by typing amounts — re-upload.
    assert result["workflow_stage"] == "UPLOAD_INTENT"
    assert "Nothing has been submitted" in message
