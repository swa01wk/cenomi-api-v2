"""Unit tests — field_correction_service.detect_correction_intent."""

from __future__ import annotations

import pytest

from app.agents.services.field_correction_service import detect_correction_intent


class TestDetectCorrectionIntent:
    def test_positive_synonym_match_with_inline_value(self) -> None:
        result = detect_correction_intent(
            "I want to change the title to Foo Bar",
            {"title": "handover-t0031259", "description": "old desc"},
            active_field="description",
        )
        assert result == ("title", True)

    def test_positive_synonym_match_without_inline_value(self) -> None:
        result = detect_correction_intent(
            "I want to change the title",
            {"title": "handover-t0031259", "description": "old desc"},
            active_field="description",
        )
        assert result == ("title", False)

    def test_verb_inflection_changes(self) -> None:
        """Regression: 'need to make some changes in title' must still match —
        the verb regex must accept inflections, not just the bare stem."""
        result = detect_correction_intent(
            "need to make some changes in title",
            {},
            active_field="inspection_done_by",
        )
        assert result == ("title", False)

    def test_verb_inflection_editing(self) -> None:
        result = detect_correction_intent(
            "editing the mall now",
            {},
            active_field="startDate",
        )
        assert result == ("mall", False)

    def test_polite_request_forms(self) -> None:
        for message, expected in (
            ("can you update the mall", "mall"),
            ("please change the description", "description"),
            ("let me edit the notes", "notes"),
        ):
            assert detect_correction_intent(message, {}, active_field="startDate") == (
                expected,
                False,
            ), message

    def test_answer_containing_verb_is_not_a_correction(self) -> None:
        """Regression: the user's ANSWER to "what would you like to change the
        title to?" may itself contain a correction verb (e.g. "Updated
        renovation title"). Treating that as a fresh correction request re-asks
        the same question forever — the exact infinite loop seen in live use."""
        for message in (
            "Updated renovation title",
            "Corrected title text",
            "Handover for shop renovation",
            "Brand new shop title",
            "20/08/2026 3:00 PM",
        ):
            assert detect_correction_intent(
                message, {"title": "old", "startDate": "x"}, active_field="startDate"
            ) is None, message

    def test_inspection_end_without_date_word(self) -> None:
        """Regression: 'change inspection end' (no trailing 'date') must still match."""
        result = detect_correction_intent(
            "i want to change inspection end",
            {},
            active_field="notes",
        )
        assert result == ("endDate", False)

    def test_inspection_start_without_date_word(self) -> None:
        result = detect_correction_intent(
            "i want to change inspection start",
            {},
            active_field="notes",
        )
        assert result == ("startDate", False)

    def test_negative_no_correction_verb(self) -> None:
        result = detect_correction_intent(
            "the title is Foo Bar",
            {"title": "handover-t0031259"},
            active_field="description",
        )
        assert result is None

    def test_negative_no_field_synonym_matched(self) -> None:
        result = detect_correction_intent(
            "please change something",
            {"title": "handover-t0031259"},
            active_field="description",
        )
        assert result is None

    def test_active_field_excluded(self) -> None:
        """Naming the field currently being asked about is not a redirect."""
        result = detect_correction_intent(
            "I want to change the description",
            {"title": "handover-t0031259", "description": "old desc"},
            active_field="description",
        )
        assert result is None

    def test_unanswered_field_still_redirects(self) -> None:
        """A field skipped earlier (never stored) is still a valid redirect target —
        only the currently active field is excluded, not empty/absent ones."""
        result = detect_correction_intent(
            "I want to change the mall",
            {"title": "handover-t0031259"},
            active_field="description",
        )
        assert result == ("mall", False)

    def test_empty_string_value_still_redirects(self) -> None:
        result = detect_correction_intent(
            "I want to change the notes",
            {"notes": ""},
            active_field="description",
        )
        assert result == ("notes", False)

    def test_skipped_optional_field_redirect(self) -> None:
        """Regression: title skipped earlier (absent from collected_data), active
        field has since moved to startDate — 'edit title' must still redirect."""
        result = detect_correction_intent(
            "i want to edit title",
            {},
            active_field="startDate",
        )
        assert result == ("title", False)

    def test_colon_inline_value_detected(self) -> None:
        result = detect_correction_intent(
            "update mall: Riyadh Park",
            {"mall": "Jawharat Jeddah"},
            active_field="description",
        )
        assert result == ("mall", True)

    def test_specific_date_field_preferred_over_generic(self) -> None:
        result = detect_correction_intent(
            "actually correct the actual trading date to 2026-07-10",
            {"actual_trading_date": "2026-06-01"},
            active_field="actual_fitout_end_date",
        )
        assert result == ("actual_trading_date", True)

    def test_no_collected_data(self) -> None:
        """collected_data=None must not raise — active_field exclusion still applies."""
        result = detect_correction_intent(
            "change the title",
            None,
            active_field="description",
        )
        assert result == ("title", False)


