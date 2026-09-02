"""End-to-end regression test for the "change field X" mid-flow correction feature.

Drives the full HTTP stack (real graph, real routing, real persistence,
real response_generation_node) via httpx.AsyncClient, exactly like the other
tests in this directory. Only external I/O is mocked: supervisor LLM, field
extraction LLM, lease lookup API, and validation service. response_generation_node
is NOT mocked — this is intentional, because the bug this test guards against
(missing_field_node's correction re-ask silently discarded by an LLM rewrite)
can only be caught with the real response_generation_node in the loop.

Reproduces the exact live scenario reported during manual testing:
  1. User provides all CREATE_SR fields except endDate, notes, comments.
  2. Bot asks for endDate; user answers it. active_field moves to "notes".
  3. User says "I want to change inspection end date" (bare redirect, no
     inline value) while active_field="notes" and endDate already has a value.
  4. Bot must ask for a NEW endDate value — not silently re-confirm the old
     one and continue to "notes" (the bug that was previously observed live).
  5. User supplies the new endDate. Flow must resume asking for "notes" —
     proving the corrected value was merged and downstream collection continued.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from httpx import AsyncClient

from tests.e2e.helpers import (
    make_field_extraction_mock,
    make_lease_mock,
    make_supervisor_mock,
    zara_lease,
)

_USER_ID = "e2e_correction_user"


@pytest.mark.asyncio
async def test_bare_correction_redirect_reasks_new_value_and_flow_resumes(
    app_client: AsyncClient,
) -> None:
    # ── Turn 1: classify intent ─────────────────────────────────────────────
    with (
        patch(
            "app.agents.graph.nodes.supervisor_node._call_supervisor_llm",
            make_supervisor_mock(),
        ),
        patch(
            "app.agents.graph.nodes.supervisor_node.get_default_gateway",
            return_value=MagicMock(model="gpt-4o-mini"),
        ),
        patch(
            "app.agents.graph.nodes.shared.field_extraction_node.FieldExtractionService",
            make_field_extraction_mock({}),
        ),
        patch(
            "app.agents.graph.nodes.shared.field_extraction_node.get_default_gateway",
            return_value=MagicMock(model="gpt-4o-mini"),
        ),
        patch(
            "app.agents.graph.nodes.shared.validation_node._validation_service.validate_draft",
            MagicMock(return_value=[]),
        ),
    ):
        body1 = await app_client.post(
            "/api/chat/service-request",
            json={"user_id": _USER_ID, "message": "I want to create a handover SR"},
        )
        body1 = body1.json()
    session_id = body1["session_id"]
    assert body1.get("active_agent") == "rdd_handover_agent"

    # ── Turn 2: supply everything except endDate/notes/comments ────────────
    with (
        patch(
            "app.agents.graph.nodes.shared.field_extraction_node.FieldExtractionService",
            make_field_extraction_mock(
                {
                    "lease_code": {"value": zara_lease().lease_code, "confidence": 1.0},
                    "already_handovered": {"value": "initiate_handover", "confidence": 1.0},
                    "title": {"value": "Handover SR E2E Correction Test", "confidence": 1.0},
                    "description": {"value": "Formal handover for e2e test", "confidence": 1.0},
                    "startDate": {"value": "2025-05-01T09:00:00", "confidence": 1.0},
                    "inspectionTime": {"value": "09:00", "confidence": 1.0},
                    "inspection_done_by": {"value": "FM_MANAGER", "confidence": 1.0},
                }
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
        resp2 = await app_client.post(
            "/api/chat/service-request",
            json={
                "user_id": _USER_ID,
                "session_id": session_id,
                "message": (
                    "Lease LC-E2E-001, initiate handover, title Handover SR E2E "
                    "Correction Test, description Formal handover for e2e test, "
                    "start 2025-05-01 9am, FM Manager will inspect"
                ),
            },
        )
        body2 = resp2.json()

    assert body2["ui"].get("field") == "endDate", (
        f"expected bot to ask for endDate next, got: {body2['ui']}"
    )

    # ── Turn 3: answer endDate — active_field should move to "notes" ───────
    with (
        patch(
            "app.agents.graph.nodes.shared.field_extraction_node.FieldExtractionService",
            make_field_extraction_mock(
                {"endDate": {"value": "2025-06-01T09:00:00", "confidence": 1.0}}
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
        resp3 = await app_client.post(
            "/api/chat/service-request",
            json={
                "user_id": _USER_ID,
                "session_id": session_id,
                "message": "21/08/2026 9:00 AM to 01/06/2025 9:00 AM",
            },
        )
        body3 = resp3.json()

    assert body3["ui"].get("field") == "notes", (
        f"expected bot to move on to notes, got: {body3['ui']}"
    )

    # ── Turn 4: THE REGRESSION — bare correction redirect, no inline value ─
    # This must NOT be routed through field_extraction's LLM at all (verified
    # separately in unit tests) — the mock below returning notes-shaped data
    # is a trap: if it gets called, something is wrong with the redirect gate.
    with (
        patch(
            "app.agents.graph.nodes.shared.field_extraction_node.FieldExtractionService",
            make_field_extraction_mock({"notes": {"value": "should not be used", "confidence": 1.0}}),
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
        resp4 = await app_client.post(
            "/api/chat/service-request",
            json={
                "user_id": _USER_ID,
                "session_id": session_id,
                "message": "i want to change inspection end date",
            },
        )
        body4 = resp4.json()

    # The core regression assertions:
    assert body4["ui"].get("field") == "endDate", (
        "correction must re-target endDate, not silently advance to the next "
        f"field — got ui: {body4['ui']}"
    )
    assert body4["ui"].get("verbatim") is True, (
        "correction re-ask must be verbatim (bypass response_generation's LLM "
        "rewrite) — otherwise the LLM sees the stale endDate value still in "
        f"collected_data and re-confirms it instead of asking for a new one. Got: {body4['ui']}"
    )
    assert "notes" not in body4["message"].lower(), (
        f"bot must not have silently advanced past the correction to notes: {body4['message']}"
    )
    assert "21/08/2026" in body4["message"] or "01/06/2025" in body4["message"], (
        f"re-ask should reference the CURRENT endDate value for context: {body4['message']}"
    )

    # ── Turn 5: supply the corrected endDate — flow must resume normally ───
    with (
        patch(
            "app.agents.graph.nodes.shared.field_extraction_node.FieldExtractionService",
            make_field_extraction_mock(
                {"endDate": {"value": "2025-06-15T09:00:00", "confidence": 1.0}}
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
        resp5 = await app_client.post(
            "/api/chat/service-request",
            json={
                "user_id": _USER_ID,
                "session_id": session_id,
                "message": "15/06/2025 9:00 AM",
            },
        )
        body5 = resp5.json()

    assert body5["ui"].get("field") == "notes", (
        "after the corrected endDate is supplied, the flow must resume asking "
        f"for the next truly-missing field (notes) — got: {body5['ui']}"
    )
