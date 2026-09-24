"""Field extraction node — calls FieldExtractionService and stores results in state.

Responsibilities
----------------
- Read ``user_message`` and ``workflow_stage`` from the graph state.
- Delegate extraction to ``FieldExtractionService`` (LLM call + retry logic).
- Emit a child LLM run span and an LLM call record via ``TraceManager`` so
  every extraction attempt is fully observable.
- Write ``extracted_fields`` to state as ``{field: {value, confidence}}``.

Non-responsibilities (enforced)
--------------------------------
- MUST NOT merge extracted fields into ``collected_data``.  That is the
  exclusive job of ``merge_state_node``.
- MUST NOT validate business rules or check required fields.
- MUST NOT perform lease / backend lookups.
"""

from __future__ import annotations

import json
import re
import time
from decimal import Decimal
from typing import Any
from uuid import UUID

import structlog

from app.agents.graph.state import ServiceRequestState
from app.agents.llm.gateway import get_default_gateway
from app.agents.registries.workflow_config import get_workflow_config
from app.agents.services.field_correction_service import (
    detect_correction_intent,
    is_unspecified_correction,
)
from app.agents.services.field_extraction_service import FieldExtractionService
from app.agents.services.validation_service import _SINGLE_DATE_FIELDS
from app.observability.decorators import trace_node

log = structlog.get_logger(__name__)

# Vague relative-time words the extraction LLM is instructed to omit (see
# "other relative dates" in handover_extraction_prompt.py), but which it may
# not reliably comply with. Guarded deterministically here so a bare
# "tomorrow" never has a chance to reach an LLM call and get silently
# resolved into an absolute date the user never actually typed.
_RELATIVE_DATE_RE = re.compile(
    r"\b(today|tomorrow|tommorow|tmrw|yesterday|next week|next month|"
    r"day after tomorrow|this week|this month)\b",
    re.IGNORECASE,
)
# Any digit in the message means the user likely gave (or attempted) an
# actual date — don't block those even if a relative word also appears.
_HAS_DIGIT_RE = re.compile(r"\d")


def _is_bare_relative_date(message: str) -> bool:
    """True when *message* names a relative date term with no actual date."""
    return bool(_RELATIVE_DATE_RE.search(message)) and not _HAS_DIGIT_RE.search(message)


# Deterministic time-period parser for firstperiod/secondperiod.
# Handles "4pm to 5pm", "2 to 3pm", "2-3pm", "9:30am to 12pm", "16:00 to 17:00".
_TIME_12H_RE = re.compile(
    r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)",
    re.IGNORECASE,
)
_TIME_24H_RE = re.compile(r"\b(\d{1,2}):(\d{2})\b")
# Optional am/pm on either side — inherit from the side that has it.
_TIME_RANGE_RE = re.compile(
    r"(?P<from>\d{1,2}(?::\d{2})?\s*(?:am|pm)?)\s*(?:to|-)\s*"
    r"(?P<to>\d{1,2}(?::\d{2})?\s*(?:am|pm)?)",
    re.IGNORECASE,
)

_MONTH_NAMES: dict[str, int] = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}

# "23 to 24 september" / "23-24 september 2026"
_DAY_MONTH_RANGE_RE = re.compile(
    r"\b(?P<d1>\d{1,2})\s*(?:to|-)\s*(?P<d2>\d{1,2})\s+"
    r"(?P<month>[A-Za-z]+)(?:\s+(?P<year>\d{4}))?\b",
    re.IGNORECASE,
)
# "24 sep to 28 sep" / "24 september to 28 september 2026"
_DAY_MONTH_EACH_SIDE_RANGE_RE = re.compile(
    r"\b(?P<d1>\d{1,2})\s+(?P<m1>[A-Za-z]+)\s*(?:to|-)\s*"
    r"(?P<d2>\d{1,2})\s+(?P<m2>[A-Za-z]+)(?:\s+(?P<year>\d{4}))?\b",
    re.IGNORECASE,
)
# Single "23 september" / "23 september 2026"
_DAY_MONTH_SINGLE_RE = re.compile(
    r"\b(?P<d1>\d{1,2})\s+(?P<month>[A-Za-z]+)(?:\s+(?P<year>\d{4}))?\b",
    re.IGNORECASE,
)
# "23/09 to 24/09" / "23/09/2026 to 24/09/2026"
_SLASH_DATE_RANGE_RE = re.compile(
    r"\b(?P<d1>\d{1,2})/(?P<m1>\d{1,2})(?:/(?P<y1>\d{2,4}))?\s*(?:to|-)\s*"
    r"(?P<d2>\d{1,2})/(?P<m2>\d{1,2})(?:/(?P<y2>\d{2,4}))?\b",
    re.IGNORECASE,
)
# "24th sep" / "23rd to 24th september" — strip ordinals before day-month match.
_ORDINAL_DAY_RE = re.compile(r"\b(\d{1,2})(st|nd|rd|th)\b", re.IGNORECASE)

_WP_SCHEDULE_FIELDS: frozenset[str] = frozenset(
    {"effective_starting", "firstperiod", "secondperiod", "planned_second_period"}
)


def _strip_ordinal_days(message: str) -> str:
    """Turn ``24th sep`` into ``24 sep`` so day-month regexes can match."""
    return _ORDINAL_DAY_RE.sub(r"\1", message)


