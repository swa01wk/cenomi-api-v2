"""Unit tests — completion state transitions (Fix 7).

Tests cover:
- FM save emits stage_complete=False and action="fm_saved"
- FM approve emits stage_complete=True and action="fm_approved"
- CREATE_SR clears confirmation_status and emits stage_complete=False
- RDD submit emits stage_complete=False and action="rdd_submitted"
- RDD final approve emits workflow_complete=True and action="rdd_final_approved"
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_sr_result(sr_id: str = "SR-001", error: str | None = None) -> MagicMock:
    r = MagicMock()
    r.sr_id = sr_id
    r.error = error
    r.status_code = 200
    r.latency_ms = 10
    r.correlation_id = None
    r.response_payload = {}
    r.endpoint = "https://api.example.com"
    r.success = error is None
    r.service_request_operations = []
    r.payload = {}
    return r


# ---------------------------------------------------------------------------
# api_submission_node — CREATE_SR
# ---------------------------------------------------------------------------


class TestCreateSRCompletionSignal:
    @pytest.mark.asyncio
    async def test_confirmation_status_cleared_after_sr_created(self) -> None:
        from app.agents.graph.nodes.handover.api_submission_node import api_submission_node

        mock_svc = MagicMock()
        mock_svc.create_service_request = AsyncMock(return_value=_make_sr_result())

        with patch(
            "app.agents.graph.nodes.handover.api_submission_node.get_service_request_api_service",
            return_value=mock_svc,
        ):
            state: dict[str, Any] = {
                "confirmation_status": "CONFIRMED",
                "validation_errors": [],
                "backend_refs": {
                    "create_payload": {
                        "payload": {"mall": "RP"},
                        "title": "T",
                        "tenant_profile_id": "TP1",
                        "property_id": "P1",
                        "service_category": "FIT_OUT_AND_HANDOVER",
                        "sub_category": "HANDOVER",
                        "lease_code": "LC-001",
                        "lease_id": "LID1",
                        "service_request_id": "",
                    }
                },
                "session_id": None,
                "user_id": None,
                "trace_manager": None,
                "trace_id": None,
                "_trace_node_run_id": None,
            }
            result = await api_submission_node(state)

        assert result.get("confirmation_status") is None
        assert result.get("confirmation_required") is False

    @pytest.mark.asyncio
    async def test_create_sr_emits_stage_complete_false(self) -> None:
        from app.agents.graph.nodes.handover.api_submission_node import api_submission_node

        mock_svc = MagicMock()
        mock_svc.create_service_request = AsyncMock(return_value=_make_sr_result())

        with patch(
            "app.agents.graph.nodes.handover.api_submission_node.get_service_request_api_service",
            return_value=mock_svc,
        ):
            state: dict[str, Any] = {
                "confirmation_status": "CONFIRMED",
                "validation_errors": [],
                "backend_refs": {
                    "create_payload": {
                        "payload": {"mall": "RP"},
                        "title": "T",
                        "tenant_profile_id": "TP1",
                        "property_id": "P1",
                        "service_category": "FIT_OUT_AND_HANDOVER",
                        "sub_category": "HANDOVER",
                        "lease_code": "LC-001",
                        "lease_id": "LID1",
                        "service_request_id": "",
                    }
                },
                "session_id": None,
                "user_id": None,
                "trace_manager": None,
                "trace_id": None,
                "_trace_node_run_id": None,
            }
            result = await api_submission_node(state)

        response_ui = result.get("response_ui", {})
        assert response_ui.get("stage_complete") is False
        assert response_ui.get("workflow_stage") == "SR_CREATED"


# ---------------------------------------------------------------------------
# fm_api_submission_node — FM save / approve
# ---------------------------------------------------------------------------


class TestFMCompletionSignals:
    def _make_fm_state(self, fm_action: str = "save") -> dict[str, Any]:
        return {
            "validation_errors": [],
            "backend_refs": {
                "sr_id": "SR-001",
                "fm_payload": {
                    "payload": {"mall": "RP"},
                    "title": "T",
                    "tenant_profile_id": "TP1",
                    "property_id": "P1",
                    "service_category": "FIT_OUT_AND_HANDOVER",
                    "sub_category": "HANDOVER",
                    "lease_code": "LC-001",
                    "lease_id": "LID1",
                    "service_request_id": "SR-001",
                    "status": "IN_PROCESS",
                    "fm_action": fm_action,
                },
                "fm_action": fm_action,
            },
            "session_id": None,
            "user_id": None,
            "trace_manager": None,
            "trace_id": None,
            "_trace_node_run_id": None,
            "auth_context": None,
        }

    @pytest.mark.asyncio
    async def test_fm_save_emits_stage_complete_false(self) -> None:
        from app.agents.graph.nodes.handover.fm_api_submission_node import fm_api_submission_node

        mock_svc = MagicMock()
        mock_svc.get_service_request = AsyncMock(return_value=_make_sr_result())
        mock_svc.patch_service_request = AsyncMock(return_value=_make_sr_result())

        with patch(
            "app.agents.graph.nodes.handover.fm_api_submission_node.get_service_request_api_service",
            return_value=mock_svc,
        ):
            result = await fm_api_submission_node(self._make_fm_state("save"))

        response_ui = result.get("response_ui", {})
        assert response_ui.get("stage_complete") is False
        assert response_ui.get("action") == "fm_saved"

    @pytest.mark.asyncio
    async def test_fm_approve_emits_stage_complete_true(self) -> None:
        from app.agents.graph.nodes.handover.fm_api_submission_node import fm_api_submission_node

        # Post-approve GET returns an operation that is FINISHED
        ops_result = _make_sr_result()
        ops_result.service_request_operations = [
            {"assigned_role": "FM_MANAGER", "status": "FINISHED"}
        ]
        mock_svc = MagicMock()
        mock_svc.get_service_request = AsyncMock(return_value=ops_result)
        mock_svc.patch_service_request = AsyncMock(return_value=_make_sr_result())

        with patch(
            "app.agents.graph.nodes.handover.fm_api_submission_node.get_service_request_api_service",
            return_value=mock_svc,
        ):
            result = await fm_api_submission_node(self._make_fm_state("approve"))

        response_ui = result.get("response_ui", {})
        assert response_ui.get("stage_complete") is True
        assert response_ui.get("action") == "fm_approved"


# ---------------------------------------------------------------------------
# rdd_api_submission_node — RDD submit / final_approve
# ---------------------------------------------------------------------------


class TestRDDCompletionSignals:
    def _make_rdd_state(self, rdd_action: str = "submit") -> dict[str, Any]:
        return {
            "validation_errors": [],
            "backend_refs": {
                "sr_id": "SR-001",
                "rdd_payload": {
                    "payload": {"mall": "RP"},
                    "title": "T",
                    "tenant_profile_id": "TP1",
                    "property_id": "P1",
                    "service_category": "FIT_OUT_AND_HANDOVER",
                    "sub_category": "HANDOVER",
                    "lease_code": "LC-001",
                    "lease_id": "LID1",
                    "service_request_id": "SR-001",
                    "status": "REPORT_SUBMITTED",
                    "rdd_action": rdd_action,
                },
                "rdd_action": rdd_action,
            },
            "session_id": None,
            "user_id": None,
            "trace_manager": None,
            "trace_id": None,
            "_trace_node_run_id": None,
            "auth_context": None,
            "workflow_stage": "RDD_REVIEW",
            "collected_data": {},
            "documents": [],
            "response_decorations": {},
        }

    @pytest.mark.asyncio
    async def test_rdd_submit_emits_stage_complete_false(self) -> None:
        from app.agents.graph.nodes.handover.rdd_api_submission_node import rdd_api_submission_node

        mock_svc = MagicMock()
        mock_svc.get_service_request = AsyncMock(return_value=_make_sr_result())
        mock_svc.submit_report = AsyncMock(return_value=_make_sr_result())

        with patch(
            "app.agents.graph.nodes.handover.rdd_api_submission_node.get_service_request_api_service",
            return_value=mock_svc,
        ):
            result = await rdd_api_submission_node(self._make_rdd_state("submit"))

        response_ui = result.get("response_ui", {})
        assert response_ui.get("stage_complete") is False
        assert response_ui.get("action") == "rdd_submitted"

    @pytest.mark.asyncio
    async def test_rdd_final_approve_emits_workflow_complete_true(self) -> None:
        from app.agents.graph.nodes.handover.rdd_api_submission_node import rdd_api_submission_node

        ops_result = _make_sr_result()
        ops_result.service_request_operations = [
            {"assigned_role": "DD_ENGINEER", "status": "FINISHED"}
        ]
        mock_svc = MagicMock()
        mock_svc.get_service_request = AsyncMock(return_value=ops_result)
        mock_svc.patch_service_request = AsyncMock(return_value=_make_sr_result())

        with patch(
            "app.agents.graph.nodes.handover.rdd_api_submission_node.get_service_request_api_service",
            return_value=mock_svc,
        ):
            result = await rdd_api_submission_node(self._make_rdd_state("final_approve"))

        response_ui = result.get("response_ui", {})
        assert response_ui.get("workflow_complete") is True
        assert response_ui.get("action") == "rdd_final_approved"
        assert result.get("workflow_stage") == "SR_COMPLETED"
