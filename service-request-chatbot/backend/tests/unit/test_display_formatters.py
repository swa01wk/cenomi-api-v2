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


def test_format_enum_handover_type_codes() -> None:
    """Both Path B codes read as "Already Trading" — the agent extracts
    ``already_trading``, the platform form stores ``already_handovered``."""
    assert format_enum_label("initiate_handover") == "Initiate Handover"
    assert format_enum_label("already_trading") == "Already Trading"
    assert format_enum_label("already_handovered") == "Already Trading"
    assert format_enum_label("ALREADY_HANDOVERED") == "Already Trading"


def test_format_field_display_value_keeps_handover_type_raw() -> None:
    """`value` must stay a literal code — the Formik swap observer matches it.

    Humanization happens via the preview row's separate ``display`` key.
    """
    assert (
        format_field_display_value("already_handovered", "already_handovered")
        == "already_handovered"
    )


class TestEnumLabelFallback:
    """Raw SCREAMING_SNAKE codes must never reach the user (TC-3662 follow-up)."""

    def test_curated_labels_win(self) -> None:
        assert format_enum_label("REPORT_SUBMITTED") == "Report Submitted"
        assert format_enum_label("FM_MANAGER") == "FM Manager"
        assert format_enum_label("APPROVED_WITH_CHANGES") == "Approved with changes"

    def test_workflow_stage_labels(self) -> None:
        assert format_enum_label("FM_REVIEW") == "FM Review"
        assert format_enum_label("RDD_REVIEW") == "RDD Review"
        assert format_enum_label("DRAFT") == "Draft"

    def test_unknown_enum_code_is_humanized(self) -> None:
        assert format_enum_label("SOME_BRAND_NEW_STATUS") == "Some Brand New Status"

    def test_non_enum_values_pass_through(self) -> None:
        # Free text and FE-matched lower-case keys must not be touched.
        assert format_enum_label("Nike") == "Nike"
        assert format_enum_label("riyadh park") == "riyadh park"
        assert format_enum_label(None) is None
        assert format_enum_label("") == ""

    def test_fe_matched_literal_still_maps(self) -> None:
        # already_handovered is FE-matched but has a curated label.
        assert format_enum_label("already_handovered") == "Already Trading"