def _schedule_value_from_extracted(entry: Any) -> Any:
    """Unwrap ``{value, confidence}`` / JSON-string nested schedule widgets."""
    val = entry.get("value") if isinstance(entry, dict) else entry
    if isinstance(val, str) and val.strip().startswith("{"):
        try:
            return json.loads(val)
        except (json.JSONDecodeError, TypeError, ValueError):
            return val
    return val


def _drop_incomplete_schedule_overwrites(
    extracted_fields: dict[str, Any],
    collected: dict[str, Any],
) -> None:
    """Remove incomplete schedule proposals that would wipe a complete collected value."""
    from app.agents.schemas.work_permit_form_fields import is_tenant_field_missing

    for field_name in _WP_SCHEDULE_FIELDS:
        if field_name not in extracted_fields:
            continue
        collected_val = collected.get(field_name)
        optional = field_name in {"secondperiod", "planned_second_period"}
        if is_tenant_field_missing(field_name, collected_val, optional=optional):
            continue
        new_val = _schedule_value_from_extracted(extracted_fields[field_name])
        if is_tenant_field_missing(field_name, new_val, optional=optional):
            log.info(
                "field_extraction.drop_incomplete_schedule",
                field=field_name,
            )
            del extracted_fields[field_name]


def _parse_time_to_hhmm(token: str, *, default_suffix: str | None = None) -> str | None:
    """Convert a time token like '4pm', '9:30am', '16:00', or bare '2' to 'HH:MM'."""
    raw = token.strip()
    m12 = _TIME_12H_RE.fullmatch(raw)
    if m12:
        hour = int(m12.group(1))
        minute = int(m12.group(2) or 0)
        suffix = m12.group(3).lower()
        if suffix == "pm" and hour != 12:
            hour += 12
        elif suffix == "am" and hour == 12:
            hour = 0
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            return None
        return f"{hour:02d}:{minute:02d}"
    m24 = _TIME_24H_RE.fullmatch(raw)
    if m24:
        hour, minute = int(m24.group(1)), int(m24.group(2))
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            return None
        return f"{hour:02d}:{minute:02d}"
    # Bare hour/minute without am/pm — inherit from the other side of the range.
    if default_suffix and re.fullmatch(r"\d{1,2}(?::\d{2})?", raw):
        return _parse_time_to_hhmm(f"{raw}{default_suffix}")
    return None


def _extract_time_period(
    message: str,
    *,
    allow_bare_hours: bool = False,
) -> dict[str, str] | None:
    """Return {from, to} if message contains a recognisable time range, else None."""
    # Prefer a match that has at least one am/pm (avoids "23 to 24" day ranges).
    best: re.Match[str] | None = None
    bare: re.Match[str] | None = None
    for m in _TIME_RANGE_RE.finditer(message):
        blob = f"{m.group('from')} {m.group('to')}".lower()
        if "am" in blob or "pm" in blob or ":" in blob:
            best = m
            break
        if allow_bare_hours and bare is None:
            # Reject day-like pairs (e.g. 24 to 26) — hours stay 0–23 and
            # typically the left hour is smaller in a same-day window.
            try:
                left_h = int(re.match(r"\d{1,2}", m.group("from").strip()).group(0))  # type: ignore[union-attr]
                right_h = int(re.match(r"\d{1,2}", m.group("to").strip()).group(0))  # type: ignore[union-attr]
            except (AttributeError, ValueError):
                continue
            if 0 <= left_h <= 23 and 0 <= right_h <= 23 and left_h != right_h:
                bare = m
    if best is None and allow_bare_hours:
        best = bare
    if best is None:
        return None
    left, right = best.group("from").strip(), best.group("to").strip()
    left_suf = re.search(r"(am|pm)\s*$", left, re.IGNORECASE)
    right_suf = re.search(r"(am|pm)\s*$", right, re.IGNORECASE)
    shared = None
    if left_suf:
        shared = left_suf.group(1).lower()
    elif right_suf:
        shared = right_suf.group(1).lower()
    from_hhmm = _parse_time_to_hhmm(left, default_suffix=shared)
    to_hhmm = _parse_time_to_hhmm(right, default_suffix=shared)
    if from_hhmm and to_hhmm:
        return {"from": from_hhmm, "to": to_hhmm}
    # Bare hours without am/pm — treat as 24h clock (9 → 09:00, 17 → 17:00).
    if allow_bare_hours and best is bare:
        left_m = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?", left)
        right_m = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?", right)
        if left_m and right_m:
            lh, lm = int(left_m.group(1)), int(left_m.group(2) or 0)
            rh, rm = int(right_m.group(1)), int(right_m.group(2) or 0)
            if 0 <= lh <= 23 and 0 <= rh <= 23 and 0 <= lm <= 59 and 0 <= rm <= 59:
                return {"from": f"{lh:02d}:{lm:02d}", "to": f"{rh:02d}:{rm:02d}"}
    return None


def _fmt_ddmmyyyy(day: int, month: int, year: int) -> str | None:
    from datetime import date as _date

    try:
        _date(year, month, day)
    except ValueError:
        return None
    return f"{day:02d}/{month:02d}/{year:04d}"


def _expand_year(raw: str | None, default_year: int) -> int:
    if not raw:
        return default_year
    y = int(raw)
    if y < 100:
        return 2000 + y
    return y


