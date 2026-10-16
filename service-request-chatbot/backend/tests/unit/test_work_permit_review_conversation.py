"""Unit tests — WP review conversation: shared action context, fallback wording,
follow-up chips, and readable rows for the response model."""

from __future__ import annotations

from typing import Any

import pytest

from app.agents.graph.nodes.shared.response_generation_node import _wp_readable_rows
from app.agents.graph.nodes.work_permit.work_permit_confirmation_node import (
    work_permit_confirmation_node,
)
from app.agents.graph.nodes.work_permit.work_permit_entry_node import (
    _legal_action_ids,
    work_permit_entry_node,
)
from app.agents.prompts.response_generation_prompt import build_response_generation_context
from app.agents.schemas.work_permit_schema import WP_FM_REVIEW


def _fm_review_state(**overrides: Any) -> dict[str, Any]:
    state: dict[str, Any] = {
        "active_agent": "work_permit_agent",
        "workflow_stage": WP_FM_REVIEW,
        "confirmation_status": "PENDING",
        "confirmation_required": True,
        "backend_refs": {
            "sr_id": "342219",
            "sr_operations": [{"assigned_role": "FM_MANAGER", "status": "IN_PROGRESS"}],
            "sr_creator": {"name": "Ali Hassan", "role": "Tenant"},
        },
        "collected_data": {
            "company": "Flying Tiger Co",
            "company_lease_code": "t0102106",
            "company_name": "48213",
            "lease_code": "t0102106",
            "mall": "Mall of Dhahran",
            "work_permit_type": "MAINTENANCE_ROOF_ACCESS",
            "sub_category": "MAINTENANCE_ROOF_ACCESS",
            "planneddate": {
                "worktimedate": "10/10/2026",
                "completionworkdate": "10/10/2026",
                "worktimefromLT": "03:28 PM",
                "worktimetoLT": "03:28 PM",
                "worktimefrom": "2026-10-10T12:28:00Z",
            },
            "measureofsafety": {"fire_extinguisher": "Yes - نعم"},
        },
        "sub_category": "MAINTENANCE_ROOF_ACCESS",
        "user_message": "hmm let me think",
    }
    state.update(overrides)
    return state


class TestSharedReviewContext:
    @pytest.mark.asyncio
    async def test_accepted_actions_match_card_without_sub_category_in_state(self) -> None:
        state = _fm_review_state(sub_category=None)
        state["backend_refs"]["create_payload"] = {"sub_category": "MAINTENANCE_ROOF_ACCESS"}
        card = (await work_permit_confirmation_node(state))["response_ui"]
        assert _legal_action_ids(state, WP_FM_REVIEW) == {a["id"] for a in card["actions"]}

    @pytest.mark.asyncio
    async def test_card_actions_unchanged(self) -> None:
        card = (await work_permit_confirmation_node(_fm_review_state()))["response_ui"]
        assert [a["label"] for a in card["actions"]] == ["Approve", "Ask for more info", "Reject"]


class TestReviewFallback:
    @pytest.mark.asyncio
    async def test_unmatched_message_lists_only_the_reviewers_actions(self) -> None:
        out = await work_permit_entry_node(_fm_review_state())
        text = out["response_message"]
        assert text == (
            "You can **Approve**, **Ask for more info**, or **Reject** this Work Permit "
            "using the buttons below, or ask me anything about the request."
        )
        for phrase in ("confirm and submit", "reply with 'yes'", "assign", "change"):
            assert phrase not in text.casefold()
        assert out["response_ui"] == {"type": "message", "message": text, "verbatim": True}
        assert "confirmation_status" not in out

    @pytest.mark.asyncio
    async def test_extension_bar_actions_are_named_instead(self) -> None:
        state = _fm_review_state()
        state["backend_refs"]["work_permit_extension"] = {"pending": True}
        out = await work_permit_entry_node(state)
        assert out["response_message"].startswith("You can **Extend**, or **Cancel** this")

    @pytest.mark.asyncio
    async def test_reply_keeps_the_review_chips(self) -> None:
        out = await work_permit_entry_node(_fm_review_state())
        labels = [o["label"] for o in out["response_decorations"]["suggestions"]["options"]]
        assert "Who raised this?" in labels
        assert "Open full request" in labels

    @pytest.mark.asyncio
    async def test_question_is_answered_with_history_facts(self) -> None:
        out = await work_permit_entry_node(
            _fm_review_state(user_message="Who raised this work permit?")
        )
        hint = out["response_message"]
        assert "Ali Hassan" in hint
        assert "**Approve**" in hint
        assert "response_ui" not in out
        assert out["response_decorations"]["suggestions"]["options"]

    @pytest.mark.asyncio
    async def test_create_path_keeps_its_text(self) -> None:
        out = await work_permit_entry_node(
            {
                "active_agent": "work_permit_agent",
                "workflow_stage": "CREATE_WORK_PERMIT",
                "confirmation_status": "PENDING",
                "backend_refs": {},
                "collected_data": {},
                "user_message": "hmm let me think",
            }
        )
        assert out["response_message"] == (
            "Please reply with 'yes' to confirm and submit, "
            "or tell me what you'd like to change."
        )


class TestReadableRowsForTheModel:
    def test_submitted_request_gets_rows_and_request_type(self) -> None:
        rows, request_type = _wp_readable_rows(_fm_review_state())
        assert request_type == "Work Permit — Maintenance – Roof Access"
        assert rows is not None

    def test_draft_and_other_agents_keep_raw_path(self) -> None:
        draft = _fm_review_state(backend_refs={}, workflow_stage="CREATE_WORK_PERMIT")
        assert _wp_readable_rows(draft) == (None, None)
        assert _wp_readable_rows(_fm_review_state(active_agent="handover_agent")) == (None, None)

    def test_context_prints_readable_rows_never_raw_codes(self) -> None:
        state = _fm_review_state()
        rows, request_type = _wp_readable_rows(state)
        context = build_response_generation_context(
            user_message="show full details",
            response_intent="Answer the question.",
            workflow_stage=WP_FM_REVIEW,
            intent=None,
            collected_data=state["collected_data"],
            missing_fields=[],
            validation_errors=[],
            confirmation_status="PENDING",
            response_ui_type=None,
            conversation_history=[],
            readable_rows=rows,
            request_type=request_type,
        )
        assert "Request type: Work Permit — Maintenance – Roof Access" in context
        assert "- Company: Flying Tiger Co" in context
        assert "- Lease: t0102106" in context
        assert "- Work dates: 10/10/2026" in context
        assert "- Work time: 03:28 PM" in context
        for raw in ("MAINTENANCE_ROOF_ACCESS", "48213", "T12:28", "worktimefromLT", "نعم"):
            assert raw not in context

    def test_raw_path_hides_numeric_company_name(self) -> None:
        context = build_response_generation_context(
            user_message="hi",
            response_intent="Continue.",
            workflow_stage=None,
            intent=None,
            collected_data={"company_name": "48213", "company": "Flying Tiger Co"},
            missing_fields=[],
            validation_errors=[],
            confirmation_status=None,
            response_ui_type=None,
            conversation_history=[],
        )
        assert "48213" not in context
        assert "- company: Flying Tiger Co" in context
