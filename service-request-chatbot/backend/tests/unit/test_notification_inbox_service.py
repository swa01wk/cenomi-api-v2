"""Unit tests — notification inbox service (TC-3520 platform agent endpoints)."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.services.notification_inbox_service import (
    MOCK_ONLY_SUB_CATEGORIES,
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
    def test_mall_manager_handover_trading_and_wp(self) -> None:
        assert _select_sub_categories(frozenset({"MALL_MANAGER"}), None) == (
            "HANDOVER",
            "FIT_OUT_CONFIRM_TRADING",
            "CONSTRUCTION_HOT",
            "CONSTRUCTION_COLD",
            "CONSTRUCTION_ROOF_ACCESS",
            "MAINTENANCE_HOT",
            "MAINTENANCE_COLD",
            "MAINTENANCE_ROOF_ACCESS",
            "OPERATIONS",
        )

    def test_fm_manager_handover_and_wp_fm_hops(self) -> None:
        assert _select_sub_categories(frozenset({"FM_MANAGER"}), None) == (
            "HANDOVER",
            "CONSTRUCTION_HOT",
            "CONSTRUCTION_ROOF_ACCESS",
            "MAINTENANCE_HOT",
            "MAINTENANCE_COLD",
            "MAINTENANCE_ROOF_ACCESS",
            "OPEN_CEILING_INSPECTION",
            "CLOSE_CEILING_INSPECTION",
        )

    def test_tenant_sales_data_only(self) -> None:
        assert _select_sub_categories(
            frozenset({"TENANT_CSV_UPLOADER"}), None
        ) == ("SALES_DATA",)

    def test_platform_billing_contact_sales_data_and_ceiling_ack(self) -> None:
        # Tenant roles acknowledge ceiling inspection reports.
        assert _select_sub_categories(
            frozenset({"BILLING_CONTACT", "LEASING_MANAGER"}), None
        ) == ("SALES_DATA", "OPEN_CEILING_INSPECTION", "CLOSE_CEILING_INSPECTION")

    def test_sales_approver_sales_data_only(self) -> None:
        assert _select_sub_categories(
            frozenset({"SALES_DATA_APPROVER"}), None
        ) == ("SALES_DATA",)

    def test_operations_handover_trading_and_cold(self) -> None:
        assert _select_sub_categories(frozenset({"OPERATIONS"}), None) == (
            "HANDOVER",
            "FIT_OUT_CONFIRM_TRADING",
            "CONSTRUCTION_COLD",
        )

    def test_dd_engineer_handover_fitout_and_wp_dd_hops(self) -> None:
        assert _select_sub_categories(frozenset({"DD_ENGINEER"}), None) == (
            "HANDOVER",
            "FIT_OUT_CONFIRM_FOS",
            "FIT_OUT_CONFIRM_END",
            "FITOUT_PROGRESS",
            "CONSTRUCTION_HOT",
            "CONSTRUCTION_COLD",
            "CONSTRUCTION_ROOF_ACCESS",
            "MAINTENANCE_ROOF_ACCESS",
            "OPEN_CEILING_INSPECTION",
            "CLOSE_CEILING_INSPECTION",
        )

    def test_fitout_progress_is_the_rdd_project_managers_alone(self) -> None:
        # The weekly SR waits on the RDDPM; no other role can act on it.
        for role in ("MALL_MANAGER", "FM_MANAGER", "OPERATIONS"):
            assert "FITOUT_PROGRESS" not in _select_sub_categories(
                frozenset({role}), None
            )
        assert "FITOUT_PROGRESS" in _select_sub_categories(
            frozenset({"DD_ENGINEER"}), None
        )

    def test_empty_roles_call_all(self) -> None:
        assert _select_sub_categories(frozenset(), None) == NOTIFICATION_SUB_CATEGORIES

    def test_unknown_roles_call_all(self) -> None:
        # Role matching is by enum NAME only — an unrecognised name (never a
        # numeric role id) falls back to calling everything; the platform's
        # server-side audience intersection keeps that safe.
        assert (
            _select_sub_categories(frozenset({"SOME_UNKNOWN_ROLE"}), None)
            == NOTIFICATION_SUB_CATEGORIES
        )

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

    def test_wp_filter_for_mall_manager(self) -> None:
        filters = NotificationListFilters(
            sub_categories=("CONSTRUCTION_HOT", "OPERATIONS")
        )
        assert _select_sub_categories(frozenset({"MALL_MANAGER"}), filters) == (
            "CONSTRUCTION_HOT",
            "OPERATIONS",
        )


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
                user_id="123",
                roles=frozenset({"FM_MANAGER"}),
                filters=NotificationListFilters(sub_categories=("HANDOVER",)),
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
    async def test_fitout_progress_calls_its_own_tool(self) -> None:
        item = {
            "id": "ntf-fp",
            "text_content": {
                "message": "Service request (reference no: 320981) for Fitout Progress has been assigned to you for review.",
                "type": "SERVICE_REQUEST",
                "id": "320981",
                "sub_category": "fitout_progress",
            },
            "created_at": "2026-09-01T13:30:00Z",
            "read_at": None,
            "action_required": True,
        }
        with patch("httpx.AsyncClient") as mock_httpx:
            get = AsyncMock(
                return_value=_mock_response(200, _platform_body("FITOUT_PROGRESS", [item]))
            )
            mock_httpx.return_value.__aenter__.return_value.get = get
            result = await _http_service().fetch_inbox(
                user_id="1077321",
                roles=frozenset({"DD_ENGINEER"}),
                filters=NotificationListFilters(sub_categories=("FITOUT_PROGRESS",)),
            )
        args, _ = get.call_args
        assert args[0] == "https://platform.example/v1/notifications/agent/fitout-progress"
        group = result.groups[0]
        assert group.sub_category == "FITOUT_PROGRESS"
        assert group.items[0].sr_id == "320981"

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
                user_id="123",
                roles=frozenset({"FM_MANAGER"}),
                filters=NotificationListFilters(sub_categories=("HANDOVER",)),
            )
        assert result.groups[0].items[0].sr_id == "11712"

    @pytest.mark.asyncio
    async def test_unread_param_passthrough(self) -> None:
        with patch("httpx.AsyncClient") as mock_httpx:
            get = AsyncMock(return_value=_mock_response(200, _platform_body("HANDOVER", [])))
            mock_httpx.return_value.__aenter__.return_value.get = get
            await _http_service().fetch_inbox(
                user_id="123",
                roles=frozenset({"FM_MANAGER"}),
                filters=NotificationListFilters(
                    sub_categories=("HANDOVER",), unread=True
                ),
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
                user_id="123",
                roles=frozenset({"DD_ENGINEER"}),
                filters=NotificationListFilters(
                    sub_categories=(
                        "HANDOVER",
                        "FIT_OUT_CONFIRM_FOS",
                        "FIT_OUT_CONFIRM_END",
                    )
                ),
            )
        assert result.success
        assert [g.sub_category for g in result.groups] == ["HANDOVER"]
        assert set(result.partial_errors) == {
            "FIT_OUT_CONFIRM_FOS",
            "FIT_OUT_CONFIRM_END",
        }

    @pytest.mark.asyncio
    async def test_all_failed_sets_error(self) -> None:
        with patch("httpx.AsyncClient") as mock_httpx:
            mock_httpx.return_value.__aenter__.return_value.get = AsyncMock(
                return_value=_mock_response(500, {})
            )
            result = await _http_service().fetch_inbox(
                user_id="123", roles=frozenset({"FM_MANAGER"})
            )
        assert not result.success
        assert result.error
        assert result.groups == []

    @pytest.mark.asyncio
    async def test_404_is_empty_success_not_hard_failure(self) -> None:
        """Local platform missing sales-data agent route must not 502 tenants."""
        with patch("httpx.AsyncClient") as mock_httpx:
            mock_httpx.return_value.__aenter__.return_value.get = AsyncMock(
                return_value=_mock_response(404, "404 page not found")
            )
            result = await _http_service().fetch_inbox(
                user_id="123",
                roles=frozenset({"COMPANY_ADMIN", "TENANT"}),
            )
        assert result.success
        assert result.error is None
        assert result.groups == []
        assert result.partial_errors == {}

    @pytest.mark.asyncio
    async def test_404_on_one_sub_keeps_other_groups(self) -> None:
        async def get_side_effect(url: str, **_: Any) -> MagicMock:
            if "sales-data" in url:
                return _mock_response(404, "404 page not found")
            return _mock_response(
                200,
                _platform_body(
                    "HANDOVER",
                    [{"id": "n1", "text_content": {"message": "m", "id": "1"}}],
                ),
            )

        with patch("httpx.AsyncClient") as mock_httpx:
            mock_httpx.return_value.__aenter__.return_value.get = AsyncMock(
                side_effect=get_side_effect
            )
            result = await _http_service().fetch_inbox(
                user_id="123",
                roles=frozenset(),  # call all
                filters=NotificationListFilters(
                    sub_categories=("HANDOVER", "SALES_DATA")
                ),
            )
        assert result.success
        assert [g.sub_category for g in result.groups] == ["HANDOVER"]
        assert "SALES_DATA" not in result.partial_errors


# ---------------------------------------------------------------------------
# Mock adapter + factory
# ---------------------------------------------------------------------------


class TestCeilingLiveRoutes:
    def test_nothing_is_mock_only(self) -> None:
        # The platform now serves both ceiling inspection routes.
        assert MOCK_ONLY_SUB_CATEGORIES == frozenset()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("sub", "path"),
        [
            ("OPEN_CEILING_INSPECTION", "/notifications/agent/open-ceiling-inspection"),
            ("CLOSE_CEILING_INSPECTION", "/notifications/agent/close-ceiling-inspection"),
        ],
    )
    async def test_ceiling_is_fetched_from_the_platform(self, sub: str, path: str) -> None:
        item = {
            "id": "ntf-9",
            "text_content": {"message": "Review SR 30001", "type": "SERVICE_REQUEST", "id": "30001"},
            "created_at": "2026-09-29T09:00:00Z",
            "read_at": None,
            "action_required": True,
        }
        with patch("httpx.AsyncClient") as mock_httpx:
            get = AsyncMock(return_value=_mock_response(200, _platform_body(sub, [item])))
            mock_httpx.return_value.__aenter__.return_value.get = get
            result = await _http_service().fetch_inbox(
                user_id="7",
                roles=frozenset({"DD_ENGINEER"}),
                filters=NotificationListFilters(sub_categories=(sub,)),
            )
        assert result.success
        assert [g.sub_category for g in result.groups] == [sub]
        assert result.groups[0].items[0].sr_id == "30001"
        args, _ = get.call_args
        assert args[0] == f"https://platform.example/v1{path}"


class TestMockAndFactory:
    @pytest.mark.asyncio
    async def test_mock_deterministic_and_filtered(self) -> None:
        svc = MockNotificationInboxService()
        all_groups = await svc.fetch_inbox(user_id="1", roles=frozenset({"ADMIN"}))
        # Ceiling rows exist only once a mock SR records them — no fixtures.
        ceiling = {"OPEN_CEILING_INSPECTION", "CLOSE_CEILING_INSPECTION"}
        assert [g.sub_category for g in all_groups.groups] == [
            s for s in NOTIFICATION_SUB_CATEGORIES if s not in ceiling
        ]
        unread = await svc.fetch_inbox(
            user_id="1",
            roles=frozenset({"FM_MANAGER"}),
            filters=NotificationListFilters(unread=True),
        )
        assert [i.sr_id for g in unread.groups for i in g.items] == [
            "11711",
            "12801",
            "12803",
            "12804",
            "12805",
            "12806",
        ]

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