def _extract_effective_starting(
    message: str,
    *,
    today: Any | None = None,
) -> dict[str, str] | None:
    """Return {datefrom, dateto} as DD/MM/YYYY when a date range is present."""
    from datetime import date as _date

    ref = today if isinstance(today, _date) else _date.today()
    default_year = ref.year
    message = _strip_ordinal_days(message)

    m = _DAY_MONTH_RANGE_RE.search(message)
    if m:
        month = _MONTH_NAMES.get(m.group("month").lower())
        if month:
            year = _expand_year(m.group("year"), default_year)
            d1, d2 = int(m.group("d1")), int(m.group("d2"))
            left = _fmt_ddmmyyyy(d1, month, year)
            right = _fmt_ddmmyyyy(d2, month, year)
            if left and right:
                return {"datefrom": left, "dateto": right}

    m = _SLASH_DATE_RANGE_RE.search(message)
    if m:
        y1 = _expand_year(m.group("y1"), default_year)
        y2 = _expand_year(m.group("y2"), y1)
        left = _fmt_ddmmyyyy(int(m.group("d1")), int(m.group("m1")), y1)
        right = _fmt_ddmmyyyy(int(m.group("d2")), int(m.group("m2")), y2)
        if left and right:
            return {"datefrom": left, "dateto": right}

    m = _DAY_MONTH_EACH_SIDE_RANGE_RE.search(message)
    if m:
        m1 = _MONTH_NAMES.get(m.group("m1").lower())
        m2 = _MONTH_NAMES.get(m.group("m2").lower())
        if m1 and m2:
            year = _expand_year(m.group("year"), default_year)
            left = _fmt_ddmmyyyy(int(m.group("d1")), m1, year)
            right = _fmt_ddmmyyyy(int(m.group("d2")), m2, year)
            if left and right:
                return {"datefrom": left, "dateto": right}

    m = _DAY_MONTH_SINGLE_RE.search(message)
    if m:
        month = _MONTH_NAMES.get(m.group("month").lower())
        if month:
            year = _expand_year(m.group("year"), default_year)
            day = int(m.group("d1"))
            both = _fmt_ddmmyyyy(day, month, year)
            if both:
                return {"datefrom": both, "dateto": both}
    return None


# Free-text fields whose value is simply whatever the user typed. When the
# answer contains the field's own name ("Post description", "Notes variant 2")
# the extraction LLM reads it as a reference to the field rather than content
# and returns an empty value — wiping the field and looping the question.
_FREE_TEXT_FIELDS: frozenset[str] = frozenset(
    {
        "title",
        "description",
        "notes",
        "comments",
        "type_work",
        "list_equipment",
        "work_info",
        "work_type",
        "work_location",
        "demand_appendices",
        "special_conditions",
        "contractor_name",
        "owner",
        "trade_name",
        "responsible_person",
        "entry_gate",
        "permission_given_to",
        # Contact fields: LLM often omits bare digit phones / short unit labels.
        "phone",
        "unit_type",
        "department",
    }
)

# Label aliases for multi-field group replies ("Type of work - …\nEquipments- …").
_WP_LABELED_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "type_work": ("type of work", "work type", "typeofwork"),
    "list_equipment": (
        "list of equipment involved",
        "list of equipment",
        "equipments",
        "equipment",
    ),
    "work_info": ("work information", "work info", "workinfo"),
    "owner": ("owner",),
    "trade_name": ("trade name", "tradename"),
    "responsible_person": ("responsible person", "responsible"),
    "phone": ("contact phone number", "phone number", "phone", "mobile"),
    "department": ("department", "dept"),
    "unit_type": ("unit type", "unittype"),
    "contractor_name": ("contractor name", "contractor"),
    "work_type": ("work type",),
    "work_location": ("work location",),
    "demand_appendices": ("demand appendices", "appendices"),
    "special_conditions": ("special conditions",),
    "notes": ("notes", "note"),
    "description": ("description",),
    "title": ("title",),
}

_LABELED_LINE_RE = re.compile(
    r"^\s*(?P<label>[A-Za-z][A-Za-z0-9 /_-]{0,60}?)\s*[-:–—]\s*(?P<value>.+?)\s*$"
)


def _normalize_label_key(label: str) -> str:
    return re.sub(r"[\s/_-]+", " ", label.strip().lower())


def _label_to_field(label: str) -> str | None:
    norm = _normalize_label_key(label)
    for field, aliases in _WP_LABELED_FIELD_ALIASES.items():
        for alias in aliases:
            if norm == alias or norm.replace(" ", "") == alias.replace(" ", ""):
                return field
    return None


def _parse_labeled_group_fields(
    message: str,
    open_fields: set[str],
) -> dict[str, str]:
    """Split labeled multi-line replies into free-text field values."""
    if not message.strip() or not open_fields:
        return {}
    out: dict[str, str] = {}
    for raw_line in message.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = _LABELED_LINE_RE.match(line)
        if not match:
            continue
        field = _label_to_field(match.group("label"))
        if field is None or field not in open_fields:
            continue
        value = match.group("value").strip()
        if value and field not in out:
            out[field] = value
    return out


def _count_labeled_lines(message: str) -> int:
    return sum(1 for line in message.splitlines() if _LABELED_LINE_RE.match(line.strip()))


def _extracted_scalar(entry: Any) -> Any:
    if isinstance(entry, dict):
        return entry.get("value")
    return entry


