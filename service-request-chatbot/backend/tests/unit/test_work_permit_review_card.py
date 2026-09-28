"""Unit tests — WP review confirmation card contract (summary layout / create unchanged)."""

from __future__ import annotations

from typing import Any

import pytest

from app.agents.graph.nodes.work_permit.work_permit_confirmation_node import (
    _build_create_confirmation_card,
    work_permit_confirmation_node,
)
from app.agents.schemas.work_permit_schema import WP_DD_REVIEW, WP_MM_OPS_REVIEW
from app.types.chat import AuthContext


def _hot_state(**overrides: Any) -> dict[str, Any]:
    state: dict[str, Any] = {
        "workflow_stage": WP_DD_REVIEW,
        "auth_context": AuthContext(
            subject_id="dd", tenant_id=None, roles=frozenset({"DD_ENGINEER"})
        ),
        "backend_refs": {
            "sr_id": "SR-1",
            "sr_operations": [
                {"assigned_role": "DD_ENGINEER", "status": "IN_PROGRESS"}
            ],
        },
        "collected_data": {
            "mall": "Mall",
            "brand": "Brand",
            "work_permit_type": "CONSTRUCTION_HOT",
            "contractor_name": "ABC",
            "planneddate": {
                "worktimedate": "25/09/2026",
                "completionworkdate": "25/09/2026",
                "worktimefrom": "09:00",
                "worktimeto": "17:00",
            },
            "workers": [{"full_name": "Ali"}],
            "measureofsafety": {"fire_extinguisher": "yes"},
        },
        "sub_category": "CONSTRUCTION_HOT",
    }
    state.update(overrides)
    return state


def _ops_state(**overrides: Any) -> dict[str, Any]:
    state: dict[str, Any] = {
        "workflow_stage": WP_MM_OPS_REVIEW,
        "auth_context": AuthContext(
            subject_id="56716",
            tenant_id=None,
            roles=frozenset({"MALL_MANAGER"}),
            first_name="Kishan",
        ),
        "backend_refs": {
            "sr_id": "341524",
            "sr_operations": [
                {"assigned_role": "MALL_MANAGER", "status": "IN_PROGRESS"}
            ],
        },
        "collected_data": {
            "mall": "Makkah Mall",
            "brand": "Flying Tiger",
            "unit_codes": "GF009",
            "work_permit_type": "OPERATIONS",
            "typeofwork": {"value": ["construction"], "otheroption": ""},
            "effective_starting": {"datefrom": "28/09/2026", "dateto": "30/09/2026"},
            "firstperiod": {"fromLT": "03:51 AM", "toLT": "04:51 AM"},
        },
        "sub_category": "OPERATIONS",
    }
    state.update(overrides)
    return state


def _chip_labels(ui: dict[str, Any]) -> list[str]:
    return [o["label"] for o in (ui.get("suggestions") or {}).get("options") or []]


@pytest.mark.asyncio
async def test_review_card_has_actions_not_confirm_cancel() -> None:
    out = await work_permit_confirmation_node(_hot_state())
    ui = out["response_ui"]
    assert ui["type"] == "confirmation_card"
    assert ui.get("confirmAction") is None
    assert ui.get("cancelAction") is None
    ids = [a["id"] for a in ui["actions"]]
    assert ids == ["confirm_approve_wp", "ask_more_info_wp", "reject_wp"]
    assert ui["suggestions"]["kind"] == "labeled_options"


@pytest.mark.asyncio
async def test_ops_review_card_has_no_assign_to() -> None:
    out = await work_permit_confirmation_node(_ops_state())
    labels = [a["label"] for a in out["response_ui"]["actions"]]
    assert labels == ["Approve", "Ask for more info", "Reject"]


@pytest.mark.asyncio
async def test_review_card_uses_summary_layout_not_field_dump() -> None:
    out = await work_permit_confirmation_node(_ops_state())
    ui = out["response_ui"]
    assert "fields" not in ui
    assert "links" not in ui
    assert [s["key"] for s in ui["summary"]] == [
        "brand",
        "mall",
        "unit",
        "schedule",
        "request_type",
        "type_of_work",
    ]
    schedule = next(s for s in ui["summary"] if s["key"] == "schedule")
    assert schedule["value"] == "28/09/2026 – 30/09/2026"
    assert schedule["detail"] == "03:51 AM – 04:51 AM"


@pytest.mark.asyncio
async def test_review_message_is_deterministic_and_bolded() -> None:
    out = await work_permit_confirmation_node(_ops_state())
    ui = out["response_ui"]
    # None → response_generation keeps the card wording instead of an LLM rewrite.
    assert out["response_message"] is None
    assert ui["message"].startswith("Hi Kishan!")
    assert "**Flying Tiger**" in ui["message"]
    assert "**Makkah Mall, Unit GF009**" in ui["message"]
    assert "**Operations review**" in ui["message"]


@pytest.mark.asyncio
async def test_off_sr_page_offers_open_full_request_chip() -> None:
    out = await work_permit_confirmation_node(_ops_state())
    ui = out["response_ui"]
    assert "note" not in ui
    chip = next(
        o for o in ui["suggestions"]["options"] if o["label"] == "Open full request"
    )
    assert chip["action"] == "view_service_request"
    assert chip["value"] == "341524"


@pytest.mark.asyncio
async def test_on_sr_page_shows_note_instead_of_navigation_chip() -> None:
    out = await work_permit_confirmation_node(
        _ops_state(msp_context={"current_sr_id": "341524"})
    )
    ui = out["response_ui"]
    assert ui["note"] == "All request details are available in the form on the left."
    assert "Open full request" not in _chip_labels(ui)


@pytest.mark.asyncio
async def test_different_sr_page_still_offers_chip() -> None:
    out = await work_permit_confirmation_node(
        _ops_state(msp_context={"current_sr_id": "999"})
    )
    ui = out["response_ui"]
    assert "note" not in ui
    assert "Open full request" in _chip_labels(ui)


def test_create_card_still_confirm_cancel() -> None:
    card = _build_create_confirmation_card(
        {
            "lease_code": "L1",
            "mall": "M",
            "brand": "B",
            "work_permit_type": "OPERATIONS",
        }
    )
    assert card["confirmAction"] == "confirm_create_wp"
    assert card["cancelAction"] == "cancel_create_wp"
    assert "actions" not in card
    assert "summary" not in card
