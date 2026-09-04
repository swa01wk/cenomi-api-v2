"""Unit tests — the four-turn CREATE_SR flow (HANDOVER_SHORT_FLOW_ENABLED).

Test groups
-----------
TestFlagOff                 — Default behaviour is byte-for-byte the long flow.
TestUnaskedFields           — Generated text / times drop out of the ask loop.
TestCombinedInspectionAsk   — One question for window + inspector, and its chips.
TestPartialAnswers          — Whatever the user leaves out is asked again, narrowed.
TestAutofill                — Generated values, provenance, and no-overwrite rules.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from app.agents.graph.nodes.handover.missing_field_node import missing_field_node
from app.agents.schemas.handover_schema import (
    INSPECTION_FIELD_GROUP,
    get_missing_fields,
)
from app.agents.services import sr_autofill_service
from app.agents.services.inspection_suggestions_service import build_inspection_options


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture()
def short_flow_on(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "handover_short_flow_enabled", True)


def _lease_resolved(**extra: Any) -> dict[str, Any]:
    """collected_data with the lease lookup already done."""
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


def _state(collected: dict[str, Any]) -> dict[str, Any]:
    return {
        "session_id": "sess-short-flow",
        "user_id": "user-1",
        "active_agent": "rdd_handover_agent",
        "workflow_stage": "CREATE_SR",
        "collected_data": collected,
    }


def _chip_labels(result: dict[str, Any]) -> list[str]:
    options = (
        result.get("response_decorations", {})
        .get("suggestions", {})
        .get("options", [])
    )
    return [o["label"] for o in options]


def _chip_values(result: dict[str, Any]) -> list[str]:
    options = (
        result.get("response_decorations", {})
        .get("suggestions", {})
        .get("options", [])
    )
    return [o["value"] for o in options]


# ---------------------------------------------------------------------------
# TestFlagOff
# ---------------------------------------------------------------------------


class TestFlagOff:
    def test_title_and_description_still_missing(self) -> None:
        """With the flag off the long flow is untouched."""
        missing = get_missing_fields("CREATE_SR", _lease_resolved(already_handovered="initiate_handover"))
        assert "title" in missing
        assert "description" in missing
        assert "notes" in missing

    @pytest.mark.asyncio
    async def test_asks_one_field_at_a_time(self) -> None:
        collected = _lease_resolved(already_handovered="initiate_handover")
        result = await missing_field_node(_state(collected))
        # Title is the next field in stage order, asked on its own.
        assert result["response_ui"]["field"] == "title"


# ---------------------------------------------------------------------------
# TestUnaskedFields
# ---------------------------------------------------------------------------


class TestUnaskedFields:
    def test_generated_text_fields_are_not_missing(self, short_flow_on: None) -> None:
        missing = get_missing_fields("CREATE_SR", _lease_resolved(already_handovered="initiate_handover"))
        for field in ("title", "description", "notes", "comments"):
            assert field not in missing

    def test_inspection_times_are_not_missing(self, short_flow_on: None) -> None:
        missing = get_missing_fields("CREATE_SR", _lease_resolved(already_handovered="initiate_handover"))
        assert "inspectionTime" not in missing
        assert "inspectionEndTime" not in missing

    def test_inspection_group_is_still_required(self, short_flow_on: None) -> None:
        missing = get_missing_fields("CREATE_SR", _lease_resolved(already_handovered="initiate_handover"))
        assert missing == list(INSPECTION_FIELD_GROUP)

    def test_handed_over_stage_keeps_its_own_dates(self, short_flow_on: None) -> None:
        missing = get_missing_fields("CREATE_HANDED_OVER_SR", _lease_resolved())
        assert "comments" not in missing
        assert "title" not in missing
        assert "actual_shop_handover_date" in missing


# ---------------------------------------------------------------------------
# TestCombinedInspectionAsk
# ---------------------------------------------------------------------------


class TestCombinedInspectionAsk:
    @pytest.mark.asyncio
    async def test_handover_type_is_still_asked_first(self, short_flow_on: None) -> None:
        """The group must not jump the queue ahead of the handover type."""
        result = await missing_field_node(_state(_lease_resolved()))
        assert result["response_ui"]["field"] == "already_handovered"

    @pytest.mark.asyncio
    async def test_single_question_covers_all_three_fields(self, short_flow_on: None) -> None:
        collected = _lease_resolved(already_handovered="initiate_handover")
        result = await missing_field_node(_state(collected))

        message = result["response_message"]
        assert "Inspection details" in message
        # Every field gets its own explainer line.
        assert "Start date" in message
        assert "End date" in message
        assert "Done by" in message
        # Bare answers bind to the first open member.
        assert result["active_field"] == "startDate"
        assert result["status"] == "WAITING_FOR_USER"

    @pytest.mark.asyncio
    async def test_offers_two_windows_and_both_roles(self, short_flow_on: None) -> None:
        collected = _lease_resolved(already_handovered="initiate_handover")
        result = await missing_field_node(_state(collected))
        labels = _chip_labels(result)
        assert len(labels) == 4
        assert labels[-2:] == ["FM Manager", "Operations"]

    def test_window_chips_are_today_plus_three_and_two(self) -> None:
        options = build_inspection_options(
            {}, {"startDate", "endDate"}, today=date(2026, 9, 4)
        )
        assert [o["value"] for o in options] == [
            "Start 07/09/2026, End 08/09/2026",
            "Start 06/09/2026, End 07/09/2026",
        ]

    def test_role_chips_keep_their_direct_set_actions(self) -> None:
        options = build_inspection_options({}, {"inspection_done_by"}, today=date(2026, 9, 4))
        assert [o["action"] for o in options] == [
            "select_inspection_done_by_fm_manager",
            "select_inspection_done_by_operations",
        ]


# ---------------------------------------------------------------------------
# TestPartialAnswers
# ---------------------------------------------------------------------------


class TestPartialAnswers:
    @pytest.mark.asyncio
    async def test_dates_given_asks_only_for_inspector(self, short_flow_on: None) -> None:
        collected = _lease_resolved(
            already_handovered="initiate_handover",
            startDate="2026-09-06",
            endDate="2026-09-07",
        )
        result = await missing_field_node(_state(collected))

        message = result["response_message"]
        assert "6 Sep 2026" in message and "7 Sep 2026" in message
        assert "conducting the inspection" in message
        assert result["active_field"] == "inspection_done_by"
        assert _chip_labels(result) == ["FM Manager", "Operations"]

    @pytest.mark.asyncio
    async def test_inspector_given_asks_only_for_window(self, short_flow_on: None) -> None:
        collected = _lease_resolved(
            already_handovered="initiate_handover",
            inspection_done_by="FM_MANAGER",
        )
        result = await missing_field_node(_state(collected))

        assert result["active_field"] == "startDate"
        assert "inspection window" in result["response_message"]
        assert len(_chip_labels(result)) == 2

    @pytest.mark.asyncio
    async def test_start_only_anchors_end_chips_to_the_start(self, short_flow_on: None) -> None:
        collected = _lease_resolved(
            already_handovered="initiate_handover",
            startDate="2026-12-01",
            inspection_done_by="OPERATIONS",
        )
        result = await missing_field_node(_state(collected))

        assert result["active_field"] == "endDate"
        # Anchored to the start the user gave, not to today.
        assert _chip_values(result) == ["End 02/12/2026", "End 03/12/2026"]

    @pytest.mark.asyncio
    async def test_group_complete_stops_asking(self, short_flow_on: None) -> None:
        collected = _lease_resolved(
            already_handovered="initiate_handover",
            startDate="2026-09-06",
            endDate="2026-09-07",
            inspection_done_by="FM_MANAGER",
        )
        result = await missing_field_node(_state(collected))
        assert result["status"] == "IN_PROGRESS"
        assert result["response_message"] == ""


# ---------------------------------------------------------------------------
# TestAutofill
# ---------------------------------------------------------------------------


class TestAutofill:
    @pytest.mark.asyncio
    async def test_fills_every_unasked_text_field(self) -> None:
        collected = _lease_resolved(
            already_handovered="initiate_handover",
            startDate="2026-09-06",
            endDate="2026-09-07",
            inspection_done_by="FM_MANAGER",
        )
        values, provenance = await sr_autofill_service.autofill_text_fields(
            collected, "CREATE_SR", use_ai=False
        )
        assert set(values) == {"title", "description", "notes", "comments"}
        assert all(v["source"] == "template" for v in provenance.values())

    @pytest.mark.asyncio
    async def test_notes_name_the_window_and_inspector(self) -> None:
        collected = _lease_resolved(
            already_handovered="initiate_handover",
            startDate="2026-09-06",
            endDate="2026-09-07",
            inspection_done_by="FM_MANAGER",
        )
        values, _ = await sr_autofill_service.autofill_text_fields(
            collected, "CREATE_SR", use_ai=False
        )
        assert "6 Sep 2026 to 7 Sep 2026" in values["notes"]
        assert "FM Manager" in values["notes"]

    @pytest.mark.asyncio
    async def test_never_overwrites_what_the_user_wrote(self) -> None:
        collected = _lease_resolved(
            already_handovered="initiate_handover",
            startDate="2026-09-06",
            endDate="2026-09-07",
            inspection_done_by="FM_MANAGER",
            description="Snagging round before opening",
        )
        values, _ = await sr_autofill_service.autofill_text_fields(
            collected, "CREATE_SR", use_ai=False
        )
        assert "description" not in values
        # The user's own words still shape the title slug.
        assert values["title"] == "handover-t0112136-snagging-round-before-opening"

    @pytest.mark.asyncio
    async def test_title_is_template_even_when_ai_is_on(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def _fake_polish(collected: dict[str, Any], fields: list[str]) -> dict[str, str]:
            return {"description": "Pre-opening handover inspection at Riyadh Park."}

        monkeypatch.setattr(sr_autofill_service, "_polish_with_llm", _fake_polish)
        collected = _lease_resolved(
            already_handovered="initiate_handover",
            startDate="2026-09-06",
            endDate="2026-09-07",
            inspection_done_by="FM_MANAGER",
        )
        values, provenance = await sr_autofill_service.autofill_text_fields(
            collected, "CREATE_SR", use_ai=True
        )
        assert values["description"].startswith("Pre-opening handover")
        assert provenance["description"]["source"] == "ai"
        assert values["title"].startswith("handover-t0112136-")
        assert provenance["title"]["source"] == "template"

    @pytest.mark.asyncio
    async def test_falls_back_to_template_when_the_model_fails(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def _boom(collected: dict[str, Any], fields: list[str]) -> dict[str, str]:
            return {}

        monkeypatch.setattr(sr_autofill_service, "_polish_with_llm", _boom)
        collected = _lease_resolved(
            already_handovered="initiate_handover",
            startDate="2026-09-06",
            endDate="2026-09-07",
            inspection_done_by="FM_MANAGER",
        )
        values, provenance = await sr_autofill_service.autofill_text_fields(
            collected, "CREATE_SR", use_ai=True
        )
        assert values["description"].startswith("Handover inspection for lease")
        assert provenance["description"]["source"] == "template"

    @pytest.mark.asyncio
    async def test_nothing_generated_before_the_window_is_known(self) -> None:
        from app.agents.graph.nodes.shared.merge_state_node import _short_flow_autofill_ready

        collected = _lease_resolved(already_handovered="initiate_handover")
        assert _short_flow_autofill_ready(collected, "CREATE_SR") is False
        collected.update(
            startDate="2026-09-06", endDate="2026-09-07", inspection_done_by="FM_MANAGER"
        )
        assert _short_flow_autofill_ready(collected, "CREATE_SR") is True
