"""Entry node for the Fitout Progress SR workflow.

Responsibilities
----------------
- First node on every turn where ``active_agent == "rdd_fitout_progress_agent"``.
- Refuse a create from a role that the web form would not offer it to.
- Resolve a bare percentage — typed or tapped — into ``corrected_fields``
  without an LLM round-trip.
- Detect confirmation responses (yes/no) when ``confirmation_status == PENDING``.
- Handle UI action overrides (confirm, cancel, …).
- Explain the actions this workflow does not have, instead of earning a 400.
- Stage-pin on the first turn: ``CREATE_SR`` or ``APPROVE_SR``.
- Stale-clear when a new request starts after a terminal stage.

Non-responsibilities
--------------------
- MUST NOT collect or validate form fields (beyond the percentage shortcut).
- MUST NOT call the LLM.
- MUST NOT submit service requests.

Why this workflow's cancel is different
---------------------------------------
The other fit-out agents cancel an open SR by PATCHing ``status: CANCELLED``.
FITOUT_PROGRESS has no such action — level 1 allows ``UPDATE / APPROVE / REJECT``
only — and a created SR has usually already written ``leases.fitout_progress``
inside the create transaction. So a cancel before submission clears the draft,
and a cancel afterwards is explained rather than attempted.
"""

from __future__ import annotations

import re
from typing import Any

import structlog

from app.agents.graph.state import ServiceRequestState
from app.agents.schemas.fitout_progress_schema import (
    PROGRESS_CONFIRMED_KEY,
    PROGRESS_FIELD,
    TERMINAL_STAGES,
)
from app.agents.services.field_correction_service import draft_has_content
from app.agents.services.fitout_progress_suggestions_service import (
    format_percentage,
    parse_percentage,
    parse_percentage_answer,
)
from app.agents.services.sr_release import released_backend_refs
from app.observability.decorators import trace_node

log = structlog.get_logger(__name__)

AGENT_NAME = "rdd_fitout_progress_agent"

CREATE_INTENT = "CREATE_FITOUT_PROGRESS"
APPROVE_INTENT = "APPROVE_FITOUT_PROGRESS"

# Creation only. Acting on an SR that already exists is governed by the
# platform's own operation assignment, not by this map.
#
# DD_ENGINEER alone, because the web form hides the sub-category from every
# other role (``filterSubCategoryList`` requires DD_ENGINEER) and the assistant
# must not offer a path the form refuses. ADMIN is the usual superuser escape.
_CREATE_ROLES: frozenset[str] = frozenset({"DD_ENGINEER", "ADMIN"})

_ROLE_LABELS: dict[str, str] = {
    "DD_ENGINEER": "RDD Project Manager",
    "MALL_MANAGER": "Mall Manager",
    "OPERATIONS": "Operations",
    "FM_MANAGER": "FM Manager",
    "ADMIN": "Admin",
}

_CONFIRM_ACTIONS: frozenset[str] = frozenset(
    {
        "confirm",
        "confirm_create_sr",
        "confirm_create_fitout_progress",
        "confirm_approve_sr",
        "confirm_approve_fitout_progress",
        "confirm_reject_fitout_progress",
        "approve",
        "update_and_approve",
    }
)

# Starting a rejection, which is a decision about the request rather than an
# answer to the question, so it is recorded and then confirmed.
REJECT_ACTIONS: frozenset[str] = frozenset({"reject_fitout_progress", "reject"})

# The key that tells the payload builder which way this decision goes.
FITOUT_PROGRESS_ACTION_KEY = "fitout_progress_action"

# Deliberately excludes "send it back": that is someone asking for more
# information, which this workflow has no step for, and the explanation below
# serves them better than a rejection card.
_REJECT_SR_PHRASES: frozenset[str] = frozenset(
    {
        "reject", "reject it", "reject this", "reject the request",
        "reject this request", "decline", "decline it", "turn it down",
        "not approved",
    }
)

