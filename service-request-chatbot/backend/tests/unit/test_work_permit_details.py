"""Unit tests — Work Permit details in the chat ("Open full request", section chips)."""

from __future__ import annotations

import copy
from typing import Any

import pytest

from app.agents.graph.help_agent_graph import _route_after_load, _route_after_sync
from app.agents.graph.nodes.work_permit.work_permit_details_node import (
    work_permit_details_node,
)
from app.agents.schemas.work_permit_schema import WP_DD_REVIEW
from app.agents.services.work_permit_review_summary import (
    FULL_REQUEST_CHIP_VALUE,
    build_wp_review_prompt_suggestions,
    wp_details_section,
)
from app.types.chat import AuthContext


def _review_state(**overrides: Any) -> dict[str, Any]:
    state: dict[str, Any] = {
        "active_agent": "work_permit_agent",
        "workflow_stage": WP_DD_REVIEW,
        "confirmation_required": True,
        "confirmation_status": "PENDING",
        "auth_context": AuthContext(
            subject_id="dd", tenant_id=None, roles=frozenset({"DD_ENGINEER"})
        ),
        "backend_refs": {
            "sr_id": "SR-1",
            "sr_operations": [{"assigned_role": "DD_ENGINEER", "status": "IN_PROGRESS"}],
        },
        "collected_data": {
            "lease_code": "t0102106",
            "mall": "Mall of Dhahran",
            "brand": "Brand",
            "work_permit_type": "CONSTRUCTION_HOT",
            "contractor_name": "ABC",
            "planneddate": {
                "worktimedate": "25/09/2026",
                "completionworkdate": "26/09/2026",
                "worktimefrom": "09:00",
                "worktimeto": "17:00",
            },
            "workers": [{"full_name": "Ali"}],
            "measureofsafety": {"fire_extinguisher": "Yes - نعم", "fire_blanket": "No - لا"},
        },
        "documents": [
            {"document_id": "doc-1", "document_type_id": "t1", "file_name": "permit.pdf"}
        ],
        "sub_category": "CONSTRUCTION_HOT",
        "user_message": FULL_REQUEST_CHIP_VALUE,
    }
    state.update(overrides)
    return state


class TestWpDetailsSection:
    @pytest.mark.parametrize(
        ("message", "section"),
        [
            (FULL_REQUEST_CHIP_VALUE, "all"),
            ("open full request", "all"),
            ("full details", "all"),
            ("Show the scedule", "schedule"),
            ("schedule", "schedule"),
            ("what is the work time?", "schedule"),
            ("safety measures", "safety"),
            ("measures of safety", "safety"),
            ("Show me the work details for this permit.", "work"),
        ],
    )
    def test_detects_section(self, message: str, section: str) -> None:
        assert wp_details_section(message) == section

    @pytest.mark.parametrize(
        "message",
        ["approve", "Reject", "yes", "No", "Not applicable", "Who raised this work permit?",
         "Approve and show schedule", "25/09/2026", ""],
    )
    def test_ignores_answers_and_review_decisions(self, message: str) -> None:
        assert wp_details_section(message) is None

    def test_every_review_prompt_chip_is_a_details_request_except_who_raised(self) -> None:
        options = build_wp_review_prompt_suggestions(
            {"measureofsafety": {"x": "yes"}, "planneddate": {"worktimedate": "1"}}
        )["options"]
        sections = {o["label"]: wp_details_section(o["value"]) for o in options}
        assert sections == {
            "Show safety details": "safety",
            "Who raised this?": None,
            "Show work details": "work",
            "Show the schedule": "schedule",
        }


