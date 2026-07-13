"""Submit the RDD_REVIEW handover report to the platform SR API.

Responsibilities
----------------
- Guard: ``backend_refs["rdd_action"]`` must be ``submit`` or ``final_approve``.
- Guard: no blocking validation errors.
- Guard: RDD payload must be present in ``backend_refs["rdd_payload"]``.
- Guard: permission check via PermissionService.
- Call ``ServiceRequestAPIService.submit_report`` (POST /service-requests with
  status=REPORT_SUBMITTED) or ``patch_service_request`` for final approve.
- On v2 final approve: UPDATE (``RE_EXAMINE``) then ``APPROVED``, then verify
  the DD/RDD operation finished on the platform.
- Capture observability (redacted snapshot + tool-call record).
- Write audit log events ``sr.rdd.report_submitted`` / ``sr.rdd.final_approved``.
- Update state with platform result.

Non-responsibilities
--------------------
- MUST NOT call the LLM.
- MUST NOT build the payload (that is ``rdd_payload_builder_node``).
"""

from __future__ import annotations

import json
import logging
from typing import Any
from uuid import UUID

from app.agents.graph.state import ServiceRequestState
from app.agents.services.permission_service import PermissionDeniedError, PermissionService
from app.agents.services.service_request_api_service import get_service_request_api_service
from app.db.repositories.audit_log_repo import AuditLogRepository
from app.observability.decorators import trace_node
from app.observability.redaction import redact

logger = logging.getLogger(__name__)

_permission_service = PermissionService()
_TOOL_NAME = "service_request_api.rdd.submit_report"
_TOOL_TYPE = "API"

_RDD_ROLES: frozenset[str] = frozenset({"DD_ENGINEER"})
_RDD_FINISHED: frozenset[str] = frozenset({"FINISHED", "APPROVED"})


def _has_blocking_errors(validation_errors: list[dict[str, Any]]) -> bool:
    return any(e.get("blocking", True) for e in validation_errors)


def _workflow_version_from_get(get_result: Any) -> int:
    """Extract workflow_version from a GET SR result (default 1 = legacy)."""
    payload = getattr(get_result, "payload", None)
    raw = None
    if isinstance(payload, dict):
        raw = payload.get("workflow_version")
    if raw is None and isinstance(getattr(get_result, "response_payload", None), dict):
        data = (get_result.response_payload or {}).get("data") or {}
        if isinstance(data, dict):
            raw = data.get("workflow_version")
    try:
        return int(raw) if raw is not None else 1
    except (TypeError, ValueError):
        return 1


def _rdd_operation_finished(operations: list[dict[str, Any]]) -> bool:
    """True when a DD/RDD op is terminal and none remain IN_PROGRESS."""
    rdd_ops = [
        op
        for op in operations
        if (op.get("assigned_role") or op.get("role") or "").upper() in _RDD_ROLES
    ]
    if not rdd_ops:
        return False

    def _status(op: dict[str, Any]) -> str:
        return (op.get("status") or "").upper()

    has_in_progress = any(_status(op) == "IN_PROGRESS" for op in rdd_ops)
    has_terminal = any(_status(op) in _RDD_FINISHED for op in rdd_ops)
    return has_terminal and not has_in_progress


def _to_uuid(value: str | UUID | None) -> UUID | None:
    if value is None:
        return None
    if isinstance(value, UUID):
        return value
    try:
        return UUID(str(value))
    except (ValueError, AttributeError):
        return None


