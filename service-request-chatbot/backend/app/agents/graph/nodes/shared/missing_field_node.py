"""Clarifying questions for missing required fields and blocking validation errors.

Strategy
--------
1. Determine which fields are required for the current ``workflow_stage``.
2. Compute the missing subset against ``collected_data``.
3. If there are blocking validation errors (e.g. endDate before startDate),
   ask the user to correct the first offending field — even if the field is
   technically present (it just has an invalid value).
4. If any backend-derived fields are still absent, ask the user for
   ``lease_code`` (or ``lease_brand_mall`` as a fallback) so that the
   ``lease_lookup_node`` can resolve all backend fields programmatically.
5. Otherwise ask for the **first** outstanding user-supplied field — one
   question per turn.
6. When nothing is missing or invalid, surface an ``IN_PROGRESS`` status to
   unblock the next node.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.agents.graph.nodes.handover.confirmation_node import _FIELD_LABELS
from app.agents.graph.state import ServiceRequestState
from app.agents.registries.workflow_config import get_workflow_config
from app.agents.schemas.fitout_confirm_schema import (
    get_missing_fields as fitout_confirm_get_missing_fields,
)
from app.agents.schemas.handover_schema import (
    HANDED_OVER_FIELD_GROUP,
    INSPECTION_FIELD_GROUP,
    RDD_REVIEW_FIELD_GROUP,
    RDD_DOC_TYPE_MEETING,
    RDD_DOC_TYPE_OTHER,
    RDD_DOC_TYPE_REJECTED,
    STAGE_REGISTRY,
    get_missing_fields as handover_get_missing_fields,
    short_flow_enabled,
)
from app.agents.services import guided_tour_service
from app.agents.services.display_formatters import format_field_display_value
from app.agents.services.inspection_suggestions_service import (
    build_inspection_decorations,
    parse_date as _parse_inspection_date,
)
from app.agents.services.missing_field_service import (
    HANDOVER_FIELD_QUESTIONS,
    get_missing_fields,
)
from app.agents.services.review_decorations_service import (
    build_document_upload_ask_response,
    maybe_reset_rdd_upload_slot,
)
from app.agents.services.validation_service import _SINGLE_DATE_FIELDS
from app.observability.decorators import trace_node

# Chip decorations reused for enum-style fields so a correction re-ask looks
# identical to the field's normal first-time ask (see the matching blocks
# further down in this file for the initial-ask versions).
#
# The ``action`` on each option matters as much as the value: the entry nodes
# key off it to write the field straight into collected_data
# (handover_entry_node._INSPECTION_DONE_BY_ACTIONS,
# rdd_review_entry_node._STATUS_ACTIONS), and the graph then routes those
# actions past field_extraction. A chip without one sends a bare label instead,
# which goes through LLM re-extraction — and extraction's correction re-arm
# reads "field already has a value + nothing extracted" as a failed correction
# and re-asks the question the user just clicked. Keep these in lockstep with
# the initial-ask blocks below.
_CORRECTION_FIELD_DECORATIONS: dict[str, dict[str, Any]] = {
    "inspection_done_by": {
        "suggestions": {
            "kind": "labeled_options",
            "options": [
                {
                    "label": "FM Manager",
                    "value": "FM_MANAGER",
                    "action": "select_inspection_done_by_fm_manager",
                },
                {
                    "label": "Operations",
                    "value": "OPERATIONS",
                    "action": "select_inspection_done_by_operations",
                },
            ],
        }
    },
    "already_handovered": {
        "suggestions": {
            "kind": "labeled_options",
            "options": [
                {
                    "label": "Already Trading",
                    "value": "already_trading",
                    "action": "select_handover_type_already_trading",
                },
                {
                    "label": "Initiate Handover",
                    "value": "initiate_handover",
                    "action": "select_handover_type_initiate_handover",
                },
            ],
        }
    },
    "document_status": {
        "suggestions": {
            "kind": "labeled_options",
            "options": [
                {
                    "label": "Approve",
                    "value": "APPROVED",
                    "action": "select_rdd_status_approved",
                },
                {
                    "label": "Approve with changes",
                    "value": "APPROVED_WITH_CHANGES",
                    "action": "select_rdd_status_approved_with_changes",
                },
            ],
        }
    },
}

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Fields populated exclusively by backend API lookups (lease resolution etc.).
# When any of these are missing, the node asks for a lease trigger so the
# ``lease_lookup_node`` can resolve them programmatically.
# NOTE: "title" is intentionally NOT in this set — it is auto-generated by
# merge_state_node from lease_code + description and must not trigger the
# backend-missing branch (which would block the user from being asked for
# description).
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

# Fields that the bot must never prompt the user for but that are also not
# resolved via the lease lookup API — they are auto-generated by the system.
# These fields are silently skipped in the user_missing list so the bot
# never asks for them, while still allowing the user_missing branch to run.
# Empty — title is now asked as optional (like description). merge_state_node
# still auto-generates ("handover-{lease}-{slug}" for Path A, "Already Trading
# - {lease}" for Path B) when the user's answer resolves to empty.
_AUTO_GENERATED_FIELDS: frozenset[str] = frozenset()

# Ordered preference for the "trigger" question when backend fields are unresolved.
# Asking for lease_code first enables the most precise lookup; lease_brand_mall
# covers free-text brand/mall input when a formal code is unavailable.
_LEASE_TRIGGER_FIELDS: tuple[str, ...] = ("lease_code", "company", "lease_brand_mall")

# Validation-error questions: maps validation_type → (field_to_ask, question_hint).
# When a blocking validation error has no entry here, fall back to the generic
# HANDOVER_FIELD_QUESTIONS for the error's field.
# Note: validate_start_end_date now returns field="endDate" so the date_range
# entry here is a fallback for any edge-case where the field is not set correctly.
_VALIDATION_ERROR_QUESTIONS: dict[str, tuple[str, str]] = {
    "date_range": (
        "endDate",
        (
            "Ask the user to provide a valid end date that is strictly after the start date. "
            "Mention the current start date and that the end date must be later."
        ),
    ),
}

# Blocking errors that are not "correct this field value" prompts — skip Priority 0
# so we keep asking for the next missing user field (or let UI show doc attach).
_NON_FIELD_VALIDATION_TYPES: frozenset[str] = frozenset({
    "document_count",
    "document_type",
    "permission",
})

# Fitout confirmation agents use fitout_confirm_schema.get_missing_fields instead
# of handover_get_missing_fields — their required fields are completely different.
_FITOUT_CONFIRM_AGENTS: frozenset[str] = frozenset({
    "rdd_fitout_start_agent",
    "rdd_fitout_end_agent",
    "rdd_trading_confirmation_agent",
})


_RDD_MANDATORY_UPLOAD_TYPES: frozenset[str] = frozenset(
    {"DR_SR_HANDOVER_REPORT", "SR_REJECTED_HANDOVER_REPORT"}
)

# Wording for the FOS / FOE / Trading yes-no milestone question.
#
# Each milestone owns its whole phrasing rather than sharing one noun label
# slotted into a fixed verb. A single label cannot serve all three: the field
# names read as nouns ("fitout start", "fitout complete"), so "Has {label}
# started?" produced "Has fitout start started?" and "Has fitout complete
# started?" — and "complete" is a state, not something that starts.
#
# - ``question``  — the first-ask form, used when the radio has no value yet
# - ``subject``   — sentence-initial subject for the "currently marked as" forms
# - ``affirmed`` / ``negated`` — how the stored yes / no reads for this milestone
# - ``not_yet`` / ``now`` — mid-sentence clauses offering the opposite answer
_RADIO_PHRASING: dict[str, dict[str, str]] = {
    "fitout_start_done": {
        "question": "Has the fitout started?",
        "subject": "The fitout",
        "affirmed": "started",
        "negated": "not started",
        "not_yet": "it has not started",
        "now": "it has now started",
    },
    "fitout_complete_done": {
        "question": "Has the fitout been completed?",
        "subject": "The fitout",
        "affirmed": "completed",
        "negated": "not completed",
        "not_yet": "it is not complete",
        "now": "it is now complete",
    },
    "trading_done": {
        "question": "Has trading started?",
        "subject": "Trading",
        "affirmed": "started",
        "negated": "not started",
        "not_yet": "it has not started",
        "now": "it has now started",
    },
}

_RADIO_PHRASING_FALLBACK: dict[str, str] = {
    "question": "Has this milestone been reached?",
    "subject": "This milestone",
    "affirmed": "done",
    "negated": "not done",
    "not_yet": "it has not been reached",
    "now": "it has now been reached",
}

# Maps each FOS/FOE/Trading actual-date field to candidate "original date"
# sources (first non-empty wins).  Mirrors the form: prefer lease base date,
# then planned milestone date when the form has already hydrated it.
_ACTUAL_TO_ORIGINAL_DATE_CANDIDATES: dict[str, tuple[str, ...]] = {
    "actual_fitout_start_date": (
        "base_fitout_start_date",
        "planned_fitout_start_date",
    ),
    "actual_fitout_complete_date": (
        "base_fitout_end_date",
        "planned_fitout_complete_date",
        "planned_fitout_end_date",
    ),
    "actual_trading_date": (
        "base_trading_date",
        "planned_trading_date",
    ),
}

# Back-compat alias used by older call sites / tests.
_ACTUAL_TO_BASE_DATE: dict[str, str] = {
    actual: candidates[0]
    for actual, candidates in _ACTUAL_TO_ORIGINAL_DATE_CANDIDATES.items()
}


def _format_base_date_for_display(raw: str) -> str:
    """Convert common date formats to DD/MM/YYYY for display in a prompt."""
    s = str(raw or "").strip()
    if not s:
        return s
    # ISO timestamps from the form/API → date-only first.
    if "T" in s:
        s = s.split("T", 1)[0]
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt).strftime("%d/%m/%Y")
        except ValueError:
            continue
    return str(raw).strip()


def _resolve_original_date(collected: dict[str, Any], actual_field: str) -> str:
    """Return the first non-empty original/base date for *actual_field*."""
    for key in _ACTUAL_TO_ORIGINAL_DATE_CANDIDATES.get(actual_field, ()):
        raw = str(collected.get(key) or "").strip()
        if raw and raw.lower() not in ("none", "null", "—", "-"):
            return raw
    return ""
_RDD_UPLOAD_ROLES: frozenset[str] = frozenset({"DD_ENGINEER"})


def _has_rdd_mandatory_upload(documents: list[dict[str, Any]]) -> bool:
    """True when the RDD stage's either-or mandatory pair has been satisfied.

    Only DD_ENGINEER's own uploads count. A DR_SR_HANDOVER_REPORT or Rejected
    Report attached by another role (unlikely — those types are RDD-only per
    the platform skeleton) still counts, but MM's SR_HANDOVER_OTHER never
    triggers this gate even though the two share doc-type IDs.
    Session-uploaded rows carry no ``role`` field and are counted as RDD's.
    """
    for doc in documents or []:
        dt = str(doc.get("document_type_id") or doc.get("document_type") or "")
        if dt not in _RDD_MANDATORY_UPLOAD_TYPES:
            continue
        role_val = str(
            doc.get("role") or doc.get("assigned_role") or ""
        ).strip().upper()
        if not role_val or role_val in _RDD_UPLOAD_ROLES:
            return True
    return False


def _should_ask_guided_tour(
    collected: dict[str, Any],
    workflow_cfg: Any,
    all_missing: list[str],
) -> bool:
    """True when the guided-tour choice is still owed for this draft.

    Only worth asking when there is something to collect: a stage with nothing
    outstanding is about to show its review card, and a question about how to
    fill a form the user has already filled is noise.
    """
    if not guided_tour_service.is_enabled():
        return False
    if workflow_cfg is not None and not getattr(workflow_cfg, "guided_tour_gate", True):
        return False
    if guided_tour_service.answered(collected):
        return False
    return bool(all_missing)


# ---------------------------------------------------------------------------
# Combined inspection question (short flow)
# ---------------------------------------------------------------------------

# Agents whose CREATE_SR stage collects the inspection window. Path B
# (CREATE_HANDED_OVER_SR) records historical dates instead and is untouched.
_INSPECTION_GROUP_AGENTS: frozenset[str] = frozenset({"rdd_handover_agent"})

# One line per field, shown under the question so the user knows what each
# value means without having to ask.
_INSPECTION_FIELD_EXPLAINERS: dict[str, str] = {
    "startDate": "**Start date** — when the inspection begins.",
    "endDate": "**End date** — when it is expected to finish (must be after the start).",
    "inspection_done_by": "**Done by** — who conducts it: FM Manager or Operations.",
}


# The two dates that carry a clock. Corrections to these accept "date time".
_WINDOW_DATE_FIELDS: frozenset[str] = frozenset({"startDate", "endDate"})

# Which stored clock belongs to which half of the window.
_WINDOW_CLOCK_KEYS: dict[str, str] = {
    "startDate": "inspectionTime",
    "endDate": "inspectionEndTime",
}


def _window_clock_display(collected: dict[str, Any], date_field: str) -> str:
    """The clock stored for one half of the inspection window, as ``h:mm AM/PM``.

    An end with no clock of its own runs on the start clock, matching
    ``resolve_start_end_hhmm``.
    """
    key = _WINDOW_CLOCK_KEYS.get(date_field, "")
    raw = str(collected.get(key) or "").strip()
    if not raw and date_field == "endDate":
        raw = str(collected.get("inspectionTime") or "").strip()
    if not raw:
        return ""
    try:
        return datetime.strptime(raw, "%H:%M").strftime("%-I:%M %p")
    except ValueError:
        return raw


def _format_inspection_date(raw: Any) -> str:
    """``2026-09-06`` → ``6 Sep 2026``; falls back to the raw string."""
    parsed = _parse_inspection_date(raw)
    if parsed is None:
        return str(raw or "").strip()
    return f"{parsed.day} {parsed:%b %Y}"


def _should_ask_inspection_group(
    active_agent: str | None,
    workflow_stage: str,
    user_missing: list[str],
) -> bool:
    """True when the inspection fields should be asked as one question."""
    if not short_flow_enabled():
        return False
    if (active_agent or "") not in _INSPECTION_GROUP_AGENTS:
        return False
    if workflow_stage != "CREATE_SR":
        return False
    # Only when the group is genuinely next in line. Stage order puts the
    # handover type first, and jumping the queue would ask for inspection
    # dates before the user has said whether this is even a new handover.
    return bool(user_missing) and user_missing[0] in INSPECTION_FIELD_GROUP


def _build_inspection_group_question(
    collected: dict[str, Any],
    group_missing: list[str],
) -> str:
    """Compose the question for whichever inspection fields are still open.

    The wording changes with the remaining subset: the first ask invites all
    three values in one line, while a re-ask states what is already booked and
    asks only for the gap — so a user who answered two of three never sees the
    same question twice.
    """
    start_display = _format_inspection_date(collected.get("startDate"))
    end_display = _format_inspection_date(collected.get("endDate"))

    known_bits: list[str] = []
    if "startDate" not in group_missing and start_display:
        known_bits.append(f"start **{start_display}**")
    if "endDate" not in group_missing and end_display:
        known_bits.append(f"end **{end_display}**")
    if "inspection_done_by" not in group_missing and collected.get("inspection_done_by"):
        label = format_field_display_value(
            "inspection_done_by", collected.get("inspection_done_by")
        )
        known_bits.append(f"inspected by **{label}**")

    lead = ""
    if known_bits:
        lead = "Got " + ", ".join(known_bits) + ". "

    if len(group_missing) == 1:
        only = group_missing[0]
        if only == "startDate":
            ask = "When should the inspection start?"
        elif only == "endDate":
            ask = "When is the inspection expected to finish?"
        else:
            ask = "Who is conducting the inspection?"
        return f"{lead}{ask}"

    if set(group_missing) == {"startDate", "endDate"}:
        ask = (
            "What is the inspection window? One line is fine — "
            "e.g. '6 Sep to 7 Sep'."
        )
    elif set(group_missing) == {"endDate", "inspection_done_by"}:
        ask = (
            "When does it finish, and who is conducting it? One line is fine — "
            "e.g. '7 Sep, FM Manager'."
        )
    else:
        ask = (
            "**Inspection details** — give me the window and who is inspecting. "
            "One line is fine, e.g. '6 Sep to 7 Sep, FM Manager'."
        )

    explainers = "\n".join(
        _INSPECTION_FIELD_EXPLAINERS[f]
        for f in INSPECTION_FIELD_GROUP
        if f in group_missing and f in _INSPECTION_FIELD_EXPLAINERS
    )
    return f"{lead}{ask}\n\n{explainers}" if explainers else f"{lead}{ask}"


def _build_inspection_group_ask(
    collected: dict[str, Any],
    group_missing: list[str],
    all_missing: list[str],
) -> dict[str, Any]:
    """State patch asking for every still-missing inspection field at once.

    ``active_field`` is set to the first open member so that a bare single
    answer still binds through the normal remap path; the other members are
    picked up by the extractor's multi-field rules, and anything the user
    leaves out is asked again next turn with narrowed chips.
    """
    question = _build_inspection_group_question(collected, group_missing)
    primary = group_missing[0]
    updates: dict[str, Any] = {
        "missing_fields": list(all_missing),
        "response_message": question,
        "response_ui": {
            "type": "text_question",
            # ``field`` is the one to scroll to and bind a bare answer to;
            # ``fields`` is everything this question asks for, so the form can
            # guide the user to all of them instead of spotlighting one and
            # dimming the other two the same question is asking about.
            "field": primary,
            "fields": list(group_missing),
            "message": question,
            "verbatim": True,
        },
        "status": "WAITING_FOR_USER",
        "active_field": primary,
    }
    decorations = build_inspection_decorations(collected, set(group_missing))
    if decorations:
        updates["response_decorations"] = decorations
    return updates


# ---------------------------------------------------------------------------
# Combined Already Trading question (Path B)
# ---------------------------------------------------------------------------

_HANDED_OVER_EXPLAINERS: dict[str, str] = {
    "actual_shop_handover_date": "**Shop handover** — when the unit was handed to the tenant.",
    "actual_fitout_start_date": "**Fit-out start** — when fit-out work began.",
    "actual_fitout_end_date": "**Fit-out end** — when fit-out work finished.",
    "actual_trading_date": "**Trading** — when the store opened for business.",
}

# Sample values for the "one line is fine" example, so it shows the fields
# actually being asked for rather than a fixed four.
_HANDED_OVER_EXAMPLE_DATES: dict[str, str] = {
    "actual_shop_handover_date": "20/08/2026",
    "actual_fitout_start_date": "27/08/2026",
    "actual_fitout_end_date": "30/09/2026",
    "actual_trading_date": "10/10/2026",
}

_HANDED_OVER_SHORT_LABELS: dict[str, str] = {
    "actual_shop_handover_date": "handover",
    "actual_fitout_start_date": "fit-out start",
    "actual_fitout_end_date": "fit-out end",
    "actual_trading_date": "trading",
}


def _should_ask_handed_over_group(
    active_agent: str | None,
    workflow_stage: str,
    user_missing: list[str],
) -> bool:
    """True when Path B's four actual dates should be asked as one question."""
    if not short_flow_enabled():
        return False
    if (active_agent or "") not in _INSPECTION_GROUP_AGENTS:
        return False
    if workflow_stage != "CREATE_HANDED_OVER_SR":
        return False
    return bool(user_missing) and user_missing[0] in HANDED_OVER_FIELD_GROUP