_CANCEL_ACTIONS: frozenset[str] = frozenset(
    {"cancel_sr", "cancel_create_sr", "cancel_create_fitout_progress"}
)

_CONFIRM_PHRASES: frozenset[str] = frozenset(
    {
        "yes", "yep", "yeah", "yup", "confirm", "confirmed", "submit",
        "proceed", "correct", "looks good", "that's correct", "thats correct",
        "go ahead", "record it", "ok", "okay", "sure", "agree",
    }
)

_REJECT_PHRASES: frozenset[str] = frozenset(
    {
        "no", "nope", "nah", "change", "update", "edit", "modify", "wrong",
        "incorrect", "not right", "that's wrong", "thats wrong", "fix",
        "correct it", "i want to change", "let me change",
    }
)

_CANCEL_SR_PHRASES: frozenset[str] = frozenset(
    {
        "cancel", "cancel request", "cancel sr", "cancel this request",
        "cancel the request", "i want to cancel", "please cancel", "abort",
    }
)

_CANCEL_WORKFLOW_PHRASES: frozenset[str] = frozenset(
    {"start over", "restart", "new request", "different request", "begin again", "reset"}
)

# Actions the platform workflow does not have at level 1, where allowed_actions
# is UPDATE / APPROVE / REJECT. Answered locally: sending any of them earns a
# 400 the workflow JSON already told us about.
_UNSUPPORTED: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"\b(reassign|assign (it |this )?to|hand (it )?(over )?to)\b",
            re.IGNORECASE,
        ),
        (
            "A fit-out progress record has a single approver step, so it can't "
            "be reassigned. I can record the percentage, or you can ask the RDD "
            "Project Manager who owns the lease to record it."
        ),
    ),
    (
        re.compile(
            r"\b(attach|upload|photo|photos|picture|document|documents|file)\b",
            re.IGNORECASE,
        ),
        (
            "Fit-out progress records don't carry attachments — just the "
            "percentage, and a note if you want one. Site photos belong on the "
            "fit-out inspection requests."
        ),
    ),
    (
        re.compile(
            r"\b(action required|more info|more information|ask the tenant"
            r"|send it back)\b",
            re.IGNORECASE,
        ),
        (
            "There's no action-required step on this request — nobody is waiting "
            "to be asked for more detail. I can record a percentage, or reject "
            "the request if the number is wrong."
        ),
    ),
    (
        re.compile(
            r"\b(save (it )?(without|but don'?t)|just save"
            r"|save (it )?for later|don'?t approve)\b",
            re.IGNORECASE,
        ),
        (
            "Saving and approving are the same step here. Give me the "
            "percentage and I'll record it — it goes straight onto the lease."
        ),
    ),
)

# Naming the workflow again is restating the request, not answering the open
# question: "update fitout progress t0112745 30%" typed over the review card.
_RESTATED_REQUEST_RE = re.compile(r"\bfit[\s-]?out\s+progress\b", re.IGNORECASE)

# A lease code is letters and digits together (t0112745, DUMMY-CUST-001); a
# percentage is digits alone, so "30%" never counts as one.
_LEASE_TOKEN_RE = re.compile(r"\b(?=[\w-]*[A-Za-z])(?=[\w-]*\d)[\w-]{4,}\b")
_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")

_CLEARED_DRAFT_MESSAGE = (
    "Nothing was recorded — I've cleared the draft. "
    "What would you like to do next?"
)


def _role_names(state: ServiceRequestState) -> set[str]:
    auth = state.get("auth_context")
    return {str(r).upper() for r in (getattr(auth, "roles", None) or ())}


def _contains_phrase(message: str, phrases: frozenset[str]) -> bool:
    lowered = message.lower()
    return any(
        bool(re.search(r"\b" + re.escape(phrase) + r"\b", lowered))
        for phrase in phrases
    )


