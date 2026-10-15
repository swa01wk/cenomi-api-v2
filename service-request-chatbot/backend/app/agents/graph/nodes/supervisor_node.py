"""Supervisor node — intent detection, classification, and agent routing.

Responsibilities
----------------
- Detect user intent by calling the LLM with the supervisor prompt.
- Classify the message into a ``SupervisorDecision`` (intent, confidence,
  service_category, sub_category, target_agent, reasoning).
- Maintain session continuity: if ``active_agent`` is already set in state,
  continue with it unless the user explicitly cancels or switches.
- Ask for clarification when confidence falls below ``CONFIDENCE_THRESHOLD``.
- Emit a trace run span and a detailed LLM-call record via ``TraceManager``.

Non-responsibilities (enforced)
--------------------------------
- MUST NOT collect, validate, or process form fields.
- MUST NOT submit or approve service requests.
- MUST NOT call downstream business APIs.
"""

from __future__ import annotations

import json
import re
import time
from decimal import Decimal
from typing import Any
from uuid import UUID

import structlog

from app.agents.llm.gateway import LLMGateway, get_default_gateway
from app.agents.prompts.supervisor_prompt import (
    CONFIDENCE_THRESHOLD,
    SUPERVISOR_SYSTEM_PROMPT,
)
from app.agents.registry import lookup_agent
from app.agents.schemas.help_agent_schema import (
    SALES_DATA_APPROVER_PLATFORM_ROLES,
    SALES_DATA_TENANT_PLATFORM_ROLES,
    intents_for_roles,
)
from app.agents.schemas.supervisor_schema import SupervisorDecision
from app.agents.services.sr_reference_parser import parse_sr_reference
from app.agents.services.context_builder import build_agent_context, format_context_for_prompt
from app.agents.services.notification_intent import (
    classify_notification_intent_from_state,
    should_bypass_for_notification_context,
)
from app.agents.services.viewing_context import (
    references_viewing_service_request,
    resolve_service_request_id,
    viewing_ids_from_state,
)
from app.agents.graph.state import ServiceRequestState
from app.observability.decorators import trace_node
from app.types.chat import AuthContext

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Phrases that signal the user wants to restart or switch workflows
# ---------------------------------------------------------------------------

_CANCEL_PHRASES: frozenset[str] = frozenset(
    {
        "cancel",
        "start over",
        "restart",
        "different request",
        "nevermind",
        "never mind",
        "stop",
        "reset",
        "begin again",
        "new request",
        "another request",
        "another handover",
        "raise another",
        "new handover",
        "create another",
        "submit another",
    }
)

# Phrases that request a summary/preview of the current session state.
# These must bypass the session-continuity shortcut so the supervisor LLM
# can classify them as PREVIEW_SERVICE_REQUEST rather than blindly continuing
# the active workflow.
_PREVIEW_PHRASES: frozenset[str] = frozenset(
    {
        "preview",
        "show me",
        "show what you have",
        "what have we collected",
        "what have you collected",
        "show details",
        "show the details",
        "can you show",
        "show the request",
        "show me the request",
        "show it",
        "review",
        "what do you have",
        "what have we got",
        "show summary",
    }
)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _rbac_denial_example(permitted: frozenset[str]) -> str:
    """The "for example" line of a role refusal, limited to what the role can do.

    It used to offer every refused user help to "create or check the status of
    a handover service request". Only a Mall Manager can create one: an RDD
    Project Manager refused a Trading Confirmation was invited to create a
    handover they are not allowed to raise either. Everyone can check status.
    """
    if "CREATE_RDD_SERVICE_REQUEST" in permitted:
        return (
            "For example, I can help you create or check the status of a "
            "handover service request."
        )
    return "For example, I can help you check the status of a service request."


def _user_wants_to_switch(message: str) -> bool:
    """Return ``True`` if *message* contains an explicit cancel/switch phrase."""
    lowered = message.lower()
    return any(phrase in lowered for phrase in _CANCEL_PHRASES)


def _user_wants_preview(message: str) -> bool:
    """Return ``True`` if *message* is a preview/summary request.

    Preview phrases bypass the session-continuity shortcut so the supervisor
    LLM can classify them as PREVIEW_SERVICE_REQUEST rather than blindly
    delegating to the active workflow agent.
    """
    lowered = message.lower()
    return any(phrase in lowered for phrase in _PREVIEW_PHRASES)


