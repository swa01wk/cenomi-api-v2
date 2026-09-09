"""The window the short flow fills for itself must be one it accepts.

Reported live on lease t0109844. The user set both inspection dates to
11/09/2026 and was told:

    The inspection starts and ends on 11/09/2026, so the end time (9:00 AM)
    has to be later than the start time (9:00 AM). Give a later time on the
    same day — e.g. '11/09/2026 6:00 PM' — or a later end date.

Neither clock was theirs. The short flow never asks for a time, so
``default_inspection_times`` wrote both, and it wrote the end as a copy of the
start — an inspection beginning and ending at the same instant, which
``validate_start_end_date`` refuses on a same-day window. Nothing they could
change about the dates cleared it, and the field it named was one they had
never been shown.

The default end clock is now a working day after the start.
"""

from __future__ import annotations

import pytest

from app.agents.services.sr_autofill_service import default_inspection_times
from app.agents.services.validation_service import validate_start_end_date

_SAME_DAY = "2026-09-11"


def _window(collected: dict[str, str]) -> dict[str, str]:
    return {**collected, **default_inspection_times(collected)}


def _passes(window: dict[str, str]) -> bool:
    result = validate_start_end_date(
        _SAME_DAY,
        _SAME_DAY,
        window.get("inspectionTime"),
        window.get("inspectionEndTime"),
    )
    return str(result.get("status")) == "PASSED"


class TestTheDefaultWindowIsValid:
    def test_a_wholly_unstated_window_is_a_working_day(self) -> None:
        assert default_inspection_times({}) == {
            "inspectionTime": "09:00",
            "inspectionEndTime": "17:00",
        }

    @pytest.mark.parametrize(
        "collected",
        [
            {},
            {"inspectionTime": "09:00"},
            {"inspectionTime": "14:30"},
            {"inspectionTime": "23:00"},
        ],
        ids=["nothing set", "09:00 start", "14:30 start", "late start"],
    )
    def test_a_same_day_inspection_passes_on_the_defaults(
        self, collected: dict[str, str]
    ) -> None:
        assert _passes(_window(collected)), _window(collected)

    def test_a_late_start_stays_on_the_same_day(self) -> None:
        # 20:00 + 8h would roll past midnight; the window must not silently
        # end on the following day, and must still end after it began.
        filled = default_inspection_times({"inspectionTime": "20:00"})
        assert filled["inspectionEndTime"] == "23:59"

    def test_minutes_are_kept(self) -> None:
        assert default_inspection_times({"inspectionTime": "10:45"}) == {
            "inspectionEndTime": "18:45"
        }


class TestWhatTheUserGaveIsLeftAlone:
    def test_both_clocks_given_fills_nothing(self) -> None:
        assert (
            default_inspection_times(
                {"inspectionTime": "09:00", "inspectionEndTime": "11:00"}
            )
            == {}
        )

    def test_an_end_the_user_gave_survives(self) -> None:
        # Even an end equal to the start is theirs to make; validation tells
        # them about it rather than the flow rewriting it behind their back.
        assert (
            default_inspection_times(
                {"inspectionTime": "09:00", "inspectionEndTime": "09:00"}
            )
            == {}
        )

    def test_an_unparseable_start_still_yields_a_later_end(self) -> None:
        filled = default_inspection_times({"inspectionTime": "not a clock"})
        assert filled["inspectionEndTime"] == "23:59"
