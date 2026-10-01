"""Notification inbox — fetch action-required notifications from the platform.

Platform contract (cenomi-api-2, TC-3520, ``docs/NOTIFICATION_AGENT_TOOLS_SPEC.md``):
sub-category-scoped GET endpoints under ``/notifications/agent/...`` (Handover,
Fit-out Confirm, Sales Data, and seven Work Permit subtypes), authenticated
with the ``x-internal-api-token`` header ONLY (no user Bearer, no S2S login).
The platform:

- always filters to ``action_required = TRUE`` rows,
- intersects the caller's roles (loaded server-side from ``tenant_contacts``)
  with each tool's audience and returns an empty list on no intersection,
- returns ``{"success": true, "data": {"sub_category", "total_count",
  "current_page", "limit", "list": [...]}}`` where each item's
  ``text_content = {"message", "type", "id"}`` and ``text_content.id`` is the
  **service request id** the notification is about.

Adapter triad mirrors ``lease_lookup_service`` / ``service_request_api_service``:
Abstract + Http + Mock + ``get_notification_inbox_service()`` factory gated by
``NOTIFICATIONS_USE_MOCK``.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from datetime import datetime, timezone
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import httpx
import structlog

from app.agents.schemas.ceiling_schema import TENANT_ROLES
from app.agents.schemas.help_agent_schema import (
    SALES_DATA_APPROVER_PLATFORM_ROLES,
    SALES_DATA_TENANT_PLATFORM_ROLES,
)
from app.core.config import settings

log = structlog.get_logger(__name__)

_MAX_RESPONSE_BYTES = 4096

# Fixed display order — groups render in this sequence.
# FIT_OUT_CONFIRM_HO is decommissioned (product decision, Aug 2026) — the
# platform tool still exists but this service no longer calls it.
# Work Permit: seven subtype-scoped agent tools (TC-3520 style).
NOTIFICATION_SUB_CATEGORIES: tuple[str, ...] = (
    "HANDOVER",
    "FIT_OUT_CONFIRM_FOS",
    "FIT_OUT_CONFIRM_END",
    "FIT_OUT_CONFIRM_TRADING",
    "SALES_DATA",
    "CONSTRUCTION_HOT",
    "CONSTRUCTION_COLD",
    "CONSTRUCTION_ROOF_ACCESS",
    "MAINTENANCE_HOT",
    "MAINTENANCE_COLD",
    "MAINTENANCE_ROOF_ACCESS",
    "OPERATIONS",
    "OPEN_CEILING_INSPECTION",
    "CLOSE_CEILING_INSPECTION",
)

# Sub-categories only the mock inbox serves; the HTTP adapter skips them so a
# live inbox never reports a 404 as a partial failure. Empty: the ceiling
# inspection routes now exist on the platform
# (/notifications/agent/{open,close}-ceiling-inspection).
MOCK_ONLY_SUB_CATEGORIES: frozenset[str] = frozenset()

_SUB_CATEGORY_PATHS: dict[str, str] = {
    "HANDOVER": "/notifications/agent/handover",
    "FIT_OUT_CONFIRM_FOS": "/notifications/agent/fit-out-confirm-fos",
    "FIT_OUT_CONFIRM_END": "/notifications/agent/fit-out-confirm-end",
    "FIT_OUT_CONFIRM_TRADING": "/notifications/agent/fit-out-confirm-trading",
    "SALES_DATA": "/notifications/agent/sales-data",
    "CONSTRUCTION_HOT": "/notifications/agent/construction-hot",
    "CONSTRUCTION_COLD": "/notifications/agent/construction-cold",
    "CONSTRUCTION_ROOF_ACCESS": "/notifications/agent/construction-roof-access",
    "MAINTENANCE_HOT": "/notifications/agent/maintenance-hot",
    "MAINTENANCE_COLD": "/notifications/agent/maintenance-cold",
    "MAINTENANCE_ROOF_ACCESS": "/notifications/agent/maintenance-roof-access",
    "OPERATIONS": "/notifications/agent/operations",
    "OPEN_CEILING_INSPECTION": "/notifications/agent/open-ceiling-inspection",
    "CLOSE_CEILING_INSPECTION": "/notifications/agent/close-ceiling-inspection",
}

# Tool audiences per the platform spec.  Used only to SKIP endpoints the user
# cannot have rows on (latency optimisation) — the platform re-intersects
# server-side, so calling everything is always safe.
_WP_HOT_ROOF_AUDIENCE: frozenset[str] = frozenset(
    {"DD_ENGINEER", "FM_MANAGER", "MALL_MANAGER"}
)
_WP_COLD_AUDIENCE: frozenset[str] = frozenset(
    {"DD_ENGINEER", "MALL_MANAGER", "OPERATIONS", "OPERATION_EXECUTIVES"}
)
_WP_MAINTENANCE_AUDIENCE: frozenset[str] = frozenset({"FM_MANAGER", "MALL_MANAGER"})
_CEILING_AUDIENCE: frozenset[str] = frozenset({"DD_ENGINEER", "FM_MANAGER"}) | TENANT_ROLES

_SUB_CATEGORY_AUDIENCE: dict[str, frozenset[str]] = {
    "HANDOVER": frozenset({"MALL_MANAGER", "FM_MANAGER", "OPERATIONS", "DD_ENGINEER"}),
    "FIT_OUT_CONFIRM_FOS": frozenset({"DD_ENGINEER"}),
    "FIT_OUT_CONFIRM_END": frozenset({"DD_ENGINEER"}),
    # Trading confirmations are raised against the mall, not RDD — the
    # scheduler targets MALL_MANAGER and OPERATIONS (mirrors the Go handler).
    "FIT_OUT_CONFIRM_TRADING": frozenset({"MALL_MANAGER", "OPERATIONS"}),
    "SALES_DATA": frozenset(
        {"TENANT_CSV_UPLOADER", "TENANT_DOC_UPLOADER", "SALES_DATA_APPROVER"}
    )
    | SALES_DATA_TENANT_PLATFORM_ROLES
    | SALES_DATA_APPROVER_PLATFORM_ROLES,
    "CONSTRUCTION_HOT": _WP_HOT_ROOF_AUDIENCE,
    "CONSTRUCTION_COLD": _WP_COLD_AUDIENCE,
    "CONSTRUCTION_ROOF_ACCESS": _WP_HOT_ROOF_AUDIENCE,
    "MAINTENANCE_HOT": _WP_MAINTENANCE_AUDIENCE,
    "MAINTENANCE_COLD": _WP_MAINTENANCE_AUDIENCE,
    "MAINTENANCE_ROOF_ACCESS": _WP_HOT_ROOF_AUDIENCE,
    "OPERATIONS": frozenset({"MALL_MANAGER"}),
    # L1 reviewer, L2 approver, and the tenant who acknowledges the report.
    "OPEN_CEILING_INSPECTION": _CEILING_AUDIENCE,
    "CLOSE_CEILING_INSPECTION": _CEILING_AUDIENCE,
}

SUB_CATEGORY_LABELS: dict[str, str] = {
    "HANDOVER": "Handover",
    "FIT_OUT_CONFIRM_FOS": "Fit-out Start Confirmation",
    "FIT_OUT_CONFIRM_END": "Fit-out End Confirmation",
    "FIT_OUT_CONFIRM_TRADING": "Trading Confirmation",
    "SALES_DATA": "Sales Data",
    "CONSTRUCTION_HOT": "Construction – Hot Work",
    "CONSTRUCTION_COLD": "Construction – Cold Work",
    "CONSTRUCTION_ROOF_ACCESS": "Construction – Roof Access",
    "MAINTENANCE_HOT": "Maintenance – Hot Work",
    "MAINTENANCE_COLD": "Maintenance – Cold Work",
    "MAINTENANCE_ROOF_ACCESS": "Maintenance – Roof Access",
    "OPERATIONS": "Operations",
    "OPEN_CEILING_INSPECTION": "Open Ceiling Inspection",
    "CLOSE_CEILING_INSPECTION": "Close Ceiling Inspection",
}


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class NotificationListFilters:
    """NL-derived list filters.  ``sub_categories=None`` means "all eligible"."""

    sub_categories: tuple[str, ...] | None = None
    unread: bool | None = None


@dataclass(slots=True)
class NotificationItem:
    notification_id: str
    sr_id: str | None
    sub_category: str
    message: str
    message_ar: str | None = None
    created_at: str | None = None
    read_at: str | None = None
    action_required: bool = True
    lease_id: str | None = None
    unique_property_id: str | None = None
    notification_type: str | None = None
    template_key: str | None = None


@dataclass(slots=True)
class NotificationGroup:
    sub_category: str
    total_count: int
    items: list[NotificationItem] = field(default_factory=list)
    page: int = 1
    #: Page size used for the fetch — lets the UI render "11–20 of 360"
    #: rather than restating the page length on every page.
    limit: int = 10

    @property
    def has_more(self) -> bool:
        """True when later pages exist (platform pages are fixed-size)."""
        return self.total_count > self.page * max(len(self.items), 1) and bool(self.items)


@dataclass(slots=True)
class NotificationInboxResult:
    success: bool
    groups: list[NotificationGroup] = field(default_factory=list)
    partial_errors: dict[str, str] = field(default_factory=dict)
    error: str | None = None

    @property
    def total(self) -> int:
        return sum(g.total_count for g in self.groups)


# ---------------------------------------------------------------------------
# Abstract interface
# ---------------------------------------------------------------------------


class AbstractNotificationInboxService(ABC):
    """Contract for the notification inbox adapters."""

    @abstractmethod
    async def fetch_inbox(
        self,
        *,
        user_id: str,
        roles: frozenset[str] = frozenset(),
        filters: NotificationListFilters | None = None,
        limit: int = 10,
        page: int = 1,
    ) -> NotificationInboxResult:
        """Fetch action-required notifications grouped by sub-category."""


def _select_sub_categories(
    roles: frozenset[str],
    filters: NotificationListFilters | None,
    exclude: frozenset[str] = frozenset(),
) -> tuple[str, ...]:
    """Pick which endpoints to call: requested ∩ audience-eligible.

    Empty/unknown role sets and ADMIN fall through to "call everything" —
    the platform's server-side audience intersection keeps that safe.
    """
    requested = tuple(
        sub
        for sub in ((filters.sub_categories if filters else None) or NOTIFICATION_SUB_CATEGORIES)
        if sub in _SUB_CATEGORY_PATHS and sub not in exclude
    )
    normalized = frozenset(r.upper() for r in roles)
    known = normalized & frozenset().union(*_SUB_CATEGORY_AUDIENCE.values())
    if not known or "ADMIN" in normalized:
        return requested
    return tuple(
        sub for sub in requested if _SUB_CATEGORY_AUDIENCE[sub] & normalized
    ) or requested


def _parse_text_content(raw: Any) -> dict[str, Any]:
    """``text_content`` arrives as a JSON object or a JSON-encoded string."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except (ValueError, TypeError):
            return {}
    return {}