class TestCorrectionRoutingAndGuards:
    """B1/B2/B4 regressions found by the 29-turn live soak test."""

    def test_route_after_validation_honours_pending_correction(self) -> None:
        """B1: a completed draft routes to confirmation, which would drop the
        correction signal without ever consuming it."""
        from app.agents.graph.help_agent_graph import _route_after_validation

        state = {
            "workflow_stage": "CREATE_SR",
            "active_agent": "rdd_handover_agent",
            "collected_data": {"title": "t"},
            "validation_errors": [],
            "correction_pending_field": "description",
        }
        assert _route_after_validation(state) == "missing_field"

    def test_route_after_handover_entry_lets_corrections_through(self) -> None:
        """B1 (second cause): the MM optional-upload gate leaves
        status=WAITING_FOR_USER, which short-circuited to response_generation
        and swallowed every correction once the draft was complete."""
        from app.agents.graph.help_agent_graph import _route_after_handover_entry

        state = {
            "status": "WAITING_FOR_USER",
            "user_message": "i want to change the description",
            "collected_data": {"description": "old text"},
            "active_field": None,
        }
        assert _route_after_handover_entry(state) == "field_extraction"

    def test_route_after_handover_entry_still_short_circuits_normally(self) -> None:
        from app.agents.graph.help_agent_graph import _route_after_handover_entry

        state = {
            "status": "WAITING_FOR_USER",
            "user_message": "yes please upload",
            "collected_data": {"description": "old text"},
            "active_field": None,
        }
        assert _route_after_handover_entry(state) == "response_generation"

    def test_route_after_handover_entry_skips_extraction_for_handover_type_chip(
        self,
    ) -> None:
        """Chip actions that write already_handovered directly inside
        handover_entry_node must skip field_extraction entirely. Routing
        through field_extraction re-runs the LLM on the chip's label text,
        finds nothing new (the value is already set), and the correction
        re-arm safety net misreads that as a failed correction attempt —
        re-asking "Current Handover Type: X. What would you like to change
        it to?" on the very first answer."""
        from app.agents.graph.help_agent_graph import _route_after_handover_entry

        state = {
            "status": "IN_PROGRESS",
            "action_override": "select_handover_type_already_trading",
            "user_message": "Already Trading",
            "collected_data": {"already_handovered": "already_trading"},
            "active_field": "already_handovered",
        }
        assert _route_after_handover_entry(state) == "merge_state"

        state["action_override"] = "select_handover_type_initiate_handover"
        state["collected_data"]["already_handovered"] = "initiate_handover"
        assert _route_after_handover_entry(state) == "merge_state"

    def test_route_after_handover_entry_skips_extraction_for_inspection_done_by_chip(
        self,
    ) -> None:
        from app.agents.graph.help_agent_graph import _route_after_handover_entry

        state = {
            "status": "IN_PROGRESS",
            "action_override": "select_inspection_done_by_fm_manager",
            "user_message": "FM Manager",
            "collected_data": {"inspection_done_by": "FM_MANAGER"},
            "active_field": "inspection_done_by",
        }
        assert _route_after_handover_entry(state) == "merge_state"

    def test_non_date_value_not_remapped_onto_date_field(self) -> None:
        """B4: while a blocking date error pins active_field to a date, an
        answer meant for another field was remapped onto the date, corrupting
        it and losing the answer."""
        from app.agents.graph.nodes.shared.merge_state_node import (
            _remap_extracted_to_active_field,
        )

        extracted = {"inspection_done_by": {"value": "FM_MANAGER", "confidence": 1.0}}
        out = _remap_extracted_to_active_field(extracted, "endDate")
        assert out == extracted, "must not bind a role name onto endDate"

    def test_real_date_still_remaps_onto_date_field(self) -> None:
        from app.agents.graph.nodes.shared.merge_state_node import (
            _remap_extracted_to_active_field,
        )

        extracted = {"startDate": {"value": "2026-08-25", "confidence": 1.0}}
        out = _remap_extracted_to_active_field(extracted, "endDate")
        assert "endDate" in out and "startDate" not in out

    @pytest.mark.asyncio
    async def test_stale_corrected_field_does_not_wipe_free_text_answer(self) -> None:
        """Observed live: user asked to change the description, answered with
        new text, but the form bridge's corrected_fields sync (still carrying
        the old/blank Formik value) raced it on the very next turn and wiped
        description back to "" — which the missing-fields check then treats
        as never-answered and re-asks the original (non-correction) question.

        A stale corrected_fields "" for an optional free-text field must not
        overwrite a real value unless the bot is actively asking about that
        exact field this turn (active_field == field_name)."""
        from app.agents.graph.nodes.shared.merge_state_node import merge_state_node

        new_description = (
            "Handover inspection for lease t0031188 - Brand SKECHERS at "
            "Hamraa Mall new delhi"
        )
        state = {
            "collected_data": {"description": new_description, "lease_code": "t0031188"},
            "corrected_fields": {"description": ""},
            "extracted_fields": {},
            "active_field": "title",
            "workflow_stage": "CREATE_SR",
            "user_message": "",
        }
        patch = await merge_state_node(state)
        assert patch["collected_data"]["description"] == new_description

    @pytest.mark.asyncio
    async def test_confirmed_guard_does_not_discard_active_field_answer(self) -> None:
        """Observed live: "i want to change the title" → bot asks → user gives a
        new title → bot re-asks quoting the OLD title, forever.

        Cause: once a session has been CONFIRMED, the _confirmed_correction_keys
        guard skips any extracted field also present in corrected_fields. The
        form bridge syncs corrected_fields every turn still carrying the OLD
        form value, so the user's fresh answer was silently discarded. The
        field being actively collected must be exempt from that guard."""
        from app.agents.graph.nodes.shared.merge_state_node import merge_state_node

        old_title = "handover-t0031188"
        state = {
            "collected_data": {"title": old_title, "lease_code": "t0031188"},
            # Bridge re-sends the stale form value alongside the answer.
            "corrected_fields": {"title": old_title},
            "confirmation_status": "CONFIRMED",
            "extracted_fields": {
                "title": {"value": "testing handover approve flow", "confidence": 1.0}
            },
            "active_field": "title",
            "workflow_stage": "CREATE_SR",
            "user_message": "testing handover approve flow",
        }
        patch = await merge_state_node(state)
        assert patch["collected_data"]["title"] == "testing handover approve flow"

    @pytest.mark.asyncio
    async def test_confirmed_guard_still_protects_non_active_fields(self) -> None:
        """The guard's original purpose must survive: a stale extraction for a
        field the bot is NOT asking about must not clobber a deliberate card
        edit on the CONFIRMED path."""
        from app.agents.graph.nodes.shared.merge_state_node import merge_state_node

        state = {
            "collected_data": {"comments": "edited on card", "lease_code": "t0031188"},
            "corrected_fields": {"comments": "edited on card"},
            "confirmation_status": "CONFIRMED",
            # Leftover from a previous turn — must be ignored.
            "extracted_fields": {
                "comments": {"value": "stale prior turn", "confidence": 1.0}
            },
            "active_field": "title",
            "workflow_stage": "CREATE_SR",
            "user_message": "some new title",
        }
        patch = await merge_state_node(state)
        assert patch["collected_data"]["comments"] == "edited on card"

    @pytest.mark.asyncio
    async def test_corrected_field_still_clears_when_actively_answering_it(
        self,
    ) -> None:
        """The guard must not block a genuine skip: when the bot is actively
        asking about the field this turn, "" is a legitimate decline."""
        from app.agents.graph.nodes.shared.merge_state_node import merge_state_node

        state = {
            "collected_data": {"comments": "old comments", "lease_code": "t0031188"},
            "corrected_fields": {"comments": ""},
            "extracted_fields": {},
            "active_field": "comments",
            "workflow_stage": "CREATE_SR",
            "user_message": "none",
        }
        patch = await merge_state_node(state)
        # CREATE_SR auto-generates a default sentence for a skipped comments
        # answer — the guard must not block the skip itself, only a stale
        # overwrite of a real value. Either "" or the generated default is
        # fine here; the old text must be gone.
        assert patch["collected_data"]["comments"] != "old comments"


