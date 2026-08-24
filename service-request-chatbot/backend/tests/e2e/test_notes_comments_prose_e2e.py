"""End-to-end regression: prose answers to notes / comments must not re-loop.

Reported live on the handover CREATE_SR flow (lease t0033388): the bot asked
for notes, the user answered, the answer was acknowledged — and the same
question came straight back, over and over, for both notes and comments.
Title, dates and inspector never showed this because their answers are short
values; notes and comments are the fields users write sentences into.

The cause was upstream of the extraction LLM: a message containing a
correction verb but naming no field ("please update the flooring before
handover") was classified as an unspecified "I want to change something"
request. That path returns an empty extraction by design, so the answer was
dropped and the field re-asked — and retyping the note failed the same way.

Only external I/O is mocked (supervisor LLM, extraction LLM, lease API,
validation); the graph, routing, persistence and response generation are real.
"""

from __future__ import annotations

from contextlib import ExitStack
from unittest.mock import MagicMock, patch

import pytest
from httpx import AsyncClient

from tests.e2e.helpers import (
    make_field_extraction_mock,
    make_lease_mock,
    make_supervisor_mock,
    zara_lease,
)

_USER_ID = "e2e_prose_notes_user"

# A note and a comment a Mall Manager would plausibly type. Both carry a
# correction verb; neither names a collectible field.
_NOTE = "please update the flooring before handover"
_COMMENT = "we need to change the shutters as well"


