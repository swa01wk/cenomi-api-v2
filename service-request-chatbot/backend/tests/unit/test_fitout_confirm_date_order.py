"""Unit tests — confirmation SR dates may not fall before their prior milestone.

The main form enforces Fitout Start ≥ Actual Handover, Fitout Complete ≥
Fitout Start and Trading ≥ Fitout End. The assistant did not, and created a
Fitout Start confirmation (session 89974cec) with fit-out start 27/08/2026
before the actual handover 12/10/2026.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.agents.graph.nodes.shared.missing_field_node import missing_field_node
from app.agents.graph.nodes.shared.validation_node import validation_node
from app.agents.services.validation_service import validate_fitout_confirm_date_order

FOS = "FIT_OUT_CONFIRM_FOS"
END = "FIT_OUT_CONFIRM_END"
TRADING = "FIT_OUT_CONFIRM_TRADING"


class TestTheRule:
    def test_fitout_start_before_handover_is_refused(self) -> None:
        errors = validate_fitout_confirm_date_order(
            FOS,
            {"actual_handover_date": "2026-10-12", "actual_fitout_start_date": "2026-08-27"},
        )
        assert [e["field"] for e in errors] == ["actual_fitout_start_date"]
        assert errors[0]["blocking"] is True
        assert "27/08/2026" in errors[0]["message"]
        assert "12/10/2026" in errors[0]["message"]

    def test_the_same_day_is_allowed(self) -> None:
        assert validate_fitout_confirm_date_order(
            FOS,
            {"actual_handover_date": "12/10/2026", "actual_fitout_start_date": "2026-10-12"},
        ) == []

    def test_a_later_date_passes(self) -> None:
        assert validate_fitout_confirm_date_order(
            FOS,
            {"actual_handover_date": "2026-10-12", "actual_fitout_start_date": "2026-10-21"},
        ) == []

    def test_fitout_complete_before_fitout_start_is_refused(self) -> None:
        errors = validate_fitout_confirm_date_order(
            END,
            {"actual_fitout_start_date": "2026-09-01", "actual_fitout_complete_date": "2026-08-20"},
        )
        assert [e["field"] for e in errors] == ["actual_fitout_complete_date"]

    def test_trading_before_fitout_end_is_refused(self) -> None:
        errors = validate_fitout_confirm_date_order(
            TRADING,
            {"actual_fitout_end_date": "2026-09-30T00:00:00Z", "actual_trading_date": "2026-09-10"},
        )
        assert [e["field"] for e in errors] == ["actual_trading_date"]

    def test_the_readonly_milestone_wins_over_the_plain_key(self) -> None:
        errors = validate_fitout_confirm_date_order(
            FOS,
            {
                "actual_handover_date_readonly": "2026-10-12",
                "actual_handover_date": "2026-01-01",
                "actual_fitout_start_date": "2026-08-27",
            },
        )
        assert errors and "12/10/2026" in errors[0]["message"]

    def test_nothing_to_compare_is_not_checked(self) -> None:
        assert validate_fitout_confirm_date_order(FOS, {"actual_fitout_start_date": "2026-08-27"}) == []
        assert validate_fitout_confirm_date_order(FOS, {"actual_handover_date": "2026-10-12"}) == []
        assert validate_fitout_confirm_date_order("HANDOVER", {"actual_fitout_start_date": "x"}) == []


def _fos_state(**collected: Any) -> dict[str, Any]:
    return {
        "session_id": "sess-fos",
        "user_id": "user-1",
        "active_agent": "rdd_fitout_start_agent",
        "sub_category": FOS,
        "workflow_stage": "CREATE_SR",
        "collected_data": {
            "lease_code": "DUMMY-CUST-009",
            "lease_id": 274596,
            "fitout_start_done": "yes",
            "guided_tour": "guided",
            **collected,
        },
    }


class TestTheFlow:
    @pytest.mark.asyncio
    async def test_validation_blocks_the_live_case(self) -> None:
        state = _fos_state(
            actual_handover_date="2026-10-12", actual_fitout_start_date="2026-08-27"
        )
        result = await validation_node(state)  # type: ignore[arg-type]
        order = [
            e for e in result["validation_errors"]
            if e["validation_type"] == "fitout_confirm_date_order"
        ]
        assert order and order[0]["blocking"] is True
        assert order[0]["field"] == "actual_fitout_start_date"

    @pytest.mark.asyncio
    async def test_the_assistant_asks_for_the_date_again_naming_both(self) -> None:
        state = _fos_state(
            actual_handover_date="2026-10-12", actual_fitout_start_date="2026-08-27"
        )
        state["validation_errors"] = validate_fitout_confirm_date_order(
            FOS, state["collected_data"]
        )
        result = await missing_field_node(state)  # type: ignore[arg-type]
        assert result["active_field"] == "actual_fitout_start_date"
        assert "12/10/2026" in result["response_message"]
        assert "27/08/2026" in result["response_message"]
