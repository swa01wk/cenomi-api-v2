"""End-user chat feedback endpoints.

POST /chat/feedback — star rating (1–5) + optional comment attached to one
agent turn, identified by the ``trace_id`` every chat response now returns.

PUT /chat/feedback/thumbs — 👍 / 👎 on any assistant reply, from any agent.
The vote is saved on the click; a 👎 may then carry an optional reason and
details from the widget's "Share feedback" panel.  ``value: null`` clears it.

Two collection surfaces share this route:

- ``help_answer`` — the star row rendered under every assistant answer.
- ``sr_created``  — the one-time prompt shown after the agent creates a
  service request (workflow_stage SR_CREATED / HANDED_OVER_COMPLETED).

Design notes
------------
- Follows the notification-inbox pattern: a chat-adjacent side endpoint that
  never starts a chat turn, never touches session state, and never opens a
  trace.  Rating an answer must not pollute transcripts.
- Requires a real signed-in user (same rule as the SR chat flow) — anonymous
  or unauthenticated subjects are rejected.  ``user_id``/``session_id``/
  ``sr_id`` are derived server-side from the trace, never trusted from the
  request body.
- Idempotent per (trace, user): re-submitting replaces the previous stars and
  comment instead of stacking rows (``ux_agent_feedback_user_rating``).
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, model_validator

from app.api.routes.msp_chat import _require_service_token
from app.core.security import get_auth_context
from app.db.models import AgentTrace
from app.db.repositories.audit_log_repo import AuditLogRepository
from app.db.session import DbSession
from app.observability.analytics_constants import (
    LABEL_THUMBS_DOWN,
    LABEL_THUMBS_UP,
    SR_CREATED_STAGES,
    THUMBS_DOWN_REASONS,
    THUMBS_SOURCE_HELP,
    THUMBS_SOURCE_SR_CREATED,
    THUMBS_SOURCE_SR_STEP,
)
from app.observability.repositories.feedback_repo import FeedbackRepository
from app.observability.repositories.trace_repo import TraceRepository
from app.observability.schemas.api_models import FeedbackResponse
from app.services.chat_orchestration_service import user_id_to_uuid
from app.types.chat import AuthContext

log = structlog.get_logger(__name__)

router = APIRouter()

# Subjects that carry no real user identity — ratings must be attributable.
_ANONYMOUS_SUBJECTS = frozenset({"anonymous", "unauthenticated", "chat_user", ""})


class ChatFeedbackRequest(BaseModel):
    """Star rating + optional comment for one agent turn."""

    trace_id: UUID = Field(
        description="Trace id of the turn being rated (from the chat response)."
    )
    rating: int = Field(
        ge=1,
        le=5,
        description="Star rating, 1 (worst) to 5 (best).",
        examples=[5],
    )
    comment: str | None = Field(
        default=None,
        max_length=2000,
        description="Optional free-text comment.",
    )
    source: Literal["help_answer", "sr_created"] = Field(
        description=(
            "Where the rating was collected: under a help answer, or the "
            "one-time prompt after an SR was created."
        ),
    )
    message_id: UUID | None = Field(
        default=None,
        description="Persisted assistant message id (chat response message_id).",
    )
    sr_id: str | None = Field(
        default=None,
        description="Created SR number — send with source='sr_created'.",
    )


class ChatFeedbackResponse(BaseModel):
    """Result of recording a rating."""

    feedback: FeedbackResponse
    created: bool = Field(
        description="True when a new rating was stored; false when an existing "
        "rating for this turn was updated."
    )


class ChatThumbsRequest(BaseModel):
    """Thumbs vote on one assistant reply."""

    trace_id: UUID = Field(
        description="Trace id of the reply being voted on (from the chat response)."
    )
    value: Literal["up", "down"] | None = Field(
        description="'up', 'down', or null to clear the caller's vote.",
        examples=["up"],
    )
    message_id: UUID | None = Field(
        default=None,
        description="Persisted assistant message id (chat response message_id).",
    )
    reason: Literal[THUMBS_DOWN_REASONS] | None = Field(  # type: ignore[valid-type]
        default=None,
        description="Why the reply was unhelpful — only with value 'down'.",
    )
    comment: str | None = Field(
        default=None,
        max_length=2000,
        description="Optional details — only with value 'down'.",
    )

    @model_validator(mode="after")
    def _reason_only_on_down(self) -> "ChatThumbsRequest":
        if self.value != "down" and (self.reason or self.comment):
            raise ValueError("reason and comment are only accepted with value 'down'")
        if self.comment is not None:
            self.comment = self.comment.strip() or None
        return self


class ChatThumbsResponse(BaseModel):
    """The caller's vote after the change."""

    trace_id: UUID
    value: Literal["up", "down"] | None
    feedback: FeedbackResponse | None = Field(
        default=None, description="The stored vote; null once cleared."
    )


_THUMBS_LABELS = {"up": LABEL_THUMBS_UP, "down": LABEL_THUMBS_DOWN}


def _caller_uuid(auth: AuthContext, detail: str) -> UUID:
    if auth.subject_id in _ANONYMOUS_SUBJECTS:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail)
    return user_id_to_uuid(auth.subject_id)