class TestSoakRegressions60Turn:
    """Bugs surfaced by the 60-turn live soak (B7-B10)."""

    def test_answer_reaches_extraction_behind_waiting_gate(self) -> None:
        """B9: with the MM upload gate active, the ANSWER to a correction was
        swallowed and the LLM improvised — once even claiming a field had been
        updated when nothing was written."""
        from app.agents.graph.help_agent_graph import _route_after_handover_entry

        state = {
            "status": "WAITING_FOR_USER",
            "user_message": "Post completion comments",
            "collected_data": {"comments": "old"},
            "active_field": "comments",
        }
        assert _route_after_handover_entry(state) == "field_extraction"

    def test_upload_offer_still_short_circuits_when_no_active_field(self) -> None:
        from app.agents.graph.help_agent_graph import _route_after_handover_entry

        state = {
            "status": "WAITING_FOR_USER",
            "user_message": "no",
            "collected_data": {"comments": "old"},
            "active_field": None,
        }
        assert _route_after_handover_entry(state) == "response_generation"

    def test_empty_extraction_value_counts_as_unusable(self) -> None:
        """B7: junk maps to {"endDate": {"value": ""}}, not {} — both mean
        'nothing usable' and must re-arm the correction."""
        from app.agents.graph.nodes.shared.field_extraction_node import (
            _has_usable_value,
        )

        assert _has_usable_value({}) is False
        assert _has_usable_value({"endDate": {"value": "", "confidence": 1.0}}) is False
        assert _has_usable_value({"endDate": {"value": "2026-08-27"}}) is True

    def test_inspection_time_does_not_block_date_remap(self) -> None:
        """B8: a typed date extracts date + inspectionTime; counting the time as
        a rival candidate suppressed the remap and let an endDate answer
        overwrite startDate."""
        from app.agents.graph.nodes.shared.merge_state_node import (
            _remap_extracted_to_active_field,
        )

        extracted = {
            "startDate": {"value": "2026-08-27", "confidence": 1.0},
            "inspectionTime": {"value": "16:00", "confidence": 1.0},
        }
        out = _remap_extracted_to_active_field(extracted, "endDate")
        assert out["endDate"]["value"] == "2026-08-27"
        assert "startDate" not in out
        assert out["inspectionTime"]["value"] == "16:00"

    def test_date_never_remapped_onto_enum_field(self) -> None:
        """B10: produced inspection_done_by='2026-08-27'."""
        from app.agents.graph.nodes.shared.merge_state_node import (
            _remap_extracted_to_active_field,
        )

        extracted = {"startDate": {"value": "2026-08-27", "confidence": 1.0}}
        out = _remap_extracted_to_active_field(extracted, "inspection_done_by")
        assert out == extracted

    def test_valid_enum_value_still_remaps(self) -> None:
        from app.agents.graph.nodes.shared.merge_state_node import (
            _remap_extracted_to_active_field,
        )

        extracted = {"notes": {"value": "OPERATIONS", "confidence": 1.0}}
        out = _remap_extracted_to_active_field(extracted, "inspection_done_by")
        assert out["inspection_done_by"]["value"] == "OPERATIONS"


