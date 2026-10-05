"""Deterministic Ops schedule parsers (effective_starting + firstperiod)."""

from __future__ import annotations

import json
from datetime import date
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.graph.nodes.shared.field_extraction_node import (
    _extract_effective_starting,
    _extract_time_period,
    field_extraction_node,
)
from app.agents.graph.nodes.shared.merge_state_node import (
    _remap_extracted_to_active_field,
)


def _make_gateway_mock(response: dict) -> MagicMock:
    mock = MagicMock()
    mock.model = "gpt-4o-mini"
    mock.complete_json = AsyncMock(return_value=(response, 10, 5, 50))
    return mock


def _parse_extracted_value(entry: Any) -> Any:
    val = entry.get("value") if isinstance(entry, dict) else entry
    if isinstance(val, str) and val.strip().startswith("{"):
        return json.loads(val)
    return val


async def _run_extraction(state: dict[str, Any]) -> dict[str, Any]:
    empty = {"summary": "", "fields": {}}
    with (
        patch(
            "app.agents.graph.nodes.shared.field_extraction_node.get_default_gateway",
            return_value=_make_gateway_mock(empty),
        ),
        patch(
            "app.agents.graph.nodes.shared.field_extraction_node._extract_effective_starting",
            side_effect=lambda msg, today=None: _extract_effective_starting(
                msg, today=date(2026, 9, 1)
            ),
        ),
    ):
        return await field_extraction_node(state)


class TestExtractTimePeriod:
    def test_both_sides_ampm(self) -> None:
        assert _extract_time_period("4pm to 5pm") == {"from": "16:00", "to": "17:00"}

    def test_inherit_pm_from_right(self) -> None:
        assert _extract_time_period("2 to 3pm") == {"from": "14:00", "to": "15:00"}

    def test_inherit_pm_hyphen(self) -> None:
        assert _extract_time_period("2-3pm") == {"from": "14:00", "to": "15:00"}

    def test_inherit_am_from_left(self) -> None:
        assert _extract_time_period("9am to 11") == {"from": "09:00", "to": "11:00"}

    def test_ignores_day_range_without_ampm(self) -> None:
        assert _extract_time_period("23 to 24 september") is None


class TestExtractEffectiveStarting:
    def test_day_month_range(self) -> None:
        got = _extract_effective_starting(
            "23 to 24 september", today=date(2026, 9, 1)
        )
        assert got == {"datefrom": "23/09/2026", "dateto": "24/09/2026"}

    def test_day_month_range_with_year(self) -> None:
        got = _extract_effective_starting(
            "23-24 september 2027", today=date(2026, 1, 1)
        )
        assert got == {"datefrom": "23/09/2027", "dateto": "24/09/2027"}

    def test_slash_range(self) -> None:
        got = _extract_effective_starting(
            "23/09 to 24/09", today=date(2026, 5, 1)
        )
        assert got == {"datefrom": "23/09/2026", "dateto": "24/09/2026"}

    def test_single_day(self) -> None:
        got = _extract_effective_starting("23 september", today=date(2026, 9, 1))
        assert got == {"datefrom": "23/09/2026", "dateto": "23/09/2026"}

    def test_ordinal_single_day(self) -> None:
        got = _extract_effective_starting("24th sep", today=date(2026, 9, 1))
        assert got == {"datefrom": "24/09/2026", "dateto": "24/09/2026"}

    def test_ordinal_september(self) -> None:
        got = _extract_effective_starting(
            "23rd september", today=date(2026, 9, 1)
        )
        assert got == {"datefrom": "23/09/2026", "dateto": "23/09/2026"}

    def test_ordinal_range(self) -> None:
        got = _extract_effective_starting(
            "23rd to 24th september", today=date(2026, 9, 1)
        )
        assert got == {"datefrom": "23/09/2026", "dateto": "24/09/2026"}

    def test_ordinal_with_time_still_parses_date(self) -> None:
        got = _extract_effective_starting(
            "24th sep 4 to 5 pm", today=date(2026, 9, 1)
        )
        assert got == {"datefrom": "24/09/2026", "dateto": "24/09/2026"}
        assert _extract_time_period("24th sep 4 to 5 pm") == {
            "from": "16:00",
            "to": "17:00",
        }


