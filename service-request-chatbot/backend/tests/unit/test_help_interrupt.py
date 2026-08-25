"""Unit tests — a help question asked mid-workflow must not be eaten by the
field currently being collected.

Reported live: after starting a handover SR, "Help me with Dashboard" was bound
to active_field=lease_code and answered "Sorry, I couldn't find a lease matching
that information." Refreshing did nothing (state is server-side); the session
had to be deleted, losing the draft.

Cause: once ``active_agent`` is set, ``_route_after_load`` skips the supervisor
on every subsequent turn, so intent is never re-classified.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.agents.graph.help_agent_graph import _route_after_load
from app.agents.services.help_interrupt_service import detect_help_interrupt


def _state(message: str, field: str | None, agent: str | None = "rdd_handover_agent") -> dict[str, Any]:
    return {
        "backend_refs": {},
        "active_agent": agent,
        "active_field": field,
        "user_message": message,
    }


class TestDetectHelpInterrupt:
    @pytest.mark.parametrize(
        "message",
        [
            "Help me with Dashboard",
            "help me with documents",
            "what is a work permit",
            "what are the handover stages",
            "how do i upload a document",
            "where can i see my payments",
            "explain the fitout process",
            "tell me about the dashboard",
            "can you explain the approval flow",
        ],
    )
    def test_help_questions_are_detected(self, message: str) -> None:
        assert detect_help_interrupt(
            message, active_agent="rdd_handover_agent", active_field="lease_code"
        ) is True

    @pytest.mark.parametrize(
        "message",
        ["t0033400", "none", "21/08/2026", "FM_MANAGER", "yes", "confirm", "Zara Riyadh Park"],
    )
    def test_field_answers_are_never_interrupts(self, message: str) -> None:
        assert detect_help_interrupt(
            message, active_agent="rdd_handover_agent", active_field="lease_code"
        ) is False

    @pytest.mark.parametrize("field", ["notes", "comments", "description", "title"])
    def test_free_text_fields_are_exempt(self, field: str) -> None:
        """A question is a perfectly good note — the same carve-out the
        correction detector needs, for the same reason."""
        assert detect_help_interrupt(
            "How do I get site access?",
            active_agent="rdd_handover_agent",
            active_field=field,
        ) is False

    @pytest.mark.parametrize(
        "message",
        [
            "i want to create a handover request",
            "help me create an SR",
            "how do i cancel this request",
            "can you update the lease code",
        ],
    )
    def test_workflow_intent_stays_with_the_agent(self, message: str) -> None:
        """Driving the workflow is not a help question, even when phrased as one."""
        assert detect_help_interrupt(
            message, active_agent="rdd_handover_agent", active_field="lease_code"
        ) is False

    def test_inactive_workflow_is_left_to_the_supervisor(self) -> None:
        assert detect_help_interrupt(
            "Help me with Dashboard", active_agent=None, active_field=None
        ) is False

    def test_blank_message_is_not_an_interrupt(self) -> None:
        assert detect_help_interrupt(
            "   ", active_agent="rdd_handover_agent", active_field="lease_code"
        ) is False


class TestRoutingSendsInterruptsToFaq:
    def test_the_reported_case_reaches_faq(self) -> None:
        assert _route_after_load(_state("Help me with Dashboard", "lease_code")) == "faq_node"

    def test_lease_code_still_reaches_the_agent(self) -> None:
        assert _route_after_load(_state("t0033400", "lease_code")) == "handover_entry"

    def test_prose_note_still_reaches_the_agent(self) -> None:
        assert _route_after_load(_state("How do I get site access?", "notes")) == "handover_entry"

    def test_no_active_agent_still_goes_to_supervisor(self) -> None:
        assert _route_after_load(_state("Help me with Dashboard", None, agent=None)) == "supervisor"

    def test_faq_node_is_a_declared_route_target(self) -> None:
        """Guards the wiring: an undeclared target makes LangGraph reject the
        compiled graph at runtime, not at import."""
        from app.agents.graph.help_agent_graph import build_help_agent_graph

        assert build_help_agent_graph() is not None