def _clear_all_state() -> dict[str, Any]:
    """Zero every workflow field, and tell the client to clear the form with it."""
    return {
        "active_agent": None,
        "intent": None,
        "service_category": None,
        "sub_category": None,
        "workflow_stage": None,
        "confirmation_status": None,
        "confirmation_required": False,
        "collected_data": {},
        "extracted_fields": {},
        "missing_fields": [],
        "validation_errors": [],
        "selected_lease": None,
        "lease_matches": [],
        "backend_refs": {},
        "documents": [],
        "active_field": None,
        "correction_pending_field": None,
        "response_ui": {
            "type": "message",
            "verbatim": True,
            "reset_conversation": True,
            "cancelled": True,
        },
        "status": "WAITING_FOR_USER",
    }


def _refuse_unauthorised_create(state: ServiceRequestState) -> dict[str, Any] | None:
    """Refuse a create the caller's role may not raise, or None to allow.

    Checked at the workflow boundary, before the lease lookup runs — otherwise
    the user is walked through the whole flow to build a request the platform
    (and the web form) would never accept from them.
    """
    backend_refs = state.get("backend_refs") or {}
    if backend_refs.get("sr_id") or state.get("workflow_stage") == "APPROVE_SR":
        return None
    if str(state.get("intent") or "") == APPROVE_INTENT:
        return None

    roles = _role_names(state)
    # An unresolved role stays permissive, as every other entry guard does:
    # they deny only when a role is actually present.
    if not roles or roles & _CREATE_ROLES:
        return None

    log.info("fitout_progress_entry.create_denied_for_role", roles=sorted(roles))
    patch = _clear_all_state()
    patch["response_message"] = (
        "Recording fit-out progress isn't available for your role — the "
        f"{_ROLE_LABELS['DD_ENGINEER']} records it. I can still help you with "
        "handover or fit-out confirmation requests, or answer questions about "
        "the platform."
    )
    patch["response_ui"] = {"type": "message", "verbatim": True}
    patch["response_decorations"] = {}
    return patch


def _direct_set_percentage(state: ServiceRequestState) -> dict[str, Any] | None:
    """Bind a bare percentage to the field without an LLM round-trip.

    The chips are value-only, so a tap arrives as the plain number the user
    appears to have typed — the same path as typing it. Both land here while
    the percentage question is the open one, which is why a chip costs no
    extraction turn and cannot be re-read as some other field.
    """
    if str(state.get("active_field") or "") != PROGRESS_FIELD:
        return None
    message = str(state.get("user_message") or "").strip()
    if not message:
        return None
    value = parse_percentage_answer(message)
    if value is None:
        return None

    collected = dict(state.get("collected_data") or {})
    collected[PROGRESS_CONFIRMED_KEY] = True
    log.info("fitout_progress_entry.percentage_direct_set", value=value)
    return {
        "collected_data": collected,
        # corrected_fields is the maximum-confidence channel merge_state honours
        # for values that came from a deliberate user action rather than the LLM.
        "corrected_fields": {PROGRESS_FIELD: format_percentage(value)},
        "confirmation_status": None,
        "status": "IN_PROGRESS",
    }


def _unsupported_request(message: str) -> str | None:
    for pattern, reply in _UNSUPPORTED:
        if pattern.search(message):
            return reply
    return None


