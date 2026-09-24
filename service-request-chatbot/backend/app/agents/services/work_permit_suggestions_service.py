"""Quick-pick chips for Work Permit create questions.

Mirrors ``inspection_suggestions_service``: chips use
``response_decorations.suggestions.kind = labeled_options``.

* Subtype / department chips carry ``action`` so ``work_permit_entry_node``
  can write the value directly (no LLM round-trip).
* Planned-date presets and measure-of-safety chips send JSON / labels as the
  chip ``value`` text — extraction + coerce already accept those shapes.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.agents.schemas.work_permit_form_fields import (
    EMPTY_MEASURE_OF_SAFETY,
    WP_DEPARTMENT_OPTIONS,
    WP_MEASURE_OF_SAFETY_LABELS,
    WP_TYPEOFWORK_OPTIONS,
)
from app.agents.schemas.work_permit_schema import (
    VALID_WORK_PERMIT_TYPES,
    WORK_PERMIT_TYPE_LABELS,
)

_PORTAL_TZ = ZoneInfo("Asia/Riyadh")

# (start_offset_days, end_offset_days) from today for planneddate presets.
_PLANNED_WINDOW_OFFSETS: tuple[tuple[int, int], ...] = ((0, 0), (1, 1), (0, 4))
_DEFAULT_FROM = "09:00"
_DEFAULT_TO = "17:00"


def today_in_portal_tz() -> date:
    return datetime.now(_PORTAL_TZ).date()


def _ddmmyyyy(d: date) -> str:
    return d.strftime("%d/%m/%Y")


def _short_label(d: date) -> str:
    return f"{d.day} {d.strftime('%b')}"


def build_wp_type_options() -> list[dict[str, Any]]:
    options: list[dict[str, Any]] = []
    for code in sorted(VALID_WORK_PERMIT_TYPES):
        options.append(
            {
                "label": WORK_PERMIT_TYPE_LABELS.get(code, code),
                "value": code,
                "action": f"select_wp_type_{code.lower()}",
            }
        )
    return options


def build_department_options() -> list[dict[str, Any]]:
    return [
        {
            "label": label,
            "value": value,
            "action": f"select_wp_department_{value}",
        }
        for label, value in WP_DEPARTMENT_OPTIONS
    ]


# Common unit-type chips for Ops (and other) contact groups that ask unit_type.
_UNIT_TYPE_CHIP_LABELS: tuple[str, ...] = ("Retail", "F&B", "Store")


def build_unit_type_options() -> list[dict[str, Any]]:
    return [
        {"label": label, "value": label} for label in _UNIT_TYPE_CHIP_LABELS
    ]


def build_typeofwork_options() -> list[dict[str, Any]]:
    """Ops Type of Work chips — values are portal checkbox keys."""
    return [
        {"label": label, "value": key} for label, key in WP_TYPEOFWORK_OPTIONS
    ]


def build_planneddate_preset(
    start: date,
    end: date,
    *,
    time_from: str = _DEFAULT_FROM,
    time_to: str = _DEFAULT_TO,
) -> dict[str, str]:
    return {
        "worktimedate": _ddmmyyyy(start),
        "completionworkdate": _ddmmyyyy(end),
        "worktimefrom": time_from,
        "worktimeto": time_to,
        "completionfrom": "",
        "completionto": "",
    }


def build_planneddate_options(today: date | None = None) -> list[dict[str, Any]]:
    today = today or today_in_portal_tz()
    options: list[dict[str, Any]] = []
    labels = ("Today 9–17", "Tomorrow 9–17", "This week (Mon window) 9–17")
    for idx, (start_off, end_off) in enumerate(_PLANNED_WINDOW_OFFSETS):
        start = today + timedelta(days=start_off)
        end = today + timedelta(days=end_off)
        planned = build_planneddate_preset(start, end)
        if idx == 2:
            # Prefer a Mon–Fri style label when the window spans days.
            label = f"{_short_label(start)}–{_short_label(end)} · 9–17"
        else:
            label = labels[idx]
        options.append(
            {
                "label": label,
                "value": json.dumps(planned),
            }
        )
    return options


def build_measureofsafety_options(
    collected: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    current = collected.get("measureofsafety") if collected else None
    if not isinstance(current, dict):
        current = {}
    options: list[dict[str, Any]] = []
    # Bulk: all keys set to yes.
    all_yes = {k: "yes" for k in EMPTY_MEASURE_OF_SAFETY}
    options.append(
        {
            "label": "All common safety measures",
            "value": json.dumps(all_yes),
        }
    )
    for key, label in WP_MEASURE_OF_SAFETY_LABELS.items():
        if str(current.get(key) or "").strip():
            continue
        single = dict(EMPTY_MEASURE_OF_SAFETY)
        single.update({k: str(v) for k, v in current.items() if v})
        single[key] = "yes"
        options.append({"label": label, "value": json.dumps(single)})
    return options


def build_wp_decorations(
    *,
    fields: list[str] | tuple[str, ...],
    collected: dict[str, Any] | None = None,
    today: date | None = None,
) -> dict[str, Any] | None:
    """Decorations for the current ask (single field or group)."""
    field_set = set(fields)
    options: list[dict[str, Any]] = []

    if field_set == {"work_permit_type"}:
        options = build_wp_type_options()
    elif "department" in field_set:
        options.extend(build_department_options())
    elif "unit_type" in field_set:
        # Ops contact remnant is often unit_type + phone with no other chips.
        options.extend(build_unit_type_options())
    elif "typeofwork" in field_set:
        options.extend(build_typeofwork_options())
    elif field_set == {"planneddate"}:
        options = build_planneddate_options(today)
    elif field_set == {"measureofsafety"}:
        options = build_measureofsafety_options(collected)

    if not options:
        return None
    return {"suggestions": {"kind": "labeled_options", "options": options}}