_ADMIN_SALES_ROLES = frozenset({"SALES_DATA_APPROVER", "ADMIN"}) | SALES_DATA_APPROVER_PLATFORM_ROLES
_TENANT_SALES_ROLES = (
    frozenset({"TENANT_CSV_UPLOADER", "TENANT_DOC_UPLOADER"})
    | SALES_DATA_TENANT_PLATFORM_ROLES
)

# Natural-language openers that the LLM otherwise classifies as ASK_HELP
# because they are phrased as questions ("what data are pending?").
_ADMIN_REVIEW_PHRASES: tuple[str, ...] = (
    "review sales",
    "pending approval",
    "pending approvals",
    "awaiting review",
    "show submitted",
    "submitted sales",
    "what is pending",
    "what's pending",
    "whats pending",
    "what data are pending",
    "what data is pending",
    "what data pending",
    "pending data",
    "pending sales",
    "pending submission",
    "data pending",
    "any pending",
    "show pending",
    "which are pending",
    "sales data to review",
    "submissions to review",
    "tenant sales",
    "review list",
    "show me review",
    "show the review",
)

_TENANT_STATUS_PHRASES: tuple[str, ...] = (
    "check my sales",
    "sales status",
    "status of my sales",
    "my submissions",
    "what data are pending",
    "what data is pending",
    "what's pending",
    "whats pending",
    "pending sales",
    "pending data",
    "has my sales",
    "show my sales",
    "show pending",
    "any pending",
    "sales data status",
)

# Generic "upload again" / "different file" are NOT here: they mean the file the
# user is currently working on (CSV or document) and are resolved by stage in
# sales_data_entry_node._resolve_command.
_TENANT_DOC_PHRASES: tuple[str, ...] = (
    "supporting document",
    "supporting doc",
    "extra document",
    "extra doc",
    "audited sales",
    "upload a pdf",
    "re-upload a pdf",
    "reupload a pdf",
    "re-upload pdf",
    "need a pdf",
    "needs a pdf",
)

# Takes precedence over status phrases so a notification deep-link like
# "pending sales data for lease <code>. I want to submit it" opens upload,
# not another status summary.
_TENANT_UPLOAD_PHRASES: tuple[str, ...] = (
    "upload sales",
    "upload my sales",
    "i want to upload",
    "i want to submit",
    "submit it",
    "submit sales",
    "upload a csv",
    "upload the file",
    "upload csv",
)


# Everyday phrasings: an action verb + a sales noun, in any order ("can I put
# in this month's revenue", "need to send sales for aug"). Both are required —
# these tenant roles also raise SRs, so a bare "upload my drawings" must not
# land here.
_UPLOAD_VERB_RE = re.compile(
    # Present-tense only — "show submitted sales" is a status question.
    r"\b(upload|uploading|submit|submitting|enter|entering|add|adding|put|putting|"
    r"send|sending|record|log|share|fill|fill\s+in|give|provide|key\s+in|type)\b",
    re.I,
)
_SALES_NOUN_RE = re.compile(r"\b(sales?|revenue|turnover|takings)\b", re.I)
# "did my sales upload go through?" is a status question, not a new upload.
_STATUS_QUESTION_RE = re.compile(r"^\s*(did|has|have|was|were)\b", re.I)


# Questions about where sales data stands ("did my sales get approved",
# "which months are pending") — asked, not an action to start.
_SALES_STATUS_WORD_RE = re.compile(
    r"\b(approved|pending|status|submitted|rejected|due|outstanding|overdue)\b", re.I
)
_QUESTION_START_RE = re.compile(
    r"^\s*(which|what|whats|what's|has|have|did|do|does|is|are|was|were|any|show|list|check|tell me)\b",
    re.I,
)
_SALES_CONTEXT_RE = re.compile(r"\b(sales?|submissions?|months?|revenue)\b", re.I)
# A document described in words: "the pdf the team asked for".
_DOC_WORD_RE = re.compile(r"\b(pdf|document|audited)\b", re.I)
_DOC_ACTION_RE = re.compile(r"\b(upload\w*|attach\w*|send|share|asked|requested|need|needs)\b", re.I)
# "here is my sales file", "my sales sheet".
_SALES_FILE_RE = re.compile(r"\b(sales|csv)\s+(file|sheet|spreadsheet|report)\b", re.I)
# Admin actions on the review queue — the admin node extracts the exact
# action/leases deterministically from the text.
_ADMIN_ACTION_RE = re.compile(
    r"\b(approve\w*|accept\w*|reject\w*)\b|"
    r"\b(request|ask)\b.*\b(re-?upload|supporting|audited|document)\b",
    re.I,
)
_ADMIN_REVIEW_RE = re.compile(
    r"\b(awaiting|to approve|my approval|need\w* (my )?approval|to review|review queue|"
    r"show (me )?(the )?sales|sales data|submissions?)\b",
    re.I,
)


