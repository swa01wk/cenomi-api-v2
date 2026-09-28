"""Deterministic Work Permit review summary facts + natural wording.

Facts are chosen in code (business-critical). Wording is a small template
composer over those facts — not a field dump and not LLM-authored action lists.
"""

from __future__ import annotations

from typing import Any

from app.agents.schemas.work_permit_form_fields import (
    WP_TYPEOFWORK_OPTIONS,
    format_wp_field_display,
    summarize_effective_starting,
    summarize_period,
    summarize_typeofwork,
)
from app.agents.schemas.work_permit_schema import (
    STAGE_LABELS,
    WORK_PERMIT_TYPE_LABELS,
    resolve_wp_sub_category,
)

# Keys emphasized on the review card (who/where, work/when, people/safety, hop).
_EMPHASIS_KEYS: frozenset[str] = frozenset(
    {
        "raised_by",
        "mall",
        "brand",
        "unit_codes",
        "work_permit_type",
        "type_of_work",
        "work_location",
        "schedule",
        "contractor_name",
        "workers",
        "measureofsafety",
        "responsible_person",
        "current_hop",
    }
)

_TYPEOFWORK_LABEL_BY_KEY: dict[str, str] = {
    key: label for label, key in WP_TYPEOFWORK_OPTIONS
}


def _str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        parts = [str(v).strip() for v in value if str(v or "").strip()]
        return ", ".join(parts)
    return str(value).strip()


def _human_typeofwork(value: Any) -> str:
    """Ops checkbox keys → spoken labels."""
    if isinstance(value, dict):
        raw = value.get("value")
        other = _str(value.get("otheroption"))
        keys: list[str] = []
        if isinstance(raw, list):
            keys = [str(k).strip() for k in raw if str(k or "").strip()]
        elif raw:
            keys = [str(raw).strip()]
        labels = [_TYPEOFWORK_LABEL_BY_KEY.get(k, k) for k in keys]
        primary = ", ".join(labels)
        if primary and other:
            return f"{primary} ({other})"
        return primary or other or summarize_typeofwork(value)
    return summarize_typeofwork(value)


def _worker_count(value: Any) -> str:
    if isinstance(value, list):
        n = len([r for r in value if isinstance(r, dict)])
        return str(n) if n else ""
    if isinstance(value, dict):
        workers = value.get("worker")
        if isinstance(workers, list):
            return str(len(workers)) if workers else ""
    shown = format_wp_field_display("workers", value)
    return _str(shown)


def _schedule_text(collected: dict[str, Any], subtype: str) -> str:
    if subtype == "OPERATIONS":
        parts: list[str] = []
        effective = collected.get("effective_starting")
        if isinstance(effective, dict):
            parts.append(summarize_effective_starting(effective))
        for key in ("firstperiod", "secondperiod"):
            period = collected.get(key)
            if isinstance(period, dict):
                label = summarize_period(period)
                if label:
                    parts.append(label)
        return " · ".join(p for p in parts if p)
    planned = collected.get("planneddate")
    if isinstance(planned, dict):
        return _str(format_wp_field_display("planneddate", planned))
    return ""


def _type_of_work_text(collected: dict[str, Any], subtype: str) -> str:
    if subtype == "OPERATIONS":
        return _human_typeofwork(collected.get("typeofwork"))
    if subtype in {"CONSTRUCTION_COLD", "MAINTENANCE_COLD"}:
        return _str(collected.get("type_work"))
    # Hot / roof free text
    return _str(collected.get("work_type"))


def _raised_by(collected: dict[str, Any]) -> str:
    for key in (
        "raised_by",
        "created_by_name",
        "creator_name",
        "permission_given_to",
        "responsible_person",
        "owner",
        "trade_name",
        "contractor_name",
    ):
        val = _str(collected.get(key))
        if val:
            return val
    return ""


