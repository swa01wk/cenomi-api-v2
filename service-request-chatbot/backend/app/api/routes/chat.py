"""Chat HTTP API.

Two routers are exported:

``router``
    Mounted at ``/api/v1`` — houses the original ``POST /chat/turn`` stub
    (kept for backward compatibility).

``service_request_router``
    Mounted at ``/api`` — houses the main ``POST /chat/service-request``
    endpoint described in the product spec.

Principle: no business workflow logic lives here.  The route handler
validates the request, delegates to ``ChatOrchestrationService``, and maps
the result back to the HTTP response contract.  Auth context is stubbed for
the POC; ``user_id`` is accepted in the request body and will move to a JWT
claim in production.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.security import get_auth_context
from app.db.session import DbSession
from app.services.chat_orchestration_service import (
    ChatOrchestrationService,
    parse_session_id,
    user_id_to_uuid,
)
from app.types.chat import AuthContext

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------

# Original v1 router — backward-compatible stub
router = APIRouter()

# New service-request chat router mounted at /api (no version prefix)
service_request_router = APIRouter()


# ---------------------------------------------------------------------------
# Request / Response models — POST /api/chat/service-request
# ---------------------------------------------------------------------------


class ActiveNotificationRef(BaseModel):
    """Notification the user tapped "Open" on.

    For FIT_OUT_AND_HANDOVER, ``id`` is the platform SR id (``text_content.id``),
    never the notification row id.  ``sub_category`` seeds the RDD/fitout agent.

    For SALES_DATA, ``id`` is the lease code (PMSTenantCode). Optional
    ``period_hint`` / ``message`` / ``key`` are parsed into
    ``notification_context`` for the Sales Data Agent. Extra fields are kept
    so the Open payload can grow without a schema bump.
    """

    model_config = ConfigDict(extra="allow")

    id: str = Field(
        description=(
            "SR id for handover/fit-out, or lease code for SALES_DATA "
            "(notification text_content.id — not the notification row id)."
        )
    )
    sub_category: str | None = Field(
        default=None,
        description="HANDOVER / FIT_OUT_CONFIRM_* / SALES_DATA.",
        examples=["HANDOVER", "SALES_DATA"],
    )
    period_hint: str | None = Field(
        default=None,
        description="Sales-data period phrase, e.g. 'month August'.",
    )
    key: str | None = Field(
        default=None,
        description="Platform template key, e.g. SALES_DATA_PENDING.",
    )
    message: str | None = Field(
        default=None,
        description="EN notification body — used to parse period_hint if omitted.",
    )
    notification_type: str | None = Field(
        default=None,
        description="Platform notification_type, e.g. SALES_DATA.",
    )

    @field_validator("id", mode="before")
    @classmethod
    def _coerce_id(cls, value: Any) -> str:
        return str(value).strip()


class NotificationListMoreRef(BaseModel):
    """View-more paging for one notification group (TC-3662)."""

    sub_category: str = Field(description="Group to page, e.g. HANDOVER.")
    page: int = Field(ge=1, description="1-based page to fetch.")
    unread: bool | None = Field(
        default=None, description="Carry the list's unread filter forward."
    )


class ServiceRequestChatRequest(BaseModel):
    """Incoming chat turn for the service-request workflow."""

    session_id: str | None = Field(
        default=None,
        description="Existing session UUID. Omit (or send null) to start a new conversation.",
        examples=["550e8400-e29b-41d4-a716-446655440000"],
    )
    user_id: str = Field(
        min_length=1,
        description="Caller's user identifier (used when no JWT is present — POC fallback).",
        examples=["user_456", "550e8400-e29b-41d4-a716-446655440001"],
    )
    message: str = Field(
        default="",
        description=(
            "The user's natural-language message. "
            "May be empty only when sr_id is provided (FM/DD opening an existing SR). "
            "Must have at least 1 character when sr_id is absent."
        ),
        examples=["I want to raise a handover request for Under Armour in Jawharat Jeddah"],
    )
    attachments: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Optional list of file attachment metadata.",
    )
    action: str | None = Field(
        default=None,
        description=(
            "Explicit UI action that bypasses text-based intent parsing. "
            "'confirm' triggers immediate SR submission; 'cancel' resets the confirmation."
        ),
        examples=["confirm", "cancel"],
    )
    selected_lease_id: str | None = Field(
        default=None,
        description="Lease ID chosen from a lease_selection card.",
        examples=["t0105712"],
    )
    corrected_fields: dict[str, Any] | None = Field(
        default=None,
        description="Inline field edits submitted from the confirmation card.",
    )
    language: str | None = Field(
        default=None,
        description="Response language hint: 'en' or 'ar'.",
    )
    context: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Page context from the widget: current_url_pattern, help_category, "
            "help_subcategory, property_id, mall_name. The frontend has always "
            "sent this; it was previously dropped because the field was not "
            "declared here."
        ),
    )
    sr_id: str | None = Field(
        default=None,
        description=(
            "Platform SR ID — passed by the frontend when FM Manager or DD Engineer opens "
            "an existing SR from a notification or SR list. Triggers sr_status_sync."
        ),
        examples=["SR-2026-00741"],
    )
    viewing_notification_id: str | None = Field(
        default=None,
        description=(
            "Notification ID for the page the user is currently viewing. "
            "Enables deictic chat ('explain this', 'mark this as read') without repeating the ID. "
            "UI will send this from the notifications detail page."
        ),
        examples=["NTF-000012"],
    )
    viewing_service_request_id: str | None = Field(
        default=None,
        description=(
            "Service request ID for the page the user is currently viewing. "
            "Often sent together with viewing_notification_id when a notification wraps an SR."
        ),
        examples=["11711"],
    )
    viewing_notification: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Optional notification snapshot (id, title, body, related_sr_id, status). "
            "Used until a platform notifications API is wired; the UI may send this with the turn."
        ),
    )
    active_notification_id: ActiveNotificationRef | None = Field(
        default=None,
        description=(
            "Sent when the user taps Open on a notification. For handover/fit-out "
            "seeds the SR id and routes into sr_status_sync. For SALES_DATA, id is "
            "the lease code and the turn lands in the Sales Data Agent. "
            "A bare string is accepted as the SR id (reconciled-spec fallback)."
        ),
    )

    notification_list_more: NotificationListMoreRef | None = Field(
        default=None,
        description=(
            "Sent when the user taps View more on a notification group — "
            "fetches the next page of that group as a fresh list card."
        ),
    )

    @field_validator("active_notification_id", mode="before")
    @classmethod
    def _coerce_notification_ref(cls, value: Any) -> Any:
        """Reconciled-spec fallback: a bare string is the SR id."""
        if isinstance(value, str) and value.strip():
            return {"id": value.strip()}
        return value


class ChatStatePayload(BaseModel):
    """Workflow state summary returned alongside the assistant reply."""

    intent: str | None = None
    workflow_stage: str | None = None
    missing_fields: list[str] = Field(default_factory=list)
    ready_to_submit: bool = False
    rdd_status: str | None = None
    fm_status: str | None = None
    sr_id: str | None = None
    action_result: str | None = Field(
        default=None,
        description=(
            "Outcome of a platform-mutating turn: 'success' after SUBMITTED/COMPLETED, "
            "'failed' after FAILED, null for field-collection / waiting turns."
        ),
    )
    confirmation_status: str | None = Field(
        default=None,
        description=(
            "HITL confirmation marker. CANCEL_PENDING means the agent asked "
            "are-you-sure before cancelling; the UI should show Yes/No."
        ),
    )


class ServiceRequestChatResponse(BaseModel):
    """Response envelope for ``POST /api/chat/service-request``."""

    session_id: str = Field(description="Session UUID (create once, reuse for the whole flow).")
    active_agent: str | None = Field(
        default=None,
        description="The LangGraph agent that handled this turn.",
    )
    message: str = Field(description="Assistant reply to display in the chat UI.")
    ui: dict[str, Any] = Field(
        default_factory=lambda: {"type": "message"},
        description="UI rendering hint emitted by the graph (message, form, selection, etc.).",
    )
    draft_preview: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Current draft SR snapshot (type='sr_preview_card').  "
            "Present on every turn where collected_data is non-empty and the SR "
            "has not yet been submitted.  Allows the frontend to keep its preview "
            "card in sync even when `ui` is a plain message or confirmation type."
        ),
    )
    faq_sources: list[dict[str, Any]] = Field(
        default_factory=list,
        description=(
            "RAG citations returned by the FAQ node.  "
            "Each item has ``source_type`` and ``title`` keys.  "
            "Empty list when RAG was unavailable or the turn was not a FAQ query."
        ),
    )
    state: ChatStatePayload
    trace_id: str | None = Field(
        default=None,
        description="Observability trace ID for this turn.",
    )


# ---------------------------------------------------------------------------
# POST /chat/service-request
# ---------------------------------------------------------------------------


@service_request_router.post(
    "/chat/service-request",
    response_model=ServiceRequestChatResponse,
    summary="Service-request chat turn",
    description=(
        "Submit one user message to the service-request chatbot.  "
        "The endpoint orchestrates LangGraph, persists messages and session state, "
        "and returns the assistant reply with workflow metadata."
    ),
    responses={
        status.HTTP_200_OK: {"description": "Turn processed successfully."},
        status.HTTP_422_UNPROCESSABLE_ENTITY: {"description": "Request validation failed."},
        status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "Graph or persistence error."},
    },
)
async def post_service_request_chat(
    body: ServiceRequestChatRequest,
    db: DbSession,
    auth: AuthContext = Depends(get_auth_context),
) -> ServiceRequestChatResponse:
    """Process one chat turn for the service-request workflow."""
    # Validate: empty message is allowed when sr_id is present (FM/DD opening an
    # existing SR), an explicit action is provided (chip click without typing),
    # OR a session_id is present (silent refresh — e.g. after a document upload
    # the FE calls refreshAfterUpload with an empty message so the graph re-emits
    # the current stage's UI with the new document row).
    if (
        not body.message
        and not body.sr_id
        and not body.action
        and not body.session_id
        and not body.viewing_notification_id
        and not body.active_notification_id
        and not body.notification_list_more
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "Either 'message', 'sr_id', 'action', 'session_id', "
                "'viewing_notification_id', or 'active_notification_id' must be provided."
            ),
        )

    # Resolve user_id: JWT subject_id takes precedence over request body user_id
    effective_user_id: str = (
        auth.subject_id
        if auth.subject_id not in ("anonymous", "unauthenticated")
        else body.user_id
    )

    bound_log = log.bind(
        user_id=effective_user_id,
        raw_session_id=body.session_id,
        role=next(iter(auth.roles), "none"),
    )
    bound_log.info("api.chat.service_request.received")

    user_uuid: UUID = user_id_to_uuid(effective_user_id)
    session_uuid: UUID | None = parse_session_id(body.session_id)

    service = ChatOrchestrationService(db)

    try:
        result = await service.process_turn(
            session_id=session_uuid,
            user_id=user_uuid,
            message=body.message,
            attachments=body.attachments,
            action=body.action,
            selected_lease_id=body.selected_lease_id,
            corrected_fields=body.corrected_fields,
            auth_context=auth,
            sr_id=body.sr_id,
            language=body.language,
            msp_context=body.context,
            viewing_notification_id=body.viewing_notification_id,
            viewing_service_request_id=body.viewing_service_request_id,
            viewing_notification=body.viewing_notification,
            active_notification_id=(
                body.active_notification_id.model_dump()
                if body.active_notification_id
                else None
            ),
            notification_list_more=(
                body.notification_list_more.model_dump()
                if body.notification_list_more
                else None
            ),
        )
    except Exception as exc:
        bound_log.exception("api.chat.service_request.unhandled_error", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred while processing your request.  Please try again.",
        ) from exc

    return ServiceRequestChatResponse(
        session_id=str(result.session_id),
        active_agent=result.active_agent,
        message=result.message,
        ui=result.ui,
        draft_preview=result.draft_preview,
        faq_sources=result.faq_sources,
        state=ChatStatePayload(
            intent=result.state.intent,
            workflow_stage=result.state.workflow_stage,
            missing_fields=result.state.missing_fields,
            ready_to_submit=result.state.ready_to_submit,
            rdd_status=result.state.rdd_status,
            fm_status=result.state.fm_status,
            sr_id=result.state.sr_id,
            action_result=result.state.action_result,
            confirmation_status=result.state.confirmation_status,
        ),
        trace_id=str(result.trace_id) if result.trace_id else None,
    )


# ---------------------------------------------------------------------------
# Backward-compatible stub — POST /api/v1/chat/turn
# ---------------------------------------------------------------------------


class ChatTurnRequest(BaseModel):
    session_id: UUID | None = Field(
        default=None,
        description="Existing session; omit to start a new conversation.",
    )
    message: str = Field(min_length=1)


class ChatTurnResponse(BaseModel):
    session_id: UUID
    reply: str
    trace_id: UUID | None = None


@router.post(
    "/chat/turn",
    response_model=ChatTurnResponse,
    deprecated=True,
    summary="[Deprecated] Chat turn (v1 stub)",
    description="Use ``POST /api/chat/service-request`` instead.",
)
async def post_chat_turn(
    body: ChatTurnRequest,
    auth: AuthContext = Depends(get_auth_context),
) -> ChatTurnResponse:
    """Backward-compatible stub — delegates to the new service-request endpoint."""
    _ = auth
    session_id = body.session_id or uuid4()
    return ChatTurnResponse(
        session_id=session_id,
        reply="Use POST /api/chat/service-request for full functionality.",
        trace_id=None,
    )