def _build_handed_over_group_question(
    collected: dict[str, Any],
    group_missing: list[str],
) -> str:
    """Compose the question for whichever Already Trading dates are open.

    No suggestions here, by design: these are dates the unit already lived
    through, and only the Mall Manager knows them.
    """
    known = [
        f"{_HANDED_OVER_SHORT_LABELS[f]} **{_format_inspection_date(collected[f])}**"
        for f in HANDED_OVER_FIELD_GROUP
        if f not in group_missing and collected.get(f)
    ]
    lead = "Got " + ", ".join(known) + ". " if known else ""

    if len(group_missing) == 1:
        only = group_missing[0]
        ask = {
            "actual_shop_handover_date": "When was the unit handed over?",
            "actual_fitout_start_date": "When did fit-out start?",
            "actual_fitout_end_date": "When did fit-out finish?",
            "actual_trading_date": "When did the store open for business?",
        }[only]
        return f"{lead}{ask} Use DD/MM/YYYY."

    # The example lists exactly the dates still wanted, in the order they are
    # read — four of them on the first ask, two when two have already landed.
    example = ", ".join(
        _HANDED_OVER_EXAMPLE_DATES[f]
        for f in HANDED_OVER_FIELD_GROUP
        if f in group_missing
    )
    ask = (
        "**The dates so far** — this unit is already trading, so I need what "
        f"actually happened. One line is fine, in order: '{example}'."
    )
    explainers = "\n".join(
        _HANDED_OVER_EXPLAINERS[f]
        for f in HANDED_OVER_FIELD_GROUP
        if f in group_missing and f in _HANDED_OVER_EXPLAINERS
    )
    return f"{lead}{ask}\n\n{explainers}" if explainers else f"{lead}{ask}"


