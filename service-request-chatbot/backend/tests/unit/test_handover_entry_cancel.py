"""Unit tests — handover_entry_node: cancel flow (Fix 1).

Tests cover:
- cancel before SR created (no API call, state cleared immediately)
- cancel after SR created (CANCEL_PENDING emitted)
- confirm_cancel_sr action (state cleared, response_ui.cancelled = True)
- decline_cancel_sr action (state restored, no clear)
- text-triggered cancel phrases (same flow as cancel_sr UI action)
- UI-triggered cancel parity (cancel_sr alias)
- state after confirmed cancel (all transient fields zeroed)
- CANCEL_PENDING confirmation via text
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.graph.nodes.handover.handover_entry_node import handover_entry_node


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _state(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "user_message": "",
        "confirmation_status": None,
        "action_override": None,
        "collected_data": {},
        "backend_refs": {},
        "workflow_stage": "CREATE_SR",
        "active_agent": "handover_agent",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Cancel before SR created
# ---------------------------------------------------------------------------


class TestCancelBeforeSRCreated:
    @pytest.mark.asyncio
    async def test_cancel_sr_action_no_sr_id_clears_state_immediately(self) -> None:
        """cancel_sr when no sr_id → clear state, no CANCEL_PENDING."""
        state = _state(action_override="cancel_sr", backend_refs={})
        result = await handover_entry_node(state)
        assert result.get("workflow_stage") is None
        assert result.get("active_agent") is None
        assert result.get("collected_data") == {}
        assert result.get("response_ui", {}).get("cancelled") is True
        assert result.get("confirmation_status") is None

    @pytest.mark.asyncio
    async def test_cancel_alias_no_sr_id_clears_state(self) -> None:
        state = _state(action_override="cancel", backend_refs={})
        result = await handover_entry_node(state)
        assert result.get("active_agent") is None
        assert result.get("response_ui", {}).get("cancelled") is True

    @pytest.mark.asyncio
    async def test_cancel_create_sr_alias_no_sr_id_clears_state(self) -> None:
        state = _state(action_override="cancel_create_sr", backend_refs={})
        result = await handover_entry_node(state)
        assert result.get("active_agent") is None
        assert result.get("response_ui", {}).get("cancelled") is True


# ---------------------------------------------------------------------------
# Cancel after SR created (CANCEL_PENDING)
# ---------------------------------------------------------------------------


class TestCancelAfterSRCreated:
    @pytest.mark.asyncio
    async def test_cancel_sr_action_with_sr_id_emits_cancel_pending(self) -> None:
        state = _state(
            action_override="cancel_sr",
            backend_refs={"sr_id": "SR-001"},
        )
        result = await handover_entry_node(state)
        assert result.get("confirmation_status") == "CANCEL_PENDING"
        assert result.get("status") == "WAITING_FOR_USER"
        assert "SR-001" in result.get("response_message", "")
        # State should NOT be cleared yet
        assert "active_agent" not in result or result.get("active_agent") is not None

    @pytest.mark.asyncio
    async def test_cancel_sr_message_mentions_sr_id(self) -> None:
        state = _state(
            action_override="cancel_sr",
            backend_refs={"sr_id": "SR-XYZ"},
        )
        result = await handover_entry_node(state)
        assert "SR-XYZ" in result.get("response_message", "")


# ---------------------------------------------------------------------------
# confirm_cancel_sr
# ---------------------------------------------------------------------------


class TestConfirmCancelSR:
    @pytest.mark.asyncio
    async def test_confirm_cancel_sr_clears_state_and_emits_cancelled(self) -> None:
        mock_svc = MagicMock()
        mock_svc.patch_service_request = AsyncMock(return_value=None)

        with patch(
            "app.agents.graph.nodes.handover.handover_entry_node.get_service_request_api_service",
            return_value=mock_svc,
        ):
            state = _state(
                action_override="confirm_cancel_sr",
                backend_refs={"sr_id": "SR-001"},
            )
            result = await handover_entry_node(state)

        assert result.get("workflow_stage") == "CANCELLED"
        assert result.get("active_agent") is None
        assert result.get("collected_data") == {}
        assert result.get("response_ui", {}).get("cancelled") is True
        assert result.get("response_ui", {}).get("workflow_stage") == "CANCELLED"
        assert result.get("confirmation_status") is None

    @pytest.mark.asyncio
    async def test_confirm_cancel_sr_calls_patch_api(self) -> None:
        mock_svc = MagicMock()
        mock_svc.patch_service_request = AsyncMock(return_value=None)

        with patch(
            "app.agents.graph.nodes.handover.handover_entry_node.get_service_request_api_service",
            return_value=mock_svc,
        ):
            state = _state(
                action_override="confirm_cancel_sr",
                backend_refs={"sr_id": "SR-002"},
            )
            await handover_entry_node(state)

        mock_svc.patch_service_request.assert_called_once()
        call_args = mock_svc.patch_service_request.call_args
        assert call_args[0][0] == "SR-002"
        assert call_args[0][1].get("status") == "CANCELLED"

    @pytest.mark.asyncio
    async def test_confirm_cancel_sr_no_sr_id_still_clears(self) -> None:
        """confirm_cancel_sr with no sr_id → skip API call, still clear state."""
        mock_svc = MagicMock()
        mock_svc.patch_service_request = AsyncMock(return_value=None)

        with patch(
            "app.agents.graph.nodes.handover.handover_entry_node.get_service_request_api_service",
            return_value=mock_svc,
        ):
            state = _state(action_override="confirm_cancel_sr", backend_refs={})
            result = await handover_entry_node(state)

        mock_svc.patch_service_request.assert_not_called()
        assert result.get("response_ui", {}).get("cancelled") is True

    @pytest.mark.asyncio
    async def test_confirm_cancel_sr_api_failure_does_not_raise(self) -> None:
        """API failure during cancel must not crash the node."""
        mock_svc = MagicMock()
        mock_svc.patch_service_request = AsyncMock(side_effect=RuntimeError("network"))

        with patch(
            "app.agents.graph.nodes.handover.handover_entry_node.get_service_request_api_service",
            return_value=mock_svc,
        ):
            state = _state(
                action_override="confirm_cancel_sr",
                backend_refs={"sr_id": "SR-003"},
            )
            result = await handover_entry_node(state)

        # Should still clear state even if API call fails
        assert result.get("collected_data") == {}
        assert result.get("response_ui", {}).get("cancelled") is True


# ---------------------------------------------------------------------------
# decline_cancel_sr
# ---------------------------------------------------------------------------


class TestDeclineCancelSR:
    @pytest.mark.asyncio
    async def test_decline_cancel_sr_does_not_clear_state(self) -> None:
        state = _state(
            action_override="decline_cancel_sr",
            confirmation_status="CANCEL_PENDING",
            collected_data={"lease_code": "LC-123", "mall": "Riyadh Park"},
            backend_refs={"sr_id": "SR-001"},
        )
        result = await handover_entry_node(state)
        assert result.get("confirmation_status") is None
        assert result.get("status") == "IN_PROGRESS"
        # collected_data must not be cleared
        assert "collected_data" not in result or result.get("collected_data") != {}

    @pytest.mark.asyncio
    async def test_decline_cancel_sr_restores_workflow(self) -> None:
        state = _state(
            action_override="decline_cancel_sr",
            confirmation_status="CANCEL_PENDING",
            backend_refs={"sr_id": "SR-001"},
        )
        result = await handover_entry_node(state)
        assert result.get("status") == "IN_PROGRESS"
        assert result.get("response_message") is not None


# ---------------------------------------------------------------------------
# Text-triggered cancel (phrase detection)
# ---------------------------------------------------------------------------


class TestTextTriggeredCancel:
    @pytest.mark.asyncio
    async def test_cancel_text_no_sr_id_clears_state(self) -> None:
        state = _state(
            user_message="cancel",
            backend_refs={},
        )
        result = await handover_entry_node(state)
        assert result.get("active_agent") is None
        assert result.get("response_ui", {}).get("cancelled") is True

    @pytest.mark.asyncio
    async def test_cancel_text_with_sr_id_emits_cancel_pending(self) -> None:
        state = _state(
            user_message="cancel this request",
            backend_refs={"sr_id": "SR-004"},
        )
        result = await handover_entry_node(state)
        assert result.get("confirmation_status") == "CANCEL_PENDING"

    @pytest.mark.asyncio
    async def test_cancel_pending_yes_text_confirms_cancel(self) -> None:
        mock_svc = MagicMock()
        mock_svc.patch_service_request = AsyncMock(return_value=None)

        with patch(
            "app.agents.graph.nodes.handover.handover_entry_node.get_service_request_api_service",
            return_value=mock_svc,
        ):
            state = _state(
                user_message="yes",
                confirmation_status="CANCEL_PENDING",
                backend_refs={"sr_id": "SR-005"},
            )
            result = await handover_entry_node(state)

        assert result.get("response_ui", {}).get("cancelled") is True
        assert result.get("collected_data") == {}

    @pytest.mark.asyncio
    async def test_cancel_pending_no_text_declines_cancel(self) -> None:
        state = _state(
            user_message="no",
            confirmation_status="CANCEL_PENDING",
            backend_refs={"sr_id": "SR-006"},
        )
        result = await handover_entry_node(state)
        assert result.get("confirmation_status") is None
        assert result.get("status") == "IN_PROGRESS"

    @pytest.mark.asyncio
    async def test_cancel_pending_ambiguous_asks_again(self) -> None:
        state = _state(
            user_message="maybe",
            confirmation_status="CANCEL_PENDING",
            backend_refs={"sr_id": "SR-007"},
        )
        result = await handover_entry_node(state)
        assert result.get("status") == "WAITING_FOR_USER"
        assert result.get("response_message") is not None


# ---------------------------------------------------------------------------
# State after confirmed cancel
# ---------------------------------------------------------------------------


class TestStateAfterConfirmedCancel:
    @pytest.mark.asyncio
    async def test_all_transient_fields_zeroed_after_cancel(self) -> None:
        mock_svc = MagicMock()
        mock_svc.patch_service_request = AsyncMock(return_value=None)

        with patch(
            "app.agents.graph.nodes.handover.handover_entry_node.get_service_request_api_service",
            return_value=mock_svc,
        ):
            state = _state(
                action_override="confirm_cancel_sr",
                backend_refs={"sr_id": "SR-009"},
                collected_data={"lease_code": "LC-1", "mall": "Mall A"},
                confirmation_status="CANCEL_PENDING",
            )
            result = await handover_entry_node(state)

        assert result.get("collected_data") == {}
        assert result.get("active_agent") is None
        assert result.get("workflow_stage") == "CANCELLED"
        assert result.get("confirmation_status") is None
        assert result.get("validation_errors") == []
        assert result.get("missing_fields") == []
        assert result.get("backend_refs") == {}, "backend_refs must be cleared on cancel"
        assert result.get("documents") == [], "documents must be cleared on cancel"
