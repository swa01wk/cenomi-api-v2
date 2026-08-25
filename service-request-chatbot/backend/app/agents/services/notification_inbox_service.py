"""Notification inbox — fetch action-required notifications from the platform.

Platform contract (cenomi-api-2, TC-3520, ``docs/NOTIFICATION_AGENT_TOOLS_SPEC.md``):
five sub-category-scoped GET endpoints under ``/notifications/agent/...``,
authenticated with the ``x-internal-api-token`` header ONLY (no user Bearer, no
S2S login).  The platform:

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
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import httpx
import structlog

from app.core.config import settings

log = structlog.get_logger(__name__)

_MAX_RESPONSE_BYTES = 4096

# Fixed display order — groups render in this sequence.
NOTIFICATION_SUB_CATEGORIES: tuple[str, ...] = (
    "HANDOVER",
    "FIT_OUT_CONFIRM_HO",
    "FIT_OUT_CONFIRM_FOS",
    "FIT_OUT_CONFIRM_END",
    "FIT_OUT_CONFIRM_TRADING",
)

_SUB_CATEGORY_PATHS: dict[str, str] = {
    "HANDOVER": "/notifications/agent/handover",
    "FIT_OUT_CONFIRM_HO": "/notifications/agent/fit-out-confirm-ho",
    "FIT_OUT_CONFIRM_FOS": "/notifications/agent/fit-out-confirm-fos",
    "FIT_OUT_CONFIRM_END": "/notifications/agent/fit-out-confirm-end",
    "FIT_OUT_CONFIRM_TRADING": "/notifications/agent/fit-out-confirm-trading",
}

# Tool audiences per the platform spec.  Used only to SKIP endpoints the user
# cannot have rows on (latency optimisation) — the platform re-intersects
# server-side, so calling everything is always safe.
_SUB_CATEGORY_AUDIENCE: dict[str, frozenset[str]] = {
    "HANDOVER": frozenset({"MALL_MANAGER", "FM_MANAGER", "DD_ENGINEER"}),
    "FIT_OUT_CONFIRM_HO": frozenset({"DD_ENGINEER"}),
    "FIT_OUT_CONFIRM_FOS": frozenset({"DD_ENGINEER"}),
    "FIT_OUT_CONFIRM_END": frozenset({"DD_ENGINEER"}),
    "FIT_OUT_CONFIRM_TRADING": frozenset({"DD_ENGINEER"}),
}

SUB_CATEGORY_LABELS: dict[str, str] = {
    "HANDOVER": "Handover",
    "FIT_OUT_CONFIRM_HO": "Handover Confirmation",
    "FIT_OUT_CONFIRM_FOS": "Fit-out Start Confirmation",
    "FIT_OUT_CONFIRM_END": "Fit-out End Confirmation",
    "FIT_OUT_CONFIRM_TRADING": "Trading Confirmation",
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
    created_at: str | None = None
    read_at: str | None = None
    action_required: bool = True
    lease_id: str | None = None
    unique_property_id: str | None = None
    notification_type: str | None = None


@dataclass(slots=True)
class NotificationGroup:
    sub_category: str
    total_count: int
    items: list[NotificationItem] = field(default_factory=list)


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
    ) -> NotificationInboxResult:
        """Fetch action-required notifications grouped by sub-category."""


def _select_sub_categories(
    roles: frozenset[str],
    filters: NotificationListFilters | None,
) -> tuple[str, ...]:
    """Pick which endpoints to call: requested ∩ audience-eligible.

    Empty/unknown role sets and ADMIN fall through to "call everything" —
    the platform's server-side audience intersection keeps that safe.
    """
    requested = tuple(
        sub
        for sub in ((filters.sub_categories if filters else None) or NOTIFICATION_SUB_CATEGORIES)
        if sub in _SUB_CATEGORY_PATHS
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
    return NotificationItem(
        notification_id=str(entry.get("id") or entry.get("notification_id") or ""),
        sr_id=sr_id,
        sub_category=sub_category,
        message=str(text_content.get("message") or "").strip(),
        created_at=entry.get("created_at"),
        read_at=entry.get("read_at"),
        action_required=bool(entry.get("action_required", True)),
        lease_id=str(lease) if lease not in (None, 0, "") else None,
        unique_property_id=str(prop) if prop not in (None, 0, "") else None,
        notification_type=entry.get("notification_type"),
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
    ) -> NotificationInboxResult:
        selected = _select_sub_categories(roles, filters)
        if not selected:
            return NotificationInboxResult(success=True, groups=[])

        outcomes = await asyncio.gather(
            *(self._fetch_one(sub, user_id=user_id, filters=filters, limit=limit) for sub in selected),
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
    ) -> tuple[NotificationGroup, str | None]:
        endpoint = f"{self._base_url}{_SUB_CATEGORY_PATHS[sub_category]}"
        params: dict[str, Any] = {"user_id": user_id, "page": 1, "limit": limit}
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
        return NotificationGroup(sub_category, total if total is not None else len(items), items), None


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
    "FIT_OUT_CONFIRM_HO": [
        {
            "id": "ntf-ho-001",
            "text_content": {
                "message": "Handover confirmation pending for SR 11720.",
                "type": "SERVICE_REQUEST",
                "id": "11720",
            },
            "created_at": "2026-08-21T08:00:00Z",
            "read_at": None,
            "action_required": True,
            "notification_type": "SERVICE_REQUEST",
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
}


class MockNotificationInboxService(AbstractNotificationInboxService):
    """Deterministic in-memory inbox for local dev and tests."""

    async def fetch_inbox(
        self,
        *,
        user_id: str,
        roles: frozenset[str] = frozenset(),
        filters: NotificationListFilters | None = None,
        limit: int = 10,
    ) -> NotificationInboxResult:
        selected = _select_sub_categories(roles, filters)
        groups: list[NotificationGroup] = []
        for sub in selected:
            entries = _MOCK_FIXTURES.get(sub, [])
            items = [_parse_item(e, sub) for e in entries]
            if filters and filters.unread is True:
                items = [i for i in items if not i.read_at]
            elif filters and filters.unread is False:
                items = [i for i in items if i.read_at]
            items = items[:limit]
            if items:
                groups.append(NotificationGroup(sub, len(items), items))
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
