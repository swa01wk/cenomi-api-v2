"""Unit tests — a document deleted on the form stays deleted.

Observed on RDD PM (and the same way for MM / FM): the form deleted the file,
the chat put the row back on the next apply, and Confirm & Submit failed with
HTTP 500 — the platform log read "Document ID not found", because the payload
still listed the deleted id.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.agents.graph.nodes.shared.merge_state_node import merge_state_node
from app.agents.services.deleted_documents import DELETED_IDS_KEY
from app.agents.services.payload_builder_service import (
    build_fm_review_payload,
    build_rdd_report_payload,
)

RDD = "DR_SR_HANDOVER_REPORT"
FM = "SR_HANDOVER_CHECKLIST"


def _cleared_row(type_id: str, deleted_id: str | None) -> dict[str, Any]:
    row: dict[str, Any] = {
        "document_type_id": type_id,
        "document_id": "",
        "fileName": "",
        "status": "missing",
    }
    if deleted_id:
        row["deleted_document_id"] = deleted_id
    return row


def _state(corrected_docs: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "session_id": "sess-test",
        "user_id": "user-test",
        "workflow_stage": "RDD_REVIEW",
        "collected_data": {"document_id": "rdd-1", "documents": []},
        "corrected_fields": {"documents": corrected_docs, "document_id": ""},
        "documents": [
            {"document_type_id": FM, "document_id": "fm-1"},
            {"document_type_id": RDD, "document_id": "rdd-1"},
        ],
        "backend_refs": {
            "sr_id": "321005",
            "uploaded_documents": ["fm-1", "rdd-1"],
            "rdd_document_id": "rdd-1",
        },
    }


class TestMergeStateForgetsDeletedDocument:
    @pytest.mark.asyncio
    async def test_deleted_row_leaves_state_documents_and_refs(self) -> None:
        result = await merge_state_node(_state([_cleared_row(RDD, "rdd-1")]))
        assert result["documents"] == [{"document_type_id": FM, "document_id": "fm-1"}]
        refs = result["backend_refs"]
        assert refs[DELETED_IDS_KEY] == ["rdd-1"]
        assert refs["uploaded_documents"] == ["fm-1"]
        assert "rdd_document_id" not in refs
        assert result["collected_data"]["document_id"] == ""

    @pytest.mark.asyncio
    async def test_stale_missing_row_without_marker_removes_nothing(self) -> None:
        # The chat's view of a type the form uploaded on its own reads
        # ``missing`` too; only an explicit deletion may trim the list.
        result = await merge_state_node(_state([_cleared_row(FM, None)]))
        assert "documents" not in result
        assert "backend_refs" not in result

    @pytest.mark.asyncio
    async def test_a_second_delete_keeps_the_first(self) -> None:
        state = _state([_cleared_row(FM, "fm-1")])
        state["backend_refs"][DELETED_IDS_KEY] = ["rdd-1"]
        result = await merge_state_node(state)
        assert result["backend_refs"][DELETED_IDS_KEY] == ["fm-1", "rdd-1"]


def _refs(**extra: Any) -> dict[str, Any]:
    return {
        "sr_id": "321005",
        "create_payload": {
            "payload": {
                "mall": "Mall",
                "brand": "Brand",
                "lease": "L1",
                "title": "T",
                "tenant_profile_id": 1,
                "property_id": 2,
                # What the platform still snapshots after the delete.
                "documents_ids": ["fm-1", "fm-gone"],
            },
            "lease_id": "LEASE-1",
        },
        "uploaded_documents": ["fm-1"],
        **extra,
    }


_RDD_DATA = {
    "guideLineLink": "https://example.com/g",
    "actual_handover_date": "2026-08-20",
    "fitout_start_date": "2026-08-30",
    "fitout_end_date": "2026-09-10",
    "trading_date": "2026-09-18",
    "document_status": "APPROVED",
}


class TestPayloadsDropDeletedIds:
    def test_rdd_submit_omits_the_deleted_platform_id(self) -> None:
        # Regression: this is the id the platform refused with
        # "Document ID not found".
        refs = _refs(rdd_document_id="rdd-1", **{DELETED_IDS_KEY: ["fm-gone"]})
        inner = build_rdd_report_payload(_RDD_DATA, refs)["payload"]
        assert "fm-gone" not in inner["documents_ids"]
        assert "fm-1" in inner["documents_ids"]
        assert "rdd-1" in inner["documents_ids"]

    def test_fm_save_omits_the_deleted_platform_id(self) -> None:
        refs = _refs(**{DELETED_IDS_KEY: ["fm-gone"]})
        inner = build_fm_review_payload({}, refs)["payload"]
        assert "fm-gone" not in inner["documents_ids"]
        assert inner["documents_ids"] == ["fm-1"]

    def test_nothing_deleted_changes_nothing(self) -> None:
        inner = build_rdd_report_payload(_RDD_DATA, _refs(rdd_document_id="rdd-1"))["payload"]
        assert "fm-gone" in inner["documents_ids"]
