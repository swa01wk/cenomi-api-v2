"""Unit tests — WP entry pending inputs and review action execution gate."""

from __future__ import annotations

import pytest

from app.agents.graph.nodes.work_permit.work_permit_entry_node import (
    work_permit_entry_node,
)
from app.agents.graph.nodes.work_permit.work_permit_payload_builder_node import (
    build_review_work_permit_payload,
    build_wp_status_payload,
)
from app.agents.schemas.work_permit_schema import WP_DD_REVIEW
from app.services.response_ui_finalize import finalize_response_ui
from app.types.chat import AuthContext


def _review_state(**kwargs):
    base = {
        "workflow_stage": WP_DD_REVIEW,
        "auth_context": AuthContext(
            subject_id="dd", tenant_id=None, roles=frozenset({"DD_ENGINEER"})
        ),
        "backend_refs": {
            "sr_id": "SR-9",
            "sr_operations": [
                {"assigned_role": "DD_ENGINEER", "status": "IN_PROGRESS"}
            ],
        },
        "collected_data": {},
        "confirmation_status": "PENDING",
        "user_message": "",
    }
    base.update(kwargs)
    return base


@pytest.mark.asyncio
async def test_reject_asks_for_reason() -> None:
    out = await work_permit_entry_node(
        _review_state(action_override="reject_wp", user_message="")
    )
    assert out["status"] == "WAITING_FOR_USER"
    assert out["backend_refs"]["wp_pending_action"] == "reject"
    assert out["confirmation_status"] == "PENDING"


@pytest.mark.asyncio
async def test_reject_with_reason_confirms() -> None:
    out = await work_permit_entry_node(
        _review_state(
            action_override="reject_wp",
            user_message="",
            collected_data={"comments": "Missing safety docs"},
        )
    )
    assert out["confirmation_status"] == "CONFIRMED"
    assert out["backend_refs"]["wp_action"] == "reject"


@pytest.mark.asyncio
async def test_pending_reason_completes_reject() -> None:
    out = await work_permit_entry_node(
        _review_state(
            action_override=None,
            user_message="Documents are incomplete",
            backend_refs={
                "sr_id": "SR-9",
                "sr_operations": [
                    {"assigned_role": "DD_ENGINEER", "status": "IN_PROGRESS"}
                ],
                "wp_pending_action": "reject",
            },
        )
    )
    assert out["confirmation_status"] == "CONFIRMED"
    assert out["backend_refs"]["wp_action"] == "reject"
    assert "Documents are incomplete" in out["collected_data"]["comments"]


@pytest.mark.asyncio
async def test_unauthorized_denied() -> None:
    out = await work_permit_entry_node(
        _review_state(
            action_override="confirm_approve_wp",
            auth_context=AuthContext(
                subject_id="fm", tenant_id=None, roles=frozenset({"FM_MANAGER"})
            ),
        )
    )
    assert out["status"] == "WAITING_FOR_USER"
    assert "permission" in (out.get("response_message") or "").lower()
    assert out.get("confirmation_status") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["assign_to_wp", "assign_to"])
async def test_assign_to_is_not_available(action: str) -> None:
    out = await work_permit_entry_node(
        _review_state(action_override=action, user_message="FM_MANAGER")
    )
    assert out["status"] == "WAITING_FOR_USER"
    assert "not available" in (out.get("response_message") or "").lower()
    assert out.get("confirmation_status") != "CONFIRMED"
    assert "wp_action" not in (out.get("backend_refs") or {})


def test_payload_action_required() -> None:
    body = build_review_work_permit_payload(
        sub_category="CONSTRUCTION_HOT",
        inner_payload={"mall": "M"},
        action="action_required",
        comment="Need Iqama",
    )
    assert body["status"] == "ACTION_REQUIRED"
    assert body["comment"] == "Need Iqama"


def test_payload_assign_to() -> None:
    body = build_review_work_permit_payload(
        sub_category="CONSTRUCTION_HOT",
        inner_payload={"mall": "M"},
        action="assign_to",
        assign_to="FM_MANAGER",
    )
    assert body["status"] == "REASSIGN_TO"
    assert body["assign_to"] == "FM_MANAGER"


def test_payload_extend_status() -> None:
    body = build_wp_status_payload(
        sr_id="SR-1",
        action="extend",
        comment="ok",
        duration={"extension_duration_in_hours": 2},
    )
    assert body["status"] == "EXTEND"
    assert body["extension_duration_in_hours"] == 2


def test_finalize_keeps_suggestions_on_review_card() -> None:
    ui = {
        "type": "confirmation_card",
        "actions": [{"id": "confirm_approve_wp", "label": "Approve"}],
        "suggestions": {
            "kind": "labeled_options",
            "options": [{"label": "Show safety", "value": "Show safety"}],
        },
    }
    out = finalize_response_ui(
        ui,
        {"suggestions": {"kind": "static_options", "options": ["x"]}},
        draft_preview=None,
        response_message="hi",
    )
    # Card already has prompt suggestions — keep them (do not strip).
    assert out["suggestions"]["options"][0]["label"] == "Show safety"


def test_finalize_create_card_without_decoration_suggestions() -> None:
    ui = {
        "type": "confirmation_card",
        "confirmAction": "confirm_create_wp",
        "cancelAction": "cancel_create_wp",
    }
    out = finalize_response_ui(
        ui,
        {"suggestions": {"kind": "static_options", "options": ["x"]}},
        draft_preview=None,
        response_message="hi",
    )
    assert "suggestions" not in out