def _looks_like_sales_status_question(message: str) -> bool:
    text = message or ""
    return bool(
        _SALES_STATUS_WORD_RE.search(text)
        and _SALES_CONTEXT_RE.search(text)
        and (_QUESTION_START_RE.search(text) or text.strip().endswith("?"))
    )


def _looks_like_sales_upload_request(message: str) -> bool:
    if _STATUS_QUESTION_RE.search(message or ""):
        return False
    return bool(
        _UPLOAD_VERB_RE.search(message or "") and _SALES_NOUN_RE.search(message or "")
    )


def _looks_like_typed_sales_entry(message: str) -> bool:
    """A lease code plus a month or an amount — "t0057323 aug 2026 12280"."""
    from app.agents.graph.nodes.sales_data.manual_entry_parser import (
        parse_manual_entry,
    )

    parsed = parse_manual_entry(message)
    return bool(
        parsed.get("lease_code")
        and (parsed.get("sales_amt") or parsed.get("sales_period_start"))
    )


def _sales_data_shortcut(
    message: str,
    roles: list[str] | tuple[str, ...] | None,
    *,
    allow_typed_entry: bool = False,
) -> dict[str, Any] | None:
    """Deterministic sales-data routing for common question phrasings.

    Returns a supervisor-equivalent state patch, or None to fall through to the LLM.
    """
    if not roles:
        return None
    role_set = set(roles)
    lowered = " ".join((message or "").lower().split())
    if not lowered:
        return None

    if role_set & _ADMIN_SALES_ROLES:
        if _ADMIN_ACTION_RE.search(lowered):
            return {
                "intent": "APPROVE_SALES_DATA",
                "service_category": "SALES_DATA",
                "sub_category": "ADMIN_REVIEW",
                "active_agent": "sales_data_agent",
                "status": "IN_PROGRESS",
            }
        if any(p in lowered for p in _ADMIN_REVIEW_PHRASES) or _ADMIN_REVIEW_RE.search(lowered):
            return {
                "intent": "REVIEW_SALES_DATA",
                "service_category": "SALES_DATA",
                "sub_category": "ADMIN_REVIEW",
                "active_agent": "sales_data_agent",
                "status": "IN_PROGRESS",
            }

    if role_set & _TENANT_SALES_ROLES:
        if any(p in lowered for p in _TENANT_DOC_PHRASES) or (
            _DOC_WORD_RE.search(lowered) and _DOC_ACTION_RE.search(lowered)
        ):
            return {
                "intent": "UPLOAD_SUPPORTING_DOCUMENT",
                "service_category": "SALES_DATA",
                "sub_category": "SUPPORTING_DOC",
                "active_agent": "sales_data_agent",
                "status": "IN_PROGRESS",
            }
        if _looks_like_sales_status_question(message) and not any(
            p in lowered for p in _TENANT_UPLOAD_PHRASES
        ):
            return {
                "intent": "CHECK_SALES_DATA_STATUS",
                "service_category": "SALES_DATA",
                "sub_category": "STATUS_CHECK",
                "active_agent": "sales_data_agent",
                "status": "IN_PROGRESS",
            }
        if (
            any(p in lowered for p in _TENANT_UPLOAD_PHRASES)
            or _looks_like_sales_upload_request(message)
            or _SALES_FILE_RE.search(lowered)
            # Only as a first message — mid-flow the sales agent reads it itself.
            or (allow_typed_entry and _looks_like_typed_sales_entry(message))
        ):
            return {
                "intent": "UPLOAD_SALES_DATA",
                "service_category": "SALES_DATA",
                "sub_category": "BULK_UPLOAD",
                "active_agent": "sales_data_agent",
                "status": "IN_PROGRESS",
            }
        if any(p in lowered for p in _TENANT_STATUS_PHRASES):
            return {
                "intent": "CHECK_SALES_DATA_STATUS",
                "service_category": "SALES_DATA",
                "sub_category": "STATUS_CHECK",
                "active_agent": "sales_data_agent",
                "status": "IN_PROGRESS",
            }

    return None


