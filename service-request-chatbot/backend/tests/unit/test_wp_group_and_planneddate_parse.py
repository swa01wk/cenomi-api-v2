"""WP group labeled replies + date-only planneddate extraction."""

from __future__ import annotations

import json
from datetime import date
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.graph.nodes.shared.field_extraction_node import (
    _extract_effective_starting,
    _parse_labeled_group_fields,
    field_extraction_node,
)
from app.agents.graph.nodes.shared.missing_field_node import _build_wp_singleton_ask
from app.agents.schemas.work_permit_form_fields import is_tenant_field_missing


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


@pytest.mark.asyncio
async def test_labeled_work_details_group_splits_three_fields() -> None:
    message = (
        "Type of work - Test work\n"
        "Equipments- hammer\n"
        "work info - hit with hammer"
    )
    out = await _run_extraction(
        {
            "user_message": message,
            "workflow_stage": "CREATE_WORK_PERMIT",
            "active_agent": "work_permit_agent",
            "active_field": "type_work",
            "missing_fields": ["type_work", "list_equipment", "work_info"],
            "response_ui": {
                "type": "text_question",
                "field": "type_work",
                "fields": ["type_work", "list_equipment", "work_info"],
            },
            "collected_data": {
                "work_permit_type": "CONSTRUCTION_COLD",
                "_wp_type_confirmed": True,
            },
            "conversation_history": [],
        }
    )
    extracted = out["extracted_fields"]
    assert _parse_extracted_value(extracted["type_work"]) == "Test work"
    assert _parse_extracted_value(extracted["list_equipment"]) == "hammer"
    assert _parse_extracted_value(extracted["work_info"]) == "hit with hammer"
    assert "Equipments" not in str(_parse_extracted_value(extracted["type_work"]))


@pytest.mark.asyncio
async def test_singleton_type_work_still_gets_plain_reply() -> None:
    out = await _run_extraction(
        {
            "user_message": "Partition install only",
            "workflow_stage": "CREATE_WORK_PERMIT",
            "active_agent": "work_permit_agent",
            "active_field": "type_work",
            "missing_fields": ["type_work"],
            "response_ui": {"type": "text_question", "field": "type_work"},
            "collected_data": {"work_permit_type": "CONSTRUCTION_COLD"},
            "conversation_history": [],
        }
    )
    assert _parse_extracted_value(out["extracted_fields"]["type_work"]) == (
        "Partition install only"
    )


@pytest.mark.asyncio
async def test_planneddate_accepts_date_only_then_times() -> None:
    out1 = await _run_extraction(
        {
            "user_message": "24 to 26 sep",
            "workflow_stage": "CREATE_WORK_PERMIT",
            "active_agent": "work_permit_agent",
            "active_field": "planneddate",
            "missing_fields": ["planneddate"],
            "response_ui": {"type": "text_question", "field": "planneddate"},
            "collected_data": {
                "work_permit_type": "CONSTRUCTION_COLD",
                "_wp_type_confirmed": True,
            },
            "conversation_history": [],
        }
    )
    planned1 = _parse_extracted_value(out1["extracted_fields"]["planneddate"])
    assert planned1["worktimedate"] == "24/09/2026"
    assert planned1["completionworkdate"] == "26/09/2026"
    assert planned1["worktimefrom"] == ""
    assert planned1["worktimeto"] == ""
    assert is_tenant_field_missing("planneddate", planned1, field_type="planneddate")

    ask = _build_wp_singleton_ask(
        "planneddate",
        "fallback",
        ["planneddate"],
        {"planneddate": planned1},
    )
    assert "First period times" in ask["response_message"]
    assert "from date" not in ask["response_message"].lower()

    out2 = await _run_extraction(
        {
            "user_message": "9am to 5pm",
            "workflow_stage": "CREATE_WORK_PERMIT",
            "active_agent": "work_permit_agent",
            "active_field": "planneddate",
            "missing_fields": ["planneddate"],
            "response_ui": {"type": "text_question", "field": "planneddate"},
            "collected_data": {
                "work_permit_type": "CONSTRUCTION_COLD",
                "_wp_type_confirmed": True,
                "planneddate": planned1,
            },
            "conversation_history": [],
        }
    )
    planned2 = _parse_extracted_value(out2["extracted_fields"]["planneddate"])
    assert planned2["worktimedate"] == planned1["worktimedate"]
    assert planned2["worktimefrom"] == "09:00"
    assert planned2["worktimeto"] == "17:00"
    assert not is_tenant_field_missing(
        "planneddate", planned2, field_type="planneddate"
    )


@pytest.mark.asyncio
async def test_planneddate_24sep_glued_month() -> None:
    out = await _run_extraction(
        {
            "user_message": "24sep to 26 sep",
            "workflow_stage": "CREATE_WORK_PERMIT",
            "active_agent": "work_permit_agent",
            "active_field": "planneddate",
            "missing_fields": ["planneddate"],
            "response_ui": {"type": "text_question", "field": "planneddate"},
            "collected_data": {"work_permit_type": "MAINTENANCE_COLD"},
            "conversation_history": [],
        }
    )
    planned = _parse_extracted_value(out["extracted_fields"]["planneddate"])
    assert planned["worktimedate"] == "24/09/2026"
    assert planned["completionworkdate"] == "26/09/2026"


def test_parse_labeled_aliases() -> None:
    parsed = _parse_labeled_group_fields(
        "Equipments- drill\nwork information: night",
        {"list_equipment", "work_info"},
    )
    assert parsed["list_equipment"] == "drill"
    assert parsed["work_info"] == "night"