def _build_handed_over_group_ask(
    collected: dict[str, Any],
    group_missing: list[str],
    all_missing: list[str],
) -> dict[str, Any]:
    """State patch asking for every still-missing Already Trading date at once."""
    question = _build_handed_over_group_question(collected, group_missing)
    primary = group_missing[0]
    return {
        "missing_fields": list(all_missing),
        "response_message": question,
        "response_ui": {
            "type": "text_question",
            "field": primary,
            "fields": list(group_missing),
            "message": question,
            "verbatim": True,
        },
        "status": "WAITING_FOR_USER",
        "active_field": primary,
    }


# ---------------------------------------------------------------------------
# Combined RDD review question
# ---------------------------------------------------------------------------

_RDD_FIELD_EXPLAINERS: dict[str, str] = {
    "document_status": (
        "**Outcome** — Approve if the Handover Meeting Report stands as it is, "
        "Approve with changes if it needs amendments."
    ),
    "actual_handover_date": "**Handover** — the date the unit was actually handed over.",
    "fitout_start_date": "**Fit-out start** — the date fit-out work actually began.",
    "fitout_end_date": "**Fit-out end** — the date fit-out work actually finished.",
}

_RDD_STATUS_WORDS: dict[str, str] = {
    "APPROVED": "Approved",
    "APPROVED_WITH_CHANGES": "Approved with changes",
    "DISAPPROVED": "Disapproved",
}


