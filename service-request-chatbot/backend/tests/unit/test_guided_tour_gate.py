"""Unit tests — the guided-tour choice (GUIDED_TOUR_CHOICE_ENABLED).

The choice governs the form, not the conversation: the assistant asks the same
questions either way. These cover the gate itself and the fact that it is one
mechanism shared by every workflow rather than a handover-only feature.

Test groups
-----------
TestFlagOff          — Default is today's behaviour, nothing asked.
TestAsking           — When the question goes out, and when it must not.
TestAnswering        — Taps, typed answers, and answers meant for something else.
TestEveryWorkflow    — FM review, RDD review, work permit and fitout inherit it.
TestPublishedMode    — The client is told which mode is in force.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.agents.graph.nodes.handover.missing_field_node import missing_field_node
from app.agents.services import guided_tour_service as tour


@pytest.fixture()
def tour_on(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "guided_tour_choice_enabled", True)


def _lease_resolved(**extra: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "tenant_profile_id": 1,
        "property_id": 2,
        "lease_code": "t0112136",
        "lease_id": 3,
        "brand_id": 4,
        "mall": "Riyadh Park",
        "brand": "Nike",
        "lease": "t0112136",
        "unit_codes": ["GF101"],
        "city": "Riyadh",
        "contracted_area": 120,
    }
    base.update(extra)
    return base


def _state(collected: dict[str, Any], **extra: Any) -> dict[str, Any]:
    state = {
        "session_id": "sess-tour",
        "user_id": "user-1",
        "active_agent": "rdd_handover_agent",
        "workflow_stage": "CREATE_SR",
        "collected_data": collected,
    }
    state.update(extra)
    return state


def _chips(result: dict[str, Any]) -> list[str]:
    options = (
        result.get("response_decorations", {}).get("suggestions", {}).get("options", [])
    )
    return [o["label"] for o in options]


class TestFlagOff:
    @pytest.mark.asyncio
    async def test_nothing_is_asked(self) -> None:
        result = await missing_field_node(_state(_lease_resolved()))
        assert result["response_ui"]["field"] == "already_handovered"

    def test_mode_defaults_to_guided(self) -> None:
        """An unanswered draft must never unlock the form."""
        assert tour.mode({}) == tour.GUIDED
        assert tour.is_open_form({}) is False


class TestAsking:
    @pytest.mark.asyncio
    async def test_asked_before_the_first_field_question(self, tour_on: None) -> None:
        result = await missing_field_node(_state(_lease_resolved()))

        assert "guide you through the form" in result["response_message"]
        assert _chips(result) == ["Guide me", "Leave the form open"]
        # Not a form field: nothing should try to highlight or bind to it.
        assert result["active_field"] is None
        assert result["collected_data"]["guided_tour"] == tour.ASKING

    @pytest.mark.asyncio
    async def test_not_asked_before_the_lease_is_known(self, tour_on: None) -> None:
        """The lease comes first — there is no form to talk about yet."""
        result = await missing_field_node(_state({}))
        assert result["response_ui"]["field"] == "lease_code"

    @pytest.mark.asyncio
    async def test_not_asked_twice(self, tour_on: None) -> None:
        collected = _lease_resolved(guided_tour=tour.OPEN)
        result = await missing_field_node(_state(collected))
        assert result["response_ui"]["field"] == "already_handovered"

    @pytest.mark.asyncio
    async def test_not_asked_when_there_is_nothing_to_collect(
        self, tour_on: None
    ) -> None:
        """A complete draft is heading for its review card, not a form."""
        collected = _lease_resolved(
            already_handovered="initiate_handover",
            title="handover-t0112136",
            description="Inspection.",
            startDate="2026-09-10",
            endDate="2026-09-11",
            inspectionTime="09:00",
            inspectionEndTime="09:00",
            inspection_done_by="FM_MANAGER",
            notes="None.",
            comments="None.",
        )
        result = await missing_field_node(_state(collected))
        assert result["status"] == "IN_PROGRESS"
        assert result["response_message"] == ""


class TestAnswering:
    def test_chip_records_the_choice(self, tour_on: None) -> None:
        collected = {"guided_tour": tour.ASKING}
        assert tour.apply_answer(collected, {"action_override": tour.ACTION_OPEN})
        assert collected["guided_tour"] == tour.OPEN
        assert tour.is_open_form(collected) is True

    @pytest.mark.parametrize(
        ("typed", "expected"),
        [
            ("guide me", tour.GUIDED),
            ("yes", tour.GUIDED),
            ("walk me through it", tour.GUIDED),
            ("no", tour.OPEN),
            ("leave the form open", tour.OPEN),
            ("i'll do it myself", tour.OPEN),
        ],
    )
    def test_typed_answers_are_read_without_an_llm(
        self, tour_on: None, typed: str, expected: str
    ) -> None:
        collected = {"guided_tour": tour.ASKING}
        assert tour.apply_answer(collected, {"user_message": typed})
        assert collected["guided_tour"] == expected

    def test_an_unrelated_message_leaves_it_pending(self, tour_on: None) -> None:
        collected = {"guided_tour": tour.ASKING}
        assert tour.apply_answer(collected, {"user_message": "what is a lease?"}) is False
        assert collected["guided_tour"] == tour.ASKING

    def test_a_no_meant_for_something_else_is_not_stolen(self, tour_on: None) -> None:
        """'No' at the confirmation card must not read as 'leave the form open'."""
        state = {"collected_data": {}, "user_message": "no"}
        assert tour.has_pending_answer(state) is False

    def test_the_answer_is_not_re_read_later(self, tour_on: None) -> None:
        state = {"collected_data": {"guided_tour": tour.OPEN}, "user_message": "no"}
        assert tour.has_pending_answer(state) is False


class TestEveryWorkflow:
    """One mechanism, inherited by every workflow that collects fields."""

    def test_every_registered_workflow_has_the_gate_on(self) -> None:
        from app.agents.registries.workflow_config import WORKFLOW_CONFIG_REGISTRY
        from app.agents.registries.workflow_config import get_workflow_config

        # Touch the registry so the lazy configs are built.
        get_workflow_config("rdd_handover_agent")
        assert WORKFLOW_CONFIG_REGISTRY
        for name, cfg in WORKFLOW_CONFIG_REGISTRY.items():
            assert cfg.guided_tour_gate is True, name

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("agent", "stage"),
        [
            ("rdd_handover_agent", "FM_REVIEW"),
            ("rdd_handover_agent", "RDD_REVIEW"),
            ("rdd_handover_agent", "CREATE_HANDED_OVER_SR"),
        ],
    )
    async def test_review_stages_ask_it_too(
        self, tour_on: None, agent: str, stage: str
    ) -> None:
        result = await missing_field_node(
            _state(_lease_resolved(), active_agent=agent, workflow_stage=stage)
        )
        assert "guide you through the form" in result["response_message"]

    @pytest.mark.asyncio
    async def test_work_permit_asks_it_too(self, tour_on: None) -> None:
        result = await missing_field_node(
            _state(
                _lease_resolved(),
                active_agent="work_permit_agent",
                workflow_stage="CREATE_WORK_PERMIT",
            )
        )
        assert "guide you through the form" in result["response_message"]

    def test_every_agent_routes_a_pending_answer_to_merge_state(
        self, tour_on: None
    ) -> None:
        """Extraction has no field to bind this to — it must not go there."""
        from app.agents.graph import help_agent_graph as g

        routes = [
            g._route_after_handover_entry,
            g._route_after_fm_entry,
            g._route_after_rdd_entry,
            g._route_after_work_permit_entry,
            g._route_after_fitout_confirm_entry,
        ]
        state = {
            "collected_data": {"guided_tour": tour.ASKING},
            "action_override": tour.ACTION_OPEN,
            "status": "WAITING_FOR_USER",
        }
        for route in routes:
            assert route(dict(state)) == "merge_state", route.__name__


class TestPublishedMode:
    def test_mode_is_only_published_once_chosen(self) -> None:
        assert tour.answered({}) is False
        assert tour.answered({"guided_tour": tour.ASKING}) is False
        assert tour.answered({"guided_tour": tour.GUIDED}) is True
