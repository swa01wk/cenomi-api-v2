"""Supervisor SR reference extraction tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.schemas.supervisor_schema import SupervisorDecision
from app.types.chat import AuthContext


def _make_decision(**kwargs) -> SupervisorDecision:
    defaults = {
        "intent": "UPDATE_RDD_SERVICE_REQUEST",
        "confidence": 0.8,
        "service_category": "FIT_OUT_AND_HANDOVER",
        "sub_category": "HANDOVER",
        "target_agent": "rdd_handover_agent",
        "reasoning": "upload",
    }
    defaults.update(kwargs)
    return SupervisorDecision(**defaults)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_supervisor_extracts_ref_no_from_message() -> None:
    from app.agents.graph.nodes.supervisor_node import supervisor_node

    decision = _make_decision()
    state = {
        "user_message": "Ref. No:11711, upload document for this handover SR",
        "auth_context": AuthContext(subject_id="u1", roles=frozenset({"FM_MANAGER"})),
    }

    with patch(
        "app.agents.graph.nodes.supervisor_node._call_supervisor_llm",
        new_callable=AsyncMock,
        return_value=(decision, 10, 10, 100),
    ), patch(
        "app.agents.graph.nodes.supervisor_node.get_default_gateway",
        return_value=MagicMock(),
    ):
        result = await supervisor_node(state)

    assert result["backend_refs"]["sr_id"] == "11711"
    assert result["status"] == "IN_PROGRESS"


@pytest.mark.asyncio
async def test_supervisor_unknown_with_ref_routes_in_progress() -> None:
    from app.agents.graph.nodes.supervisor_node import supervisor_node

    decision = _make_decision(intent="UNKNOWN", confidence=0.4, target_agent=None)
    state = {"user_message": "Ref. No:11711"}

    with patch(
        "app.agents.graph.nodes.supervisor_node._call_supervisor_llm",
        new_callable=AsyncMock,
        return_value=(decision, 10, 10, 100),
    ), patch(
        "app.agents.graph.nodes.supervisor_node.get_default_gateway",
        return_value=MagicMock(),
    ):
        result = await supervisor_node(state)

    assert result["backend_refs"]["sr_id"] == "11711"
    assert result["status"] == "IN_PROGRESS"
