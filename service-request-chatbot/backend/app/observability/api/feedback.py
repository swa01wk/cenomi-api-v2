"""Observability feedback API.

Endpoints
---------
POST /observability/traces/{trace_id}/feedback

Admin reviews, automated evals, and end-user TURN_THUMBS.  Session-level
FLOW_USEFUL / CSAT live on ``POST /api/chat/sessions/{id}/feedback``.
Star ratings (USER_RATING) are upserted here and also accepted on the
chat feedback route (``POST /chat/feedback``).
"""

from __future__ import annotations

from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.core.security import get_auth_context
from app.db.session import DbSession
from app.observability.analytics_constants import (
    FEEDBACK_ADMIN_REVIEW,
    FEEDBACK_AUTOMATED_EVAL,
    FEEDBACK_TURN_THUMBS,
    LABEL_THUMBS_DOWN,
    LABEL_THUMBS_UP,
)
from app.observability.repositories.feedback_repo import FeedbackRepository
from app.observability.repositories.trace_repo import TraceRepository
from app.observability.schemas.api_models import FeedbackResponse
from app.services.chat_orchestration_service import user_id_to_uuid
from app.types.chat import AuthContext

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/observability")

_ADMIN_TYPES = {FEEDBACK_ADMIN_REVIEW, FEEDBACK_AUTOMATED_EVAL, "rating"}


class FeedbackBody(BaseModel):
    """Request body for submitting feedback on a trace."""

    feedback_type: str = Field(
        description="Feedback category, e.g. ADMIN_REVIEW, TURN_THUMBS, AUTOMATED_EVAL"
    )
    score: int | None = Field(
        default=None,
        ge=0,
        le=5,
        description="Optional integer score (0–5)",
    )
    label: str | None = Field(
        default=None,
        description="Optional categorical label, e.g. THUMBS_UP, GOOD_EXTRACTION",
    )
    comment: str | None = Field(
        default=None,
        max_length=2000,
        description="Optional free-text reviewer comment",
    )
    user_id: str | None = Field(
        default=None,
        description="POC fallback user id when JWT is absent.",
    )


def _normalize_type(feedback_type: str) -> str:
    if feedback_type in _ADMIN_TYPES and feedback_type == "rating":
        return FEEDBACK_ADMIN_REVIEW
    return feedback_type


@router.post(
    "/traces/{trace_id}/feedback",
    response_model=FeedbackResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Submit feedback for a trace",
)
async def submit_feedback(
    trace_id: UUID,
    body: FeedbackBody,
    db: DbSession,
    auth: AuthContext = Depends(get_auth_context),
) -> FeedbackResponse:
    """Attach a feedback signal to a trace.  Persists JWT user_id when present."""
    trace_repo = TraceRepository(db)
    trace = await trace_repo.get(trace_id)
    if trace is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Trace {trace_id} not found",
        )

    effective = (
        auth.subject_id
        if auth.subject_id not in ("anonymous", "unauthenticated", "")
        else body.user_id
    )
    user_uuid = user_id_to_uuid(effective) if effective else None
    feedback_type = _normalize_type(body.feedback_type)

    feedback_repo = FeedbackRepository(db)

    if feedback_type == FEEDBACK_TURN_THUMBS:
        if body.label not in {LABEL_THUMBS_UP, LABEL_THUMBS_DOWN}:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="TURN_THUMBS label must be THUMBS_UP or THUMBS_DOWN",
            )
        existing = await feedback_repo.get_thumbs_for_trace(trace_id, user_uuid)
        if existing is not None:
            existing.label = body.label
            existing.comment = body.comment
            await db.flush()
            return FeedbackResponse.model_validate(existing)
    elif feedback_type == "USER_RATING":
        if user_uuid is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="USER_RATING requires a signed-in user",
            )
        if body.score is None or not (1 <= body.score <= 5):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="USER_RATING score must be 1–5",
            )
        row, _created = await feedback_repo.upsert_user_rating(
            trace_id=trace_id,
            user_id=user_uuid,
            score=body.score,
            comment=body.comment,
            session_id=getattr(trace, "session_id", None),
        )
        log.info(
            "observability.feedback_submitted",
            trace_id=str(trace_id),
            feedback_type=feedback_type,
            score=body.score,
            label=body.label,
        )
        return FeedbackResponse.model_validate(row)

    row = await feedback_repo.create(
        trace_id=trace_id,
        feedback_type=feedback_type,
        score=body.score,
        label=body.label,
        comment=body.comment,
        user_id=user_uuid,
        session_id=getattr(trace, "session_id", None),
    )

    log.info(
        "observability.feedback_submitted",
        trace_id=str(trace_id),
        feedback_type=feedback_type,
        score=body.score,
        label=body.label,
    )

    return FeedbackResponse.model_validate(row)
