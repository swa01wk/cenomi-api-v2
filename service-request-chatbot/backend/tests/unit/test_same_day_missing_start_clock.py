"""A start clock nobody set must not make a same-day window unwinnable.

Reported on the RDD review of an existing SR. The SR arrived with a same-day
inspection window and no start clock, and every end time the reviewer typed
came back refused:

    The inspection starts and ends on 24/06/2026, so the end time (6:00 PM)
    has to be later than the start time (not set).

There is no field at that stage to set a start time, so there was no way out.
The clock was never unknown, either: the payload builder books an unset start
at _DEFAULT_INSPECTION_HOUR, so validation was refusing a window that would
have submitted unchanged. It now judges the window that would actually be
booked.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.agents.services.payload_builder_service import _DEFAULT_INSPECTION_HOUR
from app.agents.services.validation_service import validate_start_end_date

_DAY = "2026-06-24"
_DEFAULT = f"{_DEFAULT_INSPECTION_HOUR:02d}:00"


def _check(start_time: Any, end_time: Any, end_date: str = _DAY) -> dict[str, Any]:
    return validate_start_end_date(_DAY, end_date, start_time, end_time)


class TestAMissingStartClockFallsBackToTheBooking:
    def test_an_end_time_after_the_default_passes(self) -> None:
        result = _check(None, "18:00")
        assert result["status"] == "PASSED"
        # The message states the time it assumed rather than showing "None".
        assert _DEFAULT in result["message"]

    def test_an_end_time_before_the_default_still_fails(self) -> None:
        # 08:00 really is before the 09:00 the SR would be booked at.
        result = _check(None, "08:00")
        assert result["status"] == "FAILED"
        assert _DEFAULT in result["message"]

    def test_an_end_time_equal_to_the_default_fails(self) -> None:
        assert _check(None, _DEFAULT)["status"] == "FAILED"


class TestNothingElseMoves:
    @pytest.mark.parametrize(
        ("start_time", "end_time", "expected"),
        [
            ("10:00", "22:00", "PASSED"),
            ("14:00", "09:00", "FAILED"),
            ("09:00", "09:00", "FAILED"),
        ],
    )
    def test_two_known_clocks_compare_as_before(
        self, start_time: str, end_time: str, expected: str
    ) -> None:
        assert _check(start_time, end_time)["status"] == expected

    def test_a_later_end_date_needs_no_clocks_at_all(self) -> None:
        assert _check(None, None, end_date="2026-06-25")["status"] == "PASSED"

    def test_neither_clock_set_is_still_refused(self) -> None:
        # Nothing separates the two dates, so the reviewer is asked for a time
        # — which they can now actually answer.
        assert _check(None, None)["status"] == "FAILED"
