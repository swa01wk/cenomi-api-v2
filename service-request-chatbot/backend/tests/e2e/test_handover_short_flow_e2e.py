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

from tests.e2e.conftest import _CHAT_ENDPOINT, post_turn
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
    # The form guides the user to all three, not just the scroll target.
    assert (body2.get("ui") or {}).get("fields") == [
        "startDate",
        "endDate",
        "inspection_done_by",
    ]
    labels2 = _chip_labels(body2)
    # Each chip is a whole answer: a window paired with an inspector.
    assert len(labels2) == 4
    assert all("FM Manager" in x or "Operations" in x for x in labels2)
    assert all("→" in x for x in labels2)

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


async def _post(
    client: AsyncClient,
    *,
    session_id: str,
    message: str = "",
    action: str | None = None,
    corrected_fields: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """POST a turn that can carry an action and inline card edits."""
    payload: dict[str, Any] = {
        "user_id": _USER_ID,
        "message": message,
        "session_id": session_id,
        "attachments": [],
    }
    if action is not None:
        payload["action"] = action
    if corrected_fields is not None:
        payload["corrected_fields"] = corrected_fields
    resp = await client.post(_CHAT_ENDPOINT, json=payload)
    return resp.json()


async def _drive_to_review(client: AsyncClient) -> str:
    """Run the four turns and return the session id sitting on the review card."""
    with _supervisor(), _supervisor_gateway(), _extraction(
        {"lease_code": "LC-E2E-001"}
    ), _gateway(), _lease():
        body = await post_turn(
            client, "handover for lease LC-E2E-001", user_id=_USER_ID
        )
    session_id = body["session_id"]

    with _extraction({"already_handovered": "initiate_handover"}), _gateway(), _lease():
        await post_turn(
            client, "initiate handover", session_id=session_id, user_id=_USER_ID
        )
    with _extraction(
        {
            "startDate": "2026-09-06",
            "endDate": "2026-09-07",
            "inspection_done_by": "FM_MANAGER",
        }
    ), _gateway(), _lease():
        await post_turn(
            client,
            "6 Sep to 7 Sep, FM Manager",
            session_id=session_id,
            user_id=_USER_ID,
        )
    with _extraction({}), _gateway(), _lease():
        await post_turn(client, "continue", session_id=session_id, user_id=_USER_ID)
    return session_id


@pytest.mark.asyncio
async def test_every_review_card_edit_reaches_the_submitted_sr(
    app_client: AsyncClient, short_flow_on: None
) -> None:
    """Editing on the card must win over what the flow collected or generated."""
    from tests.e2e.helpers import make_sr_api_mock

    session_id = await _drive_to_review(app_client)

    edits = {
        "title": "Nike RP-E2E-01 pre-opening handover",
        "description": "Snagging round before opening.",
        "startDate": "2026-09-10",
        "endDate": "2026-09-12",
        "inspection_done_by": "OPERATIONS",
        "notes": "Bring the fitout drawings.",
        "comments": "Coordinate with the store manager.",
    }

    sr_api = make_sr_api_mock("SR-EDIT-001")
    with _extraction({}), _gateway(), _lease(), patch(
        "app.agents.graph.nodes.handover.api_submission_node."
        "get_service_request_api_service",
        sr_api,
    ):
        body = await _post(
            app_client,
            session_id=session_id,
            message="yes",
            action="confirm",
            corrected_fields=edits,
        )

    call = sr_api.return_value.create_service_request.await_args
    envelope = call.kwargs.get("payload") or call.args[0]
    # The platform body nests the SR fields under "payload".
    sr = envelope["payload"]

    assert envelope["title"] == edits["title"]
    assert sr["title"] == edits["title"]
    assert sr["description"] == edits["description"]
    assert sr["inspectionDoneBy"] == "OPERATIONS"
    assert sr["inspection_done_by"] == "OPERATIONS"
    assert sr["notes"] == edits["notes"]
    assert sr["comments"] == edits["comments"]
    assert "2026-09-10" in str(sr["startDate"])
    assert "2026-09-12" in str(sr["endDate"])
    # Submitted, not bounced back into collection.
    assert "SR-EDIT-001" in str(body.get("message") or "") or body.get(
        "state", {}
    ).get("workflow_stage") == "SR_CREATED"


@pytest.mark.asyncio
async def test_confirming_without_edits_submits(
    app_client: AsyncClient, short_flow_on: None
) -> None:
    """The clock is never asked for, so it must not block the submit either."""
    from tests.e2e.helpers import make_sr_api_mock

    session_id = await _drive_to_review(app_client)

    sr_api = make_sr_api_mock("SR-PLAIN-001")
    with _extraction({}), _gateway(), _lease(), patch(
        "app.agents.graph.nodes.handover.api_submission_node."
        "get_service_request_api_service",
        sr_api,
    ):
        await _post(app_client, session_id=session_id, message="yes", action="confirm")

    call = sr_api.return_value.create_service_request.await_args
    assert call is not None, "the SR was never submitted"
    sr = (call.kwargs.get("payload") or call.args[0])["payload"]
    # Defaulted, not blank: 09:00 Riyadh on the start date.
    assert "09:00" in str(sr["startDateLT"]) or "9:00" in str(sr["startDateLT"])
