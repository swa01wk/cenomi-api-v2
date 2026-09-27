"""Percentage suggestion chips for the single Fitout Progress question.

The flow asks one thing — how complete the fit-out is — and the number it
offers is not a guess: it is the same arithmetic the platform scheduler
(``ComputeFitoutProgressDefault`` in ``fitout_progress_scheduler.go``) and the
web form (``fitoutProgressDefaults.ts``) already use to pre-fill the field, so
the chip the assistant shows is the value the form would have shown.

    elapsed % = round(100 * (now - start) / (end - start)), clamped to [0, 100]
                0   when either date is missing/invalid, when end <= start,
                    or when now <= start
                100 once now >= end

Around that lead value sit quarter-ladder chips (0/25/50/75/100) so the whole
question can be answered in one tap, and a recorded percentage on the lease
raises the lead — fit-out progress does not go backwards.

Chips carry their value as plain text with no ``action``, following
``inspection_suggestions_service``: the chip's number then shows in the
transcript as the user's own answer, and the existing ``labeled_options``
renderer needs no new action code. ``parse_percentage`` is what lets the entry
node bind that answer without an LLM round-trip.

Read-only and best effort: no usable dates simply means the plain ladder and a
question asked without a suggestion.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

# Wall-clock timezone the portal (and therefore the user) thinks in.
_PORTAL_TZ = ZoneInfo("Asia/Riyadh")

# The lease sync stores Go's zero-value timestamp rather than NULL.
_ZERO_DATE_PREFIX = "0001-01-01"

_DATE_INPUT_FORMATS: tuple[str, ...] = (
    "%Y-%m-%d",
    "%d/%m/%Y",
    "%d-%m-%Y",
    "%Y/%m/%d",
)

# Fit-out window resolution, in preference order.
#
# ``fitout_start_date`` / ``fitout_end_date`` are the platform payload's own key
# names and are present when an existing SR is hydrated on the approve path.
# The rest mirror the scheduler: start = actual → planned → base,
# end = base ("original") → planned.
START_DATE_CANDIDATES: tuple[str, ...] = (
    "fitout_start_date",
    "actual_fitout_start_date",
    "planned_fitout_start_date",
    "base_fitout_start_date",
)
END_DATE_CANDIDATES: tuple[str, ...] = (
    "fitout_end_date",
    "base_fitout_end_date",
    "planned_fitout_end_date",
)

# Where the recorded percentage may be found: the lease lookup (once the
# platform DTO carries it) or the stored payload of an open SR.
RECORDED_KEYS: tuple[str, ...] = (
    "recorded_fitout_progress",
    "fitout_progress",
)

PERCENT_MIN = 0.0
PERCENT_MAX = 100.0

# Quarter ladder offered around the lead value.
LADDER: tuple[int, ...] = (0, 25, 50, 75, 100)

# Chips beyond four crowd the message on a phone.
MAX_OPTIONS = 4

# Words people use instead of a number. Kept small and literal on purpose —
# anything not here falls through to LLM extraction rather than being guessed.
_WORD_VALUES: dict[str, float] = {
    "complete": 100.0,
    "completed": 100.0,
    "done": 100.0,
    "finished": 100.0,
    "hundred": 100.0,
    "one hundred": 100.0,
    "a hundred": 100.0,
    "full": 100.0,
    "fully complete": 100.0,
    "not started": 0.0,
    "not yet started": 0.0,
    "none": 0.0,
    "nothing": 0.0,
    "zero": 0.0,
    "half": 50.0,
    "halfway": 50.0,
    "half way": 50.0,
    "half done": 50.0,
    "quarter": 25.0,
    "three quarters": 75.0,
}

# A bare percentage, with or without a % sign or the word "percent".
_PERCENT_RE = re.compile(
    r"^(?P<num>\d{1,3}(?:[.,]\d{1,2})?)\s*(?:%|percent|percentage|pct)?$",
    re.IGNORECASE,
)


# ── Dates ─────────────────────────────────────────────────────────────────────


def now_in_portal_tz() -> datetime:
    """Now as the user sees it (Asia/Riyadh wall clock), naive."""
    return datetime.now(_PORTAL_TZ).replace(tzinfo=None)


def parse_lease_date(raw: Any) -> date | None:
    """Best-effort parse of a lease/payload date; ``None`` when unusable.

    ``0001-01-01…`` is the Go zero value and counts as unset, exactly as
    ``parseLeaseDate`` treats it on the web form.
    """
    text = str(raw or "").strip()
    if not text or text.startswith(_ZERO_DATE_PREFIX):
        return None
    if "T" in text:
        text = text.split("T", 1)[0]
    if " " in text:
        text = text.split(" ", 1)[0]
    for fmt in _DATE_INPUT_FORMATS:
        try:
            parsed = datetime.strptime(text, fmt).date()
        except ValueError:
            continue
        return parsed if parsed.year > 1 else None
    return None


def resolve_fitout_date(*candidates: Any) -> str:
    """First usable candidate as ``YYYY-MM-DD``, else ``""``."""
    for candidate in candidates:
        parsed = parse_lease_date(candidate)
        if parsed:
            return parsed.isoformat()
    return ""


def resolve_window(collected: dict[str, Any]) -> tuple[str, str]:
    """Return the lease's ``(start, end)`` fit-out dates as ISO strings."""
    start = resolve_fitout_date(*(collected.get(k) for k in START_DATE_CANDIDATES))
    end = resolve_fitout_date(*(collected.get(k) for k in END_DATE_CANDIDATES))
    return start, end


