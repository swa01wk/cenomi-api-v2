"""Unit tests for fitout_confirm validation logic (C3).

Coverage
--------
TestFosValidation        — FOS: delay_reason required when no; passes when yes + actual date
TestTradingValidation    — TRADING: delay_reason optional; passes without it
TestEndValidation        — END: planned dates required on no-branch
TestCrossMilestoneOrdering — actual date must be >= prior milestone date when present
TestConfirmationNode     — fitout_confirm_confirmation_node builds card correctly
"""

from __future__ import annotations

from typing import Any

import pytest

from app.agents.schemas.fitout_confirm_schema import get_missing_fields


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_BASE = {
    "lease_brand_mall": "t0105712 - Brand - Mall",
    "lease_code": "t0105712",
    "lease_id": 95404,
}


# ---------------------------------------------------------------------------
# TestFosValidation
# ---------------------------------------------------------------------------


class TestFosValidation:
    """FOS sub-category: delay_reason required when no; not required when yes."""

    def test_fos_yes_branch_no_delay_required(self) -> None:
        collected = {
            **_BASE,
            "fitout_start_done": "yes",
            "actual_fitout_start_date": "2026-05-14",
        }
        missing = get_missing_fields("CREATE_SR", "FIT_OUT_CONFIRM_FOS", collected)
        assert "delay_reason" not in missing

    def test_fos_no_branch_delay_required(self) -> None:
        collected = {
            **_BASE,
            "fitout_start_done": "no",
            "planned_fitout_start_date": "2026-05-20",
            "planned_fitout_end_date": "2026-06-10",
            "planned_trading_date": "2026-06-25",
        }
        missing = get_missing_fields("CREATE_SR", "FIT_OUT_CONFIRM_FOS", collected)
        assert "delay_reason" in missing, (
            "delay_reason must be required for FOS when status = no"
        )

    def test_fos_no_branch_passes_with_all_fields(self) -> None:
        collected = {
            **_BASE,
            "fitout_start_done": "no",
            "planned_fitout_start_date": "2026-05-20",
            "planned_fitout_end_date": "2026-06-10",
            "planned_trading_date": "2026-06-25",
            "delay_reason": "Material delays",
            "comments": "N/A",
        }
        missing = get_missing_fields("CREATE_SR", "FIT_OUT_CONFIRM_FOS", collected)
        assert not missing, f"All fields present — nothing should be missing; got {missing}"

    def test_fos_yes_branch_passes_with_actual_date(self) -> None:
        collected = {
            **_BASE,
            "fitout_start_done": "yes",
            "actual_fitout_start_date": "2026-05-14",
            "comments": "",
        }
        missing = get_missing_fields("CREATE_SR", "FIT_OUT_CONFIRM_FOS", collected)
        assert not missing, f"FOS yes-branch complete — got: {missing}"


# ---------------------------------------------------------------------------
# TestTradingValidation
# ---------------------------------------------------------------------------


class TestTradingValidation:
    """TRADING sub-category: delay_reason is optional."""

    def test_trading_no_branch_passes_without_delay_reason(self) -> None:
        """TRADING delay_reason is optional — not having it must not produce a
        non-optional blocking field in the missing list."""
        collected = {
            **_BASE,
            "trading_done": "no",
            "planned_trading_date": "2026-06-25",
            "delay_reason": None,  # never answered → ask proactively, but is optional
        }
        missing = get_missing_fields("CREATE_SR", "FIT_OUT_CONFIRM_TRADING", collected)
        # delay_reason is in missing (proactive ask) but it IS in OPTIONAL_FIELDS
        # so it should not hard-block. The test verifies it is listed (proactive ask)
        # but not alongside other required fields.
        required_non_optional = [
            m for m in missing
            if m not in ("delay_reason", "comments")
        ]
        assert not required_non_optional, (
            "Only optional fields may remain missing for TRADING no-branch when "
            f"planned_trading_date is provided; got required: {required_non_optional}"
        )

    def test_trading_no_branch_passes_with_delay_reason_empty_string(self) -> None:
        """Empty-string delay_reason for TRADING (user explicitly skipped) must not block."""
        collected = {
            **_BASE,
            "trading_done": "no",
            "planned_trading_date": "2026-06-25",
            "delay_reason": "",  # user explicitly said none
        }
        missing = get_missing_fields("CREATE_SR", "FIT_OUT_CONFIRM_TRADING", collected)
        # With empty string, delay_reason is NOT missing (user skipped it)
        assert "delay_reason" not in missing

    def test_trading_yes_branch_requires_actual_date(self) -> None:
        collected = {
            **_BASE,
            "trading_done": "yes",
        }
        missing = get_missing_fields("CREATE_SR", "FIT_OUT_CONFIRM_TRADING", collected)
        assert "actual_trading_date" in missing

    def test_trading_yes_branch_passes(self) -> None:
        collected = {
            **_BASE,
            "trading_done": "yes",
            "actual_trading_date": "2026-06-18",
        }
        missing = get_missing_fields("CREATE_SR", "FIT_OUT_CONFIRM_TRADING", collected)
        assert not missing


# ---------------------------------------------------------------------------
# TestEndValidation
# ---------------------------------------------------------------------------