# Roles that only use the sales-data agent (no handover / work-permit actions).
# SR preview / create / status replies are the wrong product surface for them.
_SALES_DATA_ONLY_ROLES = _ADMIN_SALES_ROLES | _TENANT_SALES_ROLES
# Includes tenant company admins / authorized signatories so they are not
# treated as sales-data-only when attempting CREATE_WORK_PERMIT_SR (and other
# permitted SR intents). BILLING_CONTACT etc. stay sales-only.
_PLATFORM_SR_ROLES = frozenset(
    {
        "MALL_MANAGER",
        "FM_MANAGER",
        "OPERATIONS",
        "DD_ENGINEER",
        "ADMIN",
        "COMPANY_ADMIN_CENTRAL",
        "COMPANY_ADMIN",
        "AUTHORIZED_SIGNATORY",
    }
)
_SR_INTENTS_SALES_DATA_FALLBACK = frozenset(
    {
        "PREVIEW_SERVICE_REQUEST",
        "CHECK_SERVICE_REQUEST_STATUS",
        "CREATE_RDD_SERVICE_REQUEST",
        "UPDATE_RDD_SERVICE_REQUEST",
        "APPROVE_RDD_SERVICE_REQUEST",
        "CREATE_HANDED_OVER_SR",
        "CREATE_FITOUT_CONFIRM_FOS",
        "CREATE_FITOUT_CONFIRM_END",
        "CREATE_FITOUT_CONFIRM_TRADING",
        "APPROVE_FITOUT_CONFIRM_FOS",
        "APPROVE_FITOUT_CONFIRM_END",
        "APPROVE_FITOUT_CONFIRM_TRADING",
        "CREATE_WORK_PERMIT_SR",
        "UNKNOWN",
    }
)


def _is_sales_data_only_user(roles: list[str] | tuple[str, ...] | frozenset[str] | None) -> bool:
    role_set = set(roles or [])
    if role_set & _PLATFORM_SR_ROLES:
        return False
    return bool(role_set & _SALES_DATA_ONLY_ROLES)


def _sales_data_unmatched_message(
    roles: list[str] | tuple[str, ...] | frozenset[str] | None,
) -> str:
    role_set = set(roles or [])
    if role_set & _ADMIN_SALES_ROLES:
        return (
            "I couldn't match that to a sales data action. "
            "I can help you review submitted sales data, approve a submission, "
            "or request a re-upload, supporting document, or audited sales. "
            "Please rephrase what you'd like to do."
        )
    return (
        "I can help with your sales data: upload a CSV, enter your sales "
        "figures, check your submission status, or upload a file the Sales "
        "Data Team has requested. What would you like to do?"
    )


# "create a handover SR", "raise a work permit" … from a role that cannot
# create service requests. Without this the user got "I couldn't match that
# to a sales data action", which reads as the assistant not understanding.
_SR_KIND_LABELS: tuple[tuple[str, str], ...] = (
    (r"hand\s*-?\s*over", "a handover service request"),
    (r"work\s*-?\s*permit", "a work permit request"),
    (r"fit\s*-?\s*out", "a fit-out service request"),
    (r"\brdd\b", "an RDD service request"),
    (r"service\s+request|\bsr\b", "a service request"),
)
_SR_CREATE_VERB_RE = re.compile(
    r"\b(create|creating|raise|raising|new|start|submit|apply|book|make)\b",
    re.I,
)
# Status / tracking questions are allowed for these roles — never block them.
_SR_STATUS_RE = re.compile(r"\b(status|check|track|where|progress|update on)\b", re.I)


def _requested_sr_kind(message: str) -> str | None:
    lowered = (message or "").lower()
    if (
        not _SR_CREATE_VERB_RE.search(lowered)
        or _SALES_NOUN_RE.search(lowered)
        or _SR_STATUS_RE.search(lowered)
    ):
        return None
    for pattern, label in _SR_KIND_LABELS:
        if re.search(pattern, lowered):
            return label
    return None


def _sr_create_not_permitted_patch(kind: str) -> dict[str, Any]:
    message = (
        f"Creating {kind} isn't available for your account. I can help you "
        "with your sales data, check the status of a service request, or show "
        "your notifications."
    )
    return {
        "status": "WAITING_FOR_USER",
        "response_message": message,
        "response_ui": {"type": "message", "verbatim": True, "message": message},
    }


def _sales_data_unmatched_patch(
    roles: list[str] | tuple[str, ...] | frozenset[str] | None,
    *,
    intent: str = "UNKNOWN",
) -> dict[str, Any]:
    message = _sales_data_unmatched_message(roles)
    return {
        "intent": intent,
        "status": "WAITING_FOR_USER",
        "active_agent": None,
        "response_message": message,
        "response_ui": {
            "type": "sales_data_unmatched",
            "verbatim": True,
            "message": message,
        },
    }