@trace_node("fitout_progress_entry", "AGENT")
async def fitout_progress_entry_node(state: ServiceRequestState) -> dict[str, Any]:
    """Parse user intent at the boundary of the Fitout Progress workflow."""
    user_message: str = state.get("user_message") or ""
    confirmation_status: str | None = state.get("confirmation_status")
    action_override: str | None = state.get("action_override")  # type: ignore[assignment]
    backend_refs: dict[str, Any] = dict(state.get("backend_refs") or {})
    sr_id = backend_refs.get("sr_id")

    # ── Role guard — before anything that could start collecting ───────────
    refusal = _refuse_unauthorised_create(state)
    if refusal is not None:
        return refusal

    # ── 0. Explicit UI actions ─────────────────────────────────────────────
    if action_override in _CONFIRM_ACTIONS:
        log.info("fitout_progress_entry.action_override_confirm", action=action_override)
        # Clearing the stale WAITING_FOR_USER from the turn that rendered the
        # card is what stops routing short-circuiting back into the question.
        patch: dict[str, Any] = {
            "confirmation_status": "CONFIRMED",
            "status": "IN_PROGRESS",
        }
        # "Approve 65%" is offered instead of a card, so the tap is both the
        # answer and the decision: without marking the stored percentage as
        # answered, validation reports it missing and submission is refused.
        collected = dict(state.get("collected_data") or {})
        if collected.get(PROGRESS_FIELD) not in (None, ""):
            collected[PROGRESS_CONFIRMED_KEY] = True
            patch["collected_data"] = collected
        return patch

    if action_override in REJECT_ACTIONS or (
        state.get("workflow_stage") == "APPROVE_SR"
        and confirmation_status != "PENDING"
        and _contains_phrase(user_message, _REJECT_SR_PHRASES)
    ):
        log.info("fitout_progress_entry.reject_started", sr_id=sr_id)
        return _start_rejection(state)

    if action_override == "cancel":
        # "Change the percentage" on the review card — and, on a rejection card,
        # the way back. The armed rejection has to be cleared with it, or the
        # next confirm would reject a request the user came back to approve.
        log.info("fitout_progress_entry.action_override_reject")
        patch = {"confirmation_status": "REJECTED", "status": "IN_PROGRESS"}
        if backend_refs.get(FITOUT_PROGRESS_ACTION_KEY):
            backend_refs.pop(FITOUT_PROGRESS_ACTION_KEY, None)
            patch["backend_refs"] = backend_refs
        return patch

    if action_override in _CANCEL_ACTIONS:
        log.info("fitout_progress_entry.action_override_cancel", sr_id=sr_id)
        return _cancel(sr_id)

    # ── 1. A tapped or typed percentage ───────────────────────────────────
    if confirmation_status != "PENDING":
        direct = _direct_set_percentage(state)
        if direct is not None:
            return direct

    # ── 2. Cancel / restart by text ───────────────────────────────────────
    if _contains_phrase(user_message, _CANCEL_SR_PHRASES):
        log.info("fitout_progress_entry.text_cancel", sr_id=sr_id)
        return _cancel(sr_id)

    if _contains_phrase(user_message, _CANCEL_WORKFLOW_PHRASES):
        log.info("fitout_progress_entry.workflow_reset")
        patch = _clear_all_state()
        patch["response_message"] = (
            "Sure — I've cleared everything. What would you like to do next?"
        )
        return patch

    # ── 3. The request restated over an open draft ────────────────────────
    restart = _restart_restated_request(state)
    if restart is not None:
        return restart

    # ── 4. Confirmation response parsing ──────────────────────────────────
    if confirmation_status == "PENDING":
        if _contains_phrase(user_message, _CONFIRM_PHRASES):
            log.info("fitout_progress_entry.confirmation_accepted")
            return {"confirmation_status": "CONFIRMED", "status": "IN_PROGRESS"}
        # A number on the review card is a correction, not an answer to
        # "confirm?" — take it as the new percentage and re-render the card.
        revised = parse_percentage_answer(user_message)
        if revised is not None:
            collected = dict(state.get("collected_data") or {})
            collected[PROGRESS_CONFIRMED_KEY] = True
            log.info("fitout_progress_entry.percentage_revised", value=revised)
            return {
                "collected_data": collected,
                "corrected_fields": {PROGRESS_FIELD: format_percentage(revised)},
                "confirmation_status": "REJECTED",
                "status": "IN_PROGRESS",
            }
        if _contains_phrase(user_message, _REJECT_PHRASES):
            log.info("fitout_progress_entry.confirmation_rejected")
            return {"confirmation_status": "REJECTED", "status": "IN_PROGRESS"}
        log.info("fitout_progress_entry.confirmation_ambiguous")
        return {
            "response_message": (
                "Please reply **yes** to record it, or give me a different "
                "percentage."
            ),
        }

    # ── 5. Actions this workflow does not have ────────────────────────────
    unsupported = _unsupported_request(user_message)
    if unsupported is not None:
        log.info("fitout_progress_entry.unsupported_request")
        return {
            "status": "WAITING_FOR_USER",
            "response_message": unsupported,
            "response_ui": {"type": "message", "verbatim": True},
        }

    # ── 6. Stale-clear for terminal stages or a previous failure ──────────
    if str(state.get("intent") or "") == CREATE_INTENT and (
        state.get("workflow_stage") in TERMINAL_STAGES
        or state.get("status") == "FAILED"
    ):
        log.info(
            "fitout_progress_entry.stale_sr_cleared",
            workflow_stage=state.get("workflow_stage"),
        )
        # Partial clear: the agent and the stage stay pinned so this same turn
        # continues into field_extraction and starts the new record. A full
        # clear would set WAITING_FOR_USER and swallow the message.
        return {
            "workflow_stage": "CREATE_SR",
            "confirmation_status": None,
            "confirmation_required": False,
            "collected_data": {},
            "extracted_fields": {},
            "missing_fields": [],
            "validation_errors": [],
            "selected_lease": None,
            "lease_matches": [],
            "backend_refs": released_backend_refs(state),
            "documents": [],
            "response_ui": {},
            "status": "IN_PROGRESS",
            "active_field": None,
        }

    # ── 7. Stage-pin on the first turn ────────────────────────────────────
    #
    # ``status`` is cleared with the stage. The routers short-circuit a turn
    # whose persisted status is WAITING_FOR_USER straight to
    # response_generation, and that status survives a switch of agent: asking
    # for fit-out progress in a session that was waiting on another flow's
    # question meant this turn never reached field_extraction, so the lease and
    # the percentage in "update fitout progress t0112745 30 %" were both
    # dropped and the assistant asked for the lease code again.
    #
    # ``active_field`` goes with it, for the same reason: it is the other
    # flow's open question, and while it is set the correction detector reads
    # "update fitout progress …" as a request to change something rather than
    # as the request itself — which discards the message.
    #
    # Safe because the pin only fires when no stage is set — the start of a
    # request, which by definition has nothing of its own pending.
    if state.get("active_agent") == AGENT_NAME and not state.get("workflow_stage"):
        fresh = {"status": "IN_PROGRESS", "active_field": None}
        if sr_id:
            log.info("fitout_progress_entry.routing_to_approve_sr", sr_id=sr_id)
            return {"workflow_stage": "APPROVE_SR", **fresh}
        if str(state.get("intent") or "") == APPROVE_INTENT:
            log.info("fitout_progress_entry.routing_to_approve_sr_via_intent")
            return {"workflow_stage": "APPROVE_SR", **fresh}
        log.info("fitout_progress_entry.routing_to_create_sr")
        return {"workflow_stage": "CREATE_SR", **fresh}

    return {}