def _should_ask_rdd_group(
    active_agent: str | None,
    workflow_stage: str,
    user_missing: list[str],
) -> bool:
    """True when the RDD outcome and dates should be asked as one question."""
    if not short_flow_enabled():
        return False
    if (active_agent or "") not in _INSPECTION_GROUP_AGENTS:
        return False
    if workflow_stage != "RDD_REVIEW":
        return False
    # Only once the group is genuinely next: the guideline link and the
    # mandatory upload both come first.
    return bool(user_missing) and user_missing[0] in RDD_REVIEW_FIELD_GROUP


def _build_rdd_group_question(
    collected: dict[str, Any],
    group_missing: list[str],
) -> str:
    """Compose the question for whichever RDD fields are still open."""
    known: list[str] = []
    if "document_status" not in group_missing and collected.get("document_status"):
        label = _RDD_STATUS_WORDS.get(
            str(collected["document_status"]).upper(), str(collected["document_status"])
        )
        known.append(f"**{label}**")
    for field_name, word in (
        ("actual_handover_date", "handover"),
        ("fitout_start_date", "fit-out start"),
        ("fitout_end_date", "fit-out end"),
    ):
        if field_name not in group_missing and collected.get(field_name):
            known.append(f"{word} **{_format_inspection_date(collected[field_name])}**")

    lead = "Got " + ", ".join(known) + ". " if known else ""

    if len(group_missing) == 1:
        only = group_missing[0]
        ask = {
            "document_status": "What is the outcome of the Handover Meeting Report?",
            "actual_handover_date": "What was the actual handover date?",
            "fitout_start_date": "When did fit-out actually start?",
            "fitout_end_date": "When did fit-out actually finish?",
        }[only]
        return f"{lead}{ask}"

    ask = (
        "**Report outcome and milestone dates** — the outcome, the actual "
        "handover date, and the fit-out window. One line is fine, e.g. "
        "'approved, 10 Sep, 30 Sep to 10 Nov'."
    )
    explainers = "\n".join(
        _RDD_FIELD_EXPLAINERS[f]
        for f in RDD_REVIEW_FIELD_GROUP
        if f in group_missing and f in _RDD_FIELD_EXPLAINERS
    )
    tail = "\n\nThe trading date is worked out from the fit-out end date."
    return f"{lead}{ask}\n\n{explainers}{tail}" if explainers else f"{lead}{ask}"


