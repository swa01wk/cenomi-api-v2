"""Unit tests — date and time handling (Fix 2).

Tests cover:
- Date-only extraction (YYYY-MM-DD stored correctly)
- Explicit inspectionTime extraction (HH:MM preserved)
- Date-only update preserves stored inspectionTime
- Time-only update preserves stored date (via merge)
- Payload DD/MM/YYYY HH:MM format when inspectionTime present
- Default time used when inspectionTime absent
- _to_date_only does NOT truncate HH:MM (time fields not in _USER_DATE_FIELDS)
"""

from __future__ import annotations

import re
from typing import Any
from unittest.mock import patch

import pytest

from app.agents.graph.nodes.shared.merge_state_node import merge_state_node
from app.agents.services.payload_builder_service import (
    _normalize_inspection_datetime,
    build_create_handover_payload,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _state(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "session_id": "sess-dt",
        "user_id": "user-dt",
        "collected_data": {},
        "extracted_fields": {},
        "workflow_stage": "CREATE_SR",
        "user_message": "",
    }
    base.update(overrides)
    return base


def _ext(value: str, confidence: float = 0.95) -> dict[str, Any]:
    return {"value": value, "confidence": confidence}


_REQUIRED_DATA: dict[str, Any] = {
    "mall": "Riyadh Park",
    "brand": "Nike",
    "lease_code": "LC-001",
    "title": "handover-LC-001",
    "endDate": "2026-08-05",
    "startDate": "2026-08-01",
    "description": "Standard inspection",
    "inspection_done_by": "FM_MANAGER",
    "lease_brand_mall": "Nike-Riyadh Park",
    "unit_codes": ["GF101"],
    "contracted_area": "100",
    "city": "Riyadh",
    "brand_id": "B1",
    "tenant_profile_id": "T1",
    "contract_id": "C1",
    "property_id": "P1",
    "lease_id": "L1",
}


# ---------------------------------------------------------------------------
# merge_state_node tests
# ---------------------------------------------------------------------------


class TestDateOnlyExtraction:
    @pytest.mark.asyncio
    async def test_start_date_stored_as_iso(self) -> None:
        state = _state(extracted_fields={"startDate": _ext("2026-08-01")})
        result = await merge_state_node(state)
        assert result["collected_data"]["startDate"] == "2026-08-01"

    @pytest.mark.asyncio
    async def test_inspection_time_preserved_as_hhmm(self) -> None:
        state = _state(extracted_fields={"inspectionTime": _ext("14:00")})
        result = await merge_state_node(state)
        assert result["collected_data"]["inspectionTime"] == "14:00"

    @pytest.mark.asyncio
    async def test_inspection_time_not_truncated_by_to_date_only(self) -> None:
        """inspectionTime is not in _USER_DATE_FIELDS — must pass through untouched."""
        state = _state(extracted_fields={"inspectionTime": _ext("09:30")})
        result = await merge_state_node(state)
        assert result["collected_data"]["inspectionTime"] == "09:30"


class TestDateUpdatePreservesTime:
    @pytest.mark.asyncio
    async def test_date_update_does_not_wipe_inspection_time(self) -> None:
        """Updating startDate must not clear a previously stored inspectionTime."""
        state = _state(
            extracted_fields={"startDate": _ext("2026-08-10")},
            collected_data={"inspectionTime": "10:30", "startDate": "2026-08-01"},
        )
        result = await merge_state_node(state)
        cd = result["collected_data"]
        assert cd["startDate"] == "2026-08-10"
        assert cd["inspectionTime"] == "10:30"

    @pytest.mark.asyncio
    async def test_time_update_does_not_wipe_start_date(self) -> None:
        """Updating inspectionTime must not clear startDate."""
        state = _state(
            extracted_fields={"inspectionTime": _ext("15:00")},
            collected_data={"startDate": "2026-08-01", "inspectionTime": "09:00"},
        )
        result = await merge_state_node(state)
        cd = result["collected_data"]
        assert cd["inspectionTime"] == "15:00"
        assert cd["startDate"] == "2026-08-01"


# ---------------------------------------------------------------------------
# _normalize_inspection_datetime tests
# ---------------------------------------------------------------------------


class TestNormalizeInspectionDatetime:
    def test_date_only_uses_inspection_time(self) -> None:
        utc_iso, lt_display = _normalize_inspection_datetime(
            "2026-08-01", inspection_time="14:00"
        )
        assert "14:00" in lt_display or "2:00 PM" in lt_display
        assert "2026" in utc_iso

    def test_date_only_with_no_inspection_time_defaults_to_hour(self) -> None:
        utc_iso, lt_display = _normalize_inspection_datetime("2026-08-01")
        # Must produce a valid UTC ISO string
        assert re.match(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z", utc_iso)

    def test_explicit_time_overrides_existing_lt(self) -> None:
        """inspectionTime must take precedence over an existing LT display string."""
        existing_lt = "01/08/2026 9:00 AM"
        utc_iso, lt_display = _normalize_inspection_datetime(
            "2026-08-01", existing_lt=existing_lt, inspection_time="14:00"
        )
        assert "14:00" in lt_display or "2:00 PM" in lt_display


# ---------------------------------------------------------------------------
# build_create_handover_payload tests
# ---------------------------------------------------------------------------


class TestPayloadBuilderWithInspectionTime:
    def test_inspection_time_applied_to_start_date(self) -> None:
        data = dict(_REQUIRED_DATA)
        data["inspectionTime"] = "10:30"
        payload = build_create_handover_payload(data)
        inner = payload["payload"]
        lt = inner.get("startDateLT", "")
        assert "10:30" in lt or "10:30 AM" in lt

    def test_default_time_used_when_inspection_time_absent(self) -> None:
        data = dict(_REQUIRED_DATA)
        # No inspectionTime in data
        payload = build_create_handover_payload(data)
        inner = payload["payload"]
        # Must produce a valid LT display string
        assert inner.get("startDateLT") != ""

    def test_start_and_end_use_same_inspection_time(self) -> None:
        data = dict(_REQUIRED_DATA)
        data["inspectionTime"] = "11:00"
        payload = build_create_handover_payload(data)
        inner = payload["payload"]
        start_lt = inner.get("startDateLT", "")
        end_lt = inner.get("endDateLT", "")
        # Both must contain the same time component
        assert "11:00" in start_lt or "11:00 AM" in start_lt
        assert "11:00" in end_lt or "11:00 AM" in end_lt

    def test_create_payload_utc_iso_matches_portal_format(self) -> None:
        """10:56 AM Riyadh → 07:56 UTC so the FE hydrates the real time, not 9 AM."""
        data = dict(_REQUIRED_DATA)
        data["startDate"] = "2026-08-13"
        data["endDate"] = "2026-08-13"
        data["inspectionTime"] = "10:56"
        payload = build_create_handover_payload(data)
        inner = payload["payload"]
        assert inner["startDate"] == "2026-08-13T07:56:00.000Z"
        assert inner["endDate"] == "2026-08-13T07:56:00.000Z"
        assert inner["startDateLT"] == "13/08/2026 10:56 AM"
        assert inner["endDateLT"] == "13/08/2026 10:56 AM"

    def test_end_lt_time_is_independent_of_start(self) -> None:
        data = dict(_REQUIRED_DATA)
        data["startDate"] = "2026-08-20"
        data["endDate"] = "2026-08-21"
        data["inspectionTime"] = "14:00"
        data["startDateLT"] = "20/08/2026 2:00 PM"
        data["endDateLT"] = "21/08/2026 6:00 PM"
        inner = build_create_handover_payload(data)["payload"]
        assert inner["startDate"] == "2026-08-20T11:00:00.000Z"
        assert inner["endDate"] == "2026-08-21T15:00:00.000Z"
        assert inner["startDateLT"] == "20/08/2026 2:00 PM"
        assert inner["endDateLT"] == "21/08/2026 6:00 PM"


# ---------------------------------------------------------------------------
# Time salvage / message parse / draft_preview
# ---------------------------------------------------------------------------


class TestInspectionTimeSalvage:
    @pytest.mark.asyncio
    async def test_iso_datetime_start_date_salvages_time(self) -> None:
        state = _state(
            extracted_fields={"startDate": _ext("2026-08-13T10:56:00")},
            user_message="inspection on 13/08/2026",
        )
        result = await merge_state_node(state)
        cd = result["collected_data"]
        assert cd["startDate"] == "2026-08-13"
        assert cd["inspectionTime"] == "10:56"

    @pytest.mark.asyncio
    async def test_utc_iso_start_date_converts_to_riyadh_hhmm(self) -> None:
        state = _state(
            extracted_fields={"startDate": _ext("2026-08-13T07:56:00.000Z")},
            user_message="13 August",
        )
        result = await merge_state_node(state)
        assert result["collected_data"]["inspectionTime"] == "10:56"

    @pytest.mark.asyncio
    async def test_message_time_wins_over_llm_nine_am_default(self) -> None:
        state = _state(
            extracted_fields={
                "startDate": _ext("2026-08-13"),
                "inspectionTime": _ext("09:00"),
            },
            user_message="13/08/2026 10:56 AM",
        )
        result = await merge_state_node(state)
        cd = result["collected_data"]
        assert cd["startDate"] == "2026-08-13"
        assert cd["inspectionTime"] == "10:56"
        assert cd["startDateLT"] == "13/08/2026 10:56 AM"

    @pytest.mark.asyncio
    async def test_two_pm_in_message_stored_as_14_00(self) -> None:
        state = _state(
            extracted_fields={"startDate": _ext("2026-08-13")},
            user_message="please set inspect date 13 Aug 2:00 PM",
        )
        result = await merge_state_node(state)
        assert result["collected_data"]["inspectionTime"] == "14:00"

    @pytest.mark.asyncio
    async def test_end_date_time_does_not_overwrite_start_time(self) -> None:
        first = _state(
            extracted_fields={"startDate": _ext("2026-08-20")},
            user_message="20 Aug 2:00 PM",
            active_field="startDate",
        )
        after_start = await merge_state_node(first)
        cd = after_start["collected_data"]
        assert cd["inspectionTime"] == "14:00"
        assert "2:00 PM" in cd["startDateLT"]

        second = _state(
            extracted_fields={
                "endDate": _ext("2026-08-21"),
                "inspectionTime": _ext("18:00"),
            },
            collected_data=cd,
            user_message="21/08/2026 6:00 PM",
            active_field="endDate",
        )
        after_end = await merge_state_node(second)
        out = after_end["collected_data"]
        assert out["startDate"] == "2026-08-20"
        assert out["endDate"] == "2026-08-21"
        assert out["inspectionTime"] == "14:00"
        assert out["startDateLT"] == "20/08/2026 2:00 PM"
        assert out["endDateLT"] == "21/08/2026 6:00 PM"


class TestDraftPreviewInspectionDatetime:
    def test_fields_emit_utc_iso_and_lt(self) -> None:
        from app.agents.graph.nodes.handover.preview_node import _fields_from_collected

        rows = _fields_from_collected(
            {
                "startDate": "2026-08-13",
                "endDate": "2026-08-13",
                "inspectionTime": "10:56",
            }
        )
        by_key = {row["key"]: row["value"] for row in rows}
        assert by_key["startDate"] == "2026-08-13T07:56:00.000Z"
        assert by_key["endDate"] == "2026-08-13T07:56:00.000Z"
        assert by_key["startDateLT"] == "13/08/2026 10:56 AM"
        assert by_key["endDateLT"] == "13/08/2026 10:56 AM"

    def test_end_keeps_own_time_in_preview(self) -> None:
        from app.agents.graph.nodes.handover.preview_node import _fields_from_collected

        rows = _fields_from_collected(
            {
                "startDate": "2026-08-20",
                "endDate": "2026-08-21",
                "inspectionTime": "14:00",
                "startDateLT": "20/08/2026 2:00 PM",
                "endDateLT": "21/08/2026 6:00 PM",
            }
        )
        by_key = {row["key"]: row["value"] for row in rows}
        assert by_key["startDate"] == "2026-08-20T11:00:00.000Z"
        assert by_key["endDate"] == "2026-08-21T15:00:00.000Z"
        assert by_key["startDateLT"] == "20/08/2026 2:00 PM"
        assert by_key["endDateLT"] == "21/08/2026 6:00 PM"
