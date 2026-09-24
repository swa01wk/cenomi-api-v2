"""E2E — tenant Company Admin creates Work Permit via chatbot.

Mirrors handover e2e: real graph, mocked LLM / lease / SR API.
Default app_client is MALL_MANAGER; these tests override auth to tenant
raise roles and BILLING_CONTACT for the denial case.

Staging portal smoke (manual)
-----------------------------
Actor: COMPANY_ADMIN_CENTRAL after migration 1790000001 + FE + v2 RBAC deploy.
1. Copilot rail visible.
2. POST /auth/ai-session-token → allowed_agents includes tenant_ops and help.
3. New SR → Work Permit → Construction - Hot → Create with AI opens chat.
4. Complete chat → SR WORK_PERMIT / CONSTRUCTION_HOT created.
5. Form draft sync on new-SR page (bridge).
6. Contractor WP-only role: no Create with AI.
7. MM WP create and tenant form create still work.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient

from app.types.chat import AuthContext
from tests.e2e.conftest import post_turn
from tests.e2e.helpers import (
    make_field_extraction_mock,
    make_lease_mock,
    make_sr_api_mock,
    make_supervisor_mock,
    zara_lease,
)

_USER_ID = "e2e_tenant_wp_001"


def _extraction_fields(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Serialize collected fixtures the way the extraction prompt emits them."""
    out: dict[str, dict[str, Any]] = {}
    for key, value in data.items():
        if isinstance(value, (dict, list)):
            serialized = json.dumps(value)
        else:
            serialized = str(value)
        out[key] = {"value": serialized, "confidence": 1.0}
    return out


def _wp_collected_data() -> dict[str, Any]:
    lease = zara_lease()
    return {
        "lease_code": lease.lease_code,
        "lease_id": lease.lease_id,
        "property_id": lease.property_id,
        "unit_codes": lease.unit_codes,
        "mall": lease.mall,
        "brand": lease.brand,
        "tenant_profile_id": lease.tenant_profile_id,
        "city": lease.city,
        "contracted_area": lease.contracted_area,
        "unit_type": "Retail",
        "work_permit_type": "CONSTRUCTION_HOT",
        "contractor_name": "ABC Contractors",
        "phone": "501234567",
        "planneddate": {
            "worktimedate": "01/05/2025",
            "completionworkdate": "03/05/2025",
            "worktimefrom": "09:00",
            "worktimeto": "17:00",
            "completionfrom": "",
            "completionto": "",
        },
        "workers": {
            "instructor_name": "Ali",
            "worker": [
                {
                    "name": "Omar",
                    "designation": "Welder",
                    "iqama_number": "123",
                    "document_id": "",
                    "worker_qualification": "",
                }
            ],
        },
        "measureofsafety": {"fire_extinguisher": "yes"},
        "work_type": "",
        "work_location": "",
        "demand_appendices": "",
        "special_conditions": "",
        "description": "Welding on unit FF050",
        "title": "",
    }


def _override_auth(role: str) -> None:
    from app.main import app
    from app.core.security import get_auth_context

    auth = AuthContext(
        subject_id=f"e2e_{role.lower()}",
        roles=frozenset({role}),
    )
    app.dependency_overrides[get_auth_context] = lambda: auth


def _wp_supervisor_mock() -> AsyncMock:
    return make_supervisor_mock(
        intent="CREATE_WORK_PERMIT_SR",
        service_category="WORK_PERMIT",
        sub_category="WORK_PERMIT",
        target_agent="work_permit_agent",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "role",
    ("COMPANY_ADMIN_CENTRAL", "COMPANY_ADMIN", "AUTHORIZED_SIGNATORY"),
)
async def test_tenant_work_permit_create_happy_path(
    app_client: AsyncClient, role: str
) -> None:
    """Tenant raise roles can create CONSTRUCTION_HOT via work_permit_agent."""
    _override_auth(role)
    all_data = _wp_collected_data()
    sr_factory = make_sr_api_mock("SR-WP-TENANT-001")

    with (
        patch(
            "app.agents.graph.nodes.supervisor_node._call_supervisor_llm",
            _wp_supervisor_mock(),
        ),
        patch(
            "app.agents.graph.nodes.supervisor_node.get_default_gateway",
            return_value=MagicMock(model="gpt-4o-mini"),
        ),
        patch(
            "app.agents.graph.nodes.shared.field_extraction_node.FieldExtractionService",
            make_field_extraction_mock(
                {"lease_code": {"value": "LC-E2E-001", "confidence": 0.95}}
            ),
        ),
        patch(
            "app.agents.graph.nodes.shared.field_extraction_node.get_default_gateway",
            return_value=MagicMock(model="gpt-4o-mini"),
        ),
        patch(
            "app.agents.graph.nodes.shared.lease_lookup_node.get_lease_lookup_service",
            make_lease_mock([zara_lease()]),
        ),
        patch(
            "app.agents.graph.nodes.shared.validation_node._validation_service.validate_draft",
            MagicMock(return_value=[]),
        ),
    ):
        body1 = await post_turn(
            app_client,
            "I want to create a Work Permit service request (Construction - Hot)",
            user_id=_USER_ID,
        )
        assert body1.get("active_agent") == "work_permit_agent"
        session_id = body1.get("session_id")
        assert session_id is not None

    with (
        patch(
            "app.agents.graph.nodes.shared.field_extraction_node.FieldExtractionService",
            make_field_extraction_mock(_extraction_fields(all_data)),
        ),
        patch(
            "app.agents.graph.nodes.shared.field_extraction_node.get_default_gateway",
            return_value=MagicMock(model="gpt-4o-mini"),
        ),
        patch(
            "app.agents.graph.nodes.shared.lease_lookup_node.get_lease_lookup_service",
            make_lease_mock([zara_lease()]),
        ),
        patch(
            "app.agents.graph.nodes.shared.validation_node._validation_service.validate_draft",
            MagicMock(return_value=[]),
        ),
    ):
        body2 = await post_turn(
            app_client,
            "Lease LC-E2E-001, construction hot, all details provided",
            session_id=session_id,
            user_id=_USER_ID,
        )
        session_id = body2.get("session_id") or session_id

    with (
        patch(
            "app.agents.graph.nodes.shared.field_extraction_node.FieldExtractionService",
            make_field_extraction_mock(),
        ),
        patch(
            "app.agents.graph.nodes.shared.field_extraction_node.get_default_gateway",
            return_value=MagicMock(model="gpt-4o-mini"),
        ),
        patch(
            "app.agents.graph.nodes.shared.validation_node._validation_service.validate_draft",
            MagicMock(return_value=[]),
        ),
        patch(
            "app.agents.graph.nodes.work_permit.work_permit_api_submission_node.get_service_request_api_service",
            sr_factory,
        ),
    ):
        body3 = await post_turn(
            app_client,
            "yes",
            session_id=session_id,
            user_id=_USER_ID,
        )

    create_mock = sr_factory.return_value.create_service_request
    assert create_mock.await_count >= 1
    args, kwargs = create_mock.await_args
    payload = args[0] if args else kwargs.get("payload") or kwargs.get("body")
    assert isinstance(payload, dict)
    assert payload.get("service_category") == "WORK_PERMIT"
    assert payload.get("sub_category") == "CONSTRUCTION_HOT"
    # Platform stamps created_via=AI_ASSISTANT on POST; chatbot payload may omit it.

    assert body3.get("state", {}).get("ready_to_submit") is True or (
        "SR-WP-TENANT-001" in body3.get("message", "")
    )


