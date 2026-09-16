"""Ticket 3784 — write-side analytics instrumentation.

Covers what the AI Command Center depends on being recorded:
* ``sr_submitted`` fires on the handover create call (it never did — the node
  typed the call ``"API"``);
* reviewer decisions emit ``sr_approved`` / ``sr_rejected`` once per SR;
* work-permit and fit-out submissions land in ``agent_tool_calls``;
* each trace carries channel / role / mall dimensions from the JWT.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from app.agents.graph.nodes.fitout_confirm import fitout_confirm_api_submission_node as fitout_mod
from app.agents.graph.nodes.handover import api_submission_node as create_mod
from app.agents.graph.nodes.work_permit import work_permit_api_submission_node as wp_mod
from app.observability.analytics_constants import (
    CHANNEL_COPILOT_RAIL,
    CHANNEL_MSP_HELP,
    CHANNEL_UNKNOWN,
    EVENT_SR_APPROVED,
    EVENT_SR_REJECTED,
    EVENT_SR_SUBMITTED,
    FLOW_WORK_PERMIT,
    TOOL_TYPE_SR_CREATE,
    TOOL_TYPE_SR_PATCH,
    normalize_channel,
)
from app.observability.tool_capture import record_sr_api_call, record_sr_decision
from app.observability.trace_manager import TraceManager
from app.services.chat_orchestration_service import trace_dimensions
from app.types.chat import AuthContext


def _result(**overrides):
    base = {
        "sr_id": "SR-9",
        "error": None,
        "status_code": 201,
        "latency_ms": 120,
        "response_payload": {"id": "SR-9"},
        "correlation_id": None,
        "endpoint": "/service-requests",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


# ── Channel + dimensions ────────────────────────────────────────────────────


def test_normalize_channel_accepts_known_and_folds_the_rest() -> None:
    assert normalize_channel("Copilot_Rail") == CHANNEL_COPILOT_RAIL
    assert normalize_channel(CHANNEL_MSP_HELP) == CHANNEL_MSP_HELP
    assert normalize_channel("<script>") == CHANNEL_UNKNOWN
    assert normalize_channel(None) == CHANNEL_UNKNOWN


def test_trace_dimensions_from_signed_in_user() -> None:
    auth = AuthContext(
        subject_id="4411",
        roles=frozenset({"FM_MANAGER", "DD_ENGINEER"}),
        mall_names=("Nakheel Mall",),
        unique_property_ids=(3041,),
        is_global_admin=False,
    )
    dims = trace_dimensions(auth, "copilot_panel")
    assert dims == {
        "channel": "copilot_panel",
        "user_roles": ["DD_ENGINEER", "FM_MANAGER"],
        "mall_names": ["Nakheel Mall"],
        "property_ids": [3041],
        "is_global_admin": False,
    }


def test_trace_dimensions_anonymous_keeps_only_channel() -> None:
    dims = trace_dimensions(AuthContext(subject_id="anonymous"), None)
    assert dims == {"channel": CHANNEL_UNKNOWN}


async def test_start_trace_passes_dimensions_to_repo(mock_session: AsyncMock) -> None:
    tm = TraceManager(mock_session)
    tm._trace_repo.create = AsyncMock(return_value=SimpleNamespace(id=uuid4()))
    dims = {"channel": "msp_help"}
    await tm.start_trace(uuid4(), uuid4(), "hi", dimensions=dims)
    assert tm._trace_repo.create.await_args.kwargs["dimensions"] == dims


# ── sr_submitted on create ──────────────────────────────────────────────────


def test_handover_create_is_typed_as_sr_create() -> None:
    assert create_mod._TOOL_TYPE == TOOL_TYPE_SR_CREATE


async def test_sr_create_tool_call_emits_sr_submitted(mock_session: AsyncMock) -> None:
    tm = TraceManager(mock_session)
    trace_id, session_id, user_id = uuid4(), uuid4(), uuid4()
    tm._trace_context[trace_id] = (session_id, user_id)
    tm._tool_repo.create = AsyncMock()
    with patch("app.observability.trace_manager.emit_analytics", new=AsyncMock()) as emit:
        await tm.capture_tool_call(
            trace_id=trace_id,
            run_id=uuid4(),
            tool_name="service_request_api.create_service_request",
            tool_type=create_mod._TOOL_TYPE,
            response_payload={"id": "SR-42"},
            success=True,
        )
    kwargs = emit.await_args.kwargs
    assert kwargs["event_name"] == EVENT_SR_SUBMITTED
    assert kwargs["properties"] == {"sr_id": "SR-42"}
    assert kwargs["once"] is True


# ── Reviewer decisions ──────────────────────────────────────────────────────


async def test_emit_sr_decision_approved(mock_session: AsyncMock) -> None:
    tm = TraceManager(mock_session)
    trace_id, session_id, user_id = uuid4(), uuid4(), uuid4()
    tm._trace_context[trace_id] = (session_id, user_id)
    with patch("app.observability.trace_manager.emit_analytics", new=AsyncMock()) as emit:
        await tm.emit_sr_decision(
            trace_id,
            approved=True,
            sr_id="SR-7",
            flow=FLOW_WORK_PERMIT,
            stage="WP_FM_REVIEW",
            actor_roles=["FM_MANAGER"],
        )
    kwargs = emit.await_args.kwargs
    assert kwargs["event_name"] == EVENT_SR_APPROVED
    assert kwargs["session_id"] == session_id
    assert kwargs["properties"] == {
        "flow": FLOW_WORK_PERMIT,
        "stage": "WP_FM_REVIEW",
        "sr_id": "SR-7",
        "actor_roles": ["FM_MANAGER"],
    }
    assert kwargs["once"] is True and kwargs["dedupe_sr_id"] == "SR-7"


async def test_emit_sr_decision_without_trace_context_is_a_no_op(
    mock_session: AsyncMock,
) -> None:
    tm = TraceManager(mock_session)
    with patch("app.observability.trace_manager.emit_analytics", new=AsyncMock()) as emit:
        await tm.emit_sr_decision(
            uuid4(), approved=False, sr_id="SR-1", flow=FLOW_WORK_PERMIT, stage=None
        )
    emit.assert_not_awaited()


async def test_record_sr_decision_never_raises() -> None:
    tm = SimpleNamespace(emit_sr_decision=AsyncMock(side_effect=RuntimeError("db down")))
    state = {"trace_manager": tm, "trace_id": str(uuid4())}
    await record_sr_decision(
        state, approved=True, sr_id="SR-1", flow=FLOW_WORK_PERMIT, stage=None
    )


async def test_record_sr_api_call_uses_node_run_and_redacts() -> None:
    tm = SimpleNamespace(capture_tool_call=AsyncMock())
    run_id, trace_id = uuid4(), uuid4()
    state = {"trace_manager": tm, "trace_id": trace_id, "_trace_node_run_id": run_id}
    await record_sr_api_call(
        state,
        tool_name="service_request_api.work_permit.create",
        tool_type=TOOL_TYPE_SR_CREATE,
        request_payload={"password": "x", "sub_category": "OPERATIONS"},
        result=_result(),
    )
    kwargs = tm.capture_tool_call.await_args.kwargs
    assert kwargs["run_id"] == run_id and kwargs["trace_id"] == trace_id
    assert kwargs["success"] is True and kwargs["status_code"] == 201
    assert kwargs["request_payload"].get("password") != "x"


async def test_record_sr_api_call_without_run_is_skipped() -> None:
    tm = SimpleNamespace(capture_tool_call=AsyncMock())
    await record_sr_api_call(
        {"trace_manager": tm, "trace_id": uuid4()},
        tool_name="t",
        tool_type=TOOL_TYPE_SR_PATCH,
        request_payload={},
        result=_result(),
    )
    tm.capture_tool_call.assert_not_awaited()


# ── Nodes wire the helpers ──────────────────────────────────────────────────


def _wp_review_state(status: str) -> dict:
    return {
        "workflow_stage": "WP_FM_REVIEW",
        "confirmation_status": "CONFIRMED",
        "validation_errors": [],
        "backend_refs": {"sr_id": "SR-5", "create_payload": {"status": status}},
    }


async def test_work_permit_reject_records_call_and_rejection() -> None:
    svc = SimpleNamespace(patch_service_request=AsyncMock(return_value=_result(sr_id="SR-5")))
    with (
        patch.object(wp_mod, "get_service_request_api_service", return_value=svc),
        patch.object(wp_mod, "record_sr_api_call", new=AsyncMock()) as call,
        patch.object(wp_mod, "record_sr_decision", new=AsyncMock()) as decision,
    ):
        out = await wp_mod.work_permit_api_submission_node(_wp_review_state("REJECTED"))
    assert out["workflow_stage"] == "WP_REJECTED"
    assert call.await_args.kwargs["tool_type"] == TOOL_TYPE_SR_PATCH
    assert decision.await_args.kwargs["approved"] is False
    assert decision.await_args.kwargs["stage"] == "WP_FM_REVIEW"


async def test_work_permit_create_records_call_but_no_decision() -> None:
    svc = SimpleNamespace(
        create_service_request=AsyncMock(return_value=_result()),
        get_service_request=AsyncMock(side_effect=RuntimeError("skip")),
    )
    state = {
        "workflow_stage": "CREATE_WORK_PERMIT",
        "confirmation_status": "CONFIRMED",
        "validation_errors": [],
        "backend_refs": {"create_payload": {"sub_category": "OPERATIONS"}},
    }
    with (
        patch.object(wp_mod, "get_service_request_api_service", return_value=svc),
        patch.object(wp_mod, "record_sr_api_call", new=AsyncMock()) as call,
        patch.object(wp_mod, "record_sr_decision", new=AsyncMock()) as decision,
    ):
        await wp_mod.work_permit_api_submission_node(state)
    assert call.await_args.kwargs["tool_type"] == TOOL_TYPE_SR_CREATE
    decision.assert_not_awaited()


async def test_fitout_approve_records_patch_and_approval() -> None:
    svc = SimpleNamespace(patch_service_request=AsyncMock(return_value=_result(sr_id="SR-3")))
    state = {
        "workflow_stage": "APPROVE_SR",
        "sub_category": "FIT_OUT_CONFIRM_FOS",
        "confirmation_status": "CONFIRMED",
        "validation_errors": [],
        "backend_refs": {"sr_id": "SR-3", "create_payload": {"status": "APPROVED"}},
    }
    with (
        patch.object(fitout_mod, "get_service_request_api_service", return_value=svc),
        patch.object(fitout_mod, "record_sr_api_call", new=AsyncMock()) as call,
        patch.object(fitout_mod, "record_sr_decision", new=AsyncMock()) as decision,
    ):
        out = await fitout_mod.fitout_confirm_api_submission_node(state)
    assert out["workflow_stage"] == "SR_APPROVED"
    assert call.await_args.kwargs["tool_type"] == TOOL_TYPE_SR_PATCH
    assert decision.await_args.kwargs["approved"] is True
    assert decision.await_args.kwargs["sr_id"] == "SR-3"


def test_decision_event_names() -> None:
    assert (EVENT_SR_APPROVED, EVENT_SR_REJECTED) == ("sr_approved", "sr_rejected")


# ── Platform created_via ────────────────────────────────────────────────────


async def test_http_create_marks_the_sr_as_created_by_the_assistant() -> None:
    from app.agents.services import service_request_api_service as svc_mod

    client = SimpleNamespace(
        create_service_request=AsyncMock(
            return_value=SimpleNamespace(
                success=True, error=None, sr_id="SR-1", endpoint="/service-requests",
                response_payload={"id": "SR-1"}, latency_ms=10, status_code=201,
                correlation_id=None,
            )
        )
    )
    svc = svc_mod.HttpServiceRequestAPIService.__new__(svc_mod.HttpServiceRequestAPIService)
    svc._client = client
    original = {"sub_category": "HANDOVER"}
    await svc.create_service_request(original)

    sent = client.create_service_request.await_args.args[0]
    assert sent["created_via"] == "AI_ASSISTANT"
    assert sent["sub_category"] == "HANDOVER"
    assert "created_via" not in original  # caller's payload is not mutated
