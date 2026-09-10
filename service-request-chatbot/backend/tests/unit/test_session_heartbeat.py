"""Unit tests for session heartbeat clocks, idle expiry, and clamp rules."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from app.db.models import ChatSession
from app.db.repositories.chat_session_repo import ChatSessionRepository
from app.observability.analytics_constants import (
    END_REASON_IDLE,
    clamp_active_delta_ms,
)


def test_clamp_active_delta_ms_bounds() -> None:
    assert clamp_active_delta_ms(-50, 120_000) == 0
    assert clamp_active_delta_ms(0, 120_000) == 0
    assert clamp_active_delta_ms(1_500, 120_000) == 1_500
    assert clamp_active_delta_ms(999_999, 120_000) == 120_000
    assert clamp_active_delta_ms(10, -1) == 0


def _session(**kwargs: object) -> ChatSession:
    defaults: dict[str, object] = {
        "id": uuid4(),
        "user_id": uuid4(),
        "status": "IN_PROGRESS",
        "client_active_ms": 0,
        "last_heartbeat_at": None,
        "ended_at": None,
        "end_reason": None,
    }
    defaults.update(kwargs)
    return ChatSession(**defaults)  # type: ignore[arg-type]


async def test_add_active_ms_clamps_and_accumulates(mock_session: AsyncMock) -> None:
    row = _session(client_active_ms=100)
    repo = ChatSessionRepository(mock_session)
    stamp = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    updated = await repo.add_active_ms(
        row,
        999_999,
        max_delta_ms=120_000,
        min_interval_seconds=15,
        now=stamp,
    )
    assert updated.client_active_ms == 100 + 120_000
    assert updated.last_heartbeat_at == stamp
    assert updated.last_activity_at == stamp
    mock_session.flush.assert_awaited_once()


async def test_add_active_ms_skips_increment_inside_min_interval(
    mock_session: AsyncMock,
) -> None:
    first = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    row = _session(client_active_ms=500, last_heartbeat_at=first)
    repo = ChatSessionRepository(mock_session)
    updated = await repo.add_active_ms(
        row,
        20_000,
        max_delta_ms=120_000,
        min_interval_seconds=15,
        now=first + timedelta(seconds=5),
    )
    assert updated.client_active_ms == 500
    assert updated.last_heartbeat_at == first
    assert updated.last_activity_at == first + timedelta(seconds=5)


async def test_mark_ended_is_idempotent(mock_session: AsyncMock) -> None:
    row = _session()
    repo = ChatSessionRepository(mock_session)
    first = await repo.mark_ended(row, reason="CLIENT_ENDED")
    second = await repo.mark_ended(row, reason="CLIENT_ENDED")
    assert first is True
    assert second is False
    assert row.end_reason == "CLIENT_ENDED"
    assert row.ended_at is not None


async def test_expire_idle_closes_stale_sessions(mock_session: AsyncMock) -> None:
    sid, uid = uuid4(), uuid4()
    select_result = MagicMock()
    select_result.all.return_value = [(sid, uid)]
    mock_session.execute = AsyncMock(side_effect=[select_result, MagicMock()])
    repo = ChatSessionRepository(mock_session)
    now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    expired = await repo.expire_idle(timeout_seconds=600, now=now)
    assert expired == [(sid, uid)]
    assert mock_session.execute.await_count == 2
    update_stmt = mock_session.execute.await_args_list[1].args[0]
    compiled = str(update_stmt)
    assert "chat_sessions" in compiled.lower() or hasattr(update_stmt, "table")


async def test_expire_idle_noop_when_none_stale(mock_session: AsyncMock) -> None:
    select_result = MagicMock()
    select_result.all.return_value = []
    mock_session.execute = AsyncMock(return_value=select_result)
    repo = ChatSessionRepository(mock_session)
    expired = await repo.expire_idle(timeout_seconds=600)
    assert expired == []
    assert mock_session.execute.await_count == 1


async def test_update_allows_session_clock_fields(
    mock_session: AsyncMock, make_execute_result: object
) -> None:
    row = _session()
    mock_session.execute.return_value = make_execute_result(scalar=row)  # type: ignore[operator]
    repo = ChatSessionRepository(mock_session)
    stamp = datetime(2026, 1, 1, tzinfo=timezone.utc)
    updated = await repo.update(
        row.id,
        {"last_activity_at": stamp, "client_active_ms": 12_000, "end_reason": END_REASON_IDLE},
    )
    assert updated.last_activity_at == stamp
    assert updated.client_active_ms == 12_000
    assert updated.end_reason == END_REASON_IDLE
