"""A lease that exists but cannot be used must not be reported as not found.

Reported live: the handover form's own Lease ID list offered
``t0099290 - Brand Popeyes Restaurant - Salaam Mall``, and typing that code to
the assistant answered

    Sorry, I couldn't find a lease matching that code. Please double-check the
    lease code, brand name, company name, or mall name you provided.

The code was correct — the user had just picked it from the form. The platform
withheld the lease because it has no activation date on record and returned
``reason=activation_pending``; only ``handover_exists`` was ever mapped, so
every other reason fell through to the not-found wording and sent the user
back to re-check a code that was right.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.graph.nodes.shared.lease_lookup_node import lease_lookup_node

_LOOKUP = "app.agents.graph.nodes.shared.lease_lookup_node.get_lease_lookup_service"


def _empty_result(reason: str | None) -> MagicMock:
    result = MagicMock()
    result.matches = []
    result.reason = reason
    result.endpoint = "mock://leases"
    result.request_payload = {}
    result.response_payload = {}
    result.latency_ms = 5
    result.status_code = 200
    result.error = None
    return result


def _service(reason: str | None) -> MagicMock:
    svc = AsyncMock()
    svc.lookup = AsyncMock(return_value=_empty_result(reason))
    return MagicMock(return_value=svc)


async def _message(reason: str | None) -> str:
    state: dict[str, Any] = {
        "workflow_stage": "CREATE_SR",
        "active_agent": "rdd_handover_agent",
        "sub_category": "HANDOVER",
        "collected_data": {"lease_code": "t0099290"},
        "documents": [],
        "validation_errors": [],
    }
    with patch(_LOOKUP, _service(reason)):
        result = await lease_lookup_node(state)  # type: ignore[arg-type]
    return str(result.get("response_message") or "")


@pytest.mark.asyncio
async def test_an_unactivated_lease_is_not_reported_as_missing() -> None:
    message = await _message("activation_pending")
    assert "not activated" in message
    # The instruction tells the model what NOT to say, so assert on intent:
    # this must be the activation branch, not the not-found one.
    assert "zero matches" not in message
    assert "double-check the lease code, brand name" not in message


@pytest.mark.asyncio
async def test_any_other_reason_says_ineligible_rather_than_missing() -> None:
    # A reason this code has never seen still means the lease exists.
    message = await _message("some_new_platform_rule")
    assert "not currently eligible" in message
    assert "zero matches" not in message


@pytest.mark.asyncio
async def test_a_genuinely_unmatched_code_still_says_not_found() -> None:
    # No reason from the platform is a real miss, and the user should re-check.
    message = await _message(None)
    assert "zero matches" in message or "double-check" in message


@pytest.mark.asyncio
async def test_the_lease_code_question_is_re_asked_either_way() -> None:
    state: dict[str, Any] = {
        "workflow_stage": "CREATE_SR",
        "active_agent": "rdd_handover_agent",
        "sub_category": "HANDOVER",
        "collected_data": {"lease_code": "t0099290"},
        "documents": [],
        "validation_errors": [],
    }
    with patch(_LOOKUP, _service("activation_pending")):
        result = await lease_lookup_node(state)  # type: ignore[arg-type]
    assert (result.get("response_ui") or {}).get("field") == "lease_code"
    assert result.get("status") == "WAITING_FOR_USER"