def _parse_item(entry: dict[str, Any], sub_category: str) -> NotificationItem:
    text_content = _parse_text_content(entry.get("text_content"))
    sr_raw = text_content.get("id")
    sr_id = str(sr_raw).strip() if sr_raw not in (None, "") else None
    lease = entry.get("lease_id")
    prop = entry.get("unique_property_id")
    # Real SR notifications carry the human text in ``en`` / ``ar``;
    # ``message`` only exists on some legacy payload shapes.
    message_en = str(text_content.get("en") or text_content.get("message") or "").strip()
    message_ar = str(text_content.get("ar") or "").strip() or None
    template_key = str(text_content.get("key") or "").strip() or None
    ntype = (
        str(entry.get("notification_type") or text_content.get("type") or "").strip()
        or None
    )
    return NotificationItem(
        notification_id=str(entry.get("id") or entry.get("notification_id") or ""),
        sr_id=sr_id,
        sub_category=sub_category,
        message=message_en,
        message_ar=message_ar,
        created_at=entry.get("created_at"),
        read_at=entry.get("read_at"),
        action_required=bool(entry.get("action_required", True)),
        lease_id=str(lease) if lease not in (None, 0, "") else None,
        unique_property_id=str(prop) if prop not in (None, 0, "") else None,
        notification_type=ntype,
        template_key=template_key,
    )


