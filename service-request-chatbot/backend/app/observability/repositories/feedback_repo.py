"""AgentFeedback repository.

Persists explicit user or automated feedback signals attached to a trace
or a session (FLOW_USEFUL / CSAT).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AgentFeedback
from app.observability.analytics_constants import FEEDBACK_FLOW_USEFUL

log = structlog.get_logger(__name__)


class FeedbackRepository:
    """Async repository for :class:`~app.db.models.AgentFeedback`."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        feedback_type: str,
        trace_id: UUID | None = None,
        run_id: UUID | None = None,
        user_id: UUID | None = None,
        session_id: UUID | None = None,
        sr_id: str | None = None,
        score: int | None = None,
        label: str | None = None,
        comment: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> AgentFeedback:
        """Insert a new feedback record and flush."""
        row = AgentFeedback(
            id=uuid4(),
            trace_id=trace_id,
            run_id=run_id,
            user_id=user_id,
            session_id=session_id,
            sr_id=sr_id,
            feedback_type=feedback_type,
            score=score,
            label=label,
            comment=comment,
            metadata_=metadata or {},
        )
        self._session.add(row)
        await self._session.flush()
        log.debug(
            "agent_feedback.created",
            feedback_id=str(row.id),
            trace_id=str(trace_id) if trace_id else None,
            session_id=str(session_id) if session_id else None,
            feedback_type=feedback_type,
            score=score,
        )
        return row

    async def get_thumbs_for_trace(
        self, trace_id: UUID, user_id: UUID | None
    ) -> AgentFeedback | None:
        """Return an existing TURN_THUMBS row for this trace (+ user if given)."""
        stmt = select(AgentFeedback).where(
            AgentFeedback.trace_id == trace_id,
            AgentFeedback.feedback_type == "TURN_THUMBS",
        )
        if user_id is not None:
            stmt = stmt.where(AgentFeedback.user_id == user_id)
        stmt = stmt.order_by(AgentFeedback.created_at).limit(1)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def upsert_flow_useful(
        self,
        *,
        session_id: UUID,
        user_id: UUID | None,
        label: str,
        comment: str | None,
        sr_id: str | None,
        metadata: dict[str, Any] | None,
    ) -> AgentFeedback:
        """At most one FLOW_USEFUL row per session — update in place."""
        result = await self._session.execute(
            select(AgentFeedback)
            .where(
                AgentFeedback.session_id == session_id,
                AgentFeedback.feedback_type == FEEDBACK_FLOW_USEFUL,
            )
            .order_by(AgentFeedback.created_at)
            .limit(1)
        )
        existing = result.scalar_one_or_none()
        if existing is not None:
            existing.label = label
            existing.comment = comment
            existing.sr_id = sr_id
            existing.user_id = user_id
            existing.metadata_ = metadata or {}
            await self._session.flush()
            log.debug(
                "agent_feedback.flow_useful_updated",
                feedback_id=str(existing.id),
                session_id=str(session_id),
                label=label,
            )
            return existing
        return await self.create(
            feedback_type=FEEDBACK_FLOW_USEFUL,
            session_id=session_id,
            user_id=user_id,
            label=label,
            comment=comment,
            sr_id=sr_id,
            metadata=metadata,
        )

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
