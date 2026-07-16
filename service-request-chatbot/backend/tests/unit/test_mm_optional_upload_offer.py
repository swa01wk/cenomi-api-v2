"""Unit tests — CREATE_SR optional MM upload offer gate in handover_entry."""

from __future__ import annotations

from typing import Any

import pytest

from app.agents.graph.nodes.handover.handover_entry_node import handover_entry_node


def _state(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "session_id": "sess-1",
        "active_agent": "rdd_agent",
        "workflow_stage": "CREATE_SR",
        "user_message": "",
        "action_override": None,
        "confirmation_status": None,
        "collected_data": {
            "lease_id": 95404,
            "lease_code": "t0105712",
            "mm_optional_upload_status": "awaiting_choice",
        },
        "documents": [],
        "backend_refs": {},
    }
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_accept_optional_upload_action_shows_document_card() -> None:
    result = await handover_entry_node(
        _state(action_override="accept_optional_upload")
    )
    assert result["status"] == "WAITING_FOR_USER"
    assert result["collected_data"]["mm_optional_upload_status"] == "awaiting_upload"
    deco = result["response_decorations"]
    assert deco["document_requirement"]["documents"][0]["key"] == "SR_HANDOVER_OTHER"
    assert deco["suggestions"]["options"][0]["action"] == "continue_optional_upload"


@pytest.mark.asyncio
async def test_skip_optional_upload_action_marks_done() -> None:
    result = await handover_entry_node(
        _state(action_override="skip_optional_upload")
    )
    assert result["collected_data"]["mm_optional_upload_status"] == "done"
    assert result["status"] == "IN_PROGRESS"
    assert result.get("response_decorations") == {}


@pytest.mark.asyncio
async def test_yes_text_accepts_offer() -> None:
    result = await handover_entry_node(_state(user_message="Yes"))
    assert result["collected_data"]["mm_optional_upload_status"] == "awaiting_upload"
    assert "document_requirement" in result["response_decorations"]


@pytest.mark.asyncio
async def test_no_text_skips_offer() -> None:
    result = await handover_entry_node(_state(user_message="No"))
    assert result["collected_data"]["mm_optional_upload_status"] == "done"
    assert result["status"] == "IN_PROGRESS"


@pytest.mark.asyncio
async def test_continue_from_awaiting_upload() -> None:
    result = await handover_entry_node(
        _state(
            action_override="continue_optional_upload",
            collected_data={
                "lease_id": 95404,
                "mm_optional_upload_status": "awaiting_upload",
            },
        )
    )
    assert result["collected_data"]["mm_optional_upload_status"] == "done"
    assert result["status"] == "IN_PROGRESS"


@pytest.mark.asyncio
async def test_awaiting_choice_ambiguous_reoffers() -> None:
    result = await handover_entry_node(_state(user_message="maybe later"))
    assert result["status"] == "WAITING_FOR_USER"
    assert result["collected_data"]["mm_optional_upload_status"] == "awaiting_choice"
    deco = result["response_decorations"]
    assert "document_requirement" not in deco
    actions = {o["action"] for o in deco["suggestions"]["options"]}
    assert actions == {"accept_optional_upload", "skip_optional_upload"}


@pytest.mark.asyncio
async def test_done_status_falls_through() -> None:
    result = await handover_entry_node(
        _state(
            collected_data={
                "lease_id": 95404,
                "mm_optional_upload_status": "done",
            }
        )
    )
    assert result == {}
