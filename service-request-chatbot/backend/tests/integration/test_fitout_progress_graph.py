"""End-to-end graph tests for the Fitout Progress create flow (phase 2).

Compiles the real graph and patches only external I/O (supervisor LLM, field
extraction, lease API, SR API). What these tests are really checking is that the
flow costs the user *one question*: turn 1 resolves the lease and asks it, turn 2
answers it with a chip, turn 3 records it.

Test coverage
-------------
test_first_turn_asks_the_one_question   — lease resolved, question + chips
test_chip_tap_reaches_the_review_card   — a tapped percentage needs no LLM
test_confirm_records_and_approves       — POST, and honest success wording
test_one_shot_lease_and_percentage      — both in the first sentence → card
test_out_of_range_is_re_asked           — 120% never reaches the platform
"""

from __future__ import annotations

from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.graph.help_agent_graph import build_help_agent_graph
from app.agents.schemas.supervisor_schema import SupervisorDecision
from app.agents.services.lease_lookup_service import LeaseLookupResult, LeaseRecord
from app.types.chat import AuthContext

_GRAPH = build_help_agent_graph()

AGENT = "rdd_fitout_progress_agent"
PROGRESS_FIELD = "fitout_progress_percentage"


def _state(**overrides: object) -> dict:
    return {
        "session_id": "fitout-progress-session",
        "user_id": "dd-engineer-1",
        "user_message": "",
        "auth_context": AuthContext(
            subject_id="dd-engineer-1",
            roles=frozenset({"DD_ENGINEER"}),
            unique_property_ids=(3041,),
        ),
        **overrides,
    }


def _lease_record() -> LeaseRecord:
    return LeaseRecord(
        lease_code="t0105712",
        lease_id=95404,
        contract_id=95404,
        brand="Brand Under Armour",
        brand_id=771,
        mall="Jawharat Jeddah",
        property_id=3041,
        tenant_profile_id=116,
        unit_codes=["JJ-114"],
        contracted_area=210.0,
        city="Jeddah",
        lease_brand_mall="t0105712 - Brand Under Armour - Jawharat Jeddah",
        company_name="Under Armour Trading Co.",
        actual_fitout_start_date="2026-07-15",
        base_fitout_end_date="2026-10-15",
        fitout_progress="38",
    )


def _lease_factory() -> MagicMock:
    svc = AsyncMock()
    svc.lookup.return_value = LeaseLookupResult(
        matches=[_lease_record()],
        endpoint="/leases",
        request_payload={},
        response_payload={},
        latency_ms=40,
        status_code=200,
        error=None,
    )
    return MagicMock(return_value=svc)


def _extraction(fields: dict | None = None) -> MagicMock:
    result = MagicMock()
    result.to_state_dict.return_value = fields or {}
    result.summary = ""
    meta = MagicMock(
        parse_success=True,
        latency_ms=90,
        retry_count=0,
        input_tokens=80,
        output_tokens=20,
        parse_error=None,
    )
    svc = MagicMock()
    svc.extract = AsyncMock(return_value=(result, meta))
    return MagicMock(return_value=svc)


def _supervisor(intent: str = "CREATE_FITOUT_PROGRESS") -> AsyncMock:
    decision = SupervisorDecision(
        intent=intent,  # type: ignore[arg-type]
        confidence=0.95,
        service_category="FIT_OUT_AND_HANDOVER",
        sub_category="FITOUT_PROGRESS",
        target_agent=AGENT,
        reasoning="integration test",
    )
    return AsyncMock(return_value=(decision, 90, 30, 180))


def _sr_api(sr_id: str = "8517", status: str | None = None) -> MagicMock:
    body: dict = {"data": {"service_request_id": sr_id}}
    if status:
        body["data"]["status"] = status
    result = MagicMock(
        sr_id=sr_id,
        error=None,
        status_code=201,
        latency_ms=210,
        correlation_id="CORR-1",
        endpoint="/service-requests",
        response_payload=body,
    )
    svc = AsyncMock()
    svc.create_service_request.return_value = result
    return MagicMock(return_value=svc), svc


def _patches(*, extraction_fields: dict | None = None) -> list:
    """The external I/O every turn needs stubbed."""
    return [
        patch(
            "app.agents.graph.nodes.supervisor_node._call_supervisor_llm",
            _supervisor(),
        ),
        patch(
            "app.agents.graph.nodes.supervisor_node.get_default_gateway",
            return_value=MagicMock(),
        ),
        patch(
            "app.agents.graph.nodes.shared.field_extraction_node.FieldExtractionService",
            _extraction(extraction_fields),
        ),
        patch(
            "app.agents.graph.nodes.shared.field_extraction_node.get_default_gateway",
            return_value=MagicMock(),
        ),
        patch(
            "app.agents.graph.nodes.shared.response_generation_node.get_default_gateway",
            return_value=MagicMock(),
        ),
        patch(
            "app.agents.graph.nodes.shared.lease_lookup_node.get_lease_lookup_service",
            _lease_factory(),
        ),
    ]


