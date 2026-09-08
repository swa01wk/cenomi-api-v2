"""Merge partial updates into canonical draft state.

Merge rules
-----------
1. ``extracted_fields`` values are merged into ``collected_data``.
2. Backend-derived protected fields are **never** overwritten by LLM extraction.
3. Low-confidence extractions (below ``_CONFIDENCE_THRESHOLD``) are silently skipped.
4. When an extraction changes a field that was already known, the old value and
   timestamp are appended to ``collected_data["_corrections"]`` so every user
   correction is auditable.
5. ``collected_data`` is the single source of truth for the request draft.
"""

from __future__ import annotations

import datetime
import re
from typing import Any

from app.agents.graph.state import ServiceRequestState
from app.observability.decorators import trace_node

# Provenance of every field the short flow generated, kept alongside the values
# in collected_data (persisted with the draft) so the review card can label
# them and detect a later user edit.
_AI_FIELD_META_KEY = "_ai_field_meta"

# RDD upload type codes (mirrored from handover_schema to avoid circular import).
_RDD_DOC_TYPE_MEETING = "DR_SR_HANDOVER_REPORT"
_RDD_DOC_TYPE_REJECTED = "SR_REJECTED_HANDOVER_REPORT"
_RDD_DOC_TYPE_OTHER = "SR_HANDOVER_OTHER"
_RDD_REJECTED_STATUS = "DISAPPROVED"

# ---------------------------------------------------------------------------
# Backend-derived protected fields
# ---------------------------------------------------------------------------
# These are populated exclusively by backend API lookups (lease resolution,
# tenant profile fetch, etc.).  LLM extraction output must never overwrite them.

BACKEND_PROTECTED_FIELDS: frozenset[str] = frozenset(
    {
        "tenant_profile_id",
        "property_id",
        "brand_id",
        "lease_id",
        "contract_id",
        "unit_codes",
        "city",
        "contracted_area",
        "lease_brand_mall",
    }
)

# Fields that are auto-computed from other collected values during merge.
# These are never asked of the user directly.
BACKEND_COMPUTED_FIELDS: frozenset[str] = frozenset({
    "expected_handover_date",
    "trading_date",    # auto-computed as fitout_end_date + 8 days in RDD_REVIEW
})

# ---------------------------------------------------------------------------
# Date utility
# ---------------------------------------------------------------------------

_AUTO_COMPUTE_DAYS = 7           # expected_handover_date = unit_readiness_date + 7 days
_AUTO_COMPUTE_TRADING_DAYS = 8  # trading_date = fitout_end_date + 8 days


def _add_days(date_str: str, days: int) -> str:
    """Add *days* to *date_str* and return an ISO 8601 string.

    Accepts YYYY-MM-DD, DD/MM/YYYY, and DD-MM-YYYY input formats.
    Returns the original string unchanged when parsing fails or when
    the string is empty.
    """
    if not date_str:
        return date_str

    # Try YYYY-MM-DD first (ISO 8601)
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            dt = datetime.date.fromisoformat(date_str) if fmt == "%Y-%m-%d" else datetime.datetime.strptime(date_str, fmt).date()
            return (dt + datetime.timedelta(days=days)).strftime("%Y-%m-%d")
        except ValueError:
            continue

    return date_str


# LLM-extractable date fields whose year must be coerced to the current year
# when the user did not type an explicit 4-digit year.  gpt-4o-mini frequently
# stamps a stale year (e.g. 2023) on bare dates like "30 August" even though the
# extraction prompt says to assume the current year.  This deterministic guard
# repairs that so downstream RDD date-order validation does not loop.
_USER_DATE_FIELDS: frozenset[str] = frozenset({
    "startDate",
    "endDate",
    "unit_readiness_date",
    "expected_handover_date",
    "actual_handover_date",
    "fitout_start_date",
    "fitout_end_date",
    # Path B (HANDED_OVER / Already Trading) date fields
    "actual_shop_handover_date",
    "actual_fitout_start_date",
    "actual_fitout_end_date",
    "actual_trading_date",
    # Fitout Confirmation workflow date fields
    "actual_fitout_complete_date",
    "planned_fitout_start_date",
    "planned_fitout_end_date",
    "planned_fitout_complete_date",
    "planned_trading_date",
})

# Time-only fields — stored as HH:MM; must NOT pass through _to_date_only.
_USER_TIME_FIELDS: frozenset[str] = frozenset(
    {"inspectionTime", "inspectionEndTime"}
)

_EXPLICIT_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")

# Any digit in the user's message means they may have typed a date. Used to gate
# year coercion so a bare "yes"/"confirm" never rewrites a backend-supplied date.
_DATE_TOKEN_RE = re.compile(r"\d")

# Phrases the extractor resolves against today's date rather than reading a
# year off the message.
_RELATIVE_DATE_RE = re.compile(
    r"\b(today|tomorrow|day after tomorrow|next\s+(week|month|mon|tue|wed|thu|fri|sat|sun)\w*"
    r"|in\s+\d+\s+days?|\d+\s+days?\s+from\s+now)\b",
    re.IGNORECASE,
)


