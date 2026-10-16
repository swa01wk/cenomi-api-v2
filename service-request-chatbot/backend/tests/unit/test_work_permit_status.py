"""Unit tests — Work Permit status view (no review hop waiting) + preview labels."""

from __future__ import annotations

from typing import Any

import pytest

from app.agents.graph.nodes.handover.preview_node import preview_node
from app.agents.graph.nodes.work_permit.work_permit_status_node import (
    work_permit_status_node,
)
from app.agents.schemas.work_permit_schema import (
    WP_ACTION_REQUIRED,
    WP_APPROVED,
    WP_CREATED,
    WP_FM_REVIEW,
    WP_REJECTED,
)
from app.types.chat import AuthContext


def _state(stage: str | None = WP_ACTION_REQUIRED, **overrides: Any) -> dict[str, Any]:
    state: dict[str, Any] = {
        "active_agent": "work_permit_agent",
        "workflow_stage": stage,
        "auth_context": AuthContext(
            subject_id="mm", tenant_id=None, roles=frozenset({"MALL_MANAGER"})
        ),
        "backend_refs": {
            "sr_id": "341529",
            "platform_sr_status": "ACTION_REQUIRED",
        },
        "collected_data": {
            "mall": "Mall of Dhahran",
            "brand": "Nike",
            "unit_codes": "GF012",
            "work_permit_type": "CONSTRUCTION_HOT",
            "work_type": "Welding",
            "contractor_name": "ABC",
        },
        "sub_category": "CONSTRUCTION_HOT",
    }
    state.update(overrides)
    return state


def _chip_labels(ui: dict[str, Any]) -> list[str]:
    return [o["label"] for o in (ui.get("suggestions") or {}).get("options") or []]


@pytest.mark.asyncio
async def test_sent_back_card_is_wp_summary_with_no_actions() -> None:
    result = await work_permit_status_node(_state())
    ui = result["response_ui"]

    assert ui["type"] == "confirmation_card"
    assert ui["requestType"] == "Work Permit — Sent back to tenant"
    assert "Handover" not in ui["requestType"]
    assert ui["actions"] == []
    assert {i["key"] for i in ui["summary"]} >= {"brand", "mall", "unit"}
    assert any(d["key"] == "contractor_name" for d in ui["details"])
    assert "sent back to the tenant for more information" in ui["message"]
    assert "**341529**" in ui["message"]
    assert "nothing for you to do" in ui["message"]
    assert result["response_message"] is None
    assert result["confirmation_required"] is False
    assert result["confirmation_status"] is None
    assert result["status"] == "WAITING_FOR_USER"


@pytest.mark.asyncio
async def test_sent_back_tenant_variant() -> None:
    tenant = AuthContext(
        subject_id="t", tenant_id=None, roles=frozenset({"TENANT_CSV_UPLOADER"})
    )
    result = await work_permit_status_node(_state(auth_context=tenant))
    message = result["response_ui"]["message"]
    assert "sent back to you" in message
    assert "resubmit" in message


@pytest.mark.parametrize(
    ("stage", "platform_status", "phrase", "header"),
    [
        (WP_APPROVED, "APPROVED", "approved by all reviewers", "Approved"),
        (WP_REJECTED, "REJECTED", "was rejected", "Rejected"),
        (WP_FM_REVIEW, "IN_PROCESS", "currently at **FM Review**", "FM Review"),
        (WP_CREATED, "PENDING", "no step waiting on you", "Submitted"),
        (None, "PENDING", "is in progress (status: **", "Pending"),
    ],
)
@pytest.mark.asyncio
async def test_message_per_status(
    stage: str | None, platform_status: str, phrase: str, header: str
) -> None:
    state = _state(
        stage,
        backend_refs={"sr_id": "341529", "platform_sr_status": platform_status},
    )
    ui = (await work_permit_status_node(state))["response_ui"]
    assert phrase in ui["message"]
    assert ui["requestType"].startswith("Work Permit — ")
    assert header in ui["requestType"]
    assert ui["actions"] == []


@pytest.mark.asyncio
async def test_open_full_request_chip_asks_chat_and_navigation_only_off_page() -> None:
    off_page = await work_permit_status_node(_state())
    assert _chip_labels(off_page["response_ui"]) == ["Open full request", "Go to request page"]
    options = off_page["response_decorations"]["suggestions"]["options"]
    assert options[0] == {"label": "Open full request", "value": "Show the full request"}
    assert options[1]["action"] == "view_service_request"
    assert options[1]["value"] == "341529"

    on_page = await work_permit_status_node(
        _state(msp_context={"current_sr_id": "341529"})
    )
    assert _chip_labels(on_page["response_ui"]) == ["Open full request"]
    assert on_page["response_decorations"]["suggestions"] == on_page["response_ui"]["suggestions"]


@pytest.mark.asyncio
async def test_draft_preview_keeps_pending_create_confirmation() -> None:
    state = _state(
        "CREATE_WORK_PERMIT",
        backend_refs={},
        confirmation_status="PENDING",
    )
    result = await work_permit_status_node(state)
    assert result["response_ui"]["requestType"] == "Work Permit — Draft"
    assert "hasn't been submitted" in result["response_ui"]["message"]
    assert "confirmation_status" not in result
    assert "confirmation_required" not in result


@pytest.mark.parametrize(
    ("agent", "sub_category", "label"),
    [
        ("rdd_handover_agent", "HANDOVER", "Handover Service Request"),
        ("rdd_fitout_start_agent", None, "Fitout Start Confirmation Service Request"),
        (None, "FIT_OUT_CONFIRM_TRADING", "Trading Confirmation Service Request"),
        (None, "HANDOVER", "Handover Service Request"),
    ],
)
@pytest.mark.asyncio
async def test_preview_label_comes_from_workflow_config(
    agent: str | None, sub_category: str | None, label: str
) -> None:
    state = {
        "active_agent": agent,
        "sub_category": sub_category,
        "backend_refs": {"sr_id": "321040", "platform_sr_status": "APPROVED"},
        "collected_data": {"lease_code": "t0105712"},
    }
    result = await preview_node(state)  # type: ignore[arg-type]
    assert result["response_ui"]["requestType"] == label