def _enter(stack: ExitStack, patches: list) -> None:
    for ctx in patches:
        stack.enter_context(ctx)


@pytest.mark.asyncio
async def test_first_turn_asks_the_one_question() -> None:
    """Lease resolved from the first sentence; the only question is the percentage.

    Graph path
    ----------
    load_session → supervisor → registry → fitout_progress_entry →
    field_extraction → merge_state → lease_lookup → validation →
    missing_field → response_generation → save_state
    """
    with ExitStack() as stack:
        _enter(
            stack,
            _patches(
                extraction_fields={
                    "lease_code": {"value": "t0105712", "confidence": 0.97}
                }
            ),
        )
        result = await _GRAPH.ainvoke(
            _state(user_message="record fitout progress for t0105712")
        )

    assert result["active_agent"] == AGENT
    assert result["intent"] == "CREATE_FITOUT_PROGRESS"
    assert result["workflow_stage"] == "CREATE_SR"
    # The lease came back fully resolved, so nothing about it is asked.
    assert result["collected_data"]["lease_id"] == 95404
    assert result["collected_data"]["tenant_profile_id"] == 116
    # …and the one open question is the percentage.
    assert result["missing_fields"] == [PROGRESS_FIELD]
    assert result["active_field"] == PROGRESS_FIELD
    assert result["status"] == "WAITING_FOR_USER"

    options = result["response_decorations"]["suggestions"]["options"]
    assert [o["value"] for o in options][-1] == "100"
    assert any("suggested" in o["label"] for o in options)


@pytest.mark.asyncio
async def test_chip_tap_reaches_the_review_card() -> None:
    """A tapped percentage resolves in the entry node — no extraction round-trip."""
    extraction = _extraction()
    with (
        patch(
            "app.agents.graph.nodes.shared.field_extraction_node.FieldExtractionService",
            extraction,
        ),
        patch(
            "app.agents.graph.nodes.shared.field_extraction_node.get_default_gateway",
            return_value=MagicMock(),
        ),
        patch(
            "app.agents.graph.nodes.shared.response_generation_node.get_default_gateway",
            return_value=MagicMock(),
        ),
        patch(
            "app.agents.graph.nodes.shared.lease_lookup_node.get_lease_lookup_service",
            _lease_factory(),
        ),
    ):
        result = await _GRAPH.ainvoke(
            _state(
                user_message="82",
                active_agent=AGENT,
                sub_category="FITOUT_PROGRESS",
                workflow_stage="CREATE_SR",
                active_field=PROGRESS_FIELD,
                collected_data={
                    "lease_brand_mall": "t0105712 - Brand Under Armour - Jawharat Jeddah",
                    "lease_code": "t0105712",
                    "lease_id": 95404,
                    "tenant_profile_id": 116,
                    "property_id": 3041,
                    "company_name": "Under Armour Trading Co.",
                    "brand_name": "Brand Under Armour",
                    "mall_name": "Jawharat Jeddah",
                    "actual_fitout_start_date": "2026-07-15",
                    "base_fitout_end_date": "2026-10-15",
                },
            )
        )

    extraction.assert_not_called()
    assert result["collected_data"][PROGRESS_FIELD] == "82"
    assert result["confirmation_status"] == "PENDING"
    card = result["response_ui"]
    assert card["type"] == "confirmation_card"
    assert card["confirmAction"] == "confirm_create_fitout_progress"


@pytest.mark.asyncio
async def test_confirm_records_and_approves() -> None:
    """Confirming posts the SR and says what actually happened to the lease."""
    factory, svc = _sr_api()
    with (
        patch(
            "app.agents.graph.nodes.shared.response_generation_node.get_default_gateway",
            return_value=MagicMock(),
        ),
        patch(
            "app.agents.services.service_request_api_service.get_service_request_api_service",
            factory,
        ),
        patch(
            "app.agents.graph.nodes.fitout_progress.fitout_progress_api_submission_node"
            ".get_service_request_api_service",
            factory,
        ),
    ):
        result = await _GRAPH.ainvoke(
            _state(
                user_message="",
                action_override="confirm_create_fitout_progress",
                active_agent=AGENT,
                sub_category="FITOUT_PROGRESS",
                workflow_stage="CREATE_SR",
                confirmation_status="PENDING",
                collected_data={
                    "lease_brand_mall": "t0105712 - Brand Under Armour - Jawharat Jeddah",
                    "lease_code": "t0105712",
                    "pms_tenant_code": "t0105712",
                    "lease_id": 95404,
                    "tenant_profile_id": 116,
                    "property_id": 3041,
                    "company_name": "Under Armour Trading Co.",
                    "brand_name": "Brand Under Armour",
                    "mall_name": "Jawharat Jeddah",
                    "actual_fitout_start_date": "2026-07-15",
                    "base_fitout_end_date": "2026-10-15",
                    PROGRESS_FIELD: "82",
                    "_progress_confirmed": True,
                },
            )
        )

    svc.create_service_request.assert_awaited_once()
    body = svc.create_service_request.await_args.args[0]
    assert body["sub_category"] == "FITOUT_PROGRESS"
    assert body["tenant_profile_id"] == 116
    assert body["payload"][PROGRESS_FIELD] == "82"
    assert body["title"] == "Fitout Progress - t0105712"

    # A DD create is auto-approved in the platform's create transaction, so the
    # reply must not promise a review that never happens.
    assert result["workflow_stage"] == "SR_APPROVED"
    assert result["backend_refs"]["sr_id"] == "8517"
    assert "8517" in result["response_message"]
    assert "82%" in result["response_message"]


