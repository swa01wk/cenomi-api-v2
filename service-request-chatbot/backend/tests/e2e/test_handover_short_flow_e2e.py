"""E2E — the four-turn CREATE_SR flow (HANDOVER_SHORT_FLOW_ENABLED).

Drives the real HTTP stack, orchestration service and LangGraph; only the LLM,
the lease API and the SR API are stubbed.  The point of the test is the *turn
count*: lease → handover type → inspection details → documents → review, with
title, description, notes and comments generated rather than asked.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from httpx import AsyncClient

from tests.e2e.conftest import post_turn
from tests.e2e.helpers import (
    make_field_extraction_mock,
    make_lease_mock,
    zara_lease,
)

_USER_ID = "e2e_short_flow"


@pytest.fixture()
def short_flow_on(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "handover_short_flow_enabled", True)


def _extraction(fields: dict[str, Any]) -> Any:
    return patch(
        "app.agents.graph.nodes.shared.field_extraction_node.FieldExtractionService",
        make_field_extraction_mock(
            {k: {"value": v, "confidence": 1.0} for k, v in fields.items()}
        ),
    )


def _gateway() -> Any:
    return patch(
        "app.agents.graph.nodes.shared.field_extraction_node.get_default_gateway",
        return_value=MagicMock(model="gpt-4o-mini"),
    )


def _lease() -> Any:
    return patch(
        "app.agents.graph.nodes.shared.lease_lookup_node.get_lease_lookup_service",
        make_lease_mock([zara_lease()]),
    )


def _supervisor() -> Any:
    from tests.e2e.helpers import make_supervisor_mock

    return patch(
        "app.agents.graph.nodes.supervisor_node._call_supervisor_llm",
        make_supervisor_mock(),
    )


def _supervisor_gateway() -> Any:
    return patch(
        "app.agents.graph.nodes.supervisor_node.get_default_gateway",
        return_value=MagicMock(model="gpt-4o-mini"),
    )


def _chip_labels(body: dict[str, Any]) -> list[str]:
    options = (body.get("ui") or {}).get("suggestions", {}).get("options", [])
    return [o.get("label") for o in options]


@pytest.mark.asyncio
async def test_four_turns_from_lease_to_review(
    app_client: AsyncClient, short_flow_on: None
) -> None:
    # ── Turn 1: name the lease ────────────────────────────────────────────
    with _supervisor(), _supervisor_gateway(), _extraction({"lease_code": "LC-E2E-001"}), _gateway(), _lease():
        body1 = await post_turn(
            app_client,
            "I want to raise a handover for lease LC-E2E-001",
            user_id=_USER_ID,
        )
    session_id = body1["session_id"]
    assert body1.get("active_agent") == "rdd_handover_agent"
    # Lease resolved → the first question is the handover type, with its chips.
    assert (body1.get("ui") or {}).get("field") == "already_handovered"
    assert _chip_labels(body1) == ["Already Trading", "Initiate Handover"]

    # ── Turn 2: handover type ─────────────────────────────────────────────
    with _extraction({"already_handovered": "initiate_handover"}), _gateway(), _lease():
        body2 = await post_turn(
            app_client, "initiate handover", session_id=session_id, user_id=_USER_ID
        )

    # One question for the whole inspection group — not three.
    message2 = body2.get("message") or ""
    assert "Inspection details" in message2
    assert (body2.get("ui") or {}).get("field") == "startDate"
    labels2 = _chip_labels(body2)
    assert len(labels2) == 4
    assert labels2[-2:] == ["FM Manager", "Operations"]

    # ── Turn 3: window + inspector in one message ─────────────────────────
    with _extraction(
        {
            "startDate": "2026-09-06",
            "endDate": "2026-09-07",
            "inspection_done_by": "FM_MANAGER",
        }
    ), _gateway(), _lease():
        body3 = await post_turn(
            app_client,
            "6 Sep to 7 Sep, FM Manager",
            session_id=session_id,
            user_id=_USER_ID,
        )

    # Straight to the documents step — no title / description / notes /
    # comments questions in between.
    assert (body3.get("ui") or {}).get("type") == "document_requirement"
    assert _chip_labels(body3) == ["Continue"]

    # ── Turn 4: skip the upload → review card ─────────────────────────────
    with _extraction({}), _gateway(), _lease():
        body4 = await post_turn(
            app_client, "continue", session_id=session_id, user_id=_USER_ID
        )

    ui4 = body4.get("ui") or {}
    assert ui4.get("type") == "confirmation_card"
    rows = {f["key"]: f for f in ui4["fields"]}

    # The fields the flow never asked for are present, generated and editable.
    for key in ("title", "description", "notes", "comments"):
        assert rows[key]["value"], f"{key} should have been generated"
    assert rows["description"]["generated"] is True
    assert rows["description"]["source"] == "template"
    assert rows["description"]["editable"] is True
    assert rows["notes"]["value"].startswith("Inspection scheduled 6 Sep 2026")

    # What the user actually answered carries no generated badge.
    assert "generated" not in rows["startDate"]
    assert rows["inspection_done_by"]["value"] == "FM_MANAGER"

    # Accountability rows are read-only.
    assert rows["checker_role"]["value"] == "FM Manager"
    assert rows["checker_role"]["editable"] is False
