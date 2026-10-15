"""Unit tests — WP entry pending inputs and review action execution gate."""

from __future__ import annotations

import pytest

from app.agents.graph.nodes.work_permit.work_permit_entry_node import (
    work_permit_entry_node,
)
from app.agents.graph.nodes.work_permit.work_permit_payload_builder_node import (
    build_review_work_permit_payload,
    build_wp_status_payload,
    work_permit_payload_builder_node,
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


def _pending_refs(action: str, **extra) -> dict:
    return {
        "sr_id": "SR-9",
        "sr_operations": [{"assigned_role": "DD_ENGINEER", "status": "IN_PROGRESS"}],
        "wp_pending_action": action,
        "wp_pending_stage": WP_DD_REVIEW,
        "wp_pending_sr_id": "SR-9",
        **extra,
    }


@pytest.mark.asyncio
async def test_stale_form_comment_is_not_used() -> None:
    out = await work_permit_entry_node(
        _review_state(
            action_override="reject_wp",
            user_message="",
            collected_data={"comments": "Previous hop comment"},
        )
    )
    assert out["confirmation_status"] == "PENDING"
    assert out["backend_refs"]["wp_pending_action"] == "reject"
    assert "wp_action" not in out["backend_refs"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action", "message"),
    [
        ("reject_wp", "reject_wp"),
        ("ask_more_info_wp", "ask_more_info_wp"),
        ("confirm_approve_wp", "confirm_approve_wp"),
        ("reject_wp", "Reject"),
    ],
)
async def test_button_click_echo_prompts_instead_of_submitting(
    action: str, message: str
) -> None:
    out = await work_permit_entry_node(
        _review_state(action_override=action, user_message=message)
    )
    assert out["status"] == "WAITING_FOR_USER"
    assert out["confirmation_status"] == "PENDING"
    assert "wp_action" not in out["backend_refs"]
    assert "wp_review_comment" not in out["backend_refs"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action", "pending", "label"),
    [
        ("reject_wp", "reject", "Reject"),
        ("ask_more_info_wp", "action_required", "Ask for more info"),
        ("confirm_approve_wp", "approve", "Approve"),
    ],
)
async def test_prompt_carries_comment_for(action: str, pending: str, label: str) -> None:
    out = await work_permit_entry_node(_review_state(action_override=action))
    ui = out["response_ui"]
    assert out["backend_refs"]["wp_pending_action"] == pending
    assert ui["verbatim"] is True
    assert ui["comment_for"]["action_id"] == action
    assert ui["comment_for"]["label"] == label
    assert ui["comment_for"]["required"] is True
    assert "skip_value" not in ui["comment_for"]
    assert [o["label"] for o in ui["suggestions"]["options"]] == ["Cancel"]


@pytest.mark.asyncio
async def test_typed_approve_prompts_for_comment() -> None:
    out = await work_permit_entry_node(_review_state(user_message="approve"))
    assert out["backend_refs"]["wp_pending_action"] == "approve"
    assert "wp_review_comment" not in out["backend_refs"]


@pytest.mark.asyncio
async def test_pending_reason_completes_reject() -> None:
    out = await work_permit_entry_node(
        _review_state(
            user_message="Documents are incomplete",
            backend_refs=_pending_refs("reject"),
        )
    )
    assert out["confirmation_status"] == "CONFIRMED"
    assert out["backend_refs"]["wp_action"] == "reject"
    assert out["backend_refs"]["wp_review_comment"] == "Documents are incomplete"
    assert "wp_pending_action" not in out["backend_refs"]


@pytest.mark.asyncio
@pytest.mark.parametrize("pending", ["approve", "reject", "action_required"])
async def test_required_comment_re_asks_on_empty_reply(pending: str) -> None:
    out = await work_permit_entry_node(
        _review_state(user_message="  ", backend_refs=_pending_refs(pending))
    )
    assert out["confirmation_status"] == "PENDING"
    assert out["backend_refs"]["wp_pending_action"] == pending
    assert "wp_action" not in out["backend_refs"]


@pytest.mark.asyncio
async def test_approve_with_typed_comment() -> None:
    out = await work_permit_entry_node(
        _review_state(
            user_message="All good, proceed",
            backend_refs=_pending_refs("approve", wp_review_comment="old"),
        )
    )
    assert out["confirmation_status"] == "CONFIRMED"
    assert out["backend_refs"]["wp_action"] == "approve"
    assert out["backend_refs"]["wp_review_comment"] == "All good, proceed"


@pytest.mark.asyncio
async def test_approve_skip_phrase_is_kept_as_the_comment() -> None:
    out = await work_permit_entry_node(
        _review_state(user_message="Skip", backend_refs=_pending_refs("approve"))
    )
    assert out["confirmation_status"] == "CONFIRMED"
    assert out["backend_refs"]["wp_review_comment"] == "Skip"


@pytest.mark.asyncio
@pytest.mark.parametrize("pending", ["approve", "reject", "action_required"])
async def test_cancel_chip_backs_out_of_the_prompt(pending: str) -> None:
    out = await work_permit_entry_node(
        _review_state(user_message="cancel", backend_refs=_pending_refs(pending))
    )
    assert out["status"] == "WAITING_FOR_USER"
    assert out["confirmation_status"] == "PENDING"
    assert "wp_pending_action" not in out["backend_refs"]
    assert "wp_action" not in out["backend_refs"]
    assert "still waiting for your review" in out["response_message"]


@pytest.mark.asyncio
async def test_clicking_another_action_mid_prompt_switches() -> None:
    out = await work_permit_entry_node(
        _review_state(
            action_override="reject_wp",
            user_message="reject_wp",
            backend_refs=_pending_refs("approve"),
        )
    )
    assert out["backend_refs"]["wp_pending_action"] == "reject"
    assert out["response_ui"]["comment_for"]["action_id"] == "reject_wp"


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


def test_payload_approve_sends_comment_only_when_set() -> None:
    kwargs = {"sub_category": "CONSTRUCTION_HOT", "inner_payload": {"mall": "M"}}
    assert "comment" not in build_review_work_permit_payload(**kwargs, action="approve")
    body = build_review_work_permit_payload(**kwargs, action="approve", comment="Fine")
    assert body["status"] == "APPROVED"
    assert body["comment"] == "Fine"


@pytest.mark.parametrize("action", ["approve", "action_required"])
@pytest.mark.parametrize(
    ("stored", "expected"),
    [("4821", 4821), (4821, 4821), ("Acme LLC", None), ("", None)],
)
def test_review_payload_company_name_is_int_or_absent(
    action: str, stored: object, expected: int | None
) -> None:
    body = build_review_work_permit_payload(
        sub_category="CONSTRUCTION_COLD",
        inner_payload={
            "mall": "M",
            "company_name": stored,
            "tenant_profile_id": "4821",
            "contract_id": "77",
            "property_id": "9",
        },
        action=action,
        comment="Checked",
    )
    payload = body["payload"]
    assert payload.get("company_name") == expected
    if expected is None:
        assert "company_name" not in payload
    for key in ("tenant_profile_id", "contract_id", "property_id"):
        assert key not in payload
    assert body["comment"] == "Checked"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action", "status"),
    [("reject", "REJECTED"), ("action_required", "ACTION_REQUIRED"), ("approve", "APPROVED")],
)
async def test_payload_builder_reads_wp_review_comment(action: str, status: str) -> None:
    out = await work_permit_payload_builder_node(
        _review_state(
            collected_data={"comments": "stale form comment"},
            backend_refs={
                "sr_id": "SR-9",
                "wp_action": action,
                "wp_review_comment": "Please attach the Iqama",
                "create_payload": {"sub_category": "CONSTRUCTION_HOT", "payload": {"mall": "M"}},
            },
            sub_category="CONSTRUCTION_HOT",
        )
    )
    body = out["backend_refs"]["create_payload"]
    assert body["status"] == status
    assert body["comment"] == "Please attach the Iqama"


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
