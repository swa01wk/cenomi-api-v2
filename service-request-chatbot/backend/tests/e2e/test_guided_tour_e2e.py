"""E2E — the guided-tour choice over the real HTTP stack and graph.

The point of these is the behaviour the unit tests cannot show: that the
choice is asked at the right moment, that answering it does not disturb the
question sequence, and that a value arriving from the *form* — a silent turn
carrying corrected_fields and no message — is accepted as the answer to the
question on screen, without the assistant re-asking or announcing it.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from httpx import AsyncClient

from app.agents.services import guided_tour_service as tour
from tests.e2e.conftest import _CHAT_ENDPOINT, post_turn
from tests.e2e.helpers import (
    make_field_extraction_mock,
    make_lease_mock,
    make_supervisor_mock,
    zara_lease,
)

_USER_ID = "e2e_guided_tour"


@pytest.fixture()
def tour_on(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "guided_tour_choice_enabled", True)


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
    return patch(
        "app.agents.graph.nodes.supervisor_node._call_supervisor_llm",
        make_supervisor_mock(),
    )


def _supervisor_gateway() -> Any:
    return patch(
        "app.agents.graph.nodes.supervisor_node.get_default_gateway",
        return_value=MagicMock(model="gpt-4o-mini"),
    )


async def _post(
    client: AsyncClient,
    *,
    session_id: str,
    message: str = "",
    action: str | None = None,
    corrected_fields: dict[str, Any] | None = None,
) -> dict[str, Any]:
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


async def _resolve_lease(client: AsyncClient) -> tuple[str, dict[str, Any]]:
    with _supervisor(), _supervisor_gateway(), _extraction(
        {"lease_code": "LC-E2E-001"}
    ), _gateway(), _lease():
        body = await post_turn(
            client, "handover for lease LC-E2E-001", user_id=_USER_ID
        )
    return body["session_id"], body


@pytest.mark.asyncio
async def test_choice_is_asked_after_the_lease_and_answered_by_chip(
    app_client: AsyncClient, tour_on: None
) -> None:
    session_id, body = await _resolve_lease(app_client)

    # Asked as soon as there is a form to talk about, before any field.
    assert "guide you through the form" in (body.get("message") or "")
    labels = [
        o.get("label")
        for o in (body.get("ui") or {}).get("suggestions", {}).get("options", [])
    ]
    assert labels == ["Guide me", "Leave the form open"]
    assert body["state"].get("guided_tour") is None

    with _extraction({}), _gateway(), _lease():
        answered = await _post(
            app_client, session_id=session_id, action=tour.ACTION_OPEN
        )

    # Recorded, published to the client, and the flow carries straight on to
    # its first real question — the conversation is unchanged by the choice.
    assert answered["state"]["guided_tour"] == "open"
    assert (answered.get("ui") or {}).get("field") == "already_handovered"


@pytest.mark.asyncio
async def test_a_form_edit_answers_the_question_without_a_reply(
    app_client: AsyncClient, tour_on: None
) -> None:
    """Open mode: typing into the form is answering, silently."""
    session_id, _ = await _resolve_lease(app_client)

    with _extraction({}), _gateway(), _lease():
        await _post(app_client, session_id=session_id, action=tour.ACTION_OPEN)

    # The assistant has asked for the handover type. The user picks it in the
    # form instead — a silent turn with no message at all.
    with _extraction({}), _gateway(), _lease():
        after_edit = await _post(
            app_client,
            session_id=session_id,
            corrected_fields={"already_handovered": "initiate_handover"},
        )

    assert after_edit["state"]["guided_tour"] == "open"
    # Taken as the answer: the next question is a different one.
    next_field = (after_edit.get("ui") or {}).get("field")
    assert next_field != "already_handovered"
    assert next_field is not None
    # And nothing was said about the change itself.
    assert "already_handovered" not in (after_edit.get("message") or "")


@pytest.mark.asyncio
async def test_guided_answer_keeps_todays_behaviour(
    app_client: AsyncClient, tour_on: None
) -> None:
    session_id, _ = await _resolve_lease(app_client)

    with _extraction({}), _gateway(), _lease():
        answered = await _post(
            app_client, session_id=session_id, action=tour.ACTION_GUIDED
        )

    assert answered["state"]["guided_tour"] == "guided"
    assert (answered.get("ui") or {}).get("field") == "already_handovered"


@pytest.mark.asyncio
async def test_flag_off_asks_nothing(app_client: AsyncClient) -> None:
    _session_id, body = await _resolve_lease(app_client)

    assert "guide you through the form" not in (body.get("message") or "")
    assert (body.get("ui") or {}).get("field") == "already_handovered"
    assert body["state"].get("guided_tour") is None


@pytest.mark.asyncio
async def test_form_edits_survive_the_document_and_review_steps(
    app_client: AsyncClient, tour_on: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The live report: pick the handover type, tap an inspection chip, then
    edit the form — and watch the assistant put its own values back.

    Both idle waits after the inspection question — the optional-document card
    and the review card — leave no active_field, which used to send the turn
    past merge_state to response_generation. The edit was dropped there and the
    next apply pushed the older value back over the form.
    """
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "handover_short_flow_enabled", True)

    session_id, _ = await _resolve_lease(app_client)

    with _extraction({}), _gateway(), _lease():
        await _post(app_client, session_id=session_id, action=tour.ACTION_OPEN)

    with _extraction({"already_handovered": "initiate_handover"}), _gateway(), _lease():
        await _post(
            app_client, session_id=session_id, message="initiate handover"
        )

    # The inspection chip answers the whole question in one tap.
    with _extraction(
        {
            "startDate": "2026-09-10",
            "endDate": "2026-09-11",
            "inspection_done_by": "FM_MANAGER",
        }
    ), _gateway(), _lease():
        after_chip = await _post(
            app_client,
            session_id=session_id,
            message="Start 10/09/2026, End 11/09/2026, FM Manager",
        )

    # Now sitting on the optional-document card — an idle wait, no active field.
    assert (after_chip.get("ui") or {}).get("type") == "document_requirement"

    # The user edits the form while that card is up.
    with _extraction({}), _gateway(), _lease():
        after_edit = await _post(
            app_client,
            session_id=session_id,
            corrected_fields={
                "title": "Zara RP-E2E-01 pre-opening handover",
                "description": "Snagging round before opening.",
                "startDate": "2026-09-15",
                "endDate": "2026-09-16",
                "inspection_done_by": "OPERATIONS",
            },
        )

    def _preview(body: dict[str, Any]) -> dict[str, Any]:
        return {
            f["key"]: f["value"] for f in (body.get("draft_preview") or {}).get("fields", [])
        }

    kept = _preview(after_edit)
    assert kept["title"] == "Zara RP-E2E-01 pre-opening handover"
    assert kept["description"] == "Snagging round before opening."
    assert "2026-09-15" in str(kept["startDate"])
    # draft_preview display-formats the enum for the form bridge.
    assert str(kept["inspection_done_by"]).upper() == "OPERATIONS"

    # Continue past the documents: the review card must show what the user
    # typed, not what the assistant had collected.
    with _extraction({}), _gateway(), _lease():
        review = await _post(
            app_client, session_id=session_id, message="continue"
        )

    ui = review.get("ui") or {}
    assert ui.get("type") == "confirmation_card"
    rows = {f["key"]: f["value"] for f in ui["fields"]}
    assert rows["title"] == "Zara RP-E2E-01 pre-opening handover"
    assert rows["description"] == "Snagging round before opening."
    assert "2026-09-15" in str(rows["startDate"])
    assert rows["inspection_done_by"] == "OPERATIONS"
