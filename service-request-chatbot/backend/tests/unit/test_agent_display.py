"""Unit tests for human-readable agent labels.

Agent identifiers are routing keys; anything a person reads should show a
name.  These tests pin that no code path can leak a raw snake_case
identifier into the UI.
"""

from __future__ import annotations

import pytest

from app.agents.agent_display import UNROUTED_LABEL, agent_display_name
from app.agents.registry import SERVICE_REQUEST_AGENT_REGISTRY


@pytest.mark.parametrize(
    "agent,expected",
    [
        ("rdd_trading_confirmation_agent", "RDD Trading Confirmation Agent"),
        ("rdd_handover_agent", "RDD Handover Agent"),
        ("rdd_fitout_start_agent", "RDD Fitout Start Agent"),
        ("rdd_fitout_end_agent", "RDD Fitout End Agent"),
        ("work_permit_agent", "Work Permit Agent"),
        ("help_agent", "Help Agent"),
        ("notification_agent", "Notification Agent"),
    ],
)
def test_known_agents_use_their_registry_name(agent: str, expected: str) -> None:
    assert agent_display_name(agent) == expected


def test_every_registered_agent_has_a_label() -> None:
    """Adding an agent to the registry must not need a second edit here."""
    for agents in SERVICE_REQUEST_AGENT_REGISTRY.values():
        for config in agents.values():
            name = config["agent_name"]
            assert agent_display_name(name) == config["display_name"]


def test_an_unknown_agent_is_title_cased_never_raw() -> None:
    # A new agent must not leak its identifier into the dashboard.
    assert agent_display_name("some_new_agent") == "Some New Agent"
    assert "_" not in agent_display_name("another_experimental_agent")


def test_help_turns_are_attributed_to_the_help_agent() -> None:
    # FAQ turns are answered without active_agent ever being set.
    assert agent_display_name(None, "ASK_HELP") == "Help Agent"
    assert agent_display_name(None, "UNKNOWN") == "Help Agent"


def test_notification_turns_are_attributed_to_the_notification_agent() -> None:
    assert agent_display_name(None, "GET_NOTIFICATION_DETAIL") == "Notification Agent"
    assert agent_display_name(None, "LIST_NOTIFICATIONS") == "Notification Agent"
    assert agent_display_name(None, "OPEN_NOTIFICATION") == "Notification Agent"


def test_intent_is_matched_case_insensitively() -> None:
    assert agent_display_name(None, "ask_help") == "Help Agent"


def test_an_explicit_agent_wins_over_the_intent_fallback() -> None:
    assert (
        agent_display_name("rdd_handover_agent", "ASK_HELP") == "RDD Handover Agent"
    )


def test_unattributable_turns_say_so() -> None:
    assert agent_display_name(None) == UNROUTED_LABEL
    assert agent_display_name(None, "UPDATE_RDD_SERVICE_REQUEST") == UNROUTED_LABEL
    assert agent_display_name("") == UNROUTED_LABEL
