"""Unit tests — handover_entry_node: cancel flow (Fix 1).

Tests cover:
- cancel before SR created (CANCEL_PENDING Yes/No — never clear immediately)
- cancel after SR created (CANCEL_PENDING emitted)
- confirm_cancel_sr action (state cleared; cancelled banner only when sr_id exists)
- decline_cancel_sr action (preview card restored when fields are complete)
- text-triggered cancel phrases (same flow as cancel_sr UI action)
- confirmation-card Cancel is cancel-SR, not "make changes"
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
    async def test_cancel_sr_action_no_sr_id_asks_confirmation(self) -> None:
        """cancel_sr when no sr_id → CANCEL_PENDING, collected data kept."""
        state = _state(
            action_override="cancel_sr",
            backend_refs={},
            collected_data={"lease_code": "t0031371"},
        )
        result = await handover_entry_node(state)
        assert result.get("confirmation_status") == "CANCEL_PENDING"
        assert result.get("status") == "WAITING_FOR_USER"
        assert "sure" in result.get("response_message", "").lower()
        assert result.get("response_ui", {}).get("cancelled") is not True
        assert "collected_data" not in result or result.get("collected_data") != {}

    @pytest.mark.asyncio
    async def test_cancel_alias_no_sr_id_asks_confirmation(self) -> None:
        state = _state(action_override="cancel", backend_refs={})
        result = await handover_entry_node(state)
        assert result.get("confirmation_status") == "CANCEL_PENDING"
        assert result.get("response_ui", {}).get("cancelled") is not True

    @pytest.mark.asyncio
    async def test_cancel_create_sr_alias_no_sr_id_asks_confirmation(self) -> None:
        state = _state(action_override="cancel_create_sr", backend_refs={})
        result = await handover_entry_node(state)
        assert result.get("confirmation_status") == "CANCEL_PENDING"

    @pytest.mark.asyncio
    async def test_confirmation_card_cancel_is_not_make_changes(self) -> None:
        """Review-card Cancel asks are-you-sure; it does not REJECT (make changes)."""
        state = _state(
            action_override="cancel",
            confirmation_status="PENDING",
            backend_refs={},
            collected_data={"lease_code": "t0031371", "mall": "Hamraa Mall"},
        )
        result = await handover_entry_node(state)
        assert result.get("confirmation_status") == "CANCEL_PENDING"
        assert result.get("confirmation_status") != "REJECTED"


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
        assert result.get("response_ui", {}).get("reset_conversation") is True
        assert result.get("response_ui", {}).get("workflow_stage") == "CANCELLED"
        assert "cleared" in result.get("response_message", "").lower()
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
        """confirm_cancel_sr with no sr_id → skip API call, clear lease, prompt for a new one."""
        mock_svc = MagicMock()
        mock_svc.patch_service_request = AsyncMock(return_value=None)

        with patch(
            "app.agents.graph.nodes.handover.handover_entry_node.get_service_request_api_service",
            return_value=mock_svc,
        ):
            state = _state(
                action_override="confirm_cancel_sr",
                backend_refs={},
                collected_data={"lease_code": "t0031371"},
            )
            result = await handover_entry_node(state)

        mock_svc.patch_service_request.assert_not_called()
        assert result.get("collected_data") == {}
        assert result.get("selected_lease") is None
        assert result.get("response_ui", {}).get("cancelled") is not True
        assert result.get("response_ui", {}).get("reset_conversation") is True
        suggestions = result.get("response_ui", {}).get("suggestions") or {}
        labels = [o.get("label") for o in suggestions.get("options") or []]
        assert "Create handover SR" in labels
        assert any(o.get("action") == "create_handover_sr" for o in suggestions.get("options") or [])
        assert any(
            o.get("value") == "I want to create a Handover service request"
            for o in suggestions.get("options") or []
        )
        assert "continue" in result.get("response_message", "").lower()

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
        # Incomplete draft → resume collection; collected_data must not be cleared
        assert result.get("confirmation_status") is None
        assert result.get("status") == "IN_PROGRESS"
        assert "collected_data" not in result or result.get("collected_data") != {}

    @pytest.mark.asyncio
    async def test_decline_cancel_sr_restores_workflow(self) -> None:
        state = _state(
            action_override="decline_cancel_sr",
            confirmation_status="CANCEL_PENDING",
            backend_refs={"sr_id": "SR-001"},
        )
        result = await handover_entry_node(state)
        assert result.get("status") in ("IN_PROGRESS", "WAITING_FOR_USER")
        assert result.get("response_message") is not None

    @pytest.mark.asyncio
    async def test_decline_cancel_sr_reshows_confirmation_card(self) -> None:
        """No on are-you-sure → show the review card again with lease data intact."""
        collected = {
            "tenant_profile_id": "TP-001",
            "property_id": "PROP-001",
            "lease_code": "t0031371",
            "lease_id": "LEASE-001",
            "brand_id": "BR-001",
            "mall": "Hamraa Mall",
            "brand": "Brand Kudu Restaurant",
            "lease": "t0031371",
            "unit_codes": ["FCW01"],
            "city": "Riyadh",
            "contracted_area": 63,
            "title": "handover-t0031371",
            "description": "Handover inspection",
            "already_handovered": "initiate_handover",
            "startDate": "2026-08-20",
            "endDate": "2026-08-21",
            "inspectionTime": "08:00",
            "inspection_done_by": "FM_MANAGER",
            "notes": "",
            "comments": "",
        }
        state = _state(
            action_override="decline_cancel_sr",
            confirmation_status="CANCEL_PENDING",
            collected_data=collected,
            backend_refs={},
        )
        result = await handover_entry_node(state)
        assert result.get("confirmation_status") == "PENDING"
        assert result.get("response_ui", {}).get("type") == "confirmation_card"
        assert "collected_data" not in result or result.get("collected_data") != {}


# ---------------------------------------------------------------------------
# Text-triggered cancel (phrase detection)
# ---------------------------------------------------------------------------


class TestTextTriggeredCancel:
    @pytest.mark.asyncio
    async def test_cancel_text_no_sr_id_asks_confirmation(self) -> None:
        state = _state(
            user_message="cancel",
            backend_refs={},
            collected_data={"lease_code": "t0031371"},
        )
        result = await handover_entry_node(state)
        assert result.get("confirmation_status") == "CANCEL_PENDING"
        assert result.get("response_ui", {}).get("cancelled") is not True

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
        assert result.get("confirmation_status") in (None, "PENDING")
        assert result.get("status") in ("IN_PROGRESS", "WAITING_FOR_USER")
        assert result.get("collected_data") != {} or "collected_data" not in result

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


# ---------------------------------------------------------------------------
# Milestone already-confirmed chips
# ---------------------------------------------------------------------------


class TestMilestoneContinueCancel:
    @pytest.mark.asyncio
    async def test_continue_milestone_acks_and_keeps_lease(self) -> None:
        state = _state(
            action_override="continue_milestone",
            confirmation_status="MILESTONE_PENDING",
            collected_data={"lease_code": "t0031371", "lease_id": 42},
        )
        result = await handover_entry_node(state)
        assert result.get("confirmation_status") is None
        assert result.get("status") == "IN_PROGRESS"
        assert result["collected_data"]["_milestone_confirmed_ack"] is True
        assert result["collected_data"]["lease_id"] == 42

    @pytest.mark.asyncio
    async def test_cancel_milestone_clears_draft(self) -> None:
        state = _state(
            action_override="cancel_milestone",
            confirmation_status="MILESTONE_PENDING",
            collected_data={"lease_code": "t0031371", "lease_id": 42},
        )
        result = await handover_entry_node(state)
        assert result.get("collected_data") == {}
        assert result.get("response_ui", {}).get("reset_conversation") is True
        assert "cleared" in result.get("response_message", "").lower()

    @pytest.mark.asyncio
    async def test_typed_continue_while_pending_acks(self) -> None:
        state = _state(
            user_message="continue",
            confirmation_status="MILESTONE_PENDING",
            collected_data={"lease_id": 42},
        )
        result = await handover_entry_node(state)
        assert result["collected_data"]["_milestone_confirmed_ack"] is True
        assert result.get("status") == "IN_PROGRESS"

    @pytest.mark.asyncio
    async def test_typed_cancel_while_pending_clears_without_yes_no(self) -> None:
        state = _state(
            user_message="cancel",
            confirmation_status="MILESTONE_PENDING",
            collected_data={"lease_id": 42},
        )
        result = await handover_entry_node(state)
        assert result.get("confirmation_status") != "CANCEL_PENDING"
        assert result.get("collected_data") == {}
        assert result.get("response_ui", {}).get("reset_conversation") is True

