"""AgentFeedback repository.

Persists explicit user or automated feedback signals attached to a trace
or a session (FLOW_USEFUL / CSAT).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import structlog
from sqlalchemy import delete, func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AgentFeedback
from app.observability.analytics_constants import (
    FEEDBACK_FLOW_USEFUL,
    FEEDBACK_TURN_THUMBS,
)

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
        message_id: UUID | None = None,
        source: str | None = None,
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
            message_id=message_id,
            source=source,
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

    async def get_thumbs_for_trace(
        self, trace_id: UUID, user_id: UUID | None
    ) -> AgentFeedback | None:
        """Return an existing TURN_THUMBS row for this trace (+ user if given)."""
        stmt = select(AgentFeedback).where(
            AgentFeedback.trace_id == trace_id,
            AgentFeedback.feedback_type == FEEDBACK_TURN_THUMBS,
        )
        if user_id is not None:
            stmt = stmt.where(AgentFeedback.user_id == user_id)
        stmt = stmt.order_by(AgentFeedback.created_at).limit(1)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def set_turn_thumbs(
        self,
        *,
        trace_id: UUID,
        user_id: UUID,
        label: str | None,
        session_id: UUID | None = None,
        message_id: UUID | None = None,
        source: str | None = None,
        comment: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> AgentFeedback | None:
        """Set, change or clear the caller's thumbs vote on one turn.

        The comment and metadata (agent, 👎 reason) are replaced on every
        write, so switching to 👍 drops a reason given for an earlier 👎.

        One statement each way, so quick repeated clicks cannot race: a vote
        is an ``INSERT … ON CONFLICT`` against ``ux_agent_feedback_turn_thumbs``
        (one TURN_THUMBS row per trace and user), and ``label=None`` deletes
        the row and returns None.
        """
        if label is None:
            await self._session.execute(
                delete(AgentFeedback).where(
                    AgentFeedback.trace_id == trace_id,
                    AgentFeedback.user_id == user_id,
                    AgentFeedback.feedback_type == FEEDBACK_TURN_THUMBS,
                )
            )
            log.debug("agent_feedback.thumbs_cleared", trace_id=str(trace_id))
            return None

        insert_stmt = pg_insert(AgentFeedback).values(
            id=uuid4(),
            trace_id=trace_id,
            user_id=user_id,
            session_id=session_id,
            message_id=message_id,
            source=source,
            feedback_type=FEEDBACK_TURN_THUMBS,
            label=label,
            comment=comment,
            metadata_=metadata or {},
        )
        upsert = insert_stmt.on_conflict_do_update(
            index_elements=[AgentFeedback.trace_id, AgentFeedback.user_id],
            # A literal, not a bound parameter, so Postgres can match the
            # partial index predicate when inferring the conflict target.
            index_where=text(f"feedback_type = '{FEEDBACK_TURN_THUMBS}'"),
            set_={
                "label": insert_stmt.excluded.label,
                "comment": insert_stmt.excluded.comment,
                "metadata": insert_stmt.excluded.metadata,
                "message_id": func.coalesce(
                    insert_stmt.excluded.message_id, AgentFeedback.message_id
                ),
                "source": func.coalesce(insert_stmt.excluded.source, AgentFeedback.source),
            },
        ).returning(AgentFeedback)
        result = await self._session.execute(
            select(AgentFeedback)
            .from_statement(upsert)
            .execution_options(populate_existing=True)
        )
        row = result.scalar_one()
        log.debug(
            "agent_feedback.thumbs_set",
            feedback_id=str(row.id),
            trace_id=str(trace_id),
            label=label,
        )
        return row

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