def display_date(raw: Any) -> str:
    """``15 Jul 2026`` for a message, or the raw text when unparseable."""
    parsed = parse_lease_date(raw)
    if not parsed:
        return str(raw or "").strip()
    return f"{parsed.day} {parsed.strftime('%b %Y')}"


# ── The platform's arithmetic ─────────────────────────────────────────────────


def compute_fitout_progress_default(
    start_date: Any,
    end_date: Any,
    now: datetime | None = None,
) -> int:
    """Elapsed-time percentage of the fit-out window, clamped to ``[0, 100]``.

    Mirrors ``ComputeFitoutProgressDefault`` (Go) and
    ``computeFitoutProgressDefault`` (web form). ``0`` when either date is
    missing or invalid, when the window is not positive, or before it starts;
    ``100`` from the end date onwards.

    Rounding is half-away-from-zero to match Go's ``math.Round`` and JS's
    ``Math.round``; Python's built-in ``round`` is banker's rounding and would
    disagree on exact halves.
    """
    start = parse_lease_date(start_date)
    end = parse_lease_date(end_date)
    if not start or not end:
        return 0

    start_dt = datetime.combine(start, time.min)
    end_dt = datetime.combine(end, time.min)
    now_dt = now or now_in_portal_tz()

    total = (end_dt - start_dt).total_seconds()
    if total <= 0:
        return 0
    elapsed = (now_dt - start_dt).total_seconds()
    if elapsed <= 0:
        return 0
    if elapsed >= total:
        return 100
    return math.floor(elapsed / total * 100 + 0.5)


# ── Percentage parsing / formatting ───────────────────────────────────────────


def parse_percentage(raw: Any) -> float | None:
    """Parse a user-supplied percentage, or ``None`` when it is not one.

    Accepts ``82``, ``82%``, ``82 percent``, ``82.5`` and a short list of words
    ("complete", "half", "not started"). Values outside ``[0, 100]`` are *not*
    coerced — they return ``None`` so the caller raises the validation error
    and re-asks, rather than silently recording a clamped number.
    """
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        value = float(raw)
        return round(value, 2) if PERCENT_MIN <= value <= PERCENT_MAX else None

    text = str(raw or "").strip()
    if not text:
        return None

    lowered = text.lower().rstrip(".!")
    if lowered in _WORD_VALUES:
        return _WORD_VALUES[lowered]

    match = _PERCENT_RE.match(text)
    if not match:
        return None
    number = match.group("num").replace(",", ".")
    try:
        value = float(number)
    except ValueError:
        return None
    if not PERCENT_MIN <= value <= PERCENT_MAX:
        return None
    return round(value, 2)


# A message whose only content is a percentage: "90", "90%", "make it 90",
# "~82.5". Anything with a second number, or words after the number, is left to
# the LLM — "check SR 8517 and fix it" must never read as 85%.
_PERCENT_ANSWER_RE = re.compile(
    r"^[^\d]{0,20}(?P<num>\d{1,3}(?:[.,]\d{1,2})?)\s*(?:%|percent|percentage|pct)?[\s.!]*$",
    re.IGNORECASE,
)


