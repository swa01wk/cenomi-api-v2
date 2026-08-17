"""Unit tests for the card-content fixes reported from live testing.

Covers three independent defects:

* The three fitout confirmation SRs (FOS / FOE / Trading) rendered a **Comments**
  row that the chat flow never collects — ``_route_after_validation`` excludes
  fitout agents from proactive optional asking, so the row could only ever be
  blank. Hidden on both the pre-submit confirmation card and the SR preview card,
  while the FOS/FOE **payload** keeps its ``comments`` key unchanged.
* An Already Trading (HANDED_OVER) SR reopened with the AI lost its Actual Shop
  Handover Date and Actual Fitout End Date, because ``payload_to_collected_data``
  never mapped those keys back out of the platform payload.
* Path B never asked for comments: ``merge_state_node`` filled the generated
  "No additional comments provided for lease X." sentence while the field was
  still ``None``, which is the exact state the proactive-optional-ask branch
  keys on.
"""

from __future__ import annotations

from typing import Any, ClassVar

import pytest

from app.agents.graph.nodes.fitout_confirm.fitout_confirm_confirmation_node import (
    _build_display_fields,
)
from app.agents.graph.nodes.handover.preview_node import (
    _hidden_preview_keys,
    preview_node,
)
from app.agents.graph.nodes.shared.merge_state_node import merge_state_node
from app.agents.schemas.fitout_confirm_schema import SUBCAT_CONFIG
from app.agents.services.review_decorations_service import payload_to_collected_data

_FITOUT_SUB_CATEGORIES = (
    "FIT_OUT_CONFIRM_FOS",
    "FIT_OUT_CONFIRM_END",
    "FIT_OUT_CONFIRM_TRADING",
)


# ---------------------------------------------------------------------------
# Comments hidden on the 3 fitout confirmation SRs
# ---------------------------------------------------------------------------


class TestFitoutCommentsHidden:
    @pytest.mark.parametrize("sub_category", _FITOUT_SUB_CATEGORIES)
    @pytest.mark.parametrize("radio_value", ["yes", "no"])
    def test_confirmation_card_has_no_comments_row(
        self, sub_category: str, radio_value: str
    ) -> None:
        display = _build_display_fields(sub_category, {}, radio_value)
        assert "comments" not in display

    @pytest.mark.parametrize("sub_category", _FITOUT_SUB_CATEGORIES)
    def test_preview_card_hides_comments(self, sub_category: str) -> None:
        assert _hidden_preview_keys({"sub_category": sub_category}) == frozenset(
            {"comments"}
        )

    @pytest.mark.parametrize("sub_category", ["HANDOVER", "HANDED_OVER", ""])
    def test_preview_card_keeps_comments_for_handover_srs(
        self, sub_category: str
    ) -> None:
        assert _hidden_preview_keys({"sub_category": sub_category}) == frozenset()

    @pytest.mark.asyncio
    async def test_preview_node_omits_comments_row_for_fitout_sr(self) -> None:
        state: dict[str, Any] = {
            "sub_category": "FIT_OUT_CONFIRM_FOS",
            "backend_refs": {
                "sr_id": "320999",
                "platform_sr_status": "IN_PROGRESS",
                "sr_operations": [],
            },
            "collected_data": {
                "lease_code": "t0105712",
                "fitout_start_done": "yes",
                "actual_fitout_start_date": "2026-08-20",
                # A stale comment from the portal must not surface on the card.
                "comments": "carried over from the portal",
            },
        }
        result = await preview_node(state)  # type: ignore[arg-type]
        keys = {f["key"] for f in result["response_ui"]["fields"]}
        assert "comments" not in keys
        assert "actual_fitout_start_date" in keys

    @pytest.mark.parametrize(
        ("sub_category", "expected"),
        [
            ("FIT_OUT_CONFIRM_FOS", True),
            ("FIT_OUT_CONFIRM_END", True),
            ("FIT_OUT_CONFIRM_TRADING", False),
        ],
    )
    def test_payload_comments_key_unchanged(
        self, sub_category: str, expected: bool
    ) -> None:
        """Hiding the field is a display decision only — the API contract for
        FOS/FOE still carries ``comments``."""
        assert SUBCAT_CONFIG[sub_category]["payload_has_comments"] is expected


