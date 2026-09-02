"""AgentFeedback repository.

Persists explicit user or automated feedback signals attached to a trace.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AgentFeedback

log = structlog.get_logger(__name__)


class FeedbackRepository:
    """Async repository for :class:`~app.db.models.AgentFeedback`."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    async def create(
        self,
        *,
        trace_id: UUID,
        feedback_type: str,
        run_id: UUID | None = None,
        user_id: UUID | None = None,
        score: int | None = None,
        label: str | None = None,
        comment: str | None = None,
        session_id: UUID | None = None,
        message_id: UUID | None = None,
        source: str | None = None,
        sr_id: str | None = None,
        metadata: dict | None = None,
    ) -> AgentFeedback:
        """Insert a new feedback record and flush."""
        row = AgentFeedback(
            id=uuid4(),
            trace_id=trace_id,
            run_id=run_id,
            user_id=user_id,
            feedback_type=feedback_type,
            score=score,
            label=label,
            comment=comment,
            session_id=session_id,
            message_id=message_id,
            source=source,
            sr_id=sr_id,
            metadata_=metadata if metadata is not None else {},
        )
        self._session.add(row)
        await self._session.flush()
        log.debug(
            "agent_feedback.created",
            feedback_id=str(row.id),
            trace_id=str(trace_id),
            feedback_type=feedback_type,
            score=score,
        )
        return row

    async def upsert_user_rating(
        self,
        *,
        trace_id: UUID,
        user_id: UUID,
        score: int,
        comment: str | None = None,
        session_id: UUID | None = None,
        message_id: UUID | None = None,
        source: str | None = None,
        sr_id: str | None = None,
    ) -> tuple[AgentFeedback, bool]:
        """Create or update the caller's USER_RATING for a trace.

        The ``ux_agent_feedback_user_rating`` partial unique index allows one
        USER_RATING per (trace_id, user_id); a re-submit replaces the stars and
        comment instead of stacking rows.  Returns ``(row, created)``.
        """
        result = await self._session.execute(
            select(AgentFeedback).where(
                AgentFeedback.trace_id == trace_id,
                AgentFeedback.user_id == user_id,
                AgentFeedback.feedback_type == "USER_RATING",
            )
        )
        existing = result.scalar_one_or_none()
        if existing is not None:
            existing.score = score
            existing.comment = comment
            if message_id is not None:
                existing.message_id = message_id
            if source is not None:
                existing.source = source
            if sr_id is not None:
                existing.sr_id = sr_id
            await self._session.flush()
            log.debug(
                "agent_feedback.user_rating_updated",
                feedback_id=str(existing.id),
                trace_id=str(trace_id),
                score=score,
            )
            return existing, False

        row = await self.create(
            trace_id=trace_id,
            feedback_type="USER_RATING",
            user_id=user_id,
            score=score,
            comment=comment,
            session_id=session_id,
            message_id=message_id,
            source=source,
            sr_id=sr_id,
        )
        return row, True

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------

    async def list_for_trace(
        self, trace_id: UUID, *, limit: int = 100
    ) -> list[AgentFeedback]:
        """Return all feedback for *trace_id*, oldest first."""
        result = await self._session.execute(
            select(AgentFeedback)
            .where(AgentFeedback.trace_id == trace_id)
            .order_by(AgentFeedback.created_at)
            .limit(limit)
        )
        return list(result.scalars())
