"""End-user chat feedback endpoint.

POST /chat/feedback — star rating (1–5) + optional comment attached to one
agent turn, identified by the ``trace_id`` every chat response now returns.

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
from pydantic import BaseModel, Field

from app.api.routes.msp_chat import _require_service_token
from app.core.security import get_auth_context
from app.db.repositories.audit_log_repo import AuditLogRepository
from app.db.session import DbSession
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
    if auth.subject_id in _ANONYMOUS_SUBJECTS:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sign in to rate this answer.",
        )
    user_uuid = user_id_to_uuid(auth.subject_id)

    trace = await TraceRepository(db).get(body.trace_id)
    if trace is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Trace {body.trace_id} not found",
        )
    # A user may only rate their own turns.  Both chat routes derive the trace
    # user_id from the JWT subject via the same user_id_to_uuid mapping, so a
    # legitimate rating always matches.
    if trace.user_id != user_uuid:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only rate your own conversations.",
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
