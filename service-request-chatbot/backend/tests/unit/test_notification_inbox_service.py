"""Unit tests — notification inbox service (TC-3520 platform agent endpoints)."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.services.notification_inbox_service import (
    NOTIFICATION_SUB_CATEGORIES,
    HttpNotificationInboxService,
    MockNotificationInboxService,
    NotificationListFilters,
    _select_sub_categories,
    _unwrap_list,
    get_notification_inbox_service,
)

# ---------------------------------------------------------------------------
# Endpoint selection by role audience
# ---------------------------------------------------------------------------


class TestAudienceSelection:
    def test_mall_manager_only_handover(self) -> None:
        assert _select_sub_categories(frozenset({"MALL_MANAGER"}), None) == ("HANDOVER",)

    def test_fm_manager_only_handover(self) -> None:
        assert _select_sub_categories(frozenset({"FM_MANAGER"}), None) == ("HANDOVER",)

    def test_dd_engineer_all_five(self) -> None:
        assert _select_sub_categories(frozenset({"DD_ENGINEER"}), None) == NOTIFICATION_SUB_CATEGORIES

    def test_empty_roles_call_all(self) -> None:
        assert _select_sub_categories(frozenset(), None) == NOTIFICATION_SUB_CATEGORIES

    def test_unknown_roles_call_all(self) -> None:
        assert _select_sub_categories(frozenset({"81"}), None) == NOTIFICATION_SUB_CATEGORIES

    def test_admin_calls_all(self) -> None:
        roles = frozenset({"ADMIN", "MALL_MANAGER"})
        assert _select_sub_categories(roles, None) == NOTIFICATION_SUB_CATEGORIES

    def test_filter_intersects_audience(self) -> None:
        filters = NotificationListFilters(sub_categories=("HANDOVER", "FIT_OUT_CONFIRM_FOS"))
        assert _select_sub_categories(frozenset({"MALL_MANAGER"}), filters) == ("HANDOVER",)

    def test_filter_outside_audience_falls_back_to_requested(self) -> None:
        # Server-side intersection makes calling it safe; just returns empty.
        filters = NotificationListFilters(sub_categories=("FIT_OUT_CONFIRM_FOS",))
        assert _select_sub_categories(frozenset({"MALL_MANAGER"}), filters) == (
            "FIT_OUT_CONFIRM_FOS",
        )

    def test_unknown_sub_category_dropped(self) -> None:
        filters = NotificationListFilters(sub_categories=("BOGUS", "HANDOVER"))
        assert _select_sub_categories(frozenset(), filters) == ("HANDOVER",)


# ---------------------------------------------------------------------------
# Envelope unwrapping
# ---------------------------------------------------------------------------


class TestUnwrap:
    def test_platform_envelope(self) -> None:
        body = {"success": True, "data": {"total_count": 7, "list": [{"id": "n1"}]}}
        entries, total = _unwrap_list(body)
        assert entries == [{"id": "n1"}]
        assert total == 7

    def test_flat_data_list(self) -> None:
        entries, total = _unwrap_list({"data": [{"id": "n1"}]})
        assert entries == [{"id": "n1"}]
        assert total is None

    def test_bare_list(self) -> None:
        entries, _ = _unwrap_list([{"id": "n1"}, "junk"])
        assert entries == [{"id": "n1"}]

    def test_garbage(self) -> None:
        assert _unwrap_list("nope") == ([], None)
        assert _unwrap_list({"data": {"weird": 1}}) == ([], None)


# ---------------------------------------------------------------------------
# HTTP adapter
# ---------------------------------------------------------------------------


def _mock_response(status_code: int, body: Any) -> MagicMock:
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = body
    response.text = json.dumps(body) if not isinstance(body, str) else body
    return response


def _http_service() -> HttpNotificationInboxService:
    return HttpNotificationInboxService(
        base_url="https://platform.example/v1",
        internal_api_token="tok-123",
    )


def _platform_body(sub: str, items: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "success": True,
        "data": {
            "sub_category": sub,
            "total_count": len(items),
            "current_page": 1,
            "limit": 10,
            "list": items,
        },
    }


class TestHttpFetch:
    @pytest.mark.asyncio
    async def test_fetch_parses_items_and_headers(self) -> None:
        item = {
            "id": "ntf-1",
            "text_content": {"message": "Review SR 11711", "type": "SERVICE_REQUEST", "id": "11711"},
            "created_at": "2026-08-20T09:00:00Z",
            "read_at": None,
            "action_required": True,
            "lease_id": 501,
            "unique_property_id": 3691,
        }
        with patch("httpx.AsyncClient") as mock_httpx:
            get = AsyncMock(return_value=_mock_response(200, _platform_body("HANDOVER", [item])))
            mock_httpx.return_value.__aenter__.return_value.get = get
            result = await _http_service().fetch_inbox(
                user_id="123", roles=frozenset({"MALL_MANAGER"})
            )
        assert result.success
        assert len(result.groups) == 1
        group = result.groups[0]
        assert group.sub_category == "HANDOVER"
        assert group.total_count == 1
        assert group.items[0].sr_id == "11711"
        assert group.items[0].message == "Review SR 11711"
        assert group.items[0].lease_id == "501"
        # Endpoint / headers / params of the single call
        _, kwargs = get.call_args
        args, _ = get.call_args
        assert args[0] == "https://platform.example/v1/notifications/agent/handover"
        assert kwargs["headers"] == {"x-internal-api-token": "tok-123"}
        assert kwargs["params"]["user_id"] == "123"

    @pytest.mark.asyncio
    async def test_text_content_as_json_string(self) -> None:
        item = {
            "id": "ntf-2",
            "text_content": json.dumps({"message": "m", "id": "11712"}),
        }
        with patch("httpx.AsyncClient") as mock_httpx:
            mock_httpx.return_value.__aenter__.return_value.get = AsyncMock(
                return_value=_mock_response(200, _platform_body("HANDOVER", [item]))
            )
            result = await _http_service().fetch_inbox(
                user_id="123", roles=frozenset({"FM_MANAGER"})
            )
        assert result.groups[0].items[0].sr_id == "11712"

    @pytest.mark.asyncio
    async def test_unread_param_passthrough(self) -> None:
        with patch("httpx.AsyncClient") as mock_httpx:
            get = AsyncMock(return_value=_mock_response(200, _platform_body("HANDOVER", [])))
            mock_httpx.return_value.__aenter__.return_value.get = get
            await _http_service().fetch_inbox(
                user_id="123",
                roles=frozenset({"MALL_MANAGER"}),
                filters=NotificationListFilters(unread=True),
            )
        _, kwargs = get.call_args
        assert kwargs["params"]["unread"] == "true"

    @pytest.mark.asyncio
    async def test_partial_failure_keeps_good_groups(self) -> None:
        async def get_side_effect(url: str, **_: Any) -> MagicMock:
            if "handover" in url:
                return _mock_response(
                    200,
                    _platform_body(
                        "HANDOVER",
                        [{"id": "n1", "text_content": {"message": "m", "id": "1"}}],
                    ),
                )
            return _mock_response(502, {"success": False})

        with patch("httpx.AsyncClient") as mock_httpx:
            mock_httpx.return_value.__aenter__.return_value.get = AsyncMock(
                side_effect=get_side_effect
            )
            result = await _http_service().fetch_inbox(
                user_id="123", roles=frozenset({"DD_ENGINEER"})
            )
        assert result.success
        assert [g.sub_category for g in result.groups] == ["HANDOVER"]
        assert set(result.partial_errors) == {
            "FIT_OUT_CONFIRM_HO",
            "FIT_OUT_CONFIRM_FOS",
            "FIT_OUT_CONFIRM_END",
            "FIT_OUT_CONFIRM_TRADING",
        }

    @pytest.mark.asyncio
    async def test_all_failed_sets_error(self) -> None:
        with patch("httpx.AsyncClient") as mock_httpx:
            mock_httpx.return_value.__aenter__.return_value.get = AsyncMock(
                return_value=_mock_response(500, {})
            )
            result = await _http_service().fetch_inbox(
                user_id="123", roles=frozenset({"MALL_MANAGER"})
            )
        assert not result.success
        assert result.error
        assert result.groups == []


# ---------------------------------------------------------------------------
# Mock adapter + factory
# ---------------------------------------------------------------------------


class TestMockAndFactory:
    @pytest.mark.asyncio
    async def test_mock_deterministic_and_filtered(self) -> None:
        svc = MockNotificationInboxService()
        all_groups = await svc.fetch_inbox(user_id="1", roles=frozenset({"DD_ENGINEER"}))
        assert [g.sub_category for g in all_groups.groups] == list(NOTIFICATION_SUB_CATEGORIES)
        unread = await svc.fetch_inbox(
            user_id="1",
            roles=frozenset({"MALL_MANAGER"}),
            filters=NotificationListFilters(unread=True),
        )
        assert [i.sr_id for g in unread.groups for i in g.items] == ["11711"]

    def test_factory_prefers_mock_when_forced(self) -> None:
        with patch(
            "app.agents.services.notification_inbox_service.settings"
        ) as mock_settings:
            mock_settings.platform_base_url = "https://platform.example/v1"
            mock_settings.platform_internal_api_token = "tok"
            mock_settings.notifications_use_mock = True
            assert isinstance(get_notification_inbox_service(), MockNotificationInboxService)

    def test_factory_mock_without_token(self) -> None:
        with patch(
            "app.agents.services.notification_inbox_service.settings"
        ) as mock_settings:
            mock_settings.platform_base_url = "https://platform.example/v1"
            mock_settings.platform_internal_api_token = None
            mock_settings.notifications_use_mock = False
            assert isinstance(get_notification_inbox_service(), MockNotificationInboxService)

    def test_factory_http_when_configured(self) -> None:
        with patch(
            "app.agents.services.notification_inbox_service.settings"
        ) as mock_settings:
            mock_settings.platform_base_url = "https://platform.example/v1"
            mock_settings.platform_internal_api_token = "tok"
            mock_settings.notifications_use_mock = False
            assert isinstance(get_notification_inbox_service(), HttpNotificationInboxService)
