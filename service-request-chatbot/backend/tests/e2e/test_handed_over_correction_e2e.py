"""End-to-end regression: field corrections in the Already Trading (Path B) flow.

Path B collects four "actual" dates plus comments, and the user must be able to
revisit any of them exactly as they can in the standard handover flow.

The synonym table in field_correction_service is written for the standard
handover / RDD path, which has its own ``actual_handover_date``,
``fitout_start_date`` and ``fitout_end_date``. Path B stores the same real-world
dates under ``actual_shop_handover_date``, ``actual_fitout_start_date`` and
``actual_fitout_end_date``, so a redirect phrased the natural way resolved to a
field this draft never collects — the bot then asked "What would you like to set
as the Actual Handover Date?" (note *set*, not *change*: the field had no value
because it is not part of Path B) and the user's real edit went nowhere.
"""

from __future__ import annotations

from contextlib import ExitStack
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from httpx import AsyncClient

from app.agents.services.lease_lookup_service import LeaseRecord
from tests.e2e.helpers import (
    make_field_extraction_mock,
    make_lease_mock,
    make_supervisor_mock,
)

_USER_ID = "e2e_ho_correction_user"


def _ho_lease() -> LeaseRecord:
    return LeaseRecord(
        lease_code="HO-FIX-001",
        lease_id=8001,
        contract_id=7001,
        brand="TestBrand",
        brand_id=99,
        mall="Test Mall",
        property_id=3020,
        tenant_profile_id=88,
        unit_codes=["TM-101"],
        contracted_area=200.0,
        city="Dubai",
        lease_brand_mall="HO-FIX-001 - TestBrand - Test Mall",
    )


def _patches(fields: dict) -> tuple:
    return (
        patch(
            "app.agents.graph.nodes.shared.field_extraction_node.FieldExtractionService",
            make_field_extraction_mock(fields),
        ),
        patch(
            "app.agents.graph.nodes.shared.field_extraction_node.get_default_gateway",
            return_value=MagicMock(model="gpt-4o-mini"),
        ),
        patch(
            "app.agents.graph.nodes.shared.lease_lookup_node.get_lease_lookup_service",
            make_lease_mock([_ho_lease()]),
        ),
        patch(
            "app.agents.graph.nodes.shared.validation_node._validation_service.validate_draft",
            MagicMock(return_value=[]),
        ),
    )


async def _drive_to_review_card(app_client: AsyncClient, user_id: str) -> str:
    """Create a complete Path B draft and stop at the review card."""
    with ExitStack() as stack:
        stack.enter_context(
            patch(
                "app.agents.graph.nodes.supervisor_node._call_supervisor_llm",
                make_supervisor_mock(
                    intent="CREATE_HANDED_OVER_SR",
                    sub_category="HANDED_OVER",
                    target_agent="rdd_handover_agent",
                ),
            )
        )
        stack.enter_context(
            patch(
                "app.agents.graph.nodes.supervisor_node.get_default_gateway",
                return_value=MagicMock(model="gpt-4o-mini"),
            )
        )
        for patcher in _patches({}):
            stack.enter_context(patcher)
        body = (
            await app_client.post(
                "/api/chat/service-request",
                json={
                    "user_id": user_id,
                    "message": "This unit is already trading, register it.",
                },
            )
        ).json()
    session_id: str = body["session_id"]

    lease = _ho_lease()
    await _turn(
        app_client,
        session_id,
        user_id,
        "Lease HO-FIX-001 with all four actual dates.",
        {
            "lease_code": {"value": lease.lease_code, "confidence": 1.0},
            "already_handovered": {"value": "already_trading", "confidence": 1.0},
            "actual_shop_handover_date": {"value": "2026-01-10", "confidence": 1.0},
            "actual_fitout_start_date": {"value": "2026-01-12", "confidence": 1.0},
            "actual_fitout_end_date": {"value": "2026-02-01", "confidence": 1.0},
            "actual_trading_date": {"value": "2026-02-05", "confidence": 1.0},
        },
    )
    await _turn(
        app_client, session_id, user_id, "none",
        {"comments": {"value": "", "confidence": 1.0}},
    )
    await _turn(
        app_client, session_id, user_id, "continue", {},
        action="continue_optional_upload",
    )
    return session_id


async def _turn(
    app_client: AsyncClient,
    session_id: str,
    user_id: str,
    message: str,
    fields: dict,
    **extra: Any,
) -> dict:
    with ExitStack() as stack:
        for patcher in _patches(fields):
            stack.enter_context(patcher)
        resp = await app_client.post(
            "/api/chat/service-request",
            json={
                "user_id": user_id,
                "session_id": session_id,
                "message": message,
                **extra,
            },
        )
    return resp.json()


# Every phrasing a Mall Manager plausibly uses for each Path B field, and the
# collected_data key it must resolve to.
_REDIRECTS: list[tuple[str, str]] = [
    ("i want to change the actual handover date", "actual_shop_handover_date"),
    ("i want to change the handover date", "actual_shop_handover_date"),
    ("i want to change the shop handover date", "actual_shop_handover_date"),
    ("i want to change the actual fitout start date", "actual_fitout_start_date"),
    ("i want to change the fitout start date", "actual_fitout_start_date"),
    ("i want to change the actual fitout end date", "actual_fitout_end_date"),
    ("i want to change the fitout end date", "actual_fitout_end_date"),
    ("i want to change the actual trading date", "actual_trading_date"),
    ("i want to change the trading date", "actual_trading_date"),
    ("i want to change the comments", "comments"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("message", "expected_field"), _REDIRECTS)
async def test_path_b_redirect_targets_the_field_the_flow_collects(
    app_client: AsyncClient, message: str, expected_field: str
) -> None:
    user_id = f"{_USER_ID}_{expected_field}_{len(message)}"
    session_id = await _drive_to_review_card(app_client, user_id)

    body = await _turn(app_client, session_id, user_id, message, {})

    assert body["ui"].get("field") == expected_field, (
        f"{message!r} must re-target {expected_field} — got {body['ui']}"
    )
    # "Current X: <value>. What would you like to change it to?" proves the bot
    # found an existing value. "What would you like to SET as ..." is the tell
    # that it targeted a field this draft never collected.
    assert "What would you like to change it to?" in body["message"], (
        f"expected a change prompt quoting the current value, got: {body['message']!r}"
    )


@pytest.mark.asyncio
async def test_path_b_corrected_date_is_stored_and_flow_resumes(
    app_client: AsyncClient,
) -> None:
    user_id = f"{_USER_ID}_roundtrip"
    session_id = await _drive_to_review_card(app_client, user_id)

    ask = await _turn(
        app_client, session_id, user_id, "i want to change the actual handover date", {}
    )
    assert ask["ui"].get("field") == "actual_shop_handover_date", ask["ui"]

    applied = await _turn(
        app_client,
        session_id,
        user_id,
        "05/01/2026",
        {"actual_shop_handover_date": {"value": "2026-01-05", "confidence": 1.0}},
    )
    card_fields = {f["key"]: f["value"] for f in applied["ui"].get("fields", [])}
    assert card_fields.get("actual_shop_handover_date") == "05/01/2026", card_fields
    # The other three dates must survive untouched.
    assert card_fields.get("actual_fitout_start_date") == "12/01/2026", card_fields
    assert card_fields.get("actual_fitout_end_date") == "01/02/2026", card_fields
    assert card_fields.get("actual_trading_date") == "05/02/2026", card_fields