def _build_user_content(state: ServiceRequestState) -> str:
    """Build the user-facing content string sent to the LLM.

    Includes the current message, lightweight routing hints about the active
    session, and up to 4 recent conversation turns so the model can classify
    short follow-up messages in context (e.g. "8th june" when an update
    workflow is already in progress).
    Compact context (authoritative fields, last 5 turns, viewing IDs) plus the
    current message — never the full event history.
    """
    context = build_agent_context(state)
    parts = [format_context_for_prompt(context)]

    auth: AuthContext | None = state.get("auth_context")  # type: ignore[assignment]
    if auth is not None and auth.roles:
        parts.append(f"User roles: {', '.join(auth.roles)}")

    active_agent = state.get("active_agent")
    if active_agent:
        parts.append(f"Currently active agent: {active_agent}")

    intent = state.get("intent")
    if intent:
        parts.append(f"Previously classified intent: {intent}")

    return "\n".join(part for part in parts if part)


async def _call_supervisor_llm(
    user_content: str,
    gateway: LLMGateway,
) -> tuple[SupervisorDecision, int, int, int]:
    """Call the LLM and parse the response as a ``SupervisorDecision``.

    Returns ``(decision, input_tokens, output_tokens, latency_ms)``.

    Raises
    ------
    json.JSONDecodeError
        If the model returns malformed JSON.
    pydantic.ValidationError
        If the JSON does not match ``SupervisorDecision``.
    openai.OpenAIError
        On transport / API errors.
    """
    parsed, input_tokens, output_tokens, latency_ms = await gateway.complete_json(
        system_prompt=SUPERVISOR_SYSTEM_PROMPT,
        user_message=user_content,
    )
    decision = SupervisorDecision.model_validate(parsed)
    return decision, input_tokens, output_tokens, latency_ms


def _extract_uuid(value: Any) -> UUID | None:
    if value is None:
        return None
    try:
        return value if isinstance(value, UUID) else UUID(str(value))
    except (ValueError, AttributeError):
        return None


_CREATE_NEW_SR_INTENTS: frozenset[str] = frozenset(
    {
        "CREATE_RDD_SERVICE_REQUEST",
        "CREATE_WORK_PERMIT_SR",
        "CREATE_CEILING_INSPECTION_SR",
    }
)


def _extract_sr_id_updates(
    state: ServiceRequestState,
    decision: SupervisorDecision,
) -> dict[str, Any]:
    """Parse an SR ref from the user message into ``backend_refs.sr_id``.

    Also clears a stale ``sr_id`` when the workflow has no active stage
    (``workflow_stage`` is ``None`` or ``"CANCELLED"``), preventing a ghost
    SR from a prior session from contaminating a new request.
    """
    if decision.intent in _CREATE_NEW_SR_INTENTS:
        return {}

    backend_refs: dict[str, Any] = dict(state.get("backend_refs") or {})
    workflow_stage: str | None = state.get("workflow_stage")

    # Clear stale sr_id when there is no active workflow — the prior session ended.
    if workflow_stage in (None, "CANCELLED"):
        if backend_refs.get("sr_id"):
            log.info(
                "supervisor.stale_sr_id_cleared",
                sr_id=backend_refs["sr_id"],
                workflow_stage=workflow_stage,
            )
            backend_refs = dict(backend_refs)
            backend_refs["sr_id"] = None
            return {"backend_refs": backend_refs}

    message = state.get("user_message") or ""
    allow_standalone = decision.intent == "CHECK_SERVICE_REQUEST_STATUS"
    parsed = parse_sr_reference(message, allow_standalone=allow_standalone)
    if not parsed:
        return {}
    if backend_refs.get("sr_id"):
        return {}
    backend_refs["sr_id"] = parsed
    log.info("supervisor.sr_id_extracted", sr_id=parsed, intent=decision.intent)
    return {"backend_refs": backend_refs}


# ---------------------------------------------------------------------------
# Node
# ---------------------------------------------------------------------------


