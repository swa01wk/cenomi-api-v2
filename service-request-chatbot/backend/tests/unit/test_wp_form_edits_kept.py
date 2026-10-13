"""Unit tests — the tenant's form edits survive on a sent-back Work Permit.

Seen live: with the chat open on a Work Permit sent back for more information,
the tenant edited Location and, on leaving the field, it snapped back to the
old value. The form bridge patched the edit to the chat, but status sync
rehydrated from the platform (whose value predates the edit) and the turn never
merged it, so the reply wrote the saved value back over the form.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from app.agents.graph.help_agent_graph import _route_after_sync
from app.agents.graph.nodes.shared.sr_status_sync_node import sr_status_sync_node
from app.agents.schemas.work_permit_schema import WP_ACTION_REQUIRED
from app.agents.services.platform_api_client import ServiceRequestGetResult

from tests.unit.test_wp_pending_prompt_persists import _service

_SENT_BACK_OPS = [
    {"assigned_role": "FM_MANAGER", "status": "APPROVED"},
    {"assigned_role": "MALL_MANAGER", "status": "ACTION_REQUIRED"},
]
_FM_REVIEW_OPS = [{"assigned_role": "FM_MANAGER", "status": "IN_PROGRESS"}]


def _get(
    *, location: str = "Old", status: str = "ACTION_REQUIRED", ops: list | None = None
) -> ServiceRequestGetResult:
    return ServiceRequestGetResult(
        success=True,
        sr_id="341529",
        status=status,
        service_request_operations=ops if ops is not None else _SENT_BACK_OPS,
        payload={
            "category": "WORK_PERMIT",
            "sub_category": "CONSTRUCTION_HOT",
            "work_location": location,
            "contractor_name": "Kishan Sah",
        },
    )


def _state(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "active_agent": "work_permit_agent",
        "service_category": "WORK_PERMIT",
        "sub_category": "CONSTRUCTION_HOT",
        "workflow_stage": WP_ACTION_REQUIRED,
        "collected_data": {"work_location": "Old", "contractor_name": "Kishan Sah"},
        "backend_refs": {"sr_id": "341529"},
        "user_message": "",
    }
    base.update(overrides)
    return base


async def _sync(state: dict[str, Any], result: ServiceRequestGetResult) -> dict[str, Any]:
    with patch(
        "app.agents.graph.nodes.shared.sr_status_sync_node.get_service_request_api_service",
        return_value=AsyncMock(get_service_request=AsyncMock(return_value=result)),
    ):
        return await sr_status_sync_node(state)


@pytest.mark.asyncio
async def test_edit_wins_over_the_saved_platform_value() -> None:
    out = await _sync(_state(corrected_fields={"work_location": "New"}), _get())
    assert out["collected_data"]["work_location"] == "New"
    assert out["backend_refs"]["form_edits"] == {"work_location": "New"}


@pytest.mark.asyncio
async def test_edit_survives_the_next_turn_and_a_second_edit() -> None:
    out = await _sync(
        _state(
            backend_refs={"sr_id": "341529", "form_edits": {"work_location": "New"}},
            corrected_fields={"contractor_name": "Ali"},
        ),
        _get(),
    )
    assert out["collected_data"]["work_location"] == "New"
    assert out["collected_data"]["contractor_name"] == "Ali"
    assert out["backend_refs"]["form_edits"] == {
        "work_location": "New",
        "contractor_name": "Ali",
    }


@pytest.mark.asyncio
async def test_edit_is_dropped_once_the_platform_has_it() -> None:
    out = await _sync(
        _state(backend_refs={"sr_id": "341529", "form_edits": {"work_location": "New"}}),
        _get(location="New"),
    )
    assert out["collected_data"]["work_location"] == "New"
    assert "form_edits" not in out["backend_refs"]


@pytest.mark.asyncio
async def test_edits_are_cleared_after_resubmission() -> None:
    out = await _sync(
        _state(backend_refs={"sr_id": "341529", "form_edits": {"work_location": "New"}}),
        _get(location="Resubmitted", status="IN_PROCESS", ops=_FM_REVIEW_OPS),
    )
    assert out["workflow_stage"] != WP_ACTION_REQUIRED
    assert out["collected_data"]["work_location"] == "Resubmitted"
    assert "form_edits" not in out["backend_refs"]


@pytest.mark.asyncio
async def test_review_stage_never_applies_form_edits() -> None:
    out = await _sync(
        _state(
            workflow_stage="WP_FM_REVIEW",
            corrected_fields={"work_location": "New"},
        ),
        _get(status="IN_PROCESS", ops=_FM_REVIEW_OPS),
    )
    assert out["collected_data"]["work_location"] == "Old"
    assert "form_edits" not in out["backend_refs"]


@pytest.mark.asyncio
async def test_document_keys_are_not_kept_as_form_edits() -> None:
    out = await _sync(
        _state(corrected_fields={"documents": [{"document_id": 1}], "_meta": 1}),
        _get(),
    )
    assert "form_edits" not in out["backend_refs"]


@pytest.mark.asyncio
async def test_form_edits_survive_save_and_load() -> None:
    service = _service()
    session_id = uuid4()
    await service.save_checkpoint(
        session_id,
        _state(
            workflow_stage=WP_ACTION_REQUIRED,
            backend_refs={"sr_id": "341529", "form_edits": {"work_location": "New"}},
        ),
    )
    loaded = await service.load(session_id)
    assert loaded["backend_refs"]["form_edits"] == {"work_location": "New"}
    assert "_form_edits" not in loaded["collected_data"]

    await service.save_checkpoint(session_id, _state(backend_refs={"sr_id": "341529"}))
    assert "form_edits" not in (await service.load(session_id))["backend_refs"]


def test_silent_form_patch_on_sent_back_sr_skips_the_status_card() -> None:
    state = _state(corrected_fields={"work_location": "New"})
    assert _route_after_sync(state) == "response_generation"


def test_opening_the_sent_back_sr_still_shows_the_status_card() -> None:
    assert _route_after_sync(_state()) == "work_permit_status"
    typed = _state(corrected_fields={"work_location": "New"}, user_message="what's left?")
    assert _route_after_sync(typed) == "work_permit_status"
