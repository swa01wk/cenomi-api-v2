"""Unit tests — a Work Permit review prompt survives to the next turn.

Seen live: the reviewer clicked Ask for more info, was asked "What should I
tell them?", typed the reason, and nothing was sent back. The graph keeps no
state between turns; the draft only restored ``sr_id`` into ``backend_refs``,
so ``wp_pending_action`` was gone and the reason fell through to the LLM.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from app.agents.graph.nodes.work_permit.work_permit_entry_node import (
    work_permit_entry_node,
)
from app.agents.schemas.work_permit_schema import WP_DD_REVIEW, WP_FM_REVIEW
from app.agents.services.conversation_state_service import ConversationStateService
from app.types.chat import AuthContext

_OPS = [{"assigned_role": "DD_ENGINEER", "status": "IN_PROGRESS"}]


class _FakeDraftRepo:
    def __init__(self, draft: SimpleNamespace) -> None:
        self.draft = draft

    async def get_by_session(self, session_id: Any) -> SimpleNamespace:
        return self.draft

    async def update(self, draft_id: Any, updates: dict[str, Any]) -> SimpleNamespace:
        for key, value in updates.items():
            setattr(self.draft, key, value)
        return self.draft


def _service() -> ConversationStateService:
    service = ConversationStateService(session=None)  # type: ignore[arg-type]
    service._draft_repo = _FakeDraftRepo(  # type: ignore[assignment]
        SimpleNamespace(
            id=uuid4(),
            service_category="WORK_PERMIT",
            sub_category="CONSTRUCTION_HOT",
            workflow_stage=WP_DD_REVIEW,
            collected_data={"brand": "Lefties"},
            missing_fields=[],
            documents=[],
            ready_to_submit=False,
            sr_id="341529",
            service_request_status="IN_PROCESS",
        )
    )
    return service


def _turn(state: dict[str, Any], message: str, **extra: Any) -> dict[str, Any]:
    """What the next turn's entry node sees: the loaded draft plus sync output."""
    refs = {**(state.get("backend_refs") or {}), "sr_operations": _OPS}
    return {
        "service_category": "WORK_PERMIT",
        "sub_category": "CONSTRUCTION_HOT",
        "workflow_stage": WP_DD_REVIEW,
        "collected_data": {"brand": "Lefties"},
        "confirmation_status": "PENDING",
        "auth_context": AuthContext(
            subject_id="dd", tenant_id=None, roles=frozenset({"DD_ENGINEER"})
        ),
        **state,
        "backend_refs": refs,
        "user_message": message,
        **extra,
    }


async def _save_and_load(service: ConversationStateService, state: dict[str, Any]) -> dict:
    session_id = uuid4()
    await service.save_checkpoint(session_id, state)
    return await service.load(session_id)


@pytest.mark.asyncio
async def test_the_reason_typed_next_turn_completes_ask_for_more_info() -> None:
    service = _service()
    prompt = await work_permit_entry_node(
        _turn(
            {"backend_refs": {"sr_id": "341529"}},
            "ask_more_info_wp",
            action_override="ask_more_info_wp",
        )
    )
    assert prompt["backend_refs"]["wp_pending_action"] == "action_required"

    loaded = await _save_and_load(service, _turn(prompt, "ask_more_info_wp"))
    assert loaded["backend_refs"]["wp_pending_action"] == "action_required"
    assert loaded["backend_refs"]["wp_pending_stage"] == WP_DD_REVIEW
    assert loaded["backend_refs"]["wp_pending_sr_id"] == "341529"
    assert "_wp_pending" not in loaded["collected_data"]

    out = await work_permit_entry_node(_turn(loaded, "Add more details on the equipment used"))
    assert out["confirmation_status"] == "CONFIRMED"
    assert out["backend_refs"]["wp_action"] == "action_required"
    assert out["backend_refs"]["wp_review_comment"] == "Add more details on the equipment used"
    assert "wp_pending_action" not in out["backend_refs"]


@pytest.mark.asyncio
async def test_nothing_is_kept_once_the_prompt_is_answered() -> None:
    service = _service()
    session_id = uuid4()
    await service.save_checkpoint(
        session_id,
        _turn(
            {
                "backend_refs": {
                    "sr_id": "341529",
                    "wp_pending_action": "reject",
                    "wp_pending_stage": WP_DD_REVIEW,
                    "wp_pending_sr_id": "341529",
                }
            },
            "",
        ),
    )
    await service.save_checkpoint(session_id, _turn({"backend_refs": {"sr_id": "341529"}}, ""))
    loaded = await service.load(session_id)
    assert "wp_pending_action" not in loaded["backend_refs"]
    assert "_wp_pending" not in service._draft_repo.draft.collected_data  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_extend_duration_is_kept_while_waiting_for_the_comment() -> None:
    loaded = await _save_and_load(
        _service(),
        _turn(
            {
                "backend_refs": {
                    "sr_id": "341529",
                    "wp_pending_action": "extend",
                    "wp_pending_stage": WP_DD_REVIEW,
                    "wp_pending_sr_id": "341529",
                    "wp_extension_duration": {"extension_duration_in_hours": 2},
                }
            },
            "",
        ),
    )
    assert loaded["backend_refs"]["wp_extension_duration"] == {"extension_duration_in_hours": 2}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("pending_stage", "pending_sr_id"),
    [(WP_FM_REVIEW, "341529"), (WP_DD_REVIEW, "999999")],
    ids=["other stage", "other SR"],
)
async def test_a_prompt_from_another_hop_or_sr_is_dropped(
    pending_stage: str, pending_sr_id: str
) -> None:
    out = await work_permit_entry_node(
        _turn(
            {
                "backend_refs": {
                    "sr_id": "341529",
                    "wp_pending_action": "reject",
                    "wp_pending_stage": pending_stage,
                    "wp_pending_sr_id": pending_sr_id,
                }
            },
            "Looks fine to me",
        )
    )
    refs = out["backend_refs"]
    assert "wp_pending_action" not in refs
    assert "wp_pending_stage" not in refs
    assert "wp_pending_sr_id" not in refs
    assert "wp_action" not in refs
    assert "wp_review_comment" not in refs