@pytest.mark.asyncio
async def test_one_shot_lease_and_percentage() -> None:
    """Lease and percentage in one sentence: the question is skipped entirely."""
    with ExitStack() as stack:
        _enter(
            stack,
            _patches(
                extraction_fields={
                    "lease_code": {"value": "t0105712", "confidence": 0.97},
                    PROGRESS_FIELD: {"value": "82", "confidence": 0.93},
                }
            ),
        )
        result = await _GRAPH.ainvoke(
            _state(user_message="fitout for t0105712 is 82% done")
        )

    assert result["collected_data"][PROGRESS_FIELD] == "82"
    assert result["confirmation_status"] == "PENDING"
    assert result["response_ui"]["type"] == "confirmation_card"


@pytest.mark.asyncio
async def test_out_of_range_is_re_asked() -> None:
    """120% is refused locally and the question comes back — no platform 400."""
    factory, svc = _sr_api()
    with ExitStack() as stack:
        _enter(
            stack,
            _patches(
                extraction_fields={
                    "lease_code": {"value": "t0105712", "confidence": 0.97},
                    PROGRESS_FIELD: {"value": "120", "confidence": 0.9},
                }
            ),
        )
        stack.enter_context(
            patch(
                "app.agents.graph.nodes.fitout_progress"
                ".fitout_progress_api_submission_node"
                ".get_service_request_api_service",
                factory,
            )
        )
        result = await _GRAPH.ainvoke(
            _state(user_message="fitout for t0105712 is 120% done")
        )

    svc.create_service_request.assert_not_awaited()
    # No review card: an out-of-range value never becomes something to confirm.
    assert result.get("confirmation_status") != "PENDING"
    assert result["active_field"] == PROGRESS_FIELD
    # The rejected value is cleared rather than left sitting in the form while
    # the assistant asks again.
    assert result["collected_data"].get(PROGRESS_FIELD) in (None, "")


# ---------------------------------------------------------------------------
# Approval flow (phase 3)
# ---------------------------------------------------------------------------

_STORED_PAYLOAD: dict = {
    "lease_brand_mall": "t0105712 - Brand Under Armour - Jawharat Jeddah",
    "lease_code": "t0105712",
    "company_name": "Under Armour Trading Co.",
    "brand_name": "Brand Under Armour",
    "mall_name": "Jawharat Jeddah",
    "fitout_start_date": "2026-07-15",
    "fitout_end_date": "2026-10-15",
    PROGRESS_FIELD: "65",
    "comment": "",
    "contract_id": 95404,
    "lease_id": 95404,
    "tenant_profile_id": 116,
    "property_id": 3041,
    "sub_category": "FITOUT_PROGRESS",
    "category": "FIT_OUT_AND_HANDOVER",
}


def _sr_get_and_patch(status: str = "IN_PROCESS"):
    """A platform stub for an open Fitout Progress SR: GET then PATCH."""
    get_result = MagicMock(
        success=True,
        sr_id="8517",
        status=status,
        service_request_operations=[
            {"workflow_level": 0, "assigned_role": "DD_ENGINEER", "status": "SUBMITTED"},
            {"workflow_level": 1, "assigned_role": "DD_ENGINEER", "status": "IN_PROGRESS"},
        ],
        payload=dict(_STORED_PAYLOAD),
        service_request_attachments=[],
        endpoint="/service-requests/8517",
        status_code=200,
        latency_ms=30,
        error=None,
    )
    patch_result = MagicMock(
        sr_id="8517",
        error=None,
        status_code=200,
        latency_ms=120,
        correlation_id="CORR-2",
        endpoint="/service-requests/8517",
        response_payload={"data": {"service_request_id": "8517"}},
    )
    svc = AsyncMock()
    svc.get_service_request.return_value = get_result
    svc.patch_service_request.return_value = patch_result
    return MagicMock(return_value=svc), svc