class TestUnspecifiedCorrection:
    """B11: 'can you fix it' named no field, so it fell through to extraction
    and was stored as the active field's value (live: title became
    'chnage the titel')."""

    def test_unspecified_requests_detected(self) -> None:
        from app.agents.services.field_correction_service import (
            is_unspecified_correction,
        )

        for m in ("i want to change something", "can you fix it", "update",
                  "change", "i want to change the thing"):
            assert is_unspecified_correction(m) is True, m

    def test_specific_and_plain_messages_are_not_unspecified(self) -> None:
        from app.agents.services.field_correction_service import (
            is_unspecified_correction,
        )

        for m in ("i want to change the title", "Updated renovation title",
                  "20/08/2026 3:00 PM", "", "Initial notes"):
            assert is_unspecified_correction(m) is False, m


class TestResponseGenerationRepeatGuard:
    """The backend applied the user's answer correctly, but the LLM re-asked
    the previous question anyway — so only the user-facing text was wrong.
    Verified live: title was saved as 'testing handover approve' and
    active_field advanced to endDate, yet the reply was
    "The current title is 'handover-t0031188'. What would you like to change
    it to?" (quoting the pre-correction value straight out of history)."""

    def test_guard_catches_the_observed_repeat(self) -> None:
        from app.agents.graph.nodes.shared.response_generation_node import (
            _is_repeat_of_previous_question,
        )

        previous = "Current Title: handover-t0031188. What would you like to change it to?"
        repeated = (
            "The current title is 'handover-t0031188'. "
            "What would you like to change it to?"
        )
        assert _is_repeat_of_previous_question(repeated, previous)

    def test_guard_allows_a_genuinely_new_question(self) -> None:
        from app.agents.graph.nodes.shared.response_generation_node import (
            _is_repeat_of_previous_question,
        )

        previous = "Current Title: handover-t0031188. What would you like to change it to?"
        new_q = (
            "Please provide the inspection end date, in DD/MM/YYYY format, "
            "e.g. '21/08/2026'."
        )
        assert not _is_repeat_of_previous_question(new_q, previous)

    def test_guard_is_inert_without_history(self) -> None:
        from app.agents.graph.nodes.shared.response_generation_node import (
            _is_repeat_of_previous_question,
        )

        assert not _is_repeat_of_previous_question("Please provide the end date.", "")

    def test_last_assistant_message_picks_most_recent(self) -> None:
        from app.agents.graph.nodes.shared.response_generation_node import (
            _last_assistant_message,
        )

        assert _last_assistant_message([
            {"role": "assistant", "content": "first"},
            {"role": "user", "content": "reply"},
            {"role": "assistant", "content": "second"},
        ]) == "second"

    def test_field_constraint_is_emitted_after_conversation_history(self) -> None:
        """Placement is the actual fix: the same constraint already existed
        near the top of the prompt and the model still ignored it, because the
        history that follows it is far more salient."""
        from app.agents.prompts.response_generation_prompt import (
            build_response_generation_context,
        )

        ctx = build_response_generation_context(
            user_message="testing handover approve",
            response_intent="Ask the user when the inspection should end.",
            workflow_stage="CREATE_SR",
            intent="CREATE_RDD_SERVICE_REQUEST",
            collected_data={"lease_code": "t0031188"},
            missing_fields=["endDate"],
            validation_errors=[],
            confirmation_status=None,
            response_ui_type="text_question",
            conversation_history=[{"role": "assistant", "content": "old question"}],
            asking_field="endDate",
        )
        assert ctx.index("## CONVERSATION SO FAR") < ctx.index("## REQUIRED:")


