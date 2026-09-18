"""Unit tests for Sales Data PermissionService checks (RDD-style write guards).

Coverage
--------
- ACTION_PERMISSION_MAP maps to platform permission strings
- CSV / supporting-doc / approve role unions
- FINANCIAL_CONTROLLER and SHOP_MANAGER cannot POST CSV
- Billing Contact cannot PATCH supporting docs or approve
- Mall Manager is denied on all sales-data writes
- is_global_admin bypasses even with empty roles
- graph_permission_guard skips when auth_context is missing
- Write nodes refuse before calling the platform API
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.agents.graph.nodes.sales_data.admin_status_update_node import (
    admin_status_update_node,
)
from app.agents.graph.nodes.sales_data.manual_entry_submission_node import (
    manual_entry_submission_node,
)
from app.agents.graph.nodes.sales_data.sales_data_api_submission_node import (
    sales_data_api_submission_node,
)
from app.agents.graph.nodes.sales_data.supporting_doc_submission_node import (
    supporting_doc_submission_node,
)
from app.agents.services.permission_service import (
    ACTION_PERMISSION_MAP,
    ROLE_PERMISSION_MAP,
    PermissionDeniedError,
    PermissionService,
    graph_permission_guard,
)
from app.types.chat import AuthContext


def _auth(*roles: str, is_global_admin: bool = False) -> AuthContext:
    return AuthContext(
        subject_id="test-user",
        tenant_id=None,
        roles=frozenset(roles),
        is_global_admin=is_global_admin,
    )


_svc = PermissionService()

_CSV_ROLES = (
    "TENANT_CSV_UPLOADER",
    "TENANT_DOC_UPLOADER",
    "BILLING_CONTACT",
    "LEASING_MANAGER",
    "PRIMARY_TENANT_CONTACT",
    "AUTHORIZED_SIGNATORY",
    "COMPANY_ADMIN_CENTRAL",
    "COMPANY_ADMIN",
)
_DOC_ROLES = (
    "TENANT_DOC_UPLOADER",
    "PRIMARY_TENANT_CONTACT",
    "AUTHORIZED_SIGNATORY",
    "COMPANY_ADMIN_CENTRAL",
    "COMPANY_ADMIN",
)
_APPROVE_ROLES = ("SALES_DATA_APPROVER", "SALES_DATA_TEAM")


class TestSalesDataActionMap:
    def test_submit_maps_to_sales_data_update(self) -> None:
        assert ACTION_PERMISSION_MAP["SUBMIT_SALES_DATA"] == "SALES_DATA_UPDATE"

    def test_supporting_doc_maps_to_upload_sales_data(self) -> None:
        assert (
            ACTION_PERMISSION_MAP["SUBMIT_SALES_DATA_SUPPORTING_DOC"]
            == "UPLOAD_SALES_DATA"
        )

    def test_approve_maps_to_sales_data_approve(self) -> None:
        assert ACTION_PERMISSION_MAP["APPROVE_SALES_DATA"] == "SALES_DATA_APPROVE"


class TestSalesDataRoleMap:
    def test_csv_roles_have_update(self) -> None:
        for role in _CSV_ROLES:
            assert "SALES_DATA_UPDATE" in ROLE_PERMISSION_MAP[role], role

    def test_doc_roles_have_upload(self) -> None:
        for role in _DOC_ROLES:
            assert "UPLOAD_SALES_DATA" in ROLE_PERMISSION_MAP[role], role

    def test_billing_and_leasing_cannot_upload_docs(self) -> None:
        for role in ("BILLING_CONTACT", "LEASING_MANAGER", "TENANT_CSV_UPLOADER"):
            assert "UPLOAD_SALES_DATA" not in ROLE_PERMISSION_MAP[role], role

    def test_approve_roles_have_approve(self) -> None:
        for role in _APPROVE_ROLES:
            assert "SALES_DATA_APPROVE" in ROLE_PERMISSION_MAP[role], role

    def test_admin_has_all_sales_permissions(self) -> None:
        perms = ROLE_PERMISSION_MAP["ADMIN"]
        assert "SALES_DATA_UPDATE" in perms
        assert "UPLOAD_SALES_DATA" in perms
        assert "SALES_DATA_APPROVE" in perms

    def test_mall_manager_has_no_sales_write_permissions(self) -> None:
        perms = ROLE_PERMISSION_MAP["MALL_MANAGER"]
        assert "SALES_DATA_UPDATE" not in perms
        assert "UPLOAD_SALES_DATA" not in perms
        assert "SALES_DATA_APPROVE" not in perms

    def test_financial_controller_and_shop_manager_are_not_csv_uploaders(self) -> None:
        for role in ("FINANCIAL_CONTROLLER", "SHOP_MANAGER"):
            assert role not in ROLE_PERMISSION_MAP


class TestSalesDataCheck:
    def test_csv_role_can_submit(self) -> None:
        _svc.check("SUBMIT_SALES_DATA", _auth("BILLING_CONTACT"))

    def test_permission_string_fast_path(self) -> None:
        _svc.check("SUBMIT_SALES_DATA", _auth("SALES_DATA_UPDATE"))

    def test_mall_manager_cannot_submit(self) -> None:
        with pytest.raises(PermissionDeniedError) as exc_info:
            _svc.check("SUBMIT_SALES_DATA", _auth("MALL_MANAGER"))
        assert exc_info.value.required_role == "SALES_DATA_UPDATE"

    def test_financial_controller_cannot_submit(self) -> None:
        with pytest.raises(PermissionDeniedError):
            _svc.check("SUBMIT_SALES_DATA", _auth("FINANCIAL_CONTROLLER"))

    def test_shop_manager_cannot_submit(self) -> None:
        with pytest.raises(PermissionDeniedError):
            _svc.check("SUBMIT_SALES_DATA", _auth("SHOP_MANAGER"))

    def test_billing_contact_cannot_submit_supporting_doc(self) -> None:
        with pytest.raises(PermissionDeniedError) as exc_info:
            _svc.check("SUBMIT_SALES_DATA_SUPPORTING_DOC", _auth("BILLING_CONTACT"))
        assert exc_info.value.required_role == "UPLOAD_SALES_DATA"

    def test_primary_tenant_contact_can_submit_supporting_doc(self) -> None:
        _svc.check(
            "SUBMIT_SALES_DATA_SUPPORTING_DOC", _auth("PRIMARY_TENANT_CONTACT")
        )

    def test_billing_contact_cannot_approve(self) -> None:
        with pytest.raises(PermissionDeniedError) as exc_info:
            _svc.check("APPROVE_SALES_DATA", _auth("BILLING_CONTACT"))
        assert exc_info.value.required_role == "SALES_DATA_APPROVE"

    def test_sales_data_team_can_approve(self) -> None:
        _svc.check("APPROVE_SALES_DATA", _auth("SALES_DATA_TEAM"))

    def test_sales_data_team_cannot_submit_csv(self) -> None:
        with pytest.raises(PermissionDeniedError):
            _svc.check("SUBMIT_SALES_DATA", _auth("SALES_DATA_TEAM"))

    def test_is_global_admin_bypasses_empty_roles(self) -> None:
        _svc.check("SUBMIT_SALES_DATA", _auth(is_global_admin=True))
        _svc.check("APPROVE_SALES_DATA", _auth(is_global_admin=True))
        _svc.check("SUBMIT_SALES_DATA_SUPPORTING_DOC", _auth(is_global_admin=True))

    def test_ensure_wrappers(self) -> None:
        _svc.ensure_can_submit_sales_data(_auth("BILLING_CONTACT"))
        _svc.ensure_can_submit_sales_data_supporting_doc(
            _auth("PRIMARY_TENANT_CONTACT")
        )
        _svc.ensure_can_approve_sales_data(_auth("SALES_DATA_TEAM"))
        with pytest.raises(PermissionDeniedError):
            _svc.ensure_can_approve_sales_data(_auth("BILLING_CONTACT"))


class TestGraphPermissionGuard:
    def test_missing_auth_is_skipped(self) -> None:
        assert graph_permission_guard({}, "SUBMIT_SALES_DATA") is None
        assert graph_permission_guard({"auth_context": None}, "SUBMIT_SALES_DATA") is None

    def test_allowed_role_returns_none(self) -> None:
        assert (
            graph_permission_guard(
                {"auth_context": _auth("BILLING_CONTACT")}, "SUBMIT_SALES_DATA"
            )
            is None
        )

    def test_denied_role_returns_waiting_message(self) -> None:
        denied = graph_permission_guard(
            {"auth_context": _auth("MALL_MANAGER")}, "SUBMIT_SALES_DATA"
        )
        assert denied is not None
        assert denied["status"] == "WAITING_FOR_USER"
        assert "Permission denied" in denied["response_message"]


def _csv_state(**overrides: Any) -> dict[str, Any]:
    state: dict[str, Any] = {
        "backend_refs": {
            "submit_payload": [
                {
                    "lease_code": "LC-1001",
                    "sales_period_start": "2026-07-01",
                    "sales_period_end": "2026-07-31",
                    "sales_amt": 1000.0,
                }
            ]
        }
    }
    state.update(overrides)
    return state


def _manual_state(**overrides: Any) -> dict[str, Any]:
    state: dict[str, Any] = {
        "collected_data": {
            "lease_code": "LC-1001",
            "sales_period_start": "2026-07-01",
            "sales_period_end": "2026-07-31",
            "sales_amt": 1000.0,
        },
        "backend_refs": {},
    }
    state.update(overrides)
    return state


def _admin_state(**overrides: Any) -> dict[str, Any]:
    state: dict[str, Any] = {
        "collected_data": {
            "admin_action": "APPROVED",
            "admin_targets": [
                {
                    "lease_id": 4,
                    "lease_code": "LC-2001",
                    "sales_period_start": "2026-07-01",
                    "sales_period_end": "2026-07-31",
                }
            ],
        },
        "backend_refs": {},
    }
    state.update(overrides)
    return state


def _doc_state(**overrides: Any) -> dict[str, Any]:
    state: dict[str, Any] = {
        "collected_data": {
            "lease_id": 7,
            "lease_code": "LC-1005",
            "document_id": "doc-1",
            "filename": "audit.pdf",
            "current_status": "NEED_REUPLOAD",
            "target_status": "RE_UPLOADED",
            "sales_period_start": "2026-07-01",
            "sales_period_end": "2026-07-31",
            "comment_text": "Corrected figures",
        }
    }
    state.update(overrides)
    return state


class TestSalesDataWriteNodeGuards:
    @pytest.mark.asyncio
    async def test_csv_submit_denied_for_mall_manager(self) -> None:
        with patch(
            "app.agents.graph.nodes.sales_data.sales_data_api_submission_node.SalesDataApiClient"
        ) as client_cls:
            result = await sales_data_api_submission_node(
                _csv_state(auth_context=_auth("MALL_MANAGER"))
            )
        client_cls.assert_not_called()
        assert result["status"] == "WAITING_FOR_USER"
        assert "Permission denied" in result["response_message"]

    @pytest.mark.asyncio
    async def test_csv_submit_allowed_for_billing_contact(self) -> None:
        client = AsyncMock()
        client.submit_sales_data = AsyncMock(return_value={"success": True})
        with (
            patch(
                "app.agents.graph.nodes.sales_data.sales_data_api_submission_node.get_current_bearer_token",
                return_value="tok",
            ),
            patch(
                "app.agents.graph.nodes.sales_data.sales_data_api_submission_node.SalesDataApiClient",
                return_value=client,
            ),
        ):
            result = await sales_data_api_submission_node(
                _csv_state(auth_context=_auth("BILLING_CONTACT"))
            )
        client.submit_sales_data.assert_awaited_once()
        assert result["status"] == "COMPLETED"

    @pytest.mark.asyncio
    async def test_csv_submit_skips_check_without_auth(self) -> None:
        client = AsyncMock()
        client.submit_sales_data = AsyncMock(return_value={"success": True})
        with (
            patch(
                "app.agents.graph.nodes.sales_data.sales_data_api_submission_node.get_current_bearer_token",
                return_value="tok",
            ),
            patch(
                "app.agents.graph.nodes.sales_data.sales_data_api_submission_node.SalesDataApiClient",
                return_value=client,
            ),
        ):
            result = await sales_data_api_submission_node(_csv_state())
        client.submit_sales_data.assert_awaited_once()
        assert result["status"] == "COMPLETED"

    @pytest.mark.asyncio
    async def test_manual_entry_denied_for_mall_manager(self) -> None:
        with patch(
            "app.agents.graph.nodes.sales_data.manual_entry_submission_node.SalesDataApiClient"
        ) as client_cls:
            result = await manual_entry_submission_node(
                _manual_state(auth_context=_auth("MALL_MANAGER"))
            )
        client_cls.assert_not_called()
        assert result["status"] == "WAITING_FOR_USER"
        assert "Permission denied" in result["response_message"]

    @pytest.mark.asyncio
    async def test_admin_status_denied_for_billing_contact(self) -> None:
        with patch(
            "app.agents.graph.nodes.sales_data.admin_status_update_node.SalesDataApiClient"
        ) as client_cls:
            result = await admin_status_update_node(
                _admin_state(auth_context=_auth("BILLING_CONTACT"))
            )
        client_cls.assert_not_called()
        assert result["status"] == "WAITING_FOR_USER"
        assert "Permission denied" in result["response_message"]

    @pytest.mark.asyncio
    async def test_admin_status_allowed_for_sales_data_team(self) -> None:
        client = AsyncMock()
        client.update_sales_data_statuses = AsyncMock(
            return_value=[{"lease_code": "LC-2001", "success": True}]
        )
        with (
            patch(
                "app.agents.graph.nodes.sales_data.admin_status_update_node.get_current_bearer_token",
                return_value="tok",
            ),
            patch(
                "app.agents.graph.nodes.sales_data.admin_status_update_node.SalesDataApiClient",
                return_value=client,
            ),
        ):
            result = await admin_status_update_node(
                _admin_state(auth_context=_auth("SALES_DATA_TEAM"))
            )
        client.update_sales_data_statuses.assert_awaited_once()
        assert result["status"] == "WAITING_FOR_USER"
        assert "approved" in result["response_message"].lower()

    @pytest.mark.asyncio
    async def test_supporting_doc_denied_for_billing_contact(self) -> None:
        with patch(
            "app.agents.graph.nodes.sales_data.supporting_doc_submission_node.SalesDataApiClient"
        ) as client_cls:
            result = await supporting_doc_submission_node(
                _doc_state(auth_context=_auth("BILLING_CONTACT"))
            )
        client_cls.assert_not_called()
        assert result["status"] == "WAITING_FOR_USER"
        assert "Permission denied" in result["response_message"]

    @pytest.mark.asyncio
    async def test_supporting_doc_allowed_for_primary_tenant_contact(self) -> None:
        client = AsyncMock()
        client.update_sales_data_status = AsyncMock(return_value={"success": True})
        with (
            patch(
                "app.agents.graph.nodes.sales_data.supporting_doc_submission_node.get_current_bearer_token",
                return_value="tok",
            ),
            patch(
                "app.agents.graph.nodes.sales_data.supporting_doc_submission_node.SalesDataApiClient",
                return_value=client,
            ),
        ):
            result = await supporting_doc_submission_node(
                _doc_state(auth_context=_auth("PRIMARY_TENANT_CONTACT"))
            )
        client.update_sales_data_status.assert_awaited_once()
        assert result["status"] == "COMPLETED"
