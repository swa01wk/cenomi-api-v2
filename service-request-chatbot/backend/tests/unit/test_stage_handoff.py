"""Unit tests — a stage that belongs to someone else is not collected.

Approving on FM advances the SR to RDD review, and the session followed it
there: a Mall or FM Manager was asked for the guideline link, which is the RDD
Project Manager's job on a request that is no longer theirs.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.agents.graph.nodes.handover.missing_field_node import missing_field_node


def _state(stage: str, role: str | None, **extra: Any) -> dict[str, Any]:
    state: dict[str, Any] = {
        "session_id": "sess-handoff",
        "user_id": "user-1",
        "active_agent": "rdd_handover_agent",
        "workflow_stage": stage,
        "collected_data": {
            "lease_code": "DUMMY-CUST-007",
            "lease_id": 3,
            "mall": "Dummy Mall",
            "brand": "Dummy",
            "unit_codes": ["U1"],
            "city": "Dummy City",
            "contracted_area": 200,
            "tenant_profile_id": 1,
            "property_id": 2,
            "brand_id": 4,
            "lease": "DUMMY-CUST-007",
        },
        "backend_refs": {"sr_id": "320993", **({"user_role": role} if role else {})},
    }
    state.update(extra)
    return state


class TestHandoff:
    @pytest.mark.asyncio
    async def test_fm_is_not_asked_for_the_rdd_guideline_link(self) -> None:
        result = await missing_field_node(_state("RDD_REVIEW", "FM_MANAGER"))

        message = result["response_message"]
        assert "320993" in message
        assert "with the RDD team" in message
        assert "guideline" not in message.lower()
        assert result["active_field"] is None
        assert result["missing_fields"] == []

    @pytest.mark.asyncio
    async def test_the_mall_manager_who_raised_it_is_told_too(self) -> None:
        result = await missing_field_node(_state("RDD_REVIEW", "MALL_MANAGER"))
        assert "with the RDD team" in result["response_message"]

    @pytest.mark.asyncio
    async def test_the_rdd_engineer_is_asked_as_normal(self) -> None:
        result = await missing_field_node(_state("RDD_REVIEW", "DD_ENGINEER"))
        assert result["response_ui"]["field"] == "guideLineLink"

    @pytest.mark.asyncio
    async def test_fm_still_works_its_own_stage(self) -> None:
        result = await missing_field_node(_state("FM_REVIEW", "FM_MANAGER"))
        assert result["response_ui"]["field"] == "unit_readiness_date"

    @pytest.mark.asyncio
    async def test_operations_shares_the_fm_stage(self) -> None:
        result = await missing_field_node(_state("FM_REVIEW", "OPERATIONS"))
        assert result["response_ui"]["field"] == "unit_readiness_date"

    @pytest.mark.asyncio
    async def test_an_unknown_role_is_left_alone(self) -> None:
        """No role on the turn means no judgement to make — behave as before."""
        result = await missing_field_node(_state("RDD_REVIEW", None))
        assert result["response_ui"]["field"] == "guideLineLink"

    @pytest.mark.asyncio
    async def test_the_create_stages_are_never_handed_off(self) -> None:
        result = await missing_field_node(_state("CREATE_SR", "MALL_MANAGER"))
        assert result["response_ui"]["field"] != "_permission"
        assert "with the" not in (result.get("response_message") or "")

    @pytest.mark.asyncio
    async def test_the_tour_is_not_offered_on_a_stage_that_is_not_yours(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The re-asked guided tour was the symptom that surfaced this."""
        from app.core.config import get_settings

        monkeypatch.setattr(get_settings(), "guided_tour_choice_enabled", True)
        result = await missing_field_node(_state("RDD_REVIEW", "FM_MANAGER"))
        assert "guide you through the form" not in result["response_message"]