def _coerce_year_if_absent(value: str, message_has_year: bool) -> str:
    """Force *value*'s year to the current year when the user gave no explicit year.

    Only applies to ISO ``YYYY-MM-DD`` values.  When ``message_has_year`` is
    True (the user typed a 4-digit year) the value is returned unchanged so an
    intentional past/future year is respected.
    """
    if message_has_year or not value:
        return value
    try:
        parsed = datetime.date.fromisoformat(value[:10] if len(value) >= 10 and value[4] == "-" else value)
    except (ValueError, TypeError):
        return value
    current_year = datetime.date.today().year
    if parsed.year == current_year:
        return value
    try:
        return parsed.replace(year=current_year).strftime("%Y-%m-%d")
    except ValueError:
        # Feb 29 → non-leap current year; fall back to original.
        return value


def _to_date_only(value: Any) -> Any:
    """Normalise ISO datetimes to ``YYYY-MM-DD`` for chatbot date fields."""
    if not isinstance(value, str):
        return value
    text = value.strip()
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        try:
            datetime.date.fromisoformat(text[:10])
            return text[:10]
        except ValueError:
            return value
    return value


def _salvage_inspection_time(
    collected: dict[str, Any],
    extracted: dict[str, Any],
    datetime_value: str,
    field_name: str = "startDate",
) -> None:
    """Keep the time from a datetime extraction before ``_to_date_only`` strips it.

    Start-date times update ``inspectionTime``. End-date times update only
    ``endDateLT`` so they never overwrite the start clock.
    """
    from app.agents.services.payload_builder_service import extract_hhmm_from_datetime

    salvaged = extract_hhmm_from_datetime(datetime_value)
    if not salvaged:
        return
    if field_name == "endDate":
        # Record it as the answer to inspectionEndTime too, so "21/08/2026
        # 6:00 PM" in one message does not then prompt for the end time.
        collected["inspectionEndTime"] = salvaged
        _apply_hhmm_to_date(
            collected, "endDate", "endDateLT", salvaged, date_value=datetime_value
        )
        return
    if "inspectionTime" in extracted:
        return
    collected["inspectionTime"] = salvaged


def _is_end_date_turn(
    extracted: dict[str, Any],
    active_field: str | None,
) -> bool:
    """True when this turn's time belongs to the end date, not the start."""
    # ``inspectionEndTime`` is the bot explicitly asking for the end clock.
    # The extractor has no such field and reports the answer as
    # ``inspectionTime``; without this the reply would overwrite the start
    # time the user already gave.
    if str(active_field or "") in ("endDate", "inspectionEndTime"):
        return True
    keys = set(extracted)
    return "endDate" in keys and "startDate" not in keys


def _apply_hhmm_to_date(
    collected: dict[str, Any],
    date_key: str,
    lt_key: str,
    hhmm: str,
    date_value: Any = None,
) -> None:
    """Write ``*LT`` for one date from YYYY-MM-DD + HH:MM without touching the other."""
    raw = date_value if date_value is not None else collected.get(date_key)
    if not raw or not hhmm:
        return
    from app.agents.services.payload_builder_service import format_inspection_datetime_pair

    _, lt_display = format_inspection_datetime_pair(
        _to_date_only(raw), collected.get(lt_key), inspection_time=str(hhmm)
    )
    if lt_display:
        collected[lt_key] = lt_display


def _sync_inspection_lt_fields(collected: dict[str, Any]) -> None:
    """Write startDateLT from start time; keep endDateLT's own time when set."""
    from app.agents.services.payload_builder_service import extract_hhmm_from_datetime

    start_time = collected.get("inspectionTime") or extract_hhmm_from_datetime(
        str(collected.get("startDateLT") or "")
    )
    if start_time:
        collected["inspectionTime"] = start_time
        _apply_hhmm_to_date(collected, "startDate", "startDateLT", str(start_time))
    # An explicit end time wins; otherwise reuse whatever endDateLT already
    # carries, and only then fall back to the start clock (which is what the
    # user gets when they skip the end-time question).
    end_time = (
        collected.get("inspectionEndTime")
        or extract_hhmm_from_datetime(str(collected.get("endDateLT") or ""))
        or start_time
    )
    if end_time:
        _apply_hhmm_to_date(collected, "endDate", "endDateLT", str(end_time))


# Fields that become immutable once the lease has been confirmed via the API
# (i.e. lease_id is present in collected_data).  Before lease resolution the
# user must be free to provide / correct these identifiers; after resolution
# they are backend-authoritative and must not be overwritten by subsequent LLM
# extractions (e.g. the model hallucinating "the current lease code i have
# shared" as a lease_code value).
_LEASE_CONFIRMED_FIELDS: frozenset[str] = frozenset({"lease_code", "mall", "brand"})

# Minimum confidence score required to accept an LLM-extracted value.
_CONFIDENCE_THRESHOLD: float = 0.6

# Fixed-choice fields and their accepted values (upper-cased for comparison).
# Used to stop the active-field remap from writing free text or a date into a
# field the UI only ever offers as buttons.
_ENUM_FIELD_VALUES: dict[str, frozenset[str]] = {
    "inspection_done_by": frozenset({"FM_MANAGER", "OPERATIONS"}),
    "already_handovered": frozenset({"ALREADY_TRADING", "INITIATE_HANDOVER"}),
    "document_status": frozenset({"APPROVED", "APPROVED_WITH_CHANGES", "DISAPPROVED"}),
}