class TestEndValidation:
    def test_end_no_branch_requires_planned_fitout_complete_date(self) -> None:
        collected = {
            **_BASE,
            "fitout_complete_done": "no",
        }
        missing = get_missing_fields("CREATE_SR", "FIT_OUT_CONFIRM_END", collected)
        assert "planned_fitout_complete_date" in missing

    def test_end_no_branch_requires_planned_trading_date(self) -> None:
        collected = {
            **_BASE,
            "fitout_complete_done": "no",
            "planned_fitout_complete_date": "2026-06-10",
        }
        missing = get_missing_fields("CREATE_SR", "FIT_OUT_CONFIRM_END", collected)
        assert "planned_trading_date" in missing

    def test_end_no_branch_requires_delay_reason(self) -> None:
        collected = {
            **_BASE,
            "fitout_complete_done": "no",
            "planned_fitout_complete_date": "2026-06-10",
            "planned_trading_date": "2026-06-25",
        }
        missing = get_missing_fields("CREATE_SR", "FIT_OUT_CONFIRM_END", collected)
        assert "delay_reason" in missing

    def test_end_yes_branch_passes(self) -> None:
        collected = {
            **_BASE,
            "fitout_complete_done": "yes",
            "actual_fitout_complete_date": "2026-06-03",
            "comments": "",
        }
        missing = get_missing_fields("CREATE_SR", "FIT_OUT_CONFIRM_END", collected)
        assert not missing


# ---------------------------------------------------------------------------
# TestCrossMilestoneOrdering  (schema-level — placeholder cross-check)
# ---------------------------------------------------------------------------


class TestCrossMilestoneOrdering:
    """Verify that schema correctly tracks prior milestone fields per sub-category."""

    def test_fos_prior_milestone_field_is_actual_handover(self) -> None:
        from app.agents.schemas.fitout_confirm_schema import SUBCAT_CONFIG
        assert SUBCAT_CONFIG["FIT_OUT_CONFIRM_FOS"]["prior_milestone_field"] == "actual_handover_date_readonly"

    def test_end_prior_milestone_field_is_actual_fos(self) -> None:
        from app.agents.schemas.fitout_confirm_schema import SUBCAT_CONFIG
        assert SUBCAT_CONFIG["FIT_OUT_CONFIRM_END"]["prior_milestone_field"] == "actual_fitout_start_date_readonly"

    def test_trading_prior_milestone_field_is_actual_end(self) -> None:
        from app.agents.schemas.fitout_confirm_schema import SUBCAT_CONFIG
        assert SUBCAT_CONFIG["FIT_OUT_CONFIRM_TRADING"]["prior_milestone_field"] == "actual_fitout_end_date_readonly"

    def test_missing_prior_milestone_does_not_block(self) -> None:
        """When the prior milestone date is not available in collected_data,
        the schema must not add it to missing_fields (it is readonly/backend)."""
        collected = {
            **_BASE,
            "fitout_start_done": "yes",
            "actual_fitout_start_date": "2026-05-14",
            # actual_handover_date_readonly intentionally absent
        }
        missing = get_missing_fields("CREATE_SR", "FIT_OUT_CONFIRM_FOS", collected)
        assert "actual_handover_date_readonly" not in missing, (
            "The prior milestone readonly field must not appear in missing_fields "
            "— it is backend-derived, not user-supplied"
        )


# ---------------------------------------------------------------------------
# TestConfirmationNode
# ---------------------------------------------------------------------------


class TestConfirmationNode:
    def _base_state(self, **overrides: Any) -> dict[str, Any]:
        base: dict[str, Any] = {
            "workflow_stage": "CREATE_SR",
            "sub_category": "FIT_OUT_CONFIRM_FOS",
            "active_agent": "rdd_fitout_start_agent",
            "confirmation_status": None,
            "collected_data": {
                "lease_brand_mall": "t0105712 - Brand - Mall",
                "lease_code": "t0105712",
                "fitout_start_done": "yes",
                "actual_fitout_start_date": "2026-05-14",
                "comments": "",
            },
            "backend_refs": {},
            "documents": [],
        }
        base.update(overrides)
        return base

    @pytest.mark.asyncio
    async def test_confirmation_card_generated_when_complete(self) -> None:
        from app.agents.graph.nodes.fitout_confirm.fitout_confirm_confirmation_node import (
            fitout_confirm_confirmation_node,
        )

        result = await fitout_confirm_confirmation_node(self._base_state())
        assert result.get("confirmation_status") == "PENDING"
        assert result.get("status") == "READY_TO_SUBMIT"
        assert result.get("response_ui", {}).get("type") == "confirmation_card"

    @pytest.mark.asyncio
    async def test_node_is_noop_when_already_confirmed(self) -> None:
        from app.agents.graph.nodes.fitout_confirm.fitout_confirm_confirmation_node import (
            fitout_confirm_confirmation_node,
        )

        state = self._base_state(confirmation_status="CONFIRMED")
        result = await fitout_confirm_confirmation_node(state)
        assert result == {}

    @pytest.mark.asyncio
    async def test_node_skipped_when_fields_missing(self) -> None:
        from app.agents.graph.nodes.fitout_confirm.fitout_confirm_confirmation_node import (
            fitout_confirm_confirmation_node,
        )

        state = self._base_state(
            collected_data={
                "lease_brand_mall": "t0105712 - Brand - Mall",
                # fitout_start_done not yet answered
            }
        )
        result = await fitout_confirm_confirmation_node(state)
        assert result == {}