class TestInspectionTimeAskedBeforeEndDate:
    """A start date typed without a time must be followed by the time
    question — not by the end-date question. Previously merge_state_node
    seeded inspectionTime with the current clock time, which marked it
    answered, so the flow moved on and the inspection was booked at whatever
    time the user happened to be typing."""

    def test_inspection_time_ordered_immediately_after_start_date(self) -> None:
        from app.agents.schemas.handover_schema import CREATE_SR_STAGE

        fields = list(CREATE_SR_STAGE.required_fields)
        assert fields.index("inspectionTime") == fields.index("startDate") + 1
        assert fields.index("inspectionTime") < fields.index("endDate")

    def test_bare_date_leaves_time_missing(self) -> None:
        from app.agents.schemas.handover_schema import get_missing_fields

        collected = {"startDate": "2026-08-20", "inspectionTime": None}
        assert "inspectionTime" in get_missing_fields("CREATE_SR", collected)

    def test_declined_time_is_not_missing(self) -> None:
        """'skip' stores "" — an explicit decline, not an unanswered field."""
        from app.agents.schemas.handover_schema import get_missing_fields

        collected = {"startDate": "2026-08-20", "inspectionTime": ""}
        assert "inspectionTime" not in get_missing_fields("CREATE_SR", collected)

    def test_declined_time_still_submits_nine_am(self) -> None:
        from app.agents.services.payload_builder_service import (
            _normalize_inspection_datetime,
        )

        _, lt = _normalize_inspection_datetime("2026-08-20", None, inspection_time="")
        assert lt == "20/08/2026 9:00 AM"