# Optional fields that legitimately accept "" (user declined). If the LLM
# fills these with "" but the bot was NOT actually asking for them, discard —
# see the empty-string bleed guard in the merge loop.
_OPTIONAL_SKIPPABLE_FIELDS: frozenset[str] = frozenset(
    {
        "title",
        "description",
        "notes",
        "comments",
        "inspectionTime",
        "inspectionEndTime",
        "delay_reason",
    }
)

# Required fields for which an empty-string correction from the FE must NOT
# wipe an existing value. Formik's dropdown selects sometimes report "" during
# hydration; without this guard those blanks would clobber values collected
# via chip clicks earlier in the flow.
_REQUIRED_NON_OPTIONAL_FIELDS: frozenset[str] = frozenset(
    {
        "inspection_done_by",
        "already_handovered",
        "startDate",
        "endDate",
        "unit_readiness_date",
        "expected_handover_date",
        "actual_handover_date",
        "fitout_start_date",
        "fitout_end_date",
        "actual_shop_handover_date",
        "actual_fitout_start_date",
        "actual_fitout_end_date",
        "actual_trading_date",
        "guideLineLink",
        "lease_code",
    }
)


def _remap_extracted_to_active_field(
    extracted: dict[str, Any],
    active_field: str | None,
) -> dict[str, Any]:
    """Bind a single bare extraction to the field the bot just asked for.

    ``load_session`` clears ``missing_fields``, so extraction often guesses the
    wrong key (e.g. maps "10th August" → ``fitout_start_date`` when the
    question was ``actual_handover_date``, or maps a name → ``notes`` when the
    question was ``inspection_done_by``).

    When exactly one non-backend-protected field was extracted and it differs
    from ``active_field``, remap the value to ``active_field``.  For date
    fields the existing behaviour is preserved.  For non-date fields the same
    remap logic now applies so field-update intent routing works correctly.

    Special isolation: ``notes`` and ``comments`` are semantically distinct
    portal fields — they must not be silently remapped to each other.  If
    ``active_field`` is ``comments`` and the LLM put the value in ``notes``
    (or vice-versa), we remap correctly.  However, if both are present we do
    not remap (multiple candidates → return as-is).
    """
    if not active_field or active_field in BACKEND_PROTECTED_FIELDS:
        return extracted
    if active_field in extracted:
        # The LLM already mapped to the right field — nothing to remap.
        return extracted

    # Find all non-backend-protected keys in the extraction.
    # ``inspectionTime`` rides along with any date the user types ("27/08/2026
    # 4:00 PM" extracts a date *and* a time), so counting it as a competing
    # candidate suppressed the remap and let the date land on whichever field
    # the LLM guessed — e.g. an answer meant for endDate overwriting startDate.
    candidate_keys = [
        k for k in extracted
        if k not in BACKEND_PROTECTED_FIELDS and k not in _USER_TIME_FIELDS
    ]
    if len(candidate_keys) != 1:
        # Multiple or zero candidates — don't guess; return as-is.
        return extracted

    wrong_key = candidate_keys[0]
    # Never bind a value that is not a date onto a date field. While a blocking
    # date error is pending the bot keeps ``active_field`` on that date, so an
    # answer aimed at a different field ("FM_MANAGER", a free-text note) would
    # otherwise be remapped onto the date, corrupting it and losing the real
    # answer — the user's input silently disappears.
    raw = extracted[wrong_key]
    candidate_value = raw.get("value") if isinstance(raw, dict) else raw

    if active_field in _USER_DATE_FIELDS:
        from app.agents.services.validation_service import _parse_date

        if _parse_date(candidate_value, active_field) is None:
            return extracted

    # The mirror case: never drop an arbitrary value into a fixed-choice field.
    # A date typed while ``active_field`` sits on an enum would otherwise be
    # stored as the enum's value (e.g. inspection_done_by="2026-08-27"), which
    # then fails validation with a nonsensical message.
    allowed = _ENUM_FIELD_VALUES.get(active_field)
    if allowed is not None and str(candidate_value or "").strip().upper() not in allowed:
        return extracted
    # Do NOT remap when both keys are distinct "actual_*" date fields.  When the
    # user explicitly names a specific historical date (e.g. "Corrected trading
    # date: 2026-07-10") and the bot was asking for a different specific date
    # (e.g. actual_fitout_end_date), silently remapping would corrupt state and
    # loop the validation error.  Generic date fields (startDate, endDate) still
    # remap normally — the user rarely names those explicitly.
    if (
        wrong_key.startswith("actual_")
        and active_field.startswith("actual_")
        and wrong_key != active_field
    ):
        return extracted
    remapped = dict(extracted)
    remapped[active_field] = remapped.pop(wrong_key)
    return remapped


# ---------------------------------------------------------------------------
# Node
# ---------------------------------------------------------------------------