def _unwrap_list(body: Any) -> tuple[list[dict[str, Any]], int | None]:
    """Return ``(entries, total_count)`` from the platform envelope.

    Tolerates ``{"data": {"list": [...], "total_count": N}}``, ``{"data": [...]}``,
    ``{"list": [...]}`` and a bare list (precedent: lease ``_parse_matches``).
    """
    if isinstance(body, list):
        return [e for e in body if isinstance(e, dict)], None
    if not isinstance(body, dict):
        return [], None
    data = body.get("data", body)
    if isinstance(data, list):
        return [e for e in data if isinstance(e, dict)], None
    if isinstance(data, dict):
        entries = data.get("list")
        total = data.get("total_count")
        if isinstance(entries, list):
            return (
                [e for e in entries if isinstance(e, dict)],
                int(total) if isinstance(total, (int, float, str)) and str(total).isdigit() else None,
            )
    return [], None


# ---------------------------------------------------------------------------
# HTTP adapter
# ---------------------------------------------------------------------------


class HttpNotificationInboxService(AbstractNotificationInboxService):
    """Production adapter — fans out over the sub-category agent endpoints."""

    def __init__(
        self,
        base_url: str | None = None,
        internal_api_token: str | None = None,
        timeout: float = 10.0,
    ) -> None:
        self._base_url = (base_url or settings.platform_base_url or "").rstrip("/")
        self._token = internal_api_token or settings.platform_internal_api_token or ""
        self._timeout = timeout

    async def fetch_inbox(
        self,
        *,
        user_id: str,
        roles: frozenset[str] = frozenset(),
        filters: NotificationListFilters | None = None,
        limit: int = 10,
        page: int = 1,
    ) -> NotificationInboxResult:
        selected = _select_sub_categories(roles, filters, exclude=MOCK_ONLY_SUB_CATEGORIES)
        if not selected:
            return NotificationInboxResult(success=True, groups=[])

        outcomes = await asyncio.gather(
            *(
                self._fetch_one(
                    sub, user_id=user_id, filters=filters, limit=limit, page=page
                )
                for sub in selected
            ),
            return_exceptions=True,
        )

        groups: list[NotificationGroup] = []
        partial_errors: dict[str, str] = {}
        any_success = False
        for sub, outcome in zip(selected, outcomes):
            if isinstance(outcome, BaseException):
                partial_errors[sub] = f"{type(outcome).__name__}: {outcome}"
                continue
            group, error = outcome
            if error:
                partial_errors[sub] = error
                continue
            any_success = True
            if group.items or group.total_count:
                groups.append(group)

        if not any_success:
            summary = "; ".join(f"{k}: {v}" for k, v in partial_errors.items())
            log.warning("notification_inbox.all_endpoints_failed", errors=summary)
            return NotificationInboxResult(
                success=False,
                partial_errors=partial_errors,
                error=summary or "notification inbox unavailable",
            )
        if partial_errors:
            log.warning("notification_inbox.partial_failure", errors=partial_errors)
        return NotificationInboxResult(
            success=True, groups=groups, partial_errors=partial_errors
        )

    async def _fetch_one(
        self,
        sub_category: str,
        *,
        user_id: str,
        filters: NotificationListFilters | None,
        limit: int,
        page: int = 1,
    ) -> tuple[NotificationGroup, str | None]:
        endpoint = f"{self._base_url}{_SUB_CATEGORY_PATHS[sub_category]}"
        params: dict[str, Any] = {"user_id": user_id, "page": page, "limit": limit}
        if filters and filters.unread is not None:
            params["unread"] = "true" if filters.unread else "false"
        headers = {"x-internal-api-token": self._token}

        wall_start = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.get(endpoint, params=params, headers=headers)
        except httpx.RequestError as exc:
            return NotificationGroup(sub_category, 0), f"{type(exc).__name__}: {exc}"
        latency_ms = int((time.monotonic() - wall_start) * 1000)

        try:
            body: Any = response.json()
        except Exception:
            body = {"_raw": response.text[:_MAX_RESPONSE_BYTES]}

        if response.status_code != 200:
            log.warning(
                "notification_inbox.non_200",
                sub_category=sub_category,
                status_code=response.status_code,
                latency_ms=latency_ms,
            )
            # Missing agent route (local platform / not yet shipped): treat as
            # empty rather than failing the whole inbox. Tenants only call
            # SALES_DATA — a 404 there previously became a hard 502.
            if response.status_code == 404:
                return NotificationGroup(sub_category, 0, page=page, limit=limit), None
            return NotificationGroup(sub_category, 0), f"HTTP {response.status_code}"

        entries, total = _unwrap_list(body)
        items = [_parse_item(e, sub_category) for e in entries]
        log.info(
            "notification_inbox.fetched",
            sub_category=sub_category,
            count=len(items),
            total_count=total,
            latency_ms=latency_ms,
        )
        return (
            NotificationGroup(
                sub_category,
                total if total is not None else len(items),
                items,
                page=page,
                limit=limit,
            ),
            None,
        )