@trace_node("supervisor", "SUPERVISOR")
async def supervisor_node(state: ServiceRequestState) -> dict[str, Any]:  # noqa: C901
    """LangGraph node: classify intent and route to the appropriate downstream agent.

    The ``@trace_node`` decorator handles the outer node-level run span
    (BEFORE_NODE / AFTER_NODE snapshots + SUCCESS/FAILED finish).  This
    function additionally opens a child ``LLM`` run span for the LLM call
    itself so token counts and latency are captured separately.
    """
    user_message: str = state.get("user_message") or ""
    active_agent: str | None = state.get("active_agent")

    # Resolve trace plumbing from state (injected by TraceManager / lifespan).
    tm = state.get("trace_manager")
    trace_id: UUID | None = _extract_uuid(state.get("trace_id"))

    # ── 0. Notification viewing context (deterministic, no LLM) ───────────
    # Page-aware deictic phrases and list/mark-read intents are classified
    # without an LLM call.  Do not overwrite active_agent so an in-progress
    # SR workflow is preserved after this side-path turn.
    notification_intent = classify_notification_intent_from_state(state)
    if notification_intent is not None:
        log.info(
            "supervisor.notification_intent_deterministic",
            intent=notification_intent,
            user_message=user_message[:120],
        )
        return {
            "intent": notification_intent,
            "status": "IN_PROGRESS",
        }

    _viewing_notification_id, viewing_sr_id = viewing_ids_from_state(state)
    if viewing_sr_id and references_viewing_service_request(user_message):
        resolved_sr = resolve_service_request_id(
            user_message, viewing_sr_id, allow_standalone=True
        )
        backend_refs = dict(state.get("backend_refs") or {})
        if resolved_sr and not backend_refs.get("sr_id"):
            backend_refs["sr_id"] = resolved_sr
        log.info(
            "supervisor.viewing_sr_status",
            viewing_service_request_id=viewing_sr_id,
            resolved_sr=resolved_sr,
        )
        return {
            "intent": "CHECK_SERVICE_REQUEST_STATUS",
            "backend_refs": backend_refs,
            "status": "IN_PROGRESS",
        }

    # ── 1. Session continuity ──────────────────────────────────────────────
    # If a downstream agent is already handling this session and the user has
    # not explicitly requested a switch or a preview, delegate back immediately.
    # Sales-data STATUS_LOOKUP is a one-shot: the next message must be
    # reclassified (upload vs status vs typed entry) instead of staying stuck
    # on CHECK_SALES_DATA_STATUS.
    auth_early: AuthContext | None = state.get("auth_context")  # type: ignore[assignment]
    early_roles = list(auth_early.roles) if auth_early is not None else None
    shortcut = _sales_data_shortcut(
        user_message,
        early_roles,
        allow_typed_entry=active_agent != "sales_data_agent",
    )
    requested_sr_kind = (
        _requested_sr_kind(user_message) if _is_sales_data_only_user(early_roles) else None
    )
    if requested_sr_kind and shortcut is None:
        log.info("supervisor.sr_create_not_permitted", kind=requested_sr_kind)
        return _sr_create_not_permitted_patch(requested_sr_kind)
    sales_data_idle = active_agent == "sales_data_agent" and (
        state.get("workflow_stage") or ""
    ) in {"STATUS_LOOKUP", "SALES_DATA_COMPLETE"}
    if (
        active_agent
        and not _user_wants_to_switch(user_message)
        and not _user_wants_preview(user_message)
        and shortcut is None
        and not sales_data_idle
        and not should_bypass_for_notification_context(state)
    ):
        log.debug(
            "supervisor.session_continuity",
            active_agent=active_agent,
            user_message=user_message[:120],
        )
        return {"active_agent": active_agent}

    # ── 1b. Deterministic sales-data routing ───────────────────────────────
    # Questions like "what data are pending?" are otherwise classified as
    # ASK_HELP (FAQ) because ASK_HELP is the default for interrogatives.
    if shortcut:
        if auth_early is not None:
            permitted = intents_for_roles(
                auth_early.roles, auth_early.is_global_admin
            )
            if shortcut["intent"] not in permitted:
                log.info(
                    "supervisor.rbac_denied",
                    intent=shortcut["intent"],
                    roles=list(auth_early.roles),
                    via="sales_data_shortcut",
                )
                if _is_sales_data_only_user(auth_early.roles):
                    return _sales_data_unmatched_patch(
                        auth_early.roles, intent=shortcut["intent"]
                    )
                role_display = ", ".join(sorted(auth_early.roles)) or "your role"
                intent_display = shortcut["intent"].replace("_", " ").lower()
                return {
                    "intent": shortcut["intent"],
                    "status": "WAITING_FOR_USER",
                    "response_message": (
                        f"Your role ({role_display}) does not permit this action "
                        f"({intent_display}). "
                        "I can answer platform questions or help with actions your role allows."
                    ),
                }
        log.info(
            "supervisor.sales_data_shortcut",
            intent=shortcut["intent"],
            user_message=user_message[:120],
        )
        return shortcut

    # ── 2. Open child LLM-call run span ───────────────────────────────────
    # ``_trace_node_run_id`` is injected by the ``@trace_node`` decorator
    # so the LLM span is nested under the supervisor node span in the run tree.
    node_run_id: UUID | None = _extract_uuid(state.get("_trace_node_run_id"))
    llm_run_id: UUID | None = None
    if tm is not None and trace_id is not None:
        try:
            llm_run_id = await tm.start_run(
                trace_id=trace_id,
                run_name="supervisor_llm_call",
                run_type="LLM",
                parent_run_id=node_run_id,
            )
        except Exception:
            log.warning("supervisor.start_llm_run.failed", exc_info=True)

    # ── 3. LLM classification ──────────────────────────────────────────────
    gateway = get_default_gateway()
    user_content = _build_user_content(state)

    decision: SupervisorDecision | None = None
    parse_success: bool = False
    parse_error: str | None = None
    input_tokens = output_tokens = latency_ms = 0
    llm_wall_start = time.monotonic()

    try:
        decision, input_tokens, output_tokens, latency_ms = await _call_supervisor_llm(
            user_content, gateway
        )
        parse_success = True
        log.info(
            "supervisor.llm_classified",
            intent=decision.intent,
            confidence=decision.confidence,
            target_agent=decision.target_agent,
            reasoning=decision.reasoning,
        )
    except json.JSONDecodeError as exc:
        parse_error = f"JSONDecodeError: {exc}"
        log.warning("supervisor.llm_json_parse_failed", error=parse_error)
    except Exception as exc:
        parse_error = str(exc)
        log.exception("supervisor.llm_call_failed", error=parse_error)

    # ── 4. Emit LLM call metadata ──────────────────────────────────────────
    if tm is not None and trace_id is not None and llm_run_id is not None:
        effective_latency = latency_ms or int((time.monotonic() - llm_wall_start) * 1000)
        try:
            await tm.capture_llm_call(
                trace_id=trace_id,
                run_id=llm_run_id,
                provider=getattr(gateway, "provider_name", "openai"),
                model=gateway.model,
                temperature=Decimal("0"),
                prompt_name="supervisor_prompt",
                prompt_version="1.0",
                input_tokens=input_tokens or None,
                output_tokens=output_tokens or None,
                total_tokens=(input_tokens + output_tokens) or None,
                latency_ms=effective_latency,
                structured_output=decision.model_dump() if decision else None,
                parse_success=parse_success,
                parse_error=parse_error,
            )
        except Exception:
            log.warning("supervisor.capture_llm_call.failed", exc_info=True)

        try:
            await tm.finish_run(
                run_id=llm_run_id,
                output=decision.model_dump() if decision else {"error": parse_error},
                status="SUCCESS" if parse_success else "FAILED",
                error_message=parse_error,
            )
        except Exception:
            log.warning("supervisor.finish_llm_run.failed", exc_info=True)

    # ── 5. Handle LLM/parse failure → graceful degradation ────────────────
    if decision is None:
        # A ceiling inspection request refused for lack of LLM credits is told
        # so plainly, instead of the generic (or sales-data) fallback.
        from app.agents.schemas.ceiling_schema import (
            OUT_OF_CREDITS_MESSAGE,
            is_out_of_credits_error,
            mentions_ceiling_inspection,
        )

        if is_out_of_credits_error(parse_error) and mentions_ceiling_inspection(user_message):
            return {
                "status": "WAITING_FOR_USER",
                "response_message": OUT_OF_CREDITS_MESSAGE,
                "response_ui": {
                    "type": "message",
                    "message": OUT_OF_CREDITS_MESSAGE,
                    "verbatim": True,
                },
            }
        if _is_sales_data_only_user(early_roles):
            return _sales_data_unmatched_patch(early_roles)
        return {
            "response_message": (
                "The user's intent could not be determined due to an internal error. "
                "Politely ask them to rephrase what they'd like to do with their service request."
            ),
            "status": "WAITING_FOR_USER",
        }

    # ── 6. Low-confidence or UNKNOWN → ask clarification ──────────────────
    sr_id_updates = _extract_sr_id_updates(state, decision)
    if decision.confidence < CONFIDENCE_THRESHOLD or decision.intent == "UNKNOWN":
        if (sr_id_updates.get("backend_refs") or {}).get("sr_id"):
            log.info(
                "supervisor.open_existing_sr_after_extract",
                intent=decision.intent,
                confidence=decision.confidence,
            )
            return {
                "intent": decision.intent,
                **sr_id_updates,
                "status": "IN_PROGRESS",
            }
        log.info(
            "supervisor.low_confidence_clarification",
            intent=decision.intent,
            confidence=decision.confidence,
        )
        if _is_sales_data_only_user(early_roles):
            return {
                **_sales_data_unmatched_patch(early_roles, intent=decision.intent),
                **sr_id_updates,
            }
        # This used to carry an instruction written AT the LLM ("The intent is
        # unclear. Ask the user to clarify whether they want to create,
        # update, approve... Be friendly and offer to guide them.") in
        # response_message — which is the user-facing field, so it was shown
        # to tenants verbatim. Speak to the user directly instead.
        return {
            "intent": decision.intent,
            **sr_id_updates,
            "response_message": (
                "I didn't quite catch that. You can ask me to raise a new "
                "service request, update an existing one, or check its status "
                "— just tell me which, and I'll take it from there."
            ),
            "response_ui": {"type": "message", "verbatim": True},
            "status": "WAITING_FOR_USER",
        }

    # ── 7a. Sales-data users must not fall into handover SR preview / create
    if (
        _is_sales_data_only_user(early_roles)
        and decision.intent in _SR_INTENTS_SALES_DATA_FALLBACK
    ):
        log.info(
            "supervisor.sales_data_unmatched",
            intent=decision.intent,
            user_message=user_message[:120],
        )
        return {
            **_sales_data_unmatched_patch(early_roles, intent=decision.intent),
            **sr_id_updates,
        }

    # ── 7b. Preview intent — route to preview_node ────────────────────────
    # PREVIEW_SERVICE_REQUEST has no downstream agent; preview_node fetches the
    # submitted SR from the platform API (if sr_id is available) or summarises
    # the draft collected_data.  Use IN_PROGRESS so _route_after_supervisor
    # sends the turn to the dedicated "preview" graph node.
    if decision.intent == "PREVIEW_SERVICE_REQUEST":
        log.info("supervisor.preview_intent")
        return {
            "intent": decision.intent,
            **sr_id_updates,
            "status": "IN_PROGRESS",
        }

    # ── 7c. RBAC check ────────────────────────────────────────────────────
    # Enforce role-based permissions before activating any agent.  The denial
    # message is set in returned state here (not in the routing function) so
    # that LangGraph propagates it correctly to response_generation_node.
    auth: AuthContext | None = state.get("auth_context")  # type: ignore[assignment]
    if auth is not None:
        permitted = intents_for_roles(auth.roles, auth.is_global_admin)
        if decision.intent not in permitted:
            log.info(
                "supervisor.rbac_denied",
                intent=decision.intent,
                roles=list(auth.roles),
            )
            if _is_sales_data_only_user(auth.roles):
                return {
                    **_sales_data_unmatched_patch(auth.roles, intent=decision.intent),
                    **sr_id_updates,
                }
            role_display = ", ".join(sorted(auth.roles)) or "your role"
            intent_display = decision.intent.replace("_", " ").lower()
            return {
                "intent": decision.intent,
                **sr_id_updates,
                "status": "WAITING_FOR_USER",
                "response_message": (
                    f"Your role ({role_display}) does not permit this action "
                    f"({intent_display}). "
                    "I can answer platform questions or help with actions your role allows. "
                    + _rbac_denial_example(permitted)
                ),
            }

    # ── 7d. Registry validation ────────────────────────────────────────────
    # Cross-check the LLM's routing against the authoritative registry so a
    # hallucinated agent name never propagates downstream.
    agent_config = None
    if decision.service_category and decision.sub_category:
        agent_config = lookup_agent(decision.service_category, decision.sub_category)

    if agent_config is None and decision.target_agent:
        # Log the gap but trust the LLM routing — the registry may not yet
        # contain all agents.  Registry node will validate further.
        log.warning(
            "supervisor.agent_not_in_registry",
            service_category=decision.service_category,
            sub_category=decision.sub_category,
            target_agent=decision.target_agent,
        )

    # ── 8. Route ───────────────────────────────────────────────────────────
    return {
        "intent": decision.intent,
        "service_category": decision.service_category,
        "sub_category": decision.sub_category,
        "active_agent": decision.target_agent,
        **sr_id_updates,
        "status": "IN_PROGRESS",
    }
