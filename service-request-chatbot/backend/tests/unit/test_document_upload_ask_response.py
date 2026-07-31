"""Unit tests — build_document_upload_ask_response and is_fm_review_ready.

Covers additions to review_decorations_service.py introduced in staging:
- is_fm_review_ready()         public readiness gate (date + ≥1 FM doc)
- build_document_upload_ask_response()  WAITING_FOR_USER payload with correct
                               message variant and upload card for FM / RDD.
"""

from __future__ import annotations

from typing import Any

from app.agents.services.review_decorations_service import (
    build_document_upload_ask_response,
    is_fm_review_ready,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_CHECKLIST_DOC = {"document_id": "d1", "document_type_id": "SR_HANDOVER_CHECKLIST"}
_SITE_SURVEY_DOC = {"document_id": "d2", "document_type_id": "SR_HANDOVER_SITE_SURVEY"}
_COP_CHECKLIST_DOC = {"document_id": "d3", "document_type_id": "SR_COP_CHECKLIST_OTHER"}  # Fix 2
_OTHER_DOC = {"document_id": "d3-rdd", "document_type_id": "SR_HANDOVER_OTHER"}  # RDD-only after Fix 2
_RDD_DOC = {"document_id": "d4", "document_type_id": "DR_SR_HANDOVER_REPORT"}


def _fm_state(
    collected: dict[str, Any] | None = None,
    documents: list[dict[str, Any]] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    return {
        "workflow_stage": "FM_REVIEW",
        "session_id": "sess-fm",
        "backend_refs": {"sr_id": "SR-001", "user_role": "FM_MANAGER"},
        "collected_data": collected or {},
        "documents": documents or [],
        "confirmation_status": None,
        **extra,
    }


def _rdd_state(
    collected: dict[str, Any] | None = None,
    documents: list[dict[str, Any]] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    return {
        "workflow_stage": "RDD_REVIEW",
        "session_id": "sess-rdd",
        "backend_refs": {"sr_id": "SR-002", "user_role": "DD_ENGINEER"},
        "collected_data": collected or {},
        "documents": documents or [],
        "confirmation_status": None,
        **extra,
    }


# ---------------------------------------------------------------------------
# is_fm_review_ready
# ---------------------------------------------------------------------------


class TestIsFmReviewReady:
    def test_false_when_no_date_no_doc(self) -> None:
        assert not is_fm_review_ready({}, [])

    def test_false_when_date_but_no_doc(self) -> None:
        assert not is_fm_review_ready({"unit_readiness_date": "2026-08-10"}, [])

    def test_false_when_doc_but_no_date(self) -> None:
        assert not is_fm_review_ready({}, [_CHECKLIST_DOC])

    def test_false_when_date_empty_string(self) -> None:
        assert not is_fm_review_ready({"unit_readiness_date": ""}, [_CHECKLIST_DOC])

    def test_false_when_date_none(self) -> None:
        assert not is_fm_review_ready({"unit_readiness_date": None}, [_CHECKLIST_DOC])

    def test_true_with_checklist_and_date(self) -> None:
        assert is_fm_review_ready({"unit_readiness_date": "2026-08-10"}, [_CHECKLIST_DOC])

    def test_true_with_site_survey_and_date(self) -> None:
        assert is_fm_review_ready({"unit_readiness_date": "2026-08-10"}, [_SITE_SURVEY_DOC])

    def test_true_with_cop_checklist_doc_and_date(self) -> None:
        """Fix 2: SR_COP_CHECKLIST_OTHER is the valid FM 'other' type."""
        assert is_fm_review_ready({"unit_readiness_date": "2026-08-10"}, [_COP_CHECKLIST_DOC])

    def test_false_with_rdd_other_doc_and_date(self) -> None:
        """Fix 2: SR_HANDOVER_OTHER is RDD-only — must not count for FM readiness."""
        assert not is_fm_review_ready({"unit_readiness_date": "2026-08-10"}, [_OTHER_DOC])

    def test_rdd_doc_alone_not_sufficient_for_fm_ready(self) -> None:
        """DR_SR_HANDOVER_REPORT is an RDD doc; it must not count for FM readiness."""
        assert not is_fm_review_ready({"unit_readiness_date": "2026-08-10"}, [_RDD_DOC])

    def test_mixed_docs_with_fm_doc_is_ready(self) -> None:
        assert is_fm_review_ready(
            {"unit_readiness_date": "2026-08-10"},
            [_RDD_DOC, _CHECKLIST_DOC],
        )


# ---------------------------------------------------------------------------
# build_document_upload_ask_response — return shape
# ---------------------------------------------------------------------------


class TestBuildDocumentUploadAskResponseShape:
    def test_returns_empty_for_unknown_stage(self) -> None:
        state = {"workflow_stage": "CREATE_SR", "collected_data": {}, "documents": []}
        assert build_document_upload_ask_response(state) == {}

    def test_returns_empty_for_missing_stage(self) -> None:
        assert build_document_upload_ask_response({}) == {}

    def test_fm_status_waiting_for_user(self) -> None:
        result = build_document_upload_ask_response(_fm_state())
        assert result["status"] == "WAITING_FOR_USER"

    def test_rdd_status_waiting_for_user(self) -> None:
        result = build_document_upload_ask_response(_rdd_state())
        assert result["status"] == "WAITING_FOR_USER"

    def test_confirmation_status_cleared(self) -> None:
        state = _fm_state(confirmation_status="PENDING")
        result = build_document_upload_ask_response(state)
        assert result["confirmation_status"] is None
        assert result["confirmation_required"] is False

    def test_active_field_cleared(self) -> None:
        result = build_document_upload_ask_response(_fm_state())
        assert result.get("active_field") is None

    def test_response_ui_type_is_message(self) -> None:
        result = build_document_upload_ask_response(_fm_state())
        assert result["response_ui"]["type"] == "message"

    def test_response_ui_message_matches_response_message(self) -> None:
        result = build_document_upload_ask_response(_fm_state())
        assert result["response_ui"]["message"] == result["response_message"]


# ---------------------------------------------------------------------------
# build_document_upload_ask_response — FM_REVIEW message variants
# ---------------------------------------------------------------------------


class TestBuildDocumentUploadAskResponseFmMessages:
    def test_ask_message_when_not_ready(self) -> None:
        """No date, no doc → 'please upload at least one FM document'."""
        result = build_document_upload_ask_response(_fm_state())
        msg = result["response_message"].lower()
        assert "please upload" in msg
        assert "fm document" in msg or "handover checklist" in msg

    def test_review_message_when_ready(self) -> None:
        """Date + FM doc present → softer 'Review the details' message."""
        result = build_document_upload_ask_response(
            _fm_state(
                collected={"unit_readiness_date": "2026-08-10"},
                documents=[_CHECKLIST_DOC],
            )
        )
        msg = result["response_message"].lower()
        assert "review the details" in msg

    def test_ask_message_when_date_missing_but_doc_present(self) -> None:
        """Doc uploaded but date missing → still the 'please upload' variant."""
        result = build_document_upload_ask_response(
            _fm_state(documents=[_CHECKLIST_DOC])
        )
        msg = result["response_message"].lower()
        assert "please upload" in msg


# ---------------------------------------------------------------------------
# build_document_upload_ask_response — RDD_REVIEW message
# ---------------------------------------------------------------------------


class TestBuildDocumentUploadAskResponseRddMessages:
    def test_rdd_upload_ask_message(self) -> None:
        result = build_document_upload_ask_response(_rdd_state())
        msg = result["response_message"].lower()
        assert "handover meeting report" in msg

    def test_rdd_message_same_regardless_of_docs(self) -> None:
        with_doc = build_document_upload_ask_response(
            _rdd_state(documents=[_RDD_DOC])
        )
        without_doc = build_document_upload_ask_response(_rdd_state())
        assert with_doc["response_message"] == without_doc["response_message"]


# ---------------------------------------------------------------------------
# build_document_upload_ask_response — decorations include document card
# ---------------------------------------------------------------------------


class TestBuildDocumentUploadAskResponseDecorations:
    def test_fm_decorations_contain_document_requirement(self) -> None:
        result = build_document_upload_ask_response(_fm_state())
        deco = result.get("response_decorations") or {}
        assert "document_requirement" in deco

    def test_fm_doc_keys_are_fm_types_only(self) -> None:
        result = build_document_upload_ask_response(_fm_state())
        deco = result["response_decorations"]
        keys = {d["key"] for d in deco["document_requirement"]["documents"]}
        assert keys == {
            "SR_HANDOVER_CHECKLIST",
            "SR_HANDOVER_SITE_SURVEY",
            "SR_COP_CHECKLIST_OTHER",  # Fix 2: SR_COP_CHECKLIST_OTHER replaced SR_HANDOVER_OTHER
        }
        assert "SR_HANDOVER_OTHER" not in keys
        assert "DR_SR_HANDOVER_REPORT" not in keys

    def test_rdd_decorations_contain_document_requirement(self) -> None:
        # Fix 6: guideLineLink + rdd_upload_doc_type must be collected before upload card appears
        result = build_document_upload_ask_response(_rdd_state(collected={
            "guideLineLink": "https://guidelines.com",
            "rdd_upload_doc_type": "DR_SR_HANDOVER_REPORT",
            "document_status": "APPROVED",
        }))
        deco = result.get("response_decorations") or {}
        assert "document_requirement" in deco

    def test_rdd_doc_key_is_rdd_report(self) -> None:
        # Fix 6: guideLineLink + rdd_upload_doc_type must be collected before upload card appears
        result = build_document_upload_ask_response(_rdd_state(collected={
            "guideLineLink": "https://guidelines.com",
            "rdd_upload_doc_type": "DR_SR_HANDOVER_REPORT",
            "document_status": "APPROVED",
        }))
        deco = result["response_decorations"]
        keys = {d["key"] for d in deco["document_requirement"]["documents"]}
        assert "DR_SR_HANDOVER_REPORT" in keys

    def test_confirmation_status_cleared_before_building_decorations(self) -> None:
        """PENDING confirmation_status must be cleared so document_requirement appears."""
        state = _fm_state(confirmation_status="PENDING")
        result = build_document_upload_ask_response(state)
        deco = result.get("response_decorations") or {}
        # document_requirement must appear even though the original state had PENDING
        assert "document_requirement" in deco

    def test_uploaded_doc_reflected_in_card_status(self) -> None:
        """An already-uploaded FM doc should show 'uploaded' status on the card."""
        result = build_document_upload_ask_response(
            _fm_state(documents=[_CHECKLIST_DOC])
        )
        deco = result["response_decorations"]
        docs = deco["document_requirement"]["documents"]
        checklist = next(d for d in docs if d["key"] == "SR_HANDOVER_CHECKLIST")
        assert checklist.get("status") == "uploaded"
