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
    """Incomplete FM review → only Save Progress (not Approve)."""
    state = {
        "backend_refs": {"sr_id": "12847", "user_role": "FM_MANAGER"},
        "workflow_stage": "FM_REVIEW",
        "session_id": "sess-1",
        "documents": [],
        "collected_data": {},
    }
    deco = build_review_decorations(state)
    assert deco.get("promote_preview") is True
    assert deco["suggestions"]["kind"] == "labeled_options"
    assert len(deco["suggestions"]["options"]) == 1
    assert deco["suggestions"]["options"][0]["action"] == "save_fm_progress"
    assert deco.get("document_requirement") is not None
    docs = deco["document_requirement"]["documents"]
    assert {d["key"] for d in docs} == {
        "SR_HANDOVER_CHECKLIST",
        "SR_HANDOVER_SITE_SURVEY",
        "SR_COP_CHECKLIST_OTHER",  # Fix 2: SR_COP_CHECKLIST_OTHER replaced SR_HANDOVER_OTHER
    }
    assert "SR_HANDOVER_OTHER" not in {d["key"] for d in docs}
    labels = {d["key"]: d["label"] for d in docs}
    assert labels["SR_COP_CHECKLIST_OTHER"] == "Other"


def test_build_review_decorations_fm_approve_when_ready() -> None:
    """Ready FM review (date + doc) → only Approve Review."""
    state = {
        "backend_refs": {"sr_id": "12847", "user_role": "FM_MANAGER"},
        "workflow_stage": "FM_REVIEW",
        "session_id": "sess-1",
        "documents": [
            {"document_id": "d1", "document_type_id": "SR_HANDOVER_CHECKLIST"},
        ],
        "collected_data": {"unit_readiness_date": "2026-07-15"},
    }
    deco = build_review_decorations(state)
    assert len(deco["suggestions"]["options"]) == 1
    assert deco["suggestions"]["options"][0]["action"] == "approve_fm_review"


def test_build_review_decorations_keeps_card_after_fm_upload() -> None:
    """After an FM attach, the card stays so the user sees Uploaded status."""
    state = {
        "backend_refs": {"sr_id": "12847", "user_role": "FM_MANAGER"},
        "workflow_stage": "FM_REVIEW",
        "session_id": "sess-1",
        "documents": [
            {
                "document_id": "d1",
                "document_type_id": "SR_HANDOVER_CHECKLIST",
            }
        ],
    }
    deco = build_review_decorations(state)
    req = deco.get("document_requirement")
    assert req is not None
    by_key = {d["key"]: d["status"] for d in req["documents"]}
    assert by_key["SR_HANDOVER_CHECKLIST"] == "uploaded"
    assert by_key["SR_HANDOVER_SITE_SURVEY"] == "missing"


def test_build_review_decorations_hides_docs_for_non_actor_role() -> None:
    """MM viewing an SR already in FM_REVIEW: no upload card, no chips.

    fm_review_entry_node denies FM actions for MALL_MANAGER, so offering the
    document upload/delete card next to that denial is a dead end.
    """
    state = {
        "backend_refs": {"sr_id": "12847", "user_role": "MALL_MANAGER"},
        "workflow_stage": "FM_REVIEW",
        "session_id": "sess-1",
        "documents": [
            {"document_id": "d1", "document_type_id": "SR_HANDOVER_CHECKLIST"}
        ],
        "collected_data": {},
    }
    deco = build_review_decorations(state)
    assert "document_requirement" not in deco
    assert deco.get("promote_preview") is True
    # No stage action left for MM — offer the SR page instead of a dead end.
    options = deco["suggestions"]["options"]
    assert [o["action"] for o in options] == ["view_service_request"]
    assert options[0]["value"] == "12847"