@trace_node("merge_state", "CHAIN")
async def merge_state_node(state: ServiceRequestState) -> dict[str, Any]:
    """Merge ``extracted_fields`` and ``corrected_fields`` into ``collected_data``."""
    collected: dict[str, Any] = dict(state.get("collected_data") or {})
    extracted: dict[str, Any] = _remap_extracted_to_active_field(
        dict(state.get("extracted_fields") or {}),
        state.get("active_field"),
    )

    # Whether the user's raw message contained an explicit 4-digit year. Used to
    # decide if extracted date years may be coerced to the current year.
    #
    # Year coercion exists to repair *bare* dates the user typed ("30 August"),
    # which gpt-4o-mini sometimes stamps with a stale year. It must NOT touch a
    # date the user merely CONFIRMED. Answering "yes" / "confirm" to a
    # backend-supplied planned date (e.g. base_fitout_end_date = 2025-10-31)
    # carries no date token at all, so there is no bare year to repair — yet the
    # old `bool(user_message.strip())` guard let coercion run and silently
    # rewrote 2025-10-31 to 2026-10-31.
    #
    # Only coerce when the message actually contains a date-like token (any
    # digit). Confirmations, chip clicks, and yes/no answers are excluded.
    user_message: str = state.get("user_message") or ""
    message_has_date_token: bool = bool(_DATE_TOKEN_RE.search(user_message))
    can_coerce_year: bool = bool(user_message.strip()) and message_has_date_token
    # A relative phrase ("in 3 days", "next Monday") is resolved by the
    # extractor against today's date, so its year is already deliberate —
    # coercing it to the current year would move a New Year window back into
    # the year that is ending.
    message_has_year: bool = bool(_EXPLICIT_YEAR_RE.search(user_message)) or bool(
        _RELATIVE_DATE_RE.search(user_message)
    )

    # ── Apply inline edits from the confirmation card first ────────────────
    # corrected_fields come directly from the user's UI interaction, so they
    # are treated as maximum-confidence updates and bypass the normal threshold.
    # Backend-protected fields are still guarded.
    corrected: dict[str, Any] = state.get("corrected_fields") or {}  # type: ignore[assignment]
    active_field_this_turn: str = str(state.get("active_field") or "")
    for field_name, value in corrected.items():
        if field_name in BACKEND_PROTECTED_FIELDS:
            continue
        if value is not None:
            # Don't let an empty-string correction wipe a required field's
            # already-collected value. Formik's dropdown / select occasionally
            # reports "" while the FE is still hydrating (e.g. inspection_done_by
            # loses its selection when the ConfirmationCard mounts before the
            # bridge writes it back), and that empty overrides the real value
            # collected via chip clicks — breaking payload_builder.
            #
            # Optional free-text fields (description, title, notes, comments)
            # get the same protection: the form bridge syncs corrected_fields
            # on every turn, and a stale/not-yet-hydrated "" for one of these
            # can race a genuine correction answer and wipe it right back to
            # empty on the very next turn. Only allow "" through when the bot
            # is actively asking about this exact field right now — that is
            # the one case where "" legitimately means "user skipped".
            existing = collected.get(field_name)
            if (
                isinstance(value, str)
                and value == ""
                and existing not in (None, "", [])
                and (
                    field_name in _REQUIRED_NON_OPTIONAL_FIELDS
                    or (
                        field_name in _OPTIONAL_SKIPPABLE_FIELDS
                        and field_name != active_field_this_turn
                    )
                )
            ):
                continue
            if field_name in _USER_DATE_FIELDS and field_name not in _USER_TIME_FIELDS:
                if field_name in ("startDate", "endDate") and isinstance(value, str):
                    _salvage_inspection_time(collected, {}, value, field_name)
                value = _to_date_only(value)
            collected[field_name] = value

    # The guided-tour choice. Recorded here rather than in each agent's entry
    # node so every workflow — handover, work permit, fitout confirmation and
    # whatever registers next — inherits it from one place.
    from app.agents.services import guided_tour_service

    guided_tour_service.apply_answer(collected, dict(state))

    # The form's date-and-time picker is one control, and changing only its
    # clock arrives as the LT display string alone ("15/09/2026 2:00 PM").
    # That is the user's newest word on the field, so it has to set the stored
    # clock too — otherwise _sync_inspection_lt_fields rebuilds the LT from the
    # older inspectionTime further down and the edit reverts to 9:00 AM while
    # the date half of the same edit sticks.
    if corrected:
        from app.agents.services.payload_builder_service import (
            extract_hhmm_from_datetime,
        )

        for _lt_key, _time_key in (
            ("startDateLT", "inspectionTime"),
            ("endDateLT", "inspectionEndTime"),
        ):
            if _lt_key not in corrected:
                continue
            _hhmm = extract_hhmm_from_datetime(str(corrected[_lt_key] or ""))
            if _hhmm:
                collected[_time_key] = _hhmm

    corrections: list[dict[str, Any]] = list(collected.get("_corrections") or [])

    # On the CONFIRMED path (user confirmed the card), corrected_fields contain
    # intentional user edits that were applied above.  load_session_node does not
    # clear extracted_fields between turns when workflow_stage is active, so T1's
    # extracted_fields can still be present in state on T2.  Guard every field
    # that was already written by corrected_fields so stale extraction values
    # from a previous turn cannot overwrite the deliberate correction.
    #
    # EXCEPT the field the bot is actively collecting this turn: an answer to
    # the question just asked is by definition fresh, not a leftover from a
    # prior turn. Without this carve-out, a session that was ever CONFIRMED
    # plus the form bridge's every-turn corrected_fields sync (which still
    # carries the OLD form value) silently discarded the user's correction —
    # the bot then re-asked quoting the stale value, looping forever.
    _active_field_now: str = str(state.get("active_field") or "")
    _confirmed_correction_keys: frozenset[str] = frozenset(
        k for k, v in corrected.items()
        if k not in BACKEND_PROTECTED_FIELDS
        and v is not None
        and k != _active_field_now
    ) if state.get("confirmation_status") == "CONFIRMED" and corrected else frozenset()

    # Once the lease is resolved (lease_id present) the user-facing lease
    # identifiers are considered authoritative and must not be overwritten.
    lease_resolved = bool(collected.get("lease_id"))

    for field_name, extraction in extracted.items():
        # Never overwrite backend-derived fields from LLM extraction.
        if field_name in BACKEND_PROTECTED_FIELDS:
            continue

        # On the CONFIRMED path, do not let stale extracted_fields from a prior
        # turn overwrite fields that were just set by corrected_fields.
        if field_name in _confirmed_correction_keys:
            continue

        # After lease resolution, protect the confirmed identifiers.
        if lease_resolved and field_name in _LEASE_CONFIRMED_FIELDS:
            continue

        # Support both rich extraction shape {value, confidence} and plain values.
        if isinstance(extraction, dict):
            new_value = extraction.get("value")
            confidence = float(extraction.get("confidence", 1.0))
        else:
            new_value = extraction
            confidence = 1.0

        # Skip low-confidence proposals.
        if confidence < _CONFIDENCE_THRESHOLD:
            continue

        # Normalise the "None" sentinel that the extraction prompt emits when
        # the user explicitly declines an optional field (e.g. "no comments").
        # Storing the literal string "None" would cause it to be submitted
        # verbatim to the upstream API; empty string is the correct neutral value.
        if new_value == "None":
            new_value = ""

        # Skip explicitly null proposals.
        if new_value is None:
            continue

        # Defense-in-depth: when the user answers "none"/"skip" to a specific
        # question, the LLM sometimes leaks that empty-string value to OTHER
        # optional fields the user did not intend to skip. Reject empty-string
        # values for fields OTHER than the one the bot just asked about
        # (active_field). Non-empty extractions still pass through — the user
        # explicitly typed something for that field.
        active_field: str = str(state.get("active_field") or "")
        if (
            isinstance(new_value, str)
            and new_value == ""
            and active_field
            and field_name != active_field
            and field_name in _OPTIONAL_SKIPPABLE_FIELDS
        ):
            continue

        # An unusable answer must not destroy a good value. Extraction maps
        # junk and refusals ("not a real date", "no") to an empty string, which
        # for a required field would wipe a previously valid entry the user was
        # merely trying to amend. Keep the old value; the field stays active and
        # is re-asked until a real replacement arrives.
        if (
            isinstance(new_value, str)
            and new_value == ""
            and field_name in _REQUIRED_NON_OPTIONAL_FIELDS
            and collected.get(field_name) not in (None, "", [])
        ):
            continue

        # Time fields: keep raw HH:MM, never strip via _to_date_only.
        # A time extracted while answering endDate belongs on endDateLT only —
        # never overwrite the start inspectionTime (2 PM start + 6 PM end).
        if field_name in _USER_TIME_FIELDS:
            # A clock time supplied while answering the END date belongs to the
            # end of the window. The extractor still reports it as
            # `inspectionTime`, so re-key it rather than letting it overwrite
            # the start clock (2 PM start + 6 PM end must survive).
            if field_name == "inspectionTime" and _is_end_date_turn(
                extracted, state.get("active_field")
            ):
                collected["inspectionEndTime"] = str(new_value)
                _apply_hhmm_to_date(collected, "endDate", "endDateLT", str(new_value))
                continue
        # Repair hallucinated years on bare dates (no year typed by the user).
        elif (
            can_coerce_year
            and field_name in _USER_DATE_FIELDS
            and isinstance(new_value, str)
        ):
            if field_name in ("startDate", "endDate"):
                _salvage_inspection_time(collected, extracted, new_value, field_name)
            new_value = _to_date_only(new_value)
            new_value = _coerce_year_if_absent(new_value, message_has_year)
        elif field_name in _USER_DATE_FIELDS:
            if field_name in ("startDate", "endDate") and isinstance(new_value, str):
                _salvage_inspection_time(collected, extracted, new_value, field_name)
            new_value = _to_date_only(new_value)

        current_value = collected.get(field_name)

        # Record correction when user changes a previously known value.
        if current_value is not None and current_value != new_value:
            corrections.append(
                {
                    "field": field_name,
                    "old_value": current_value,
                    "new_value": new_value,
                    "corrected_at": datetime.datetime.utcnow().isoformat(),
                }
            )

        collected[field_name] = new_value

    if corrections:
        collected["_corrections"] = corrections

    # Notes and comments are independent portal form fields (one may be
    # mandatory on the form, the other optional). Do not mirror values between
    # them — that caused duplicate display of the same text in confirmation.

    workflow_stage: str = state.get("workflow_stage") or ""

    # Auto-set optional text fields to "" when the user has confirmed
    # (confirmation_status == CONFIRMED) so that the payload builder never sends
    # a null value to the API and so that _route_after_validation's
    # optional_never_asked guard (bypassed for already-confirmed turns) does not
    # leave None in collected_data.
    #
    # Path A collects description / notes / comments; Path B collects only
    # comments (its payload has no description or notes fields), so filling those
    # here would invent values for fields that flow never had.
    _CONFIRMED_OPTIONAL_DEFAULTS: dict[str, tuple[str, ...]] = {
        "CREATE_SR": ("description", "notes", "comments"),
        "CREATE_HANDED_OVER_SR": ("comments",),
    }
    if state.get("confirmation_status") == "CONFIRMED":
        # Mark still-unanswered optionals as skipped ("") rather than leaving
        # None. The auto-generation block below then turns each "" into a
        # readable default, so a confirmed SR never reaches the platform with a
        # blank Description / Notes / Comments.
        for _optional_key in _CONFIRMED_OPTIONAL_DEFAULTS.get(workflow_stage, ()):
            if collected.get(_optional_key) is None:
                collected[_optional_key] = ""

    # ── Auto-generate description / notes / comments ──────────────────────────
    # These are OPTIONAL in the chat flow, but the portal form shows description
    # on the SR card and marks `comments` as required. Previously a skipped
    # answer submitted an empty string, so the Mall Manager's SR landed on the
    # platform with blank Description / Notes / Comments.
    #
    # Fill a deterministic default ONLY when the user explicitly skipped
    # (value == "", written when they answer "none"/"skip"). `None` means the
    # field has not been asked yet and must stay untouched so missing_field_node
    # still prompts for it.
    #
    # Tracks whether we synthesised the description, so the title block below
    # does not fold a generated sentence into its slug (which would produce the
    # redundant "handover-t0112136-handover-inspection-for-lease-t0112136").
    _description_was_generated = False

    if workflow_stage in ("CREATE_SR", "CREATE_HANDED_OVER_SR"):
        _lease_for_text = str(collected.get("lease_code") or "").strip()
        if _lease_for_text:
            _is_already_trading = (
                workflow_stage == "CREATE_HANDED_OVER_SR"
                or str(collected.get("already_handovered") or "").strip().lower()
                == "already_trading"
            )
            if _was_skipped(collected.get("description")):
                collected["description"] = _generate_description(
                    _lease_for_text,
                    str(collected.get("brand") or ""),
                    str(collected.get("mall") or ""),
                    already_trading=_is_already_trading,
                )
                _description_was_generated = True
            if _was_skipped(collected.get("notes")):
                collected["notes"] = _generate_notes(_lease_for_text)
            if _was_skipped(collected.get("comments")):
                collected["comments"] = _generate_comments(_lease_for_text)

    # ── Auto-generate title ────────────────────────────────────────────────────
    # Title generation is branched by workflow stage:
    #   CREATE_HANDED_OVER_SR → "Already Trading - {lease_code}"
    #   CREATE_SR / others    → "handover-{lease_code}-{description_slug}"
    #
    # NOTE: title is asked as an OPTIONAL field. Only auto-generate when the
    # user has EXPLICITLY skipped (title == "") — never when title is `None`
    # (untouched), because merge_state runs BEFORE missing_field_node and
    # filling `None` here would silently rob the bot of its chance to prompt.

    def _title_was_skipped(value: object) -> bool:
        return isinstance(value, str) and value.strip() == ""

    title_value = collected.get("title")
    title_needs_default = title_value is None or _title_was_skipped(title_value)

    if workflow_stage == "CREATE_HANDED_OVER_SR":
        # Path B does not ask for title (auto-generated); safe to fill on None too.
        if not collected.get("title") and collected.get("lease_code"):
            collected["title"] = f"Already Trading - {collected['lease_code']}"
    else:
        # Path A: only auto-generate when the user explicitly skipped it,
        # so missing_field_node still gets to ask when title is None.
        if (
            _title_was_skipped(title_value)
            and collected.get("lease_code")
            and collected.get("description") is not None
        ):
            collected["title"] = _generate_title(
                str(collected["lease_code"]),
                # Only slug a description the USER wrote. A synthesised one just
                # restates the lease code, yielding a redundant title.
                "" if _description_was_generated else str(collected["description"]),
            )
    # Silence unused-var warning when the CREATE_HANDED_OVER_SR branch runs.
    _ = title_needs_default

    # ── Short flow: fill the fields the bot no longer asks for ────────────────
    # Runs only once everything the user *is* asked for has landed, so the
    # generated description / notes can name the real inspection window. Never
    # overwrites a value the user typed or edited on the review card — see
    # ``autofill_text_fields``.
    from app.agents.schemas.handover_schema import short_flow_enabled

    if short_flow_enabled() and _short_flow_autofill_ready(collected, workflow_stage):
        from app.agents.services.sr_autofill_service import autofill_text_fields

        from app.agents.services.sr_autofill_service import default_inspection_times

        # The clocks are never asked for, so store the default the payload
        # would use. Left unset they read as a missing required field and
        # block submission — the user edits the window on the card, hits
        # Confirm, and is asked for a time instead.
        collected.update(default_inspection_times(collected))

        filled, provenance = await autofill_text_fields(collected, workflow_stage)
        if filled:
            collected.update(filled)
            meta: dict[str, Any] = dict(collected.get(_AI_FIELD_META_KEY) or {})
            for field_name, entry in provenance.items():
                meta[field_name] = {**entry, "value": filled[field_name]}
            collected[_AI_FIELD_META_KEY] = meta

    # ── Auto-compute expected_handover_date from unit_readiness_date ──────────
    # FM_REVIEW: expected_handover_date = unit_readiness_date + 7 days.
    # Skipped for CREATE_HANDED_OVER_SR — expected_handover_date is not used
    # in Path B and must not be auto-computed from stale state.

    # For FM_REVIEW and RDD_REVIEW the bot does not proactively ask for optional
    # comments before showing the confirmation card (see _route_after_validation).
    # Auto-satisfy comments to empty string so the backend API never receives a
    # null value and rejects the submission. FM_REVIEW / RDD_REVIEW comments are
    # reviewer remarks — an empty string is the correct neutral value.
    #
    # CREATE_HANDED_OVER_SR is deliberately NOT in this list. Path B is a Mall
    # Manager create flow and _route_after_validation does route it through the
    # proactive optional ask, but that branch only fires while comments is still
    # None — filling it here consumed the question before it could be asked, so
    # every Already Trading SR shipped with the generated "No additional comments
    # provided for lease X." sentence and the user was never consulted. The
    # CONFIRMED-path block above is the safety net that keeps a submitted Path B
    # SR from carrying a null.
    if workflow_stage in ("FM_REVIEW", "RDD_REVIEW"):
        if collected.get("comments") is None:
            collected["comments"] = ""

    if (
        workflow_stage != "CREATE_HANDED_OVER_SR"
        and workflow_stage == "FM_REVIEW"
        and collected.get("unit_readiness_date")
        and not collected.get("expected_handover_date")
    ):
        collected["expected_handover_date"] = _add_days(
            str(collected["unit_readiness_date"]), _AUTO_COMPUTE_DAYS
        )

    # ── Auto-compute trading_date from fitout_end_date ────────────────────────
    # RDD_REVIEW: trading_date = fitout_end_date + 8 days (per product spec).
    # Skipped for CREATE_HANDED_OVER_SR — trading_date is a user-supplied actual
    # date in Path B (actual_trading_date), not an auto-computed field.
    if (
        workflow_stage != "CREATE_HANDED_OVER_SR"
        and workflow_stage == "RDD_REVIEW"
        and collected.get("fitout_end_date")
    ):
        recomputed = _add_days(
            str(collected["fitout_end_date"]), _AUTO_COMPUTE_TRADING_DAYS
        )
        if recomputed != collected.get("trading_date"):
            collected["trading_date"] = recomputed

    # ── Auto-default inspectionTime to current time (CREATE_SR) ─────────────
    # Only applies to Path A (inspection-based handover).
    # Fitout confirmation agents do not use inspectionTime — skip for them.
    _FITOUT_AGENTS: frozenset[str] = frozenset({
        "rdd_fitout_start_agent",
        "rdd_fitout_end_agent",
        "rdd_trading_confirmation_agent",
    })
    _FITOUT_RADIO_FIELDS: frozenset[str] = frozenset({
        "fitout_start_done",
        "fitout_complete_done",
        "trading_done",
    })
    _FITOUT_RADIO_TO_ACTUAL: dict[str, str] = {
        "fitout_start_done": "actual_fitout_start_date",
        "fitout_complete_done": "actual_fitout_complete_date",
        "trading_done": "actual_trading_date",
    }
    _FITOUT_RADIO_TO_PLANNED: dict[str, tuple[str, ...]] = {
        "fitout_start_done": (
            "planned_fitout_start_date",
            "planned_fitout_end_date",
            "planned_trading_date",
        ),
        "fitout_complete_done": (
            "planned_fitout_complete_date",
            "planned_trading_date",
        ),
        "trading_done": ("planned_trading_date",),
    }
    active_agent: str | None = state.get("active_agent")
    if (
        workflow_stage == "CREATE_SR"
        and active_agent not in _FITOUT_AGENTS
    ):
        # A clock time typed this turn always wins — gpt-4o-mini often omits
        # inspectionTime or hallucinates 09:00 (the prompt's documented default).
        from app.agents.services.payload_builder_service import (
            extract_hhmm_from_message,
        )

        msg_time = extract_hhmm_from_message(user_message)
        if msg_time:
            if _is_end_date_turn(extracted, state.get("active_field")):
                collected["inspectionEndTime"] = msg_time
                _apply_hhmm_to_date(collected, "endDate", "endDateLT", msg_time)
            else:
                collected["inspectionTime"] = msg_time
        # Deliberately NOT defaulted to the current clock time here. Doing so
        # marked the field answered, so a date given without a time silently
        # advanced to the next question and the inspection was booked at
        # whatever time the user happened to be typing. Leaving it None keeps
        # it "missing" so missing_field_node asks for the time straight after
        # the start date. A user who declines still gets a real default:
        # payload_builder falls back to _DEFAULT_INSPECTION_HOUR (09:00).
        _sync_inspection_lt_fields(collected)

    # CREATE_SR / APPROVE_SR: when the user answers the yes/no radio in this
    # session (via extracted_fields — not silent form corrected_fields), mark
    # confirmed and clear the opposite branch so planned/actual dates are
    # re-collected.  Form pre-fill alone must not skip the chat yes/no step.
    #
    # Only confirm when the bot was asking for the radio (active_field).  Lease
    # turns often re-extract a form-synced trading_done/fitout_*_done value and
    # must NOT flip ``_approve_radio_confirmed`` or we skip straight to planned
    # dates.
    if (
        workflow_stage in ("CREATE_SR", "APPROVE_SR")
        and active_agent in _FITOUT_AGENTS
    ):
        active_field_now = str(state.get("active_field") or "").strip()
        extracted_keys = set((state.get("extracted_fields") or {}).keys())
        radio_answered = extracted_keys & _FITOUT_RADIO_FIELDS
        if radio_answered and active_field_now in _FITOUT_RADIO_FIELDS:
            collected["_approve_radio_confirmed"] = True
            for radio_key in radio_answered:
                new_radio = str(collected.get(radio_key) or "").strip().lower()
                actual_key = _FITOUT_RADIO_TO_ACTUAL.get(radio_key)
                planned_keys = _FITOUT_RADIO_TO_PLANNED.get(radio_key, ())
                if new_radio == "no" and actual_key:
                    collected[actual_key] = ""
                elif new_radio == "yes":
                    for pk in planned_keys:
                        collected[pk] = ""
                    collected["delay_reason"] = ""
                    # Clear the actual date so the bot re-asks to confirm or
                    # replace it (missing_field_node shows the original/base date).
                    if actual_key:
                        collected[actual_key] = ""

    # RDD status: derive from the actually-uploaded document types now that we
    # show all three types in one document_requirement UI (no chip selection).
    # Rejected upload → auto-set document_status = DISAPPROVED.
    # Meeting Report upload → leave document_status alone so user picks
    #   APPROVED / APPROVED_WITH_CHANGES via the confirmation-card <select>.
    if workflow_stage == "RDD_REVIEW":
        uploaded_types = {
            str(d.get("document_type_id") or d.get("document_type") or "")
            for d in (state.get("documents") or [])
            if d.get("document_type_id") or d.get("document_type")
        }
        if _RDD_DOC_TYPE_REJECTED in uploaded_types:
            collected["document_status"] = _RDD_REJECTED_STATUS
        elif _RDD_DOC_TYPE_MEETING in uploaded_types:
            # Preserve any status the user has already picked; do not overwrite.
            pass

    # ── Handover Type* switch — Path A (CREATE_SR) → Path B (CREATE_HANDED_OVER_SR) ──
    # Once the user picks "already_trading" for already_handovered, flip the
    # workflow_stage + sub_category so the remaining fields align with Path B
    # (four actual dates instead of description/dates/inspector).
    patch: dict[str, Any] = {"collected_data": collected}
    active_agent = state.get("active_agent") or ""
    current_stage = state.get("workflow_stage") or ""
    already_handovered = str(collected.get("already_handovered") or "").strip().lower()
    if (
        active_agent == "rdd_handover_agent"
        and current_stage == "CREATE_SR"
        and already_handovered == "already_trading"
    ):
        patch["workflow_stage"] = "CREATE_HANDED_OVER_SR"
        patch["sub_category"] = "HANDED_OVER"
    return patch