class TestInspectionEndTimeAskedAfterEndDate:
    """Mirror of the start-date rule for the end of the window: a bare end
    date must prompt for its time rather than silently inheriting the start
    time, and the two clocks must stay independent."""

    def test_end_time_ordered_immediately_after_end_date(self) -> None:
        from app.agents.schemas.handover_schema import CREATE_SR_STAGE

        fields = list(CREATE_SR_STAGE.required_fields)
        assert fields.index("inspectionEndTime") == fields.index("endDate") + 1

    def test_bare_end_date_leaves_end_time_missing(self) -> None:
        from app.agents.schemas.handover_schema import get_missing_fields

        missing = get_missing_fields(
            "CREATE_SR", {"endDate": "2026-08-21", "inspectionEndTime": None}
        )
        assert "inspectionEndTime" in missing

    def test_declined_end_time_is_not_missing(self) -> None:
        from app.agents.schemas.handover_schema import get_missing_fields

        missing = get_missing_fields(
            "CREATE_SR", {"endDate": "2026-08-21", "inspectionEndTime": ""}
        )
        assert "inspectionEndTime" not in missing

    def test_end_time_answer_does_not_overwrite_start_time(self) -> None:
        """The extractor has no inspectionEndTime field and reports the reply
        as inspectionTime; without the re-key it would clobber the start clock."""
        from app.agents.graph.nodes.shared.merge_state_node import _is_end_date_turn

        assert _is_end_date_turn({"inspectionTime": {}}, "inspectionEndTime")
        assert _is_end_date_turn({}, "endDate")
        assert not _is_end_date_turn({"inspectionTime": {}}, "startDate")

    def test_payload_keeps_start_and_end_times_independent(self) -> None:
        from app.agents.services.payload_builder_service import resolve_start_end_hhmm

        assert resolve_start_end_hhmm(
            {"inspectionTime": "10:00", "inspectionEndTime": "18:00"}
        ) == ("10:00", "18:00")

    def test_skipped_end_time_falls_back_to_start(self) -> None:
        from app.agents.services.payload_builder_service import resolve_start_end_hhmm

        assert resolve_start_end_hhmm(
            {"inspectionTime": "10:00", "inspectionEndTime": ""}
        ) == ("10:00", "10:00")


class TestRepeatGuardDiscriminates:
    """The first cut of the repeat guard keyed purely on similarity, which
    misfired: sibling questions in this workflow are near-identical by design
    (notes vs comments score 0.96), so real questions were discarded and
    replaced with a blunt fallback. It also went inert exactly when needed —
    the pre-submission upload gate emits no `field`, so a re-ask slipped
    through and asked for comments the user had already declined."""

    @staticmethod
    def _rejected(message: str, previous: str, asking_field: str | None) -> bool:
        from app.agents.graph.nodes.shared.response_generation_node import (
            _is_repeat_of_previous_question,
            _mentions_field,
        )

        return not _mentions_field(message, asking_field) and (
            _is_repeat_of_previous_question(message, previous)
        )

    NOTES_Q = (
        "Do you have any additional notes for this handover request, "
        "or can you reply 'none' to skip?"
    )
    COMMENTS_Q = (
        "Do you have any additional comments for this handover request, "
        "or can you reply 'none' to skip?"
    )

    def test_comments_question_after_notes_is_kept(self) -> None:
        assert not self._rejected(self.COMMENTS_Q, self.NOTES_Q, "comments")

    def test_end_date_question_after_start_date_is_kept(self) -> None:
        start_q = "Please provide the inspection start date and time in DD/MM/YYYY format."
        end_q = "Please provide the inspection end date and time in DD/MM/YYYY format."
        assert not self._rejected(end_q, start_q, "endDate")

    def test_genuine_repeat_still_rejected(self) -> None:
        previous = "Current Title: handover-t0031188. What would you like to change it to?"
        repeat = (
            "The current title is 'handover-t0031188'. "
            "What would you like to change it to?"
        )
        assert self._rejected(repeat, previous, "endDate")

    def test_guard_active_when_no_field_is_being_collected(self) -> None:
        """asking_field is None on the upload gate — the guard must still fire."""
        assert self._rejected(self.COMMENTS_Q, self.COMMENTS_Q, None)

    def test_mentions_field_handles_camel_case_labels(self) -> None:
        from app.agents.graph.nodes.shared.response_generation_node import _mentions_field

        assert _mentions_field("Please give the inspection end date.", "endDate")
        assert not _mentions_field("Please give the title.", "endDate")