def test_build_review_decorations_hides_rdd_docs_for_non_actor_role() -> None:
    """FM Manager viewing an SR in RDD_REVIEW gets no RDD upload card."""
    state = {
        "backend_refs": {
            "sr_id": "11721",
            "user_role": "FM_MANAGER",
            "rdd_status": "IN_PROGRESS",
        },
        "workflow_stage": "RDD_REVIEW",
        "session_id": "sess-1",
        "documents": [],
        "collected_data": {
            "guideLineLink": "https://example.com/g",
            "rdd_upload_doc_type": "DR_SR_HANDOVER_REPORT",
        },
    }
    deco = build_review_decorations(state)
    assert "document_requirement" not in deco
    assert deco["suggestions"]["options"][0]["action"] == "view_service_request"


def test_build_review_decorations_hides_docs_for_admin() -> None:
    """ADMIN is denied by fm_review_entry_node, so it gets no card and no chips."""
    state = {
        "backend_refs": {"sr_id": "12847", "user_role": "ADMIN"},
        "workflow_stage": "FM_REVIEW",
        "session_id": "sess-1",
        "documents": [],
        "collected_data": {},
    }
    deco = build_review_decorations(state)
    assert "document_requirement" not in deco
    assert deco["suggestions"]["options"][0]["action"] == "view_service_request"


def test_build_review_decorations_docs_shown_when_role_unresolved() -> None:
    """No role on state (dev/unauthenticated) stays permissive, as before."""
    state = {
        "backend_refs": {"sr_id": "12847"},
        "workflow_stage": "FM_REVIEW",
        "session_id": "sess-1",
        "documents": [],
        "collected_data": {},
    }
    deco = build_review_decorations(state)
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


def test_build_review_decorations_hides_docs_after_report_submitted() -> None:
    state = {
        "backend_refs": {
            "sr_id": "11721",
            "user_role": "DD_ENGINEER",
            "rdd_status": "REPORT_SUBMITTED",
        },
        "workflow_stage": "RDD_REVIEW",
        "session_id": "sess-1",
        "documents": [],
    }
    deco = build_review_decorations(state)
    assert "document_requirement" not in deco
    assert deco["suggestions"]["options"][0]["action"] == "approve_rdd_final"


def test_build_review_decorations_hides_actions_after_rdd_approved() -> None:
    """Post-approval: no stage actions — only the read-only View SR chip."""
    state = {
        "backend_refs": {
            "sr_id": "11721",
            "user_role": "DD_ENGINEER",
            "rdd_status": "APPROVED",
        },
        "workflow_stage": "RDD_REVIEW",
        "session_id": "sess-1",
        "documents": [],
        "collected_data": {
            "actual_handover_date": "2026-07-01",
            "fitout_start_date": "2026-07-02",
            "fitout_end_date": "2026-07-10",
        },
    }
    deco = build_review_decorations(state)
    options = deco["suggestions"]["options"]
    assert [o["action"] for o in options] == ["view_service_request"]
    assert options[0]["value"] == "11721"


def test_build_review_decorations_rdd_hides_docs_until_dates() -> None:
    """RDD document card must wait until required dates are collected."""
    state = {
        "backend_refs": {
            "sr_id": "11721",
            "user_role": "DD_ENGINEER",
            "rdd_status": "IN_PROGRESS",
        },
        "workflow_stage": "RDD_REVIEW",
        "session_id": "sess-1",
        "documents": [],
        "collected_data": {},
    }
    deco = build_review_decorations(state)
    assert "document_requirement" not in deco


def test_build_review_decorations_rdd_shows_docs_after_dates() -> None:
    """Dates alone are not enough for Submit — still collecting type/docs."""
    state = {
        "backend_refs": {
            "sr_id": "11721",
            "user_role": "DD_ENGINEER",
            "rdd_status": "IN_PROGRESS",
        },
        "workflow_stage": "RDD_REVIEW",
        "session_id": "sess-1",
        "documents": [],
        "collected_data": {
            "actual_handover_date": "2026-07-01",
            "fitout_start_date": "2026-07-02",
            "fitout_end_date": "2026-07-10",
        },
    }
    deco = build_review_decorations(state)
    # Type-first: upload card waits for rdd_upload_doc_type; never show Submit yet.
    assert "suggestions" not in deco


