"""AI Command Center — access gate, platform AI share, upload recording (3784)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.api.routes import upload as upload_route
from app.observability import analytics_access as access
from app.observability import platform_stats
from app.observability.analytics_constants import EVENT_DOCUMENT_UPLOADED
from app.types.chat import AuthContext

NOW = datetime(2026, 9, 17, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _fresh_role_cache():
    platform_stats.reset_caches()
    yield
    platform_stats.reset_caches()


# ── Access gate ────────────────────────────────────────────────────────────


def _user(*roles: str) -> AuthContext:
    return AuthContext(subject_id="4411", roles=frozenset(roles))


async def test_role_with_the_permission_is_allowed() -> None:
    with patch.object(access, "roles_with_permission", new=AsyncMock(return_value=frozenset({"SUPER_ADMIN"}))):
        auth = _user("SUPER_ADMIN")
        assert await access.require_analytics_viewer(auth) is auth


async def test_role_without_the_permission_is_forbidden() -> None:
    with (
        patch.object(access, "roles_with_permission", new=AsyncMock(return_value=frozenset({"SUPER_ADMIN"}))),
        pytest.raises(HTTPException) as exc,
    ):
        await access.require_analytics_viewer(_user("FM_MANAGER"))
    assert exc.value.status_code == 403


@pytest.mark.parametrize("enforce,expected", [(False, None), (True, 401)])
async def test_anonymous_follows_rbac_mode(enforce: bool, expected: int | None) -> None:
    anonymous = AuthContext(subject_id="anonymous")
    with patch.object(access.settings, "rbac_enforce", enforce):
        if expected is None:
            assert await access.require_analytics_viewer(anonymous) is anonymous
        else:
            with pytest.raises(HTTPException) as exc:
                await access.require_analytics_viewer(anonymous)
            assert exc.value.status_code == expected


@pytest.mark.parametrize("enforce,expected", [(False, None), (True, 503)])
async def test_unreadable_platform_roles_follow_rbac_mode(enforce: bool, expected: int | None) -> None:
    auth = _user("FM_MANAGER")
    with (
        patch.object(access, "roles_with_permission", new=AsyncMock(return_value=None)),
        patch.object(access.settings, "rbac_enforce", enforce),
    ):
        if expected is None:
            assert await access.require_analytics_viewer(auth) is auth
        else:
            with pytest.raises(HTTPException) as exc:
                await access.require_analytics_viewer(auth)
            assert exc.value.status_code == expected


# ── Platform reader ────────────────────────────────────────────────────────


class _Reader:
    def __init__(self, rows=None, error: Exception | None = None) -> None:
        self.rows = rows or []
        self.error = error
        self.calls = 0

    async def fetch_all(self, sql, params=None):  # noqa: ANN001, ARG002
        self.calls += 1
        if self.error:
            raise self.error
        return self.rows


async def test_ai_share_groups_sub_categories_into_functions() -> None:
    reader = _Reader(
        rows=[
            {"sub_category": "HANDOVER", "total": 10, "ai": 4},
            {"sub_category": "HANDED_OVER", "total": 2, "ai": 1},
            {"sub_category": "FIT_OUT_CONFIRM_FOS", "total": 3, "ai": 0},
        ]
    )
    with patch.object(platform_stats, "_get_reader", return_value=reader):
        share = await platform_stats.ai_share_of_service_requests(NOW - timedelta(days=7), NOW)
    assert share["total"] == 15 and share["ai"] == 5
    assert share["by_function"][0] == {"function": "handover", "total": 12, "ai": 5}


async def test_ai_share_is_none_before_the_platform_migration() -> None:
    reader = _Reader(error=RuntimeError('column "created_via" does not exist'))
    with patch.object(platform_stats, "_get_reader", return_value=reader):
        assert await platform_stats.ai_share_of_service_requests(NOW - timedelta(days=7), NOW) is None


async def test_role_grants_are_cached() -> None:
    reader = _Reader(rows=[{"role_name": "SUPER_ADMIN"}])
    with patch.object(platform_stats, "_get_reader", return_value=reader):
        first = await platform_stats.roles_with_permission("VIEW_ASSISTANT_ANALYTICS")
        second = await platform_stats.roles_with_permission("VIEW_ASSISTANT_ANALYTICS")
    assert first == second == frozenset({"SUPER_ADMIN"})
    assert reader.calls == 1


# ── Upload recording ───────────────────────────────────────────────────────


async def test_upload_is_recorded_and_committed() -> None:
    db = AsyncMock()
    session_id = uuid4()
    with patch.object(upload_route, "emit_analytics", new=AsyncMock()) as emit:
        await upload_route._record_upload(
            db,
            auth=AuthContext(subject_id="4411"),
            session_id=session_id,
            document_type="SR_HANDOVER_CHECKLIST",
            succeeded=False,
            latency_ms=812,
        )
    kwargs = emit.await_args.kwargs
    assert kwargs["event_name"] == EVENT_DOCUMENT_UPLOADED
    assert kwargs["session_id"] == session_id
    assert kwargs["properties"] == {
        "success": False,
        "latency_ms": 812,
        "document_type": "SR_HANDOVER_CHECKLIST",
    }
    db.commit.assert_awaited_once()


@pytest.mark.parametrize("subject,session", [("anonymous", uuid4()), ("4411", None)])
async def test_upload_without_user_or_session_is_not_recorded(subject, session) -> None:  # noqa: ANN001
    with patch.object(upload_route, "emit_analytics", new=AsyncMock()) as emit:
        await upload_route._record_upload(
            AsyncMock(),
            auth=AuthContext(subject_id=subject),
            session_id=session,
            document_type=None,
            succeeded=True,
            latency_ms=10,
        )
    emit.assert_not_awaited()