def _approval_patches(factory) -> list:
    return [
        patch(
            "app.agents.graph.nodes.shared.response_generation_node.get_default_gateway",
            return_value=MagicMock(),
        ),
        patch(
            "app.agents.graph.nodes.shared.field_extraction_node.FieldExtractionService",
            _extraction(),
        ),
        patch(
            "app.agents.graph.nodes.shared.field_extraction_node.get_default_gateway",
            return_value=MagicMock(),
        ),
        patch(
            "app.agents.graph.nodes.shared.sr_status_sync_node.get_service_request_api_service",
            factory,
        ),
        patch(
            "app.agents.services.service_request_api_service.get_service_request_api_service",
            factory,
        ),
        patch(
            "app.agents.graph.nodes.fitout_progress.fitout_progress_api_submission_node"
            ".get_service_request_api_service",
            factory,
        ),
    ]


@pytest.mark.asyncio
async def test_opening_an_open_sr_asks_the_same_one_question() -> None:
    """The approver's turn: stage pinned from the platform, one question asked.

    Graph path
    ----------
    load_session → sr_status_sync → fitout_progress_entry → field_extraction →
    merge_state → lease_lookup → validation → missing_field → response_generation
    """
    factory, svc = _sr_get_and_patch()
    with ExitStack() as stack:
        _enter(stack, _approval_patches(factory))
        result = await _GRAPH.ainvoke(
            _state(user_message="", backend_refs={"sr_id": "8517"})
        )

    svc.get_service_request.assert_awaited()
    # Named explicitly by the sync, because both this workflow and the fitout
    # confirmations declare APPROVE_SR in stage_sync_nodes.
    assert result["active_agent"] == AGENT
    assert result["sub_category"] == "FITOUT_PROGRESS"
    assert result["workflow_stage"] == "APPROVE_SR"
    # The stored payload is kept verbatim for the PATCH echo.
    assert result["backend_refs"]["stored_payload"][PROGRESS_FIELD] == "65"

    # The stored percentage is context, not an answer — so the question is asked.
    assert result["missing_fields"] == [PROGRESS_FIELD]
    assert "SR 8517 records **65%**" in result["response_message"]

    options = result["response_decorations"]["suggestions"]["options"]
    assert options[0]["action"] == "confirm_approve_fitout_progress"
    assert options[0]["label"] == "Approve 65%"
    assert options[-1]["action"] == "reject_fitout_progress"


@pytest.mark.asyncio
async def test_approve_chip_patches_the_stored_percentage() -> None:
    """Tapping "Approve 65%" sends APPROVED with the payload untouched."""
    factory, svc = _sr_get_and_patch()
    with ExitStack() as stack:
        _enter(stack, _approval_patches(factory))
        result = await _GRAPH.ainvoke(
            _state(
                user_message="approve 65%",
                action_override="confirm_approve_fitout_progress",
                active_agent=AGENT,
                sub_category="FITOUT_PROGRESS",
                workflow_stage="APPROVE_SR",
                backend_refs={
                    "sr_id": "8517",
                    "stored_payload": dict(_STORED_PAYLOAD),
                },
                collected_data={
                    "lease_code": "t0105712",
                    "lease_id": 95404,
                    "fitout_start_date": "2026-07-15",
                    "fitout_end_date": "2026-10-15",
                    PROGRESS_FIELD: "65",
                },
            )
        )

    svc.patch_service_request.assert_awaited_once()
    sr_id, body = svc.patch_service_request.await_args.args
    assert sr_id == "8517"
    assert body["status"] == "APPROVED"
    assert body["sub_category"] == "FITOUT_PROGRESS"
    # Only the editable keys may differ from the stored payload, or the platform
    # answers "field <key> is not editable at level 1".
    changed = {
        k for k, v in body["payload"].items() if _STORED_PAYLOAD.get(k) != v
    }
    assert changed <= {PROGRESS_FIELD, "comment"}
    assert body["payload"][PROGRESS_FIELD] == "65"

    assert result["workflow_stage"] == "SR_APPROVED"
    assert "approved at **65%**" in result["response_message"]


