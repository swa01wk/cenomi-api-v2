"""Unit tests — the four-turn CREATE_SR flow (HANDOVER_SHORT_FLOW_ENABLED).

Test groups
-----------
TestFlagOff                 — Default behaviour is byte-for-byte the long flow.
TestUnaskedFields           — Generated text / times drop out of the ask loop.
TestCombinedInspectionAsk   — One question for window + inspector, and its chips.
TestPartialAnswers          — Whatever the user leaves out is asked again, narrowed.
TestAutofill                — Generated values, provenance, and no-overwrite rules.
TestMergeStateAutofill      — merge_state_node writes the values and their provenance.
TestReviewCard              — Badges, hints and the raised-by / reviewed-next-by rows.
TestEditableWindow          — The window is fixable on the card: date and time pickers.
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


# ---------------------------------------------------------------------------
# TestMergeStateAutofill
# ---------------------------------------------------------------------------


def _merge_state(collected: dict[str, Any]) -> dict[str, Any]:
    return {
        "session_id": "sess-short-flow",
        "user_id": "user-1",
        "active_agent": "rdd_handover_agent",
        "workflow_stage": "CREATE_SR",
        "collected_data": collected,
        "extracted_fields": {},
        "user_message": "",
    }


class TestMergeStateAutofill:
    @pytest.mark.asyncio
    async def test_nothing_written_before_the_window_is_known(
        self, short_flow_on: None
    ) -> None:
        from app.agents.graph.nodes.shared.merge_state_node import merge_state_node

        result = await merge_state_node(
            _merge_state(_lease_resolved(already_handovered="initiate_handover"))
        )
        collected = result["collected_data"]
        assert collected.get("description") is None
        assert "_ai_field_meta" not in collected

    @pytest.mark.asyncio
    async def test_fills_and_records_provenance_once_complete(
        self, short_flow_on: None
    ) -> None:
        from app.agents.graph.nodes.shared.merge_state_node import merge_state_node

        result = await merge_state_node(
            _merge_state(
                _lease_resolved(
                    already_handovered="initiate_handover",
                    startDate="2026-09-06",
                    endDate="2026-09-07",
                    inspection_done_by="FM_MANAGER",
                )
            )
        )
        collected = result["collected_data"]
        assert collected["title"] == "handover-t0112136"
        assert collected["description"]
        assert collected["notes"]
        assert collected["comments"]
        meta = collected["_ai_field_meta"]
        assert meta["description"]["source"] == "template"
        # The generated text is kept so a later edit can be detected.
        assert meta["description"]["value"] == collected["description"]

    @pytest.mark.asyncio
    async def test_flag_off_leaves_the_fields_alone(self) -> None:
        from app.agents.graph.nodes.shared.merge_state_node import merge_state_node

        result = await merge_state_node(
            _merge_state(
                _lease_resolved(
                    already_handovered="initiate_handover",
                    startDate="2026-09-06",
                    endDate="2026-09-07",
                    inspection_done_by="FM_MANAGER",
                )
            )
        )
        collected = result["collected_data"]
        assert collected.get("description") is None
        assert "_ai_field_meta" not in collected


# ---------------------------------------------------------------------------
# TestReviewCard
# ---------------------------------------------------------------------------


def _card(collected: dict[str, Any], backend_refs: dict[str, Any] | None = None):
    from app.agents.graph.nodes.handover.confirmation_node import _build_confirmation_card

    card = _build_confirmation_card(collected, "CREATE_SR", backend_refs)
    return {f["key"]: f for f in card["fields"]}


def _complete_draft(**extra: Any) -> dict[str, Any]:
    collected = _lease_resolved(
        already_handovered="initiate_handover",
        startDate="2026-09-06",
        endDate="2026-09-07",
        inspection_done_by="FM_MANAGER",
        title="handover-t0112136",
        description="Handover inspection for lease t0112136 - Nike at Riyadh Park.",
        notes="Inspection scheduled 6 Sep 2026 to 7 Sep 2026, conducted by FM Manager.",
        comments="No additional comments provided for lease t0112136.",
    )
    collected["_ai_field_meta"] = {
        "description": {
            "source": "template",
            "confidence": 1.0,
            "value": collected["description"],
        },
        "notes": {"source": "ai", "confidence": 0.8, "value": collected["notes"]},
    }
    collected.update(extra)
    return collected

class TestReviewCard:
    def test_generated_rows_are_labelled_and_stay_editable(self) -> None:
        rows = _card(_complete_draft())
        assert rows["description"]["generated"] is True
        assert rows["description"]["source"] == "template"
        assert rows["description"]["editable"] is True
        assert "confidence" not in rows["description"]

    def test_ai_rows_carry_a_confidence_and_a_hint(self) -> None:
        rows = _card(_complete_draft())
        assert rows["notes"]["source"] == "ai"
        assert rows["notes"]["confidence"] == 0.8
        assert "assistant" in rows["notes"]["hint"]

    def test_an_edited_row_is_no_longer_marked_generated(self) -> None:
        rows = _card(_complete_draft(description="Snagging round before opening"))
        assert rows["description"]["source"] == "user"
        assert "generated" not in rows["description"]

    def test_rows_the_user_supplied_carry_no_provenance(self) -> None:
        rows = _card(_complete_draft())
        assert "source" not in rows["startDate"]

    def test_flag_off_keeps_the_card_exactly_as_it_was(self) -> None:
        """No extra rows for the long flow — QA scripts assert the field list."""
        rows = _card(_complete_draft(), {"user_role": "MALL_MANAGER"})
        assert "created_by_role" not in rows
        assert "checker_role" not in rows

    def test_raised_by_and_reviewed_next_by(self, short_flow_on: None) -> None:
        rows = _card(_complete_draft(), {"user_role": "MALL_MANAGER"})
        assert rows["created_by_role"]["value"] == "Mall Manager"
        assert rows["created_by_role"]["editable"] is False
        assert rows["checker_role"]["value"] == "FM Manager"
        assert rows["checker_role"]["editable"] is False

    def test_raised_by_is_omitted_when_the_role_is_unknown(self, short_flow_on: None) -> None:
        rows = _card(_complete_draft(), {})
        assert "created_by_role" not in rows
        assert rows["checker_role"]["value"] == "FM Manager"


# ---------------------------------------------------------------------------
# TestEditableWindow
# ---------------------------------------------------------------------------


class TestEditableWindow:
    def test_dates_and_times_are_separate_editable_rows(
        self, short_flow_on: None
    ) -> None:
        rows = _card(_complete_draft(inspectionTime="14:00", inspectionEndTime="18:00"))
        for key in ("startDate", "inspectionTime", "endDate", "inspectionEndTime"):
            assert rows[key]["editable"] is True, key

    def test_each_clock_sits_under_its_own_date(self, short_flow_on: None) -> None:
        from app.agents.graph.nodes.handover.confirmation_node import (
            _build_confirmation_card,
        )

        card = _build_confirmation_card(_complete_draft(), "CREATE_SR", None)
        keys = [f["key"] for f in card["fields"]]
        assert keys[keys.index("startDate") + 1] == "inspectionTime"
        assert keys[keys.index("endDate") + 1] == "inspectionEndTime"

    def test_rows_ask_for_native_pickers(self, short_flow_on: None) -> None:
        rows = _card(_complete_draft())
        assert rows["startDate"]["inputType"] == "date"
        assert rows["endDate"]["inputType"] == "date"
        assert rows["inspectionTime"]["inputType"] == "time"
        assert rows["inspectionEndTime"]["inputType"] == "time"

    def test_dates_stay_machine_readable_for_the_picker(
        self, short_flow_on: None
    ) -> None:
        """A date input round-trips YYYY-MM-DD, not '06/09/2026 09:00 AM'."""
        rows = _card(_complete_draft(inspectionTime="14:00"))
        assert rows["startDate"]["value"] == "2026-09-06"
        assert rows["endDate"]["value"] == "2026-09-07"

    def test_clock_rows_show_the_time_that_will_be_submitted(
        self, short_flow_on: None
    ) -> None:
        rows = _card(_complete_draft(inspectionTime="14:00", inspectionEndTime="18:00"))
        assert rows["inspectionTime"]["value"] == "14:00"
        assert rows["inspectionEndTime"]["value"] == "18:00"

    def test_unanswered_clock_shows_the_real_default(self, short_flow_on: None) -> None:
        """The payload books 09:00 when no time was given — say so on the card."""
        rows = _card(_complete_draft())
        assert rows["inspectionTime"]["value"] == "09:00"
        assert rows["inspectionEndTime"]["value"] == "09:00"

    def test_inspector_is_a_dropdown(self, short_flow_on: None) -> None:
        rows = _card(_complete_draft())
        assert rows["inspection_done_by"]["editable"] is True
        assert rows["inspection_done_by"]["options"] == ["FM_MANAGER", "OPERATIONS"]

    def test_flag_off_keeps_the_old_single_datetime_row(self) -> None:
        rows = _card(_complete_draft(inspectionTime="14:00"))
        assert "inspectionTime" not in rows
        # Long flow folds the clock into the date's display value.
        assert rows["startDate"]["value"] == "06/09/2026 2:00 PM"
        assert "inputType" not in rows["startDate"]

    @pytest.mark.asyncio
    async def test_edited_date_and_time_come_back_through_the_card(
        self, short_flow_on: None
    ) -> None:
        """A picker sends YYYY-MM-DD / HH:MM — the same shapes merge already stores."""
        from app.agents.graph.nodes.shared.merge_state_node import merge_state_node

        state = _merge_state(_complete_draft())
        state["corrected_fields"] = {
            "startDate": "2026-09-10",
            "inspectionTime": "14:00",
            "inspectionEndTime": "18:00",
        }
        result = await merge_state_node(state)
        collected = result["collected_data"]

        assert collected["startDate"] == "2026-09-10"
        assert collected["inspectionTime"] == "14:00"
        assert collected["inspectionEndTime"] == "18:00"
        # The portal's local-time strings follow the edit.
        assert collected["startDateLT"] == "10/09/2026 2:00 PM"
        assert collected["endDateLT"] == "07/09/2026 6:00 PM"
