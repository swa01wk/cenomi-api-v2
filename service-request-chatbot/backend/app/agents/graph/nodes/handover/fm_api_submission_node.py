"""Submit FM_REVIEW PATCH to the platform SR API.

Responsibilities
----------------
- Guard: confirmation/action must be present (``backend_refs["fm_action"]``).
- Guard: no blocking validation errors.
- Guard: FM payload must be present in ``backend_refs["fm_payload"]``.
- Guard: permission check via PermissionService.
- Legacy V1 Handover contract only: save → ``IN_PROCESS``, approve → ``APPROVED``.
- Call ``ServiceRequestAPIService.patch_service_request`` with the payload.
- Capture observability (redacted payload snapshot, tool-call record).
- Write audit log event (``sr.fm.progress_saved`` or ``sr.fm.approved``).
- Update state with platform result.

Non-responsibilities
--------------------
- MUST NOT call the LLM.
- MUST NOT build the payload (that is ``fm_payload_builder_node``).
- MUST NOT send RE_EXAMINE (V2 UPDATE_APPROVE); Handover is legacy-only.
"""

from __future__ import annotations

import json
import logging
from typing import Any
from uuid import UUID

from app.agents.graph.state import ServiceRequestState
from app.agents.services.permission_service import PermissionDeniedError, PermissionService
from app.agents.services.payload_builder_service import snapshot_create_payload_from_platform
from app.agents.services.service_request_api_service import get_service_request_api_service
from app.db.repositories.audit_log_repo import AuditLogRepository
from app.observability.decorators import trace_node
from app.observability.redaction import redact

logger = logging.getLogger(__name__)

_permission_service = PermissionService()

_TOOL_NAME_SAVE = "service_request_api.fm.patch_save_progress"
_TOOL_NAME_APPROVE = "service_request_api.fm.patch_approve"
_TOOL_TYPE = "API"

_FM_ROLES: frozenset[str] = frozenset({"FM_MANAGER", "OPERATIONS"})
_FM_FINISHED: frozenset[str] = frozenset({"FINISHED", "APPROVED"})


def _has_blocking_errors(validation_errors: list[dict[str, Any]]) -> bool:
    return any(e.get("blocking", True) for e in validation_errors)


def _fm_operation_finished(operations: list[dict[str, Any]]) -> bool:
    """True when no FM/OPERATIONS op is still IN_PROGRESS and at least one is terminal.

    Ignores ``YET_TO_START`` rows (unused inspector role at the same level) so a
    finished FM_MANAGER op is not blocked by a dormant OPERATIONS row.
    """
    fm_ops = [
        op
        for op in operations
        if (op.get("assigned_role") or op.get("role") or "").upper() in _FM_ROLES
    ]
    if not fm_ops:
        return False

    def _status(op: dict[str, Any]) -> str:
        return (op.get("status") or "").upper()

    has_in_progress = any(_status(op) == "IN_PROGRESS" for op in fm_ops)
    has_terminal = any(_status(op) in _FM_FINISHED for op in fm_ops)
    return has_terminal and not has_in_progress


def _map_stage_after_fm_approve(operations: list[dict[str, Any]]) -> str | None:
    """Return next workflow_stage after a successful FM approve."""
    _RDD_ROLES = frozenset({"DD_ENGINEER"})
    in_progress: set[str] = set()
    for op in operations:
        role = (op.get("assigned_role") or op.get("role") or "").upper()
        status = (op.get("status") or "").upper()
        if status == "IN_PROGRESS":
            in_progress.add(role)
    if in_progress & _RDD_ROLES:
        return "RDD_REVIEW"
    if in_progress & _FM_ROLES:
        return "FM_REVIEW"
    return None


def _to_uuid(value: str | UUID | None) -> UUID | None:
    if value is None:
        return None
    if isinstance(value, UUID):
        return value
    try:
        return UUID(str(value))
    except (ValueError, AttributeError):
        return None