@pytest.mark.asyncio
async def test_a_different_percentage_updates_and_approves() -> None:
    """Typing a number is the web form's Update-and-Approve: card, then PATCH."""
    factory, svc = _sr_get_and_patch()
    with ExitStack() as stack:
        _enter(stack, _approval_patches(factory))
        card_turn = await _GRAPH.ainvoke(
            _state(
                user_message="90",
                active_agent=AGENT,
                sub_category="FITOUT_PROGRESS",
                workflow_stage="APPROVE_SR",
                active_field=PROGRESS_FIELD,
                backend_refs={
                    "sr_id": "8517",
                    "stored_payload": dict(_STORED_PAYLOAD),
                },
                collected_data={
                    "lease_code": "t0105712",
                    "lease_id": 95404,
                    "fitout_start_date": "2026-07-15",
                    "fitout_end_date": "2026-10-15",
                    PROGRESS_FIELD: "65",
                },
            )
        )

    # The revised number reached the card, and nothing was sent yet.
    svc.patch_service_request.assert_not_awaited()
    assert card_turn["collected_data"][PROGRESS_FIELD] == "90"
    assert card_turn["response_ui"]["type"] == "confirmation_card"
    assert card_turn["response_ui"]["confirmLabel"] == "Approve"

    with ExitStack() as stack:
        _enter(stack, _approval_patches(factory))
        approved = await _GRAPH.ainvoke(
            _state(
                user_message="",
                action_override="confirm_approve_fitout_progress",
                active_agent=AGENT,
                sub_category="FITOUT_PROGRESS",
                workflow_stage="APPROVE_SR",
                confirmation_status="PENDING",
                backend_refs={
                    "sr_id": "8517",
                    "stored_payload": dict(_STORED_PAYLOAD),
                },
                collected_data={
                    **card_turn["collected_data"],
                },
            )
        )

    body = svc.patch_service_request.await_args.args[1]
    assert body["status"] == "APPROVED"
    assert body["payload"][PROGRESS_FIELD] == "90"
    assert approved["workflow_stage"] == "SR_APPROVED"
    assert "90%" in approved["response_message"]


@pytest.mark.asyncio
async def test_reject_confirms_then_patches_rejected() -> None:
    """Reject is terminal, gets its own card, and leaves the lease alone."""
    factory, svc = _sr_get_and_patch()
    base_collected = {
        "lease_code": "t0105712",
        "lease_id": 95404,
        "fitout_start_date": "2026-07-15",
        "fitout_end_date": "2026-10-15",
        PROGRESS_FIELD: "65",
    }

    with ExitStack() as stack:
        _enter(stack, _approval_patches(factory))
        card_turn = await _GRAPH.ainvoke(
            _state(
                user_message="reject",
                action_override="reject_fitout_progress",
                active_agent=AGENT,
                sub_category="FITOUT_PROGRESS",
                workflow_stage="APPROVE_SR",
                backend_refs={
                    "sr_id": "8517",
                    "stored_payload": dict(_STORED_PAYLOAD),
                },
                collected_data=dict(base_collected),
            )
        )

    svc.patch_service_request.assert_not_awaited()
    card = card_turn["response_ui"]
    assert card["confirmLabel"] == "Reject"
    assert card["confirmAction"] == "confirm_reject_fitout_progress"
    assert "Reject SR **8517**?" in card["message"]
    # Nothing is editable on a rejection: the number is not what is being decided.
    assert all(not row["editable"] for row in card["fields"])
    assert card_turn["backend_refs"]["fitout_progress_action"] == "reject"

    with ExitStack() as stack:
        _enter(stack, _approval_patches(factory))
        rejected = await _GRAPH.ainvoke(
            _state(
                user_message="",
                action_override="confirm_reject_fitout_progress",
                active_agent=AGENT,
                sub_category="FITOUT_PROGRESS",
                workflow_stage="APPROVE_SR",
                confirmation_status="PENDING",
                backend_refs=dict(card_turn["backend_refs"]),
                collected_data=dict(card_turn["collected_data"]),
            )
        )

    body = svc.patch_service_request.await_args.args[1]
    assert body["status"] == "REJECTED"
    assert body["comment"] == "Rejected via assistant"
    assert rejected["workflow_stage"] == "SR_REJECTED"
    assert "rejected" in rejected["response_message"].lower()
    assert "not updated" in rejected["response_message"]


@pytest.mark.asyncio
async def test_a_closed_sr_is_not_reopened_for_approval() -> None:
    """An SR the platform already approved syncs to its terminal stage."""
    factory, _svc = _sr_get_and_patch(status="APPROVED")
    with ExitStack() as stack:
        _enter(stack, _approval_patches(factory))
        result = await _GRAPH.ainvoke(
            _state(user_message="", backend_refs={"sr_id": "8517"})
        )

    assert result["workflow_stage"] == "SR_APPROVED"
    assert result.get("confirmation_status") != "PENDING"