async def _build_rdd_group_ask(
    collected: dict[str, Any],
    group_missing: list[str],
    all_missing: list[str],
) -> dict[str, Any]:
    """State patch asking for every still-missing RDD review field at once."""
    from app.agents.services.rdd_suggestions_service import build_rdd_decorations

    question = _build_rdd_group_question(collected, group_missing)
    primary = group_missing[0]
    updates: dict[str, Any] = {
        "missing_fields": list(all_missing),
        "response_message": question,
        "response_ui": {
            "type": "text_question",
            "field": primary,
            "fields": list(group_missing),
            "message": question,
            "verbatim": True,
        },
        "status": "WAITING_FOR_USER",
        "active_field": primary,
    }
    decorations = await build_rdd_decorations(collected, set(group_missing))
    if decorations:
        updates["response_decorations"] = decorations
    return updates


# ---------------------------------------------------------------------------
# Node
# ---------------------------------------------------------------------------


@trace_node("missing_field", "AGENT")
async def missing_field_node(state: ServiceRequestState) -> dict[str, Any]:
    """Ask for exactly one missing required field per turn.

    Also handles blocking validation errors (e.g. invalid date range) by asking
    the user to correct the offending field before the form can advance.
    """
    workflow_stage: str = state.get("workflow_stage") or "CREATE_SR"

    # ── Look up per-workflow config — fall back to handover defaults ──────────
    active_agent: str | None = state.get("active_agent")
    workflow_cfg = get_workflow_config(active_agent)

    # Gracefully degrade if an unrecognised stage arrives — but only for agents
    # that use the handover STAGE_REGISTRY (fitout agents have their own stages).
    if active_agent not in _FITOUT_CONFIRM_AGENTS and workflow_stage not in STAGE_REGISTRY:
        workflow_stage = "CREATE_SR"

    field_questions: dict[str, str] = (
        workflow_cfg.field_questions if workflow_cfg else HANDOVER_FIELD_QUESTIONS
    )
    _effective_backend_fields: frozenset[str] = (
        workflow_cfg.backend_fields if workflow_cfg else BACKEND_PROTECTED_FIELDS
    )
    _effective_auto_generated: frozenset[str] = (
        workflow_cfg.auto_generated_fields if workflow_cfg else _AUTO_GENERATED_FIELDS
    )
    _effective_lease_triggers: tuple[str, ...] = (
        workflow_cfg.lease_trigger_fields if workflow_cfg else _LEASE_TRIGGER_FIELDS
    )

    # required_fields is only used by the work_permit path (get_missing_fields signature).
    # For handover/fitout agents, all_missing is computed via their own schema functions.
    required: tuple[str, ...] = (
        STAGE_REGISTRY[workflow_stage].required_fields
        if workflow_stage in STAGE_REGISTRY
        else ()
    )
    collected: dict[str, Any] = state.get("collected_data") or {}
    validation_errors: list[dict[str, Any]] = state.get("validation_errors") or []
    documents: list[dict[str, Any]] = list(state.get("documents") or [])

    # ── Priority -1: pending "change field X" redirect ─────────────────────
    # Set by field_extraction_node when the user asked to revisit an already
    # answered field with no inline value yet. One-turn signal: always cleared
    # in the returned patch so the next turn's answer flows through the
    # normal active_field binding.
    #
    # This message is sent to the user verbatim (response_ui["verbatim"] =
    # True) rather than going through response_generation_node's LLM
    # rewrite step. That LLM rewrite is fed collected_data, which still
    # shows the field's OLD value at this point (nothing has changed it
    # yet) — it would "helpfully" read that as the field already being
    # answered and re-confirm the old value instead of asking for a new
    # one. Building the final user-facing text here, deterministically,
    # avoids that misread entirely.
    correction_pending_field: str | None = state.get("correction_pending_field")
    if correction_pending_field:
        label = _FIELD_LABELS.get(
            correction_pending_field, correction_pending_field
        )
        # Refuse fields the merge layer will not let the user overwrite.
        # Backend-derived fields come from the lease lookup, and once the lease
        # is resolved its identifiers are authoritative (see
        # ``_LEASE_CONFIRMED_FIELDS`` in merge_state_node). Asking "what would
        # you like to change it to?" for one of these promises a change that is
        # silently discarded on the next turn.
        _locked = set(_effective_backend_fields)
        if collected.get("lease_id"):
            _locked |= {"lease_code", "mall", "brand"}
        if correction_pending_field in _locked:
            refusal = (
                f"{label} comes from the lease record and can't be edited here. "
                f"To use a different lease, start a new handover request. "
                f"Is there anything else you'd like to change?"
            )
            return {
                "response_message": refusal,
                "response_ui": {
                    "type": "message",
                    "message": refusal,
                    "verbatim": True,
                },
                "status": "WAITING_FOR_USER",
                "active_field": None,
                "correction_pending_field": None,
            }
        current_value = collected.get(correction_pending_field)
        display_value = format_field_display_value(correction_pending_field, current_value)
        # The inspection window carries a clock, so state it and let the user
        # change both halves in one answer. Without this the prompt showed a
        # bare date, asked for a bare date, and the time looked unchangeable.
        if correction_pending_field in _WINDOW_DATE_FIELDS:
            clock = _window_clock_display(collected, correction_pending_field)
            if display_value not in (None, "", []) and clock:
                display_value = f"{display_value} {clock}"
        if display_value not in (None, "", []):
            question = f"Current {label}: {display_value}. What would you like to change it to?"
        else:
            question = f"What would you like to set as the {label}?"
        if correction_pending_field in _WINDOW_DATE_FIELDS:
            question += (
                " Give the date as DD/MM/YYYY, and add a time if you want to "
                "change that too — e.g. '21/08/2026 6:00 PM'."
            )
        elif correction_pending_field in _SINGLE_DATE_FIELDS:
            question += " Use DD/MM/YYYY format, e.g. '21/08/2026'."
        correction_update: dict[str, Any] = {
            "missing_fields": [correction_pending_field],
            "response_message": question,
            "response_ui": {
                "type": "text_question",
                "field": correction_pending_field,
                "message": question,
                "verbatim": True,
            },
            "status": "WAITING_FOR_USER",
            "active_field": correction_pending_field,
            "correction_pending_field": None,
        }
        decorations = _CORRECTION_FIELD_DECORATIONS.get(correction_pending_field)
        if decorations:
            correction_update["response_decorations"] = decorations
        return correction_update

    # After optional Rejected/Other upload, clear the slot so Meeting Report
    # can be selected next (Submit still requires the Meeting Report).
    if workflow_stage == "RDD_REVIEW":
        collected = maybe_reset_rdd_upload_slot(collected, documents)

    # Handover schema owns stage-aware missing logic (e.g. conditional document_status).
    # Fitout confirm agents have their own required-field logic driven by sub_category.
    if (active_agent or "") == "work_permit_agent":
        all_missing: list[str] = get_missing_fields(collected, list(required))
    elif active_agent in _FITOUT_CONFIRM_AGENTS:
        sub_category: str = state.get("sub_category") or ""  # type: ignore[assignment]
        all_missing = fitout_confirm_get_missing_fields(workflow_stage, sub_category, collected)
    else:
        all_missing = handover_get_missing_fields(workflow_stage, collected)

    backend_missing = [f for f in all_missing if f in _effective_backend_fields]
    # Auto-generated fields are excluded from both lists — the bot must not ask
    # for them directly, and they are not resolved via lease lookup.
    user_missing = [
        f for f in all_missing
        if f not in _effective_backend_fields and f not in _effective_auto_generated
    ]

    next_field: str | None = None
    next_question: str | None = None
    # When True, response_generation must not LLM-rewrite the question (keeps
    # the concrete original date in the user-facing message).
    verbatim_question = False

    # ── Priority 0: non-required blocking validation errors ───────────────
    # "required" failures mean a field is simply absent — those are handled by
    # the backend_missing / user_missing branches below which know how to ask
    # for the right field (or resolve via lease lookup).
    #
    # Non-required failures (date_range, enum, …) mean the field IS present
    # but has an invalid value.  These must be corrected before the workflow
    # can advance, so they take priority even when other required fields are
    # still missing (e.g. endDate < startDate must be fixed before we ask for
    # inspection_done_by).
    non_required_blocking = [
        e for e in validation_errors
        if e.get("blocking", True)
        and e.get("validation_type") != "required"
        and e.get("validation_type") not in _NON_FIELD_VALIDATION_TYPES
        and e.get("field") not in (None, "", "_documents")
    ]
    if non_required_blocking:
        # Pick the first non-required blocking error and ask the user to correct it.
        first_error = non_required_blocking[0]
        v_type = first_error.get("validation_type", "")
        error_field = first_error.get("field", "")

        if v_type in _VALIDATION_ERROR_QUESTIONS:
            next_field, next_question = _VALIDATION_ERROR_QUESTIONS[v_type]
            # Name the value that was rejected. The generic hint alone produces
            # a byte-identical message every turn, so a user who retypes the
            # same bad date sees no sign that anything was read or refused.
            if v_type == "date_range" and next_field == "endDate":
                bad_end = str(collected.get("endDate") or "").strip()
                start_raw = str(collected.get("startDate") or "").strip()
                if bad_end and start_raw:
                    start_display = _format_base_date_for_display(start_raw)
                    end_display = _format_base_date_for_display(bad_end)
                    if start_display == end_display:
                        # A one-day inspection is legitimate — the clock is what
                        # is wrong, so ask about the clock rather than repeating
                        # a date question the user already answered correctly.
                        start_clock = _window_clock_display(collected, "startDate")
                        end_clock = _window_clock_display(collected, "endDate")
                        next_question = (
                            f"The inspection starts and ends on {start_display}, "
                            f"so the end time ({end_clock or 'not set'}) has to be "
                            f"later than the start time ({start_clock or 'not set'}). "
                            f"Give a later time on the same day — e.g. "
                            f"'{start_display} 6:00 PM' — or a later end date."
                        )
                    else:
                        next_question = (
                            f"The end date you entered ({end_display}) is not after "
                            f"the start date ({start_display}). Please enter an end "
                            f"date later than {start_display} in DD/MM/YYYY format, "
                            f"or the same date with a later time — e.g. "
                            f"'{start_display} 6:00 PM'."
                        )
                    verbatim_question = True
        elif error_field and error_field not in _effective_backend_fields:
            next_field = error_field
            next_question = field_questions.get(
                error_field,
                f"Please correct the value for: {error_field}",
            )

    elif backend_missing and not collected.get("lease_id"):
        # Backend-derived fields are unresolved and the lease is not identified
        # yet — ask for a lease trigger so ``lease_lookup_node`` can fill them.
        # Once lease_id is set the lease is already resolved (form is populated).
        # Do not fall through to ``company`` just because city/units/etc. came
        # back empty for that lease — that re-asks tenant/company QA already see.
        for trigger in _effective_lease_triggers:
            if not collected.get(trigger):
                next_field = trigger
                next_question = field_questions.get(trigger)
                break
        # If all trigger fields are already present the lease lookup simply
        # hasn't run yet — nothing more to ask; leave next_field as None.

    elif _should_ask_guided_tour(collected, workflow_cfg, all_missing):
        # Asked once per draft, before the first field question of any
        # collection stage — after the lease is resolved on CREATE, and
        # straight away on the review stages, which have no lease to find.
        ask = guided_tour_service.build_question_response(collected)
        ask["missing_fields"] = list(all_missing)
        return ask

    # RDD: once the guideline link is captured, prompt for the doc upload
    # BEFORE asking for the date fields (actual_handover_date, fitout_*,
    # trading_date). Those dates are nested on the Meeting Report row per the
    # platform payload shape — asking for them without a doc uploaded first
    # leaves the user typing dates that have nowhere to attach. Mirrors FM's
    # "docs first, dates next" implicit ordering.
    elif (
        workflow_stage == "RDD_REVIEW"
        and collected.get("guideLineLink")
        and not _has_rdd_mandatory_upload(documents)
    ):
        ask = build_document_upload_ask_response(
            {
                **dict(state),
                "workflow_stage": workflow_stage,
                "collected_data": collected,
            }
        )
        if ask:
            ask["missing_fields"] = list(all_missing)
            ask["collected_data"] = collected
            return ask
        # If the decoration couldn't build (missing session/sr_id), fall
        # through to the user_missing branch below.

    elif _should_ask_handed_over_group(active_agent, workflow_stage, user_missing):
        # Already Trading: all four actual dates in one question. Nothing is
        # suggested — only the Mall Manager knows what already happened.
        group_missing = [f for f in HANDED_OVER_FIELD_GROUP if f in user_missing]
        return _build_handed_over_group_ask(collected, group_missing, all_missing)

    elif _should_ask_rdd_group(active_agent, workflow_stage, user_missing):
        # RDD review: the meeting outcome and the milestone dates are one
        # question. Returns directly — the shared question-building code below
        # assumes a single field per ask.
        group_missing = [f for f in RDD_REVIEW_FIELD_GROUP if f in user_missing]
        return await _build_rdd_group_ask(collected, group_missing, all_missing)

    elif _should_ask_inspection_group(active_agent, workflow_stage, user_missing):
        # Short flow: the inspection window and the inspector are one question,
        # not three turns. Returns directly — the shared question-building code
        # below assumes a single field per ask.
        group_missing = [f for f in INSPECTION_FIELD_GROUP if f in user_missing]
        return _build_inspection_group_ask(collected, group_missing, all_missing)

    elif user_missing:
        # All backend fields are resolved; ask for the first user-supplied gap.
        next_field = user_missing[0]
        next_question = field_questions.get(
            next_field,
            f"Please provide: {next_field}",
        )

        # CREATE/APPROVE: form may pre-fill yes/no — still ask the user to
        # confirm in chat before collecting planned/actual dates.
        if (
            active_agent in _FITOUT_CONFIRM_AGENTS
            and workflow_stage in ("CREATE_SR", "APPROVE_SR")
            and next_field in (
                "fitout_start_done",
                "fitout_complete_done",
                "trading_done",
            )
            and not collected.get("_approve_radio_confirmed")
        ):
            radio_val = str(collected.get(next_field) or "").strip().lower()
            _RADIO_TO_ACTUAL = {
                "fitout_start_done": "actual_fitout_start_date",
                "fitout_complete_done": "actual_fitout_complete_date",
                "trading_done": "actual_trading_date",
            }
            phrasing = _RADIO_PHRASING.get(next_field, _RADIO_PHRASING_FALLBACK)
            subject = phrasing["subject"]
            actual_key = _RADIO_TO_ACTUAL.get(next_field, "")
            actual_raw = str(collected.get(actual_key) or "").strip()
            actual_display = (
                _format_base_date_for_display(actual_raw) if actual_raw else ""
            )
            if radio_val == "yes" and actual_display:
                next_question = (
                    f"{subject} is currently marked as **{phrasing['affirmed']}** "
                    f"(actual date {actual_display}). "
                    f"Reply **yes** to confirm, or **no** if {phrasing['not_yet']} "
                    f"and you need to add revised planned dates."
                )
            elif radio_val == "yes":
                next_question = (
                    f"{subject} is currently marked as **{phrasing['affirmed']}**. "
                    f"Reply **yes** to confirm, or **no** if {phrasing['not_yet']} "
                    f"and you need to add revised planned dates."
                )
            elif radio_val == "no":
                next_question = (
                    f"{subject} is currently marked as **{phrasing['negated']}**. "
                    f"Reply **no** to keep that and review/update planned dates, "
                    f"or **yes** if {phrasing['now']} and you have an actual date."
                )
            else:
                next_question = (
                    f"{phrasing['question']} Reply **yes** or **no**. "
                    f"If no, you can add revised planned dates."
                )

        # For FOS/FOE/Trading actual-date fields, if an original/base date is
        # already in collected_data (or form-synced planned fallback), ask the
        # user to confirm it or enter a different one.
        elif (
            active_agent in _FITOUT_CONFIRM_AGENTS
            and next_field in _ACTUAL_TO_ORIGINAL_DATE_CANDIDATES
        ):
            original_raw = _resolve_original_date(collected, next_field)
            if original_raw:
                display_base = _format_base_date_for_display(original_raw)
                next_question = (
                    f"The original date is **{display_base}**. "
                    f"Would you like to confirm this date, or provide a new one? "
                    f"Reply **confirm** to use it, or enter a different date in DD/MM/YYYY format."
                )
                verbatim_question = True

        # The Handover Meeting Report outcome is answered with the two chips
        # below, so the prompt is built here rather than left to the LLM
        # rewrite: field_questions holds an instruction *about* asking ("Ask the
        # RDD Project Manager for...") and when that rewrite does not land the
        # chips render with no question above them at all.
        elif next_field == "document_status":
            next_question = (
                "Please select the document status for the Handover Meeting "
                "Report: **Approve** if the report is accepted as-is, or "
                "**Approve with changes** if it needs amendments."
            )
            verbatim_question = True

    # When the next question is driven by a non-required validation error (e.g.
    # date_range), the correction target field may already be in collected_data
    # (it's just invalid).  Prepend it to missing_fields so that
    # field_extraction_node's hint points to the right field and the user's
    # corrected value is routed there — not to the next truly-missing field.
    effective_missing = list(all_missing)
    if next_field and next_field not in effective_missing and non_required_blocking:
        effective_missing.insert(0, next_field)

    updates: dict[str, Any] = {"missing_fields": effective_missing}
    if workflow_stage == "RDD_REVIEW":
        updates["collected_data"] = collected

    if next_question:
        updates["response_message"] = next_question
        updates["response_ui"] = {
            "type": "text_question",
            "field": next_field,
            "message": next_question,
            **({"verbatim": True} if verbatim_question else {}),
        }
        updates["status"] = "WAITING_FOR_USER"
        # Persist so the next turn's extraction can bind a bare answer to this
        # field even though load_session clears missing_fields.
        updates["active_field"] = next_field

        # Type-first RDD upload: offer chips for document type / meeting status.
        if next_field == "rdd_upload_doc_type":
            updates["response_decorations"] = {
                "suggestions": {
                    "kind": "labeled_options",
                    "options": [
                        {
                            "label": "Handover Meeting Report",
                            "value": RDD_DOC_TYPE_MEETING,
                            "action": "select_rdd_doc_meeting",
                        },
                        {
                            "label": "Rejected Fitout and Handover Report",
                            "value": RDD_DOC_TYPE_REJECTED,
                            "action": "select_rdd_doc_rejected",
                        },
                        {
                            "label": "Other",
                            "value": RDD_DOC_TYPE_OTHER,
                            "action": "select_rdd_doc_other",
                        },
                    ],
                }
            }
        elif next_field == "inspection_done_by":
            # Two fixed choices for the CREATE_SR inspector role. Chip values
            # match the enum codes the extractor / merge_state accepts, so a
            # click resolves the field directly on the next turn.
            updates["response_decorations"] = {
                "suggestions": {
                    "kind": "labeled_options",
                    "options": [
                        {
                            "label": "FM Manager",
                            "value": "FM_MANAGER",
                            "action": "select_inspection_done_by_fm_manager",
                        },
                        {
                            "label": "Operations",
                            "value": "OPERATIONS",
                            "action": "select_inspection_done_by_operations",
                        },
                    ],
                }
            }
        elif next_field == "already_handovered":
            updates["response_decorations"] = {
                "suggestions": {
                    "kind": "labeled_options",
                    "options": [
                        {
                            "label": "Already Trading",
                            "value": "already_trading",
                            "action": "select_handover_type_already_trading",
                        },
                        {
                            "label": "Initiate Handover",
                            "value": "initiate_handover",
                            "action": "select_handover_type_initiate_handover",
                        },
                    ],
                }
            }
        elif next_field == "document_status":
            updates["response_decorations"] = {
                "suggestions": {
                    "kind": "labeled_options",
                    "options": [
                        {
                            "label": "Approve",
                            "value": "APPROVED",
                            "action": "select_rdd_status_approved",
                        },
                        {
                            "label": "Approve with changes",
                            "value": "APPROVED_WITH_CHANGES",
                            "action": "select_rdd_status_approved_with_changes",
                        },
                    ],
                }
            }
        elif next_field in (
            "fitout_start_done",
            "fitout_complete_done",
            "trading_done",
        ):
            # Value-only chips → FE sends "yes"/"no" as the user message; active_field
            # binds the answer to the radio (same path as typing yes/no).
            updates["response_decorations"] = {
                "suggestions": {
                    "kind": "labeled_options",
                    "options": [
                        {"label": "Yes", "value": "yes"},
                        {"label": "No", "value": "no"},
                    ],
                }
            }
    else:
        # Fields complete — if FM/RDD still need documents, ask for upload
        # instead of silently advancing (document_count was skipped above).
        doc_count_blocking = any(
            e.get("blocking", True) and e.get("validation_type") == "document_count"
            for e in validation_errors
        )
        if doc_count_blocking and workflow_stage in ("FM_REVIEW", "RDD_REVIEW"):
            ask = build_document_upload_ask_response(
                {
                    **dict(state),
                    "workflow_stage": workflow_stage,
                    "collected_data": collected,
                }
            )
            if ask:
                ask["missing_fields"] = effective_missing
                if workflow_stage == "RDD_REVIEW":
                    ask["collected_data"] = collected
                return ask

        # Nothing left to ask — signal readiness for the next pipeline step.
        updates["response_message"] = ""
        updates["status"] = "IN_PROGRESS"
        updates["active_field"] = None

    return updates

