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
_USER_TIME_FIELDS: frozenset[str] = frozenset({"inspectionTime"})

_EXPLICIT_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")


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


# Fields that become immutable once the lease has been confirmed via the API
# (i.e. lease_id is present in collected_data).  Before lease resolution the
# user must be free to provide / correct these identifiers; after resolution
# they are backend-authoritative and must not be overwritten by subsequent LLM
# extractions (e.g. the model hallucinating "the current lease code i have
# shared" as a lease_code value).
_LEASE_CONFIRMED_FIELDS: frozenset[str] = frozenset({"lease_code", "mall", "brand"})

# Minimum confidence score required to accept an LLM-extracted value.
_CONFIDENCE_THRESHOLD: float = 0.6

# Optional fields that legitimately accept "" (user declined). If the LLM
# fills these with "" but the bot was NOT actually asking for them, discard —
# see the empty-string bleed guard in the merge loop.
_OPTIONAL_SKIPPABLE_FIELDS: frozenset[str] = frozenset(
    {"title", "description", "notes", "comments", "inspectionTime", "delay_reason"}
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
    candidate_keys = [k for k in extracted if k not in BACKEND_PROTECTED_FIELDS]
    if len(candidate_keys) != 1:
        # Multiple or zero candidates — don't guess; return as-is.
        return extracted

    wrong_key = candidate_keys[0]
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
    # decide if extracted date years may be coerced to the current year. Only
    # attempt coercion when a message is actually present to inspect.
    user_message: str = state.get("user_message") or ""
    can_coerce_year: bool = bool(user_message.strip())
    message_has_year: bool = bool(_EXPLICIT_YEAR_RE.search(user_message))

    # ── Apply inline edits from the confirmation card first ────────────────
    # corrected_fields come directly from the user's UI interaction, so they
    # are treated as maximum-confidence updates and bypass the normal threshold.
    # Backend-protected fields are still guarded.
    corrected: dict[str, Any] = state.get("corrected_fields") or {}  # type: ignore[assignment]
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
            existing = collected.get(field_name)
            if (
                isinstance(value, str)
                and value == ""
                and existing not in (None, "", [])
                and field_name in _REQUIRED_NON_OPTIONAL_FIELDS
            ):
                continue
            if field_name in _USER_DATE_FIELDS and field_name not in _USER_TIME_FIELDS:
                value = _to_date_only(value)
            collected[field_name] = value

    corrections: list[dict[str, Any]] = list(collected.get("_corrections") or [])

    # On the CONFIRMED path (user confirmed the card), corrected_fields contain
    # intentional user edits that were applied above.  load_session_node does not
    # clear extracted_fields between turns when workflow_stage is active, so T1's
    # extracted_fields can still be present in state on T2.  Guard every field
    # that was already written by corrected_fields so stale extraction values
    # from a previous turn cannot overwrite the deliberate correction.
    _confirmed_correction_keys: frozenset[str] = frozenset(
        k for k, v in corrected.items()
        if k not in BACKEND_PROTECTED_FIELDS and v is not None
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

        # Time fields: keep raw HH:MM, never strip via _to_date_only.
        if field_name in _USER_TIME_FIELDS:
            pass  # preserve value as-is
        # Repair hallucinated years on bare dates (no year typed by the user).
        elif (
            can_coerce_year
            and field_name in _USER_DATE_FIELDS
            and isinstance(new_value, str)
        ):
            new_value = _to_date_only(new_value)
            new_value = _coerce_year_if_absent(new_value, message_has_year)
        elif field_name in _USER_DATE_FIELDS:
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

    # For CREATE_SR, auto-set optional text fields to "" when the user has
    # confirmed (confirmation_status == CONFIRMED) so that the payload builder
    # never sends a null value to the API and so that _route_after_validation's
    # optional_never_asked guard (now bypassed for already-confirmed turns) does
    # not leave None in collected_data.
    if (
        workflow_stage == "CREATE_SR"
        and state.get("confirmation_status") == "CONFIRMED"
    ):
        if collected.get("notes") is None:
            collected["notes"] = ""
        if collected.get("comments") is None:
            collected["comments"] = ""

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
                str(collected["description"]),
            )
    # Silence unused-var warning when the CREATE_HANDED_OVER_SR branch runs.
    _ = title_needs_default

    # ── Auto-compute expected_handover_date from unit_readiness_date ──────────
    # FM_REVIEW: expected_handover_date = unit_readiness_date + 7 days.
    # Skipped for CREATE_HANDED_OVER_SR — expected_handover_date is not used
    # in Path B and must not be auto-computed from stale state.

    # For FM_REVIEW, RDD_REVIEW, and CREATE_HANDED_OVER_SR the bot no longer
    # proactively asks for optional comments before showing the confirmation card
    # (see _route_after_validation).  Auto-satisfy comments to empty string so
    # the backend API never receives a null value and rejects the submission.
    if workflow_stage in ("FM_REVIEW", "RDD_REVIEW", "CREATE_HANDED_OVER_SR"):
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
    active_agent: str | None = state.get("active_agent")
    if (
        workflow_stage == "CREATE_SR"
        and not collected.get("inspectionTime")
        and active_agent not in _FITOUT_AGENTS
    ):
        collected["inspectionTime"] = datetime.datetime.now().strftime("%H:%M")

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