# ---------------------------------------------------------------------------
# Regressions from live testing (2026-09-27)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_form_pre_fill_does_not_answer_the_question() -> None:
    """A background form sync must not skip the only question this flow asks.

    The web form computes the same elapsed-time default the agent suggests and
    writes it into the field the moment a lease is picked; the form→chat bridge
    then syncs it back as a silent patch — no message, nobody at the keyboard.
    Counted as an answer, that took a live session (DUMMY-CUST-001) from the
    question straight to "Recording fit-out progress at 100%".
    """
    with ExitStack() as stack:
        _enter(stack, _patches())
        result = await _GRAPH.ainvoke(
            _state(
                # The silent form patch: empty message, no action.
                user_message="",
                active_agent=AGENT,
                sub_category="FITOUT_PROGRESS",
                workflow_stage="CREATE_SR",
                collected_data={
                    "lease_brand_mall": "t0512099 - Levi's - Mall of Dhahran",
                    "lease_code": "t0512099",
                    "lease_id": 90218,
                    "tenant_profile_id": 318,
                    "actual_fitout_start_date": "2026-01-15",
                    "base_fitout_end_date": "2027-06-30",
                },
                corrected_fields={PROGRESS_FIELD: "100"},
            )
        )

    # The value is kept, so the draft and the form agree…
    assert result["collected_data"][PROGRESS_FIELD] == "100"
    # …but it is not an answer, so no review card and the question stands.
    assert result["collected_data"].get("_progress_confirmed") is not True
    assert result.get("confirmation_status") != "PENDING"
    assert result.get("response_ui", {}).get("type") != "confirmation_card"
    assert result["missing_fields"] == [PROGRESS_FIELD]


@pytest.mark.asyncio
async def test_a_tapped_chip_still_answers_it() -> None:
    """The guard must not cost the user their actual answer."""
    with ExitStack() as stack:
        _enter(stack, _patches())
        result = await _GRAPH.ainvoke(
            _state(
                user_message="100",
                active_agent=AGENT,
                sub_category="FITOUT_PROGRESS",
                workflow_stage="CREATE_SR",
                active_field=PROGRESS_FIELD,
                collected_data={
                    "lease_brand_mall": "t0512099 - Levi's - Mall of Dhahran",
                    "lease_code": "t0512099",
                    "lease_id": 90218,
                    "tenant_profile_id": 318,
                    "actual_fitout_start_date": "2026-01-15",
                    "base_fitout_end_date": "2027-06-30",
                },
            )
        )

    assert result["collected_data"]["_progress_confirmed"] is True
    assert result["response_ui"]["type"] == "confirmation_card"


@pytest.mark.asyncio
async def test_a_pending_question_survives_a_background_form_patch() -> None:
    """A form sync must not replace a question the user has not answered yet.

    Reported on the fitout confirmations: picking a lease whose milestone was
    already confirmed showed "…already confirmed on 12/10/2026. Continue and
    revise, or pick a different lease?" and then, with no user turn in between,
    "Before we start — would you like me to guide you through the form?" — the
    Continue / Cancel chips left under a different question.
    """
    with ExitStack() as stack:
        _enter(stack, _patches())
        result = await _GRAPH.ainvoke(
            _state(
                user_message="",
                active_agent="rdd_fitout_start_agent",
                sub_category="FIT_OUT_CONFIRM_FOS",
                workflow_stage="CREATE_SR",
                confirmation_status="MILESTONE_PENDING",
                collected_data={
                    "lease_code": "t0109326",
                    "lease_brand_mall": "t0109326 - Brand Marella - Mall",
                    "lease_id": 7,
                },
                corrected_fields={"company_name": "Marella Co"},
            )
        )

    # The pause holds, and the question on screen is still the one asked.
    assert result["confirmation_status"] == "MILESTONE_PENDING"
    message = result.get("response_message") or ""
    assert "guide you through the form" not in message
    assert "Continue" in message
    # The patch was still merged — the draft and the form stay in step.
    assert result["collected_data"]["company_name"] == "Marella Co"


@pytest.mark.asyncio
async def test_answering_the_pause_lets_the_flow_move_on() -> None:
    """Continue is an action, so it is handled before the hold above."""
    with ExitStack() as stack:
        _enter(stack, _patches())
        result = await _GRAPH.ainvoke(
            _state(
                user_message="continue",
                action_override="continue_milestone",
                active_agent="rdd_fitout_start_agent",
                sub_category="FIT_OUT_CONFIRM_FOS",
                workflow_stage="CREATE_SR",
                confirmation_status="MILESTONE_PENDING",
                collected_data={
                    "lease_code": "t0109326",
                    "lease_brand_mall": "t0109326 - Brand Marella - Mall",
                    "lease_id": 7,
                },
            )
        )

    assert result.get("confirmation_status") != "MILESTONE_PENDING"
    assert result["collected_data"]["_milestone_confirmed_ack"] is True



