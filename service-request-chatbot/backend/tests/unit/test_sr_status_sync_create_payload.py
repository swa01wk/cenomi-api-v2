"""sr_status_sync stores create_payload snapshot for open-existing FM/RDD builders."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.agents.graph.nodes.shared.sr_status_sync_node import sr_status_sync_node
from app.agents.services.platform_api_client import ServiceRequestGetResult


@pytest.mark.asyncio
async def test_sync_stores_create_payload_from_get() -> None:
    state = {
        "session_id": "sess-001",
        "user_id": "user-001",
        "trace_manager": None,
        "trace_id": None,
        "workflow_stage": None,
        "backend_refs": {"sr_id": "11714"},
        "auth_context": None,
    }
    mock_result = ServiceRequestGetResult(
        success=True,
        sr_id="11714",
        status="PENDING",
        service_request_operations=[
            {"assigned_role": "FM_MANAGER", "status": "IN_PROGRESS"},
        ],
        payload={
            "mall": "Jawharat Jeddah",
            "brand": "Under Armour",
            "lease": "T0028604",
            "lease_code": "T0028604",
            "title": "handover-T0028604",
            "tenant_profile_id": 116,
            "property_id": 789,
            "contract_id": 456,
            "lease_id": 456,
        },
    )
    with patch(
        "app.agents.graph.nodes.shared.sr_status_sync_node.get_service_request_api_service",
        return_value=AsyncMock(get_service_request=AsyncMock(return_value=mock_result)),
    ):
        result = await sr_status_sync_node(state)

    create_payload = result["backend_refs"]["create_payload"]
    assert create_payload["service_request_id"] == "11714"
    assert create_payload["lease_code"] == "T0028604"
    assert create_payload["lease_id"] == 456
    assert create_payload["payload"]["mall"] == "Jawharat Jeddah"
    assert create_payload["payload"]["brand"] == "Under Armour"
    assert result["workflow_stage"] == "FM_REVIEW"
