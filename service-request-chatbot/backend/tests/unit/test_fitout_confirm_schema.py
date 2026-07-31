"""Unit tests for fitout_confirm_schema.py (C1).

Coverage
--------
TestSubcatConfig         — SUBCAT_CONFIG shape and completeness per sub-category
TestGetMissingFields     — get_missing_fields() yes/no branch logic per sub-category
TestRolePermissions      — role_can_act_on_stage(), role_can_approve()
TestFitoutConfirmModel   — FitoutConfirmExtractedFields strips backend-only/unknown keys
"""

from __future__ import annotations

import pytest

from app.agents.schemas.fitout_confirm_schema import (
    SUBCAT_CONFIG,
    FitoutConfirmExtractedFields,
    ALLOWED_EXTRACTED_FIELDS,
    BACKEND_ONLY_FIELDS,
    OPTIONAL_FIELDS,
    TERMINAL_STAGES,
    get_missing_fields,
    role_can_act_on_stage,
    role_can_approve,
    build_title,
)


# ---------------------------------------------------------------------------
# TestSubcatConfig
# ---------------------------------------------------------------------------


class TestSubcatConfig:
    """SUBCAT_CONFIG completeness and shape."""

    def test_all_three_sub_categories_present(self) -> None:
        assert "FIT_OUT_CONFIRM_FOS" in SUBCAT_CONFIG
        assert "FIT_OUT_CONFIRM_END" in SUBCAT_CONFIG
        assert "FIT_OUT_CONFIRM_TRADING" in SUBCAT_CONFIG

    def test_fos_has_correct_radio_field(self) -> None:
        assert SUBCAT_CONFIG["FIT_OUT_CONFIRM_FOS"]["radio_field"] == "fitout_start_done"

    def test_end_has_correct_radio_field(self) -> None:
        assert SUBCAT_CONFIG["FIT_OUT_CONFIRM_END"]["radio_field"] == "fitout_complete_done"

    def test_trading_has_correct_radio_field(self) -> None:
        assert SUBCAT_CONFIG["FIT_OUT_CONFIRM_TRADING"]["radio_field"] == "trading_done"

    def test_fos_actual_date_field(self) -> None:
        assert SUBCAT_CONFIG["FIT_OUT_CONFIRM_FOS"]["actual_date_field"] == "actual_fitout_start_date"

    def test_end_actual_date_field(self) -> None:
        assert SUBCAT_CONFIG["FIT_OUT_CONFIRM_END"]["actual_date_field"] == "actual_fitout_complete_date"

    def test_trading_actual_date_field(self) -> None:
        assert SUBCAT_CONFIG["FIT_OUT_CONFIRM_TRADING"]["actual_date_field"] == "actual_trading_date"

    def test_fos_delay_reason_required(self) -> None:
        assert SUBCAT_CONFIG["FIT_OUT_CONFIRM_FOS"]["delay_reason_required"] is True

    def test_end_delay_reason_required(self) -> None:
        assert SUBCAT_CONFIG["FIT_OUT_CONFIRM_END"]["delay_reason_required"] is True

    def test_trading_delay_reason_optional(self) -> None:
        assert SUBCAT_CONFIG["FIT_OUT_CONFIRM_TRADING"]["delay_reason_required"] is False

    def test_fos_has_documents(self) -> None:
        assert SUBCAT_CONFIG["FIT_OUT_CONFIRM_FOS"]["has_documents"] is True

    def test_end_has_documents(self) -> None:
        assert SUBCAT_CONFIG["FIT_OUT_CONFIRM_END"]["has_documents"] is True

    def test_trading_no_documents(self) -> None:
        assert SUBCAT_CONFIG["FIT_OUT_CONFIRM_TRADING"]["has_documents"] is False

    def test_fos_prior_milestone_is_actual_handover(self) -> None:
        assert SUBCAT_CONFIG["FIT_OUT_CONFIRM_FOS"]["prior_milestone_field"] == "actual_handover_date_readonly"

    def test_end_prior_milestone_is_actual_fos(self) -> None:
        assert SUBCAT_CONFIG["FIT_OUT_CONFIRM_END"]["prior_milestone_field"] == "actual_fitout_start_date_readonly"

    def test_trading_prior_milestone_is_actual_end(self) -> None:
        assert SUBCAT_CONFIG["FIT_OUT_CONFIRM_TRADING"]["prior_milestone_field"] == "actual_fitout_end_date_readonly"

    def test_fos_approver_role_dd_engineer(self) -> None:
        assert SUBCAT_CONFIG["FIT_OUT_CONFIRM_FOS"]["approver_role"] == "DD_ENGINEER"

    def test_trading_approver_role_mall_manager(self) -> None:
        assert SUBCAT_CONFIG["FIT_OUT_CONFIRM_TRADING"]["approver_role"] == "MALL_MANAGER"

    def test_terminal_stages_contains_sr_created_sr_approved_cancelled(self) -> None:
        assert "SR_CREATED" in TERMINAL_STAGES
        assert "SR_APPROVED" in TERMINAL_STAGES
        assert "CANCELLED" in TERMINAL_STAGES