def _restart_restated_request(state: ServiceRequestState) -> dict[str, Any] | None:
    """Start the create over when the user restates it, or None.

    Retyping "update fitout progress t0112745 30%" over the review card used
    to read "update" as a rejection of the card, and then field_extraction
    read it as a correction verb with no field named — so nothing was
    extracted, no card came back and the reply was improvised. The message is
    the whole request again, possibly with a new lease or number, so it is
    treated exactly as it was on the first turn: the draft is dropped and
    extraction reads the sentence afresh, which is the path that already
    goes straight to the card.

    Without a lease code ("fitout progress is 90") the lease stays and only
    the number is taken, as a bare number on the card is; dropping the draft
    there would ask for a lease the user never meant to change.

    Only while creating and before anything is submitted — an approval is
    about an SR that already exists, and there is nothing to restart there.
    Session bookkeeping (underscore keys such as the guided-tour choice)
    survives, apart from the "answered" flag the new reading sets again.
    """
    if state.get("workflow_stage") != "CREATE_SR":
        return None
    if (state.get("backend_refs") or {}).get("sr_id"):
        return None
    user_message = str(state.get("user_message") or "")
    if not _RESTATED_REQUEST_RE.search(user_message):
        return None
    collected = state.get("collected_data") or {}
    if not draft_has_content(collected):
        return None

    if not _LEASE_TOKEN_RE.search(user_message):
        numbers = _NUMBER_RE.findall(user_message)
        value = parse_percentage(numbers[0]) if len(numbers) == 1 else None
        if value is None:
            return None
        revised = dict(collected)
        revised[PROGRESS_CONFIRMED_KEY] = True
        log.info("fitout_progress_entry.request_restated_percentage", value=value)
        return {
            "collected_data": revised,
            "corrected_fields": {PROGRESS_FIELD: format_percentage(value)},
            "confirmation_status": "REJECTED",
            "status": "IN_PROGRESS",
            "active_field": None,
        }

    log.info(
        "fitout_progress_entry.request_restated",
        confirmation_status=state.get("confirmation_status"),
    )
    bookkeeping = {
        key: value
        for key, value in collected.items()
        if key.startswith("_") and key != PROGRESS_CONFIRMED_KEY
    }
    return {
        "workflow_stage": "CREATE_SR",
        "confirmation_status": None,
        "confirmation_required": False,
        "collected_data": bookkeeping,
        "extracted_fields": {},
        "missing_fields": [],
        "validation_errors": [],
        "selected_lease": None,
        "lease_matches": [],
        "response_ui": {},
        "status": "IN_PROGRESS",
        "active_field": None,
        "correction_pending_field": None,
    }