class TestWorkPermitDetailsNode:
    @pytest.mark.asyncio
    async def test_full_request_is_a_readable_details_card(self) -> None:
        out = await work_permit_details_node(_review_state())
        ui = out["response_ui"]
        assert ui["type"] == "details_card"
        assert ui["verbatim"] is True
        assert ui["title"] == "Work Permit SR SR-1 — Construction – Hot Work"
        assert out["response_message"] == "Here are the full details of Work Permit SR **SR-1**."
        values = {f["label"]: f["value"] for f in ui["fields"]}
        assert values["Lease"] == "t0102106"
        assert values["Work dates"] == "25/09/2026 – 26/09/2026"
        assert "نعم" not in str(values)
        assert ui["files"] == [
            {"label": "permit.pdf", "fileName": "permit.pdf", "documentId": "doc-1"}
        ]
        assert [section["id"] for section in ui["sections"]] == [
            "request",
            "work",
            "schedule",
            "safety",
            "workers",
        ]
        safety = next(section for section in ui["sections"] if section["id"] == "safety")
        assert safety["icon"] == "shield"
        assert ui["summary"]
        assert {field["label"] for field in ui["fields"]} == {
            field["label"]
            for section in ui["sections"]
            for field in section["fields"]
        }

    @pytest.mark.asyncio
    async def test_cold_request_shows_title_description_and_worker_iqama(self) -> None:
        state = _review_state(sub_category="CONSTRUCTION_COLD", documents=[])
        state["collected_data"].update(
            {
                "work_permit_type": "CONSTRUCTION_COLD",
                "title": "Shop fit-out",
                "description": "Install shelving",
                "workers": [
                    {"full_name": "Kishan", "phone": "38485", "document_id": "iqama-1"}
                ],
            }
        )
        ui = (await work_permit_details_node(state))["response_ui"]
        values = {f["label"]: f["value"] for f in ui["fields"]}
        assert values["Title"] == "Shop fit-out"
        assert values["Description"] == "Install shelving"
        assert ui["files"] == [
            {"label": "Iqama — Kishan", "fileName": "Iqama — Kishan", "documentId": "iqama-1"}
        ]

    @pytest.mark.asyncio
    async def test_is_read_only_and_keeps_the_pending_review(self) -> None:
        state = _review_state()
        before = copy.deepcopy(state)
        out = await work_permit_details_node(state)
        assert state == before
        for key in (
            "confirmation_status",
            "confirmation_required",
            "workflow_stage",
            "collected_data",
            "backend_refs",
            "active_agent",
        ):
            assert key not in out

    @pytest.mark.asyncio
    async def test_actor_gets_review_chips_without_the_chip_just_used(self) -> None:
        out = await work_permit_details_node(_review_state())
        labels = [o["label"] for o in out["response_ui"]["suggestions"]["options"]]
        assert "Open full request" not in labels
        assert "Show the schedule" in labels
        assert out["response_decorations"]["suggestions"] == out["response_ui"]["suggestions"]

    @pytest.mark.asyncio
    async def test_schedule_section_only_has_schedule_rows(self) -> None:
        out = await work_permit_details_node(_review_state(user_message="Show the scedule"))
        ui = out["response_ui"]
        assert ui["title"] == "Planned work schedule"
        assert {f["label"] for f in ui["fields"]} == {"Work dates", "Work time"}
        assert "files" not in ui
        assert "sections" not in ui
        assert "summary" not in ui
        assert "Open full request" in [o["label"] for o in ui["suggestions"]["options"]]

    @pytest.mark.asyncio
    async def test_safety_section_shows_yes_and_no_plainly(self) -> None:
        out = await work_permit_details_node(
            _review_state(user_message="Show me the safety measures for this permit.")
        )
        values = [f["value"] for f in out["response_ui"]["fields"]]
        assert sorted(values) == ["No", "Yes"]

    @pytest.mark.asyncio
    async def test_empty_section_says_so(self) -> None:
        state = _review_state(user_message="show safety")
        state["collected_data"].pop("measureofsafety")
        out = await work_permit_details_node(state)
        assert out["response_ui"]["fields"] == []
        assert out["response_message"] == (
            "The measure of safety hasn't been provided for Work Permit SR **SR-1**."
        )

    @pytest.mark.asyncio
    async def test_approved_request_offers_navigation_only(self) -> None:
        state = _review_state(workflow_stage="WP_APPROVED", confirmation_status=None)
        out = await work_permit_details_node(state)
        labels = [o["label"] for o in out["response_ui"]["suggestions"]["options"]]
        assert labels == ["Go to request page"]

    @pytest.mark.asyncio
    async def test_draft_title(self) -> None:
        state = _review_state(workflow_stage="CREATE_WORK_PERMIT", backend_refs={})
        out = await work_permit_details_node(state)
        assert out["response_ui"]["title"] == "Work Permit draft — Construction – Hot Work"
        assert out["response_message"] == (
            "Here are the full details of this Work Permit draft."
        )


class TestDetailsRouting:
    def test_terminal_request_skips_sync(self) -> None:
        state = _review_state(workflow_stage="WP_APPROVED")
        assert _route_after_load(state) == "work_permit_details"

    def test_open_review_syncs_first_then_shows_details(self) -> None:
        state = _review_state(user_message="Show me the planned work schedule.")
        assert _route_after_load(state) == "sr_status_sync"
        assert _route_after_sync(state) == "work_permit_details"

    def test_draft_routes_full_request_only(self) -> None:
        draft = _review_state(workflow_stage="CREATE_WORK_PERMIT", backend_refs={})
        assert _route_after_load(draft) == "work_permit_details"
        draft["user_message"] = "schedule"
        assert _route_after_load(draft) != "work_permit_details"

    def test_pending_review_comment_is_not_a_details_request(self) -> None:
        state = _review_state()
        state["backend_refs"]["wp_pending_action"] = "reject_wp"
        assert _route_after_load(state) == "sr_status_sync"
        assert _route_after_sync(state) != "work_permit_details"

    def test_free_text_field_is_not_a_details_request(self) -> None:
        state = _review_state(active_field="description", backend_refs={})
        assert _route_after_load(state) != "work_permit_details"

    def test_other_agents_are_untouched(self) -> None:
        state = _review_state(active_agent="handover_agent", workflow_stage="SR_COMPLETED")
        assert _route_after_load(state) != "work_permit_details"

    def test_review_decision_is_not_a_details_request(self) -> None:
        state = _review_state(user_message="approve")
        assert _route_after_sync(state) != "work_permit_details"

    def test_sync_error_falls_back(self) -> None:
        state = _review_state()
        state["backend_refs"].update({"sync_attempted": True, "sync_error": "boom"})
        assert _route_after_sync(state) != "work_permit_details"