def _short_flow_autofill_ready(collected: dict[str, Any], workflow_stage: str) -> bool:
    """True once the user has supplied everything the short flow asks for.

    Generating earlier would describe an inspection whose window is not known
    yet, and the text would then have to be regenerated (or, worse, kept) once
    the dates arrive.
    """
    # Local import: handover_schema imports BACKEND_COMPUTED_FIELDS from this
    # module, so a module-level import here would close the cycle.
    from app.agents.schemas.handover_schema import AI_FILLED_STAGES, INSPECTION_FIELD_GROUP

    if workflow_stage not in AI_FILLED_STAGES:
        return False
    if not str(collected.get("lease_code") or "").strip():
        return False
    if workflow_stage == "CREATE_SR":
        required_now: tuple[str, ...] = INSPECTION_FIELD_GROUP
    else:
        required_now = (
            "actual_shop_handover_date",
            "actual_fitout_start_date",
            "actual_fitout_end_date",
            "actual_trading_date",
        )
    return all(collected.get(f) not in (None, "", []) for f in required_now)


def _was_skipped(value: object) -> bool:
    """True when the user explicitly declined a field (answered "none"/"skip").

    ``merge_state`` normalises those answers to ``""``. ``None`` means the field
    has not been asked yet, so it must be left alone for ``missing_field_node``.
    """
    return isinstance(value, str) and value.strip() == ""