def parse_percentage_answer(message: Any) -> float | None:
    """Parse a message that is *only* a percentage answer, else ``None``.

    Used where the percentage is already the open question — the chips and the
    review card — so a bare number needs no LLM round-trip. Stricter than it
    looks: the number must be the only one in the message, which is what keeps
    an SR id or a lease code from being read as a reading.
    """
    text = str(message or "").strip()
    if not text:
        return None
    direct = parse_percentage(text)
    if direct is not None:
        return direct
    match = _PERCENT_ANSWER_RE.match(text)
    if not match:
        return None
    return parse_percentage(match.group("num"))


def format_percentage(value: float | None) -> str:
    """``"82"`` / ``"82.5"`` — the string shape the platform payload expects."""
    if value is None:
        return ""
    as_float = float(value)
    if as_float.is_integer():
        return str(int(as_float))
    return f"{round(as_float, 2):g}"


def recorded_percentage(collected: dict[str, Any]) -> float | None:
    """The percentage already recorded for this lease, when we know it."""
    for key in RECORDED_KEYS:
        if key in collected:
            parsed = parse_percentage(collected.get(key))
            if parsed is not None:
                return parsed
    return None


def prefilled_percentage(collected: dict[str, Any]) -> float | None:
    """The percentage that arrived with the request rather than from this user.

    An approver's SR is hydrated with the number the scheduler (or whoever
    raised it) put there, and until they answer in this session that value is
    context, not their answer — which is what ``_progress_confirmed`` tracks.
    """
    from app.agents.schemas.fitout_progress_schema import (
        PROGRESS_CONFIRMED_KEY,
        PROGRESS_FIELD,
    )

    if collected.get(PROGRESS_CONFIRMED_KEY):
        return None
    return parse_percentage(collected.get(PROGRESS_FIELD))


# ── Suggestion ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ProgressSuggestion:
    """What to offer for the one question, and what to say about it."""

    start: str
    end: str
    elapsed: int | None
    recorded: float | None
    lead: float | None
    lead_source: str | None  # "suggested" | "recorded" | None
    options: tuple[float, ...]

    @property
    def has_window(self) -> bool:
        return bool(self.start and self.end)


def _ladder_around(lead: float) -> list[float]:
    """Quarter neighbours of *lead*: nearest below, nearest above, then 100."""
    below = [q for q in LADDER if q < lead]
    above = [q for q in LADDER if q > lead]
    ordered: list[float] = []
    if below:
        ordered.append(float(max(below)))
    if above:
        ordered.append(float(min(above)))
    ordered.append(float(PERCENT_MAX))
    return ordered


def suggest_percentages(
    collected: dict[str, Any],
    now: datetime | None = None,
    *,
    prefilled: float | None = None,
) -> ProgressSuggestion:
    """Build the lead value and the chip ladder for *collected*.

    *prefilled* is a percentage that came with the request rather than from the
    lease — the value on an approver's open SR. It is treated exactly like a
    recorded one: shown as context, and a floor for the suggestion.
    """
    start, end = resolve_window(collected)
    recorded = recorded_percentage(collected)
    if recorded is None:
        recorded = prefilled

    elapsed: int | None = None
    if start and end:
        elapsed = compute_fitout_progress_default(start, end, now)

    lead: float | None = None
    lead_source: str | None = None
    if elapsed is not None and (recorded is None or recorded <= elapsed):
        lead, lead_source = float(elapsed), "suggested"
    elif recorded is not None:
        # Progress does not go backwards: a lease already at 90% is not 82%.
        lead, lead_source = recorded, "recorded"

    if lead is None:
        options = [float(q) for q in LADDER if q > PERCENT_MIN]
    else:
        options = [lead, *_ladder_around(lead)]

    deduped: list[float] = []
    for value in options:
        if PERCENT_MIN <= value <= PERCENT_MAX and value not in deduped:
            deduped.append(value)

    return ProgressSuggestion(
        start=start,
        end=end,
        elapsed=elapsed,
        recorded=recorded,
        lead=lead,
        lead_source=lead_source,
        options=tuple(deduped[:MAX_OPTIONS]),
    )


def _chip_label(value: float, *, lead: float | None, lead_source: str | None) -> str:
    """Short chip label; the suffix says why the value is being offered."""
    text = f"{format_percentage(value)}%"
    if value == PERCENT_MAX:
        return f"{text} · complete"
    if value == PERCENT_MIN:
        return f"{text} · not started"
    if lead is not None and value == lead and lead_source:
        return f"{text} · {lead_source}"
    return text


