"""Unit tests — who may raise a fitout confirmation SR, and what cancel clears.

Two defects reported from the RDD Project Manager (DD_ENGINEER) account:

- Trading Confirmation is a mall-side request, but the assistant offered lease
  suggestions and walked an RDD PM through the whole creation form — after
  telling them in the same breath that their role did not allow it.
- Cancelling any of the three confirmation SRs left the left-hand Service
  Request form populated with the cancelled request's lease, company and
  dates, because the cancel turn never asked the client to reset the form.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.agents.graph.nodes.fitout_confirm.fitout_confirm_entry_node import (
    fitout_confirm_entry_node,
)
from app.types.chat import AuthContext

TRADING = "rdd_trading_confirmation_agent"
FITOUT_START = "rdd_fitout_start_agent"
FITOUT_END = "rdd_fitout_end_agent"


def _state(agent: str, roles: list[str], **overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "active_agent": agent,
        "intent": None,
        "workflow_stage": None,
        "user_message": "I want to create a confirmation service request",
        "auth_context": AuthContext(subject_id="u-1", roles=frozenset(roles)),
        "backend_refs": {},
        "collected_data": {"lease_code": "DUMMY-CUST-001"},
        "documents": [],
        "confirmation_status": None,
        "action_override": None,
    }
    base.update(overrides)
    return base


def _refused(result: dict[str, Any]) -> bool:
    return "isn't available for your role" in (result.get("response_message") or "")


class TestWhoMayRaiseAConfirmation:
    @pytest.mark.asyncio
    async def test_rdd_pm_cannot_raise_trading_confirmation(self) -> None:
        result = await fitout_confirm_entry_node(
            _state(TRADING, ["DD_ENGINEER"], intent="CREATE_FITOUT_CONFIRM_TRADING")
        )
        assert _refused(result)
        assert "Trading Confirmation" in result["response_message"]

    @pytest.mark.asyncio
    async def test_the_refusal_names_who_can(self) -> None:
        result = await fitout_confirm_entry_node(
            _state(TRADING, ["DD_ENGINEER"], intent="CREATE_FITOUT_CONFIRM_TRADING")
        )
        message = result["response_message"]
        assert "Mall Manager" in message
        assert "Operations" in message
        # Admin is a superuser, not an answer to "who raises these".
        assert "Admin" not in message

    @pytest.mark.asyncio
    async def test_the_refusal_stops_the_flow_dead(self) -> None:
        """No lease lookup, no guided-mode choice, nothing left to resume."""
        result = await fitout_confirm_entry_node(
            _state(TRADING, ["DD_ENGINEER"], intent="CREATE_FITOUT_CONFIRM_TRADING")
        )
        assert result["status"] == "WAITING_FOR_USER"
        assert result["active_agent"] is None
        assert result["intent"] is None
        assert result["workflow_stage"] is None
        assert result["collected_data"] == {}
        assert result["lease_matches"] == []
        assert result["selected_lease"] is None
        assert result["response_decorations"] == {}

    @pytest.mark.asyncio
    @pytest.mark.parametrize("role", ["MALL_MANAGER", "OPERATIONS", "ADMIN"])
    async def test_mall_side_roles_may_raise_trading(self, role: str) -> None:
        result = await fitout_confirm_entry_node(
            _state(TRADING, [role], intent="CREATE_FITOUT_CONFIRM_TRADING")
        )
        assert not _refused(result)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("agent", [FITOUT_START, FITOUT_END])
    async def test_rdd_pm_may_raise_the_fitout_confirmations(self, agent: str) -> None:
        result = await fitout_confirm_entry_node(_state(agent, ["DD_ENGINEER"]))
        assert not _refused(result)

    @pytest.mark.asyncio
    async def test_mall_manager_may_not_raise_a_fitout_confirmation(self) -> None:
        result = await fitout_confirm_entry_node(_state(FITOUT_END, ["MALL_MANAGER"]))
        assert _refused(result)
        assert "RDD Project Manager" in result["response_message"]

    @pytest.mark.asyncio
    async def test_a_second_role_that_is_allowed_grants_it(self) -> None:
        """Roles are a set — holding one that may raise it is enough."""
        result = await fitout_confirm_entry_node(
            _state(TRADING, ["DD_ENGINEER", "MALL_MANAGER"])
        )
        assert not _refused(result)

    @pytest.mark.asyncio
    async def test_an_unresolved_role_stays_permissive(self) -> None:
        """Matches every other entry guard: deny only when a role is present."""
        result = await fitout_confirm_entry_node(_state(TRADING, []))
        assert not _refused(result)

    @pytest.mark.asyncio
    async def test_an_unclassified_turn_is_still_gated(self) -> None:
        """The node stage-pins an intent-less turn to CREATE_SR, so gate it."""
        result = await fitout_confirm_entry_node(
            _state(TRADING, ["DD_ENGINEER"], intent="UNKNOWN")
        )
        assert _refused(result)

    @pytest.mark.asyncio
    async def test_the_approve_intent_alone_is_not_gated(self) -> None:
        result = await fitout_confirm_entry_node(
            _state(TRADING, ["DD_ENGINEER"], intent="APPROVE_FITOUT_CONFIRM_TRADING")
        )
        assert not _refused(result)

    @pytest.mark.asyncio
    async def test_approving_an_existing_sr_is_not_gated_here(self) -> None:
        """Opening someone else's Trading SR to approve it is a different action."""
        result = await fitout_confirm_entry_node(
            _state(
                TRADING,
                ["DD_ENGINEER"],
                workflow_stage="APPROVE_SR",
                backend_refs={"sr_id": "320935"},
            )
        )
        assert not _refused(result)


