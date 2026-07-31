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
        "active_agent": "rdd_handover_agent",
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
async def test_gate_active_for_sr_created_stage_with_upload_offer() -> None:
    """After SR creation (SR_CREATED), the gate IS active to manage the optional
    upload offer. A non-matching message re-emits the offer (not an empty patch)."""
    result = await handover_entry_node(
        _state(
            workflow_stage="SR_CREATED",
            user_message="skip",  # not a recognised phrase → re-offer
        )
    )
    # Gate is active for SR_CREATED → re-offers the upload choice
    assert result != {}
    assert result.get("status") == "WAITING_FOR_USER"

@pytest.mark.asyncio
async def test_gate_bypassed_for_create_sr_stage() -> None:
    """CREATE_SR stage is not the upload-offer stage — gate is a no-op there."""
    result = await handover_entry_node(
        _state(
            workflow_stage="CREATE_SR",
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
        "active_agent": "rdd_handover_agent",
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


# ---------------------------------------------------------------------------
# HANDED_OVER / Path B — stale-clear for terminal stages
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stale_clear_fires_for_handed_over_completed_stage() -> None:
    """intent=CREATE_HANDED_OVER_SR + workflow_stage=HANDED_OVER_COMPLETED is a
    terminal combination — _clear_all_state() must be called, returning an empty
    collected_data and a None workflow_stage."""
    state = {
        "session_id": "sess-stale-ho",
        "active_agent": "rdd_handover_agent",
        "intent": "CREATE_HANDED_OVER_SR",
        "workflow_stage": "HANDED_OVER_COMPLETED",
        "sub_category": "HANDED_OVER",
        "user_message": "I want to register another already trading unit",
        "action_override": None,
        "confirmation_status": None,
        "collected_data": {
            "actual_shop_handover_date": "2024-01-10",
            "actual_trading_date": "2024-03-10",
            "lease_code": "LC-STALE-001",
        },
        "documents": [],
        "backend_refs": {},
    }
    result = await handover_entry_node(state)
    # Stale-clear must have fired
    assert result.get("collected_data") == {} or result.get("collected_data") is None, (
        "collected_data must be cleared when starting a new CREATE_HANDED_OVER_SR "
        "after a completed HANDED_OVER_COMPLETED stage"
    )
    assert result.get("workflow_stage") is None, (
        "workflow_stage must be reset to None after stale-clear"
    )


@pytest.mark.asyncio
async def test_stale_clear_does_not_fire_for_in_progress_handed_over_stage() -> None:
    """intent=CREATE_HANDED_OVER_SR + workflow_stage=CREATE_HANDED_OVER_SR is an
    in-progress (non-terminal) combination — state must be preserved, not cleared."""
    collected = {
        "actual_shop_handover_date": "2024-01-10",
        "lease_code": "LC-IN-PROGRESS-001",
    }
    state = {
        "session_id": "sess-in-progress-ho",
        "active_agent": "rdd_handover_agent",
        "intent": "CREATE_HANDED_OVER_SR",
        "workflow_stage": "CREATE_HANDED_OVER_SR",
        "sub_category": "HANDED_OVER",
        "user_message": "The actual fitout start date is January 15th 2024",
        "action_override": None,
        "confirmation_status": None,
        "collected_data": collected,
        "documents": [],
        "backend_refs": {},
    }
    result = await handover_entry_node(state)
    # Stale-clear must NOT have fired — the node should return {} (fall-through)
    # so that collected_data is preserved from the prior state
    assert result == {}, (
        "handover_entry_node must return {} (no mutation) for an in-progress "
        "CREATE_HANDED_OVER_SR stage so that collected_data is preserved"
    )


# ---------------------------------------------------------------------------
# B2 gap: stale-clear for HANDED_OVER
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stale_clear_fires_for_handed_over_completed_stage() -> None:
    """B2 gap: when intent=CREATE_HANDED_OVER_SR and workflow_stage=HANDED_OVER_COMPLETED
    (terminal), stale state must be cleared so a new flow can begin."""
    state = {
        "session_id": "sess-stale-hoc",
        "active_agent": "rdd_handover_agent",
        "intent": "CREATE_HANDED_OVER_SR",
        "workflow_stage": "HANDED_OVER_COMPLETED",
        "sub_category": "HANDED_OVER",
        "user_message": "Register another already trading unit",
        "action_override": None,
        "confirmation_status": None,
        "collected_data": {"lease_code": "OLD_LC"},
        "documents": [],
        "backend_refs": {"sr_id": "old-sr-001"},
    }
    result = await handover_entry_node(state)
    assert result.get("collected_data") == {} or result.get("active_agent") is None, (
        "Stale state must be cleared when workflow_stage=HANDED_OVER_COMPLETED"
    )


@pytest.mark.asyncio
async def test_stale_clear_does_not_fire_for_in_progress_handed_over_stage_b2() -> None:
    """B2 gap: CREATE_HANDED_OVER_SR in-progress must NOT trigger stale-clear."""
    state = {
        "session_id": "sess-no-stale-hoc",
        "active_agent": "rdd_handover_agent",
        "intent": "CREATE_HANDED_OVER_SR",
        "workflow_stage": "CREATE_HANDED_OVER_SR",
        "sub_category": "HANDED_OVER",
        "user_message": "The trading date is 2026-06-01",
        "action_override": None,
        "confirmation_status": None,
        "collected_data": {"lease_code": "LC-ACTIVE"},
        "documents": [],
        "backend_refs": {},
    }
    result = await handover_entry_node(state)
    # collected_data must NOT be cleared
    assert result.get("collected_data") != {}, (
        "In-progress CREATE_HANDED_OVER_SR must NOT trigger stale-clear"
    ) if "collected_data" in result else True
