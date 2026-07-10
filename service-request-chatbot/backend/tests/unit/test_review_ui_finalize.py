"""Unit tests for review_decorations_service and response_ui_finalize."""

from __future__ import annotations

from app.agents.services.review_decorations_service import (
    build_inbox_decorations,
    build_review_decorations,
    derive_user_role,
)
from app.services.response_ui_finalize import finalize_response_ui
from app.types.chat import AuthContext


def test_derive_user_role_from_role_name() -> None:
    auth = AuthContext(subject_id="u1", roles=frozenset({"FM_MANAGER"}))
    assert derive_user_role(auth) == "FM_MANAGER"


def test_build_inbox_decorations_for_reviewer() -> None:
    auth = AuthContext(subject_id="u1", roles=frozenset({"FM_MANAGER"}))
    deco = build_inbox_decorations(auth)
    assert deco["suggestions"]["kind"] == "resource_picker"
    assert deco["suggestions"]["resource"] == "service_requests"


def test_build_inbox_decorations_skips_non_reviewer() -> None:
    auth = AuthContext(subject_id="u1", roles=frozenset({"MALL_MANAGER"}))
    assert build_inbox_decorations(auth) == {}


def test_build_review_decorations_fm_stage() -> None:
    state = {
        "backend_refs": {"sr_id": "12847", "user_role": "FM_MANAGER"},
        "workflow_stage": "FM_REVIEW",
        "session_id": "sess-1",
        "documents": [],
    }
    deco = build_review_decorations(state)
    assert deco.get("promote_preview") is True
    assert deco["suggestions"]["kind"] == "labeled_options"
    assert len(deco["suggestions"]["options"]) == 2
    assert deco.get("document_requirement") is not None


def test_build_review_decorations_skips_when_confirmation_pending() -> None:
    state = {
        "backend_refs": {"sr_id": "12847", "user_role": "FM_MANAGER"},
        "workflow_stage": "FM_REVIEW",
        "confirmation_status": "PENDING",
        "documents": [],
    }
    deco = build_review_decorations(state)
    assert "suggestions" not in deco
    assert deco.get("promote_preview") is True


def test_finalize_response_ui_merges_suggestions() -> None:
    ui = finalize_response_ui(
        {"type": "message"},
        {"suggestions": {"kind": "labeled_options", "options": []}},
        draft_preview=None,
        response_message="Hello",
    )
    assert ui["suggestions"]["kind"] == "labeled_options"
    assert ui["message"] == "Hello"


def test_finalize_response_ui_promotes_preview() -> None:
    draft = {"type": "sr_preview_card", "srId": "99", "fields": []}
    ui = finalize_response_ui(
        {"type": "message"},
        {"promote_preview": True},
        draft_preview=draft,
        response_message="SR open",
    )
    assert ui["type"] == "sr_preview_card"
    assert ui["srId"] == "99"


def test_finalize_merges_lease_picker_on_text_question() -> None:
    ui = finalize_response_ui(
        {"type": "text_question", "field": "lease_code", "message": "Lease?"},
        {"suggestions": {"kind": "resource_picker", "resource": "leases", "filters": {}}},
        draft_preview=None,
        response_message="Lease?",
    )
    assert ui["suggestions"]["resource"] == "leases"
    assert ui["type"] == "text_question"


def test_finalize_skips_suggestions_on_confirmation_card() -> None:
    ui = finalize_response_ui(
        {"type": "confirmation_card", "fields": []},
        {"suggestions": {"kind": "labeled_options", "options": [{"label": "X", "action": "y"}]}},
        draft_preview=None,
        response_message="Confirm?",
    )
    assert "suggestions" not in ui
