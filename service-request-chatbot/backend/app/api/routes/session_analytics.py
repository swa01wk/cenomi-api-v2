"""HTTP routes for session heartbeats, client-end, and session-level feedback.

Mounted at ``/api`` (and ``/api/v2``) next to the chat router.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.core.config import settings
from app.core.security import get_auth_context
from app.db.models import ChatSession
from app.db.repositories.chat_session_repo import ChatSessionRepository
from app.db.session import DbSession
from app.observability.analytics_constants import (
    END_REASON_CLIENT,
    END_REASON_IDLE,
    EVENT_SESSION_ENDED,
    FEEDBACK_CSAT,
    FEEDBACK_FLOW_USEFUL,
    LABEL_NOT_USEFUL,
    LABEL_USEFUL,
    NOT_USEFUL_REASONS,
)
from app.observability.analytics_emit import emit_analytics
from app.observability.repositories.feedback_repo import FeedbackRepository
from app.observability.schemas.api_models import FeedbackResponse
from app.services.chat_orchestration_service import user_id_to_uuid
from app.types.chat import AuthContext

log = structlog.get_logger(__name__)

router = APIRouter()


class HeartbeatBody(BaseModel):
    client_active_delta_ms: int = Field(ge=0, description="Focused-ms since last tick.")
    user_id: str | None = Field(
        default=None,
        description="POC fallback when JWT is absent (same as chat turn).",
    )


class SessionEndBody(BaseModel):
    reason: str = Field(default=END_REASON_CLIENT)
    user_id: str | None = None


class SessionFeedbackBody(BaseModel):
    feedback_type: str
    label: str | None = None
    score: int | None = Field(default=None, ge=1, le=5)
    comment: str | None = Field(default=None, max_length=2000)
    sr_id: str | None = None
    metadata: dict[str, Any] | None = None
    user_id: str | None = None


def _effective_user_uuid(auth: AuthContext, body_user_id: str | None) -> UUID:
    effective = (
        auth.subject_id
        if auth.subject_id not in ("anonymous", "unauthenticated")
        else (body_user_id or auth.subject_id)
    )
    return user_id_to_uuid(effective)


async def _owned_session(
    db: DbSession, session_id: UUID, caller: UUID
) -> ChatSession:
    repo = ChatSessionRepository(db)
    row = await repo.get_by_id(session_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    if row.user_id != caller:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Session not owned by caller"
        )
    return row


async def close_idle_sessions(db: DbSession) -> None:
    """Expire idle sessions and emit session_ended.  Never raises."""
    try:
        repo = ChatSessionRepository(db)
        expired = await repo.expire_idle(
            timeout_seconds=settings.analytics_idle_timeout_seconds
        )
        for session_id, user_id in expired:
            await emit_analytics(
                db,
                user_id=user_id,
                session_id=session_id,
                event_name=EVENT_SESSION_ENDED,
                properties={"reason": END_REASON_IDLE},
            )
    except Exception:
        log.exception("analytics.idle_expiry.failed")


@router.post("/chat/sessions/{session_id}/heartbeat")
async def post_session_heartbeat(
    session_id: UUID,
    body: HeartbeatBody,
    db: DbSession,
    auth: AuthContext = Depends(get_auth_context),
) -> dict[str, Any]:
    """Record focused time for an open session."""
    caller = _effective_user_uuid(auth, body.user_id)
    await close_idle_sessions(db)
    session = await _owned_session(db, session_id, caller)
    repo = ChatSessionRepository(db)
    if session.ended_at is not None:
        return {
            "client_active_ms": session.client_active_ms,
            "last_activity_at": session.last_activity_at,
            "ended_at": session.ended_at,
        }
    updated = await repo.add_active_ms(
        session,
        body.client_active_delta_ms,
        max_delta_ms=settings.analytics_max_active_delta_ms,
        min_interval_seconds=settings.analytics_heartbeat_min_interval_seconds,
    )
    return {
        "client_active_ms": updated.client_active_ms,
        "last_activity_at": updated.last_activity_at,
        "ended_at": updated.ended_at,
    }


@router.post("/chat/sessions/{session_id}/end")
async def post_session_end(
    session_id: UUID,
    body: SessionEndBody,
    db: DbSession,
    auth: AuthContext = Depends(get_auth_context),
) -> dict[str, Any]:
    """Mark a session client-ended.  Idempotent."""
    if body.reason != END_REASON_CLIENT:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Client may only send reason=CLIENT_ENDED",
        )
    caller = _effective_user_uuid(auth, body.user_id)
    session = await _owned_session(db, session_id, caller)
    repo = ChatSessionRepository(db)
    first = await repo.mark_ended(session, reason=END_REASON_CLIENT)
    if first:
        await emit_analytics(
            db,
            user_id=session.user_id,
            session_id=session.id,
            event_name=EVENT_SESSION_ENDED,
            properties={"reason": END_REASON_CLIENT},
        )
    return {
        "ended_at": session.ended_at,
        "end_reason": session.end_reason,
        "already_ended": not first,
    }


@router.post(
    "/chat/sessions/{session_id}/feedback",
    response_model=FeedbackResponse,
    status_code=status.HTTP_201_CREATED,
)
async def post_session_feedback(
    session_id: UUID,
    body: SessionFeedbackBody,
    db: DbSession,
    auth: AuthContext = Depends(get_auth_context),
) -> FeedbackResponse:
    """Session-level FLOW_USEFUL or CSAT.  FLOW_USEFUL is upserted."""
    caller = _effective_user_uuid(auth, body.user_id)
    session = await _owned_session(db, session_id, caller)
    feedback_repo = FeedbackRepository(db)
    meta = dict(body.metadata or {})

    if body.feedback_type == FEEDBACK_FLOW_USEFUL:
        if body.label not in {LABEL_USEFUL, LABEL_NOT_USEFUL}:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="FLOW_USEFUL label must be USEFUL or NOT_USEFUL",
            )
        reason = meta.get("reason")
        if reason is not None and reason not in NOT_USEFUL_REASONS:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Invalid not-useful reason",
            )
        row = await feedback_repo.upsert_flow_useful(
            session_id=session.id,
            user_id=caller,
            label=body.label,
            comment=body.comment,
            sr_id=body.sr_id,
            metadata=meta,
        )
        return FeedbackResponse.model_validate(row)

    if body.feedback_type == FEEDBACK_CSAT:
        if body.score is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="CSAT requires score 1–5",
            )
        row = await feedback_repo.create(
            feedback_type=FEEDBACK_CSAT,
            session_id=session.id,
            user_id=caller,
            score=body.score,
            comment=body.comment,
            sr_id=body.sr_id,
            metadata=meta,
        )
        return FeedbackResponse.model_validate(row)

    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail="feedback_type must be FLOW_USEFUL or CSAT",
    )
