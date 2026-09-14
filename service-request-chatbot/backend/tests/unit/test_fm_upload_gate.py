"""FM review waits for the reviewer to finish uploading.

Reported from FM testing: the reviewer uploaded the first document and the
flow immediately showed the confirmation card, while they were still choosing
the Site Survey. The upload card was gone and nothing said "I have more to
attach".

FM counts as ready with *any one* of the three types, so the first upload
satisfied it and the flow moved on. It now holds on the upload card — which
carries a Continue chip — until the reviewer says they are done, exactly as
the MM pre-submission gate does before the review card.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.agents.graph.nodes.handover import confirmation_node as C
from app.agents.graph.nodes.handover.fm_review_entry_node import fm_review_entry_node
from app.agents.services.review_decorations_service import (
    FM_UPLOAD_GATE_DONE,
    FM_UPLOAD_GATE_KEY,
)

_CHECKLIST = [
    {"document_type_id": "SR_HANDOVER_CHECKLIST", "document_id": "d1", "file_name": "a.pdf"}
]


def _state(documents: list[dict[str, Any]], gate: str | None = None) -> dict[str, Any]:
    collected: dict[str, Any] = {
        "lease_code": "t0110565",
        "unit_readiness_date": "2026-09-20",
        "expected_handover_date": "2026-09-25",
        "comments": "Looks good.",
    }
    if gate:
        collected[FM_UPLOAD_GATE_KEY] = gate
    return {
        "workflow_stage": "FM_REVIEW",
        "active_agent": "rdd_handover_agent",
        "sub_category": "HANDOVER",
        "collected_data": collected,
        "documents": documents,
        "validation_errors": [],
        "backend_refs": {
            "sr_id": "341669",
            "user_role": "FM_MANAGER",
            "documents": documents,
        },
        "session_id": "s1",
    }


async def _turn(documents: list[dict[str, Any]], gate: str | None = None) -> tuple[str, list[str]]:
    result = await C.confirmation_node(_state(documents, gate))  # type: ignore[arg-type]
    ui_type = str((result.get("response_ui") or {}).get("type") or "none")
    suggestions = (result.get("response_decorations") or {}).get("suggestions") or {}
    return ui_type, [o.get("action") for o in suggestions.get("options", [])]


class TestTheCardStaysOpen:
    @pytest.mark.asyncio
    async def test_the_first_document_does_not_advance_the_flow(self) -> None:
        ui_type, actions = await _turn(_CHECKLIST)
        assert ui_type != "confirmation_card"
        assert "continue_fm_upload" in actions

    @pytest.mark.asyncio
    async def test_with_no_documents_it_still_offers_continue(self) -> None:
        """Attaching nothing is a valid FM review, so Continue is the way on."""
        ui_type, actions = await _turn([])
        assert ui_type != "confirmation_card"
        assert "continue_fm_upload" in actions

    @pytest.mark.asyncio
    async def test_continuing_reaches_the_confirmation_card(self) -> None:
        ui_type, _ = await _turn(_CHECKLIST, FM_UPLOAD_GATE_DONE)
        assert ui_type == "confirmation_card"


class TestTheContinueAction:
    @pytest.mark.asyncio
    async def test_it_marks_the_gate_done(self) -> None:
        state = _state(_CHECKLIST)
        state["action_override"] = "continue_fm_upload"
        result = await fm_review_entry_node(state)  # type: ignore[arg-type]
        assert result["collected_data"][FM_UPLOAD_GATE_KEY] == FM_UPLOAD_GATE_DONE

    @pytest.mark.asyncio
    async def test_it_keeps_what_was_already_collected(self) -> None:
        state = _state(_CHECKLIST)
        state["action_override"] = "continue_fm_upload"
        result = await fm_review_entry_node(state)  # type: ignore[arg-type]
        assert result["collected_data"]["unit_readiness_date"] == "2026-09-20"