async def _own_trace(db: DbSession, trace_id: UUID, user_uuid: UUID, detail: str) -> AgentTrace:
    trace = await TraceRepository(db).get(trace_id)
    if trace is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Trace {trace_id} not found",
        )
    # Both chat routes derive the trace user_id from the JWT subject via the
    # same user_id_to_uuid mapping, so a legitimate vote always matches.
    if trace.user_id != user_uuid:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)
    return trace


def thumbs_source(workflow_stage: str | None) -> str:
    """Where the voted reply sat: a help answer, a step in an SR flow, or the SR result."""
    if not workflow_stage:
        return THUMBS_SOURCE_HELP
    if workflow_stage in SR_CREATED_STAGES:
        return THUMBS_SOURCE_SR_CREATED
    return THUMBS_SOURCE_SR_STEP


@router.post(
    "/chat/feedback",
    response_model=ChatFeedbackResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Rate an agent turn (stars 1–5 + comment)",
    description=(
        "Attach an end-user star rating to one chat turn.  Idempotent per "
        "user and turn — re-submitting updates the previous rating.  Requires "
        "a signed-in user; the rating is bound to the JWT subject."
    ),
    dependencies=[Depends(_require_service_token)],
    responses={
        status.HTTP_201_CREATED: {"description": "Rating recorded."},
        status.HTTP_401_UNAUTHORIZED: {"description": "Signed-in user required."},
        status.HTTP_403_FORBIDDEN: {"description": "Turn belongs to another user."},
        status.HTTP_404_NOT_FOUND: {"description": "Trace not found."},
    },
)
async def submit_chat_feedback(
    body: ChatFeedbackRequest,
    db: DbSession,
    auth: AuthContext = Depends(get_auth_context),
) -> ChatFeedbackResponse:
    """Record (or update) the caller's rating for one agent turn."""
    user_uuid = _caller_uuid(auth, "Sign in to rate this answer.")
    # A user may only rate their own turns.
    trace = await _own_trace(
        db, body.trace_id, user_uuid, "You can only rate your own conversations."
    )

    row, created = await FeedbackRepository(db).upsert_user_rating(
        trace_id=body.trace_id,
        user_id=user_uuid,
        score=body.rating,
        comment=body.comment,
        session_id=trace.session_id,
        message_id=body.message_id,
        source=body.source,
        sr_id=body.sr_id,
    )

    await AuditLogRepository(db).create(
        session_id=trace.session_id,
        action="feedback.submitted",
        actor_user_id=user_uuid,
        after_state={
            "feedback_id": str(row.id),
            "rating": body.rating,
            "source": body.source,
            "created": created,
        },
        metadata={
            "trace_id": str(body.trace_id),
            "sr_id": body.sr_id,
            "has_comment": bool(body.comment),
        },
    )

    log.info(
        "chat_feedback.submitted",
        trace_id=str(body.trace_id),
        user_id=str(user_uuid),
        rating=body.rating,
        source=body.source,
        created=created,
    )

    return ChatFeedbackResponse(
        feedback=FeedbackResponse.model_validate(row), created=created
    )


@router.put(
    "/chat/feedback/thumbs",
    response_model=ChatThumbsResponse,
    summary="Thumbs up / down on an assistant reply",
    description=(
        "Set, change or clear the caller's 👍 / 👎 on one reply from any agent.  "
        "One vote per user and reply; no reason is collected.  Requires a "
        "signed-in user; the vote is bound to the JWT subject."
    ),
    dependencies=[Depends(_require_service_token)],
    responses={
        status.HTTP_200_OK: {"description": "Vote stored or cleared."},
        status.HTTP_401_UNAUTHORIZED: {"description": "Signed-in user required."},
        status.HTTP_403_FORBIDDEN: {"description": "Reply belongs to another user."},
        status.HTTP_404_NOT_FOUND: {"description": "Trace not found."},
    },
)
async def set_chat_thumbs(
    body: ChatThumbsRequest,
    db: DbSession,
    auth: AuthContext = Depends(get_auth_context),
) -> ChatThumbsResponse:
    """Record, change or clear the caller's thumbs vote for one reply."""
    user_uuid = _caller_uuid(auth, "Sign in to rate this answer.")
    trace = await _own_trace(
        db, body.trace_id, user_uuid, "You can only rate your own conversations."
    )
    source = thumbs_source(trace.workflow_stage_after)
    label = _THUMBS_LABELS.get(body.value) if body.value else None

    row = await FeedbackRepository(db).set_turn_thumbs(
        trace_id=body.trace_id,
        user_id=user_uuid,
        label=label,
        session_id=trace.session_id,
        message_id=body.message_id,
        source=source,
        comment=body.comment,
        metadata={
            key: value
            for key, value in (("agent", trace.active_agent), ("reason", body.reason))
            if value
        },
    )

    await AuditLogRepository(db).create(
        session_id=trace.session_id,
        action="feedback.thumbs",
        actor_user_id=user_uuid,
        after_state={
            "feedback_id": str(row.id) if row is not None else None,
            "value": body.value,
            "source": source,
            "reason": body.reason,
            "has_comment": bool(body.comment),
        },
        metadata={"trace_id": str(body.trace_id), "agent": trace.active_agent},
    )

    log.info(
        "chat_feedback.thumbs",
        trace_id=str(body.trace_id),
        user_id=str(user_uuid),
        value=body.value,
        source=source,
    )

    return ChatThumbsResponse(
        trace_id=body.trace_id,
        value=body.value,
        feedback=FeedbackResponse.model_validate(row) if row is not None else None,
    )
