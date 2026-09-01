"""ChatSession repository.

Responsible only for persistence of ChatSession records.  No orchestration,
no agent logic, no LLM calls belong here.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.exceptions import InvalidUpdateFieldError, RecordNotFoundError
from app.db.models import ChatSession
from app.observability.analytics_constants import END_REASON_IDLE, clamp_active_delta_ms

log = structlog.get_logger(__name__)

_UPDATABLE_FIELDS: frozenset[str] = frozenset(
    {
        "active_agent",
        "intent",
        "workflow_stage",
        "status",
        "last_activity_at",
        "last_heartbeat_at",
        "ended_at",
        "end_reason",
        "client_active_ms",
    }
)


class ChatSessionRepository:
    """Async repository for :class:`~app.db.models.ChatSession`."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    async def create(
        self,
        *,
        user_id: UUID,
        active_agent: str | None = None,
        intent: str | None = None,
        workflow_stage: str | None = None,
        status: str = "IN_PROGRESS",
    ) -> ChatSession:
        """Insert a new ChatSession and flush to obtain the DB-generated id.

        The caller's outer transaction (or ``get_db_session``) is responsible
        for the final commit.
        """
        row = ChatSession(
            user_id=user_id,
            active_agent=active_agent,
            intent=intent,
            workflow_stage=workflow_stage,
            status=status,
        )
        self._session.add(row)
        await self._session.flush()
        log.debug("chat_session.created", session_id=str(row.id), user_id=str(user_id))
        return row

    async def update(self, session_id: UUID, updates: dict[str, Any]) -> ChatSession:
        """Apply a partial update to an existing ChatSession.

        Only fields listed in ``_UPDATABLE_FIELDS`` may be changed.

        Raises:
            RecordNotFoundError: if no row with *session_id* exists.
            InvalidUpdateFieldError: if *updates* contains a non-updatable key.
        """
        row = await self.get_by_id(session_id)
        if row is None:
            raise RecordNotFoundError("ChatSession", session_id)

        for key, value in updates.items():
            if key not in _UPDATABLE_FIELDS:
                raise InvalidUpdateFieldError("ChatSession", key)
            setattr(row, key, value)

        await self._session.flush()
        log.debug(
            "chat_session.updated",
            session_id=str(session_id),
            fields=list(updates.keys()),
        )
        return row

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------

    async def get_by_id(self, session_id: UUID) -> ChatSession | None:
        """Return the ChatSession with *session_id*, or ``None``."""
        result = await self._session.execute(
            select(ChatSession).where(ChatSession.id == session_id)
        )
        return result.scalar_one_or_none()

    async def touch(self, session_id: UUID, *, at: datetime | None = None) -> None:
        """Refresh ``last_activity_at`` without changing routing fields."""
        stamp = at or datetime.now(timezone.utc)
        try:
            await self.update(session_id, {"last_activity_at": stamp})
        except RecordNotFoundError:
            log.warning("chat_session.touch.missing", session_id=str(session_id))

    async def add_active_ms(
        self,
        session: ChatSession,
        delta_ms: int,
        *,
        max_delta_ms: int,
        min_interval_seconds: int,
        now: datetime | None = None,
    ) -> ChatSession:
        """Apply a heartbeat: clamp delta, respect min interval, refresh activity."""
        stamp = now or datetime.now(timezone.utc)
        add = clamp_active_delta_ms(delta_ms, max_delta_ms)
        last_hb = session.last_heartbeat_at
        skip_add = False
        if last_hb is not None:
            gap = (stamp - last_hb).total_seconds()
            if gap < min_interval_seconds:
                skip_add = True
        if not skip_add:
            session.client_active_ms = int(session.client_active_ms or 0) + add
            session.last_heartbeat_at = stamp
        session.last_activity_at = stamp
        await self._session.flush()
        log.debug(
            "analytics.session_heartbeat",
            session_id=str(session.id),
            added_ms=0 if skip_add else add,
            client_active_ms=session.client_active_ms,
        )
        return session

    async def mark_ended(
        self,
        session: ChatSession,
        *,
        reason: str,
        at: datetime | None = None,
    ) -> bool:
        """Set ended_at once.  Returns False if already ended."""
        if session.ended_at is not None:
            return False
        stamp = at or datetime.now(timezone.utc)
        session.ended_at = stamp
        session.end_reason = reason
        session.last_activity_at = stamp
        await self._session.flush()
        return True

    async def expire_idle(
        self,
        *,
        timeout_seconds: int,
        now: datetime | None = None,
    ) -> list[tuple[UUID, UUID]]:
        """Close sessions whose last activity is older than the idle timeout.

        Returns ``[(session_id, user_id), ...]`` for newly expired rows.
        """
        stamp = now or datetime.now(timezone.utc)
        cutoff = stamp - timedelta(seconds=timeout_seconds)
        result = await self._session.execute(
            select(ChatSession.id, ChatSession.user_id).where(
                ChatSession.ended_at.is_(None),
                ChatSession.last_activity_at < cutoff,
            )
        )
        rows = list(result.all())
        if not rows:
            return []
        ids = [row[0] for row in rows]
        await self._session.execute(
            update(ChatSession)
            .where(ChatSession.id.in_(ids))
            .values(ended_at=stamp, end_reason=END_REASON_IDLE, last_activity_at=stamp)
        )
        await self._session.flush()
        log.info("analytics.idle_expired", count=len(rows))
        return [(row[0], row[1]) for row in rows]
