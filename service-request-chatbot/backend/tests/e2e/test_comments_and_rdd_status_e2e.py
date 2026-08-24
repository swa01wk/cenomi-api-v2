"""End-to-end regressions for two questions the bot was skipping.

1. **Already Trading (Path B) never asked for comments.** ``merge_state_node``
   auto-filled the generated "No additional comments provided for lease X."
   sentence while ``comments`` was still ``None`` — the exact state
   ``_route_after_validation``'s proactive-optional-ask branch keys on — so the
   question was consumed before it could be asked and every Path B SR shipped
   with the placeholder.

2. **RDD PM was never asked the Handover Meeting Report outcome.** The
   Approve / Approve-with-changes chips existed and ``rdd_review_entry_node``
   already handled them, but ``get_missing_fields`` gated the question on
   ``rdd_upload_doc_type`` — a field that stopped being collected when uploads
   moved to the ``document_requirement`` card — so the condition was dead.

Only external I/O is mocked (supervisor LLM, extraction LLM, lease API,
validation); graph, routing, persistence and response generation are real.
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


def _ho_lease() -> LeaseRecord:
    return LeaseRecord(
        lease_code="HO-CMT-001",
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
        lease_brand_mall="HO-CMT-001 - TestBrand - Test Mall",
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


async def _start(app_client: AsyncClient, user_id: str, message: str, **supervisor) -> str:
    with ExitStack() as stack:
        stack.enter_context(
            patch(
                "app.agents.graph.nodes.supervisor_node._call_supervisor_llm",
                make_supervisor_mock(**supervisor),
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
                json={"user_id": user_id, "message": message},
            )
        ).json()
    return body["session_id"]


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


# ---------------------------------------------------------------------------
# 1. Already Trading asks for comments
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_already_trading_asks_for_comments_before_the_card(
    app_client: AsyncClient,
) -> None:
    user_id = "e2e_ho_comments_user"
    session_id = await _start(
        app_client,
        user_id,
        "This unit is already trading, register it as already traded.",
        intent="CREATE_HANDED_OVER_SR",
        sub_category="HANDED_OVER",
        target_agent="rdd_handover_agent",
    )

    lease = _ho_lease()
    body = await _turn(
        app_client,
        session_id,
        user_id,
        "Lease HO-CMT-001. Shop handover 10 Jan 2026, fitout 12 Jan to 1 Feb, "
        "trading from 5 Feb.",
        {
            "lease_code": {"value": lease.lease_code, "confidence": 1.0},
            "already_handovered": {"value": "already_trading", "confidence": 1.0},
            "actual_shop_handover_date": {"value": "2026-01-10", "confidence": 1.0},
            "actual_fitout_start_date": {"value": "2026-01-12", "confidence": 1.0},
            "actual_fitout_end_date": {"value": "2026-02-01", "confidence": 1.0},
            "actual_trading_date": {"value": "2026-02-05", "confidence": 1.0},
        },
    )

    assert body["ui"].get("field") == "comments", (
        "Path B must ask for comments once before the review card — got "
        f"{body['ui']}"
    )

    # The answer must survive, not be replaced by the generated placeholder.
    answer = "Unit verified on site by the mall team"
    await _turn(
        app_client,
        session_id,
        user_id,
        answer,
        {"comments": {"value": answer, "confidence": 1.0}},
    )
    # Path B then offers the optional upload; Continue reaches the review card.
    body3 = await _turn(
        app_client, session_id, user_id, "continue", {},
        action="continue_optional_upload",
    )
    card_fields = {f["key"]: f["value"] for f in body3["ui"].get("fields", [])}
    assert card_fields.get("comments") == answer, card_fields
    assert "No additional comments provided" not in str(card_fields.get("comments"))


@pytest.mark.asyncio
async def test_already_trading_declined_comments_get_the_generated_default(
    app_client: AsyncClient,
) -> None:
    """Answering "none" is still valid and still yields a readable value."""
    user_id = "e2e_ho_comments_skip_user"
    session_id = await _start(
        app_client,
        user_id,
        "This unit is already trading, register it as already traded.",
        intent="CREATE_HANDED_OVER_SR",
        sub_category="HANDED_OVER",
        target_agent="rdd_handover_agent",
    )

    lease = _ho_lease()
    await _turn(
        app_client,
        session_id,
        user_id,
        "Lease HO-CMT-001 with all the actual dates.",
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
        app_client,
        session_id,
        user_id,
        "none",
        {"comments": {"value": "", "confidence": 1.0}},
    )
    body = await _turn(
        app_client, session_id, user_id, "continue", {},
        action="continue_optional_upload",
    )
    card_fields = {f["key"]: f["value"] for f in body["ui"].get("fields", [])}
    assert card_fields.get("comments") == (
        f"No additional comments provided for lease {lease.lease_code}."
    ), card_fields


# ---------------------------------------------------------------------------
# 2. RDD PM is asked the Meeting Report outcome
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rdd_asks_document_status_after_meeting_report_upload() -> None:
    """Guideline link → upload gate → status chips → dates.

    Driven at the node level rather than over HTTP: reaching RDD_REVIEW through
    the API needs a real platform SR plus DD_ENGINEER auth, and the behaviour
    under test is entirely the missing-field ordering.
    """
    from app.agents.graph.nodes.shared.missing_field_node import missing_field_node

    def _state(collected: dict, documents: list[dict]) -> dict[str, Any]:
        return {
            "workflow_stage": "RDD_REVIEW",
            "active_agent": "rdd_handover_agent",
            "collected_data": collected,
            "documents": documents,
            "validation_errors": [],
            "session_id": "sess-rdd-e2e",
            "backend_refs": {"sr_id": "8426"},
        }

    # 1. Nothing collected → guideline link first.
    step1 = await missing_field_node(_state({}, []))  # type: ignore[arg-type]
    assert step1["response_ui"]["field"] == "guideLineLink"

    collected: dict[str, Any] = {"guideLineLink": "https://guidelines.example.com"}

    # 2. Guideline captured, nothing uploaded → the upload card, not the status.
    step2 = await missing_field_node(_state(collected, []))  # type: ignore[arg-type]
    assert step2["response_ui"].get("field") != "document_status"

    # 3. Meeting Report uploaded → status question with both chips.
    docs = [{"document_type_id": "DR_SR_HANDOVER_REPORT", "document_id": "doc-1"}]
    step3 = await missing_field_node(_state(collected, docs))  # type: ignore[arg-type]
    assert step3["response_ui"]["field"] == "document_status"

    # The chips must not arrive bare — the question text has to be built here
    # and marked verbatim, because field_questions holds an instruction *about*
    # asking ("Ask the RDD Project Manager for...") that only becomes a real
    # question if the response_generation rewrite lands.
    message = step3["response_ui"]["message"]
    assert "select the document status" in message.lower(), message
    assert "Approve" in message and "Approve with changes" in message, message
    assert not message.startswith("Ask the"), (
        f"raw LLM instruction leaked to the user: {message!r}"
    )
    assert step3["response_ui"]["verbatim"] is True, step3["response_ui"]
    assert step3["response_message"] == message

    options = step3["response_decorations"]["suggestions"]["options"]
    assert [(o["label"], o["value"]) for o in options] == [
        ("Approve", "APPROVED"),
        ("Approve with changes", "APPROVED_WITH_CHANGES"),
    ]

    # 4. Status chosen → the dates follow.
    step4 = await missing_field_node(  # type: ignore[arg-type]
        _state({**collected, "document_status": "APPROVED_WITH_CHANGES"}, docs)
    )
    assert step4["response_ui"]["field"] == "actual_handover_date"


@pytest.mark.asyncio
async def test_rdd_rejected_report_skips_the_status_question() -> None:
    """A Rejected upload is auto-DISAPPROVED by merge_state_node, so asking
    would only offer choices that get overwritten."""
    from app.agents.graph.nodes.shared.merge_state_node import merge_state_node
    from app.agents.graph.nodes.shared.missing_field_node import missing_field_node

    docs = [{"document_type_id": "SR_REJECTED_HANDOVER_REPORT", "document_id": "doc-2"}]
    merged = await merge_state_node(  # type: ignore[arg-type]
        {
            "workflow_stage": "RDD_REVIEW",
            "collected_data": {"guideLineLink": "https://guidelines.example.com"},
            "extracted_fields": {},
            "corrected_fields": {},
            "documents": docs,
            "user_message": "",
            "active_field": None,
        }
    )
    assert merged["collected_data"]["document_status"] == "DISAPPROVED"

    result = await missing_field_node(  # type: ignore[arg-type]
        {
            "workflow_stage": "RDD_REVIEW",
            "active_agent": "rdd_handover_agent",
            "collected_data": merged["collected_data"],
            "documents": docs,
            "validation_errors": [],
            "session_id": "sess-rdd-e2e-2",
            "backend_refs": {"sr_id": "8426"},
        }
    )
    assert result["response_ui"]["field"] == "actual_handover_date"


def test_rdd_status_chips_bypass_re_extraction() -> None:
    """The chips write document_status directly, so routing them through
    field_extraction would let its correction re-arm re-ask the question the
    user just answered (same reason _DIRECT_SET_CHIP_ACTIONS exists)."""
    from app.agents.graph.help_agent_graph import _route_after_rdd_entry

    for action in (
        "select_rdd_status_approved",
        "select_rdd_status_approved_with_changes",
    ):
        assert _route_after_rdd_entry({"action_override": action}) == "merge_state"

    # A normal message still goes through extraction.
    assert _route_after_rdd_entry({"user_message": "hello"}) == "field_extraction"