# ---------------------------------------------------------------------------
# Already Trading preview hydration
# ---------------------------------------------------------------------------


class TestHandedOverPreviewHydration:
    _PLATFORM_PAYLOAD: ClassVar[dict[str, Any]] = {
        "lease": "t0031016",
        "mall": "Hamraa Mall",
        "brand": "Brand Baskin Robbins",
        "city": "Riyadh",
        "unit_codes": ["FC018"],
        "contracted_area": 40,
        "title": "Already Trading - t0031016",
        "already_handovered": "already_trading",
        "actual_shop_handover_date": "2026-08-01",
        "actual_fitout_start_date": "2026-08-20",
        "actual_fitout_end_date": "2026-08-20",
        "actual_trading_date": "2026-08-20",
        "comments": "No additional comments provided for lease t0031016.",
    }

    @pytest.mark.parametrize(
        "key",
        [
            "actual_shop_handover_date",
            "actual_fitout_start_date",
            "actual_fitout_end_date",
            "actual_trading_date",
        ],
    )
    def test_all_four_actual_dates_are_mapped(self, key: str) -> None:
        mapped = payload_to_collected_data(self._PLATFORM_PAYLOAD)
        assert mapped[key] == self._PLATFORM_PAYLOAD[key]

    def test_already_handovered_discriminator_is_mapped(self) -> None:
        """HandoverAgenticBridge reads this to pick the HANDED_OVER skeleton."""
        mapped = payload_to_collected_data(self._PLATFORM_PAYLOAD)
        assert mapped["already_handovered"] == "already_trading"

    def test_notes_are_mapped(self) -> None:
        mapped = payload_to_collected_data({"notes": "site access via loading bay"})
        assert mapped["notes"] == "site access via loading bay"

    @pytest.mark.asyncio
    async def test_preview_card_shows_the_two_previously_missing_dates(self) -> None:
        state: dict[str, Any] = {
            "sub_category": "HANDED_OVER",
            "backend_refs": {
                "sr_id": "320940",
                "platform_sr_status": "APPROVED",
                "sr_operations": [
                    {"assigned_role": "MALL_MANAGER", "status": "FINISHED"}
                ],
            },
            "collected_data": payload_to_collected_data(self._PLATFORM_PAYLOAD),
        }
        result = await preview_node(state)  # type: ignore[arg-type]
        labels = {f["label"] for f in result["response_ui"]["fields"]}
        assert "Actual Shop Handover Date" in labels
        assert "Actual Fitout End Date" in labels


# ---------------------------------------------------------------------------
# Path B asks for comments
# ---------------------------------------------------------------------------


