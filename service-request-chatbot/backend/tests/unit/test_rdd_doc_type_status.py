"""Unit tests — RDD type-first document status branching."""

from __future__ import annotations

import pytest

from app.agents.graph.nodes.document_upload_node import document_upload_node
from app.agents.graph.nodes.shared.merge_state_node import merge_state_node
from app.agents.schemas.handover_schema import (
    RDD_DOC_TYPE_MEETING,
    RDD_DOC_TYPE_OTHER,
    RDD_DOC_TYPE_REJECTED,
    RDD_REJECTED_STATUS,
    get_missing_fields,
)
from app.agents.services.review_decorations_service import (
    maybe_reset_rdd_upload_slot,
    build_review_decorations,
)


def _rdd_base_collected(**extra: object) -> dict:
    data = {
        "actual_handover_date": "2026-07-01",
        "fitout_start_date": "2026-07-10",
        "fitout_end_date": "2026-08-01",
        "guideLineLink": "https://example.com/guidelines",
    }
    data.update(extra)
    return data


class TestRddMissingFieldStatusBranching:
    def test_other_skips_document_status(self) -> None:
        collected = _rdd_base_collected(rdd_upload_doc_type=RDD_DOC_TYPE_OTHER)
        missing = get_missing_fields("RDD_REVIEW", collected)
        assert "document_status" not in missing
        assert "rdd_upload_doc_type" not in missing

    def test_rejected_skips_document_status_question(self) -> None:
        # Status is auto-set elsewhere; missing check must not ask for it.
        collected = _rdd_base_collected(
            rdd_upload_doc_type=RDD_DOC_TYPE_REJECTED,
            document_status=RDD_REJECTED_STATUS,
        )
        missing = get_missing_fields("RDD_REVIEW", collected)
        assert "document_status" not in missing

    def test_meeting_report_requires_document_status(self) -> None:
        collected = _rdd_base_collected(rdd_upload_doc_type=RDD_DOC_TYPE_MEETING)
        missing = get_missing_fields("RDD_REVIEW", collected)
        assert "document_status" in missing

    def test_meeting_report_with_status_complete(self) -> None:
        collected = _rdd_base_collected(
            rdd_upload_doc_type=RDD_DOC_TYPE_MEETING,
            document_status="APPROVED",
        )
        missing = get_missing_fields("RDD_REVIEW", collected)
        assert missing == []


class TestMergeAutoDisapproved:
    @pytest.mark.asyncio
    async def test_rejected_sets_disapproved(self) -> None:
        state = {
            "workflow_stage": "RDD_REVIEW",
            "collected_data": {},
            "extracted_fields": {
                "rdd_upload_doc_type": {
                    "value": RDD_DOC_TYPE_REJECTED,
                    "confidence": 1.0,
                }
            },
            "corrected_fields": {},
            "user_message": "",
            "active_field": None,
        }
        result = await merge_state_node(state)  # type: ignore[arg-type]
        assert result["collected_data"]["document_status"] == RDD_REJECTED_STATUS

    @pytest.mark.asyncio
    async def test_other_clears_document_status(self) -> None:
        state = {
            "workflow_stage": "RDD_REVIEW",
            "collected_data": {"document_status": "APPROVED"},
            "extracted_fields": {
                "rdd_upload_doc_type": {
                    "value": RDD_DOC_TYPE_OTHER,
                    "confidence": 1.0,
                }
            },
            "corrected_fields": {},
            "user_message": "",
            "active_field": None,
        }
        result = await merge_state_node(state)  # type: ignore[arg-type]
        assert "document_status" not in result["collected_data"]


class TestMaybeResetRddUploadSlot:
    def test_resets_after_other_upload_when_meeting_missing(self) -> None:
        collected = {"rdd_upload_doc_type": RDD_DOC_TYPE_OTHER}
        docs = [{"document_type_id": RDD_DOC_TYPE_OTHER, "document_id": "o1"}]
        out = maybe_reset_rdd_upload_slot(collected, docs)
        assert "rdd_upload_doc_type" not in out

    def test_keeps_type_when_meeting_uploaded(self) -> None:
        collected = {
            "rdd_upload_doc_type": RDD_DOC_TYPE_MEETING,
            "document_status": "APPROVED",
        }
        docs = [
            {"document_type_id": RDD_DOC_TYPE_MEETING, "document_id": "m1"},
        ]
        out = maybe_reset_rdd_upload_slot(collected, docs)
        assert out["rdd_upload_doc_type"] == RDD_DOC_TYPE_MEETING

    def test_clears_disapproved_when_resetting_rejected(self) -> None:
        collected = {
            "rdd_upload_doc_type": RDD_DOC_TYPE_REJECTED,
            "document_status": RDD_REJECTED_STATUS,
        }
        docs = [
            {"document_type_id": RDD_DOC_TYPE_REJECTED, "document_id": "r1"},
        ]
        out = maybe_reset_rdd_upload_slot(collected, docs)
        assert "rdd_upload_doc_type" not in out
        assert "document_status" not in out


class TestRddDecorationsTypeFilter:
    def test_upload_card_filtered_to_chosen_type(self) -> None:
        state = {
            "workflow_stage": "RDD_REVIEW",
            "backend_refs": {"sr_id": "123", "user_role": "DD_ENGINEER"},
            "session_id": "sess-1",
            "documents": [],
            "collected_data": _rdd_base_collected(
                rdd_upload_doc_type=RDD_DOC_TYPE_OTHER
            ),
            "confirmation_status": None,
            "auth_context": None,
        }
        deco = build_review_decorations(state)
        doc_req = deco.get("document_requirement") or {}
        keys = [d["key"] for d in doc_req.get("documents") or []]
        assert keys == [RDD_DOC_TYPE_OTHER]

    def test_no_upload_card_before_type_chosen(self) -> None:
        state = {
            "workflow_stage": "RDD_REVIEW",
            "backend_refs": {"sr_id": "123", "user_role": "DD_ENGINEER"},
            "session_id": "sess-1",
            "documents": [],
            "collected_data": _rdd_base_collected(),
            "confirmation_status": None,
            "auth_context": None,
        }
        deco = build_review_decorations(state)
        assert "document_requirement" not in deco


class TestRejectedDocumentUploadNode:
    @pytest.mark.asyncio
    async def test_rejected_tracked_separately(self) -> None:
        state = {
            "session_id": "s1",
            "backend_refs": {},
            "documents": [
                {
                    "document_id": "rej-1",
                    "document_type_id": RDD_DOC_TYPE_REJECTED,
                }
            ],
        }
        result = await document_upload_node(state)  # type: ignore[arg-type]
        assert result["backend_refs"]["rdd_rejected_document_ids"] == ["rej-1"]
        assert "rdd_document_id" not in result["backend_refs"]