class TestFreeTextAnswerNotReclassifiedAsIntent:
    """`_PREVIEW_PHRASES` matches bare substrings such as "review". While a
    free-text field is being collected the message is CONTENT, so the comment
    "Please review the details and approve it - FM" was routed to the
    supervisor, classified as an approve intent, and RBAC-denied instead of
    being stored."""

    MSG = "Please review the details and approve it - FM"

    @staticmethod
    def _route(message: str, active_field: str | None) -> str:
        from app.agents.graph.help_agent_graph import _route_after_load

        return _route_after_load({
            "user_message": message,
            "active_field": active_field,
            "active_agent": "rdd_handover_agent",
            "backend_refs": {},
        })

    def test_preview_phrase_inside_a_comment_is_not_hijacked(self) -> None:
        assert self._route(self.MSG, "comments") == "handover_entry"

    def test_same_applies_to_every_free_text_field(self) -> None:
        for field in ("title", "description", "notes", "comments"):
            assert self._route(self.MSG, field) == "handover_entry", field

    def test_structured_fields_still_reach_the_supervisor(self) -> None:
        """A date answer is never prose, so preview detection stays active."""
        assert self._route(self.MSG, "endDate") == "supervisor"

    def test_genuine_preview_request_still_works(self) -> None:
        assert self._route("show me what you have", None) == "supervisor"


# ---------------------------------------------------------------------------
# Field synonyms match whole words only
# ---------------------------------------------------------------------------


class TestSynonymWordBoundaries:
    """Short synonyms must not be read out of the middle of longer words.

    ``end``/``start``/``note``/``comment`` are legitimate synonyms, but an
    unanchored substring search finds them inside "weekend", "restart",
    "denote" and "commentary", turning an ordinary sentence into a redirect to
    a field the user never mentioned.
    """

    @pytest.mark.parametrize(
        "message",
        [
            "please update the weekend schedule",
            "can you update the restart procedure",
            "i want to change the commentary in the report",
            "please update the footnotes on page two",
        ],
    )
    def test_embedded_synonym_is_not_a_field_redirect(self, message: str) -> None:
        assert detect_correction_intent(message, {}, "notes") is None

    @pytest.mark.parametrize(
        ("message", "expected"),
        [
            ("please update the end date", "endDate"),
            ("i want to change the start date", "startDate"),
            ("can you update the title", "title"),
            ("please update the comments", "comments"),
        ],
    )
    def test_standalone_synonym_still_matches(self, message: str, expected: str) -> None:
        result = detect_correction_intent(message, {}, "notes")
        assert result is not None
        assert result[0] == expected


# ---------------------------------------------------------------------------
# Stage-scoped synonyms (Path B / Already Trading)
# ---------------------------------------------------------------------------


