"""Unit tests — document merge for sr_status_sync / draft persistence."""

from __future__ import annotations

from app.agents.graph.nodes.shared.sr_status_sync_node import (
    _apply_document_refs,
    _attachments_to_documents,
    _merge_documents,
)


class TestMergeDocuments:
    def test_keeps_session_rdd_report_when_platform_only_has_fm(self) -> None:
        existing = [
            {
                "document_id": "rdd-1",
                "document_type_id": "DR_SR_HANDOVER_REPORT",
                "filename": "report.pdf",
            }
        ]
        platform = [
            {
                "document_id": "fm-1",
                "document_type_id": "SR_HANDOVER_CHECKLIST",
                "file_name": "checklist.pdf",
            }
        ]
        merged = _merge_documents(existing, _attachments_to_documents(platform))
        types = {d["document_type_id"] for d in merged}
        assert types == {"DR_SR_HANDOVER_REPORT", "SR_HANDOVER_CHECKLIST"}

    def test_dedupes_by_document_id(self) -> None:
        existing = [{"document_id": "doc-1", "document_type_id": "DR_SR_HANDOVER_REPORT"}]
        incoming = [{"document_id": "doc-1", "document_type_id": "DR_SR_HANDOVER_REPORT"}]
        merged = _merge_documents(existing, incoming)
        assert len(merged) == 1

    def test_apply_document_refs_sets_rdd_document_id(self) -> None:
        refs: dict = {}
        _apply_document_refs(
            refs,
            [
                {"document_id": "fm-1", "document_type_id": "SR_HANDOVER_CHECKLIST"},
                {"document_id": "rdd-9", "document_type_id": "DR_SR_HANDOVER_REPORT"},
            ],
        )
        assert refs["rdd_document_id"] == "rdd-9"
        assert "rdd-9" in refs["uploaded_documents"]
