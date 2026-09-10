"""Unit tests — build_document_upload_ask_response and is_fm_review_ready.

Covers additions to review_decorations_service.py introduced in staging:
- is_fm_review_ready()         public readiness gate (unit readiness date)
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
    """The readiness date is the only thing FM approval waits on.

    All three FM documents are optional — the upload card marks every row
    ``required: False`` — so a reviewer with nothing to attach must still be
    able to approve. Readiness used to also demand one FM document, which left
    that reviewer looping on "Please upload at least one FM document".
    """

    def test_false_when_no_date_no_doc(self) -> None:
        assert not is_fm_review_ready({}, [])

    def test_true_when_date_but_no_doc(self) -> None:
        assert is_fm_review_ready({"unit_readiness_date": "2026-08-10"}, [])

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

    def test_true_with_rdd_other_doc_and_date(self) -> None:
        """An RDD-only type does not count for FM, but no longer has to."""
        assert is_fm_review_ready({"unit_readiness_date": "2026-08-10"}, [_OTHER_DOC])

    def test_true_with_rdd_doc_and_date(self) -> None:
        """DR_SR_HANDOVER_REPORT is an RDD doc; the date still carries the gate."""
        assert is_fm_review_ready({"unit_readiness_date": "2026-08-10"}, [_RDD_DOC])

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


def _actions(result: dict[str, Any]) -> list[str]:
    suggestions = (result.get("response_decorations") or {}).get("suggestions") or {}
    return [str(option.get("action")) for option in suggestions.get("options", [])]


class TestBuildDocumentUploadAskResponseFmMessages:
    def test_ask_message_when_not_ready(self) -> None:
        """No date → ask for the date, and say the documents are optional.

        The old wording demanded a document ("Please upload at least one FM
        document ... to continue"), which named the wrong blocker: documents
        never block FM, the readiness date does.
        """
        result = build_document_upload_ask_response(_fm_state())
        msg = result["response_message"].lower()
        assert "unit readiness date" in msg
        assert "optional" in msg
        assert "please upload at least one" not in msg

    def test_continue_message_when_ready(self) -> None:
        """Date present → the card stays open, saying the documents are optional."""
        result = build_document_upload_ask_response(
            _fm_state(
                collected={"unit_readiness_date": "2026-08-10"},
                documents=[_CHECKLIST_DOC],
            )
        )
        msg = result["response_message"].lower()
        assert "optional" in msg
        assert "continue" in msg
        assert "please upload at least one" not in msg
        assert "continue_fm_upload" in _actions(result)

    def test_continue_is_offered_before_any_document(self) -> None:
        """Nothing attached is a valid FM review — Continue must still be there."""
        result = build_document_upload_ask_response(
            _fm_state(collected={"unit_readiness_date": "2026-08-10"})
        )
        assert "continue_fm_upload" in _actions(result)

    def test_continue_is_the_only_chip_on_the_card(self) -> None:
        """No Save Progress beside it.

        The card has its own Save button per document and Continue carries the
        readiness date to the review, so a Save Progress chip here saved
        nothing new and read as a third, competing way off the card.
        """
        result = build_document_upload_ask_response(
            _fm_state(
                collected={"unit_readiness_date": "2026-08-10", "comments": "typed"},
                documents=[_CHECKLIST_DOC],
            )
        )
        assert _actions(result) == ["continue_fm_upload"]

    def test_ask_message_when_date_missing_but_doc_present(self) -> None:
        """Doc uploaded but date missing → still the readiness-date variant."""
        result = build_document_upload_ask_response(
            _fm_state(documents=[_CHECKLIST_DOC])
        )
        msg = result["response_message"].lower()
        assert "unit readiness date" in msg


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
        docs = deco["document_requirement"]["documents"]
        keys = {d["key"] for d in docs}
        assert keys == {
            "SR_HANDOVER_CHECKLIST",
            "SR_HANDOVER_SITE_SURVEY",
            "SR_COP_CHECKLIST_OTHER",  # Fix 2: SR_COP_CHECKLIST_OTHER replaced SR_HANDOVER_OTHER
        }
        assert "SR_HANDOVER_OTHER" not in keys
        assert "DR_SR_HANDOVER_REPORT" not in keys
        labels = {d["key"]: d["label"] for d in docs}
        assert labels["SR_COP_CHECKLIST_OTHER"] == "Other"

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