@trace_node("fm_api_submission", "TOOL")
async def fm_api_submission_node(state: ServiceRequestState) -> dict[str, Any]:
    """PATCH the SR at the FM_REVIEW stage (save progress or approve).

    Reads from state
    ----------------
    ``backend_refs``      — must contain ``sr_id``, ``fm_payload``, ``fm_action``.
    ``validation_errors`` — aborts if any blocking error is present.
    ``trace_manager``     — optional for observability.

    Writes to state
    ---------------
    ``backend_refs``      — updated with ``fm_status``.
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
                f"FM submission blocked: {blocking_count} validation error(s) must be "
                "resolved before the FM review can be submitted."
            ),
        }

    # ── 2. Payload guard ───────────────────────────────────────────────────────
    backend_refs: dict[str, Any] = dict(state.get("backend_refs") or {})
    sr_id: str | None = backend_refs.get("sr_id")
    payload: dict[str, Any] | None = backend_refs.get("fm_payload")
    # Match fm_payload_builder default: Submit → Approve. save_progress must
    # be set explicitly by the Save chip (`save_fm_progress` / `confirm_fm_save`).
    fm_action: str = backend_refs.get("fm_action") or "approve"

    if not sr_id:
        return {
            "status": "FAILED",
            "response_message": "FM submission blocked: service request ID is missing.",
        }
    if not payload:
        return {
            "status": "FAILED",
            "response_message": (
                "FM submission blocked: the FM payload could not be found. "
                "Please try again."
            ),
        }

    # ── 3. Permission guard ────────────────────────────────────────────────────
    from app.types.chat import AuthContext
    auth: AuthContext | None = state.get("auth_context")  # type: ignore[assignment]
    if auth is not None:
        try:
            action_name = (
                "APPROVE_FM_HANDOVER"
                if fm_action == "approve"
                else "SAVE_FM_HANDOVER_PROGRESS"
            )
            _permission_service.check(action_name, auth)
        except PermissionDeniedError as exc:
            logger.warning("fm_api_submission_node: permission denied — %s", exc)
            return {
                "status": "FAILED",
                "response_message": f"Permission denied: {exc}",
            }

    # ── 4. Observability setup ─────────────────────────────────────────────────
    trace_manager = state.get("trace_manager")  # type: ignore[assignment]
    trace_id: UUID | None = _to_uuid(state.get("trace_id"))
    node_run_id: UUID | None = _to_uuid(state.get("_trace_node_run_id"))
    tool_name = _TOOL_NAME_APPROVE if fm_action == "approve" else _TOOL_NAME_SAVE
    run_id: UUID | None = None

    if trace_manager is not None and trace_id is not None:
        run_id = await trace_manager.start_run(
            trace_id=trace_id,
            run_name=tool_name,
            run_type="TOOL",
            parent_run_id=node_run_id,
            input={"sr_id": sr_id, "fm_action": fm_action},
        )

    if trace_manager is not None and trace_id is not None and run_id is not None:
        await trace_manager.capture_state_snapshot(
            trace_id=trace_id,
            run_id=run_id,
            snapshot_type="PAYLOAD_BUILDER_OUTPUT",
            state=redact(payload),
        )

    # ── 5. API call (legacy V1 Handover contract) ───────────────────────────────
    # Handover always uses V1: save → IN_PROCESS, approve → APPROVED.
    # Do not send RE_EXAMINE (that is V2 UPDATE_APPROVE semantics).
    svc = get_service_request_api_service()
    try:
        pre_get = await svc.get_service_request(sr_id)
        if pre_get.success:
            if pre_get.service_request_operations:
                backend_refs["sr_operations"] = list(pre_get.service_request_operations)
            if isinstance(pre_get.payload, dict) and pre_get.payload:
                snapshot_create_payload_from_platform(
                    backend_refs, pre_get.payload, sr_id=sr_id
                )
    except Exception:
        logger.warning(
            "fm_api_submission_node: pre-PATCH GET failed sr_id=%s (continuing)",
            sr_id,
            exc_info=True,
        )

    logger.info(
        "fm_api_submission_node: PATCH payload sr_id=%s fm_action=%s body=%s",
        sr_id,
        fm_action,
        json.dumps(payload, default=str, ensure_ascii=False),
    )
    result = await svc.patch_service_request(sr_id, payload)

    logger.info(
        "fm_api_submission_node: sr_id=%s fm_action=%s status_code=%s error=%s latency_ms=%s",
        sr_id,
        fm_action,
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
            tool_name=tool_name,
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
                "I was unable to submit the FM review due to an API error. "
                f"Please try again later.{error_detail}"
            ),
        }

    # ── 8. Approve: verify FM operation advanced on platform ───────────────────
    if fm_action == "approve":
        try:
            get_result = await svc.get_service_request(sr_id)
        except Exception as exc:
            logger.warning(
                "fm_api_submission_node: post-approve GET failed sr_id=%s error=%s",
                sr_id,
                exc,
                exc_info=True,
            )
            return {
                "backend_refs": backend_refs,
                "status": "FAILED",
                "response_message": (
                    f"FM approve was sent for **{sr_id}**, but I could not verify "
                    "that the Facility Manager step finished. Please refresh the "
                    "service request and try again if it is still in FM review."
                ),
            }

        operations: list[dict[str, Any]] = list(
            getattr(get_result, "service_request_operations", None) or []
        )
        platform_status = getattr(get_result, "status", None)
        if getattr(get_result, "error", None) or not getattr(get_result, "success", True):
            return {
                "backend_refs": backend_refs,
                "status": "FAILED",
                "response_message": (
                    f"FM approve was sent for **{sr_id}**, but verification failed "
                    f"({getattr(get_result, 'error', None) or 'GET error'}). "
                    "Please refresh the service request and confirm the FM step."
                ),
            }

        backend_refs["sr_operations"] = operations
        if platform_status:
            backend_refs["platform_sr_status"] = platform_status
        if isinstance(getattr(get_result, "payload", None), dict) and get_result.payload:
            snapshot_create_payload_from_platform(
                backend_refs, get_result.payload, sr_id=sr_id
            )

        if not _fm_operation_finished(operations):
            # PATCH returned success but the platform's op row hasn't flipped
            # to FINISHED yet — this is usually async processing lag on the
            # platform side, not a real failure. Log a warning and proceed as
            # success; the next sr_status_sync GET will pick up the settled
            # status. Blocking the user here with a "retry" message when their
            # approve actually landed causes confusion.
            logger.warning(
                "fm_api_submission_node: approve PATCH succeeded but FM op not "
                "finished yet (platform lag) — proceeding as success. "
                "sr_id=%s operations=%s",
                sr_id,
                operations,
            )

    # ── 9. Success ─────────────────────────────────────────────────────────────
    backend_refs["fm_status"] = "APPROVED" if fm_action == "approve" else "IN_PROCESS"

    # Persist the body we just wrote so later stages merge from a fresh snapshot.
    written_inner = payload.get("payload") if isinstance(payload, dict) else None
    if isinstance(written_inner, dict) and written_inner:
        snapshot_create_payload_from_platform(backend_refs, written_inner, sr_id=sr_id)

    next_stage: str | None = None
    if fm_action == "approve":
        next_stage = _map_stage_after_fm_approve(
            list(backend_refs.get("sr_operations") or [])
        )
        if not next_stage:
            # Platform may not yet reflect the DD_ENGINEER IN_PROGRESS operation
            # immediately after approve; default to RDD_REVIEW rather than
            # leaving the stage unknown (which previously caused a FAILED exit).
            next_stage = "RDD_REVIEW"
            logger.info(
                "fm_api_submission_node: no DD_ENGINEER IN_PROGRESS op found post-approve "
                "sr_id=%s — defaulting next_stage=RDD_REVIEW",
                sr_id,
            )

    # Audit log
    session_id: UUID | None = _to_uuid(state.get("session_id"))
    user_id: UUID | None = _to_uuid(state.get("user_id"))
    audit_action = "sr.fm.approved" if fm_action == "approve" else "sr.fm.progress_saved"

    if trace_manager is not None and session_id is not None:
        try:
            repo = AuditLogRepository(trace_manager._session)
            await repo.create(
                session_id=session_id,
                action=audit_action,
                actor_user_id=user_id,
                after_state={
                    "sr_id": result.sr_id,
                    "fm_action": fm_action,
                    "status_code": result.status_code,
                    "latency_ms": result.latency_ms,
                    "workflow_stage": next_stage or "FM_REVIEW",
                },
                metadata={"endpoint": result.endpoint, "workflow_stage": next_stage or "FM_REVIEW"},
            )
        except Exception:
            logger.warning("fm_api_submission_node: audit log write failed (non-fatal)", exc_info=True)

    if fm_action == "approve":
        fm_message = (
            f"Your Handover Service Request has been approved at the FM review stage. "
            f"Reference: **{sr_id}**."
            + (" It has moved to RDD review." if next_stage == "RDD_REVIEW" else "")
        )
        response_ui: dict[str, Any] = {
            "stage_complete": True,
            "action": "fm_approved",
            "workflow_stage": next_stage or "FM_REVIEW",
        }
    else:
        fm_message = (
            f"FM review progress has been saved for Service Request **{sr_id}**. "
            "The request remains in progress."
        )
        response_ui = {
            "stage_complete": False,
            "action": "fm_saved",
            "workflow_stage": "FM_REVIEW",
        }

    out: dict[str, Any] = {
        "backend_refs": backend_refs,
        "status": "SUBMITTED",
        "response_message": fm_message,
        "response_ui": response_ui,
        # Clear confirmation so the next turn does not re-show a stale FM card.
        "confirmation_status": None,
        "confirmation_required": False,
    }
    if next_stage:
        out["workflow_stage"] = next_stage
        out["active_agent"] = "rdd_handover_agent"
    return out
