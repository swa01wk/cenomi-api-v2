"""Reopening an SR for review must keep the times it was created with.

Reported on the FM approver side: an SR created with the same inspection start
and end (09/09/2026 7:27 PM) opened with "Review with AI", and the assistant
said both times were "not set" and refused to move on — while the form beside
it showed 7:27 PM in both fields.

Two things were wrong, and a third made it unwinnable.

* The platform stores the times only in the LT display strings;
  ``inspectionTime``/``inspectionEndTime`` come back null. The reopen mapping
  carried none of the four, so the review started with no clocks at all.
* The clocks must come from the LT strings, never from the UTC ``startDate``:
  the same 9:00 AM is stored as 03:30Z on one SR and 06:00Z on another, so
  re-deriving from UTC would display one time and submit a different one.
* And the start/end ordering is a create-time rule. A reviewer did not choose
  the window and has no field to change it, so enforcing it at review only
  blocks the approval.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.agents.services.review_decorations_service import payload_to_collected_data
from app.agents.services.validation_service import ValidationService


def _payload(**over: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "startDate": "2026-09-09T16:27:00.000Z",
        "endDate": "2026-09-09T16:27:00.000Z",
        "startDateLT": "09/09/2026 7:27 PM",
        "endDateLT": "09/09/2026 7:27 PM",
        "inspectionTime": None,
        "inspectionEndTime": None,
    }
    payload.update(over)
    return payload


class TestTheClocksComeBack:
    def test_the_times_are_recovered_from_the_display_strings(self) -> None:
        mapped = payload_to_collected_data(_payload())
        assert mapped["inspectionTime"] == "19:27"
        assert mapped["inspectionEndTime"] == "19:27"

    def test_the_local_time_wins_over_the_utc_field(self) -> None:
        # 06:00Z stored against a 9:00 AM local booking. Reading the UTC field
        # would show 06:00 and submit a time the user never chose.
        mapped = payload_to_collected_data(
            _payload(
                startDate="2026-09-11T06:00:00.000Z",
                startDateLT="11/09/2026 9:00 AM",
            )
        )
        assert mapped["inspectionTime"] == "09:00"

    def test_a_clock_the_platform_did_send_is_kept(self) -> None:
        mapped = payload_to_collected_data(_payload(inspectionTime="08:15"))
        assert mapped["inspectionTime"] == "08:15"

    def test_nothing_is_invented_without_a_display_string(self) -> None:
        mapped = payload_to_collected_data(
            {"startDate": "2026-09-11T06:00:00.000Z", "endDate": "2026-09-12T06:00:00.000Z"}
        )
        assert "inspectionTime" not in mapped


class TestTheWindowIsNotReLitigatedAtReview:
    def _blocking_range(self, stage: str) -> list[dict[str, Any]]:
        data = {
            **payload_to_collected_data(_payload()),
            "lease_code": "t0110565",
            "unit_readiness_date": "2026-09-20",
            "expected_handover_date": "2026-09-25",
        }
        errors = ValidationService().validate_draft(
            data=data, workflow_stage=stage, documents=[], role="FM_MANAGER"
        )
        return [
            e
            for e in errors
            if e.get("validation_type") == "date_range" and e.get("blocking")
        ]

    @pytest.mark.parametrize("stage", ["FM_REVIEW", "RDD_REVIEW", "APPROVE_SR"])
    def test_a_reviewer_is_not_blocked_by_the_creators_window(self, stage: str) -> None:
        assert self._blocking_range(stage) == []

    def test_creation_still_refuses_a_zero_length_window(self) -> None:
        assert len(self._blocking_range("CREATE_SR")) == 1
