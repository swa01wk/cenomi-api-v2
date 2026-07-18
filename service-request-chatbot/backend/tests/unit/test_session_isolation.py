"""Unit tests — session isolation and stale-state cleanup (Fix 8).

Tests cover:
- Stale sr_id cleared when workflow_stage=None
- Stale sr_id cleared when workflow_stage="CANCELLED"
- Cross-session state not leaked (load_session_node clears transient fields)
- Cancel clears all transient state
- New request after cancel starts clean
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# supervisor_node._extract_sr_id_updates
# ---------------------------------------------------------------------------


class TestStaleSrIdGuard:
    def _make_decision(self, intent: str = "CREATE_HANDOVER_SERVICE_REQUEST") -> MagicMock:
        d = MagicMock()
        d.intent = intent
        d.confidence = 0.95
        d.target_agent = "handover_agent"
        d.service_category = "FIT_OUT_AND_HANDOVER"
        d.sub_category = "HANDOVER"
        return d

    def _state(self, **overrides: Any) -> dict[str, Any]:
        return {
            "user_message": "create handover for LC-123",
            "backend_refs": {},
            "workflow_stage": None,
            **overrides,
        }

    def test_stale_sr_id_cleared_when_workflow_stage_none(self) -> None:
        from app.agents.graph.nodes.supervisor_node import _extract_sr_id_updates

        state = self._state(
            backend_refs={"sr_id": "OLD-SR-001"},
            workflow_stage=None,
        )
        result = _extract_sr_id_updates(state, self._make_decision())
        backend_refs = result.get("backend_refs", {})
        assert backend_refs.get("sr_id") is None

    def test_stale_sr_id_cleared_when_workflow_stage_cancelled(self) -> None:
        from app.agents.graph.nodes.supervisor_node import _extract_sr_id_updates

        state = self._state(
            backend_refs={"sr_id": "OLD-SR-001"},
            workflow_stage="CANCELLED",
        )
        result = _extract_sr_id_updates(state, self._make_decision())
        backend_refs = result.get("backend_refs", {})
        assert backend_refs.get("sr_id") is None

    def test_sr_id_not_cleared_when_workflow_active(self) -> None:
        from app.agents.graph.nodes.supervisor_node import _extract_sr_id_updates

        state = self._state(
            backend_refs={"sr_id": "ACTIVE-SR"},
            workflow_stage="FM_REVIEW",
        )
        result = _extract_sr_id_updates(state, self._make_decision())
        # Active workflow — must not clear sr_id
        assert "backend_refs" not in result or result.get("backend_refs", {}).get("sr_id") == "ACTIVE-SR"

    def test_create_new_sr_intent_skips_sr_id_extraction(self) -> None:
        from app.agents.graph.nodes.supervisor_node import _extract_sr_id_updates

        state = self._state(
            backend_refs={"sr_id": "OLD-SR"},
            workflow_stage=None,
        )
        decision = self._make_decision("CREATE_RDD_SERVICE_REQUEST")
        result = _extract_sr_id_updates(state, decision)
        # CREATE_NEW_SR_INTENTS → return {} immediately, no sr_id manipulation
        assert result == {}


# ---------------------------------------------------------------------------
# load_session_node — transient state cleared on cancelled/empty session
# ---------------------------------------------------------------------------


class TestLoadSessionTransientClear:
    def _make_state_service(self, loaded: dict) -> MagicMock:
        svc = MagicMock()
        svc.load = AsyncMock(return_value=loaded)
        return svc

    @pytest.mark.asyncio
    async def test_transient_fields_cleared_when_workflow_stage_none(self) -> None:
        from app.agents.graph.nodes.shared.load_session_node import load_session_node
        from uuid import uuid4

        session_id = str(uuid4())
        loaded = {
            "workflow_stage": None,
            "active_field": "startDate",
            "extracted_fields": {"startDate": "2026-08-01"},
            "corrected_fields": {"startDate": "2026-08-02"},
            "validation_errors": [{"field": "startDate", "message": "error"}],
            "response_ui": {"type": "confirmation_card"},
            "collected_data": {"lease_code": "LC-001"},
        }
        state_service = self._make_state_service(loaded)

        state: dict[str, Any] = {
            "session_id": session_id,
            "conversation_state_service": state_service,
        }
        result = await load_session_node(state)

        assert "active_field" not in result
        assert "extracted_fields" not in result
        assert "corrected_fields" not in result
        assert "validation_errors" not in result
        assert "response_ui" not in result
        # collected_data should be preserved (it's not transient)
        assert result.get("collected_data") == {"lease_code": "LC-001"}

    @pytest.mark.asyncio
    async def test_transient_fields_cleared_when_workflow_stage_cancelled(self) -> None:
        from app.agents.graph.nodes.shared.load_session_node import load_session_node
        from uuid import uuid4

        session_id = str(uuid4())
        loaded = {
            "workflow_stage": "CANCELLED",
            "active_field": "notes",
            "extracted_fields": {"notes": "test"},
            "validation_errors": [],
            "response_ui": {"cancelled": True},
        }
        state_service = self._make_state_service(loaded)

        state: dict[str, Any] = {
            "session_id": session_id,
            "conversation_state_service": state_service,
        }
        result = await load_session_node(state)

        assert "active_field" not in result
        assert "extracted_fields" not in result
        assert "response_ui" not in result

    @pytest.mark.asyncio
    async def test_transient_fields_preserved_when_workflow_active(self) -> None:
        from app.agents.graph.nodes.shared.load_session_node import load_session_node
        from uuid import uuid4

        session_id = str(uuid4())
        loaded = {
            "workflow_stage": "FM_REVIEW",
            "active_field": "unit_readiness_date",
            "extracted_fields": {"unit_readiness_date": "2026-08-10"},
            "collected_data": {"lease_code": "LC-001"},
        }
        state_service = self._make_state_service(loaded)

        state: dict[str, Any] = {
            "session_id": session_id,
            "conversation_state_service": state_service,
        }
        result = await load_session_node(state)

        # Active workflow — transient fields preserved
        assert result.get("active_field") == "unit_readiness_date"
        assert result.get("extracted_fields") == {"unit_readiness_date": "2026-08-10"}

    @pytest.mark.asyncio
    async def test_missing_fields_always_reset(self) -> None:
        from app.agents.graph.nodes.shared.load_session_node import load_session_node
        from uuid import uuid4

        session_id = str(uuid4())
        loaded = {
            "workflow_stage": "CREATE_SR",
            "missing_fields": ["notes", "comments"],
            "collected_data": {"lease_code": "LC-001"},
        }
        state_service = self._make_state_service(loaded)

        state: dict[str, Any] = {
            "session_id": session_id,
            "conversation_state_service": state_service,
        }
        result = await load_session_node(state)

        # missing_fields is always popped regardless of workflow_stage
        assert "missing_fields" not in result