def test_build_review_decorations_rdd_no_submit_during_upload_ask() -> None:
    """Upload ask turn: Meeting type chosen but file not attached → no Submit chip."""
    state = {
        "backend_refs": {
            "sr_id": "11721",
            "user_role": "DD_ENGINEER",
            "rdd_status": "IN_PROGRESS",
        },
        "workflow_stage": "RDD_REVIEW",
        "session_id": "sess-1",
        "documents": [],
        "collected_data": {
            "actual_handover_date": "2026-07-01",
            "fitout_start_date": "2026-07-02",
            "fitout_end_date": "2026-07-10",
            "guideLineLink": "https://example.com/g",
            "rdd_upload_doc_type": "DR_SR_HANDOVER_REPORT",
            "document_status": "APPROVED",
        },
    }
    deco = build_review_decorations(state)
    assert deco.get("document_requirement") is not None
    assert "suggestions" not in deco


def test_build_review_decorations_rdd_submit_when_ready() -> None:
    """Submit Report only after Meeting Report uploaded and required fields set."""
    state = {
        "backend_refs": {
            "sr_id": "11721",
            "user_role": "DD_ENGINEER",
            "rdd_status": "IN_PROGRESS",
        },
        "workflow_stage": "RDD_REVIEW",
        "session_id": "sess-1",
        "documents": [
            {
                "document_id": "m1",
                "document_type_id": "DR_SR_HANDOVER_REPORT",
            }
        ],
        "collected_data": {
            "actual_handover_date": "2026-07-01",
            "fitout_start_date": "2026-07-02",
            "fitout_end_date": "2026-07-10",
            "guideLineLink": "https://example.com/g",
            "rdd_upload_doc_type": "DR_SR_HANDOVER_REPORT",
            "document_status": "APPROVED",
            "comments": "",  # optional — explicitly declined
        },
    }
    deco = build_review_decorations(state)
    assert deco["suggestions"]["options"][0]["action"] == "submit_rdd_report"


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


def test_finalize_preserves_suggestions_when_promoting_preview() -> None:
    draft = {"type": "sr_preview_card", "srId": "99", "fields": []}
    suggestions = {
        "kind": "labeled_options",
        "options": [{"label": "Final Approve", "action": "approve_rdd_final"}],
    }
    ui = finalize_response_ui(
        {"type": "message"},
        {"promote_preview": True, "suggestions": suggestions},
        draft_preview=draft,
        response_message="Report submitted",
    )
    assert ui["type"] == "sr_preview_card"
    assert ui["suggestions"] == suggestions


def test_finalize_merges_lease_picker_on_text_question() -> None:
    ui = finalize_response_ui(
        {"type": "text_question", "field": "lease_code", "message": "Lease?"},
        {"suggestions": {"kind": "resource_picker", "resource": "leases", "filters": {}}},
        draft_preview=None,
        response_message="Lease?",
    )
    assert ui["suggestions"]["resource"] == "leases"
    assert ui["type"] == "text_question"


def test_finalize_does_not_promote_docs_over_text_question() -> None:
    ui = finalize_response_ui(
        {
            "type": "text_question",
            "field": "description",
            "message": "Please share a short description.",
        },
        {
            "document_requirement": {
                "documents": [{"key": "SR_HANDOVER_OTHER", "required": False}],
                "uploadContext": {"session_id": "s1", "sr_id": ""},
            }
        },
        draft_preview=None,
        response_message="Please share a short description.",
    )
    assert ui["type"] == "text_question"
    assert ui["field"] == "description"
    assert "documents" not in ui


