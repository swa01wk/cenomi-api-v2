"""Service Request REST client — adapter, models, mock.

Architecture
------------
- ``ServiceRequestCreationResult`` — canonical result shape for create/submit calls.
- ``ServiceRequestGetResult``      — result shape for GET /service-requests/{sr_id}.
- ``AbstractServiceRequestAPIService`` — adapter interface; swap real ↔ mock.
- ``HttpServiceRequestAPIService`` — production adapter; delegates to
  ``ServiceRequestPlatformClient``.
- ``MockServiceRequestAPIService`` — in-memory POC adapter; zero external deps.
- ``get_service_request_api_service()`` — factory that picks HTTP vs mock.

Security contract
-----------------
- The LLM must never call this service directly.
- Only ``api_submission_node``, ``fm_api_submission_node``, and
  ``rdd_api_submission_node`` are permitted to invoke the write methods.
- Submission is only attempted after confirmation_status == CONFIRMED and all
  blocking validation errors have been cleared.
"""

from __future__ import annotations

import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import structlog

from app.core.config import settings

log = structlog.get_logger(__name__)

_MAX_RESPONSE_BYTES = 32_768  # cap raw body stored in trace payload

# Work Permit V1 subtypes (workflow 5–8). Kept here so the mock does not import
# work_permit_schema (graph nodes already depend on this module).
_WP_HOT_ROOF: frozenset[str] = frozenset(
    {"CONSTRUCTION_HOT", "CONSTRUCTION_ROOF_ACCESS", "MAINTENANCE_ROOF_ACCESS"}
)
_WP_COLD: frozenset[str] = frozenset({"CONSTRUCTION_COLD"})
_WP_MAINT: frozenset[str] = frozenset({"MAINTENANCE_HOT", "MAINTENANCE_COLD"})
_WP_OPS: frozenset[str] = frozenset({"OPERATIONS"})
_WP_SUBTYPES: frozenset[str] = _WP_HOT_ROOF | _WP_COLD | _WP_MAINT | _WP_OPS


def _wp_operations_for_subtype(sub_category: str) -> list[dict[str, Any]]:
    """Seed V1 operations matching platform workflows 5–8."""
    if sub_category in _WP_HOT_ROOF:
        return [
            {"assigned_role": "DD_ENGINEER", "workflow_level": 1, "status": "IN_PROGRESS"},
            {"assigned_role": "FM_MANAGER", "workflow_level": 2, "status": "YET_TO_START"},
            {"assigned_role": "MALL_MANAGER", "workflow_level": 3, "status": "YET_TO_START"},
        ]
    if sub_category in _WP_COLD:
        return [
            {"assigned_role": "DD_ENGINEER", "workflow_level": 1, "status": "IN_PROGRESS"},
            {"assigned_role": "MALL_MANAGER", "workflow_level": 2, "status": "YET_TO_START"},
            {"assigned_role": "OPERATION_EXECUTIVES", "workflow_level": 2, "status": "YET_TO_START"},
            {"assigned_role": "OPERATIONS", "workflow_level": 2, "status": "YET_TO_START"},
        ]
    if sub_category in _WP_MAINT:
        return [
            {"assigned_role": "FM_MANAGER", "workflow_level": 1, "status": "IN_PROGRESS"},
            {"assigned_role": "MALL_MANAGER", "workflow_level": 2, "status": "YET_TO_START"},
        ]
    if sub_category in _WP_OPS:
        return [
            {"assigned_role": "MALL_MANAGER", "workflow_level": 1, "status": "IN_PROGRESS"},
        ]
    return []


