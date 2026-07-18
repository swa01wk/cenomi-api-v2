"""Unit tests — description / notes / comments field separation (Fix 4).

Tests cover:
- description is not satisfied by notes value
- notes turn routes to notes field (not clobbered by comments)
- comments turn routes to comments field
- each field can be declined independently (empty string, not None)
- comments required in CREATE_SR, FM_REVIEW, RDD_REVIEW stage required_fields
- description question is distinct from notes question
"""

from __future__ import annotations

import pytest

from app.agents.schemas.handover_schema import (
    CREATE_SR_STAGE,
    FM_REVIEW_STAGE,
    OPTIONAL_FIELDS,
    RDD_REVIEW_STAGE,
    get_missing_fields,
)
from app.agents.services.missing_field_service import HANDOVER_FIELD_QUESTIONS


# ---------------------------------------------------------------------------
# Schema-level tests
# ---------------------------------------------------------------------------


class TestCommentsInRequiredFields:
    def test_comments_in_create_sr_required_fields(self) -> None:
        assert "comments" in CREATE_SR_STAGE.required_fields

    def test_comments_in_fm_review_required_fields(self) -> None:
        assert "comments" in FM_REVIEW_STAGE.required_fields

    def test_comments_in_rdd_review_required_fields(self) -> None:
        assert "comments" in RDD_REVIEW_STAGE.required_fields

    def test_notes_in_create_sr_required_fields(self) -> None:
        assert "notes" in CREATE_SR_STAGE.required_fields

    def test_inspection_time_in_create_sr_required_fields(self) -> None:
        assert "inspectionTime" in CREATE_SR_STAGE.required_fields

    def test_description_and_notes_both_optional(self) -> None:
        assert "description" in OPTIONAL_FIELDS
        assert "notes" in OPTIONAL_FIELDS

    def test_comments_is_optional(self) -> None:
        assert "comments" in OPTIONAL_FIELDS

    def test_inspection_time_is_optional(self) -> None:
        assert "inspectionTime" in OPTIONAL_FIELDS


class TestGetMissingFields:
    def test_description_not_satisfied_by_notes(self) -> None:
        """Having notes but not description should still list description as missing."""
        collected = {"notes": "Some notes"}
        missing = get_missing_fields("CREATE_SR", collected)
        assert "description" in missing

    def test_notes_not_satisfied_by_description(self) -> None:
        """Having description but not notes should still list notes as missing."""
        collected = {"description": "A description"}
        missing = get_missing_fields("CREATE_SR", collected)
        assert "notes" in missing

    def test_comments_not_satisfied_by_notes(self) -> None:
        """Having notes but not comments should still list comments as missing."""
        collected = {"notes": "Some notes", "description": "Desc"}
        missing = get_missing_fields("CREATE_SR", collected)
        assert "comments" in missing

    def test_all_optional_fields_declined_removes_them_from_missing(self) -> None:
        """Empty string (explicit decline) must remove optional field from missing."""
        collected = {"description": "", "notes": "", "comments": "", "inspectionTime": ""}
        missing = get_missing_fields("CREATE_SR", collected)
        assert "description" not in missing
        assert "notes" not in missing
        assert "comments" not in missing
        assert "inspectionTime" not in missing

    def test_none_value_for_optional_still_missing(self) -> None:
        """None means not yet answered — optional field should still be listed."""
        collected = {"description": None, "notes": None, "comments": None}
        missing = get_missing_fields("CREATE_SR", collected)
        assert "description" in missing
        assert "notes" in missing
        assert "comments" in missing


# ---------------------------------------------------------------------------
# Question map tests
# ---------------------------------------------------------------------------


class TestFieldQuestions:
    def test_description_question_exists(self) -> None:
        assert "description" in HANDOVER_FIELD_QUESTIONS

    def test_notes_question_exists(self) -> None:
        assert "notes" in HANDOVER_FIELD_QUESTIONS

    def test_comments_question_exists(self) -> None:
        assert "comments" in HANDOVER_FIELD_QUESTIONS

    def test_inspection_time_question_exists(self) -> None:
        assert "inspectionTime" in HANDOVER_FIELD_QUESTIONS

    def test_description_question_distinct_from_notes(self) -> None:
        desc_q = HANDOVER_FIELD_QUESTIONS["description"]
        notes_q = HANDOVER_FIELD_QUESTIONS["notes"]
        assert desc_q != notes_q

    def test_notes_question_distinct_from_comments(self) -> None:
        notes_q = HANDOVER_FIELD_QUESTIONS["notes"]
        comments_q = HANDOVER_FIELD_QUESTIONS["comments"]
        assert notes_q != comments_q

    def test_description_question_mentions_skip_or_none(self) -> None:
        """Description question must tell user they can skip it."""
        desc_q = HANDOVER_FIELD_QUESTIONS["description"].lower()
        assert "none" in desc_q or "skip" in desc_q or "no" in desc_q


# ---------------------------------------------------------------------------
# RDD field order test
# ---------------------------------------------------------------------------


class TestRDDFieldOrder:
    def test_guideline_link_before_dates(self) -> None:
        required = RDD_REVIEW_STAGE.required_fields
        guideline_idx = list(required).index("guideLineLink")
        date_idx = list(required).index("actual_handover_date")
        assert guideline_idx < date_idx

    def test_rdd_upload_doc_type_before_dates(self) -> None:
        required = RDD_REVIEW_STAGE.required_fields
        doc_type_idx = list(required).index("rdd_upload_doc_type")
        date_idx = list(required).index("actual_handover_date")
        assert doc_type_idx < date_idx

    def test_guideline_first_required_field(self) -> None:
        required = RDD_REVIEW_STAGE.required_fields
        assert required[0] == "guideLineLink"

    def test_rdd_upload_doc_type_second_required_field(self) -> None:
        required = RDD_REVIEW_STAGE.required_fields
        assert required[1] == "rdd_upload_doc_type"