def build_wp_review_facts(
    collected: dict[str, Any],
    *,
    workflow_stage: str,
    subtype: str | None = None,
) -> list[dict[str, Any]]:
    """Short emphasized fact rows for the review card (not a full form dump)."""
    token = resolve_wp_sub_category(subtype, collected)
    permit_label = WORK_PERMIT_TYPE_LABELS.get(token, token.replace("_", " ").title())
    hop_label = STAGE_LABELS.get(workflow_stage, "Review")

    candidates: list[tuple[str, str, str]] = [
        ("raised_by", "Raised by", _raised_by(collected)),
        ("mall", "Mall", _str(collected.get("mall"))),
        ("brand", "Brand", _str(collected.get("brand"))),
        ("unit_codes", "Unit", _str(collected.get("unit_codes"))),
        ("work_permit_type", "Permit", permit_label),
        ("type_of_work", "Type of work", _type_of_work_text(collected, token)),
        ("work_location", "Location", _str(collected.get("work_location"))),
        ("schedule", "Schedule", _schedule_text(collected, token)),
        ("contractor_name", "Contractor", _str(collected.get("contractor_name"))),
        ("workers", "Workers", _worker_count(collected.get("workers"))),
        (
            "measureofsafety",
            "Safety",
            _str(format_wp_field_display("measureofsafety", collected.get("measureofsafety"))),
        ),
        (
            "responsible_person",
            "On site",
            _str(collected.get("responsible_person")),
        ),
        ("current_hop", "With you as", hop_label),
    ]

    fields: list[dict[str, Any]] = []
    for key, label, value in candidates:
        if not value:
            continue
        fields.append(
            {
                "key": key,
                "label": label,
                "value": value,
                "editable": False,
                "emphasis": key in _EMPHASIS_KEYS,
            }
        )
    return fields


def _schedule_parts(collected: dict[str, Any], subtype: str) -> tuple[str, str, str]:
    """``(date_from, date_to, times)`` — dates for the headline, times as detail."""
    if subtype == "OPERATIONS":
        effective = collected.get("effective_starting")
        date_from = date_to = ""
        if isinstance(effective, dict):
            date_from = _str(effective.get("datefrom"))
            date_to = _str(effective.get("dateto"))
        periods: list[str] = []
        for key in ("firstperiod", "secondperiod"):
            period = collected.get(key)
            if isinstance(period, dict):
                label = summarize_period(period)
                if label:
                    periods.append(label)
        return date_from, date_to, " · ".join(periods)

    planned = collected.get("planneddate")
    if not isinstance(planned, dict):
        return "", "", ""
    date_from = _str(planned.get("worktimedate"))
    date_to = _str(planned.get("completionworkdate"))
    windows: list[str] = []
    for from_keys, to_keys in (
        (("worktimefromLT", "worktimefrom"), ("worktimetoLT", "worktimeto")),
        (("completionfromLT", "completionfrom"), ("completiontoLT", "completionto")),
    ):
        t_from = next((_str(planned.get(k)) for k in from_keys if _str(planned.get(k))), "")
        t_to = next((_str(planned.get(k)) for k in to_keys if _str(planned.get(k))), "")
        if t_from and t_to:
            windows.append(f"{t_from} – {t_to}")
        elif t_from or t_to:
            windows.append(t_from or t_to)
    return date_from, date_to, " · ".join(windows)


def _date_range(date_from: str, date_to: str, sep: str) -> str:
    if date_from and date_to and date_from != date_to:
        return f"{date_from}{sep}{date_to}"
    return date_from or date_to


def build_wp_review_summary(
    collected: dict[str, Any],
    *,
    subtype: str | None = None,
) -> list[dict[str, Any]]:
    """Compact summary items (generic ``summary`` UI primitive) — empty rows skipped."""
    token = resolve_wp_sub_category(subtype, collected)
    date_from, date_to, times = _schedule_parts(collected, token)
    candidates: list[dict[str, Any]] = [
        {"key": "brand", "label": "Brand", "value": _str(collected.get("brand")), "icon": "store"},
        {"key": "mall", "label": "Mall", "value": _str(collected.get("mall")), "icon": "location"},
        {"key": "unit", "label": "Unit", "value": _str(collected.get("unit_codes")), "icon": "building"},
        {
            "key": "schedule",
            "label": "Schedule",
            "value": _date_range(date_from, date_to, " – "),
            "detail": times,
            "icon": "calendar",
        },
        {
            "key": "request_type",
            "label": "Permit / Request Type",
            "value": WORK_PERMIT_TYPE_LABELS.get(token, token.replace("_", " ").title()),
            "icon": "document",
        },
        {
            "key": "type_of_work",
            "label": "Type of work",
            "value": _type_of_work_text(collected, token),
            "icon": "tool",
        },
    ]
    items: list[dict[str, Any]] = []
    for item in candidates:
        if not item["value"]:
            continue
        if not item.get("detail"):
            item.pop("detail", None)
        items.append(item)
    return items