class TestStageScopedSynonyms:
    """Path B stores the same real-world milestones under ``actual_*`` keys.

    Resolved against the base table, the natural phrasings land on the standard
    handover / RDD fields that Path B never collects — the bot then asked
    "What would you like to *set* as the Actual Handover Date?" for a field
    outside the flow, and the edit went nowhere.
    """

    _PATH_B = "CREATE_HANDED_OVER_SR"

    @pytest.mark.parametrize(
        ("phrase", "expected"),
        [
            ("actual handover date", "actual_shop_handover_date"),
            ("handover date", "actual_shop_handover_date"),
            ("shop handover date", "actual_shop_handover_date"),
            ("actual shop handover date", "actual_shop_handover_date"),
            ("fitout start date", "actual_fitout_start_date"),
            ("actual fitout start date", "actual_fitout_start_date"),
            ("fitout end date", "actual_fitout_end_date"),
            ("actual fitout end date", "actual_fitout_end_date"),
            ("fitout completion date", "actual_fitout_end_date"),
            ("trading date", "actual_trading_date"),
            ("actual trading date", "actual_trading_date"),
            ("comments", "comments"),
            ("title", "title"),
        ],
    )
    def test_path_b_resolves_to_the_field_it_collects(
        self, phrase: str, expected: str
    ) -> None:
        result = detect_correction_intent(
            f"i want to change the {phrase}", {}, None, self._PATH_B
        )
        assert result is not None, f"{phrase!r} was not recognised as a redirect"
        assert result[0] == expected

    @pytest.mark.parametrize(
        ("phrase", "expected"),
        [
            ("actual handover date", "actual_handover_date"),
            ("fitout start date", "fitout_start_date"),
            ("fitout end date", "fitout_end_date"),
            ("guideline link", "guideLineLink"),
        ],
    )
    def test_standard_path_is_unchanged(self, phrase: str, expected: str) -> None:
        for stage in ("RDD_REVIEW", "CREATE_SR", None):
            result = detect_correction_intent(
                f"i want to change the {phrase}", {}, None, stage
            )
            assert result is not None
            assert result[0] == expected, f"{phrase!r} at stage {stage!r}"

    def test_path_b_phrase_is_not_treated_as_unspecified(self) -> None:
        """"the handover date" names no field in the base table, so without the
        stage override it fell through to the unspecified-correction path and
        the request was silently dropped."""
        from app.agents.services.field_correction_service import (
            is_unspecified_correction,
        )

        message = "i want to change the handover date"
        assert is_unspecified_correction(message, self._PATH_B) is False

    def test_inline_value_still_detected_under_a_stage_override(self) -> None:
        result = detect_correction_intent(
            "change the handover date to 05/01/2026", {}, None, self._PATH_B
        )
        assert result == ("actual_shop_handover_date", True)

    def test_active_field_match_is_not_a_redirect_under_an_override(self) -> None:
        """Answering the question currently being asked is not a redirect."""
        assert (
            detect_correction_intent(
                "i want to change the handover date",
                {},
                "actual_shop_handover_date",
                self._PATH_B,
            )
            is None
        )


# ---------------------------------------------------------------------------
# Plural and spelling tolerance
# ---------------------------------------------------------------------------


class TestPluralAndSpellingTolerance:
    """Whether a user singularises a field name carries no meaning.

    Listing both forms by hand is how the plural of "guideline" came to be
    missing: "i want to edit the guidelines" matched nothing, fell through to
    the unspecified-correction path, and re-asked whichever field happened to
    be active — the user asked for the guideline link and was asked for the
    actual handover date.
    """

    @pytest.mark.parametrize(
        "phrase",
        [
            "guideline link",
            "guidelines link",
            "guideline links",
            "guidelines links",
            "guideline",
            "guidelines",
            # Dropped 'e' — the misspelling observed live.
            "guidline link",
            "guidlines link",
            "guidline",
            "guidlines",
            # Spaced out.
            "guide line link",
            "guide line",
        ],
    )
    def test_guideline_link_variants_all_resolve(self, phrase: str) -> None:
        result = detect_correction_intent(
            f"i want to edit {phrase}", {}, None, "RDD_REVIEW"
        )
        assert result is not None, f"{phrase!r} was not recognised"
        assert result[0] == "guideLineLink"

    def test_mixed_case_is_handled(self) -> None:
        result = detect_correction_intent(
            "i want to edit GuideLines Link", {}, None, "RDD_REVIEW"
        )
        assert result == ("guideLineLink", False)

    @pytest.mark.parametrize(
        ("phrase", "expected"),
        [
            ("note", "notes"),
            ("notes", "notes"),
            ("comment", "comments"),
            ("comments", "comments"),
            ("mall", "mall"),
            ("malls", "mall"),
            ("end date", "endDate"),
            ("end dates", "endDate"),
        ],
    )
    def test_plural_is_general_not_per_field(
        self, phrase: str, expected: str
    ) -> None:
        result = detect_correction_intent(f"i want to change the {phrase}", {}, None)
        assert result is not None
        assert result[0] == expected

    @pytest.mark.parametrize(
        "message",
        [
            "please update the weekend schedule",
            "i want to change the commentary in the report",
            "please update the footnotes on page two",
            "can you update the restart procedure",
        ],
    )
    def test_plural_tolerance_adds_no_false_positives(self, message: str) -> None:
        assert detect_correction_intent(message, {}, "notes") is None
