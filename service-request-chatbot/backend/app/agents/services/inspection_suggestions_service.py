"""Quick-pick chips for the combined inspection question (short flow).

The short flow asks for the inspection window and the inspector in a single
turn.  This module builds the chips that sit under that question and narrows
them as the user fills fields in: once a start date exists the window chips are
replaced by end-date chips anchored to it, and once the inspector is chosen its
chips disappear.

Window chips carry their value as text rather than a chat action.  Two reasons:
the chip's wording ("Start 07/09/2026, End 08/09/2026") then shows in the
transcript as the user's own answer, and no new action code has to be taught to
the frontend — the existing ``labeled_options`` renderer sends the value as a
plain message, which ``field_extraction`` already knows how to read as a packed
date pair.  The inspector chips keep the existing direct-set actions.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

# Wall-clock timezone the portal (and therefore the user) thinks in.
_PORTAL_TZ = ZoneInfo("Asia/Riyadh")

# Offsets in days from today for the two suggested inspection windows,
# as (start, end) pairs.  Both windows are one day long, the shape the
# Mall Managers book in practice.
WINDOW_OFFSETS: tuple[tuple[int, int], ...] = ((3, 4), (2, 3))

# Offsets in days from a known start date for the end-date-only re-ask.
END_ONLY_OFFSETS: tuple[int, ...] = (1, 2)

_DATE_INPUT_FORMATS: tuple[str, ...] = ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d")


def today_in_portal_tz() -> date:
    """Today's date as the user sees it (Asia/Riyadh wall clock)."""
    return datetime.now(_PORTAL_TZ).date()


def parse_date(raw: Any) -> date | None:
    """Best-effort parse of a stored date value; ``None`` when unusable."""
    text = str(raw or "").strip()
    if not text:
        return None
    if "T" in text:
        text = text.split("T", 1)[0]
    for fmt in _DATE_INPUT_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _chip_value(d: date) -> str:
    """DD/MM/YYYY — the format the extraction prompt is explicit about."""
    return d.strftime("%d/%m/%Y")


def _chip_label(d: date) -> str:
    """Short human label, e.g. ``7 Sep``."""
    return f"{d.day} {d.strftime('%b')}"


def build_inspection_options(
    collected: dict[str, Any],
    missing: set[str],
    today: date | None = None,
) -> list[dict[str, Any]]:
    """Return chip options for whichever inspection fields are still open.

    *missing* is the subset of ``INSPECTION_FIELD_GROUP`` that still needs an
    answer, so a partially answered group gets chips only for the remainder.
    """
    today = today or today_in_portal_tz()
    options: list[dict[str, Any]] = []

    start_known = parse_date(collected.get("startDate"))
    end_known = parse_date(collected.get("endDate"))

    if "startDate" in missing and "endDate" in missing:
        # Nothing booked yet — offer two complete windows.
        for start_offset, end_offset in WINDOW_OFFSETS:
            start = today + timedelta(days=start_offset)
            end = today + timedelta(days=end_offset)
            options.append(
                {
                    "label": f"{_chip_label(start)} → {_chip_label(end)}",
                    "value": f"Start {_chip_value(start)}, End {_chip_value(end)}",
                }
            )
    elif "endDate" in missing:
        # Anchor the end suggestions to the start the user already gave, so a
        # window booked far in the future does not get "tomorrow" offered as
        # its end date.
        anchor = start_known or today
        for offset in END_ONLY_OFFSETS:
            end = anchor + timedelta(days=offset)
            day_word = "day" if offset == 1 else "days"
            options.append(
                {
                    "label": f"{_chip_label(end)} (+{offset} {day_word})",
                    "value": f"End {_chip_value(end)}",
                }
            )
    elif "startDate" in missing and end_known:
        # Unusual order (end given first) — suggest starts that stay valid.
        for offset in END_ONLY_OFFSETS:
            start = end_known - timedelta(days=offset)
            options.append(
                {
                    "label": f"{_chip_label(start)}",
                    "value": f"Start {_chip_value(start)}",
                }
            )

    if "inspection_done_by" in missing:
        # Same values and actions as the single-field ask, so a click resolves
        # the field directly without an extraction round-trip.
        options.append(
            {
                "label": "FM Manager",
                "value": "FM_MANAGER",
                "action": "select_inspection_done_by_fm_manager",
            }
        )
        options.append(
            {
                "label": "Operations",
                "value": "OPERATIONS",
                "action": "select_inspection_done_by_operations",
            }
        )

    return options


def build_inspection_decorations(
    collected: dict[str, Any],
    missing: set[str],
    today: date | None = None,
) -> dict[str, Any]:
    """``response_decorations`` fragment for the combined inspection ask."""
    options = build_inspection_options(collected, missing, today=today)
    if not options:
        return {}
    return {"suggestions": {"kind": "labeled_options", "options": options}}