def test_finalize_promotes_docs_over_message() -> None:
    ui = finalize_response_ui(
        {"type": "message", "message": "Upload optional docs."},
        {
            "document_requirement": {
                "documents": [{"key": "SR_HANDOVER_OTHER", "required": False}],
                "uploadContext": {"session_id": "s1", "sr_id": ""},
            }
        },
        draft_preview=None,
        response_message="Upload optional docs.",
    )
    assert ui["type"] == "document_requirement"
    assert ui["documents"][0]["key"] == "SR_HANDOVER_OTHER"


def test_build_create_sr_attachment_requires_awaiting_upload() -> None:
    from app.agents.services.review_decorations_service import (
        build_create_sr_attachment_decorations,
    )

    base = {
        "workflow_stage": "CREATE_SR",
        "session_id": "sess-1",
        "documents": [],
        "collected_data": {"lease_id": 95404},
        "backend_refs": {},
    }
    assert build_create_sr_attachment_decorations(base) == {}

    awaiting = {
        **base,
        "collected_data": {
            "lease_id": 95404,
            "mm_optional_upload_status": "awaiting_upload",
        },
    }
    deco = build_create_sr_attachment_decorations(awaiting)
    assert deco.get("document_requirement") is not None
    assert deco["suggestions"]["options"][0]["action"] == "continue_optional_upload"


def test_finalize_skips_suggestions_on_confirmation_card() -> None:
    ui = finalize_response_ui(
        {"type": "confirmation_card", "fields": []},
        {"suggestions": {"kind": "labeled_options", "options": [{"label": "X", "action": "y"}]}},
        draft_preview=None,
        response_message="Confirm?",
    )
    assert "suggestions" not in ui


# ---------------------------------------------------------------------------
# The rebuilt upload card rides a question as refresh_card (issue 28)
# ---------------------------------------------------------------------------


def _doc_req_deco() -> dict:
    """Decorations as they look once the Meeting Report has been uploaded."""
    return {
        "document_requirement": {
            "documents": [
                {"key": "DR_SR_HANDOVER_REPORT", "status": "uploaded"},
                {"key": "SR_HANDOVER_OTHER", "status": "missing"},
            ],
            "uploadContext": {
                "session_id": "sess-1",
                "sr_id": "321026",
                "allowedDocumentTypes": [
                    "DR_SR_HANDOVER_REPORT",
                    "SR_HANDOVER_OTHER",
                ],
            },
        }
    }


def test_finalize_sends_rebuilt_upload_card_with_a_question() -> None:
    """Regression: the upload is answered by the next question, and the card
    was dropped — so the client went on offering the Rejected report."""
    ui = finalize_response_ui(
        {"type": "text_question", "field": "actual_handover_date"},
        _doc_req_deco(),
        draft_preview=None,
        response_message="What is the actual handover date?",
    )
    assert ui["type"] == "text_question"
    card = ui["refresh_card"]
    assert card["type"] == "document_requirement"
    assert [d["key"] for d in card["documents"]] == [
        "DR_SR_HANDOVER_REPORT",
        "SR_HANDOVER_OTHER",
    ]
    assert "SR_REJECTED_HANDOVER_REPORT" not in card["uploadContext"][
        "allowedDocumentTypes"
    ]


def test_finalize_still_promotes_the_card_onto_a_plain_message() -> None:
    ui = finalize_response_ui(
        {"type": "message"},
        _doc_req_deco(),
        draft_preview=None,
        response_message="Please upload the report.",
    )
    assert ui["type"] == "document_requirement"
    assert "refresh_card" not in ui


def test_finalize_keeps_a_refresh_card_the_turn_already_set() -> None:
    """The fitout card belongs to the question being asked; it wins."""
    own = {"type": "confirmation_card", "fields": []}
    ui = finalize_response_ui(
        {"type": "text_question", "field": "delay_reason", "refresh_card": own},
        _doc_req_deco(),
        draft_preview=None,
        response_message="Why the delay?",
    )
    assert ui["refresh_card"] is own