class TestHandedOverCommentsAsked:
    @staticmethod
    def _state(**overrides: Any) -> dict[str, Any]:
        state: dict[str, Any] = {
            "workflow_stage": "CREATE_HANDED_OVER_SR",
            "collected_data": {"lease_code": "t0031016"},
            "extracted_fields": {},
            "corrected_fields": {},
            "user_message": "",
            "active_field": None,
        }
        state.update(overrides)
        return state

    @pytest.mark.asyncio
    async def test_comments_left_none_so_the_bot_can_ask(self) -> None:
        """The proactive optional ask in _route_after_validation only fires while
        comments is None — auto-filling here consumed the question."""
        result = await merge_state_node(self._state())  # type: ignore[arg-type]
        assert result["collected_data"].get("comments") is None

    @pytest.mark.asyncio
    async def test_user_answer_is_kept(self) -> None:
        result = await merge_state_node(  # type: ignore[arg-type]
            self._state(
                extracted_fields={
                    "comments": {"value": "handover accepted by FM", "confidence": 1.0}
                },
                active_field="comments",
            )
        )
        assert result["collected_data"]["comments"] == "handover accepted by FM"

    @pytest.mark.asyncio
    async def test_declined_answer_gets_the_generated_default(self) -> None:
        result = await merge_state_node(  # type: ignore[arg-type]
            self._state(
                extracted_fields={"comments": {"value": "", "confidence": 1.0}},
                active_field="comments",
            )
        )
        assert (
            result["collected_data"]["comments"]
            == "No additional comments provided for lease t0031016."
        )

    @pytest.mark.asyncio
    async def test_confirmed_path_never_submits_a_null(self) -> None:
        """Safety net: a Path B SR confirmed without ever answering comments
        still reaches the platform with a readable value."""
        result = await merge_state_node(  # type: ignore[arg-type]
            self._state(confirmation_status="CONFIRMED")
        )
        assert (
            result["collected_data"]["comments"]
            == "No additional comments provided for lease t0031016."
        )

    @pytest.mark.asyncio
    async def test_confirmed_path_does_not_invent_description_or_notes(self) -> None:
        """Path B has no description/notes fields in its payload, so the
        CONFIRMED-path default must not fabricate them."""
        result = await merge_state_node(  # type: ignore[arg-type]
            self._state(confirmation_status="CONFIRMED")
        )
        assert result["collected_data"].get("description") is None
        assert result["collected_data"].get("notes") is None

    @pytest.mark.asyncio
    async def test_fm_and_rdd_review_still_auto_satisfy_comments(self) -> None:
        """Reviewer stages are unchanged — they never ask, so "" stays correct."""
        for stage in ("FM_REVIEW", "RDD_REVIEW"):
            result = await merge_state_node(  # type: ignore[arg-type]
                self._state(workflow_stage=stage)
            )
            assert result["collected_data"]["comments"] == ""


# ---------------------------------------------------------------------------
# FOS / FOE / Trading milestone question wording
# ---------------------------------------------------------------------------


class TestMilestoneQuestionWording:
    """Each milestone owns its verb.

    The three radio fields read as nouns ("fitout start", "fitout complete"), so
    slotting one shared label into a fixed "Has {label} started?" template
    produced "Has the fitout start started?" and "Has fitout complete started?"
    — the second being doubly wrong, since completion is a state that is
    reached, not something that starts.
    """

    _AGENTS: ClassVar[dict[str, str]] = {
        "fitout_start_done": "rdd_fitout_start_agent",
        "fitout_complete_done": "rdd_fitout_end_agent",
        "trading_done": "rdd_trading_confirmation_agent",
    }
    _SUB_CATEGORIES: ClassVar[dict[str, str]] = {
        "fitout_start_done": "FIT_OUT_CONFIRM_FOS",
        "fitout_complete_done": "FIT_OUT_CONFIRM_END",
        "trading_done": "FIT_OUT_CONFIRM_TRADING",
    }
    _ACTUAL_DATES: ClassVar[dict[str, str]] = {
        "fitout_start_done": "actual_fitout_start_date",
        "fitout_complete_done": "actual_fitout_complete_date",
        "trading_done": "actual_trading_date",
    }

    async def _ask(
        self, field: str, radio: str | None = None, actual_date: str | None = None
    ) -> str:
        from app.agents.graph.nodes.shared.missing_field_node import missing_field_node

        collected: dict[str, Any] = {"lease_code": "t0105712", "lease_brand_mall": "x"}
        if radio:
            collected[field] = radio
        if actual_date:
            collected[self._ACTUAL_DATES[field]] = actual_date

        result = await missing_field_node(  # type: ignore[arg-type]
            {
                "workflow_stage": "CREATE_SR",
                "active_agent": self._AGENTS[field],
                "sub_category": self._SUB_CATEGORIES[field],
                "collected_data": collected,
                "documents": [],
                "validation_errors": [],
            }
        )
        return str((result.get("response_ui") or {}).get("message") or "")

    @pytest.mark.parametrize(
        ("field", "expected"),
        [
            ("fitout_start_done", "Has the fitout started?"),
            ("fitout_complete_done", "Has the fitout been completed?"),
            ("trading_done", "Has trading started?"),
        ],
    )
    @pytest.mark.asyncio
    async def test_first_ask_reads_naturally(self, field: str, expected: str) -> None:
        assert (await self._ask(field)).startswith(expected)

    @pytest.mark.parametrize("field", list(_AGENTS))
    @pytest.mark.asyncio
    async def test_no_variant_contains_the_doubled_verb(self, field: str) -> None:
        """"start started" / "complete started" are the exact regressions."""
        for radio, date in ((None, None), ("yes", "2026-08-20"), ("yes", None), ("no", None)):
            message = await self._ask(field, radio, date)
            lowered = message.lower()
            assert "start started" not in lowered, message
            assert "complete started" not in lowered, message
            assert "marked as **done**" not in lowered, message
            assert "marked as **not done**" not in lowered, message

    @pytest.mark.parametrize(
        ("field", "affirmed", "negated"),
        [
            ("fitout_start_done", "**started**", "**not started**"),
            ("fitout_complete_done", "**completed**", "**not completed**"),
            ("trading_done", "**started**", "**not started**"),
        ],
    )
    @pytest.mark.asyncio
    async def test_prefilled_radio_states_the_right_verb(
        self, field: str, affirmed: str, negated: str
    ) -> None:
        assert affirmed in await self._ask(field, "yes")
        assert negated in await self._ask(field, "no")

    @pytest.mark.asyncio
    async def test_fitout_end_offers_the_opposite_answer_in_completion_terms(
        self,
    ) -> None:
        """The trailing clause has to agree with the verb too — "if it has not
        started" makes no sense for a completion milestone."""
        assert "it is not complete" in await self._ask("fitout_complete_done", "yes")
        assert "it is now complete" in await self._ask("fitout_complete_done", "no")


