"""Unit tests — corrections must survive the WAITING_FOR_USER short circuit.

Every question the bot asks saves ``status = WAITING_FOR_USER``, so that value
is present on the *next* turn — the one carrying the user's reply. The FM and
RDD entry routes short-circuited straight to response_generation on that stale
value, which swallowed both correction requests and answers.

Observed live in RDD_REVIEW: "i want to edit the fitout start date" reached
response_generation, which cannot write ``collected_data``, so the LLM
improvised "I see you want to change the fitout start date to 28/08/2026" —
quoting the *current* value back. Every date typed afterwards (29/08, 30/08)
was discarded and the same question returned, forever.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.agents.graph.help_agent_graph import (
    _route_after_fm_entry,
    _route_after_handover_entry,
    _route_after_rdd_entry,
    _waiting_short_circuit_target,
)

_RDD_COLLECTED: dict[str, Any] = {
    "guideLineLink": "https://example.com/guidelines",
    "actual_handover_date": "2026-08-28",
    "fitout_start_date": "2026-08-28",
    "fitout_end_date": "2026-08-28",
}


class TestWaitingShortCircuitTarget:
    def test_correction_request_reaches_extraction(self) -> None:
        assert (
            _waiting_short_circuit_target(
                {
                    "workflow_stage": "RDD_REVIEW",
                    "user_message": "i want to edit the fitout start date",
                    "collected_data": _RDD_COLLECTED,
                }
            )
            == "field_extraction"
        )

    def test_answer_to_the_active_field_reaches_extraction(self) -> None:
        """The reply to "what would you like to change it to?" must be merged,
        not handed to an LLM that cannot write collected_data."""
        assert (
            _waiting_short_circuit_target(
                {
                    "workflow_stage": "RDD_REVIEW",
                    "user_message": "29/08/2026",
                    "active_field": "fitout_start_date",
                    "collected_data": _RDD_COLLECTED,
                }
            )
            == "field_extraction"
        )

    def test_genuine_idle_wait_still_short_circuits(self) -> None:
        """An upload offer leaves active_field None — unchanged behaviour."""
        assert (
            _waiting_short_circuit_target(
                {
                    "workflow_stage": "RDD_REVIEW",
                    "user_message": "thanks",
                    "collected_data": _RDD_COLLECTED,
                }
            )
            == "response_generation"
        )


class TestRddEntryRouting:
    @staticmethod
    def _state(**overrides: Any) -> dict[str, Any]:
        state: dict[str, Any] = {
            "workflow_stage": "RDD_REVIEW",
            "status": "WAITING_FOR_USER",
            "collected_data": _RDD_COLLECTED,
            "user_message": "",
        }
        state.update(overrides)
        return state

    @pytest.mark.parametrize(
        "message",
        [
            "i want to edit the fitout start date",
            "i want to change the actual handover date",
            "please update the guideline link",
        ],
    )
    def test_correction_is_not_swallowed_by_stale_waiting_status(
        self, message: str
    ) -> None:
        assert (
            _route_after_rdd_entry(self._state(user_message=message))
            == "field_extraction"
        )

    def test_date_answer_is_not_swallowed(self) -> None:
        assert (
            _route_after_rdd_entry(
                self._state(user_message="29/08/2026", active_field="fitout_start_date")
            )
            == "field_extraction"
        )

    def test_idle_message_still_goes_to_response_generation(self) -> None:
        assert (
            _route_after_rdd_entry(self._state(user_message="ok thanks"))
            == "response_generation"
        )

    def test_confirmed_is_not_shadowed_by_stale_waiting_status(self) -> None:
        """Ordering regression: the action set this turn must win over the
        status persisted by the previous save_state."""
        assert (
            _route_after_rdd_entry(self._state(confirmation_status="CONFIRMED"))
            == "merge_state"
        )

    def test_status_chip_is_not_shadowed(self) -> None:
        assert (
            _route_after_rdd_entry(
                self._state(action_override="select_rdd_status_approved")
            )
            == "merge_state"
        )

    def test_rdd_action_is_not_shadowed(self) -> None:
        assert (
            _route_after_rdd_entry(self._state(backend_refs={"rdd_action": "submit"}))
            == "merge_state"
        )

    def test_submitted_report_still_idles(self) -> None:
        assert (
            _route_after_rdd_entry(
                self._state(backend_refs={"rdd_status": "REPORT_SUBMITTED"})
            )
            == "response_generation"
        )


class TestFmEntryRouting:
    def test_correction_is_not_swallowed(self) -> None:
        assert (
            _route_after_fm_entry(
                {
                    "workflow_stage": "FM_REVIEW",
                    "status": "WAITING_FOR_USER",
                    "user_message": "i want to change the unit readiness date",
                    "collected_data": {"unit_readiness_date": "2026-08-20"},
                }
            )
            == "field_extraction"
        )

    def test_idle_still_short_circuits(self) -> None:
        assert (
            _route_after_fm_entry(
                {
                    "workflow_stage": "FM_REVIEW",
                    "status": "WAITING_FOR_USER",
                    "user_message": "hello",
                    "collected_data": {},
                }
            )
            == "response_generation"
        )

    def test_confirmed_still_wins(self) -> None:
        assert (
            _route_after_fm_entry(
                {"status": "WAITING_FOR_USER", "confirmation_status": "CONFIRMED"}
            )
            == "merge_state"
        )


class TestHandoverEntryRoutingUnchanged:
    """The CREATE_SR route already had this behaviour — it must keep it."""

    def test_correction_reaches_extraction(self) -> None:
        assert (
            _route_after_handover_entry(
                {
                    "workflow_stage": "CREATE_SR",
                    "status": "WAITING_FOR_USER",
                    "user_message": "i want to change the title",
                    "collected_data": {"title": "handover-t01"},
                }
            )
            == "field_extraction"
        )

    def test_upload_offer_still_idles(self) -> None:
        assert (
            _route_after_handover_entry(
                {
                    "workflow_stage": "CREATE_SR",
                    "status": "WAITING_FOR_USER",
                    "user_message": "no",
                    "collected_data": {"title": "handover-t01"},
                }
            )
            == "response_generation"
        )
