"""Unit tests — merge_state_node: deterministic state merge.

All tests are synchronous-friendly via ``pytest.mark.asyncio``.

Test groups
-----------
TestBasicMerge            — Happy-path: new fields written to collected_data.
TestBackendProtectedFields — Backend-derived fields are never overwritten.
TestUserCorrectionFlow    — Corrections are detected, values updated, metadata stored.
TestConfidenceFiltering   — Low-confidence extractions are silently skipped.
TestEdgeCases             — Empty/absent extractions, None values, list fields.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.agents.graph.nodes.handover.merge_state_node import (
    BACKEND_PROTECTED_FIELDS,
    merge_state_node,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _state(**overrides: Any) -> dict[str, Any]:
    """Minimal graph state dict."""
    return {
        "session_id": "sess-test",
        "user_id": "user-test",
        **overrides,
    }


def _ext(value: str, confidence: float = 0.9) -> dict[str, Any]:
    """Build an extracted-field entry matching the shape produced by field_extraction_node."""
    return {"value": value, "confidence": confidence}


# ---------------------------------------------------------------------------
# TestBasicMerge
# ---------------------------------------------------------------------------


class TestBasicMerge:
    @pytest.mark.asyncio
    async def test_new_field_written_to_collected_data(self) -> None:
        state = _state(
            extracted_fields={"title": _ext("My SR")},
            collected_data={},
        )
        result = await merge_state_node(state)
        assert result["collected_data"]["title"] == "My SR"

    @pytest.mark.asyncio
    async def test_multiple_new_fields_all_written(self) -> None:
        state = _state(
            extracted_fields={
                "title": _ext("SR"),
                "description": _ext("Fit-out complete"),
                "startDate": _ext("2025-06-01"),
            },
            collected_data={},
        )
        result = await merge_state_node(state)
        cd = result["collected_data"]
        assert cd["title"] == "SR"
        assert cd["description"] == "Fit-out complete"
        assert cd["startDate"] == "2025-06-01"

    @pytest.mark.asyncio
    async def test_existing_collected_data_keys_preserved(self) -> None:
        """Fields already in collected_data but not in extracted_fields must survive."""
        state = _state(
            extracted_fields={"title": _ext("New Title")},
            collected_data={"description": "Existing description"},
        )
        result = await merge_state_node(state)
        cd = result["collected_data"]
        assert cd["description"] == "Existing description"
        assert cd["title"] == "New Title"

    @pytest.mark.asyncio
    async def test_returns_collected_data_key(self) -> None:
        """Node must always return a dict with 'collected_data'."""
        state = _state(extracted_fields={}, collected_data={})
        result = await merge_state_node(state)
        assert "collected_data" in result

    @pytest.mark.asyncio
    async def test_plain_value_extraction_also_merged(self) -> None:
        """Extraction entries that are plain values (not dicts) are accepted."""
        state = _state(
            extracted_fields={"title": "Plain value"},
            collected_data={},
        )
        result = await merge_state_node(state)
        assert result["collected_data"]["title"] == "Plain value"


# ---------------------------------------------------------------------------
# TestDateYearCoercion
# ---------------------------------------------------------------------------


class TestDateYearCoercion:
    @pytest.mark.asyncio
    async def test_bare_date_year_coerced_to_current(self) -> None:
        """LLM stamping a stale year on a bare date is repaired to current year."""
        import datetime

        current = datetime.date.today().year
        state = _state(
            user_message="10th september",
            extracted_fields={"fitout_end_date": _ext("2023-09-10")},
            collected_data={},
            workflow_stage="RDD_REVIEW",
        )
        result = await merge_state_node(state)
        assert result["collected_data"]["fitout_end_date"] == f"{current}-09-10"

    @pytest.mark.asyncio
    async def test_explicit_year_respected(self) -> None:
        """When the user typed a 4-digit year it must not be overridden."""
        state = _state(
            user_message="10 September 2023",
            extracted_fields={"fitout_end_date": _ext("2023-09-10")},
            collected_data={},
            workflow_stage="RDD_REVIEW",
        )
        result = await merge_state_node(state)
        assert result["collected_data"]["fitout_end_date"] == "2023-09-10"

    @pytest.mark.asyncio
    async def test_no_message_no_coercion(self) -> None:
        """Absent user_message → no coercion (deterministic pipeline tests)."""
        state = _state(
            extracted_fields={"startDate": _ext("2025-06-01")},
            collected_data={},
        )
        result = await merge_state_node(state)
        assert result["collected_data"]["startDate"] == "2025-06-01"


# ---------------------------------------------------------------------------
# TestTradingDateRecompute
# ---------------------------------------------------------------------------


class TestTradingDateRecompute:
    @pytest.mark.asyncio
    async def test_trading_date_recomputed_when_fitout_end_changes(self) -> None:
        """A stale trading_date must refresh when fitout_end_date is corrected."""
        state = _state(
            user_message="fitout ends 2026-09-10",
            extracted_fields={"fitout_end_date": _ext("2026-09-10")},
            collected_data={"trading_date": "2023-11-01"},
            workflow_stage="RDD_REVIEW",
        )
        result = await merge_state_node(state)
        # 2026-09-10 + 8 days
        assert result["collected_data"]["trading_date"] == "2026-09-18"

    @pytest.mark.asyncio
    async def test_trading_date_set_when_absent(self) -> None:
        state = _state(
            user_message="fitout ends 2026-09-10",
            extracted_fields={"fitout_end_date": _ext("2026-09-10")},
            collected_data={},
            workflow_stage="RDD_REVIEW",
        )
        result = await merge_state_node(state)
        assert result["collected_data"]["trading_date"] == "2026-09-18"


# ---------------------------------------------------------------------------
# TestBackendProtectedFields
# ---------------------------------------------------------------------------


class TestBackendProtectedFields:
    @pytest.mark.asyncio
    async def test_backend_field_not_written_when_absent(self) -> None:
        """An LLM attempt to write tenant_profile_id must be ignored."""
        state = _state(
            extracted_fields={"tenant_profile_id": _ext("99999")},
            collected_data={},
        )
        result = await merge_state_node(state)
        assert "tenant_profile_id" not in result["collected_data"]

    @pytest.mark.asyncio
    async def test_backend_field_not_overwritten_when_present(self) -> None:
        """Backend-derived value already in collected_data must not be replaced."""
        state = _state(
            extracted_fields={"lease_id": _ext("NEW-LEASE")},
            collected_data={"lease_id": "ORIGINAL-LEASE"},
        )
        result = await merge_state_node(state)
        assert result["collected_data"]["lease_id"] == "ORIGINAL-LEASE"

    @pytest.mark.asyncio
    async def test_all_backend_protected_fields_blocked(self) -> None:
        """Every field in BACKEND_PROTECTED_FIELDS must be blocked from extraction."""
        extracted = {f: _ext("injected") for f in BACKEND_PROTECTED_FIELDS}
        state = _state(extracted_fields=extracted, collected_data={})
        result = await merge_state_node(state)
        for field in BACKEND_PROTECTED_FIELDS:
            assert field not in result["collected_data"], (
                f"Protected field '{field}' must not appear in collected_data"
            )

    @pytest.mark.asyncio
    async def test_user_field_merged_alongside_protected_field_ignored(self) -> None:
        """User-supplied fields proceed normally even when extraction also has protected keys."""
        state = _state(
            extracted_fields={
                "title": _ext("Good Title"),
                "property_id": _ext("PROP-001"),
                "contract_id": _ext("CONTRACT-001"),
            },
            collected_data={},
        )
        result = await merge_state_node(state)
        cd = result["collected_data"]
        assert cd["title"] == "Good Title"
        assert "property_id" not in cd
        assert "contract_id" not in cd

    @pytest.mark.asyncio
    async def test_backend_protected_fields_set_contents(self) -> None:
        """Verify the expected protected fields are in BACKEND_PROTECTED_FIELDS."""
        expected = {
            "tenant_profile_id",
            "property_id",
            "brand_id",
            "lease_id",
            "contract_id",
            "unit_codes",
            "city",
            "contracted_area",
            "lease_brand_mall",
        }
        assert expected == BACKEND_PROTECTED_FIELDS


# ---------------------------------------------------------------------------
# TestUserCorrectionFlow
# ---------------------------------------------------------------------------


class TestUserCorrectionFlow:
    @pytest.mark.asyncio
    async def test_correction_updates_field_value(self) -> None:
        """When user provides a new value for an existing field, it should be updated."""
        state = _state(
            extracted_fields={"title": _ext("Updated Title")},
            collected_data={"title": "Original Title"},
        )
        result = await merge_state_node(state)
        assert result["collected_data"]["title"] == "Updated Title"

    @pytest.mark.asyncio
    async def test_correction_records_old_value_in_metadata(self) -> None:
        """Correction metadata must record the old value."""
        state = _state(
            extracted_fields={"title": _ext("Updated Title")},
            collected_data={"title": "Original Title"},
        )
        result = await merge_state_node(state)
        corrections = result["collected_data"].get("_corrections", [])
        assert len(corrections) == 1
        assert corrections[0]["old_value"] == "Original Title"

    @pytest.mark.asyncio
    async def test_correction_records_new_value_in_metadata(self) -> None:
        state = _state(
            extracted_fields={"title": _ext("Updated Title")},
            collected_data={"title": "Original Title"},
        )
        result = await merge_state_node(state)
        correction = result["collected_data"]["_corrections"][0]
        assert correction["new_value"] == "Updated Title"

    @pytest.mark.asyncio
    async def test_correction_records_field_name_in_metadata(self) -> None:
        state = _state(
            extracted_fields={"description": _ext("New desc")},
            collected_data={"description": "Old desc"},
        )
        result = await merge_state_node(state)
        correction = result["collected_data"]["_corrections"][0]
        assert correction["field"] == "description"

    @pytest.mark.asyncio
    async def test_correction_includes_timestamp(self) -> None:
        state = _state(
            extracted_fields={"title": _ext("New")},
            collected_data={"title": "Old"},
        )
        result = await merge_state_node(state)
        correction = result["collected_data"]["_corrections"][0]
        assert "corrected_at" in correction
        assert isinstance(correction["corrected_at"], str) and correction["corrected_at"]

    @pytest.mark.asyncio
    async def test_no_correction_when_value_unchanged(self) -> None:
        """Same value → no correction entry created."""
        state = _state(
            extracted_fields={"title": _ext("Same Title")},
            collected_data={"title": "Same Title"},
        )
        result = await merge_state_node(state)
        assert "_corrections" not in result["collected_data"]

    @pytest.mark.asyncio
    async def test_no_correction_when_field_is_new(self) -> None:
        """First-time write (field absent in collected_data) → no correction entry."""
        state = _state(
            extracted_fields={"title": _ext("Brand New")},
            collected_data={},
        )
        result = await merge_state_node(state)
        assert "_corrections" not in result["collected_data"]

    @pytest.mark.asyncio
    async def test_multiple_corrections_accumulated(self) -> None:
        """Multiple changed fields each produce their own correction record."""
        state = _state(
            extracted_fields={
                "title": _ext("New Title"),
                "description": _ext("New Desc"),
            },
            collected_data={"title": "Old Title", "description": "Old Desc"},
        )
        result = await merge_state_node(state)
        corrections = result["collected_data"]["_corrections"]
        corrected_fields = {c["field"] for c in corrections}
        assert corrected_fields == {"title", "description"}

    @pytest.mark.asyncio
    async def test_existing_corrections_preserved(self) -> None:
        """Corrections from a previous turn must be retained and extended."""
        prior_corrections = [{"field": "startDate", "old_value": "2025-01-01", "new_value": "2025-02-01", "corrected_at": "T"}]
        state = _state(
            extracted_fields={"title": _ext("Updated")},
            collected_data={"title": "Original", "_corrections": prior_corrections},
        )
        result = await merge_state_node(state)
        corrections = result["collected_data"]["_corrections"]
        assert len(corrections) == 2


# ---------------------------------------------------------------------------
# TestConfidenceFiltering
# ---------------------------------------------------------------------------


class TestConfidenceFiltering:
    @pytest.mark.asyncio
    async def test_high_confidence_field_accepted(self) -> None:
        state = _state(
            extracted_fields={"title": _ext("Good Title", confidence=0.9)},
            collected_data={},
        )
        result = await merge_state_node(state)
        assert result["collected_data"]["title"] == "Good Title"

    @pytest.mark.asyncio
    async def test_field_at_threshold_accepted(self) -> None:
        """Confidence exactly at _CONFIDENCE_THRESHOLD (0.6) must be accepted."""
        state = _state(
            extracted_fields={"title": _ext("Threshold Title", confidence=0.6)},
            collected_data={},
        )
        result = await merge_state_node(state)
        assert result["collected_data"]["title"] == "Threshold Title"

    @pytest.mark.asyncio
    async def test_low_confidence_field_skipped(self) -> None:
        """Confidence below 0.6 → field must not be written."""
        state = _state(
            extracted_fields={"title": _ext("Uncertain Title", confidence=0.5)},
            collected_data={},
        )
        result = await merge_state_node(state)
        assert "title" not in result["collected_data"]

    @pytest.mark.asyncio
    async def test_low_confidence_does_not_trigger_correction(self) -> None:
        """A low-confidence extraction must not overwrite or produce a correction."""
        state = _state(
            extracted_fields={"title": _ext("Shadowy Title", confidence=0.3)},
            collected_data={"title": "Original Title"},
        )
        result = await merge_state_node(state)
        assert result["collected_data"]["title"] == "Original Title"
        assert "_corrections" not in result["collected_data"]


# ---------------------------------------------------------------------------
# TestEdgeCases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    @pytest.mark.asyncio
    async def test_empty_extracted_fields_leaves_collected_data_unchanged(self) -> None:
        state = _state(
            extracted_fields={},
            collected_data={"title": "Existing"},
        )
        result = await merge_state_node(state)
        assert result["collected_data"] == {"title": "Existing"}

    @pytest.mark.asyncio
    async def test_none_extracted_fields_treated_as_empty(self) -> None:
        state = _state(extracted_fields=None, collected_data={"title": "Keep"})
        result = await merge_state_node(state)
        assert result["collected_data"]["title"] == "Keep"

    @pytest.mark.asyncio
    async def test_none_collected_data_treated_as_empty(self) -> None:
        state = _state(
            extracted_fields={"title": _ext("New SR")},
            collected_data=None,
        )
        result = await merge_state_node(state)
        assert result["collected_data"]["title"] == "New SR"

    @pytest.mark.asyncio
    async def test_extraction_with_none_value_skipped(self) -> None:
        """An extraction entry whose value is None must not be written."""
        state = _state(
            extracted_fields={"title": {"value": None, "confidence": 0.95}},
            collected_data={},
        )
        result = await merge_state_node(state)
        assert "title" not in result["collected_data"]

    @pytest.mark.asyncio
    async def test_absent_extracted_fields_key_in_state(self) -> None:
        """State without 'extracted_fields' key must not raise."""
        state = _state(collected_data={"title": "Existing"})
        result = await merge_state_node(state)
        assert result["collected_data"]["title"] == "Existing"


# ---------------------------------------------------------------------------
# TestActiveFieldRemap — bare dates bind to the field the bot asked for
# ---------------------------------------------------------------------------


class TestActiveFieldRemap:
    @pytest.mark.asyncio
    async def test_wrong_date_key_remapped_to_active_field(self) -> None:
        """LLM mapped a bare date to fitout_start; active was actual_handover."""
        state = _state(
            active_field="actual_handover_date",
            user_message="10th august",
            extracted_fields={"fitout_start_date": _ext("2026-08-10")},
            collected_data={},
        )
        result = await merge_state_node(state)
        cd = result["collected_data"]
        assert cd["actual_handover_date"] == "2026-08-10"
        assert "fitout_start_date" not in cd

    @pytest.mark.asyncio
    async def test_correct_date_key_not_remapped(self) -> None:
        state = _state(
            active_field="fitout_end_date",
            user_message="20th august",
            extracted_fields={"fitout_end_date": _ext("2026-08-20")},
            collected_data={"actual_handover_date": "2026-08-10", "fitout_start_date": "2026-08-15"},
            workflow_stage="RDD_REVIEW",
        )
        result = await merge_state_node(state)
        assert result["collected_data"]["fitout_end_date"] == "2026-08-20"

    @pytest.mark.asyncio
    async def test_no_remap_when_multiple_dates_extracted(self) -> None:
        state = _state(
            active_field="actual_handover_date",
            user_message="handover 10 aug fitout start 15 aug",
            extracted_fields={
                "fitout_start_date": _ext("2026-08-15"),
                "fitout_end_date": _ext("2026-08-20"),
            },
            collected_data={},
        )
        result = await merge_state_node(state)
        cd = result["collected_data"]
        assert "actual_handover_date" not in cd
        assert cd["fitout_start_date"] == "2026-08-15"
        assert cd["fitout_end_date"] == "2026-08-20"

    @pytest.mark.asyncio
    async def test_no_remap_without_active_field(self) -> None:
        state = _state(
            user_message="10th august",
            extracted_fields={"fitout_start_date": _ext("2026-08-10")},
            collected_data={},
        )
        result = await merge_state_node(state)
        assert result["collected_data"]["fitout_start_date"] == "2026-08-10"
        assert "actual_handover_date" not in result["collected_data"]


class TestNonDateActiveFieldRemap:
    """Fix 3: non-date active_field must also be remapped correctly."""

    @pytest.mark.asyncio
    async def test_inspection_done_by_remapped_when_llm_maps_to_notes(self) -> None:
        """LLM extracted 'Operations' to notes; active_field=inspection_done_by → remap."""
        state = _state(
            active_field="inspection_done_by",
            user_message="Operations",
            extracted_fields={"notes": _ext("Operations")},
            collected_data={},
        )
        result = await merge_state_node(state)
        cd = result["collected_data"]
        assert cd.get("inspection_done_by") == "Operations"
        assert "notes" not in cd

    @pytest.mark.asyncio
    async def test_guideline_link_remapped_when_llm_maps_to_notes(self) -> None:
        """LLM extracted URL to notes; active_field=guideLineLink → remap."""
        state = _state(
            active_field="guideLineLink",
            user_message="https://guidelines.example.com",
            extracted_fields={"notes": _ext("https://guidelines.example.com")},
            collected_data={},
        )
        result = await merge_state_node(state)
        cd = result["collected_data"]
        assert cd.get("guideLineLink") == "https://guidelines.example.com"
        assert "notes" not in cd

    @pytest.mark.asyncio
    async def test_no_remap_when_multi_fields_extracted(self) -> None:
        """Multiple extracted fields → do not remap; return all as-is."""
        state = _state(
            active_field="inspection_done_by",
            user_message="FM Manager, starts July 10",
            extracted_fields={
                "notes": _ext("FM Manager"),
                "startDate": _ext("2026-07-10"),
            },
            collected_data={},
        )
        result = await merge_state_node(state)
        cd = result["collected_data"]
        assert "inspection_done_by" not in cd
        assert cd.get("notes") == "FM Manager"
        assert cd.get("startDate") == "2026-07-10"

    @pytest.mark.asyncio
    async def test_date_active_field_remap_still_works(self) -> None:
        """Regression guard: date active_field remap still works after Fix 3."""
        state = _state(
            active_field="actual_handover_date",
            user_message="5th august",
            extracted_fields={"fitout_start_date": _ext("2026-08-05")},
            collected_data={},
        )
        result = await merge_state_node(state)
        cd = result["collected_data"]
        assert cd.get("actual_handover_date") == "2026-08-05"
        assert "fitout_start_date" not in cd

    @pytest.mark.asyncio
    async def test_backend_protected_field_never_remapped_to(self) -> None:
        """active_field in BACKEND_PROTECTED_FIELDS → no remap."""
        state = _state(
            active_field="tenant_profile_id",
            user_message="12345",
            extracted_fields={"notes": _ext("12345")},
            collected_data={},
        )
        result = await merge_state_node(state)
        cd = result["collected_data"]
        assert "tenant_profile_id" not in cd
        assert cd.get("notes") == "12345"


class TestInspectionTimeField:
    """Fix 2: inspectionTime must be preserved as-is (not passed through _to_date_only)."""

    @pytest.mark.asyncio
    async def test_inspection_time_preserved_as_hhmm(self) -> None:
        state = _state(
            extracted_fields={"inspectionTime": _ext("14:00")},
            collected_data={},
        )
        result = await merge_state_node(state)
        assert result["collected_data"]["inspectionTime"] == "14:00"

    @pytest.mark.asyncio
    async def test_inspection_time_not_truncated_to_date(self) -> None:
        """HH:MM must never be truncated to 10 chars like a date."""
        state = _state(
            extracted_fields={"inspectionTime": _ext("09:30")},
            collected_data={},
        )
        result = await merge_state_node(state)
        assert result["collected_data"]["inspectionTime"] == "09:30"

    @pytest.mark.asyncio
    async def test_date_update_preserves_stored_inspection_time(self) -> None:
        """Updating startDate must not wipe a previously stored inspectionTime."""
        state = _state(
            extracted_fields={"startDate": _ext("2026-08-10")},
            collected_data={"inspectionTime": "10:30", "startDate": "2026-08-01"},
        )
        result = await merge_state_node(state)
        cd = result["collected_data"]
        assert cd["startDate"] == "2026-08-10"
        assert cd["inspectionTime"] == "10:30"

    @pytest.mark.asyncio
    async def test_inspection_time_auto_defaulted_in_create_sr_when_absent(self) -> None:
        """When inspectionTime is absent in CREATE_SR, it is seeded with the current HH:MM."""
        import datetime
        import re

        state = _state(
            extracted_fields={},
            collected_data={},
            workflow_stage="CREATE_SR",
        )
        before = datetime.datetime.now().strftime("%H:%M")
        result = await merge_state_node(state)
        after = datetime.datetime.now().strftime("%H:%M")

        time_value = result["collected_data"].get("inspectionTime")
        assert time_value is not None, "inspectionTime must be auto-defaulted"
        assert re.fullmatch(r"\d{2}:\d{2}", time_value), f"Expected HH:MM, got {time_value!r}"
        # The defaulted time must be within the window of the test run.
        assert before <= time_value <= after or time_value == before or time_value == after

    @pytest.mark.asyncio
    async def test_inspection_time_not_overwritten_when_already_set(self) -> None:
        """An existing inspectionTime must never be replaced by the auto-default."""
        state = _state(
            extracted_fields={},
            collected_data={"inspectionTime": "08:00"},
            workflow_stage="CREATE_SR",
        )
        result = await merge_state_node(state)
        assert result["collected_data"]["inspectionTime"] == "08:00"

    @pytest.mark.asyncio
    async def test_inspection_time_not_auto_defaulted_outside_create_sr(self) -> None:
        """Auto-default must only fire in CREATE_SR, not FM_REVIEW or RDD_REVIEW."""
        for stage in ("FM_REVIEW", "RDD_REVIEW"):
            state = _state(
                extracted_fields={},
                collected_data={},
                workflow_stage=stage,
            )
            result = await merge_state_node(state)
            assert "inspectionTime" not in result["collected_data"], (
                f"inspectionTime must not be auto-defaulted in {stage}"
            )


class TestNotesCommentsIndependence:
    """Notes and comments are independent form fields — never mirrored."""

    @pytest.mark.asyncio
    async def test_notes_does_not_populate_comments(self) -> None:
        state = _state(
            extracted_fields={"notes": _ext("Site ready")},
            collected_data={},
        )
        result = await merge_state_node(state)
        assert result["collected_data"]["notes"] == "Site ready"
        assert "comments" not in result["collected_data"]

    @pytest.mark.asyncio
    async def test_comments_does_not_populate_notes(self) -> None:
        state = _state(
            extracted_fields={"comments": _ext("Follow up with FM")},
            collected_data={},
        )
        result = await merge_state_node(state)
        assert result["collected_data"]["comments"] == "Follow up with FM"
        assert "notes" not in result["collected_data"]

    @pytest.mark.asyncio
    async def test_notes_and_comments_can_differ(self) -> None:
        state = _state(
            extracted_fields={
                "notes": _ext("Note A"),
                "comments": _ext("Comment B"),
            },
            collected_data={},
        )
        result = await merge_state_node(state)
        assert result["collected_data"]["notes"] == "Note A"
        assert result["collected_data"]["comments"] == "Comment B"