def _advance_wp_operations(
    ops: list[dict[str, Any]],
    status: str,
) -> str:
    """Advance WP V1 hops. Returns the new SR status."""
    submitted = (status or "").upper()
    if submitted == "REJECTED":
        for op in ops:
            if (op.get("status") or "").upper() == "IN_PROGRESS":
                op["status"] = "FINISHED"
        return "REJECTED"
    if submitted != "APPROVED":
        return submitted or "IN_PROCESS"

    current_levels = [
        int(op.get("workflow_level") or 0)
        for op in ops
        if (op.get("status") or "").upper() == "IN_PROGRESS"
    ]
    if not current_levels:
        return "APPROVED"
    level = min(current_levels)
    for op in ops:
        if int(op.get("workflow_level") or 0) == level and (
            (op.get("status") or "").upper() == "IN_PROGRESS"
        ):
            op["status"] = "FINISHED"
    next_ops = [op for op in ops if int(op.get("workflow_level") or 0) == level + 1]
    if not next_ops:
        return "APPROVED"
    for op in next_ops:
        op["status"] = "IN_PROGRESS"
    return "IN_PROCESS"


# ---------------------------------------------------------------------------
# Result models
# ---------------------------------------------------------------------------


@dataclass
class ServiceRequestCreationResult:
    """Output of a create_service_request / submit_report / patch call.

    Attributes
    ----------
    sr_id:
        The service request ID assigned/confirmed by the backend.
        ``None`` on failure.
    endpoint:
        Full URL that was (or would have been) called.
    request_payload:
        The exact payload sent to the API (redact before persisting).
    response_payload:
        Parsed JSON response body, or ``None`` when the call failed before a
        response was received.
    latency_ms:
        Wall-clock time in milliseconds.
    status_code:
        HTTP status code returned by the API.  ``None`` on connection error.
    correlation_id:
        Correlation / request ID extracted from response headers or body.
    error:
        Human-readable error string.  ``None`` on success.
    """

    sr_id: str | None
    endpoint: str
    request_payload: dict[str, Any]
    response_payload: dict[str, Any] | None
    latency_ms: int
    status_code: int | None
    correlation_id: str | None = None
    error: str | None = None


# Re-export ServiceRequestGetResult from the platform client for convenience.
from app.agents.services.platform_api_client import ServiceRequestGetResult  # noqa: E402


# ---------------------------------------------------------------------------
# Abstract adapter
# ---------------------------------------------------------------------------


class AbstractServiceRequestAPIService(ABC):
    """Interface for the Service Request API backend; swap real ↔ mock without
    changing the submission nodes."""

    @abstractmethod
    async def create_service_request(
        self,
        payload: dict[str, Any],
    ) -> ServiceRequestCreationResult:
        """POST a new Service Request."""

    @abstractmethod
    async def get_service_request(self, sr_id: str) -> ServiceRequestGetResult:
        """GET an existing SR by ID (status sync)."""

    @abstractmethod
    async def patch_service_request(
        self,
        sr_id: str,
        payload: dict[str, Any],
    ) -> ServiceRequestCreationResult:
        """PATCH an existing SR (FM save progress / approve)."""

    @abstractmethod
    async def submit_report(
        self,
        payload: dict[str, Any],
    ) -> ServiceRequestCreationResult:
        """POST a report payload (RDD REPORT_SUBMITTED stage).

        Uses the same POST /service-requests endpoint as create but includes
        an existing ``service_request_id`` in the payload and sets
        ``status=REPORT_SUBMITTED``.
        """


# ---------------------------------------------------------------------------
# HTTP adapter (production)
# ---------------------------------------------------------------------------


