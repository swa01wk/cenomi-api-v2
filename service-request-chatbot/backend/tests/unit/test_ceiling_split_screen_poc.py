"""Ceiling Inspection split-screen POC — form_state, lookups, mock notifications."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from httpx import ASGITransport, AsyncClient

from app.agents.schemas import ceiling_schema as cs
from app.agents.services.ceiling_form_state import build_ceiling_form_state
from app.agents.services.notification_inbox_service import (
    MockNotificationInboxService,
    NotificationListFilters,
    _select_sub_categories,
    reset_mock_notifications,
)
from app.agents.services.service_request_api_service import (
    MockServiceRequestAPIService,
    reset_mock_sr_state,
)
from app.core.security import get_auth_context
from app.main import app
from app.types.chat import AuthContext

# ── form_state ────────────────────────────────────────────────────────────────


class TestFormState:
    def test_create_stage_is_editable_and_names_the_active_field(self) -> None:
        state = {
            "active_agent": cs.AGENT_NAME,
            "sub_category": cs.SUB_CATEGORY_OCI,
            "workflow_stage": cs.STAGE_CREATE,
            "collected_data": {
                "lease_code": "t0105712",
                "tenant_profile_id": 116,
                "lease_brand_mall": "t0105712 - Brand Under Armour - Jawharat Jeddah",
                "title": "OCI for FF050",
                "startDate": "2026-10-01",
                "inspection_done_by": 12,
                "_pending_message": "internal",
            },
            "missing_fields": ["description", "endDate", "comments"],
            "active_field": "description",
            "backend_refs": {},
        }
        fs = build_ceiling_form_state(state)
        assert fs["locked"] is False
        assert fs["active_field"] == "description"
        assert fs["missing"] == ["description", "endDate", "comments"]
        assert fs["values"]["title"] == "OCI for FF050"
        # Company resolved from the lease seed by tenant_profile_id.
        assert fs["values"]["company_name"] == "Under Armour Trading Co"
        assert "_pending_message" not in fs["values"]

    def test_reopened_sr_is_locked_with_banner_and_date_only(self) -> None:
        state = {
            "workflow_stage": cs.STAGE_L1_REVIEW,
            "sub_category": cs.SUB_CATEGORY_CCI,
            "collected_data": {
                "lease": "t0208831",
                "company_name": "204",
                "tenant_profile_id": 204,
                "startDate": "2026-10-01T06:00:00Z",
            },
            "backend_refs": {"sr_id": "sr-1"},
            "documents": [{"document_id": "d1", "document_type_id": "FITOUT_INSPECTION_OTHER", "filename": "a.pdf"}],
        }
        fs = build_ceiling_form_state(state)
        assert fs["locked"] is True
        assert "Level 1" in fs["banner"]
        assert fs["values"]["lease_code"] == "t0208831"
        assert fs["values"]["startDate"] == "2026-10-01"
        assert fs["values"]["company_name"] == "Nike Saudi Arabia LLC"
        assert fs["documents"][0]["file_name"] == "a.pdf"

    def test_inspection_times_reach_the_form(self) -> None:
        fs = build_ceiling_form_state(
            {
                "workflow_stage": cs.STAGE_CREATE,
                "collected_data": {"startDate": "2026-10-15", "inspectionTime": "10:30", "inspectionEndTime": "12:00"},
                "missing_fields": ["comments"],
                "active_field": "comments",
                "backend_refs": {},
            }
        )
        assert fs["values"]["inspectionTime"] == "10:30"
        assert fs["values"]["inspectionEndTime"] == "12:00"
        assert fs["active_field"] == "comments"

    def test_no_highlight_once_everything_is_collected(self) -> None:
        fs = build_ceiling_form_state(
            {"workflow_stage": cs.STAGE_CREATE, "missing_fields": [], "active_field": "comments", "backend_refs": {}}
        )
        assert fs["active_field"] is None


# ── lookups ───────────────────────────────────────────────────────────────────


@pytest.fixture
def auth_as() -> Iterator:
    def _set(ctx: AuthContext) -> None:
        app.dependency_overrides[get_auth_context] = lambda: ctx

    yield _set
    app.dependency_overrides.pop(get_auth_context, None)


async def _get(url: str) -> dict:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.get(url)
    assert resp.status_code == 200, resp.text
    return resp.json()


class TestLookups:
    @pytest.mark.asyncio
    async def test_companies_scoped_by_property(self, auth_as) -> None:
        auth_as(AuthContext(subject_id="t", roles=frozenset({"AUTHORIZED_SIGNATORY"}), unique_property_ids=(3041, 2018, 1905)))
        body = await _get("/api/v1/lookups/companies")
        names = [c["company_name"] for c in body["companies"]]
        assert names == ["Nike Saudi Arabia LLC", "Under Armour Trading Co"]

    @pytest.mark.asyncio
    async def test_leases_filtered_by_company(self, auth_as) -> None:
        auth_as(AuthContext(subject_id="t", unique_property_ids=(3041, 2018, 1905)))
        body = await _get("/api/v1/lookups/leases?tenant_profile_id=204")
        assert sorted(l["lease_code"] for l in body["leases"]) == ["t0208831", "t0301144"]

    @pytest.mark.asyncio
    async def test_single_property_sees_one_company(self, auth_as) -> None:
        auth_as(AuthContext(subject_id="t", unique_property_ids=(3041,)))
        body = await _get("/api/v1/lookups/companies")
        assert [c["tenant_profile_id"] for c in body["companies"]] == [116]

    @pytest.mark.asyncio
    async def test_global_admin_sees_all(self, auth_as) -> None:
        auth_as(AuthContext(subject_id="a", is_global_admin=True, unique_property_ids=(3041,)))
        body = await _get("/api/v1/lookups/companies")
        assert len(body["companies"]) == 3

    @pytest.mark.asyncio
    async def test_form_options(self) -> None:
        body = await _get("/api/v1/lookups/ceiling-form")
        assert {s["value"] for s in body["sub_categories"]} == {cs.SUB_CATEGORY_OCI, cs.SUB_CATEGORY_CCI}
        assert body["document_types"][0]["value"] == cs.OTHER_DOC_TYPE


# ── mock notifications ───────────────────────────────────────────────────────


@pytest.fixture(autouse=False)
def clean_mocks() -> Iterator[None]:
    reset_mock_sr_state()
    reset_mock_notifications()
    yield
    reset_mock_sr_state()
    reset_mock_notifications()


async def _rows(roles: set[str]) -> list[dict]:
    result = await MockNotificationInboxService().fetch_inbox(
        user_id="u",
        roles=frozenset(roles),
        filters=NotificationListFilters(sub_categories=(cs.SUB_CATEGORY_OCI,)),
    )
    return [item for g in result.groups for item in g.items]


class TestMockNotifications:
    @pytest.mark.asyncio
    async def test_each_transition_notifies_the_next_party(self, clean_mocks) -> None:
        svc = MockServiceRequestAPIService()
        created = await svc.create_service_request(
            {
                "sub_category": cs.SUB_CATEGORY_OCI,
                "service_category": cs.SERVICE_CATEGORY,
                "payload": {"lease_brand_mall": "t0105712 - Brand Under Armour - Jawharat Jeddah"},
            }
        )
        sr_id = created.sr_id
        assert sr_id

        dd = await _rows({"DD_ENGINEER"})
        assert [r.sr_id for r in dd] == [sr_id]
        assert "Level 1" in dd[0].message
        assert await _rows({"FM_MANAGER"}) == []
        assert await _rows({"AUTHORIZED_SIGNATORY"}) == []

        await svc.patch_service_request(sr_id, {"status": "APPROVED"})  # L1
        assert await _rows({"DD_ENGINEER"}) == []
        fm = await _rows({"FM_MANAGER"})
        assert [r.sr_id for r in fm] == [sr_id]

        await svc.patch_service_request(sr_id, {"status": "APPROVED"})  # L2
        assert await _rows({"FM_MANAGER"}) == []
        tenant = await _rows({"AUTHORIZED_SIGNATORY"})
        assert [r.sr_id for r in tenant] == [sr_id]
        assert "acknowledge" in tenant[0].message

        await svc.patch_service_request(sr_id, {"status": "APPROVED"})  # tenant ack
        assert await _rows({"AUTHORIZED_SIGNATORY"}) == []

    @pytest.mark.asyncio
    async def test_more_info_goes_back_to_the_tenant(self, clean_mocks) -> None:
        svc = MockServiceRequestAPIService()
        created = await svc.create_service_request(
            {"sub_category": cs.SUB_CATEGORY_CCI, "service_category": cs.SERVICE_CATEGORY, "payload": {}}
        )
        await svc.patch_service_request(created.sr_id, {"status": "ACTION_REQUIRED"})
        result = await MockNotificationInboxService().fetch_inbox(
            user_id="u", roles=frozenset({"COMPANY_ADMIN"})
        )
        rows = [i for g in result.groups if g.sub_category == cs.SUB_CATEGORY_CCI for i in g.items]
        assert [r.sr_id for r in rows] == [created.sr_id]
        assert await _rows({"DD_ENGINEER"}) == []

    @pytest.mark.parametrize("roles", [{"PRIMARY_TENANT_CONTACT"}, {"DD_ENGINEER"}, {"ADMIN"}])
    def test_live_adapter_never_calls_the_mock_only_tools(self, roles: set[str]) -> None:
        mock_only = frozenset({cs.SUB_CATEGORY_OCI, cs.SUB_CATEGORY_CCI})
        selected = _select_sub_categories(frozenset(roles), None, exclude=mock_only)
        assert selected
        assert not mock_only & set(selected)