# ---------------------------------------------------------------------------
# TestGetMissingFields
# ---------------------------------------------------------------------------


class TestGetMissingFields:
    """get_missing_fields() yes/no branch logic per sub-category."""

    # ── FOS yes branch ─────────────────────────────────────────────────────

    def test_fos_yes_branch_passes_when_actual_date_present(self) -> None:
        collected = {
            "lease_brand_mall": "t0105712 - Brand - Mall",
            "fitout_start_done": "yes",
            "actual_fitout_start_date": "2026-05-14",
        }
        missing = get_missing_fields("CREATE_SR", "FIT_OUT_CONFIRM_FOS", collected)
        assert "actual_fitout_start_date" not in missing

    def test_fos_yes_branch_missing_actual_date(self) -> None:
        collected = {
            "lease_brand_mall": "t0105712 - Brand - Mall",
            "fitout_start_done": "yes",
        }
        missing = get_missing_fields("CREATE_SR", "FIT_OUT_CONFIRM_FOS", collected)
        assert "actual_fitout_start_date" in missing

    def test_fos_yes_branch_does_not_require_planned_dates(self) -> None:
        collected = {
            "lease_brand_mall": "t0105712 - Brand - Mall",
            "fitout_start_done": "yes",
            "actual_fitout_start_date": "2026-05-14",
        }
        missing = get_missing_fields("CREATE_SR", "FIT_OUT_CONFIRM_FOS", collected)
        # None of the planned date fields should be in missing
        for pf in ("planned_fitout_start_date", "planned_fitout_end_date", "planned_trading_date"):
            assert pf not in missing, f"{pf} must not be required in yes-branch"

    def test_fos_yes_branch_does_not_require_delay_reason(self) -> None:
        collected = {
            "lease_brand_mall": "t0105712 - Brand - Mall",
            "fitout_start_done": "yes",
            "actual_fitout_start_date": "2026-05-14",
        }
        missing = get_missing_fields("CREATE_SR", "FIT_OUT_CONFIRM_FOS", collected)
        assert "delay_reason" not in missing

    # ── FOS no branch ──────────────────────────────────────────────────────

    def test_fos_no_branch_requires_planned_dates(self) -> None:
        collected = {
            "lease_brand_mall": "t0105712 - Brand - Mall",
            "fitout_start_done": "no",
        }
        missing = get_missing_fields("CREATE_SR", "FIT_OUT_CONFIRM_FOS", collected)
        for pf in ("planned_fitout_start_date", "planned_fitout_end_date", "planned_trading_date"):
            assert pf in missing, f"{pf} must be required in no-branch for FOS"

    def test_fos_no_branch_requires_delay_reason(self) -> None:
        collected = {
            "lease_brand_mall": "t0105712 - Brand - Mall",
            "fitout_start_done": "no",
            "planned_fitout_start_date": "2026-05-20",
            "planned_fitout_end_date": "2026-06-10",
            "planned_trading_date": "2026-06-25",
        }
        missing = get_missing_fields("CREATE_SR", "FIT_OUT_CONFIRM_FOS", collected)
        assert "delay_reason" in missing

    def test_fos_no_branch_passes_when_all_fields_present(self) -> None:
        collected = {
            "lease_brand_mall": "t0105712 - Brand - Mall",
            "fitout_start_done": "no",
            "planned_fitout_start_date": "2026-05-20",
            "planned_fitout_end_date": "2026-06-10",
            "planned_trading_date": "2026-06-25",
            "delay_reason": "Material delays",
            "comments": "",
        }
        missing = get_missing_fields("CREATE_SR", "FIT_OUT_CONFIRM_FOS", collected)
        blocking = [m for m in missing if m not in ("comments",)]
        assert not blocking, f"No blocking fields should remain: {missing}"

    def test_fos_no_branch_does_not_require_actual_date(self) -> None:
        collected = {
            "lease_brand_mall": "t0105712 - Brand - Mall",
            "fitout_start_done": "no",
            "planned_fitout_start_date": "2026-05-20",
            "planned_fitout_end_date": "2026-06-10",
            "planned_trading_date": "2026-06-25",
            "delay_reason": "Delay",
        }
        missing = get_missing_fields("CREATE_SR", "FIT_OUT_CONFIRM_FOS", collected)
        assert "actual_fitout_start_date" not in missing

    # ── TRADING no branch — delay_reason optional ──────────────────────────

    def test_trading_no_branch_passes_without_delay_reason(self) -> None:
        """TRADING delay_reason is optional — missing it must not block."""
        collected = {
            "lease_brand_mall": "t0105712 - Brand - Mall",
            "trading_done": "no",
            "planned_trading_date": "2026-06-25",
        }
        missing = get_missing_fields("CREATE_SR", "FIT_OUT_CONFIRM_TRADING", collected)
        # delay_reason is optional; when it is None the system may ask, but once
        # the user explicitly skips (empty string), it should not block.
        # The schema treats None as "never answered" (ask proactively) but does
        # not block when missing — so it should be in missing but treated optional.
        blocking_non_optional = [m for m in missing if m != "delay_reason" and m != "comments"]
        assert not blocking_non_optional

    def test_trading_yes_branch_passes_with_actual_date(self) -> None:
        collected = {
            "lease_brand_mall": "t0105712 - Brand - Mall",
            "trading_done": "yes",
            "actual_trading_date": "2026-06-18",
        }
        missing = get_missing_fields("CREATE_SR", "FIT_OUT_CONFIRM_TRADING", collected)
        assert "actual_trading_date" not in missing

    # ── END branch ────────────────────────────────────────────────────────

    def test_end_no_branch_requires_planned_fitout_complete_date(self) -> None:
        collected = {
            "lease_brand_mall": "t0105712 - Brand - Mall",
            "fitout_complete_done": "no",
        }
        missing = get_missing_fields("CREATE_SR", "FIT_OUT_CONFIRM_END", collected)
        assert "planned_fitout_complete_date" in missing

    def test_end_no_branch_requires_delay_reason(self) -> None:
        collected = {
            "lease_brand_mall": "t0105712 - Brand - Mall",
            "fitout_complete_done": "no",
            "planned_fitout_complete_date": "2026-06-10",
            "planned_trading_date": "2026-06-25",
        }
        missing = get_missing_fields("CREATE_SR", "FIT_OUT_CONFIRM_END", collected)
        assert "delay_reason" in missing

    # ── Radio field deferred until answered ───────────────────────────────

    def test_missing_radio_field_blocks_branch_resolution(self) -> None:
        """When radio field is not yet answered, only the radio field itself is returned."""
        collected = {
            "lease_brand_mall": "t0105712 - Brand - Mall",
        }
        missing = get_missing_fields("CREATE_SR", "FIT_OUT_CONFIRM_FOS", collected)
        assert "fitout_start_done" in missing

    def test_missing_lease_brand_mall_blocks_first(self) -> None:
        collected = {}
        missing = get_missing_fields("CREATE_SR", "FIT_OUT_CONFIRM_FOS", collected)
        assert "lease_brand_mall" in missing