def _start_rejection(state: ServiceRequestState) -> dict[str, Any]:
    """Arm a rejection and let the flow render its confirmation card.

    Rejecting is terminal and the lease is left untouched, so it goes through
    the same one-tap confirmation an approval does. The reason is never a second
    question — ``REJECT`` has no ``require_fields`` — but a reason volunteered
    in the same breath travels with it as the actor comment.
    """
    backend_refs = dict(state.get("backend_refs") or {})
    backend_refs[FITOUT_PROGRESS_ACTION_KEY] = "reject"
    collected = dict(state.get("collected_data") or {})
    # The stored percentage is not being changed, so it counts as answered —
    # otherwise validation blocks the rejection on a missing required field.
    if collected.get(PROGRESS_FIELD) not in (None, ""):
        collected[PROGRESS_CONFIRMED_KEY] = True
    return {
        "backend_refs": backend_refs,
        "collected_data": collected,
        "confirmation_status": None,
        "status": "IN_PROGRESS",
    }


def _cancel(sr_id: Any) -> dict[str, Any]:
    """Cancel a draft, or explain why a recorded percentage cannot be cancelled.

    There is no CANCELLED action on this workflow, and by the time an SR exists
    the percentage is usually already on the lease, so attempting a PATCH would
    fail and telling the user it was cancelled would be a lie.
    """
    if sr_id:
        log.info("fitout_progress_entry.cancel_refused_after_submit", sr_id=sr_id)
        return {
            "status": "WAITING_FOR_USER",
            "response_message": (
                f"Service Request **{sr_id}** can't be cancelled — a fit-out "
                "progress record goes straight onto the lease. If the "
                "percentage is wrong, record a new one with the right number."
            ),
            "response_ui": {"type": "message", "verbatim": True},
        }
    patch = _clear_all_state()
    patch["response_message"] = _CLEARED_DRAFT_MESSAGE
    return patch