# ---------------------------------------------------------------------------
# Mock adapter (local dev / eval)
# ---------------------------------------------------------------------------


_MOCK_FIXTURES: dict[str, list[dict[str, Any]]] = {
    "HANDOVER": [
        {
            "id": "ntf-hand-001",
            "text_content": {
                "message": "Handover SR 11711 requires your review.",
                "type": "SERVICE_REQUEST",
                "id": "11711",
            },
            "created_at": "2026-08-20T09:00:00Z",
            "read_at": None,
            "action_required": True,
            "notification_type": "SERVICE_REQUEST",
            "lease_id": 501,
            "unique_property_id": 3691,
        },
        {
            "id": "ntf-hand-002",
            "text_content": {
                "message": "Handover SR 11712 was updated by the tenant.",
                "type": "SERVICE_REQUEST",
                "id": "11712",
            },
            "created_at": "2026-08-19T15:30:00Z",
            "read_at": "2026-08-19T16:00:00Z",
            "action_required": True,
            "notification_type": "SERVICE_REQUEST",
            "lease_id": 502,
            "unique_property_id": 3691,
        },
    ],
    "FIT_OUT_CONFIRM_FOS": [
        {
            "id": "ntf-fos-001",
            "text_content": {
                "message": "Fit-out start confirmation needed for SR 11730.",
                "type": "SERVICE_REQUEST",
                "id": "11730",
            },
            "created_at": "2026-08-22T10:00:00Z",
            "read_at": None,
            "action_required": True,
            "notification_type": "SERVICE_REQUEST",
        },
    ],
    "FIT_OUT_CONFIRM_END": [
        {
            "id": "ntf-end-001",
            "text_content": {
                "message": "Fit-out end confirmation needed for SR 11740.",
                "type": "SERVICE_REQUEST",
                "id": "11740",
            },
            "created_at": "2026-08-23T11:00:00Z",
            "read_at": None,
            "action_required": True,
            "notification_type": "SERVICE_REQUEST",
        },
    ],
    "FIT_OUT_CONFIRM_TRADING": [
        {
            "id": "ntf-trd-001",
            "text_content": {
                "message": "Trading confirmation needed for SR 11750.",
                "type": "SERVICE_REQUEST",
                "id": "11750",
            },
            "created_at": "2026-08-24T12:00:00Z",
            "read_at": None,
            "action_required": True,
            "notification_type": "SERVICE_REQUEST",
        },
    ],
    "SALES_DATA": [
        {
            "id": "ntf-sd-001",
            "text_content": {
                "en": "Please submit sales data as of month August for Brand@Mall.",
                "ar": "",
                "type": "SALES_DATA",
                "key": "SALES_DATA_PENDING",
                "id": "LC-1001",
                "category": "PENDING",
            },
            "created_at": "2026-08-25T08:00:00Z",
            "read_at": None,
            "action_required": True,
            "notification_type": "SALES_DATA",
            "lease_id": 1,
        },
    ],
    "CONSTRUCTION_HOT": [
        {
            "id": "ntf-wp-hot-001",
            "text_content": {
                "message": "Work Permit (Construction Hot) SR 12801 requires your review.",
                "type": "SERVICE_REQUEST",
                "id": "12801",
            },
            "created_at": "2026-09-01T09:00:00Z",
            "read_at": None,
            "action_required": True,
            "notification_type": "SERVICE_REQUEST",
        },
    ],
    "CONSTRUCTION_COLD": [
        {
            "id": "ntf-wp-cold-001",
            "text_content": {
                "message": "Work Permit (Construction Cold) SR 12802 requires your review.",
                "type": "SERVICE_REQUEST",
                "id": "12802",
            },
            "created_at": "2026-09-01T09:05:00Z",
            "read_at": None,
            "action_required": True,
            "notification_type": "SERVICE_REQUEST",
        },
    ],
    "CONSTRUCTION_ROOF_ACCESS": [
        {
            "id": "ntf-wp-roof-001",
            "text_content": {
                "message": "Work Permit (Roof Access) SR 12803 requires your review.",
                "type": "SERVICE_REQUEST",
                "id": "12803",
            },
            "created_at": "2026-09-01T09:10:00Z",
            "read_at": None,
            "action_required": True,
            "notification_type": "SERVICE_REQUEST",
        },
    ],
    "MAINTENANCE_HOT": [
        {
            "id": "ntf-wp-mh-001",
            "text_content": {
                "message": "Work Permit (Maintenance Hot) SR 12804 requires your review.",
                "type": "SERVICE_REQUEST",
                "id": "12804",
            },
            "created_at": "2026-09-01T09:15:00Z",
            "read_at": None,
            "action_required": True,
            "notification_type": "SERVICE_REQUEST",
        },
    ],
    "MAINTENANCE_COLD": [
        {
            "id": "ntf-wp-mc-001",
            "text_content": {
                "message": "Work Permit (Maintenance Cold) SR 12805 requires your review.",
                "type": "SERVICE_REQUEST",
                "id": "12805",
            },
            "created_at": "2026-09-01T09:20:00Z",
            "read_at": None,
            "action_required": True,
            "notification_type": "SERVICE_REQUEST",
        },
    ],
    "MAINTENANCE_ROOF_ACCESS": [
        {
            "id": "ntf-wp-mr-001",
            "text_content": {
                "message": "Work Permit (Maintenance Roof) SR 12806 requires your review.",
                "type": "SERVICE_REQUEST",
                "id": "12806",
            },
            "created_at": "2026-09-01T09:25:00Z",
            "read_at": None,
            "action_required": True,
            "notification_type": "SERVICE_REQUEST",
        },
    ],
    "OPERATIONS": [
        {
            "id": "ntf-wp-ops-001",
            "text_content": {
                "message": "Work Permit (Operations) SR 12807 requires your review.",
                "type": "SERVICE_REQUEST",
                "id": "12807",
            },
            "created_at": "2026-09-01T09:30:00Z",
            "read_at": None,
            "action_required": True,
            "notification_type": "SERVICE_REQUEST",
        },
    ],
}


