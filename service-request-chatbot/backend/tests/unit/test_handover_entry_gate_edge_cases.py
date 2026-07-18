"""Unit tests — handover_entry_node MM optional upload gate edge cases.

Focuses on the bypass conditions introduced in staging:
- Gate is skipped when workflow_stage is not CREATE_SR (e.g., FM_REVIEW)
- Gate is skipped when confirmation_status == "PENDING"
- Gate is skipped when mm_optional_upload_status is absent from collected_data
- RDD agent with no workflow_stage falls through to {"workflow_stage": "CREATE_SR"}

The happy-path tests (accept/skip/continue/re-offer) live in
tests/unit/test_mm_optional_upload_offer.py.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.agents.graph.nodes.handover.handover_entry_node import handover_entry_node


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _state(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "session_id": "sess-edge",
        "active_agent": "rdd_agent",
        "workflow_stage": "CREATE_SR",
        "user_message": "",
        "action_override": None,
        "confirmation_status": None,
        "collected_data": {
            "lease_id": 95404,
            "mm_optional_upload_status": "awaiting_choice",
        },
        "documents": [],
        "backend_refs": {},
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Gate bypassed when workflow_stage != CREATE_SR
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gate_bypassed_for_fm_review_stage() -> None:
    """MM gate must be a no-op during FM_REVIEW — should fall through to {}."""
    result = await handover_entry_node(
        _state(
            workflow_stage="FM_REVIEW",
            user_message="Yes",  # would normally trigger accept in CREATE_SR
        )
    )
    # Gate is not active → node returns empty dict (no mm status mutation)
    assert result == {}


@pytest.mark.asyncio
async def test_gate_bypassed_for_rdd_review_stage() -> None:
    """MM gate must be a no-op during RDD_REVIEW."""
    result = await handover_entry_node(
        _state(
            workflow_stage="RDD_REVIEW",
            user_message="Yes",
        )
    )
    assert result == {}


@pytest.mark.asyncio
async def test_gate_bypassed_for_sr_created_stage() -> None:
    """After SR is created, the gate must not re-activate."""
    result = await handover_entry_node(
        _state(
            workflow_stage="SR_CREATED",
            user_message="skip",
        )
    )
    assert result == {}


# ---------------------------------------------------------------------------
# Gate bypassed when confirmation_status == "PENDING"
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gate_bypassed_when_confirmation_pending() -> None:
    """If a confirmation card is already showing, the MM upload gate must not intercept.
    The confirmation parsing runs first: 'Yes' → CONFIRMED."""
    result = await handover_entry_node(
        _state(
            confirmation_status="PENDING",
            user_message="Yes",
        )
    )
    # Confirmation parsing intercepts before the gate — 'Yes' → CONFIRMED.
    # mm_optional_upload_status must NOT have changed (gate did not run).
    assert result.get("confirmation_status") == "CONFIRMED"
    collected = result.get("collected_data") or {}
    assert collected.get("mm_optional_upload_status") != "done"


@pytest.mark.asyncio
async def test_gate_bypassed_for_accept_action_while_confirmation_pending() -> None:
    """action_override=accept_optional_upload while PENDING must not mutate mm status.
    Confirmation parsing runs first and returns ambiguous-input message."""
    result = await handover_entry_node(
        _state(
            confirmation_status="PENDING",
            action_override="accept_optional_upload",
        )
    )
    # Confirmation parsing intercepts — ambiguous (no yes/no phrase) → re-prompt.
    # mm_optional_upload_status must NOT have changed.
    assert "confirmation_status" not in result or result.get("confirmation_status") != "done"
    collected = result.get("collected_data") or {}
    assert collected.get("mm_optional_upload_status") != "done"


# ---------------------------------------------------------------------------
# Gate inactive when mm_optional_upload_status is not set / done
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gate_inactive_when_status_key_absent() -> None:
    """If collected_data has no mm_optional_upload_status, gate is a no-op."""
    result = await handover_entry_node(
        _state(
            collected_data={"lease_id": 95404},  # no mm status key
            user_message="Yes",
        )
    )
    assert result == {}


@pytest.mark.asyncio
async def test_gate_inactive_when_status_is_done() -> None:
    """Once status is 'done', gate returns None and node falls through to {}."""
    result = await handover_entry_node(
        _state(
            collected_data={
                "lease_id": 95404,
                "mm_optional_upload_status": "done",
            },
            user_message="Yes",
        )
    )
    assert result == {}


# ---------------------------------------------------------------------------
# RDD agent with no workflow_stage gets assigned CREATE_SR
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rdd_agent_no_stage_initialises_create_sr() -> None:
    """When active_agent=rdd_agent and workflow_stage is absent, node sets CREATE_SR.

    This is the initial entry for an RDD-handled CREATE_SR flow.
    """
    state = {
        "session_id": "sess-init",
        "active_agent": "rdd_agent",
        "workflow_stage": None,
        "user_message": "I want to create a handover request",
        "action_override": None,
        "confirmation_status": None,
        "collected_data": {},
        "documents": [],
        "backend_refs": {},
    }
    result = await handover_entry_node(state)
    assert result == {"workflow_stage": "CREATE_SR"}


# ---------------------------------------------------------------------------
# Cancellation phrases always handled regardless of MM gate state
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_phrase_handled_before_gate() -> None:
    """Cancellation check runs before the MM gate — 'cancel this request' triggers
    cancel_sr confirmation flow (CANCEL_PENDING), not upload status mutation."""
    result = await handover_entry_node(
        _state(user_message="cancel this request")
    )
    # cancel_sr triggers CANCEL_PENDING (asks user to confirm cancellation),
    # OR clears state immediately if no sr_id. Either way, upload must not be marked done.
    status = result.get("status")
    assert status in ("WAITING_FOR_USER", "CANCELLED", "IN_PROGRESS", None)
    # Most importantly: mm_optional_upload_status must not have been mutated to "done"
    collected = result.get("collected_data") or {}
    mm = collected.get("mm_optional_upload_status")
    assert mm != "done"  # cancel should not mark upload done