@pytest.mark.asyncio
@pytest.mark.parametrize(
    "message",
    [
        "update fitout progress t0105712 30 %",
        "update fitout progress for t0105712 30%",
        "record fitout progress t0105712 30%",
        "fitout for t0105712 is 30% done",
    ],
)
async def test_a_full_intent_goes_straight_to_the_review_card(message: str) -> None:
    """Lease and percentage in one sentence: nothing left to ask.

    Reported by a client on "update fitout progress t0112745 30 %". The leading
    "update" matched the correction cue while naming no known field, so the turn
    was read as "the user wants to change something", the LLM call was skipped
    and *nothing* was collected — the assistant then asked for the lease code
    they had just given. The other three phrasings always worked, which is what
    made it look like an extraction problem.
    """
    with ExitStack() as stack:
        _enter(
            stack,
            _patches(
                extraction_fields={
                    "lease_code": {"value": "t0105712", "confidence": 0.97},
                    PROGRESS_FIELD: {"value": "30", "confidence": 0.94},
                }
            ),
        )
        result = await _GRAPH.ainvoke(_state(user_message=message))

    assert result["response_ui"]["type"] == "confirmation_card"
    assert result["collected_data"][PROGRESS_FIELD] == "30"
    assert result["collected_data"]["_progress_confirmed"] is True
    assert result["collected_data"]["lease_id"] == 95404


@pytest.mark.asyncio
async def test_the_form_default_cannot_overwrite_the_answer_given() -> None:
    """The percentage the user stated survives the form hydrating the lease.

    `corrected_fields` is a maximum-confidence write, so the form's own
    elapsed-time default — synced back the moment it resolves the lease —
    replaced the number the user had just given in the same breath.
    """
    with ExitStack() as stack:
        _enter(stack, _patches())
        result = await _GRAPH.ainvoke(
            _state(
                user_message="",
                active_agent=AGENT,
                sub_category="FITOUT_PROGRESS",
                workflow_stage="CREATE_SR",
                collected_data={
                    "lease_brand_mall": "t0105712 - Brand Under Armour - Jawharat Jeddah",
                    "lease_code": "t0105712",
                    "lease_id": 95404,
                    "tenant_profile_id": 116,
                    "actual_fitout_start_date": "2026-07-15",
                    "base_fitout_end_date": "2026-10-15",
                    PROGRESS_FIELD: "30",
                    "_progress_confirmed": True,
                },
                corrected_fields={PROGRESS_FIELD: "100"},
            )
        )

    assert result["collected_data"][PROGRESS_FIELD] == "30"


@pytest.mark.asyncio
async def test_a_full_intent_works_mid_session_too() -> None:
    """Switching into this flow from another one must not drop the message.

    The routers short-circuit a turn whose persisted `status` is
    WAITING_FOR_USER straight to response_generation, and that status survives
    a change of agent. Asking for fit-out progress in a session that was
    waiting on another flow's question meant the turn never reached
    field_extraction: reported live, where "update fitout progress t0112745
    30 %" was answered with "please share the lease code".
    """
    with ExitStack() as stack:
        _enter(
            stack,
            _patches(
                extraction_fields={
                    "lease_code": {"value": "t0105712", "confidence": 0.97},
                    PROGRESS_FIELD: {"value": "30", "confidence": 0.94},
                }
            ),
        )
        result = await _GRAPH.ainvoke(
            _state(
                user_message="update fitout progress t0105712 30 %",
                # What the previous flow left behind.
                status="WAITING_FOR_USER",
                active_field="actual_fitout_start_date",
            )
        )

    assert result["response_ui"]["type"] == "confirmation_card"
    assert result["collected_data"][PROGRESS_FIELD] == "30"


@pytest.mark.asyncio
async def test_typing_the_lease_code_is_not_re_asked() -> None:
    """Answering the lease question by typing must reach the lookup.

    Every question saves WAITING_FOR_USER, so it is present on the turn that
    carries the answer. Short-circuiting that to response_generation swallowed
    the answer — response_generation cannot write `collected_data` — and the
    assistant asked for the lease code again. Reported live: typing t0112745
    instead of tapping the chip.
    """
    with ExitStack() as stack:
        _enter(
            stack,
            _patches(
                extraction_fields={
                    "lease_code": {"value": "t0105712", "confidence": 0.97}
                }
            ),
        )
        result = await _GRAPH.ainvoke(
            _state(
                user_message="t0105712",
                active_agent=AGENT,
                sub_category="FITOUT_PROGRESS",
                workflow_stage="CREATE_SR",
                # What the lease question left behind.
                status="WAITING_FOR_USER",
                active_field="lease_code",
                collected_data={},
            )
        )

    assert result["collected_data"]["lease_id"] == 95404
    assert result["missing_fields"] == [PROGRESS_FIELD]
    assert "How complete is the fit-out today?" in result["response_message"]