class HttpServiceRequestAPIService(AbstractServiceRequestAPIService):
    """Production adapter — delegates all platform calls to
    ``ServiceRequestPlatformClient``.
    """

    def __init__(self) -> None:
        from app.agents.services.platform_api_client import get_platform_client
        self._client = get_platform_client()

    async def create_service_request(
        self,
        payload: dict[str, Any],
    ) -> ServiceRequestCreationResult:
        """POST /service-requests and return a ``ServiceRequestCreationResult``."""
        wall_start = time.monotonic()
        result = await self._client.create_service_request(payload)
        latency_ms = result.latency_ms or int((time.monotonic() - wall_start) * 1000)

        if not result.success or result.error:
            log.warning(
                "sr_api.create.failed",
                status_code=result.status_code,
                error=result.error,
            )
            return ServiceRequestCreationResult(
                sr_id=None,
                endpoint=result.endpoint,
                request_payload=payload,
                response_payload=result.response_payload,
                latency_ms=latency_ms,
                status_code=result.status_code,
                correlation_id=result.correlation_id,
                error=result.error or f"HTTP {result.status_code}",
            )

        sr_id = result.sr_id or self._extract_sr_id(result.response_payload)
        log.info(
            "sr_api.create.success",
            sr_id=sr_id,
            latency_ms=latency_ms,
        )
        return ServiceRequestCreationResult(
            sr_id=sr_id,
            endpoint=result.endpoint,
            request_payload=payload,
            response_payload=result.response_payload,
            latency_ms=latency_ms,
            status_code=result.status_code,
            correlation_id=result.correlation_id,
        )

    async def get_service_request(self, sr_id: str) -> ServiceRequestGetResult:
        """GET /service-requests/{sr_id}."""
        return await self._client.get_service_request(sr_id)

    async def patch_service_request(
        self,
        sr_id: str,
        payload: dict[str, Any],
    ) -> ServiceRequestCreationResult:
        """PATCH /service-requests/{sr_id}."""
        wall_start = time.monotonic()
        result = await self._client.patch_service_request(sr_id, payload)
        latency_ms = result.latency_ms or int((time.monotonic() - wall_start) * 1000)

        if not result.success:
            log.warning(
                "sr_api.patch.failed",
                sr_id=sr_id,
                status_code=result.status_code,
                error=result.error,
            )
            return ServiceRequestCreationResult(
                sr_id=sr_id,
                endpoint=result.endpoint,
                request_payload=payload,
                response_payload=result.response_payload,
                latency_ms=latency_ms,
                status_code=result.status_code,
                correlation_id=result.correlation_id,
                error=result.error,
            )

        # Use sr_id from result (already extracted by client) or fall back to the
        # known sr_id that was passed in.
        confirmed_sr_id = result.sr_id or sr_id
        log.info("sr_api.patch.success", sr_id=confirmed_sr_id, latency_ms=latency_ms)
        return ServiceRequestCreationResult(
            sr_id=confirmed_sr_id,
            endpoint=result.endpoint,
            request_payload=payload,
            response_payload=result.response_payload,
            latency_ms=latency_ms,
            status_code=result.status_code,
            correlation_id=result.correlation_id,
        )

    async def submit_report(
        self,
        payload: dict[str, Any],
    ) -> ServiceRequestCreationResult:
        """POST /service-requests with status=REPORT_SUBMITTED."""
        wall_start = time.monotonic()
        result = await self._client.submit_report(payload)
        latency_ms = result.latency_ms or int((time.monotonic() - wall_start) * 1000)

        sr_id = payload.get("service_request_id") or self._extract_sr_id(result.response_payload)

        if not result.success:
            log.warning(
                "sr_api.submit_report.failed",
                sr_id=sr_id,
                status_code=result.status_code,
                error=result.error,
            )
            return ServiceRequestCreationResult(
                sr_id=sr_id,
                endpoint=result.endpoint,
                request_payload=payload,
                response_payload=result.response_payload,
                latency_ms=latency_ms,
                status_code=result.status_code,
                correlation_id=result.correlation_id,
                error=result.error,
            )

        confirmed_sr_id = self._extract_sr_id(result.response_payload) or sr_id
        log.info(
            "sr_api.submit_report.success",
            sr_id=confirmed_sr_id,
            latency_ms=latency_ms,
        )
        return ServiceRequestCreationResult(
            sr_id=confirmed_sr_id,
            endpoint=result.endpoint,
            request_payload=payload,
            response_payload=result.response_payload,
            latency_ms=latency_ms,
            status_code=result.status_code,
            correlation_id=result.correlation_id,
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_sr_id(body: dict[str, Any] | None) -> str | None:
        """Extract the service request ID from various response envelope shapes."""
        if not isinstance(body, dict):
            return None
        for key in ("id", "sr_id", "service_request_id"):
            val = body.get(key)
            if val is not None:
                return str(val)
        nested = body.get("data")
        if isinstance(nested, dict):
            for key in ("id", "sr_id", "service_request_id"):
                val = nested.get(key)
                if val is not None:
                    return str(val)
        return None


# ---------------------------------------------------------------------------
# Mock adapter (local POC / testing)
# ---------------------------------------------------------------------------

# Module-level store so SR state survives across HTTP requests in mock mode.
# Tests that instantiate MockServiceRequestAPIService directly with
# force_error/force_status_code always get their own fresh _sr_states via the
# instance override, so test isolation is not affected.
_MOCK_PERSISTENT_SR_STATES: dict[str, dict[str, Any]] = {}


class MockServiceRequestAPIService(AbstractServiceRequestAPIService):
    """In-memory SR service mock for local POC development and unit tests.

    When ``SERVICE_REQUEST_API_BASE_URL`` is not set the factory returns this
    mock.  SR state (operations list, status) is stored in the module-level
    ``_MOCK_PERSISTENT_SR_STATES`` dict so that status-sync works across
    multiple HTTP turns — enabling full lifecycle testing without a live
    platform connection.

    Pass ``isolated=True`` (or any ``force_*`` argument) to get a fresh
    per-instance state dict; this is what unit/integration tests do when they
    patch the service directly.
    """

    _MOCK_ENDPOINT_BASE = "mock://service-request-api/service-requests"

    def __init__(
        self,
        simulated_latency_ms: int = 15,
        force_error: str | None = None,
        force_status_code: int | None = None,
        isolated: bool = False,
    ) -> None:
        self._latency_ms = simulated_latency_ms
        self._force_error = force_error
        self._force_status_code = force_status_code
        self._created: list[dict[str, Any]] = []
        self._patched: list[dict[str, Any]] = []
        self._submitted: list[dict[str, Any]] = []
        # Use the shared persistent store unless explicitly isolated (tests).
        if isolated or force_error or force_status_code:
            self._sr_states: dict[str, dict[str, Any]] = {}
        else:
            self._sr_states = _MOCK_PERSISTENT_SR_STATES

    @property
    def created_requests(self) -> list[dict[str, Any]]:
        return list(self._created)

    @property
    def patched_requests(self) -> list[dict[str, Any]]:
        return list(self._patched)

    @property
    def submitted_reports(self) -> list[dict[str, Any]]:
        return list(self._submitted)

    async def create_service_request(
        self,
        payload: dict[str, Any],
    ) -> ServiceRequestCreationResult:
        endpoint = self._MOCK_ENDPOINT_BASE
        if self._force_error:
            sc = self._force_status_code or 500
            return ServiceRequestCreationResult(
                sr_id=None,
                endpoint=endpoint,
                request_payload=payload,
                response_payload=None,
                latency_ms=self._latency_ms,
                status_code=sc,
                error=self._force_error,
            )

        sr_id = str(uuid.uuid4())
        correlation_id = str(uuid.uuid4())
        self._created.append({"sr_id": sr_id, "payload": payload})

        # Seed GET state — WP V1, fitout confirm, or handover FM → DD.
        sub_category: str = str(payload.get("sub_category") or "").upper()
        service_category: str = str(payload.get("service_category") or "").upper()
        _FITOUT_FOS_END = frozenset({"FIT_OUT_CONFIRM_FOS", "FIT_OUT_CONFIRM_END"})
        _FITOUT_TRADING = frozenset({"FIT_OUT_CONFIRM_TRADING"})
        is_wp = service_category == "WORK_PERMIT" or sub_category in _WP_SUBTYPES
        if is_wp:
            ops = _wp_operations_for_subtype(sub_category)
            sr_status = "IN_PROCESS"
            sr_category = "WORK_PERMIT"
        elif sub_category in _FITOUT_FOS_END:
            ops = [{"assigned_role": "DD_ENGINEER", "workflow_level": 1, "status": "IN_PROGRESS"}]
            sr_status = "SUBMITTED"
            sr_category = "FIT_OUT_AND_HANDOVER"
        elif sub_category in _FITOUT_TRADING:
            ops = [{"assigned_role": "MALL_MANAGER", "workflow_level": 1, "status": "IN_PROGRESS"}]
            sr_status = "SUBMITTED"
            sr_category = "FIT_OUT_AND_HANDOVER"
        else:
            ops = [
                {"assigned_role": "MALL_MANAGER", "workflow_level": 1, "status": "FINISHED"},
                # Only the selected inspector role is active (matches platform).
                {"assigned_role": "FM_MANAGER", "workflow_level": 2, "status": "IN_PROGRESS"},
                {"assigned_role": "DD_ENGINEER", "workflow_level": 3, "status": "YET_TO_START"},
            ]
            sr_status = "SUBMITTED"
            sr_category = "FIT_OUT_AND_HANDOVER"

        # Persist the inner payload and sub_category so that GET /service-requests/{sr_id}
        # can return them for status-sync and sub_category inference.
        inner_payload: dict[str, Any] = dict(payload.get("payload") or {})
        if sub_category:
            inner_payload["sub_category"] = sub_category
        inner_payload.setdefault("category", sr_category)
        inner_payload.setdefault("service_category", sr_category)

        self._sr_states[sr_id] = {
            "service_request_id": sr_id,
            "status": sr_status,
            "category": sr_category,
            "sub_category": sub_category,
            "service_request_operations": ops,
            "workflow_version": 1,
            "payload": inner_payload,
        }
        return ServiceRequestCreationResult(
            sr_id=sr_id,
            endpoint=endpoint,
            request_payload=payload,
            response_payload={"success": True, "data": {"service_request_id": sr_id}},
            latency_ms=self._latency_ms,
            status_code=self._force_status_code or 201,
            correlation_id=correlation_id,
        )

    async def get_service_request(self, sr_id: str) -> ServiceRequestGetResult:
        from app.agents.services.platform_api_client import ServiceRequestGetResult as _GR
        state = self._sr_states.get(sr_id)
        if not state:
            return _GR(
                success=False,
                sr_id=sr_id,
                status=None,
                service_request_operations=[],
                endpoint=f"{self._MOCK_ENDPOINT_BASE}/{sr_id}",
                status_code=404,
                latency_ms=self._latency_ms,
                error="SR not found in mock",
            )
        payload = dict(state.get("payload") or {})
        if state.get("workflow_version") is not None:
            payload["workflow_version"] = state["workflow_version"]
        if state.get("category") and not payload.get("category"):
            payload["category"] = state["category"]
        if state.get("sub_category") and not payload.get("sub_category"):
            payload["sub_category"] = state["sub_category"]
        if state.get("category") == "WORK_PERMIT":
            payload.setdefault("service_category", "WORK_PERMIT")
        return _GR(
            success=True,
            sr_id=sr_id,
            status=state.get("status"),
            service_request_operations=state.get("service_request_operations", []),
            payload=payload or None,
            endpoint=f"{self._MOCK_ENDPOINT_BASE}/{sr_id}",
            status_code=200,
            latency_ms=self._latency_ms,
        )

    async def patch_service_request(
        self,
        sr_id: str,
        payload: dict[str, Any],
    ) -> ServiceRequestCreationResult:
        endpoint = f"{self._MOCK_ENDPOINT_BASE}/{sr_id}"
        if self._force_error:
            sc = self._force_status_code or 500
            return ServiceRequestCreationResult(
                sr_id=sr_id,
                endpoint=endpoint,
                request_payload=payload,
                response_payload=None,
                latency_ms=self._latency_ms,
                status_code=sc,
                error=self._force_error,
            )
        self._patched.append({"sr_id": sr_id, "payload": payload})
        status = payload.get("status", "IN_PROCESS")
        # Advance mock state
        if sr_id in self._sr_states:
            is_wp = (
                self._sr_states[sr_id].get("category") == "WORK_PERMIT"
                or str(self._sr_states[sr_id].get("sub_category") or "").upper() in _WP_SUBTYPES
            )
            # V2 UPDATE (RE_EXAMINE) keeps FM IN_PROGRESS — same as platform TransitionTypeStay.
            if status == "RE_EXAMINE":
                self._sr_states[sr_id]["status"] = "IN_PROCESS"
            elif is_wp:
                ops = self._sr_states[sr_id].get("service_request_operations", [])
                self._sr_states[sr_id]["status"] = _advance_wp_operations(ops, status)
            else:
                self._sr_states[sr_id]["status"] = status
            if (not is_wp) and status == "APPROVED":
                ops = self._sr_states[sr_id].get("service_request_operations", [])
                # Determine who was active before this PATCH so we can correctly
                # advance the workflow:
                #   FM approval  → FM_MANAGER was IN_PROGRESS → DD_ENGINEER moves to IN_PROGRESS
                #   RDD final    → DD_ENGINEER was IN_PROGRESS → DD_ENGINEER moves to FINISHED
                fm_was_active = any(
                    op.get("assigned_role") in ("FM_MANAGER", "OPERATIONS")
                    and (op.get("status") or "").upper() == "IN_PROGRESS"
                    for op in ops
                )
                for op in ops:
                    role = op.get("assigned_role", "")
                    op_status = (op.get("status") or "").upper()
                    if role in ("FM_MANAGER", "OPERATIONS"):
                        # Only finish ops that were active; leave YET_TO_START alone.
                        if op_status in ("IN_PROGRESS", "FINISHED", "APPROVED"):
                            op["status"] = "FINISHED"
                    if role == "DD_ENGINEER":
                        if fm_was_active:
                            # FM just approved → DD_ENGINEER becomes the next active role.
                            op["status"] = "IN_PROGRESS"
                        else:
                            # RDD final approve → DD_ENGINEER step is complete.
                            op["status"] = "FINISHED"
        return ServiceRequestCreationResult(
            sr_id=sr_id,
            endpoint=endpoint,
            request_payload=payload,
            response_payload={"success": True, "data": "Successfully processed"},
            latency_ms=self._latency_ms,
            status_code=200,
        )

    async def submit_report(
        self,
        payload: dict[str, Any],
    ) -> ServiceRequestCreationResult:
        endpoint = self._MOCK_ENDPOINT_BASE
        sr_id = payload.get("service_request_id") or str(uuid.uuid4())
        if self._force_error:
            sc = self._force_status_code or 500
            return ServiceRequestCreationResult(
                sr_id=sr_id,
                endpoint=endpoint,
                request_payload=payload,
                response_payload=None,
                latency_ms=self._latency_ms,
                status_code=sc,
                error=self._force_error,
            )
        self._submitted.append({"sr_id": sr_id, "payload": payload})
        if sr_id in self._sr_states:
            self._sr_states[sr_id]["status"] = "REPORT_SUBMITTED"
            # Keep DD_ENGINEER IN_PROGRESS so sr_status_sync_node continues
            # to route to RDD_REVIEW for the final-approve turn.
            ops = self._sr_states[sr_id].get("service_request_operations", [])
            for op in ops:
                if op["assigned_role"] == "DD_ENGINEER":
                    op["status"] = "IN_PROGRESS"
        return ServiceRequestCreationResult(
            sr_id=sr_id,
            endpoint=endpoint,
            request_payload=payload,
            response_payload={"success": True, "data": {"service_request_id": sr_id}},
            latency_ms=self._latency_ms,
            status_code=self._force_status_code or 201,
        )


# ---------------------------------------------------------------------------
# Factory helper
# ---------------------------------------------------------------------------


def get_service_request_api_service() -> AbstractServiceRequestAPIService:
    """Return the appropriate service-request adapter.

    ``MockServiceRequestAPIService`` is used when either:
    - No ``SERVICE_REQUEST_API_BASE_URL`` is configured (local/POC mode), or
    - ``SERVICE_REQUEST_USE_MOCK=true`` is set (local eval runs where the UAT API
      rejects the chatbot's locally-signed JWT).
    """
    if settings.service_request_api_base_url and not settings.service_request_use_mock:
        return HttpServiceRequestAPIService()
    return MockServiceRequestAPIService()


def reset_mock_sr_state() -> None:
    """Clear the persistent mock SR state store.

    Call this between lifecycle test runs (e.g. via a dev admin endpoint or
    directly in test teardown) to start fresh without restarting the server.
    Only has effect when running in mock mode (no SERVICE_REQUEST_API_BASE_URL).
    """
    _MOCK_PERSISTENT_SR_STATES.clear()
