"""Unit tests — fitout confirm entry: already-confirmed milestone chips."""

from __future__ import annotations

from typing import Any

import pytest

from app.agents.graph.nodes.fitout_confirm.fitout_confirm_entry_node import (
    fitout_confirm_entry_node,
)


def _state(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "user_message": "",
        "confirmation_status": None,
        "action_override": None,
        "collected_data": {},
        "backend_refs": {},
        "workflow_stage": "CREATE_SR",
        "active_agent": "rdd_fitout_start_agent",
    }
    base.update(overrides)
    return base


class TestFitoutMilestoneContinueCancel:
    @pytest.mark.asyncio
    async def test_continue_milestone_acks_and_keeps_lease(self) -> None:
        state = _state(
            action_override="continue_milestone",
            confirmation_status="MILESTONE_PENDING",
            collected_data={"lease_code": "t0031371", "lease_id": 42},
        )
        result = await fitout_confirm_entry_node(state)
        assert result.get("confirmation_status") is None
        assert result.get("status") == "IN_PROGRESS"
        assert result["collected_data"]["_milestone_confirmed_ack"] is True
        assert result["collected_data"]["lease_id"] == 42

    @pytest.mark.asyncio
    async def test_cancel_milestone_clears_draft(self) -> None:
        state = _state(
            action_override="cancel_milestone",
            confirmation_status="MILESTONE_PENDING",
            collected_data={"lease_code": "t0031371", "lease_id": 42},
        )
        result = await fitout_confirm_entry_node(state)
        assert result.get("collected_data") == {}
        assert result.get("response_ui", {}).get("reset_conversation") is True
        assert "cleared" in result.get("response_message", "").lower()