@pytest.mark.asyncio
class TestOpsScheduleRescue:
    async def test_combined_message_fills_both_when_llm_empty(self) -> None:
        state: dict[str, Any] = {
            "user_message": "23 to 24 september 2 to 3pm",
            "workflow_stage": "CREATE_WORK_PERMIT",
            "active_agent": "work_permit_agent",
            "active_field": "effective_starting",
            "missing_fields": ["effective_starting", "firstperiod"],
            "response_ui": {
                "type": "text_question",
                "field": "effective_starting",
                "fields": ["effective_starting", "firstperiod"],
            },
            "collected_data": {},
            "conversation_history": [],
            "session_id": "test",
        }
        result = await _run_extraction(state)

        extracted = result.get("extracted_fields") or {}
        assert _parse_extracted_value(extracted["effective_starting"]) == {
            "datefrom": "23/09/2026",
            "dateto": "24/09/2026",
        }
        assert _parse_extracted_value(extracted["firstperiod"]) == {
            "from": "14:00",
            "to": "15:00",
        }

    async def test_ordinal_combined_fills_both(self) -> None:
        state: dict[str, Any] = {
            "user_message": "24th sep 4 to 5 pm",
            "workflow_stage": "CREATE_WORK_PERMIT",
            "active_agent": "work_permit_agent",
            "active_field": "effective_starting",
            "missing_fields": ["effective_starting", "firstperiod"],
            "response_ui": {
                "type": "text_question",
                "field": "effective_starting",
                "fields": ["effective_starting", "firstperiod"],
            },
            "collected_data": {"work_permit_type": "Operations"},
            "conversation_history": [],
            "session_id": "test",
        }
        result = await _run_extraction(state)
        extracted = result.get("extracted_fields") or {}
        assert _parse_extracted_value(extracted["effective_starting"]) == {
            "datefrom": "24/09/2026",
            "dateto": "24/09/2026",
        }
        assert _parse_extracted_value(extracted["firstperiod"]) == {
            "from": "16:00",
            "to": "17:00",
        }

    async def test_time_only_with_stale_missing_ui(self) -> None:
        """load_session cleared missing/UI; only active_field remains — still fill period."""
        state: dict[str, Any] = {
            "user_message": "4 to 5 pm",
            "workflow_stage": "CREATE_WORK_PERMIT",
            "active_agent": "work_permit_agent",
            "active_field": "effective_starting",
            "missing_fields": [],
            "response_ui": {},
            "collected_data": {"work_permit_type": "Operations"},
            "conversation_history": [],
            "session_id": "test",
        }
        result = await _run_extraction(state)
        extracted = result.get("extracted_fields") or {}
        assert "effective_starting" not in extracted
        assert _parse_extracted_value(extracted["firstperiod"]) == {
            "from": "16:00",
            "to": "17:00",
        }

    async def test_date_only_with_stale_missing_ui(self) -> None:
        state: dict[str, Any] = {
            "user_message": "24th sep",
            "workflow_stage": "CREATE_WORK_PERMIT",
            "active_agent": "work_permit_agent",
            "active_field": "firstperiod",
            "missing_fields": [],
            "response_ui": {},
            "collected_data": {"work_permit_type": "Operations"},
            "conversation_history": [],
            "session_id": "test",
        }
        result = await _run_extraction(state)
        extracted = result.get("extracted_fields") or {}
        assert "firstperiod" not in extracted
        assert _parse_extracted_value(extracted["effective_starting"]) == {
            "datefrom": "24/09/2026",
            "dateto": "24/09/2026",
        }

    async def test_does_not_overwrite_complete_collected_period(self) -> None:
        state: dict[str, Any] = {
            "user_message": "skip",
            "workflow_stage": "CREATE_WORK_PERMIT",
            "active_agent": "work_permit_agent",
            "active_field": "effective_starting",
            "missing_fields": ["effective_starting"],
            "response_ui": {"fields": ["effective_starting"]},
            "collected_data": {
                "work_permit_type": "Operations",
                "firstperiod": {"from": "16:00", "to": "17:00"},
            },
            "conversation_history": [],
            "session_id": "test",
            # Pretend LLM returned an empty period stub.
        }
        empty = {
            "summary": "",
            "fields": {
                "firstperiod": {"value": {"from": "", "to": ""}, "confidence": 1.0},
            },
        }
        with (
            patch(
                "app.agents.graph.nodes.shared.field_extraction_node.get_default_gateway",
                return_value=_make_gateway_mock(empty),
            ),
            patch(
                "app.agents.graph.nodes.shared.field_extraction_node._extract_effective_starting",
                side_effect=lambda msg, today=None: _extract_effective_starting(
                    msg, today=date(2026, 9, 1)
                ),
            ),
        ):
            result = await field_extraction_node(state)
        extracted = result.get("extracted_fields") or {}
        assert "firstperiod" not in extracted


class TestScheduleRemapGuard:
    def test_lone_firstperiod_not_remapped_to_effective_starting(self) -> None:
        extracted = {
            "firstperiod": {
                "value": {"from": "16:00", "to": "17:00"},
                "confidence": 1.0,
            }
        }
        out = _remap_extracted_to_active_field(extracted, "effective_starting")
        assert "firstperiod" in out
        assert "effective_starting" not in out
        assert out["firstperiod"]["value"] == {"from": "16:00", "to": "17:00"}

    def test_lone_effective_starting_not_remapped_to_firstperiod(self) -> None:
        extracted = {
            "effective_starting": {
                "value": {"datefrom": "24/09/2026", "dateto": "24/09/2026"},
                "confidence": 1.0,
            }
        }
        out = _remap_extracted_to_active_field(extracted, "firstperiod")
        assert "effective_starting" in out
        assert "firstperiod" not in out
