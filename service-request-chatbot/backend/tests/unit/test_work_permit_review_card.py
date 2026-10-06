"""Unit tests — WP review confirmation card contract (summary layout / create unchanged)."""

from __future__ import annotations

from typing import Any

import pytest

from app.agents.graph.nodes.work_permit.work_permit_confirmation_node import (
    _build_create_confirmation_card,
    work_permit_confirmation_node,
)
from app.agents.schemas.work_permit_schema import WP_DD_REVIEW, WP_MM_OPS_REVIEW
from app.services.response_ui_finalize import finalize_response_ui
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
async def test_review_card_details_list_every_filled_field() -> None:
    state = _hot_state()
    state["collected_data"].update(
        {
            "unit_codes": ["GF001"],
            "phone": "0500000000",
            "work_location": "",
            "lease": "L-1",
            "country_code": "966",
            "tenant_contact": "x",
            "lease_brand_mall": "L / B / M",
        }
    )
    out = await work_permit_confirmation_node(state)
    ui = out["response_ui"]
    details = {d["key"]: d for d in ui["details"]}
    assert list(details) == [
        "mall",
        "brand",
        "unit_codes",
        "work_permit_type",
        "phone",
        "contractor_name",
        "planneddate",
        "workers",
        "measureofsafety",
    ]
    assert details["unit_codes"]["value"] == "GF001"
    assert details["phone"] == {"key": "phone", "label": "Phone", "value": "0500000000"}
    assert details["work_permit_type"]["value"] == "Construction – Hot Work"
    assert details["planneddate"]["value"] == "25/09/2026 09:00 → 25/09/2026 17:00"
    assert details["workers"]["value"] == "Ali"
    assert details["measureofsafety"]["value"] == "Fire extinguisher"
    assert len(ui["details"]) > len(ui["summary"])
    assert details["planneddate"]["summaryKey"] == "schedule"
    assert details["unit_codes"]["summaryKey"] == "unit"
    assert "summaryKey" not in details["contractor_name"]


@pytest.mark.asyncio
async def test_review_message_names_the_latest_action_from_history() -> None:
    state = _hot_state(workflow_stage="WP_FM_REVIEW")
    state["auth_context"] = AuthContext(
        subject_id="fm", tenant_id=None, roles=frozenset({"FM_MANAGER"})
    )
    state["backend_refs"] = {
        "sr_id": "SR-1",
        "sr_operations": [
            {"assigned_role": "DD_ENGINEER", "status": "FINISHED"},
            {"assigned_role": "FM_MANAGER", "status": "IN_PROGRESS"},
        ],
        "sr_history": [
            {"status": "CREATED", "operator_name": "Ali", "user_type": "TENANT"},
            {
                "status": "APPROVED",
                "operator_name": "Sara",
                "operator_role": "DD_ENGINEER",
                "created_at": "2026-09-29T10:15:00Z",
            },
        ],
    }
    state["backend_refs"]["sr_creator"] = {"name": "Ali", "role": "PRIMARY_TENANT_CONTACT"}
    out = await work_permit_confirmation_node(state)
    message = out["response_ui"]["message"]
    assert message.split("\n\n")[-1] == (
        "**Ali** (Tenant) submitted this request, and "
        "**Sara** (DD Engineer) approved it. What would you like to do?"
    )
    assert message.index("pending your **FM review**") < message.index("**Ali**")


@pytest.mark.asyncio
async def test_review_message_uses_creator_when_history_is_empty() -> None:
    state = _hot_state(workflow_stage="WP_DD_REVIEW")
    state["backend_refs"] = {
        "sr_id": "SR-1",
        "sr_operations": [{"assigned_role": "DD_ENGINEER", "status": "IN_PROGRESS"}],
        "sr_creator": {
            "name": "Ali Hassan",
            "role": "PRIMARY_TENANT_CONTACT",
            "created_at": "2026-09-28T08:00:00Z",
        },
    }
    out = await work_permit_confirmation_node(state)
    assert out["response_ui"]["message"].endswith(
        "**Ali Hassan** (Tenant) submitted this request. "
        "What would you like to do?"
    )


@pytest.mark.asyncio
async def test_ops_review_card_details_format_schedule_and_labels() -> None:
    state = _ops_state()
    state["collected_data"]["department"] = "operations"
    out = await work_permit_confirmation_node(state)
    details = {d["key"]: d["value"] for d in out["response_ui"]["details"]}
    assert details["department"] == "Operations"
    assert details["typeofwork"] == "Construction"
    assert details["effective_starting"] == "28/09/2026 → 30/09/2026"
    assert details["firstperiod"] == "03:51 AM – 04:51 AM"
    assert "secondperiod" not in details


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
async def test_on_sr_page_has_no_note_and_no_navigation_chip() -> None:
    out = await work_permit_confirmation_node(
        _ops_state(msp_context={"current_sr_id": "341524"})
    )
    ui = out["response_ui"]
    assert "note" not in ui
    assert "Open full request" not in _chip_labels(ui)
    assert ui["details"]


@pytest.mark.asyncio
async def test_different_sr_page_still_offers_chip() -> None:
    out = await work_permit_confirmation_node(
        _ops_state(msp_context={"current_sr_id": "999"})
    )
    ui = out["response_ui"]
    assert "note" not in ui
    assert "Open full request" in _chip_labels(ui)


def _non_actor_state(**overrides: Any) -> dict[str, Any]:
    state = _hot_state(
        auth_context=AuthContext(
            subject_id="tc", tenant_id=None, roles=frozenset({"COMPANY_ADMIN"})
        ),
        backend_refs={
            "sr_id": "341527",
            "sr_operations": [
                {"assigned_role": "DD_ENGINEER", "status": "IN_PROGRESS"}
            ],
        },
    )
    state.update(overrides)
    return state


_WP_DRAFT_PREVIEW = {
    "type": "sr_preview_card",
    "requestType": "Work Permit Service Request",
    "srId": "341527",
    "status": "SUBMITTED",
    "isSubmitted": True,
    "fields": [{"key": "lease_code", "label": "Lease Code", "value": "t0102106"}],
}


@pytest.mark.asyncio
async def test_non_actor_gets_fixed_message_not_card() -> None:
    out = await work_permit_confirmation_node(_non_actor_state())
    ui = out["response_ui"]
    assert ui["type"] == "message"
    assert ui["verbatim"] is True
    assert "actions" not in ui
    assert ui["message"] == (
        "Work Permit SR **341527** is with **DD Review**. "
        "You don't have an action on this step."
    )
    assert out["response_message"] == ui["message"]
    assert out["response_decorations"] == {}


@pytest.mark.asyncio
async def test_non_actor_on_sr_page_has_no_chip() -> None:
    out = await work_permit_confirmation_node(
        _non_actor_state(msp_context={"current_sr_id": "341527"})
    )
    assert "suggestions" not in out["response_ui"]


@pytest.mark.asyncio
async def test_non_actor_off_sr_page_offers_open_full_request() -> None:
    out = await work_permit_confirmation_node(_non_actor_state())
    assert _chip_labels(out["response_ui"]) == ["Open full request"]


@pytest.mark.asyncio
async def test_non_actor_reply_is_not_swapped_for_details_card() -> None:
    out = await work_permit_confirmation_node(
        _non_actor_state(msp_context={"current_sr_id": "341527"})
    )
    final = finalize_response_ui(
        out["response_ui"],
        out["response_decorations"],
        draft_preview=_WP_DRAFT_PREVIEW,
        response_message=out["response_message"],
    )
    assert final["type"] == "message"
    assert "suggestions" not in final


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
