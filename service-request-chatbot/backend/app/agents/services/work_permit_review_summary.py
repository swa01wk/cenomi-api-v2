"""Deterministic Work Permit review summary facts + natural wording.

Facts are chosen in code (business-critical). Wording is a small template
composer over those facts — not a field dump and not LLM-authored action lists.
"""

from __future__ import annotations

import difflib
import re
from typing import Any, TypedDict

from app.agents.schemas.work_permit_form_fields import (
    FIELD_LABELS,
    WP_DEPARTMENT_OPTIONS,
    WP_MEASURE_OF_SAFETY_LABELS,
    WP_TYPEOFWORK_OPTIONS,
    form_spec_for,
    format_wp_field_display,
    summarize_effective_starting,
    summarize_period,
    summarize_typeofwork,
    wp_english_part,
    wp_safety_answer,
)
from app.agents.schemas.work_permit_schema import (
    STAGE_LABELS,
    WORK_PERMIT_TYPE_LABELS,
    WP_ACTION_REQUIRED,
    WP_APPROVED,
    WP_REJECTED,
    WP_REVIEW_STAGES,
    normalize_wp_role,
    resolve_wp_sub_category,
)
from app.agents.services.display_formatters import format_enum_label
from app.agents.services.general_inquiry_review_summary import company_name_from_lease_label
from app.agents.services.lease_lookup_service import normalise_lease_code

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
    date_from, date_to, first, second = _schedule_windows(collected, subtype)
    return date_from, date_to, " · ".join(w for w in (first, second) if w)


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
        {
            "key": "company",
            "label": "Company",
            "value": readable_wp_company(collected),
            "icon": "briefcase",
        },
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
        {
            "key": "lease",
            "label": "Lease",
            "value": _str(collected.get("lease_code") or collected.get("lease")),
            "icon": "key",
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


# Form keys that are plumbing, not request details: the lease picker value,
# the phone prefix, and the tenant/lease composites the form resolves itself.
# ``planned_second_period`` is a chat-only ask already folded into ``planneddate``.
_DETAIL_SKIP_KEYS: frozenset[str] = frozenset(
    {
        "lease",
        "country_code",
        "tenant_contact",
        "lease_brand_mall",
        "planned_second_period",
    }
)

_DEPARTMENT_LABEL_BY_KEY: dict[str, str] = {
    key: label for label, key in WP_DEPARTMENT_OPTIONS
}


def _detail_value(key: str, value: Any) -> str:
    if key == "typeofwork":
        return _human_typeofwork(value)
    if key == "work_permit_type":
        token = _str(value)
        return WORK_PERMIT_TYPE_LABELS.get(token.upper(), token) if token else ""
    if key == "department":
        token = _str(value)
        return _DEPARTMENT_LABEL_BY_KEY.get(token.lower(), wp_english_part(token))
    if isinstance(value, dict) or (isinstance(value, list) and key != "unit_codes"):
        return _str(format_wp_field_display(key, value))
    if isinstance(value, list):
        return _str(value)
    return wp_english_part(value)


class WpDetailRow(TypedDict, total=False):
    """One request-detail row; serialized as-is into ``summary`` / ``details`` UI."""

    key: str
    label: str
    value: str
    section: str
    summaryKey: str


# Row sections, used to answer "show the schedule / safety / work details".
SECTION_LOCATION = "location"
SECTION_PERMIT = "permit"
SECTION_SCHEDULE = "schedule"
SECTION_SAFETY = "safety"
SECTION_WORK = "work"
SECTION_WORKERS = "workers"
SECTION_OTHER = "other"

# Display groups for a full-request card: (id, title, icon, row sections).
# Field order inside a group follows the source rows; empty groups are omitted
# by the grouping helper. Icons are names the chat card's icon set knows.
_DETAIL_SECTION_GROUPS: tuple[tuple[str, str, str, tuple[str, ...]], ...] = (
    ("request", "Request and location", "location", (SECTION_LOCATION, SECTION_PERMIT)),
    ("work", "Work details", "tool", (SECTION_WORK, SECTION_OTHER)),
    ("schedule", "Schedule", "calendar", (SECTION_SCHEDULE,)),
    ("safety", "Safety", "shield", (SECTION_SAFETY,)),
    ("workers", "Workers", "person", (SECTION_WORKERS,)),
)

_SECTION_BY_KEY: dict[str, str] = {
    "work_permit_type": SECTION_PERMIT,
    "department": SECTION_PERMIT,
    "unit_type": SECTION_PERMIT,
    "owner": SECTION_PERMIT,
    "trade_name": SECTION_PERMIT,
    "permission_given_to": SECTION_PERMIT,
    "responsible_person": SECTION_WORK,
    "phone": SECTION_WORK,
    "type_work": SECTION_WORK,
    "work_type": SECTION_WORK,
    "typeofwork": SECTION_WORK,
    "list_equipment": SECTION_WORK,
    "work_info": SECTION_WORK,
    "contractor_name": SECTION_WORK,
    "work_location": SECTION_WORK,
    "demand_appendices": SECTION_WORK,
    "special_conditions": SECTION_WORK,
    "entry_gate": SECTION_WORK,
    "description": SECTION_WORK,
    "notes": SECTION_WORK,
}

# Keys rendered by dedicated row builders (or never shown), not the generic loop.
_DETAIL_DEDICATED_KEYS: frozenset[str] = frozenset(
    {
        "company_name",
        "lease_code",
        "work_permit_type",
        "planneddate",
        "effective_starting",
        "firstperiod",
        "secondperiod",
        "measureofsafety",
        "workers",
    }
)

_TYPE_OF_WORK_KEYS: tuple[str, ...] = ("type_work", "work_type", "typeofwork")


def _row(
    key: str,
    label: str,
    value: str,
    section: str,
    summary_key: str | None = None,
) -> WpDetailRow:
    row: WpDetailRow = {"key": key, "label": label, "value": value, "section": section}
    if summary_key:
        row["summaryKey"] = summary_key
    return row


def wp_lease_code(collected: dict[str, Any]) -> str:
    """Normalised lease code of the request (``lease_code``, else the ``lease`` picker)."""
    return normalise_lease_code(collected.get("lease_code") or collected.get("lease"))


def readable_wp_company(collected: dict[str, Any]) -> str:
    """Tenant company name for display, or ``""``.

    With a lease code only lease-tied sources count: ``company`` when
    ``company_lease_code`` marks it as verified for this lease, else the
    company part of ``lease_brand_mall``. ``company`` alone may be the user's
    search text or a previous request's name. ``company_name`` usually holds
    the tenant profile id, so digits are never a name.
    """
    lease_code = wp_lease_code(collected)
    if lease_code:
        if normalise_lease_code(collected.get("company_lease_code")) == lease_code:
            company = _str(collected.get("company"))
            if company:
                return company
        return company_name_from_lease_label(
            collected.get("lease_brand_mall"),
            lease_code=collected.get("lease_code") or collected.get("lease"),
            brand=collected.get("brand"),
            mall=collected.get("mall"),
        )
    company = _str(collected.get("company"))
    if company:
        return company
    company_name = _str(collected.get("company_name"))
    if company_name and not company_name.isdigit():
        return company_name
    return ""


def _plain_time(value: Any) -> str:
    """Display time; ISO timestamps (no LT twin) are not user-facing."""
    text = _str(value)
    if not text or "T" in text:
        return ""
    return text


def _time_window(t_from: str, t_to: str) -> str:
    if t_from and t_to and t_from != t_to:
        return f"{t_from} – {t_to}"
    return t_from or t_to


def _schedule_windows(collected: dict[str, Any], subtype: str) -> tuple[str, str, str, str]:
    """``(date_from, date_to, first_window, second_window)`` from the stored schedule."""
    if subtype == "OPERATIONS":
        effective = collected.get("effective_starting")
        date_from = date_to = ""
        if isinstance(effective, dict):
            date_from = _str(effective.get("datefrom"))
            date_to = _str(effective.get("dateto"))
        windows = []
        for key in ("firstperiod", "secondperiod"):
            period = collected.get(key)
            windows.append(summarize_period(period) if isinstance(period, dict) else "")
        return date_from, date_to, windows[0], windows[1]

    planned = collected.get("planneddate")
    if not isinstance(planned, dict):
        return "", "", "", ""
    windows = []
    for from_keys, to_keys in (
        (("worktimefromLT", "worktimefrom"), ("worktimetoLT", "worktimeto")),
        (("completionfromLT", "completionfrom"), ("completiontoLT", "completionto")),
    ):
        t_from = next((t for k in from_keys if (t := _plain_time(planned.get(k)))), "")
        t_to = next((t for k in to_keys if (t := _plain_time(planned.get(k)))), "")
        windows.append(_time_window(t_from, t_to))
    return (
        _str(planned.get("worktimedate")),
        _str(planned.get("completionworkdate")),
        windows[0],
        windows[1],
    )


def _schedule_rows(collected: dict[str, Any], subtype: str) -> list[WpDetailRow]:
    date_from, date_to, first, second = _schedule_windows(collected, subtype)
    rows: list[WpDetailRow] = []
    for key, label, value in (
        ("schedule_dates", "Work dates", _date_range(date_from, date_to, " – ")),
        ("schedule_time", "Work time", first),
        ("schedule_second_period", "Second period", second),
    ):
        if value:
            rows.append(_row(key, label, value, SECTION_SCHEDULE, "schedule"))
    return rows


def _safety_rows(value: Any) -> list[WpDetailRow]:
    if not isinstance(value, dict):
        return []
    rows: list[WpDetailRow] = []
    for item_key, answer in value.items():
        shown = wp_safety_answer(answer)
        if not shown:
            continue
        label = WP_MEASURE_OF_SAFETY_LABELS.get(item_key) or format_enum_label(item_key)
        rows.append(_row(f"measureofsafety.{item_key}", label, shown, SECTION_SAFETY))
    return rows


def _worker_line(parts: list[tuple[str, Any]]) -> str:
    shown: list[str] = []
    for prefix, raw in parts:
        text = wp_english_part(raw)
        if text:
            shown.append(f"{prefix}{text}")
    return " · ".join(shown)


def _worker_rows(value: Any) -> list[WpDetailRow]:
    """One row per worker for both stored shapes (cold ``listWorker`` / hot ``addworker``)."""
    rows: list[WpDetailRow] = []
    if isinstance(value, list):
        for row in value:
            if not isinstance(row, dict):
                continue
            company = _str(row.get("company_name"))
            line = _worker_line(
                [
                    ("", row.get("full_name")),
                    ("", row.get("designation")),
                    ("", "" if company.isdigit() else company),
                    ("Phone ", row.get("phone")),
                    ("Iqama ", row.get("iqama_number")),
                ]
            )
            if line:
                n = len(rows) + 1
                rows.append(_row(f"workers.{n}", f"Worker {n}", line, SECTION_WORKERS))
        return rows
    if isinstance(value, dict):
        workers = value.get("worker")
        for row in workers if isinstance(workers, list) else []:
            if not isinstance(row, dict):
                continue
            line = _worker_line(
                [
                    ("", row.get("name")),
                    ("", row.get("designation")),
                    ("", row.get("worker_qualification")),
                    ("Iqama ", row.get("iqama_number")),
                ]
            )
            if line:
                n = len(rows) + 1
                rows.append(_row(f"workers.{n}", f"Worker {n}", line, SECTION_WORKERS))
        instructor = _str(value.get("instructor_name"))
        if instructor:
            rows.append(_row("workers.instructor", "Instructor name", instructor, SECTION_WORKERS))
    return rows


def group_wp_detail_sections(rows: list[WpDetailRow]) -> list[dict[str, Any]]:
    """Full-request sections in display order, without rearranging their fields.

    A field stays in the position it already has inside ``rows``. Groups with
    no filled fields are left out.
    """
    grouped: dict[str, list[dict[str, str]]] = {
        group_id: [] for group_id, _title, _icon, _sections in _DETAIL_SECTION_GROUPS
    }
    membership = {
        section: group_id
        for group_id, _title, _icon, sections in _DETAIL_SECTION_GROUPS
        for section in sections
    }
    for row in rows:
        group_id = membership.get(str(row.get("section") or ""))
        label = _str(row.get("label"))
        value = _str(row.get("value"))
        if not group_id or not label or not value:
            continue
        grouped[group_id].append({"label": label, "value": value})
    return [
        {"id": group_id, "title": title, "icon": icon, "fields": grouped[group_id]}
        for group_id, title, icon, _sections in _DETAIL_SECTION_GROUPS
        if grouped[group_id]
    ]


def build_wp_review_details(
    collected: dict[str, Any],
    *,
    subtype: str | None = None,
) -> list[WpDetailRow]:
    """Every filled Work Permit detail as human-readable rows — the one source for
    the review / status card details, the details reply, and the model's context.

    Order: who and where, permit type, type of work, schedule, then the remaining
    form fields (safety and workers one row per item). Empty values are skipped.
    Rows already shown by the compact summary carry ``summaryKey``.
    """
    token = resolve_wp_sub_category(subtype, collected)
    spec = form_spec_for(token)
    if spec is not None:
        form_keys = {*spec.common_keys, *spec.field_types.keys()}
    else:
        form_keys = set(FIELD_LABELS.keys())

    rows: list[WpDetailRow] = []
    for key, label, value, summary_key in (
        ("company", "Company", readable_wp_company(collected), "company"),
        ("brand", "Brand", _str(collected.get("brand")), "brand"),
        ("mall", "Mall", _str(collected.get("mall")), "mall"),
        ("unit_codes", "Unit", _str(collected.get("unit_codes")), "unit"),
        ("lease_code", "Lease", _str(collected.get("lease_code") or collected.get("lease")), "lease"),
    ):
        if value:
            rows.append(_row(key, label, value, SECTION_LOCATION, summary_key))

    permit = _detail_value("work_permit_type", collected.get("work_permit_type") or token)
    if permit:
        rows.append(
            _row("work_permit_type", "Permit / Request Type", permit, SECTION_PERMIT, "request_type")
        )
    # SR-level fields: not in any form spec, but part of the request.
    for key, label, section in (
        ("title", "Title", SECTION_PERMIT),
        ("description", "Description", SECTION_WORK),
    ):
        value = _str(collected.get(key))
        if value:
            rows.append(_row(key, label, value, section))

    for key in _TYPE_OF_WORK_KEYS:
        if key not in form_keys:
            continue
        value = _detail_value(key, collected.get(key))
        if value:
            rows.append(_row(key, FIELD_LABELS[key], value, SECTION_WORK, "type_of_work"))

    rows.extend(_schedule_rows(collected, token))

    for key in FIELD_LABELS:
        if (
            key not in form_keys
            or key in _DETAIL_SKIP_KEYS
            or key in _DETAIL_DEDICATED_KEYS
            or key in _TYPE_OF_WORK_KEYS
            or key in {"title", "description"}
        ):
            continue
        value = _detail_value(key, collected.get(key))
        if value:
            rows.append(_row(key, FIELD_LABELS[key], value, _SECTION_BY_KEY.get(key, SECTION_OTHER)))

    if "measureofsafety" in form_keys:
        rows.extend(_safety_rows(collected.get("measureofsafety")))
    if "workers" in form_keys:
        rows.extend(_worker_rows(collected.get("workers")))
    return rows


def _document_id(row: dict[str, Any]) -> str:
    return str(row.get("document_id") or row.get("documentId") or "").strip()


def build_wp_detail_files(
    collected: dict[str, Any],
    documents: list[dict[str, Any]] | None = None,
) -> list[dict[str, str]]:
    """Every file on the request as ``{label, fileName, documentId}``.

    SR attachments come from ``documents``; worker Iqamas live only on the
    ``workers`` rows (the platform does not list them as attachments).
    """
    names: dict[str, str] = {
        _document_id(doc): str(doc.get("file_name") or doc.get("fileName") or "").strip()
        for doc in documents or []
        if isinstance(doc, dict)
    }
    files: list[dict[str, str]] = []
    seen: set[str] = set()

    def add(doc_id: str, label: str) -> None:
        if not doc_id or doc_id in seen:
            return
        seen.add(doc_id)
        files.append({"label": label, "fileName": names.get(doc_id) or label, "documentId": doc_id})

    for doc in documents or []:
        if isinstance(doc, dict):
            add(_document_id(doc), names.get(_document_id(doc)) or "Document")

    workers = collected.get("workers")
    if isinstance(workers, dict):
        workers = workers.get("worker")
    for n, row in enumerate(workers if isinstance(workers, list) else [], start=1):
        if isinstance(row, dict):
            who = wp_english_part(row.get("full_name") or row.get("name")) or f"Worker {n}"
            add(_document_id(row), f"Iqama — {who}")
    return files


# Subtypes whose workflow has a single approval hop — the pending line names the
# permit ("the Operations review") rather than the hop ("pending FM review").
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


# History rows that are form saves or skipped hops, not a workflow decision.
_HISTORY_NOISE: frozenset[str] = frozenset({"", "SKIPPED", "UPDATE", "RE_EXAMINE"})

# Platform history status → (active verb phrase, passive fallback).
_HISTORY_VERBS: dict[str, tuple[str, str]] = {
    **dict.fromkeys(
        ("CREATED", "NEW", "SUBMIT", "SUBMITTED"),
        ("submitted this request", "was submitted"),
    ),
    **dict.fromkeys(
        ("RESUBMIT", "RESUBMITTED", "RE_SUBMITTED"),
        (
            "resubmitted this request with the requested information",
            "was resubmitted with the requested information",
        ),
    ),
    **dict.fromkeys(
        ("APPROVE", "APPROVED", "FINISHED", "DONE", "COMPLETED"),
        ("approved this request", "was approved"),
    ),
    **dict.fromkeys(
        ("REJECT", "REJECTED"),
        ("rejected this request", "was rejected"),
    ),
    **dict.fromkeys(
        ("ACTION_REQUIRED", "ASK_FOR_MORE_INFO", "REQUEST_MORE_INFO"),
        ("asked for more information", "was sent back for more information"),
    ),
    **dict.fromkeys(
        ("REQUESTED_RE_UPLOAD", "REQUEST_TO_REUPLOAD"),
        ("asked for documents to be re-uploaded", "was sent back for re-upload"),
    ),
    **dict.fromkeys(
        ("REASSIGN_TO", "REASSIGN_ROLE", "ASSIGN_TO"),
        ("reassigned this request", "was reassigned"),
    ),
    "EXTEND": ("requested an extension", "had an extension requested"),
    **dict.fromkeys(
        ("CANCEL", "CANCELLED"),
        ("requested a cancellation", "had a cancellation requested"),
    ),
}

_WP_ROLE_LABELS: dict[str, str] = {
    "DD_ENGINEER": "DD Engineer",
    "FM_MANAGER": "FM Manager",
    "MALL_MANAGER": "Mall Manager",
    "OPERATIONS": "Operations",
    "OPERATION_EXECUTIVES": "Operation Executive",
    "ADMIN": "Admin",
}

# Tenant-side platform roles; the reviewer only needs to know it was the tenant.
# LEASING_MANAGER is left out: Cenomi staff hold it too.
_TENANT_ROLES: frozenset[str] = frozenset(
    {
        "TENANT",
        "PRIMARY_TENANT_CONTACT",
        "SECONDARY_TENANT_CONTACT",
        "COMPANY_ADMIN",
        "COMPANY_ADMIN_CENTRAL",
        "AUTHORIZED_SIGNATORY",
        "BILLING_CONTACT",
        "FINANCIAL_CONTROLLER",
        "SHOP_MANAGER",
        "STORE_MANAGER",
        "STORE MANAGER",
        "EXISTING_TENANT",
        "PROSPECTIVE_TENANT",
    }
)

_SUBMIT_STATUSES: frozenset[str] = frozenset({"CREATED", "NEW", "SUBMIT", "SUBMITTED"})
_SEND_BACK_STATUSES: frozenset[str] = frozenset(
    {
        "ACTION_REQUIRED",
        "ASK_FOR_MORE_INFO",
        "REQUEST_MORE_INFO",
        "REQUESTED_RE_UPLOAD",
        "REQUEST_TO_REUPLOAD",
    }
)
_RESUBMIT_STATUS = "RESUBMITTED"
# Form saves that count as the tenant's resubmission only while a send-back is open.
_REPLY_STATUSES: frozenset[str] = frozenset({"UPDATE", "RE_EXAMINE", "IN_PROCESS"})


def _history_role(row: dict[str, Any]) -> str:
    raw = row.get("operator_role")
    if isinstance(raw, dict):
        raw = raw.get("role_name") or raw.get("name")
    role = normalize_wp_role(raw)
    if role in _TENANT_ROLES or (
        not role and str(row.get("user_type") or "").upper() == "TENANT"
    ):
        return "Tenant"
    if not role:
        return ""
    return _WP_ROLE_LABELS.get(role) or _str(format_enum_label(role))


def _history_status(row: dict[str, Any]) -> str:
    return str(row.get("status") or "").upper()


def _history_name(row: dict[str, Any]) -> str:
    return _str(row.get("operator_name") or row.get("created_by_name"))


def _meaningful_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """History without form saves and skipped hops.

    A form save or a fresh submission while a send-back is open is the tenant
    answering it, so it becomes a resubmission (a re-tagged copy; the input is
    not mutated).
    """
    out: list[dict[str, Any]] = []
    send_back_open = False
    for row in rows:
        status = _history_status(row)
        if status in _SEND_BACK_STATUSES:
            send_back_open = True
        elif send_back_open and (status in _REPLY_STATUSES or status in _SUBMIT_STATUSES):
            row = {**row, "status": _RESUBMIT_STATUS}
            send_back_open = False
        elif status in _HISTORY_NOISE or status in _REPLY_STATUSES:
            continue
        else:
            send_back_open = False
        out.append(row)
    return out


def _history_clause(
    row: dict[str, Any], *, follow_on: bool, same_actor: bool = False
) -> str:
    """``"**Sara** (DD Engineer) approved this request"``.

    A ``follow_on`` clause joins a submission clause, so the request becomes
    "it" and a role-only actor is lower-case; ``same_actor`` drops the actor
    the submission clause already named. Without a name or a role the clause
    goes passive rather than inventing a person.
    """
    status = _history_status(row)
    label = format_enum_label(status)
    active, passive = _HISTORY_VERBS.get(
        status,
        (f"moved this request to **{label}**", f"moved to **{label}**"),
    )
    if follow_on:
        active = active.replace("this request", "it")
    if same_actor:
        return active
    name = _history_name(row)
    role = _history_role(row)

    if name and role:
        actor = f"**{name}** ({role})"
    elif name:
        actor = f"**{name}**"
    elif role:
        actor = f"{'the' if follow_on else 'The'} **{role}**"
    else:
        subject = "it" if follow_on else "This request"
        return f"{subject} {passive}"
    return f"{actor} {active}"


def compose_wp_history_sentence(
    history: list[dict[str, Any]] | tuple[dict[str, Any], ...] | None,
    creator: dict[str, Any] | None = None,
) -> str:
    """Who submitted the request and, when there is one, the latest step since.

    The submitter is the first submission row in the history, else the SR's
    ``creator`` (``name`` / ``role``). Form saves and skipped hops are ignored,
    except the tenant's answer to a send-back (see ``_meaningful_rows``).
    Returns ``""`` when nothing is known.
    """
    rows = _meaningful_rows([row for row in (history or []) if isinstance(row, dict)])
    submit_at = next(
        (i for i, row in enumerate(rows) if _history_status(row) in _SUBMIT_STATUSES),
        None,
    )
    submitter: dict[str, Any] | None = rows[submit_at] if submit_at is not None else None
    if submitter is None and isinstance(creator, dict):
        submitter = {
            "status": "CREATED",
            "operator_name": creator.get("name"),
            "operator_role": creator.get("role"),
        }
        if not any(_str(submitter.get(k)) for k in ("operator_name", "operator_role")):
            submitter = None

    later = rows[submit_at + 1 :] if submit_at is not None else rows
    latest = next(
        (row for row in reversed(later) if _history_status(row) not in _SUBMIT_STATUSES),
        None,
    )

    if submitter is not None and latest is not None:
        submitter_name = _history_name(submitter).casefold()
        same_actor = bool(submitter_name) and submitter_name == _history_name(latest).casefold()
        return (
            f"{_history_clause(submitter, follow_on=False)}, and "
            f"{_history_clause(latest, follow_on=True, same_actor=same_actor)}."
        )
    only = submitter if submitter is not None else latest
    return f"{_history_clause(only, follow_on=False)}." if only is not None else ""


def compose_wp_review_message(
    collected: dict[str, Any],
    *,
    workflow_stage: str,
    subtype: str | None = None,
    first_name: str | None = None,
    request_history: list[dict[str, Any]] | tuple[dict[str, Any], ...] | None = None,
    creator: dict[str, Any] | None = None,
) -> str:
    """Greeting + who/what/where/when + the pending review line + who acted so far.

    The closing question ends the last paragraph, so it follows the history
    sentence when there is one.

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
        pending = f"This **{permit}** permit is now pending **{hop_phrase}**."

    question = "What would you like to do?"
    history = compose_wp_history_sentence(request_history, creator)
    if history:
        return "\n\n".join([greeting, sentence, pending, f"{history} {question}"])
    return "\n\n".join([greeting, sentence, f"{pending} {question}"])


_VIEW_DETAILS_HINT = "Use **View details** below to see the full request."


def compose_wp_status_message(
    collected: dict[str, Any],
    *,
    sr_id: str | None,
    subtype: str | None = None,
    workflow_stage: str | None = None,
    platform_status: str | None = None,
    is_tenant: bool = False,
) -> str:
    """Status reply for a Work Permit with no review step waiting on the caller.

    Covers sent back (``WP_ACTION_REQUIRED``), approved, rejected, submitted,
    a hop held by someone else, and an unsubmitted draft.
    """
    token = resolve_wp_sub_category(subtype, collected)
    permit = WORK_PERMIT_TYPE_LABELS.get(token, "")
    stage = workflow_stage or ""

    if not sr_id:
        if not any(v not in (None, "", [], {}) for v in collected.values()):
            return (
                "Nothing has been collected for this Work Permit yet.\n\n"
                "Tell me the lease code, brand or mall and the type of work, "
                "and I'll help you raise it."
            )
        what = f"**{permit}** Work Permit" if permit else "Work Permit"
        return (
            f"Here's your {what} draft so far — it hasn't been submitted yet.\n\n"
            "Use **View details** below to check every field before you confirm it."
        )

    subject = f"Work Permit SR **{sr_id}**"
    if permit:
        subject += f" (**{permit}**)"

    if stage == WP_ACTION_REQUIRED:
        if is_tenant:
            return (
                f"{subject} was sent back to you for more information.\n\n"
                "Update the requested details in the form and resubmit it to "
                "restart the review. Use **View details** below to check what "
                "was submitted."
            )
        return (
            f"{subject} was sent back to the tenant for more information.\n\n"
            "It returns to the review queue once they update the requested "
            "details and resubmit it, so there is nothing for you to do on it "
            "right now. Use **View details** below to see what was submitted."
        )
    if stage == WP_APPROVED:
        return (
            f"{subject} has been approved by all reviewers, so the permit is "
            "final.\n\nNo further action is needed. Use **View details** below "
            "to see the approved request."
        )
    if stage == WP_REJECTED:
        return (
            f"{subject} was rejected, and its review is closed.\n\n"
            "To carry out this work, a new Work Permit needs to be raised. "
            "Use **View details** below to see what was submitted."
        )
    if stage in WP_REVIEW_STAGES:
        hop = STAGE_LABELS.get(stage, "review")
        return (
            f"{subject} is currently at **{hop}**.\n\n"
            "It's waiting on that reviewer, so there is nothing for you to do "
            f"on it right now. {_VIEW_DETAILS_HINT}"
        )
    status = format_enum_label(platform_status) if platform_status else ""
    in_progress = (
        f"is in progress (status: **{status}**)" if status else "has been submitted"
    )
    return (
        f"{subject} {in_progress}.\n\n"
        f"There is no step waiting on you right now. {_VIEW_DETAILS_HINT}"
    )


FULL_REQUEST_CHIP_VALUE = "Show the full request"


def build_wp_full_request_chips(sr_id: Any, *, on_sr_page: bool) -> list[dict[str, str]]:
    """"Open full request" asks the chat for the details; navigation is its own chip."""
    chips = [{"label": "Open full request", "value": FULL_REQUEST_CHIP_VALUE}]
    if sr_id and not on_sr_page:
        chips.append(
            {"label": "Go to request page", "value": str(sr_id), "action": "view_service_request"}
        )
    return chips


# Sections a "show me …" message can ask for; rows carry ``section`` (see
# build_wp_review_details). "work" shows the permit and work rows together.
WP_DETAILS_SECTIONS: dict[str, frozenset[str]] = {
    "schedule": frozenset({SECTION_SCHEDULE}),
    "safety": frozenset({SECTION_SAFETY}),
    "work": frozenset({SECTION_PERMIT, SECTION_WORK}),
}

_SECTION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "all",
        re.compile(
            r"\b(full|all|complete|entire|whole)\s+(the\s+)?"
            r"(request|details?|fields?|information|info|permit)\b"
            r"|\brequest\s+details\b"
        ),
    ),
    (
        "safety",
        re.compile(r"\b(measures?\s+)?safety(\s+(measures?|details|checklist))?\b"),
    ),
    (
        "schedule",
        re.compile(
            r"\b(planned\s+)?(work\s+)?sch?edu?le?s?\b|\bplanned\s+(work|dates?|times?)\b"
            r"|\bwork(ing)?\s+(times?|hours|dates?)\b|\btimings?\b"
        ),
    ),
    ("work", re.compile(r"\bwork\s+details\b|\bdetails\s+of\s+(the\s+)?work\b")),
)

_SHOW_VERB_RE = re.compile(
    r"\b(show|view|see|display|open|give|list|check|tell\s+me|what(?:'s|\s+is|\s+are))\b"
)
# A message that also asks to act is a review decision, never a details request.
_ACTION_WORD_RE = re.compile(
    r"\b(approve|approved|reject|rejected|assign|reassign|cancel|extend|"
    r"more\s+info|ask\s+for|send\s+back|confirm|submit)\b"
)
_FILLER_RE = re.compile(r"\b(the|a|an|please|me|for|this|that|permit|work\s+permit|of)\b")


def _schedule_typo(text: str) -> bool:
    return any(
        difflib.get_close_matches(word, ["schedule"], n=1, cutoff=0.8)
        for word in re.findall(r"[a-z]+", text)
    )


def wp_details_section(message: str | None) -> str | None:
    """``"all" | "schedule" | "safety" | "work"`` when the message asks to see
    Work Permit details, else None. Needs a show-verb, or the message must be
    just the section name; any review action word disqualifies it."""
    text = re.sub(r"\s+", " ", str(message or "").casefold()).strip(" .?!")
    if not text or _ACTION_WORD_RE.search(text):
        return None
    has_verb = bool(_SHOW_VERB_RE.search(text))
    bare = re.sub(r"\s+", " ", _FILLER_RE.sub(" ", text)).strip()
    for section, pattern in _SECTION_PATTERNS:
        match = pattern.search(text)
        if match and (has_verb or pattern.fullmatch(bare)):
            return section
    if (has_verb or len(bare.split()) == 1) and _schedule_typo(text):
        return "schedule"
    return None


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
