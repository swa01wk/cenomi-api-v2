"""Cenomi platform sales-data CSV template — parse, normalize, and render.

Matches the portal upload screen (SalesDataUpload.tsx) and the live
GET /api/v1/sales-data/template generator:

  Lease code,Brand,Property,Start date(DD-MM-YYYY),End date(DD-MM-YYYY),Sales amount

Headers have parenthetical suffixes stripped before matching. Dates accept
DD-MM-YYYY first, then YYYY-MM-DD, and are stored internally as YYYY-MM-DD
for POST /api/v1/sales-data.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

# Exact header row written by cenomi-api salesDataToCSVData.
PLATFORM_CSV_HEADERS: list[str] = [
    "Lease code",
    "Brand",
    "Property",
    "Start date(DD-MM-YYYY)",
    "End date(DD-MM-YYYY)",
    "Sales amount",
]

# Portal expectedHeaders after stripping "(DD-MM-YYYY)".
PLATFORM_DISPLAY_HEADERS: list[str] = [
    "Lease code",
    "Brand",
    "Property",
    "Start date",
    "End date",
    "Sales amount",
]

INTERNAL_FIELDS: list[str] = [
    "lease_code",
    "brand",
    "property",
    "sales_period_start",
    "sales_period_end",
    "sales_amt",
]

# Upload must have these; Brand / Property may be omitted (legacy tenant CSVs).
REQUIRED_PARSE_FIELDS: list[str] = [
    "lease_code",
    "sales_period_start",
    "sales_period_end",
    "sales_amt",
]

_INTERNAL_TO_DISPLAY: dict[str, str] = {
    "lease_code": "Lease code",
    "brand": "Brand",
    "property": "Property",
    "sales_period_start": "Start date",
    "sales_period_end": "End date",
    "sales_amt": "Sales amount",
}

# Portal: h.replace(/\s*\(.*?\)\s*$/, '').trim()
_PAREN_SUFFIX = re.compile(r"\s*\(.*?\)\s*$")

# Case-insensitive aliases after the parenthetical suffix is stripped.
_HEADER_ALIASES: dict[str, str] = {
    "lease code": "lease_code",
    "lease_code": "lease_code",
    "brand": "brand",
    "brand_name": "brand",
    "property": "property",
    "property_name": "property",
    "mall": "property",
    "mall_name": "property",
    "start date": "sales_period_start",
    "sales_period_start": "sales_period_start",
    "end date": "sales_period_end",
    "sales_period_end": "sales_period_end",
    "sales amount": "sales_amt",
    "sales_amt": "sales_amt",
    "sales amt": "sales_amt",
}

_TEMPLATE_STATUSES = frozenset({"PENDING", "NEED_REUPLOAD"})


@dataclass
class SalesDataCsvParseResult:
    missing_headers: list[str] = field(default_factory=list)
    rows: list[dict[str, str]] = field(default_factory=list)
    headers_found: list[str] = field(default_factory=list)


def strip_header_suffix(raw: str) -> str:
    """Strip parenthetical suffixes so 'Start date(DD-MM-YYYY)' → 'Start date'."""
    return _PAREN_SUFFIX.sub("", (raw or "").strip()).strip()


def canonicalize_header(raw: str) -> str | None:
    return _HEADER_ALIASES.get(strip_header_suffix(raw).lower())


def parse_sales_period_date(value: str) -> str | None:
    """Parse a sales-period cell to YYYY-MM-DD.

    Portal uses moment strict with ['DD-MM-YYYY', 'YYYY-MM-DD']. Empty input
    returns None (caller treats it as a missing field).
    """
    raw = (value or "").strip()
    if not raw:
        return None
    for fmt in ("%d-%m-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def iso_to_ddmmyyyy(value: str) -> str:
    """Render YYYY-MM-DD as DD-MM-YYYY for the platform template."""
    raw = (value or "").strip()
    parsed = parse_sales_period_date(raw)
    if not parsed:
        return raw
    y, m, d = parsed.split("-")
    return f"{d}-{m}-{y}"


def _cell(row: list[str], index: int) -> str:
    if index < 0 or index >= len(row):
        return ""
    return (row[index] or "").strip()


def _optional_cell(row: list[str], index_by_field: dict[str, int], key: str) -> str:
    if key not in index_by_field:
        return ""
    return _cell(row, index_by_field[key])


def parse_sales_data_csv(text: str) -> SalesDataCsvParseResult:
    """Parse a Cenomi sales-data CSV (platform template or internal aliases)."""
    reader = csv.reader(io.StringIO(text))
    try:
        raw_header = next(reader)
    except StopIteration:
        return SalesDataCsvParseResult(
            missing_headers=[_INTERNAL_TO_DISPLAY[f] for f in REQUIRED_PARSE_FIELDS]
        )

    canonical = [canonicalize_header(h) for h in raw_header]
    index_by_field: dict[str, int] = {}
    for i, key in enumerate(canonical):
        if key and key not in index_by_field:
            index_by_field[key] = i

    missing = [
        _INTERNAL_TO_DISPLAY[field]
        for field in REQUIRED_PARSE_FIELDS
        if field not in index_by_field
    ]
    headers_found = [
        strip_header_suffix(h) for h, key in zip(raw_header, canonical) if key
    ]

    if missing:
        return SalesDataCsvParseResult(
            missing_headers=missing,
            headers_found=headers_found,
        )

    rows: list[dict[str, str]] = []
    for raw in reader:
        if not any((c or "").strip() for c in raw):
            continue
        start_raw = _optional_cell(raw, index_by_field, "sales_period_start")
        end_raw = _optional_cell(raw, index_by_field, "sales_period_end")
        start_iso = parse_sales_period_date(start_raw)
        end_iso = parse_sales_period_date(end_raw)
        rows.append({
            "lease_code": _optional_cell(raw, index_by_field, "lease_code"),
            "brand": _optional_cell(raw, index_by_field, "brand"),
            "property": _optional_cell(raw, index_by_field, "property"),
            "sales_period_start": start_iso or start_raw,
            "sales_period_end": end_iso or end_raw,
            "sales_amt": _optional_cell(raw, index_by_field, "sales_amt"),
        })

    return SalesDataCsvParseResult(
        missing_headers=[],
        rows=rows,
        headers_found=headers_found,
    )


def remap_row_keys(row: dict[str, Any]) -> dict[str, str]:
    """Normalize a dict that may still have platform header names."""
    mapped: dict[str, str] = {}
    for key, value in row.items():
        canon = canonicalize_header(str(key)) if key else None
        if canon and canon not in mapped:
            mapped[canon] = "" if value is None else str(value).strip()
    for field in INTERNAL_FIELDS:
        mapped.setdefault(field, "")
    start_iso = parse_sales_period_date(mapped["sales_period_start"])
    end_iso = parse_sales_period_date(mapped["sales_period_end"])
    if start_iso:
        mapped["sales_period_start"] = start_iso
    if end_iso:
        mapped["sales_period_end"] = end_iso
    return mapped


def normalize_sales_data_rows(rows: list[dict[str, Any]]) -> list[dict[str, str]]:
    return [remap_row_keys(row) for row in rows]


def _format_template_amount(amt: Any) -> str:
    if amt is None or amt == "":
        return ""
    try:
        number = float(amt)
        if number == int(number):
            return str(int(number))
        return str(number)
    except (TypeError, ValueError):
        return str(amt)


def template_matrix_from_sales_data(records: list[dict[str, Any]]) -> list[list[str]]:
    """Build the platform template: header + PENDING / NEED_REUPLOAD rows."""
    matrix: list[list[str]] = [list(PLATFORM_CSV_HEADERS)]
    for record in records:
        if record.get("status") not in _TEMPLATE_STATUSES:
            continue
        matrix.append([
            str(record.get("lease_code") or ""),
            str(record.get("brand_name") or record.get("brand") or ""),
            str(
                record.get("property_name")
                or record.get("mall_name")
                or record.get("property")
                or ""
            ),
            iso_to_ddmmyyyy(str(record.get("sales_period_start") or "")),
            iso_to_ddmmyyyy(str(record.get("sales_period_end") or "")),
            _format_template_amount(record.get("sales_amt")),
        ])
    return matrix


def render_platform_csv(rows: list[dict[str, Any]]) -> str:
    """Serialize internal-key row dicts as a platform-template CSV."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(PLATFORM_CSV_HEADERS)
    for row in rows:
        mapped = remap_row_keys(row) if row else {}
        writer.writerow([
            mapped.get("lease_code") or "",
            mapped.get("brand") or "",
            mapped.get("property") or "",
            iso_to_ddmmyyyy(mapped.get("sales_period_start") or ""),
            iso_to_ddmmyyyy(mapped.get("sales_period_end") or ""),
            mapped.get("sales_amt") or "",
        ])
    return buf.getvalue()