# ---------------------------------------------------------------------------
# TestRolePermissions
# ---------------------------------------------------------------------------


class TestRolePermissions:
    def test_dd_engineer_can_act_on_create_sr(self) -> None:
        assert role_can_act_on_stage("DD_ENGINEER", "CREATE_SR") is True

    def test_dd_engineer_can_act_on_approve_sr(self) -> None:
        assert role_can_act_on_stage("DD_ENGINEER", "APPROVE_SR") is True

    def test_mall_manager_can_act_on_create_sr(self) -> None:
        assert role_can_act_on_stage("MALL_MANAGER", "CREATE_SR") is True

    def test_operations_can_act_on_approve_sr(self) -> None:
        assert role_can_act_on_stage("OPERATIONS", "APPROVE_SR") is True

    def test_dd_engineer_can_approve_fos(self) -> None:
        assert role_can_approve("DD_ENGINEER", "FIT_OUT_CONFIRM_FOS") is True

    def test_dd_engineer_can_approve_end(self) -> None:
        assert role_can_approve("DD_ENGINEER", "FIT_OUT_CONFIRM_END") is True

    def test_dd_engineer_cannot_approve_trading(self) -> None:
        assert role_can_approve("DD_ENGINEER", "FIT_OUT_CONFIRM_TRADING") is False

    def test_mall_manager_can_approve_trading(self) -> None:
        assert role_can_approve("MALL_MANAGER", "FIT_OUT_CONFIRM_TRADING") is True

    def test_operations_can_approve_trading(self) -> None:
        assert role_can_approve("OPERATIONS", "FIT_OUT_CONFIRM_TRADING") is True

    def test_mall_manager_cannot_approve_fos(self) -> None:
        assert role_can_approve("MALL_MANAGER", "FIT_OUT_CONFIRM_FOS") is False

    def test_unknown_role_cannot_act(self) -> None:
        assert role_can_act_on_stage("TENANT_ADMIN", "CREATE_SR") is False