# Rows recorded at runtime by the mock SR service as a ceiling inspection moves
# between parties (create → DD, L1 → FM, L2 → tenant). In memory, like the
# mock SR state — a restart clears both. Each row carries the roles it is for.
_MOCK_RUNTIME_ROWS: list[dict[str, Any]] = []


def record_mock_notification(
    *,
    sub_category: str,
    sr_id: str,
    audience_roles: frozenset[str],
    message: str,
    key: str,
) -> None:
    """Record an action-required row for the parties who act next on *sr_id*.

    Earlier rows for the same SR are dropped first — once the SR moves on, the
    previous party's action is discharged.
    """
    clear_mock_notifications(sr_id)
    _MOCK_RUNTIME_ROWS.append(
        {
            "id": f"ntf-{uuid.uuid4().hex[:12]}",
            "sub_category": sub_category,
            "audience": frozenset(r.upper() for r in audience_roles),
            "text_content": {
                "en": message,
                "type": "SERVICE_REQUEST",
                "id": sr_id,
                "key": key,
            },
            "created_at": datetime.now(timezone.utc).isoformat(),
            "read_at": None,
            "action_required": True,
            "notification_type": "SERVICE_REQUEST",
        }
    )


def clear_mock_notifications(sr_id: str) -> None:
    """Drop every runtime row about *sr_id* (the SR moved on or closed)."""
    _MOCK_RUNTIME_ROWS[:] = [
        row for row in _MOCK_RUNTIME_ROWS if row["text_content"]["id"] != sr_id
    ]