@pytest.mark.asyncio
async def test_a_prose_percentage_answer_reaches_extraction() -> None:
    """"about 30" is not a bare number, so it must still reach the extractor."""
    with ExitStack() as stack:
        _enter(
            stack,
            _patches(extraction_fields={PROGRESS_FIELD: {"value": "30", "confidence": 0.9}}),
        )
        result = await _GRAPH.ainvoke(
            _state(
                user_message="about 30 i'd say",
                active_agent=AGENT,
                sub_category="FITOUT_PROGRESS",
                workflow_stage="CREATE_SR",
                status="WAITING_FOR_USER",
                active_field=PROGRESS_FIELD,
                collected_data={
                    "lease_brand_mall": "t0105712 - Brand Under Armour - Jawharat Jeddah",
                    "lease_code": "t0105712",
                    "lease_id": 95404,
                    "tenant_profile_id": 116,
                },
            )
        )

    assert result["collected_data"][PROGRESS_FIELD] == "30"
    assert result["response_ui"]["type"] == "confirmation_card"


@pytest.mark.asyncio
@pytest.mark.parametrize("retyped, expected", [("30", "30"), ("45", "45")])
async def test_retyping_the_request_over_the_card_re_renders_it(
    retyped: str, expected: str
) -> None:
    """The whole request typed again while its review card is pending.

    "update" used to reject the card and then read as a correction verb naming
    no field: nothing was extracted, no card came back, and the reply was
    improvised. The retype is the request again and lands on the card again.
    """
    with ExitStack() as stack:
        _enter(
            stack,
            _patches(
                extraction_fields={
                    "lease_code": {"value": "t0105712", "confidence": 0.97},
                    PROGRESS_FIELD: {"value": "30", "confidence": 0.95},
                }
            ),
        )
        first = await _GRAPH.ainvoke(
            _state(user_message="update fitout progress t0105712 30%")
        )
    assert first["response_ui"]["type"] == "confirmation_card"

    carried = {
        key: value
        for key, value in first.items()
        if key not in ("response_message", "response_ui", "corrected_fields")
    }
    with ExitStack() as stack:
        _enter(
            stack,
            _patches(
                extraction_fields={
                    "lease_code": {"value": "t0105712", "confidence": 0.97},
                    PROGRESS_FIELD: {"value": retyped, "confidence": 0.95},
                }
            ),
        )
        second = await _GRAPH.ainvoke(
            {
                **carried,
                "user_message": f"update fitout progress t0105712 {retyped}%",
            }
        )

    assert second["response_ui"]["type"] == "confirmation_card"
    assert second["confirmation_status"] == "PENDING"
    assert second["collected_data"][PROGRESS_FIELD] == expected
    assert second["collected_data"]["lease_id"] == 95404


async def _card_for_30() -> dict:
    with ExitStack() as stack:
        _enter(
            stack,
            _patches(
                extraction_fields={
                    "lease_code": {"value": "t0105712", "confidence": 0.97},
                    PROGRESS_FIELD: {"value": "30", "confidence": 0.95},
                }
            ),
        )
        first = await _GRAPH.ainvoke(
            _state(user_message="update fitout progress t0105712 30%")
        )
    assert first["response_ui"]["type"] == "confirmation_card"
    # What a real next turn starts from: the session restores the draft, not
    # this turn's extraction or its reply.
    return {
        key: value
        for key, value in first.items()
        if key
        not in ("response_message", "response_ui", "corrected_fields", "extracted_fields")
    }


async def _form_sync(carried: dict, corrected: dict) -> dict:
    """A silent form→chat turn: no message, the changed fields only."""
    with ExitStack() as stack:
        _enter(stack, _patches())
        return await _GRAPH.ainvoke(
            {**carried, "user_message": "", "corrected_fields": corrected}
        )


@pytest.mark.asyncio
async def test_a_percentage_edited_in_the_form_updates_the_pending_card() -> None:
    """One-shot straight to the card, then the % changed in the open form.

    It used to fall through to "Please reply yes", and merge_state would have
    restored 30 anyway, reading the edit as the form's background default.
    """
    result = await _form_sync(await _card_for_30(), {PROGRESS_FIELD: "45"})

    assert result["collected_data"][PROGRESS_FIELD] == "45"
    assert result["confirmation_status"] == "PENDING"
    assert result["response_ui"]["type"] == "confirmation_card"


@pytest.mark.asyncio
async def test_a_comment_added_in_the_form_reaches_the_pending_card() -> None:
    result = await _form_sync(await _card_for_30(), {"comment": "Ceiling works done"})

    assert result["collected_data"]["comment"] == "Ceiling works done"
    assert result["collected_data"][PROGRESS_FIELD] == "30"
    assert result["response_ui"]["type"] == "confirmation_card"


@pytest.mark.asyncio
async def test_a_background_lease_sync_over_the_card_is_not_answered() -> None:
    result = await _form_sync(await _card_for_30(), {"brand_name": "Brand Under Armour"})

    assert "Please reply" not in str(result.get("response_message") or "")
    assert result["collected_data"][PROGRESS_FIELD] == "30"
    assert result["confirmation_status"] == "PENDING"