# Subtypes whose workflow has a single approval hop — the pending line names the
# permit ("the Operations review") rather than the hop ("your FM review").
_SINGLE_HOP_SUBTYPES: frozenset[str] = frozenset({"OPERATIONS"})


def _work_phrase(collected: dict[str, Any], subtype: str) -> str:
    """Object of "wants to carry out …": ``construction work`` / free-text work."""
    work = _type_of_work_text(collected, subtype)
    if not work:
        return ""
    if subtype == "OPERATIONS":
        labels = [p.strip().lower() for p in work.split(",") if p.strip()]
        joined = (
            labels[0]
            if len(labels) == 1
            else f"{', '.join(labels[:-1])} and {labels[-1]}"
        )
        return joined if joined.endswith("work") else f"{joined} work"
    return work[:1].lower() + work[1:] if work[1:2].islower() else work


def _article(word: str) -> str:
    return "an" if word[:1].lower() in "aeiou" else "a"


def compose_wp_review_message(
    collected: dict[str, Any],
    *,
    workflow_stage: str,
    subtype: str | None = None,
    first_name: str | None = None,
) -> str:
    """Greeting + one who/what/where/when sentence + the pending review line.

    Entities are wrapped in ``**`` so the chat bubble emphasises them; every
    clause degrades away when its fact is missing.
    """
    token = resolve_wp_sub_category(subtype, collected)
    permit = WORK_PERMIT_TYPE_LABELS.get(token, "Work Permit")
    brand = _str(collected.get("brand"))
    mall = _str(collected.get("mall"))
    unit = _str(collected.get("unit_codes"))
    date_from, date_to, _times = _schedule_parts(collected, token)
    work = _work_phrase(collected, token)

    name = (first_name or "").strip()
    greeting = f"Hi {name}!" if name else "Hi!"

    subject = f"**{brand}**" if brand else "A tenant"
    if work:
        sentence = f"{subject} wants to carry out **{work}**"
    else:
        sentence = f"{subject} has requested {_article(permit)} **{permit}** permit"
    where = ", ".join(p for p in (mall, f"Unit {unit}" if unit else "") if p)
    if where:
        sentence += f" at **{where}**"
    if date_from and date_to and date_from != date_to:
        sentence += f", from **{date_from} to {date_to}**"
    elif date_from or date_to:
        sentence += f", on **{date_from or date_to}**"
    sentence += "."

    if token in _SINGLE_HOP_SUBTYPES:
        pending = f"The **{permit} review** is now pending with you."
    else:
        hop = STAGE_LABELS.get(workflow_stage, "")
        base = hop[: -len("review")].strip() if hop.lower().endswith("review") else hop
        hop_phrase = f"{base} review" if base else "review"
        pending = f"This **{permit}** permit is now pending your **{hop_phrase}**."
    pending += " What would you like to do?"

    return "\n\n".join([greeting, sentence, pending])


def build_wp_review_prompt_suggestions(
    collected: dict[str, Any],
    *,
    subtype: str | None = None,
) -> dict[str, Any]:
    """Conversational prompt chips (no workflow ``action`` ids)."""
    token = resolve_wp_sub_category(subtype, collected)
    options: list[dict[str, str]] = [
        {
            "label": "Who raised this?",
            "value": "Who raised this work permit?",
        },
        {
            "label": "Show work details",
            "value": "Show me the work details for this permit.",
        },
    ]
    if collected.get("measureofsafety") or token in {
        "CONSTRUCTION_HOT",
        "CONSTRUCTION_ROOF_ACCESS",
        "MAINTENANCE_HOT",
        "MAINTENANCE_ROOF_ACCESS",
    }:
        options.insert(
            0,
            {
                "label": "Show safety details",
                "value": "Show me the safety measures for this permit.",
            },
        )
    if collected.get("planneddate") or collected.get("effective_starting"):
        options.append(
            {
                "label": "Show the schedule",
                "value": "Show me the planned work schedule.",
            }
        )
    return {"kind": "labeled_options", "options": options}
