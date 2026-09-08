"""Unit tests — Path B's four actual dates asked as one question.

Already Trading records a unit that is already open, so every date comes from
the Mall Manager. Nothing is suggested and nothing is derived; the only saving
available is asking once instead of four times.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.agents.graph.nodes.handover.missing_field_node import missing_field_node
from app.agents.schemas.handover_schema import HANDED_OVER_FIELD_GROUP


@pytest.fixture()
def short_flow_on(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "handover_short_flow_enabled", True)


def _resolved(**extra: Any) -> dict[str, Any]:
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
        "already_handovered": "already_trading",
    }
    base.update(extra)
    return base


def _state(collected: dict[str, Any]) -> dict[str, Any]:
    return {
        "session_id": "sess-path-b",
        "user_id": "user-1",
        "active_agent": "rdd_handover_agent",
        "workflow_stage": "CREATE_HANDED_OVER_SR",
        "collected_data": collected,
    }


class TestFlagOff:
    @pytest.mark.asyncio
    async def test_asks_one_field_at_a_time(self) -> None:
        """Title first, then each date on its own — the long flow, untouched."""
        result = await missing_field_node(_state(_resolved()))
        assert result["response_ui"]["field"] == "title"
        assert "fields" not in result["response_ui"]

    @pytest.mark.asyncio
    async def test_asks_the_dates_singly(self) -> None:
        result = await missing_field_node(_state(_resolved(title="Already Trading")))
        assert result["response_ui"]["field"] == "actual_shop_handover_date"
        assert "fields" not in result["response_ui"]


class TestGroupAsk:
    @pytest.mark.asyncio
    async def test_one_question_covers_all_four(self, short_flow_on: None) -> None:
        result = await missing_field_node(_state(_resolved()))

        message = result["response_message"]
        assert "The dates so far" in message
        assert "Shop handover" in message and "Trading" in message
        assert result["response_ui"]["fields"] == list(HANDED_OVER_FIELD_GROUP)
        assert result["active_field"] == "actual_shop_handover_date"

    @pytest.mark.asyncio
    async def test_offers_no_suggestions(self, short_flow_on: None) -> None:
        """Only the Mall Manager knows what already happened."""
        result = await missing_field_node(_state(_resolved()))
        assert "response_decorations" not in result

    @pytest.mark.asyncio
    async def test_shows_the_expected_order(self, short_flow_on: None) -> None:
        """The user types four dates in a row — the order has to be stated."""
        message = (await missing_field_node(_state(_resolved())))["response_message"]
        assert "20/08/2026, 27/08/2026, 30/09/2026, 10/10/2026" in message


class TestNarrowing:
    @pytest.mark.asyncio
    async def test_two_given_asks_for_the_rest(self, short_flow_on: None) -> None:
        result = await missing_field_node(
            _state(
                _resolved(
                    actual_shop_handover_date="2026-08-20",
                    actual_fitout_start_date="2026-08-27",
                )
            )
        )
        assert result["response_ui"]["fields"] == [
            "actual_fitout_end_date",
            "actual_trading_date",
        ]
        message = result["response_message"]
        assert "handover **20 Aug 2026**" in message
        # The example lists what is still wanted, not a fixed four.
        assert "'30/09/2026, 10/10/2026'" in message

    @pytest.mark.asyncio
    async def test_one_left_asks_for_it_alone(self, short_flow_on: None) -> None:
        result = await missing_field_node(
            _state(
                _resolved(
                    actual_shop_handover_date="2026-08-20",
                    actual_fitout_start_date="2026-08-27",
                    actual_fitout_end_date="2026-09-30",
                )
            )
        )
        assert result["response_ui"]["fields"] == ["actual_trading_date"]
        assert "When did the store open for business?" in result["response_message"]
        assert "DD/MM/YYYY" in result["response_message"]

    @pytest.mark.asyncio
    async def test_all_four_given_stops_asking(self, short_flow_on: None) -> None:
        result = await missing_field_node(
            _state(
                _resolved(
                    actual_shop_handover_date="2026-08-20",
                    actual_fitout_start_date="2026-08-27",
                    actual_fitout_end_date="2026-09-30",
                    actual_trading_date="2026-10-10",
                    title="Already Trading - t0112136",
                    comments="None.",
                )
            )
        )
        assert result["status"] == "IN_PROGRESS"

class TestReviewCard:
    """The card is where a mistyped date gets fixed."""

    @staticmethod
    def _rows(short_flow: bool = True) -> dict[str, Any]:
        from app.agents.graph.nodes.handover.confirmation_node import (
            _build_confirmation_card,
        )

        collected = _resolved(
            title="Already Trading - t0112136",
            actual_shop_handover_date="2026-08-20",
            actual_fitout_start_date="2026-08-27",
            actual_fitout_end_date="2026-09-30",
            actual_trading_date="2026-10-10",
            comments="Opened on schedule.",
        )
        card = _build_confirmation_card(collected, "CREATE_HANDED_OVER_SR", None)
        return {f["key"]: f for f in card["fields"]}

    def test_every_supplied_date_is_editable(self, short_flow_on: None) -> None:
        rows = self._rows()
        for key in HANDED_OVER_FIELD_GROUP:
            assert rows[key]["editable"] is True, key

    def test_the_dates_get_pickers_and_raw_values(self, short_flow_on: None) -> None:
        rows = self._rows()
        assert rows["actual_trading_date"]["inputType"] == "date"
        # A picker round-trips the raw value, not a display string.
        assert rows["actual_trading_date"]["value"] == "2026-10-10"

    def test_no_clock_rows_on_this_path(self, short_flow_on: None) -> None:
        """Path B's dates carry no time — that is Path A's inspection window."""
        rows = self._rows()
        assert "inspectionTime" not in rows
        assert "inspectionEndTime" not in rows

    def test_the_lease_stays_read_only(self, short_flow_on: None) -> None:
        assert self._rows()["lease_code"]["editable"] is False

    def test_the_dates_are_editable_in_the_long_flow_too(self) -> None:
        """Read-only actual dates were a gap on this card, not a flag choice.

        Only the picker rides with the short flow; without it the row is a
        text field, which the client shows as DD/MM/YYYY like every other
        editable date.
        """
        rows = self._rows()
        assert rows["actual_trading_date"]["editable"] is True
        assert "inputType" not in rows["actual_trading_date"]
