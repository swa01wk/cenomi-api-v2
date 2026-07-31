"""Unit tests — handover_entry_node routing for the HANDED_OVER sub_category.

B2 gap: new file per plan item.

Coverage
--------
- sub_category=HANDED_OVER in state routes to CREATE_HANDED_OVER_SR before
  any collected_data is present.
- CREATE_SR intent with no sub_category routes to CREATE_SR (not HANDED_OVER).
- already_handovered field in collected_data takes precedence over missing sub_category.
"""

from __future__ import annotations

from typing import Any

import pytest


def _base_state(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "session_id": "sess-ho-routing",
        "user_id": "user-ho-routing",
        "user_message": "I want to create a service request",
        "intent": "CREATE_HANDED_OVER_SR",
        "active_agent": "rdd_handover_agent",
        "sub_category": "HANDED_OVER",
        "service_category": "FIT_OUT_AND_HANDOVER",
        "workflow_stage": None,
        "collected_data": {},
        "conversation_history": [],
        "backend_refs": {},
        "status": "IN_PROGRESS",
        "confirmation_status": None,
        "confirmation_required": False,
        "documents": [],
        "missing_fields": [],
        "active_field": None,
        "extracted_fields": {},
        "validation_errors": [],
        "lease_matches": [],
        "selected_lease": None,
        "response_message": None,
        "response_ui": None,
        "response_decorations": None,
        "faq_sources": [],
        "action_override": None,
        "corrected_fields": None,
        "trace_id": None,
        "trace_manager": None,
        "conversation_state_service": None,
        "auth_context": None,
        "language": None,
        "msp_context": None,
    }
    base.update(overrides)
    return base


class TestHandedOverEntryRouting:
    @pytest.mark.asyncio
    async def test_sub_category_handed_over_in_state_routes_before_collected_data(
        self,
    ) -> None:
        """B2 gap: When sub_category=HANDED_OVER is already in state (set by supervisor/
        registry on the first turn), handover_entry_node must route to CREATE_HANDED_OVER_SR
        even before any collected_data is present."""
        from app.agents.graph.nodes.handover.handover_entry_node import handover_entry_node

        state = _base_state(
            sub_category="HANDED_OVER",
            collected_data={},
            workflow_stage=None,
        )
        result = await handover_entry_node(state)
        assert result.get("workflow_stage") == "CREATE_HANDED_OVER_SR", (
            "handover_entry_node must pin workflow_stage=CREATE_HANDED_OVER_SR when "
            f"sub_category=HANDED_OVER; got {result.get('workflow_stage')!r}"
        )

    @pytest.mark.asyncio
    async def test_handover_sub_category_routes_to_create_sr(self) -> None:
        """sub_category=HANDOVER must route to CREATE_SR (not CREATE_HANDED_OVER_SR)."""
        from app.agents.graph.nodes.handover.handover_entry_node import handover_entry_node

        state = _base_state(
            sub_category="HANDOVER",
            intent="CREATE_RDD_SERVICE_REQUEST",
            collected_data={},
            workflow_stage=None,
        )
        result = await handover_entry_node(state)
        assert result.get("workflow_stage") == "CREATE_SR", (
            f"Expected CREATE_SR for HANDOVER sub_category; got {result.get('workflow_stage')!r}"
        )

    @pytest.mark.asyncio
    async def test_already_handovered_field_routes_to_create_handed_over_sr(
        self,
    ) -> None:
        """already_handovered=already_trading in collected_data routes to CREATE_HANDED_OVER_SR
        even when sub_category was not set in state."""
        from app.agents.graph.nodes.handover.handover_entry_node import handover_entry_node

        state = _base_state(
            sub_category=None,
            intent="CREATE_HANDED_OVER_SR",
            collected_data={"already_handovered": "already_trading"},
            workflow_stage=None,
        )
        result = await handover_entry_node(state)
        assert result.get("workflow_stage") == "CREATE_HANDED_OVER_SR", (
            "already_handovered=already_trading must route to CREATE_HANDED_OVER_SR"
        )

    @pytest.mark.asyncio
    async def test_no_sub_category_no_collected_data_routes_to_create_sr(self) -> None:
        """When neither sub_category nor already_handovered field is present, fall to CREATE_SR."""
        from app.agents.graph.nodes.handover.handover_entry_node import handover_entry_node

        state = _base_state(
            sub_category=None,
            intent="CREATE_RDD_SERVICE_REQUEST",
            collected_data={},
            workflow_stage=None,
        )
        result = await handover_entry_node(state)
        assert result.get("workflow_stage") == "CREATE_SR"
