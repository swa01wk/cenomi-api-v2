"""Unit tests for display_formatters."""

from __future__ import annotations

from app.agents.services.display_formatters import (
    format_date_ddmmyyyy,
    format_enum_label,
    format_field_display_value,
)


def test_format_date_iso_to_ddmmyyyy() -> None:
    assert format_date_ddmmyyyy("2026-07-15") == "15/07/2026"


def test_format_date_with_time() -> None:
    assert format_date_ddmmyyyy("2026-07-15T10:30:00") == "15/07/2026"


def test_format_enum_inspection_done_by() -> None:
    assert format_enum_label("FM_MANAGER") == "FM Manager"
    assert format_enum_label("OPERATIONS") == "Operations"


def test_format_field_display_value_date_and_enum() -> None:
    assert format_field_display_value("unit_readiness_date", "2026-08-01") == "01/08/2026"
    assert format_field_display_value("inspection_done_by", "FM_MANAGER") == "FM Manager"