def _generate_description(
    lease_code: str,
    brand: str = "",
    mall: str = "",
    already_trading: bool = False,
) -> str:
    """Human-readable default description when the Mall Manager skips it."""
    kind = "Already Trading handover" if already_trading else "Handover inspection"
    where = " at ".join(p for p in (brand.strip(), mall.strip()) if p.strip())
    if where:
        return f"{kind} for lease {lease_code} - {where}."
    return f"{kind} for lease {lease_code}."


def _generate_notes(lease_code: str) -> str:
    """Default notes when the Mall Manager skips the notes question."""
    return f"No additional notes provided for lease {lease_code}."


def _generate_comments(lease_code: str) -> str:
    """Default comments when the Mall Manager skips the comments question.

    ``comments`` is ``required: true`` on the platform HANDOVER form skeleton,
    so an empty string here shows as a blank mandatory field on the SR detail
    page. A deterministic sentence keeps the record readable.
    """
    return f"No additional comments provided for lease {lease_code}."


def _generate_title(lease_code: str, description: str) -> str:
    """Build 'handover-{lease_code}-{slug}' from lease_code and description.

    The slug is the first five significant words of the description,
    lowercased and joined with hyphens.  Non-alphanumeric characters are
    stripped so the title is URL-safe and consistent.

    When *description* is empty the trailing separator is omitted, producing
    'handover-{lease_code}' rather than 'handover-{lease_code}-'.
    """
    slug_words = re.sub(r"[^a-zA-Z0-9\s]", "", description.lower()).split()
    slug = "-".join(slug_words[:5])
    return f"handover-{lease_code}-{slug}" if slug else f"handover-{lease_code}"