@pytest.mark.asyncio
async def test_billing_contact_denied_work_permit_create(app_client: AsyncClient) -> None:
    """BILLING_CONTACT stays sales-data-only and cannot CREATE_WORK_PERMIT_SR."""
    _override_auth("BILLING_CONTACT")

    with (
        patch(
            "app.agents.graph.nodes.supervisor_node._call_supervisor_llm",
            _wp_supervisor_mock(),
        ),
        patch(
            "app.agents.graph.nodes.supervisor_node.get_default_gateway",
            return_value=MagicMock(model="gpt-4o-mini"),
        ),
        patch(
            "app.agents.graph.nodes.shared.field_extraction_node.FieldExtractionService",
            make_field_extraction_mock(),
        ),
        patch(
            "app.agents.graph.nodes.shared.field_extraction_node.get_default_gateway",
            return_value=MagicMock(model="gpt-4o-mini"),
        ),
    ):
        body = await post_turn(
            app_client,
            "I want to create a Work Permit service request (Construction - Hot)",
            user_id=_USER_ID,
        )

    assert body.get("active_agent") != "work_permit_agent"
    message = (body.get("message") or "").lower()
    assert message, "Expected a refusal or sales-data unmatched message"
    # Must not be ready to submit a WP
    assert body.get("state", {}).get("ready_to_submit") is not True


@pytest.mark.asyncio
async def test_mall_manager_work_permit_create_still_works(app_client: AsyncClient) -> None:
    """Regression: MALL_MANAGER (default app_client auth) still creates WP."""
    all_data = _wp_collected_data()
    sr_factory = make_sr_api_mock("SR-WP-MM-001")

    with (
        patch(
            "app.agents.graph.nodes.supervisor_node._call_supervisor_llm",
            _wp_supervisor_mock(),
        ),
        patch(
            "app.agents.graph.nodes.supervisor_node.get_default_gateway",
            return_value=MagicMock(model="gpt-4o-mini"),
        ),
        patch(
            "app.agents.graph.nodes.shared.field_extraction_node.FieldExtractionService",
            make_field_extraction_mock(_extraction_fields(all_data)),
        ),
        patch(
            "app.agents.graph.nodes.shared.field_extraction_node.get_default_gateway",
            return_value=MagicMock(model="gpt-4o-mini"),
        ),
        patch(
            "app.agents.graph.nodes.shared.lease_lookup_node.get_lease_lookup_service",
            make_lease_mock([zara_lease()]),
        ),
        patch(
            "app.agents.graph.nodes.shared.validation_node._validation_service.validate_draft",
            MagicMock(return_value=[]),
        ),
        patch(
            "app.agents.graph.nodes.work_permit.work_permit_api_submission_node.get_service_request_api_service",
            sr_factory,
        ),
    ):
        body1 = await post_turn(
            app_client,
            "I want to create a Work Permit service request (Construction - Hot)",
            user_id=_USER_ID,
        )
        assert body1.get("active_agent") == "work_permit_agent"
        session_id = body1.get("session_id")
        assert session_id

        body2 = await post_turn(
            app_client,
            "Lease LC-E2E-001 packed details",
            session_id=session_id,
            user_id=_USER_ID,
        )
        session_id = body2.get("session_id") or session_id

        body3 = await post_turn(
            app_client,
            "Confirm",
            session_id=session_id,
            user_id=_USER_ID,
        )

    create_mock = sr_factory.return_value.create_service_request
    assert create_mock.await_count >= 1
    assert body3.get("state", {}).get("ready_to_submit") is True or (
        "SR-WP-MM-001" in body3.get("message", "")
    )
