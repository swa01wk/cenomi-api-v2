"""Unit tests for preview_node reusing sr_status_sync state."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.agents.graph.nodes.handover.preview_node import preview_node


@pytest.mark.asyncio
async def test_preview_skips_get_when_sync_marker_present() -> None:
    state = {
        "backend_refs": {
            "sr_id": "11711",
            "platform_sr_status": "IN_PROCESS",
            "sr_operations": [{"assigned_role": "FM_MANAGER", "status": "IN_PROGRESS"}],
        },
        "collected_data": {"brand": "Test Brand", "mall": "Test Mall"},
    }

    with patch(
        "app.agents.graph.nodes.handover.preview_node.get_service_request_api_service"
    ) as mock_factory:
        result = await preview_node(state)

    mock_factory.assert_not_called()
    assert result["response_ui"]["type"] == "sr_preview_card"
    assert result["response_ui"]["srId"] == "11711"
    assert result["response_ui"]["status"] == "IN_PROCESS"
    field_keys = {f["key"] for f in result["response_ui"]["fields"]}
    assert "workflow_operations" in field_keys


@pytest.mark.asyncio
async def test_preview_gets_when_no_sync_marker() -> None:
    state = {
        "backend_refs": {"sr_id": "11711"},
        "collected_data": {},
    }
    mock_result = AsyncMock()
    mock_result.success = True
    mock_result.status = "IN_PROCESS"
    mock_result.payload = {"title": "Handover"}
    mock_result.service_request_operations = []
    mock_svc = AsyncMock(get_service_request=AsyncMock(return_value=mock_result))

    with patch(
        "app.agents.graph.nodes.handover.preview_node.get_service_request_api_service",
        return_value=mock_svc,
    ):
        result = await preview_node(state)

    mock_svc.get_service_request.assert_awaited_once_with("11711")
    assert result["response_ui"]["srId"] == "11711"