def _patches(fields: dict) -> tuple:
    return (
        patch(
            "app.agents.graph.nodes.shared.field_extraction_node.FieldExtractionService",
            make_field_extraction_mock(fields),
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
    )


@pytest.mark.asyncio
async def test_prose_notes_and_comments_are_stored_without_relooping(
    app_client: AsyncClient,
) -> None:
    with ExitStack() as stack:
        stack.enter_context(
            patch(
                "app.agents.graph.nodes.supervisor_node._call_supervisor_llm",
                make_supervisor_mock(),
            )
        )
        stack.enter_context(
            patch(
                "app.agents.graph.nodes.supervisor_node.get_default_gateway",
                return_value=MagicMock(model="gpt-4o-mini"),
            )
        )
        for patcher in _patches({}):
            stack.enter_context(patcher)
        body1 = (
            await app_client.post(
                "/api/chat/service-request",
                json={"user_id": _USER_ID, "message": "I want to create a handover SR"},
            )
        ).json()
    session_id = body1["session_id"]

    async def turn(message: str, fields: dict, **extra) -> dict:
        with ExitStack() as stack:
            for patcher in _patches(fields):
                stack.enter_context(patcher)
            resp = await app_client.post(
                "/api/chat/service-request",
                json={
                    "user_id": _USER_ID,
                    "session_id": session_id,
                    "message": message,
                    **extra,
                },
            )
        return resp.json()

    # ── Supply everything up to the optional free-text tail ─────────────────
    body2 = await turn(
        "Lease LC-E2E-001, initiate handover, title Handover SR Prose Test, "
        "description Formal handover, start 2025-05-01 9am, end 2025-06-01 9am, "
        "FM Manager will inspect",
        {
            "lease_code": {"value": zara_lease().lease_code, "confidence": 1.0},
            "already_handovered": {"value": "initiate_handover", "confidence": 1.0},
            "title": {"value": "Handover SR Prose Test", "confidence": 1.0},
            "description": {"value": "Formal handover", "confidence": 1.0},
            "startDate": {"value": "2025-05-01T09:00:00", "confidence": 1.0},
            "endDate": {"value": "2025-06-01T09:00:00", "confidence": 1.0},
            "inspectionTime": {"value": "09:00", "confidence": 1.0},
            "inspectionEndTime": {"value": "09:00", "confidence": 1.0},
            "inspection_done_by": {"value": "FM_MANAGER", "confidence": 1.0},
        },
    )
    assert body2["ui"].get("field") == "notes", (
        f"expected the bot to ask for notes next, got: {body2['ui']}"
    )

    # ── The regression: a prose answer must advance the flow ────────────────
    body3 = await turn(_NOTE, {"notes": {"value": _NOTE, "confidence": 1.0}})
    assert body3["ui"].get("field") == "comments", (
        "notes was re-asked instead of being stored — the prose answer was "
        f"read as a correction request. Got: {body3['ui']}"
    )

    body4 = await turn(_COMMENT, {"comments": {"value": _COMMENT, "confidence": 1.0}})
    assert body4["ui"].get("field") != "comments", (
        f"comments was re-asked instead of being stored. Got: {body4['ui']}"
    )

    # ── Both values reached the draft ───────────────────────────────────────
    body5 = await turn("continue", {}, action="continue_optional_upload")
    card_fields = {f["key"]: f["value"] for f in body5["ui"].get("fields", [])}
    assert card_fields.get("notes") == _NOTE, card_fields
    assert card_fields.get("comments") == _COMMENT, card_fields


@pytest.mark.asyncio
async def test_prose_answer_to_a_notes_correction_reask_does_not_reloop(
    app_client: AsyncClient,
) -> None:
    """The "Current Notes: X. What would you like to change it to?" re-ask,
    answered with prose, must accept the new value and move on."""
    with ExitStack() as stack:
        stack.enter_context(
            patch(
                "app.agents.graph.nodes.supervisor_node._call_supervisor_llm",
                make_supervisor_mock(),
            )
        )
        stack.enter_context(
            patch(
                "app.agents.graph.nodes.supervisor_node.get_default_gateway",
                return_value=MagicMock(model="gpt-4o-mini"),
            )
        )
        for patcher in _patches({}):
            stack.enter_context(patcher)
        body1 = (
            await app_client.post(
                "/api/chat/service-request",
                json={
                    "user_id": f"{_USER_ID}_reask",
                    "message": "I want to create a handover SR",
                },
            )
        ).json()
    session_id = body1["session_id"]

    async def turn(message: str, fields: dict, **extra) -> dict:
        with ExitStack() as stack:
            for patcher in _patches(fields):
                stack.enter_context(patcher)
            resp = await app_client.post(
                "/api/chat/service-request",
                json={
                    "user_id": f"{_USER_ID}_reask",
                    "session_id": session_id,
                    "message": message,
                    **extra,
                },
            )
        return resp.json()

    await turn(
        "Lease LC-E2E-001, initiate handover, title Handover SR Prose Test, "
        "description Formal handover, start 2025-05-01 9am, end 2025-06-01 9am, "
        "FM Manager will inspect",
        {
            "lease_code": {"value": zara_lease().lease_code, "confidence": 1.0},
            "already_handovered": {"value": "initiate_handover", "confidence": 1.0},
            "title": {"value": "Handover SR Prose Test", "confidence": 1.0},
            "description": {"value": "Formal handover", "confidence": 1.0},
            "startDate": {"value": "2025-05-01T09:00:00", "confidence": 1.0},
            "endDate": {"value": "2025-06-01T09:00:00", "confidence": 1.0},
            "inspectionTime": {"value": "09:00", "confidence": 1.0},
            "inspectionEndTime": {"value": "09:00", "confidence": 1.0},
            "inspection_done_by": {"value": "FM_MANAGER", "confidence": 1.0},
        },
    )
    await turn("none", {"notes": {"value": "", "confidence": 1.0}})
    await turn("none", {"comments": {"value": "", "confidence": 1.0}})

    reask = await turn("i want to change the notes", {})
    assert reask["ui"].get("field") == "notes", reask["ui"]
    assert "What would you like to change it to?" in reask["message"], reask["message"]

    answer = "please update the notes to say FM team can proceed"
    body = await turn(answer, {"notes": {"value": "FM team can proceed", "confidence": 1.0}})
    assert body["ui"].get("field") != "notes", (
        f"notes re-asked after the correction was supplied. Got: {body['ui']}"
    )
