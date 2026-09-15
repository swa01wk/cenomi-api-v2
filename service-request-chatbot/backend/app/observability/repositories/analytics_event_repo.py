"""AnalyticsEvent repository.

Fail-safe emit: callers should still wrap in try/except, but unknown
event names are logged and skipped rather than raising.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AnalyticsEvent
from app.observability.analytics_constants import ALLOWED_EVENT_NAMES

log = structlog.get_logger(__name__)


class AnalyticsEventRepository:
    """Async repository for :class:`~app.db.models.AnalyticsEvent`."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def emit(
        self,
        *,
        user_id: UUID,
        session_id: UUID,
        event_name: str,
        trace_id: UUID | None = None,
        properties: dict[str, Any] | None = None,
    ) -> AnalyticsEvent | None:
        """Insert an event.  Returns ``None`` if ``event_name`` is unknown."""
        if event_name not in ALLOWED_EVENT_NAMES:
            log.warning("analytics.emit.unknown_event", event_name=event_name)
            return None
        row = AnalyticsEvent(
            id=uuid4(),
            user_id=user_id,
            session_id=session_id,
            trace_id=trace_id,
            event_name=event_name,
            properties=properties or {},
        )
        self._session.add(row)
        await self._session.flush()
        log.info(
            "analytics.event_emitted",
            event_name=event_name,
            session_id=str(session_id),
            user_id=str(user_id),
        )
        return row

    async def exists(
        self,
        *,
        session_id: UUID,
        event_name: str,
        sr_id: str | None = None,
    ) -> bool:
        """True if this session already has ``event_name`` (optionally for ``sr_id``)."""
        stmt = select(AnalyticsEvent.id).where(
            AnalyticsEvent.session_id == session_id,
            AnalyticsEvent.event_name == event_name,
        )
        if sr_id is not None:
            stmt = stmt.where(AnalyticsEvent.properties.contains({"sr_id": sr_id}))
        stmt = stmt.limit(1)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none() is not None

    async def emit_once(
        self,
        *,
        user_id: UUID,
        session_id: UUID,
        event_name: str,
        trace_id: UUID | None = None,
        properties: dict[str, Any] | None = None,
        dedupe_sr_id: str | None = None,
    ) -> AnalyticsEvent | None:
        """Emit unless an equivalent event already exists for this session."""
        sr_id = dedupe_sr_id
        if sr_id is None and properties:
            raw = properties.get("sr_id")
            sr_id = str(raw) if raw else None
        try:
            if await self.exists(
                session_id=session_id, event_name=event_name, sr_id=sr_id
            ):
                return None
            return await self.emit(
                user_id=user_id,
                session_id=session_id,
                event_name=event_name,
                trace_id=trace_id,
                properties=properties,
            )
        except Exception:
            log.exception(
                "analytics.emit_once.failed",
                event_name=event_name,
                session_id=str(session_id),
            )
            return None