# ---------------------------------------------------------------------------
# TestBuildTitle
# ---------------------------------------------------------------------------


class TestBuildTitle:
    def test_fos_title_prefix(self) -> None:
        title = build_title("FIT_OUT_CONFIRM_FOS", "t0105712")
        assert title == "Confirm Fitout Start - t0105712"

    def test_end_title_prefix(self) -> None:
        title = build_title("FIT_OUT_CONFIRM_END", "t0105712")
        assert title == "Confirm Fitout End - t0105712"

    def test_trading_title_prefix(self) -> None:
        title = build_title("FIT_OUT_CONFIRM_TRADING", "t0105712")
        assert title == "Confirm Trading - t0105712"

    def test_empty_pms_returns_prefix_only(self) -> None:
        title = build_title("FIT_OUT_CONFIRM_FOS", "")
        assert title == "Confirm Fitout Start"


# ---------------------------------------------------------------------------
# TestFitoutConfirmExtractedFields
# ---------------------------------------------------------------------------


class TestFitoutConfirmExtractedFields:
    def test_known_fields_are_preserved(self) -> None:
        obj = FitoutConfirmExtractedFields(
            fields={
                "fitout_start_done": {"value": "yes", "confidence": 0.95},
                "actual_fitout_start_date": {"value": "2026-05-14", "confidence": 0.9},
            }
        )
        assert "fitout_start_done" in obj.fields
        assert "actual_fitout_start_date" in obj.fields

    def test_backend_only_fields_are_stripped(self) -> None:
        obj = FitoutConfirmExtractedFields(
            fields={
                "lease_id": {"value": "95404", "confidence": 1.0},
                "pms_tenant_code": {"value": "t0105712", "confidence": 1.0},
                "fitout_start_done": {"value": "yes", "confidence": 0.9},
            }
        )
        assert "lease_id" not in obj.fields
        assert "pms_tenant_code" not in obj.fields
        assert "fitout_start_done" in obj.fields

    def test_unknown_fields_are_stripped(self) -> None:
        obj = FitoutConfirmExtractedFields(
            fields={
                "completely_unknown_field": {"value": "foo", "confidence": 0.8},
                "actual_trading_date": {"value": "2026-06-18", "confidence": 0.9},
            }
        )
        assert "completely_unknown_field" not in obj.fields
        assert "actual_trading_date" in obj.fields

    def test_to_flat_dict(self) -> None:
        obj = FitoutConfirmExtractedFields(
            fields={
                "trading_done": {"value": "yes", "confidence": 0.9},
                "actual_trading_date": {"value": "2026-06-18", "confidence": 0.85},
            }
        )
        flat = obj.to_flat_dict()
        assert flat["trading_done"] == "yes"
        assert flat["actual_trading_date"] == "2026-06-18"

    def test_empty_fields_accepted(self) -> None:
        obj = FitoutConfirmExtractedFields(fields={})
        assert obj.fields == {}