# Actions the approve turn's decision chips carry. The entry node keys off
# these, and the graph routes them past field_extraction — a decision is not a
# value to be re-read by the LLM.
APPROVE_ACTION = "confirm_approve_fitout_progress"
REJECT_ACTION = "reject_fitout_progress"


def build_progress_options(
    collected: dict[str, Any],
    now: datetime | None = None,
    *,
    prefilled: float | None = None,
) -> list[dict[str, Any]]:
    """Chip options for the percentage question — value-only, no actions."""
    suggestion = suggest_percentages(collected, now, prefilled=prefilled)
    return [
        {
            "label": _chip_label(
                value, lead=suggestion.lead, lead_source=suggestion.lead_source
            ),
            "value": format_percentage(value),
        }
        for value in suggestion.options
    ]


def build_approve_options(
    collected: dict[str, Any],
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Chips for the approver's turn: the decision, then the alternatives.

    "Approve 65%" carries an action because it is a decision — tapping it must
    not go through extraction, which would read the number and leave the
    approval itself unspoken. The percentage chips stay value-only: supplying a
    different number *is* the update-and-approve the web form's single button
    performs, so it re-renders the card rather than deciding anything.
    """
    stored = prefilled_percentage(collected)
    options: list[dict[str, Any]] = []
    if stored is not None:
        options.append(
            {
                "label": f"Approve {format_percentage(stored)}%",
                "value": f"approve {format_percentage(stored)}%",
                "action": APPROVE_ACTION,
            }
        )
    # Two alternatives at most: with the decision chips either side, a longer
    # row stops reading as a choice.
    for option in build_progress_options(collected, now, prefilled=stored):
        if stored is not None and option["value"] == format_percentage(stored):
            continue
        options.append(option)
        if len(options) >= 3:
            break
    options.append({"label": "Reject", "value": "reject", "action": REJECT_ACTION})
    return options


def build_fitout_progress_decorations(
    collected: dict[str, Any],
    now: datetime | None = None,
    *,
    stage: str = "CREATE_SR",
) -> dict[str, Any]:
    """``response_decorations`` for the percentage question."""
    options = (
        build_approve_options(collected, now)
        if stage == "APPROVE_SR"
        else build_progress_options(collected, now)
    )
    if not options:
        return {}
    return {"suggestions": {"kind": "labeled_options", "options": options}}


# ── The question itself ───────────────────────────────────────────────────────


def build_progress_question(
    collected: dict[str, Any],
    *,
    stage: str = "CREATE_SR",
    sr_id: str | None = None,
    now: datetime | None = None,
) -> str:
    """The one question, verbatim.

    Built here rather than left to the LLM rewrite because the chips carry the
    answers: ``field_questions`` holds an instruction *about* asking, and when
    that rewrite does not land the chips render with no question above them.
    """
    suggestion = suggest_percentages(
        collected,
        now,
        # On the approve path the stored percentage is the thing being approved,
        # so the question has to name it.
        prefilled=prefilled_percentage(collected) if stage == "APPROVE_SR" else None,
    )
    parts: list[str] = []

    if suggestion.has_window:
        window = (
            f"Fit-out ran {display_date(suggestion.start)} → "
            f"{display_date(suggestion.end)}"
            if stage == "APPROVE_SR"
            else f"Fit-out started {display_date(suggestion.start)} and is due "
            f"{display_date(suggestion.end)}"
        )
        if suggestion.elapsed is not None:
            parts.append(
                f"{window}, so about **{suggestion.elapsed}%** of the window "
                f"has elapsed."
            )
        else:
            parts.append(f"{window}.")

    if stage == "APPROVE_SR":
        recorded = suggestion.recorded
        lead_in = f"SR {sr_id}" if sr_id else "This request"
        if recorded is not None:
            parts.insert(
                0, f"{lead_in} records **{format_percentage(recorded)}%**."
            )
            parts.append(
                f"Approve {format_percentage(recorded)}%, or give me the right "
                f"percentage."
            )
        else:
            parts.insert(0, f"{lead_in} has no percentage recorded yet.")
            parts.append("How complete is the fit-out today?")
        return " ".join(parts)

    if suggestion.recorded is not None:
        parts.append(
            f"The lease currently records "
            f"**{format_percentage(suggestion.recorded)}%**."
        )
    parts.append("How complete is the fit-out today?")
    if not suggestion.has_window:
        parts.append("Give me a percentage between 0 and 100.")
    else:
        parts.append("Tap a percentage or type your own (0-100).")
    return " ".join(parts)
