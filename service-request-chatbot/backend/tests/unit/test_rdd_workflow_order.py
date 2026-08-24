"""Unit tests — RDDPM workflow field order (Fix 6).

Tests cover:
- guideLineLink is first required field in RDD_REVIEW
- rdd_upload_doc_type is second required field
- actual_handover_date, fitout_start_date, fitout_end_date come after doc type
- document_status asked only for Meeting Report (conditional)
- _rdd_required_dates_complete now gates on guideline + doc type (not dates)
- Document upload card not shown until guideline + doc type collected
"""

from __future__ import annotations

from typing import Any

import pytest

from app.agents.schemas.handover_schema import (
    RDD_REVIEW_STAGE,
    get_missing_fields,
)
from app.agents.services.review_decorations_service import _rdd_required_dates_complete


# ---------------------------------------------------------------------------
# Field order assertions
# ---------------------------------------------------------------------------


class TestRDDFieldOrder:
    def test_guideline_link_first(self) -> None:
        required = list(RDD_REVIEW_STAGE.required_fields)
        assert required[0] == "guideLineLink"

    def test_rdd_upload_doc_type_second(self) -> None:
        required = list(RDD_REVIEW_STAGE.required_fields)
        assert required[1] == "rdd_upload_doc_type"

    def test_actual_handover_date_after_doc_type(self) -> None:
        required = list(RDD_REVIEW_STAGE.required_fields)
        doc_type_idx = required.index("rdd_upload_doc_type")
        date_idx = required.index("actual_handover_date")
        assert date_idx > doc_type_idx

    def test_fitout_start_date_after_doc_type(self) -> None:
        required = list(RDD_REVIEW_STAGE.required_fields)
        doc_type_idx = required.index("rdd_upload_doc_type")
        date_idx = required.index("fitout_start_date")
        assert date_idx > doc_type_idx

    def test_fitout_end_date_after_doc_type(self) -> None:
        required = list(RDD_REVIEW_STAGE.required_fields)
        doc_type_idx = required.index("rdd_upload_doc_type")
        date_idx = required.index("fitout_end_date")
        assert date_idx > doc_type_idx

    def test_comments_in_rdd_required_fields(self) -> None:
        assert "comments" in RDD_REVIEW_STAGE.required_fields


# ---------------------------------------------------------------------------
# get_missing_fields — document_status conditional
# ---------------------------------------------------------------------------


class TestDocumentStatusConditional:
    def test_document_status_required_for_meeting_report(self) -> None:
        collected = {
            "guideLineLink": "https://guidelines.com",
            "rdd_upload_doc_type": "DR_SR_HANDOVER_REPORT",
            "actual_handover_date": "2026-08-10",
            "fitout_start_date": "2026-08-11",
            "fitout_end_date": "2026-08-20",
            "comments": "",
        }
        missing = get_missing_fields("RDD_REVIEW", collected)
        assert "document_status" in missing

    def test_document_status_required_after_guideline_link(self) -> None:
        """The Meeting Report outcome is collected before the handover dates."""
        collected = {
            "guideLineLink": "https://guidelines.com",
            "actual_handover_date": "2026-08-10",
            "fitout_start_date": "2026-08-11",
            "fitout_end_date": "2026-08-20",
            "comments": "",
        }
        missing = get_missing_fields("RDD_REVIEW", collected)
        assert missing == ["document_status"]

    def test_document_status_not_required_once_rejected_auto_set(self) -> None:
        """A Rejected upload makes merge_state_node write DISAPPROVED, so the
        question is already answered and must not be asked."""
        collected = {
            "guideLineLink": "https://guidelines.com",
            "document_status": "DISAPPROVED",
            "actual_handover_date": "2026-08-10",
            "fitout_start_date": "2026-08-11",
            "fitout_end_date": "2026-08-20",
            "comments": "",
        }
        missing = get_missing_fields("RDD_REVIEW", collected)
        assert "document_status" not in missing

    def test_guideline_asked_first_when_all_empty(self) -> None:
        missing = get_missing_fields("RDD_REVIEW", {})
        assert missing[0] == "guideLineLink"

    def test_document_status_asked_second(self) -> None:
        """rdd_upload_doc_type is retired — uploads moved to the
        document_requirement card, and the Meeting Report outcome took its place
        as the second RDD question."""
        collected = {"guideLineLink": "https://guidelines.com"}
        missing = get_missing_fields("RDD_REVIEW", collected)
        assert missing[0] == "document_status"


# ---------------------------------------------------------------------------
# _rdd_required_dates_complete — gates on guideline + doc type (not dates)
# ---------------------------------------------------------------------------


class TestRddRequiredDatesComplete:
    def test_false_when_guideline_missing(self) -> None:
        collected: dict[str, Any] = {"rdd_upload_doc_type": "SR_HANDOVER_OTHER"}
        assert _rdd_required_dates_complete(collected) is False

    def test_false_when_doc_type_missing(self) -> None:
        collected: dict[str, Any] = {"guideLineLink": "https://guidelines.com"}
        assert _rdd_required_dates_complete(collected) is False

    def test_true_when_guideline_and_doc_type_present(self) -> None:
        collected: dict[str, Any] = {
            "guideLineLink": "https://guidelines.com",
            "rdd_upload_doc_type": "SR_HANDOVER_OTHER",
        }
        assert _rdd_required_dates_complete(collected) is True

    def test_true_even_when_dates_missing(self) -> None:
        """Gate must be true once guideline + doc type collected, even without dates."""
        collected: dict[str, Any] = {
            "guideLineLink": "https://guidelines.com",
            "rdd_upload_doc_type": "SR_HANDOVER_OTHER",
            # actual_handover_date etc. NOT present
        }
        assert _rdd_required_dates_complete(collected) is True

    def test_false_when_empty_strings(self) -> None:
        collected: dict[str, Any] = {
            "guideLineLink": "",
            "rdd_upload_doc_type": "",
        }
        assert _rdd_required_dates_complete(collected) is False