@trace_node("rdd_api_submission", "TOOL")
async def rdd_api_submission_node(state: ServiceRequestState) -> dict[str, Any]:
    """Submit the RDD handover report via POST /service-requests (REPORT_SUBMITTED).

    Reads from state
    ----------------
    ``backend_refs``      — must contain ``sr_id``, ``rdd_payload``, ``rdd_action``.
    ``validation_errors`` — aborts if any blocking error is present.
    ``trace_manager``     — optional for observability.

    Writes to state
    ---------------
    ``backend_refs``      — updated with ``rdd_submitted_sr_id``.
    ``status``            — ``"SUBMITTED"`` on success; ``"FAILED"`` otherwise.
    ``response_message``  — human-readable outcome.
    """
    # ── 1. Blocking-validation guard ──────────────────────────────────────────
    validation_errors: list[dict[str, Any]] = state.get("validation_errors") or []
    if _has_blocking_errors(validation_errors):
        blocking_count = sum(1 for e in validation_errors if e.get("blocking", True))
        return {
            "status": "FAILED",
            "response_message": (
                f"RDD submission blocked: {blocking_count} validation error(s) must be "
                "resolved before the report can be submitted."
            ),
        }

    # ── 2. Payload guard ───────────────────────────────────────────────────────
    backend_refs: dict[str, Any] = dict(state.get("backend_refs") or {})
    sr_id: str | None = backend_refs.get("sr_id")
    payload: dict[str, Any] | None = backend_refs.get("rdd_payload")
    rdd_action: str = backend_refs.get("rdd_action", "")

    if not sr_id:
        return {
            "status": "FAILED",
            "response_message": "RDD submission blocked: service request ID is missing.",
        }
    if not payload:
        return {
            "status": "FAILED",
            "response_message": (
                "RDD submission blocked: the report payload could not be found. "
                "Please try again."
            ),
        }

    # ── 3. Permission guard ────────────────────────────────────────────────────
    from app.types.chat import AuthContext
    auth: AuthContext | None = state.get("auth_context")  # type: ignore[assignment]
    if auth is not None:
        try:
            _permission_service.check("SUBMIT_RDD_HANDOVER_REPORT", auth)
        except PermissionDeniedError as exc:
            logger.warning("rdd_api_submission_node: permission denied — %s", exc)
            return {
                "status": "FAILED",
                "response_message": f"Permission denied: {exc}",
            }

    # ── 4. Observability setup ─────────────────────────────────────────────────
    trace_manager = state.get("trace_manager")  # type: ignore[assignment]
    trace_id: UUID | None = _to_uuid(state.get("trace_id"))
    node_run_id: UUID | None = _to_uuid(state.get("_trace_node_run_id"))
    run_id: UUID | None = None

    if trace_manager is not None and trace_id is not None:
        run_id = await trace_manager.start_run(
            trace_id=trace_id,
            run_name=_TOOL_NAME,
            run_type="TOOL",
            parent_run_id=node_run_id,
            input={"sr_id": sr_id, "rdd_action": rdd_action},
        )

    if trace_manager is not None and trace_id is not None and run_id is not None:
        await trace_manager.capture_state_snapshot(
            trace_id=trace_id,
            run_id=run_id,
            snapshot_type="PAYLOAD_BUILDER_OUTPUT",
            state=redact(payload),
        )

    # ── 5. API call ────────────────────────────────────────────────────────────
    svc = get_service_request_api_service()
    # Explicit guard: only call submit_report when rdd_action is exactly "submit".
    # A missing or empty rdd_action must fail-closed; silently defaulting to
    # submit_report caused accidental POST calls with no action context.
    if rdd_action == "submit":
        result = await svc.submit_report(payload)
    elif rdd_action == "final_approve":
        # V2 workflows require UPDATE (RE_EXAMINE) before APPROVE at RDD level
        # (UPDATE_APPROVE + require_update_before_action), same as FM.
        workflow_version = 1
        try:
            pre_get = await svc.get_service_request(sr_id)
            if getattr(pre_get, "success", False) is True:
                workflow_version = _workflow_version_from_get(pre_get)
                if pre_get.service_request_operations:
                    backend_refs["sr_operations"] = list(pre_get.service_request_operations)
                if pre_get.payload and not backend_refs.get("create_payload"):
                    backend_refs["create_payload"] = {"payload": pre_get.payload}
        except Exception:
            logger.warning(
                "rdd_api_submission_node: pre-PATCH GET failed sr_id=%s (continuing)",
                sr_id,
                exc_info=True,
            )

        if workflow_version >= 2:
            update_payload = dict(payload)
            update_payload["status"] = "RE_EXAMINE"
            update_inner = dict(update_payload.get("payload") or {})
            # Keep pre-action status for UPDATE; top-level carries RE_EXAMINE.
            if not update_inner.get("current_sr_status"):
                update_inner["current_sr_status"] = "REPORT_SUBMITTED"
            update_payload["payload"] = update_inner
            logger.info(
                "rdd_api_submission_node: v2 final_approve pre-step RE_EXAMINE sr_id=%s",
                sr_id,
            )
            update_result = await svc.patch_service_request(sr_id, update_payload)
            if update_result.error is not None:
                logger.warning(
                    "rdd_api_submission_node: v2 RE_EXAMINE failed sr_id=%s error=%s",
                    sr_id,
                    update_result.error,
                )
                return {
                    "backend_refs": backend_refs,
                    "status": "FAILED",
                    "response_message": (
                        "I could not save RDD progress before approving "
                        f"(required for this service request). Detail: {update_result.error}"
                    ),
                }

        logger.info(
            "rdd_api_submission_node: PATCH payload sr_id=%s rdd_action=final_approve body=%s",
            sr_id,
            json.dumps(payload, default=str, ensure_ascii=False),
        )
        result = await svc.patch_service_request(sr_id, payload)
    else:
        return {
            "status": "FAILED",
            "response_message": (
                f"RDD submission blocked: unrecognised rdd_action '{rdd_action}'. "
                "Expected 'submit' or 'final_approve'."
            ),
        }

    logger.info(
        "rdd_api_submission_node: sr_id=%s status_code=%s error=%s latency_ms=%s",
        sr_id,
        result.status_code,
        result.error,
        result.latency_ms,
    )

    api_success = result.error is None and result.sr_id is not None

    # ── 6. Record in trace ─────────────────────────────────────────────────────
    if trace_manager is not None and trace_id is not None and run_id is not None:
        await trace_manager.capture_tool_call(
            trace_id=trace_id,
            run_id=run_id,
            tool_name=_TOOL_NAME,
            tool_type=_TOOL_TYPE,
            request_payload=redact(payload),
            response_payload=result.response_payload,
            status_code=result.status_code,
            success=api_success,
            latency_ms=result.latency_ms,
            error_message=result.error,
        )
        await trace_manager.finish_run(
            run_id=run_id,
            output={
                "sr_id": result.sr_id,
                "status_code": result.status_code,
                "latency_ms": result.latency_ms,
                "error": result.error,
            },
            status="SUCCESS" if api_success else "FAILED",
            error_message=result.error,
        )

    # ── 7. Failure path ────────────────────────────────────────────────────────
    if not api_success:
        error_detail = f" (Detail: {result.error})" if result.error else ""
        return {
            "backend_refs": backend_refs,
            "status": "FAILED",
            "response_message": (
                "I was unable to submit the RDD handover report due to an API error. "
                f"Please try again later.{error_detail}"
            ),
        }

    # ── 8. Success ─────────────────────────────────────────────────────────────
    submitted_sr_id = result.sr_id or sr_id
    backend_refs["rdd_submitted_sr_id"] = submitted_sr_id

    # Audit log
    session_id: UUID | None = _to_uuid(state.get("session_id"))
    user_id: UUID | None = _to_uuid(state.get("user_id"))

    # Phase 3a (submit_report) — SR is not yet complete; awaits final approval
    if rdd_action != "final_approve":
        backend_refs["rdd_status"] = "REPORT_SUBMITTED"
        if trace_manager is not None and session_id is not None:
            try:
                repo = AuditLogRepository(trace_manager._session)
                await repo.create(
                    session_id=session_id,
                    action="sr.rdd.report_submitted",
                    actor_user_id=user_id,
                    after_state={
                        "sr_id": submitted_sr_id,
                        "status_code": result.status_code,
                        "latency_ms": result.latency_ms,
                    },
                    metadata={"endpoint": result.endpoint, "workflow_stage": "RDD_REVIEW"},
                )
            except Exception:
                logger.warning(
                    "rdd_api_submission_node: audit log write failed (non-fatal)", exc_info=True
                )
        return {
            "backend_refs": backend_refs,
            "status": "SUBMITTED",
            # workflow_stage stays RDD_REVIEW — SR is not completed until final approval
            "response_message": (
                f"The RDD Handover Report has been successfully submitted. "
                f"Your reference number is **{submitted_sr_id}**. "
                "Awaiting final approval to complete the service request."
            ),
        }

    # Phase 3b (final_approve) — verify RDD operation advanced on platform
    try:
        get_result = await svc.get_service_request(sr_id)
    except Exception as exc:
        logger.warning(
            "rdd_api_submission_node: post-approve GET failed sr_id=%s error=%s",
            sr_id,
            exc,
            exc_info=True,
        )
        return {
            "backend_refs": backend_refs,
            "status": "FAILED",
            "response_message": (
                f"RDD approve was sent for **{sr_id}**, but I could not verify "
                "that the RDD step finished. Please refresh the service request "
                "and try again if it is still in RDD review."
            ),
        }

    operations: list[dict[str, Any]] = list(
        getattr(get_result, "service_request_operations", None) or []
    )
    if getattr(get_result, "error", None) or not getattr(get_result, "success", True):
        return {
            "backend_refs": backend_refs,
            "status": "FAILED",
            "response_message": (
                f"RDD approve was sent for **{sr_id}**, but verification failed "
                f"({getattr(get_result, 'error', None) or 'GET error'}). "
                "Please refresh the service request and confirm the RDD step."
            ),
        }

    backend_refs["sr_operations"] = operations
    platform_status = getattr(get_result, "status", None)
    if platform_status:
        backend_refs["platform_sr_status"] = platform_status

    if not _rdd_operation_finished(operations):
        logger.error(
            "rdd_api_submission_node: approve PATCH succeeded but RDD op not finished "
            "sr_id=%s operations=%s",
            sr_id,
            operations,
        )
        return {
            "backend_refs": backend_refs,
            "status": "FAILED",
            "response_message": (
                f"The platform accepted the RDD approve for **{sr_id}**, but the "
                "RDD operation is still in progress. The workflow did not complete "
                "— please retry or approve from the portal form."
            ),
        }

    backend_refs["rdd_status"] = "APPROVED"
    if trace_manager is not None and session_id is not None:
        try:
            repo = AuditLogRepository(trace_manager._session)
            await repo.create(
                session_id=session_id,
                action="sr.rdd.final_approved",
                actor_user_id=user_id,
                after_state={
                    "sr_id": submitted_sr_id,
                    "status_code": result.status_code,
                    "latency_ms": result.latency_ms,
                },
                metadata={"endpoint": result.endpoint, "workflow_stage": "SR_COMPLETED"},
            )
        except Exception:
            logger.warning(
                "rdd_api_submission_node: audit log write failed (non-fatal)", exc_info=True
            )
    return {
        "backend_refs": backend_refs,
        "status": "COMPLETED",
        "workflow_stage": "SR_COMPLETED",
        "response_message": (
            f"The RDD Handover Report has been approved. "
            f"Your reference number is **{submitted_sr_id}**. "
            "The service request is now complete."
        ),
    }