def _planneddate_has_dates(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    return bool(
        str(value.get("worktimedate") or "").strip()
        and str(value.get("completionworkdate") or "").strip()
    )


def _planneddate_has_first_times(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    return bool(
        str(value.get("worktimefrom") or "").strip()
        and str(value.get("worktimeto") or "").strip()
    )


def _normalize_day_month_glue(message: str) -> str:
    """Turn ``24sep`` into ``24 sep`` so day-month regexes can match."""
    return re.sub(
        r"\b(\d{1,2})(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec|"
        r"january|february|march|april|june|july|august|september|october|"
        r"november|december)\b",
        r"\1 \2",
        message,
        flags=re.IGNORECASE,
    )


def _merge_planneddate_base(
    collected: dict[str, Any],
    extracted_entry: Any,
) -> dict[str, str]:
    from app.agents.schemas.work_permit_form_fields import EMPTY_PLANNEDDATE

    base = dict(EMPTY_PLANNEDDATE)
    existing = collected.get("planneddate")
    if isinstance(existing, dict):
        for key, raw in existing.items():
            if raw not in (None, ""):
                base[key] = str(raw)
    current = _schedule_value_from_extracted(extracted_entry) if extracted_entry else None
    if isinstance(current, dict):
        for key, raw in current.items():
            if raw not in (None, ""):
                base[key] = str(raw)
    return base


# Answers that genuinely mean "leave this blank".
_SKIP_ANSWER_RE = re.compile(
    r"^\s*(none|no|nope|skip|n/?a|nothing|no\s+\w+|not\s+now|-{1,2})\s*[.!]?\s*$",
    re.IGNORECASE,
)

# Ops / WP optionals that accept "" or empty period as an explicit skip.
_WP_OPTIONAL_SKIP_FIELDS: frozenset[str] = frozenset(
    {"secondperiod", "planned_second_period", "entry_gate", "notes"}
)


def _has_usable_value(extracted: dict[str, Any]) -> bool:
    """True when any extracted field carries a real (non-empty) value.

    Extraction maps junk and refusals to an empty string rather than omitting
    the field, so ``{"endDate": {"value": ""}}`` means "nothing usable" just as
    much as ``{}`` does.
    """
    for entry in extracted.values():
        value = entry.get("value") if isinstance(entry, dict) else entry
        if value not in (None, "", []):
            return True
    return False


def _extract_uuid(value: Any) -> UUID | None:
    if value is None:
        return None
    try:
        return value if isinstance(value, UUID) else UUID(str(value))
    except (ValueError, AttributeError):
        return None


@trace_node("field_extraction", "AGENT")
async def field_extraction_node(state: ServiceRequestState) -> dict[str, Any]:
    """LangGraph node: extract handover fields from the user message.

    The ``@trace_node`` decorator handles the outer node-level run span
    (BEFORE_NODE / AFTER_NODE snapshots + SUCCESS/FAILED finish).  This
    function additionally opens a child ``LLM`` run span for the LLM call
    itself so token counts, latency, and retry metadata are captured
    separately from the node span.
    """
    user_message: str = state.get("user_message") or ""
    workflow_stage: str | None = state.get("workflow_stage")
    conversation_history: list[dict] = state.get("conversation_history") or []  # type: ignore[assignment]
    missing_fields: list[str] = state.get("missing_fields") or []  # type: ignore[assignment]
    active_field: str | None = state.get("active_field")
    collected_data: dict[str, Any] = state.get("collected_data") or {}  # type: ignore[assignment]

    # ── Deterministic "change field X" redirect detection ─────────────────
    # Runs before LLM extraction: the extractor is primed toward active_field
    # and the previous question, so it tends to mis-bind correction phrases
    # like "I want to change the title" to whatever field is being collected.
    correction = detect_correction_intent(
        user_message, collected_data, active_field, workflow_stage
    )
    if correction is not None:
        target_field, has_inline_value = correction
        if not has_inline_value:
            # Bare redirect with nothing to extract yet — skip the LLM call
            # entirely and let missing_field_node ask for the target field.
            log.info(
                "field_extraction.correction_redirect",
                target_field=target_field,
                active_field=active_field,
            )
            return {
                "extracted_fields": {},
                "active_field": target_field,
                "correction_pending_field": target_field,
            }
        # Inline value present ("change the title to Foo") — let the LLM
        # extract it, but point it at the target field first so it becomes
        # missing_fields[0] / "Field currently being collected" in the prompt.
        active_field = target_field

    # ── Unspecified correction request ─────────────────────────────────────
    # "can you fix it" / "i want to change something" names no field. Letting
    # it reach the LLM while a field is being collected stores the phrase as
    # that field's value, and can blank a field the user never mentioned.
    #
    # Never while a free-text field is being collected. Notes, comments,
    # descriptions and titles are prose, and prose about a handover routinely
    # carries a correction verb — "please update the flooring before
    # handover", "we need to change the shutters". No field is named in those,
    # so there is nothing to redirect to: the only effect is that the answer is
    # discarded and the same question is re-asked, and retyping the note fails
    # the identical way, so the conversation cannot move on. While one of these
    # fields is active the message IS the value, not an instruction.
    if active_field not in _FREE_TEXT_FIELDS and is_unspecified_correction(
        user_message, workflow_stage
    ):
        log.info(
            "field_extraction.correction_unspecified",
            active_field=active_field,
            user_message=user_message[:80],
        )
        unspec: dict[str, Any] = {"extracted_fields": {}, "active_field": active_field}
        if active_field and collected_data.get(active_field) not in (None, "", []):
            unspec["correction_pending_field"] = active_field
        return unspec

    # ── Deterministic relative-date guard ──────────────────────────────────
    # "tomorrow" / "today" must never satisfy a date field — always re-ask
    # for an explicit date rather than risk the LLM resolving it silently.
    if active_field in _SINGLE_DATE_FIELDS and _is_bare_relative_date(user_message):
        log.info(
            "field_extraction.relative_date_rejected",
            active_field=active_field,
            user_message=user_message,
        )
        patch: dict[str, Any] = {"extracted_fields": {}, "active_field": active_field}
        # When the field already holds a value it is not "missing", so
        # missing_field_node would advance to the next gap and the user's
        # correction would vanish without a word. Re-arm the correction so the
        # same field is asked again with an explicit format hint.
        if collected_data.get(active_field) not in (None, "", []):
            patch["correction_pending_field"] = active_field
        return patch

    # Prefer the field asked on the previous turn. ``missing_fields`` is cleared
    # at load time, so without ``active_field`` bare dates map to the wrong key.
    extraction_hint: list[str] | None = None
    if active_field:
        extraction_hint = [active_field]
        for field in missing_fields:
            if field != active_field:
                extraction_hint.append(field)
    elif missing_fields:
        extraction_hint = list(missing_fields)

    # Look up the agent's extraction prompt — falls back to handover default when absent.
    active_agent: str | None = state.get("active_agent")
    workflow_cfg = get_workflow_config(active_agent)
    extraction_prompt: str | None = workflow_cfg.extraction_prompt if workflow_cfg else None

    # Resolve trace plumbing injected by TraceManager / lifespan.
    tm = state.get("trace_manager")
    trace_id: UUID | None = _extract_uuid(state.get("trace_id"))

    # ── 1. Open child LLM-call run span ───────────────────────────────────
    # ``_trace_node_run_id`` is injected by the ``@trace_node`` decorator
    # so the LLM span is nested under the field_extraction node span.
    node_run_id: UUID | None = _extract_uuid(state.get("_trace_node_run_id"))
    llm_run_id: UUID | None = None
    if tm is not None and trace_id is not None:
        try:
            llm_run_id = await tm.start_run(
                trace_id=trace_id,
                run_name="field_extraction_llm_call",
                run_type="LLM",
                parent_run_id=node_run_id,
            )
        except Exception:
            log.warning("field_extraction.start_llm_run.failed", exc_info=True)

    # ── 2. Extract fields via service ──────────────────────────────────────
    gateway = get_default_gateway()
    service = FieldExtractionService(gateway=gateway)

    llm_wall_start = time.monotonic()
    result, meta = await service.extract(
        user_message,
        workflow_stage,
        recent_history=conversation_history[-4:] if conversation_history else None,
        missing_fields=extraction_hint,
        system_prompt=extraction_prompt,
    )

    # ── 3. Emit LLM call metadata ──────────────────────────────────────────
    if tm is not None and trace_id is not None and llm_run_id is not None:
        effective_latency = meta.latency_ms or int((time.monotonic() - llm_wall_start) * 1000)
        structured_output: dict[str, Any] = {
            "summary": result.summary,
            "fields": result.to_state_dict(),
            "retry_count": meta.retry_count,
            "parse_success": meta.parse_success,
        }
        try:
            await tm.capture_llm_call(
                trace_id=trace_id,
                run_id=llm_run_id,
                provider=getattr(gateway, "provider_name", "openai"),
                model=gateway.model,
                temperature=Decimal("0"),
                prompt_name="handover_extraction_prompt",
                prompt_version="1.0",
                input_tokens=meta.input_tokens or None,
                output_tokens=meta.output_tokens or None,
                total_tokens=(meta.input_tokens + meta.output_tokens) or None,
                latency_ms=effective_latency,
                structured_output=structured_output,
                parse_success=meta.parse_success,
                parse_error=meta.parse_error,
            )
        except Exception:
            log.warning("field_extraction.capture_llm_call.failed", exc_info=True)

        try:
            await tm.finish_run(
                run_id=llm_run_id,
                output=structured_output,
                status="SUCCESS" if meta.parse_success else "FAILED",
                error_message=meta.parse_error,
            )
        except Exception:
            log.warning("field_extraction.finish_llm_run.failed", exc_info=True)

    # ── 4. Handle empty / failed extraction ───────────────────────────────
    if not meta.parse_success:
        log.warning(
            "field_extraction.parse_failed",
            parse_error=meta.parse_error,
            retry_count=meta.retry_count,
        )
        result_update: dict[str, Any] = {"extracted_fields": {}}
        if correction is not None:
            result_update["active_field"] = active_field
        return result_update

    # ── 5. Build state update ──────────────────────────────────────────────
    # Store the full {value, confidence} shape so merge_state_node can apply
    # a confidence threshold at merge time rather than discarding information.
    extracted_fields = result.to_state_dict()

    log.info(
        "field_extraction.complete",
        fields=list(extracted_fields.keys()),
        retry_count=meta.retry_count,
    )

    # Deterministic skip for Ops optionals: LLM often omits the field on "none",
    # leaving collected None so get_missing_fields keeps re-asking.
    if (
        active_field in _WP_OPTIONAL_SKIP_FIELDS
        and _SKIP_ANSWER_RE.match(user_message)
    ):
        from app.agents.schemas.work_permit_form_fields import EMPTY_PERIOD

        if active_field in {"secondperiod", "planned_second_period"}:
            skip_value: Any = dict(EMPTY_PERIOD)
        else:
            skip_value = ""
        log.info(
            "field_extraction.optional_skip",
            active_field=active_field,
        )
        extracted_fields[active_field] = {
            "value": skip_value
            if not isinstance(skip_value, dict)
            else json.dumps(skip_value),
            "confidence": 1.0,
        }

    # Deterministic rescue for free-text answers the LLM read as field
    # references rather than content. Verified live: "Post description" came
    # back as {"description": {"value": ""}} and "Post notes" as {}, which
    # blanked the field (auto-generating a replacement) and re-asked forever.
    # The user typed the value, so use it verbatim.
    #
    # Multi-field group asks (work details / identity): parse labeled lines
    # BEFORE dumping the whole message into active_field.
    _ui_fields_ft = set((state.get("response_ui") or {}).get("fields") or [])
    _group_open = {
        f
        for f in (set(missing_fields or []) | _ui_fields_ft)
        if f in _FREE_TEXT_FIELDS
    }
    if active_field in _FREE_TEXT_FIELDS:
        _group_open.add(active_field)
    _labeled = _parse_labeled_group_fields(user_message, _group_open)
    _labeled_applied = False
    for _lf, _lv in _labeled.items():
        entry = extracted_fields.get(_lf)
        current = _extracted_scalar(entry)
        if current in (None, "", []):
            log.info(
                "field_extraction.labeled_group_field",
                field=_lf,
                value=_lv[:80],
            )
            extracted_fields[_lf] = {"value": _lv, "confidence": 1.0}
            _labeled_applied = True

    # Do NOT bind a digits-only reply onto unit_type (group primary) — that is
    # a phone answer when phone is still open.
    _skip_full_dump = _labeled_applied or (
        len(_group_open) >= 2 and _count_labeled_lines(user_message) >= 2
    )
    if (
        active_field in _FREE_TEXT_FIELDS
        and not _SKIP_ANSWER_RE.match(user_message)
        and user_message.strip()
        and not _skip_full_dump
        and not (
            active_field == "unit_type"
            and _WP_BARE_PHONE_RE.match(user_message.strip())
            and (
                "phone" in (missing_fields or [])
                or "phone" in set((state.get("response_ui") or {}).get("fields") or [])
            )
        )
    ):
        entry = extracted_fields.get(active_field)
        current = entry.get("value") if isinstance(entry, dict) else entry
        if current in (None, "", []):
            log.info(
                "field_extraction.free_text_fallback",
                active_field=active_field,
                user_message=user_message[:80],
            )
            extracted_fields[active_field] = {
                "value": user_message.strip(),
                "confidence": 1.0,
            }

    # typeofwork is a nested widget (not in _FREE_TEXT_FIELDS), but suffers the
    # same "field-name as reference" failure: "Type of work - Stock Removal"
    # comes back empty because the LLM reads "Type of work" as a pointer to the
    # field, not as content.  Strip the prefix deterministically and use the
    # remainder so the user is not stuck in an infinite re-ask loop.
    if (
        active_field == "typeofwork"
        and not _SKIP_ANSWER_RE.match(user_message)
        and user_message.strip()
    ):
        entry = extracted_fields.get("typeofwork")
        current = entry.get("value") if isinstance(entry, dict) else entry
        if current in (None, "", []):
            text = re.sub(
                r"^[Tt]ype\s+of\s+work\s*[-:–—]\s*", "", user_message.strip()
            ).strip()
            if text:
                log.info(
                    "field_extraction.typeofwork_fallback",
                    value=text,
                )
                extracted_fields["typeofwork"] = {"value": text, "confidence": 1.0}

    # firstperiod / secondperiod: deterministic rescue for "4pm to 5pm" / "2 to 3pm"
    # patterns the LLM may miss when both schedule fields are asked together.
    # load_session often clears missing_fields / response_ui — expand open set
    # from collected incompleteness when any schedule field is already in scope.
    _ui_fields = (state.get("response_ui") or {}).get("fields") or []
    _schedule_open = set(missing_fields or []) | set(_ui_fields)
    if active_field:
        _schedule_open.add(active_field)
    _collected = state.get("collected_data") or {}
    if _schedule_open & _WP_SCHEDULE_FIELDS:
        from app.agents.schemas.work_permit_form_fields import (
            form_spec_for,
            is_tenant_field_missing,
        )
        from app.agents.schemas.work_permit_schema import resolve_wp_sub_category

        _subtype = resolve_wp_sub_category(
            state.get("sub_category"),
            {
                **_collected,
                "work_permit_type": _collected.get("work_permit_type")
                or state.get("sub_category"),
            },
        )
        _spec = form_spec_for(_subtype)
        _ft = (_spec.field_types if _spec else {}) or {}
        for _sf in _WP_SCHEDULE_FIELDS:
            if is_tenant_field_missing(
                _sf,
                _collected.get(_sf),
                field_type=_ft.get(_sf),
                optional=_sf in {"secondperiod", "planned_second_period"},
            ):
                _schedule_open.add(_sf)

    for _period_field in ("firstperiod", "secondperiod", "planned_second_period"):
        if _period_field not in _schedule_open:
            continue
        _period_entry = extracted_fields.get(_period_field)
        _period_val = (
            _period_entry.get("value") if isinstance(_period_entry, dict) else _period_entry
        )
        if isinstance(_period_val, str) and _period_val.strip().startswith("{"):
            try:
                _period_val = json.loads(_period_val)
            except (json.JSONDecodeError, TypeError, ValueError):
                pass
        _period_empty = (
            not _period_val
            or (
                isinstance(_period_val, str)
                and not any(c.isdigit() for c in _period_val)
            )
            or (
                isinstance(_period_val, dict)
                and not str(_period_val.get("from") or "").strip()
            )
        )
        if _period_empty:
            parsed_period = _extract_time_period(user_message)
            if parsed_period:
                log.info(
                    "field_extraction.period_fallback",
                    field=_period_field,
                    value=parsed_period,
                )
                extracted_fields[_period_field] = {
                    "value": json.dumps(parsed_period),
                    "confidence": 1.0,
                }
                break  # only bind the range to the first missing period field

    # effective_starting: deterministic rescue for "23 to 24 september" (+ time).
    # Also copy datefrom → dateto when LLM returned a single date only.
    if "effective_starting" in _schedule_open and not _SKIP_ANSWER_RE.match(
        user_message
    ):
        entry = extracted_fields.get("effective_starting")
        inner: Any = None
        if isinstance(entry, dict):
            inner = entry.get("value")
            if isinstance(inner, str) and inner.strip().startswith("{"):
                try:
                    inner = json.loads(inner)
                except (json.JSONDecodeError, TypeError, ValueError):
                    inner = None
        datefrom = ""
        dateto = ""
        if isinstance(inner, dict):
            datefrom = str(inner.get("datefrom") or "").strip()
            dateto = str(inner.get("dateto") or "").strip()
            if datefrom and not dateto:
                log.info(
                    "field_extraction.effective_starting_single_date",
                    datefrom=datefrom,
                )
                entry = dict(entry) if isinstance(entry, dict) else {}
                entry["value"] = {"datefrom": datefrom, "dateto": datefrom}
                entry.setdefault("confidence", 1.0)
                extracted_fields["effective_starting"] = entry
                datefrom = dateto = datefrom
        parsed_dates = _extract_effective_starting(user_message)
        if parsed_dates:
            parsed_from = str(parsed_dates.get("datefrom") or "").strip()
            parsed_to = str(parsed_dates.get("dateto") or "").strip()
            # Apply when LLM left a hole, or collapsed a real range to one day.
            should_apply = (not datefrom or not dateto) or (
                parsed_from
                and parsed_to
                and parsed_from != parsed_to
                and datefrom == dateto
            )
            if should_apply:
                log.info(
                    "field_extraction.effective_starting_fallback",
                    value=parsed_dates,
                )
                extracted_fields["effective_starting"] = {
                    "value": json.dumps(parsed_dates),
                    "confidence": 1.0,
                }

    # planneddate: accept date-only ("24 to 26 sep") then times ("9 to 5").
    # Preserve already-collected dates/times when only one half arrives.
    _planneddate_open = (
        active_field == "planneddate"
        or "planneddate" in (missing_fields or [])
        or "planneddate" in set(_ui_fields)
    )
    if (
        _planneddate_open
        and not _SKIP_ANSWER_RE.match(user_message)
        and user_message.strip()
    ):
        planned_base = _merge_planneddate_base(
            _collected, extracted_fields.get("planneddate")
        )
        date_msg = _normalize_day_month_glue(_strip_ordinal_days(user_message))
        parsed_dates = _extract_effective_starting(date_msg)
        changed = False
        if parsed_dates:
            planned_base["worktimedate"] = str(parsed_dates["datefrom"])
            planned_base["completionworkdate"] = str(parsed_dates["dateto"])
            changed = True
            log.info(
                "field_extraction.planneddate_dates",
                value=parsed_dates,
            )
        # Time-only (or dates+times) reply: fill first period when missing.
        if not _planneddate_has_first_times(planned_base):
            # Prefer am/pm or HH:MM; allow bare "9 to 5" once dates are known
            # (avoids treating "24 to 26" as times).
            parsed_period = _extract_time_period(user_message)
            if (
                not parsed_period
                and _planneddate_has_dates(planned_base)
                and not parsed_dates
            ):
                parsed_period = _extract_time_period(
                    user_message, allow_bare_hours=True
                )
            if parsed_period:
                planned_base["worktimefrom"] = parsed_period["from"]
                planned_base["worktimeto"] = parsed_period["to"]
                changed = True
                log.info(
                    "field_extraction.planneddate_times",
                    value=parsed_period,
                )
        if changed:
            extracted_fields["planneddate"] = {
                "value": json.dumps(planned_base),
                "confidence": 1.0,
            }

    # Drop incomplete schedule stubs that would wipe a complete collected value.
    _drop_incomplete_schedule_overwrites(extracted_fields, _collected)

    # Ops contact group: "Store, 8486697081" → unit_type + phone when LLM missed.
    _apply_wp_unit_type_phone_split(
        extracted_fields,
        user_message=user_message,
        active_field=active_field,
        missing_fields=missing_fields,
        response_ui=state.get("response_ui"),
        workflow_stage=workflow_stage,
        active_agent=state.get("active_agent"),
    )
    # Singleton phone ask: "8486697081" with no unit_type still open.
    _apply_wp_bare_phone(
        extracted_fields,
        user_message=user_message,
        active_field=active_field,
        missing_fields=missing_fields,
        response_ui=state.get("response_ui"),
        workflow_stage=workflow_stage,
        active_agent=state.get("active_agent"),
    )

    final_update: dict[str, Any] = {"extracted_fields": extracted_fields}
    if correction is not None:
        final_update["active_field"] = active_field

    # Nothing usable came back while the bot was waiting on a field that
    # ALREADY holds a value — i.e. a correction re-ask answered with something
    # unparseable ("not a real date"). That field is not "missing", so the
    # normal flow would move on to the next gap and the user's correction
    # would vanish without a word; worse, their next message can then bind to
    # the wrong field. Re-arm the correction so the same field is asked again.
    if (
        not _has_usable_value(extracted_fields)
        and active_field
        and collected_data.get(active_field) not in (None, "", [])
        and active_field not in missing_fields
    ):
        log.info("field_extraction.correction_reasked", active_field=active_field)
        final_update["active_field"] = active_field
        final_update["correction_pending_field"] = active_field
    return final_update


# "Store, 8486697081" / "Retail, +966501234567" / "F&B 8486697081"
_WP_UNIT_PHONE_COMMA_RE = re.compile(
    r"^\s*(?P<unit>[^,]+?)\s*,\s*(?P<phone>\+?\d[\d\s\-()]{5,})\s*$"
)
_WP_UNIT_PHONE_SPACE_RE = re.compile(
    r"^\s*(?P<unit>[A-Za-z][&/A-Za-z0-9 ]{0,40}?)\s+(?P<phone>\+?\d{7,15})\s*$"
)
# Bare contact number after unit_type is already filled.
_WP_BARE_PHONE_RE = re.compile(r"^\s*(?P<phone>\+?\d[\d\s\-()]{6,})\s*$")


def _field_entry_value(extracted: dict[str, Any], key: str) -> Any:
    entry = extracted.get(key)
    if isinstance(entry, dict):
        return entry.get("value")
    return entry


def _apply_wp_unit_type_phone_split(
    extracted_fields: dict[str, Any],
    *,
    user_message: str,
    active_field: str | None,
    missing_fields: list[str],
    response_ui: dict[str, Any] | None,
    workflow_stage: str | None,
    active_agent: str | None,
) -> None:
    """Fill unit_type + phone from a single free-text answer when the LLM omitted them."""
    if (active_agent or "") != "work_permit_agent":
        return
    if (workflow_stage or "") not in {"CREATE_WORK_PERMIT", "CREATE_SR"}:
        return
    ui_fields = set((response_ui or {}).get("fields") or [])
    open_fields = set(missing_fields or []) | ui_fields
    if active_field:
        open_fields.add(active_field)
    if "unit_type" not in open_fields or "phone" not in open_fields:
        return

    msg = (user_message or "").strip()
    if not msg:
        return
    match = _WP_UNIT_PHONE_COMMA_RE.match(msg) or _WP_UNIT_PHONE_SPACE_RE.match(msg)
    if not match:
        return
    unit = match.group("unit").strip()
    phone = re.sub(r"[\s\-()]", "", match.group("phone").strip())
    if not unit or not phone:
        return

    filled = False
    if _field_entry_value(extracted_fields, "unit_type") in (None, "", []):
        extracted_fields["unit_type"] = {"value": unit, "confidence": 1.0}
        filled = True
    if _field_entry_value(extracted_fields, "phone") in (None, "", []):
        extracted_fields["phone"] = {"value": phone, "confidence": 1.0}
        filled = True
    if filled:
        log.info(
            "field_extraction.wp_unit_phone_split",
            unit_type=unit,
            phone=phone[:4] + "…",
        )


def _apply_wp_bare_phone(
    extracted_fields: dict[str, Any],
    *,
    user_message: str,
    active_field: str | None,
    missing_fields: list[str],
    response_ui: dict[str, Any] | None,
    workflow_stage: str | None,
    active_agent: str | None,
) -> None:
    """Bind a digits-only reply to ``phone`` when that field is still open.

    Live loop: after unit_type was filled the bot asked for phone alone; the
    user typed ``84884848478``; the LLM often returned ``{}`` (phone is not a
    nested widget and was not previously in the free-text rescue list). Response
    generation still thanked them for the phone because the digits were in the
    utterance — then missing_field re-asked forever.
    """
    if (active_agent or "") != "work_permit_agent":
        return
    if (workflow_stage or "") not in {"CREATE_WORK_PERMIT", "CREATE_SR"}:
        return
    ui_fields = set((response_ui or {}).get("fields") or [])
    open_fields = set(missing_fields or []) | ui_fields
    if active_field:
        open_fields.add(active_field)
    if "phone" not in open_fields:
        return
    if _field_entry_value(extracted_fields, "phone") not in (None, "", []):
        return

    msg = (user_message or "").strip()
    if not msg:
        return
    # Combined answers belong to the unit+phone splitter.
    if _WP_UNIT_PHONE_COMMA_RE.match(msg) or _WP_UNIT_PHONE_SPACE_RE.match(msg):
        return
    match = _WP_BARE_PHONE_RE.match(msg)
    if not match:
        return
    phone = re.sub(r"[\s\-()]", "", match.group("phone").strip())
    if len(re.sub(r"\D", "", phone)) < 7:
        return
    extracted_fields["phone"] = {"value": phone, "confidence": 1.0}
    log.info("field_extraction.wp_bare_phone", phone=phone[:4] + "…")
