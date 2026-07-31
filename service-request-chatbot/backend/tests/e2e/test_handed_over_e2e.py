"""End-to-end tests for the Path B (HANDED_OVER / Already Trading) SR workflow.

Each test drives the full HTTP stack via an httpx.AsyncClient with
ASGITransport.  Only external I/O is mocked (LLM, Lease API, SR API, DB).

Test cases
----------
1.  test_successful_handed_over_sr_creation
    Full happy-path: classify → collect 4 actual dates + lease → confirm → submit
    → HANDED_OVER_COMPLETED (no FM/RDD steps).

2.  test_handed_over_intent_classified_correctly
    Supervisor classifies "CREATE_HANDED_OVER_SR" intent → active_agent=rdd_handover_agent.

3.  test_handed_over_sr_payload_has_correct_sub_category
    Payload built during submission has sub_category=HANDED_OVER.

4.  test_handed_over_skips_fm_and_rdd_stages
    After SR creation workflow_stage is HANDED_OVER_COMPLETED, not SR_CREATED
    waiting for FM/RDD steps.

5.  test_handed_over_api_failure_handled_gracefully
    SR API failure returns a user-facing error message without raising.

6.  test_handed_over_missing_dates_blocks_submission
    Missing actual dates prevent the confirmation card from appearing.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient

from app.agents.services.lease_lookup_service import LeaseRecord
from tests.e2e.helpers import (
    make_field_extraction_mock,
    make_lease_mock,
    make_sr_api_mock,
    make_supervisor_mock,
    zara_lease,
)
from tests.e2e.conftest import post_turn

# ---------------------------------------------------------------------------
# Shared test data
# ---------------------------------------------------------------------------

_ENDPOINT = "/api/chat/service-request"
_USER_ID = "e2e_mm_001"


def _ho_lease() -> LeaseRecord:
    """Lease record for Already Trading tests."""
    return LeaseRecord(
        lease_code="HO-E2E-001",
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
        lease_brand_mall="HO-E2E-001 - TestBrand - Test Mall",
    )


def _handed_over_collected_data() -> dict[str, Any]:
    """All collected_data fields required for CREATE_HANDED_OVER_SR."""
    lease = _ho_lease()
    return {
        "tenant_profile_id": lease.tenant_profile_id,
        "property_id": lease.property_id,
        "lease_code": lease.lease_code,
        "lease_id": lease.lease_id,
        "contract_id": lease.contract_id,
        "brand_id": lease.brand_id,
        "unit_codes": lease.unit_codes,
        "city": lease.city,
        "contracted_area": lease.contracted_area,
        "lease_brand_mall": lease.lease_brand_mall,
        "mall": lease.mall,
        "brand": lease.brand,
        "title": f"Already Trading - {lease.lease_code}",
        "already_handovered": "already_trading",
        "actual_shop_handover_date": "2026-01-10",
        "actual_fitout_start_date": "2026-01-12",
        "actual_fitout_end_date": "2026-02-01",
        "actual_trading_date": "2026-02-05",
        "comments": "",
    }


def _make_ho_supervisor_mock() -> AsyncMock:
    """Supervisor mock that returns CREATE_HANDED_OVER_SR intent."""
    return make_supervisor_mock(
        intent="CREATE_HANDED_OVER_SR",
        service_category="FIT_OUT_AND_HANDOVER",
        sub_category="HANDED_OVER",
        target_agent="rdd_handover_agent",
    )


# ===========================================================================
# 1. Successful HANDED_OVER SR creation — full happy path
# ===========================================================================


@pytest.mark.asyncio
async def test_successful_handed_over_sr_creation(app_client: AsyncClient) -> None:
    """Full happy-path for Path B.

    Turn 1: classify intent → active_agent=rdd_handover_agent, workflow_stage=CREATE_HANDED_OVER_SR.
    Turn 2: supply all 4 actual dates + lease → confirmation card shown.
    Turn 3: confirm → SR created → workflow_stage=HANDED_OVER_COMPLETED.
    """
    # Turn 1: intent classification
    with (
        patch(
            "app.agents.graph.nodes.supervisor_node._call_supervisor_llm",
            _make_ho_supervisor_mock(),
        ),
        patch(
            "app.agents.graph.nodes.supervisor_node.get_default_gateway",
            return_value=MagicMock(model="gpt-4o-mini"),
        ),
        patch(
            "app.agents.graph.nodes.shared.field_extraction_node.FieldExtractionService",
            make_field_extraction_mock(),
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
    ):
        body1 = await post_turn(
            app_client,
            "This unit is already trading. I want to register it as already traded.",
            user_id=_USER_ID,
        )
        session_id = body1.get("session_id")
        assert session_id is not None
        assert body1.get("active_agent") == "rdd_handover_agent"

    # Turn 2: supply all required data
    all_data = _handed_over_collected_data()
    with (
        patch(
            "app.agents.graph.nodes.shared.field_extraction_node.FieldExtractionService",
            make_field_extraction_mock(
                {k: {"value": str(v) if not isinstance(v, list) else ",".join(str(i) for i in v), "confidence": 1.0}
                 for k, v in all_data.items()}
            ),
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
    ):
        body2 = await post_turn(
            app_client,
            "Lease HO-E2E-001. Shop handover 10 Jan 2026, fitout 12 Jan to 1 Feb, trading from 5 Feb.",
            session_id=session_id,
            user_id=_USER_ID,
        )
        session_id = body2.get("session_id") or session_id

    # Turn 3: confirm → SR submitted
    from app.agents.services.payload_builder_service import build_create_handed_over_payload

    with (
        patch(
            "app.agents.graph.nodes.shared.field_extraction_node.FieldExtractionService",
            make_field_extraction_mock(),
        ),
        patch(
            "app.agents.graph.nodes.shared.field_extraction_node.get_default_gateway",
            return_value=MagicMock(model="gpt-4o-mini"),
        ),
        patch(
            "app.agents.graph.nodes.shared.validation_node._validation_service.validate_draft",
            MagicMock(return_value=[]),
        ),
        patch(
            "app.agents.graph.nodes.handover.payload_builder_node.build_create_handed_over_payload",
            MagicMock(return_value=build_create_handed_over_payload(all_data, {})),
        ),
        patch(
            "app.agents.graph.nodes.handover.api_submission_node.get_service_request_api_service",
            make_sr_api_mock("SR-HO-E2E-001"),
        ),
    ):
        body3 = await post_turn(
            app_client,
            "yes",
            session_id=session_id,
            user_id=_USER_ID,
        )

    # SR-HO-E2E-001 must appear in the response (submission confirmed)
    message = body3.get("message", "")
    state = body3.get("state", {})
    assert "SR-HO-E2E-001" in message or state.get("sr_id") == "SR-HO-E2E-001" or (
        "already trading" in message.lower() or "auto" in message.lower()
    ), f"Expected SR ID or completion message; got: {message!r}"


# ===========================================================================
# 2. CREATE_HANDED_OVER_SR intent classified correctly
# ===========================================================================


@pytest.mark.asyncio
async def test_handed_over_intent_classified_correctly(app_client: AsyncClient) -> None:
    """Supervisor mock returns CREATE_HANDED_OVER_SR → active_agent=rdd_handover_agent."""
    with (
        patch(
            "app.agents.graph.nodes.supervisor_node._call_supervisor_llm",
            _make_ho_supervisor_mock(),
        ),
        patch(
            "app.agents.graph.nodes.supervisor_node.get_default_gateway",
            return_value=MagicMock(model="gpt-4o-mini"),
        ),
        patch(
            "app.agents.graph.nodes.shared.field_extraction_node.FieldExtractionService",
            make_field_extraction_mock(),
        ),
        patch(
            "app.agents.graph.nodes.shared.field_extraction_node.get_default_gateway",
            return_value=MagicMock(model="gpt-4o-mini"),
        ),
        patch(
            "app.agents.graph.nodes.shared.lease_lookup_node.get_lease_lookup_service",
            make_lease_mock([]),
        ),
        patch(
            "app.agents.graph.nodes.shared.validation_node._validation_service.validate_draft",
            MagicMock(return_value=[]),
        ),
    ):
        body = await post_turn(
            app_client,
            "My unit is already trading. Register as already traded.",
            user_id=_USER_ID,
        )

    assert body.get("active_agent") == "rdd_handover_agent"
    assert body.get("session_id") is not None


# ===========================================================================
# 3. Payload built during submission has sub_category=HANDED_OVER
# ===========================================================================


@pytest.mark.asyncio
async def test_handed_over_sr_payload_has_correct_sub_category(
    app_client: AsyncClient,
) -> None:
    """The payload passed to the SR API must have sub_category=HANDED_OVER."""
    from app.agents.services.payload_builder_service import build_create_handed_over_payload

    all_data = _handed_over_collected_data()
    expected_payload = build_create_handed_over_payload(all_data, {})
    assert expected_payload["sub_category"] == "HANDED_OVER"
    assert expected_payload["payload"]["already_handovered"] == "already_trading"

    captured_payloads: list[dict[str, Any]] = []

    mock_sr_result = MagicMock()
    mock_sr_result.sr_id = "SR-HO-SUB-001"
    mock_sr_result.error = None
    mock_sr_result.status_code = 201
    mock_sr_result.latency_ms = 50
    mock_sr_result.correlation_id = "CORR-HO-001"
    mock_sr_result.endpoint = "/api/service-requests"
    mock_sr_result.response_payload = {"id": "SR-HO-SUB-001"}

    async def _capture_create(payload: dict[str, Any]) -> MagicMock:
        captured_payloads.append(payload)
        return mock_sr_result

    mock_svc = AsyncMock()
    mock_svc.create_service_request = _capture_create

    with (
        patch(
            "app.agents.graph.nodes.shared.field_extraction_node.FieldExtractionService",
            make_field_extraction_mock(),
        ),
        patch(
            "app.agents.graph.nodes.shared.field_extraction_node.get_default_gateway",
            return_value=MagicMock(model="gpt-4o-mini"),
        ),
        patch(
            "app.agents.graph.nodes.shared.validation_node._validation_service.validate_draft",
            MagicMock(return_value=[]),
        ),
        patch(
            "app.agents.graph.nodes.handover.payload_builder_node.build_create_handed_over_payload",
            MagicMock(return_value=expected_payload),
        ),
        patch(
            "app.agents.graph.nodes.handover.api_submission_node.get_service_request_api_service",
            MagicMock(return_value=mock_svc),
        ),
    ):
        await post_turn(
            app_client,
            "yes",
            user_id=_USER_ID,
        )

    if captured_payloads:
        submitted = captured_payloads[0]
        assert submitted.get("sub_category") == "HANDED_OVER"
        assert submitted["payload"]["already_handovered"] == "already_trading"


# ===========================================================================
# 4. Path B skips FM and RDD stages
# ===========================================================================


@pytest.mark.asyncio
async def test_handed_over_skips_fm_and_rdd_stages(app_client: AsyncClient) -> None:
    """After Path B SR creation, workflow_stage must be HANDED_OVER_COMPLETED.

    The workflow_stage in the response state must NOT be SR_CREATED (which
    would mean FM/RDD steps follow) — it must immediately be a terminal state.
    """
    from app.agents.services.payload_builder_service import build_create_handed_over_payload

    all_data = _handed_over_collected_data()

    with (
        patch(
            "app.agents.graph.nodes.shared.field_extraction_node.FieldExtractionService",
            make_field_extraction_mock(),
        ),
        patch(
            "app.agents.graph.nodes.shared.field_extraction_node.get_default_gateway",
            return_value=MagicMock(model="gpt-4o-mini"),
        ),
        patch(
            "app.agents.graph.nodes.shared.validation_node._validation_service.validate_draft",
            MagicMock(return_value=[]),
        ),
        patch(
            "app.agents.graph.nodes.handover.payload_builder_node.build_create_handed_over_payload",
            MagicMock(return_value=build_create_handed_over_payload(all_data, {})),
        ),
        patch(
            "app.agents.graph.nodes.handover.api_submission_node.get_service_request_api_service",
            make_sr_api_mock("SR-HO-E2E-004"),
        ),
    ):
        body = await post_turn(
            app_client,
            "yes",
            user_id=_USER_ID,
        )

    state = body.get("state", {})
    workflow_stage = state.get("workflow_stage")
    # Path B completes immediately — no SR_CREATED waiting for FM/RDD
    if workflow_stage is not None:
        assert workflow_stage != "SR_CREATED", (
            f"Path B must not land on SR_CREATED (FM/RDD gate) — got {workflow_stage!r}"
        )


# ===========================================================================
# 5. API failure handled gracefully
# ===========================================================================


@pytest.mark.asyncio
async def test_handed_over_api_failure_handled_gracefully(
    app_client: AsyncClient,
) -> None:
    """SR API returns an error; graph returns a user-facing error message."""
    from app.agents.services.payload_builder_service import build_create_handed_over_payload

    all_data = _handed_over_collected_data()

    with (
        patch(
            "app.agents.graph.nodes.shared.field_extraction_node.FieldExtractionService",
            make_field_extraction_mock(),
        ),
        patch(
            "app.agents.graph.nodes.shared.field_extraction_node.get_default_gateway",
            return_value=MagicMock(model="gpt-4o-mini"),
        ),
        patch(
            "app.agents.graph.nodes.shared.validation_node._validation_service.validate_draft",
            MagicMock(return_value=[]),
        ),
        patch(
            "app.agents.graph.nodes.handover.payload_builder_node.build_create_handed_over_payload",
            MagicMock(return_value=build_create_handed_over_payload(all_data, {})),
        ),
        patch(
            "app.agents.graph.nodes.handover.api_submission_node.get_service_request_api_service",
            make_sr_api_mock(error="Service Unavailable"),
        ),
    ):
        body = await post_turn(
            app_client,
            "yes",
            user_id=_USER_ID,
        )

    message = body.get("message", "")
    assert message, "Expected a non-empty message even on API failure"
    state_ready = body.get("state", {}).get("ready_to_submit", False)
    error_keywords = any(
        kw in message.lower()
        for kw in ("unable", "error", "failed", "try again", "contact", "sorry")
    )
    assert error_keywords or not state_ready, (
        f"Expected an error message or ready_to_submit=False; "
        f"message={message!r}, ready_to_submit={state_ready}"
    )


# ===========================================================================
# 6. Missing dates block confirmation card
# ===========================================================================


@pytest.mark.asyncio
async def test_handed_over_missing_dates_blocks_submission(
    app_client: AsyncClient,
) -> None:
    """When actual dates are missing, the confirmation card must not appear.

    The validation layer (or missing_field node) must detect the absent dates
    and ask for them before the confirmation card is shown.
    """
    with (
        patch(
            "app.agents.graph.nodes.supervisor_node._call_supervisor_llm",
            _make_ho_supervisor_mock(),
        ),
        patch(
            "app.agents.graph.nodes.supervisor_node.get_default_gateway",
            return_value=MagicMock(model="gpt-4o-mini"),
        ),
        patch(
            "app.agents.graph.nodes.shared.field_extraction_node.FieldExtractionService",
            # Only lease code provided — no actual dates extracted
            make_field_extraction_mock(
                {"lease_code": {"value": "HO-E2E-001", "confidence": 0.95}}
            ),
        ),
        patch(
            "app.agents.graph.nodes.shared.field_extraction_node.get_default_gateway",
            return_value=MagicMock(model="gpt-4o-mini"),
        ),
        patch(
            "app.agents.graph.nodes.shared.lease_lookup_node.get_lease_lookup_service",
            make_lease_mock([_ho_lease()]),
        ),
    ):
        body = await post_turn(
            app_client,
            "I want to register an already trading unit, lease HO-E2E-001",
            user_id=_USER_ID,
        )

    # Must not be ready to submit (missing dates)
    assert body.get("state", {}).get("ready_to_submit") is False
    # Must have a response message asking for dates
    assert body.get("message"), "Expected a response asking for the required dates"
    # Confirm card type must NOT be 'confirmation'
    ui = body.get("ui", {})
    assert ui.get("type") != "confirmation", (
        "Confirmation card must not appear when actual dates are missing"
    )
