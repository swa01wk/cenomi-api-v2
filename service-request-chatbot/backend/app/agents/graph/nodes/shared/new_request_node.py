"""Start a new service request from a session that has another SR open.

A turn that carries an ``sr_id`` normally goes straight to status sync and the
entry node of that SR's stage, before the supervisor classifies anything. That
is right for "approve", a date, a comment — but it swallowed an explicit request
for a *new* SR. A Mall Manager who opened an SR they had already submitted and
clicked "Review with AI" got its preview — correct — but typing "create
handover" in the same chat showed that preview again, and on SR 321005 (pending
FM) answered "You do not have permission to perform FM review actions": the
message had been handed to the SR on screen instead of starting a new one.

This node clears the open SR's context so the supervisor classifies the request
on a clean slate: the create flow starts in CREATE_SR with no SR attached, the
lease picker is offered, and the frontend moves the user from the details page
to the create form.

Non-responsibilities
--------------------
- MUST NOT call the LLM — detection is deterministic; the supervisor does the
  classifying, typos included.
- MUST NOT touch the platform SR — only the chat's pointer to it is dropped.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any

import structlog

from app.agents.graph.state import ServiceRequestState
from app.observability.decorators import trace_node

log = structlog.get_logger(__name__)

# Deliberately strong verbs only. "new", "start", "open" and "submit" all occur
# in ordinary review answers ("the new handover date…", "the handover start
# date", "submit the handover report") and would drop a reviewer's session.
_CREATE_VERB = re.compile(r"\b(create|raise|initiate)\b")

# The SR types the assistant can create.
_SR_NOUN = re.compile(
    r"\b(handover|handed over|fit[\s-]?out|trading|work permit|"
    r"service request|request|sr)\b"
)

# "handover" is typed fast and often wrong — the reports that surfaced this said
# "hanover" and "handoevr". A near-miss spelling counts; "handle" and "hand" do
# not come close enough.
_HANDOVER = "handover"
_HANDOVER_SIMILARITY = 0.8

# A question about how to create one is a help request, not a new request.
_QUESTION_OPENERS: tuple[str, ...] = (
    "how ", "what ", "why ", "when ", "where ", "who ", "can i ", "could i ",
)


def _names_a_handover(text: str) -> bool:
    return any(
        SequenceMatcher(None, word, _HANDOVER).ratio() >= _HANDOVER_SIMILARITY
        for word in re.findall(r"[a-z]+", text)
    )


def asks_for_a_new_service_request(message: str) -> bool:
    """True when *message* explicitly asks to create a service request."""
    text = " ".join(message.lower().split())
    if not text or text.startswith(_QUESTION_OPENERS):
        return False
    if not _CREATE_VERB.search(text):
        return False
    return bool(_SR_NOUN.search(text)) or _names_a_handover(text)


@trace_node("new_request", "AGENT")
async def new_request_node(state: ServiceRequestState) -> dict[str, Any]:
    """Drop the open SR so the supervisor classifies the request afresh."""
    refs: dict[str, Any] = dict(state.get("backend_refs") or {})
    log.info(
        "new_request.open_sr_released",
        sr_id=refs.get("sr_id"),
        workflow_stage=state.get("workflow_stage"),
    )
    # The caller's role is not a property of the SR; entry guards read it.
    kept_refs: dict[str, Any] = (
        {"user_role": refs["user_role"]} if refs.get("user_role") else {}
    )
    # Tell the save step to forget the SR too. The session draft keeps the last
    # sr_id it saw and restores it on every turn, so releasing it only here left
    # the very next message — the lease answer — re-attached to the old SR and
    # showing its preview instead of continuing the new request.
    if refs.get("sr_id"):
        kept_refs["released_sr_id"] = refs["sr_id"]
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
        "backend_refs": kept_refs,
        "documents": [],
        "active_field": None,
        "correction_pending_field": None,
        "response_ui": {},
        "response_decorations": {},
        "status": "IN_PROGRESS",
    }