class TestCancelClearsTheForm:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("agent", [TRADING, FITOUT_START, FITOUT_END])
    async def test_the_cancel_chip_resets_the_form(self, agent: str) -> None:
        roles = ["MALL_MANAGER"] if agent == TRADING else ["DD_ENGINEER"]
        result = await fitout_confirm_entry_node(
            _state(agent, roles, action_override="confirm_cancel_sr")
        )
        ui = result["response_ui"]
        assert ui["reset_conversation"] is True
        assert ui["cancelled"] is True
        assert result["collected_data"] == {}
        assert result["workflow_stage"] == "CANCELLED"

    @pytest.mark.asyncio
    async def test_a_typed_yes_resets_the_form_too(self) -> None:
        result = await fitout_confirm_entry_node(
            _state(
                FITOUT_END,
                ["DD_ENGINEER"],
                confirmation_status="CANCEL_PENDING",
                user_message="yes",
            )
        )
        assert result["response_ui"]["reset_conversation"] is True
        assert result["collected_data"] == {}

    @pytest.mark.asyncio
    async def test_cancelling_a_draft_that_never_reached_the_platform(self) -> None:
        """The create path has no sr_id — the form still has to empty out."""
        result = await fitout_confirm_entry_node(
            _state(FITOUT_START, ["DD_ENGINEER"], action_override="confirm_cancel_sr")
        )
        assert result["response_ui"]["reset_conversation"] is True

    @pytest.mark.asyncio
    async def test_a_draft_cancel_names_no_service_request(self) -> None:
        """It said "Service Request **N/A** has been cancelled".

        A request being created has no number yet — the SR is raised on submit —
        so naming one read as a failed lookup rather than a discarded draft.
        """
        result = await fitout_confirm_entry_node(
            _state(FITOUT_END, ["DD_ENGINEER"], action_override="confirm_cancel_sr")
        )
        message = result["response_message"]
        assert "N/A" not in message
        assert "Service Request" not in message
        assert "cancelled" in message

    @pytest.mark.asyncio
    async def test_cancelling_a_real_sr_still_names_it(self) -> None:
        result = await fitout_confirm_entry_node(
            _state(
                TRADING,
                ["MALL_MANAGER"],
                action_override="confirm_cancel_sr",
                backend_refs={"sr_id": "321026"},
                workflow_stage="APPROVE_SR",
            )
        )
        assert "**321026**" in result["response_message"]

    @pytest.mark.asyncio
    async def test_start_over_clears_the_form_as_well(self) -> None:
        """"I've cleared everything" has to be true of the form too."""
        result = await fitout_confirm_entry_node(
            _state(FITOUT_END, ["DD_ENGINEER"], user_message="start over")
        )
        assert result["response_ui"]["reset_conversation"] is True
        assert result["collected_data"] == {}

    @pytest.mark.asyncio
    async def test_declining_the_cancel_keeps_the_request(self) -> None:
        result = await fitout_confirm_entry_node(
            _state(FITOUT_END, ["DD_ENGINEER"], action_override="decline_cancel_sr")
        )
        assert (result.get("response_ui") or {}).get("reset_conversation") is None
        assert "collected_data" not in result
