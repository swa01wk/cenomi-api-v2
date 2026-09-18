"""Period helpers for sales-data CSV vs the month the tenant asked to upload."""

from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any

_MONTH_MAP = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4,
    "jun": 6, "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

_QUARTER_MAP = {
    "q1": (1, 3), "q2": (4, 6), "q3": (7, 9), "q4": (10, 12),
}

_MONTH_LABELS = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)


def _month_end(year: int, month: int) -> date:
    if month == 12:
        return date(year, 12, 31)
    return date(year, month + 1, 1) - timedelta(days=1)


def resolve_period(hint: str) -> tuple[str | None, str | None]:
    """Convert a period hint to (start_date, end_date) in YYYY-MM-DD."""
    hint_lower = (hint or "").lower().strip()
    if not hint_lower:
        return None, None
    today = date.today()
    year = today.year

    year_match = re.search(r"\b(20\d{2})\b", hint_lower)
    if year_match:
        year = int(year_match.group(1))

    quarter_match = re.search(r"\bq([1-4])\b", hint_lower)
    if quarter_match:
        q = f"q{quarter_match.group(1)}"
        start_month, end_month = _QUARTER_MAP[q]
        start = date(year, start_month, 1)
        return start.isoformat(), _month_end(year, end_month).isoformat()

    # Longest names first so "august" wins over "aug".
    for month_name, month_num in sorted(_MONTH_MAP.items(), key=lambda x: -len(x[0])):
        if len(month_name) <= 3 and not year_match:
            continue
        # "may" is also an English verb — only treat it as a month with a year.
        if month_name == "may" and not year_match:
            continue
        if re.search(r"\b" + re.escape(month_name) + r"\b", hint_lower):
            start = date(year, month_num, 1)
            return start.isoformat(), _month_end(year, month_num).isoformat()

    date_range_match = re.search(
        r"(\d{4}-\d{2}-\d{2})\s*(?:to|-|through)\s*(\d{4}-\d{2}-\d{2})",
        hint_lower,
    )
    if date_range_match:
        return date_range_match.group(1), date_range_match.group(2)

    return None, None


def year_month_from_start(start: str) -> str | None:
    raw = (start or "").strip()
    if len(raw) >= 7 and raw[4] == "-":
        return raw[:7]
    return None


def format_year_month(year_month: str) -> str:
    try:
        year, month = year_month.split("-")
        return f"{_MONTH_LABELS[int(month) - 1]} {year}"
    except (ValueError, IndexError):
        return year_month


def parse_asked_period(text: str) -> dict[str, str] | None:
    """Extract an explicit asked month from free text, if any.

    Quarters are ignored here — a Q3 ask vs a September CSV is not a mismatch.
    """
    hint_lower = (text or "").lower()
    if re.search(r"\bq[1-4]\b", hint_lower):
        return None
    start, end = resolve_period(text or "")
    if not start:
        return None
    year_month = year_month_from_start(start)
    if not year_month:
        return None
    return {
        "label": format_year_month(year_month),
        "year_month": year_month,
        "start": start,
        "end": end or start,
    }


def csv_year_months(rows: list[dict[str, Any]]) -> list[str]:
    seen: list[str] = []
    for row in rows:
        ym = year_month_from_start(str(row.get("sales_period_start") or ""))
        if ym and ym not in seen:
            seen.append(ym)
    return seen


def asked_period_from_state(state: dict[str, Any]) -> dict[str, str] | None:
    """Resolve the month the tenant asked to upload, across turns.

    ``backend_refs`` is not persisted for sales-data sessions, so we also
    look at ``collected_data._asked_period`` and user messages in history.
    The most recent explicit month wins.
    """
    refs = state.get("backend_refs") or {}
    asked = refs.get("asked_period") if isinstance(refs, dict) else None
    if isinstance(asked, dict) and asked.get("year_month"):
        return asked  # type: ignore[return-value]

    collected = state.get("collected_data") or {}
    asked = collected.get("_asked_period") if isinstance(collected, dict) else None
    if isinstance(asked, dict) and asked.get("year_month"):
        return asked  # type: ignore[return-value]

    found: dict[str, str] | None = None
    for turn in state.get("conversation_history") or []:
        if not isinstance(turn, dict) or turn.get("role") != "user":
            continue
        parsed = parse_asked_period(str(turn.get("content") or ""))
        if parsed:
            found = parsed
    parsed = parse_asked_period(str(state.get("user_message") or ""))
    if parsed:
        found = parsed
    return found


def period_mismatch_warning(
    asked: dict[str, Any] | None,
    csv_rows: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """If the tenant named a month and the CSV is a different month, return a warning payload."""
    if not asked or not isinstance(asked, dict):
        return None
    asked_ym = str(asked.get("year_month") or "")
    if not asked_ym:
        return None
    file_months = csv_year_months(csv_rows)
    if not file_months:
        return None
    if all(ym == asked_ym for ym in file_months):
        return None

    asked_label = str(asked.get("label") or format_year_month(asked_ym))
    file_label = " and ".join(format_year_month(ym) for ym in file_months)
    includes_asked = asked_ym in file_months
    extra_labels = [
        format_year_month(ym) for ym in file_months if ym != asked_ym
    ]
    extra_label = " and ".join(extra_labels) or file_label
    if includes_asked:
        message = (
            f"This file includes **{asked_label}** and also **{extra_label}**.\n\n"
            "That's fine — extra PENDING months can go in the same sheet. "
            "Do you want to submit this file?"
        )
    else:
        message = (
            f"The file you uploaded contains sales data for **{file_label}**. "
            f"You asked to upload **{asked_label}** data.\n\n"
            "Do you still want to proceed with this file?"
        )
    return {
        "asked_label": asked_label,
        "file_label": file_label,
        "asked_year_month": asked_ym,
        "file_year_months": file_months,
        "includes_asked_month": includes_asked,
        "message": message,
    }