# ---------------------------------------------------------------------------
# Chip parity between the first ask and the correction re-ask
# ---------------------------------------------------------------------------


class TestCorrectionChipsMatchTheFirstAsk:
    """A correction chip must carry the same ``action`` as its first-ask twin.

    The entry nodes key off ``action`` to write the value straight into
    collected_data, and the graph routes those actions past field_extraction. A
    chip without one sends a bare label through LLM re-extraction instead, where
    the correction re-arm reads "field already has a value + nothing extracted"
    as a failed correction and re-asks the question the user just clicked.
    """

    @pytest.mark.parametrize(
        ("field", "expected_actions"),
        [
            (
                "inspection_done_by",
                [
                    "select_inspection_done_by_fm_manager",
                    "select_inspection_done_by_operations",
                ],
            ),
            (
                "already_handovered",
                [
                    "select_handover_type_already_trading",
                    "select_handover_type_initiate_handover",
                ],
            ),
            (
                "document_status",
                [
                    "select_rdd_status_approved",
                    "select_rdd_status_approved_with_changes",
                ],
            ),
        ],
    )
    def test_correction_chips_carry_their_actions(
        self, field: str, expected_actions: list[str]
    ) -> None:
        from app.agents.graph.nodes.shared.missing_field_node import (
            _CORRECTION_FIELD_DECORATIONS,
        )

        options = _CORRECTION_FIELD_DECORATIONS[field]["suggestions"]["options"]
        assert [o.get("action") for o in options] == expected_actions

    @pytest.mark.asyncio
    async def test_document_status_correction_reask_has_message_and_chips(self) -> None:
        """The re-ask quotes the current value and ships the chips with it."""
        from app.agents.graph.nodes.shared.missing_field_node import missing_field_node

        result = await missing_field_node(  # type: ignore[arg-type]
            {
                "workflow_stage": "RDD_REVIEW",
                "active_agent": "rdd_handover_agent",
                "collected_data": {
                    "guideLineLink": "https://example.com",
                    "document_status": "APPROVED",
                },
                "documents": [
                    {"document_type_id": "DR_SR_HANDOVER_REPORT", "document_id": "d1"}
                ],
                "validation_errors": [],
                "correction_pending_field": "document_status",
            }
        )
        assert result["response_ui"]["field"] == "document_status"
        assert result["response_ui"]["verbatim"] is True
        assert "What would you like to change it to?" in result["response_ui"]["message"]
        options = result["response_decorations"]["suggestions"]["options"]
        assert all(o.get("action") for o in options), options