def reset_mock_notifications() -> None:
    _MOCK_RUNTIME_ROWS.clear()


def _runtime_rows_for(sub_category: str, roles: frozenset[str]) -> list[dict[str, Any]]:
    normalized = frozenset(r.upper() for r in roles)
    return [
        row
        for row in reversed(_MOCK_RUNTIME_ROWS)  # newest first
        if row["sub_category"] == sub_category
        and ("ADMIN" in normalized or row["audience"] & normalized)
    ]


class MockNotificationInboxService(AbstractNotificationInboxService):
    """Deterministic in-memory inbox for local dev and tests."""

    async def fetch_inbox(
        self,
        *,
        user_id: str,
        roles: frozenset[str] = frozenset(),
        filters: NotificationListFilters | None = None,
        limit: int = 10,
        page: int = 1,
    ) -> NotificationInboxResult:
        selected = _select_sub_categories(roles, filters)
        groups: list[NotificationGroup] = []
        for sub in selected:
            entries = _runtime_rows_for(sub, roles) + _MOCK_FIXTURES.get(sub, [])
            items = [_parse_item(e, sub) for e in entries]
            # Platform parity: read rows STAY listed — every agent row is
            # action-required and reading one does not discharge the action
            # (an earlier read-hiding cut made "Needs your action" drop rows
            # whose work was still owed). unread only acts as an explicit
            # filter, mirroring the platform's IsUnRead param.
            if filters and filters.unread is True:
                items = [i for i in items if not i.read_at]
            elif filters and filters.unread is False:
                items = [i for i in items if i.read_at]
            total = len(items)
            items = items[(page - 1) * limit : page * limit]
            if items:
                groups.append(
                    NotificationGroup(sub, total, items, page=page, limit=limit)
                )
        return NotificationInboxResult(success=True, groups=groups)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def get_notification_inbox_service() -> AbstractNotificationInboxService:
    """Return the notification inbox adapter for the current configuration.

    Mock is used when the platform base URL or internal API token is missing,
    or when ``NOTIFICATIONS_USE_MOCK=true`` forces it.
    """
    if (
        settings.platform_base_url
        and settings.platform_internal_api_token
        and not settings.notifications_use_mock
    ):
        return HttpNotificationInboxService()
    return MockNotificationInboxService()
