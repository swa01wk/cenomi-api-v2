"""Integration tests — GET /api/v1/notifications/inbox (TC-3662 panel read)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.agents.services.notification_inbox_service import (
    NotificationGroup,
    NotificationInboxResult,
    NotificationItem,
)
from app.core.security import get_auth_context
from app.main import app
from app.types.chat import AuthContext

_ENDPOINT = "/api/v1/notifications/inbox"
_SERVICE = "app.api.routes.notifications.get_notification_inbox_service"


def _override_auth(subject_id: str = "6", roles: frozenset[str] | None = None) -> None:
    app.dependency_overrides[get_auth_context] = lambda: AuthContext(
        subject_id=subject_id,
        roles=roles if roles is not None else frozenset({"DD_ENGINEER"}),
    )


def _clear_auth() -> None:
    app.dependency_overrides.pop(get_auth_context, None)


def _result() -> NotificationInboxResult:
    return NotificationInboxResult(
        success=True,
        groups=[
            NotificationGroup(
                "HANDOVER",
                42,
                [
                    NotificationItem(
                        notification_id="ntf-1",
                        sr_id="320971",
                        sub_category="HANDOVER",
                        message="Handover SR 320971 needs review.",
                        action_required=True,
                        notification_type="SERVICE_REQUEST",
                    )
                ],
                page=1,
            )
        ],
    )


async def _get(url: str) -> tuple[int, dict]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.get(url)
    return resp.status_code, (resp.json() if resp.content else {})


@pytest.mark.asyncio
async def test_returns_serialized_groups() -> None:
    svc = AsyncMock()
    svc.fetch_inbox = AsyncMock(return_value=_result())
    _override_auth()
    try:
        with patch(_SERVICE, return_value=svc):
            status_code, body = await _get(f"{_ENDPOINT}?limit=5")
    finally:
        _clear_auth()

    assert status_code == 200
    group = body["groups"][0]
    # Same contract fields as the chat card (shared serializer).
    assert group["service_category"] == "FIT_OUT_AND_HANDOVER"
    assert group["sub_category"] == "HANDOVER"
    # The badge shows the whole outstanding count, not the rows on this page:
    # the fixture holds 1 row of a 42-row group.
    assert group["action_required_count"] == 42
    assert len(group["notifications"]) == 1
    assert group["has_more"] is True
    row = group["notifications"][0]
    assert row["sr_id"] == "320971"
    assert row["sub_category"] == "HANDOVER"
    assert row["kind"] == "service_request"


@pytest.mark.asyncio
async def test_passes_filters_to_service() -> None:
    svc = AsyncMock()
    svc.fetch_inbox = AsyncMock(return_value=_result())
    _override_auth()
    try:
        with patch(_SERVICE, return_value=svc):
            await _get(f"{_ENDPOINT}?sub_category=handover&page=2&limit=5&unread=true")
    finally:
        _clear_auth()

    kwargs = svc.fetch_inbox.call_args.kwargs
    assert kwargs["user_id"] == "6"
    assert kwargs["page"] == 2
    assert kwargs["limit"] == 5
    # Lower-case input is canonicalised before it reaches the service.
    assert kwargs["filters"].sub_categories == ("HANDOVER",)
    assert kwargs["filters"].unread is True


@pytest.mark.asyncio
async def test_anonymous_is_rejected() -> None:
    _override_auth(subject_id="anonymous", roles=frozenset())
    try:
        status_code, _ = await _get(_ENDPOINT)
    finally:
        _clear_auth()
    assert status_code == 401


@pytest.mark.asyncio
async def test_unknown_sub_category_is_422() -> None:
    _override_auth()
    try:
        status_code, _ = await _get(f"{_ENDPOINT}?sub_category=BOGUS")
    finally:
        _clear_auth()
    assert status_code == 422


@pytest.mark.asyncio
async def test_platform_failure_is_502() -> None:
    svc = AsyncMock()
    svc.fetch_inbox = AsyncMock(
        return_value=NotificationInboxResult(success=False, error="HTTP 500")
    )
    _override_auth()
    try:
        with patch(_SERVICE, return_value=svc):
            status_code, _ = await _get(_ENDPOINT)
    finally:
        _clear_auth()
    assert status_code == 502


@pytest.mark.asyncio
async def test_limit_is_capped() -> None:
    _override_auth()
    try:
        status_code, _ = await _get(f"{_ENDPOINT}?limit=500")
    finally:
        _clear_auth()
    assert status_code == 422
