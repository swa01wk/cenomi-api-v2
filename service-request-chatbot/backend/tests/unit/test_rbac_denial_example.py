"""Unit tests — a role refusal only offers what that role can actually do.

An RDD Project Manager asking for a Trading Confirmation was refused and then
told "I can help you create or check the status of a handover service request".
RDD cannot create a handover either — only check one.
"""

from __future__ import annotations

import pytest

from app.agents.graph.nodes.supervisor_node import _rbac_denial_example
from app.agents.schemas.help_agent_schema import intents_for_roles


@pytest.mark.parametrize("role", ["DD_ENGINEER", "FM_MANAGER", "OPERATIONS"])
def test_a_role_that_cannot_create_a_handover_is_not_offered_one(role: str) -> None:
    line = _rbac_denial_example(intents_for_roles(frozenset({role})))
    assert "create" not in line
    assert "check the status" in line


def test_a_mall_manager_is_still_offered_to_create_one() -> None:
    line = _rbac_denial_example(intents_for_roles(frozenset({"MALL_MANAGER"})))
    assert "create or check the status of a handover" in line


def test_an_unknown_role_falls_back_to_checking() -> None:
    line = _rbac_denial_example(intents_for_roles(frozenset({"SOMETHING_NEW"})))
    assert "create" not in line
