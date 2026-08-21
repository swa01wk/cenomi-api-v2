"""Work Permit Service Request — workflow configuration schema.

Create is ``CREATE_WORK_PERMIT``. Review hops are WP-prefixed so they never
collide with Handover ``FM_REVIEW`` / ``RDD_REVIEW`` in shared graph routing.

Platform mapping (V1 workflow.flow, workflow_id 5–8):
    CONSTRUCTION_HOT / CONSTRUCTION_ROOF_ACCESS / MAINTENANCE_ROOF_ACCESS
        L1 DD_ENGINEER → L2 FM_MANAGER → L3 MALL_MANAGER
    CONSTRUCTION_COLD
        L1 DD_ENGINEER → L2 MALL_MANAGER | OPERATION_EXECUTIVES | OPERATIONS
    MAINTENANCE_HOT / MAINTENANCE_COLD
        L1 FM_MANAGER → L2 MALL_MANAGER
    OPERATIONS
        L1 MALL_MANAGER only (may auto-approve if MM created it)
"""

from __future__ import annotations

from dataclasses import dataclass, field as dc_field
from typing import Any, Final, Literal

# ── Constants ─────────────────────────────────────────────────────────────────

SERVICE_CATEGORY: Final[str] = "WORK_PERMIT"

# ── Literal types ─────────────────────────────────────────────────────────────

WorkPermitType = Literal[
    "CONSTRUCTION_HOT",
    "CONSTRUCTION_COLD",
    "CONSTRUCTION_ROOF_ACCESS",
    "MAINTENANCE_HOT",
    "MAINTENANCE_COLD",
    "MAINTENANCE_ROOF_ACCESS",
    "OPERATIONS",
]

VALID_WORK_PERMIT_TYPES: frozenset[str] = frozenset(
    {
        "CONSTRUCTION_HOT",
        "CONSTRUCTION_COLD",
        "CONSTRUCTION_ROOF_ACCESS",
        "MAINTENANCE_HOT",
        "MAINTENANCE_COLD",
        "MAINTENANCE_ROOF_ACCESS",
        "OPERATIONS",
    }
)

WORK_PERMIT_TYPE_LABELS: dict[str, str] = {
    "CONSTRUCTION_HOT": "Construction – Hot Work",
    "CONSTRUCTION_COLD": "Construction – Cold Work",
    "CONSTRUCTION_ROOF_ACCESS": "Construction – Roof Access",
    "MAINTENANCE_HOT": "Maintenance – Hot Work",
    "MAINTENANCE_COLD": "Maintenance – Cold Work",
    "MAINTENANCE_ROOF_ACCESS": "Maintenance – Roof Access",
    "OPERATIONS": "Operations",
}

# Chatbot review stages (not platform field names).
WP_DD_REVIEW: Final[str] = "WP_DD_REVIEW"
WP_FM_REVIEW: Final[str] = "WP_FM_REVIEW"
WP_MM_REVIEW: Final[str] = "WP_MM_REVIEW"
WP_MM_OPS_REVIEW: Final[str] = "WP_MM_OPS_REVIEW"

WP_REVIEW_STAGES: frozenset[str] = frozenset(
    {WP_DD_REVIEW, WP_FM_REVIEW, WP_MM_REVIEW, WP_MM_OPS_REVIEW}
)

WP_APPROVED: Final[str] = "WP_APPROVED"
WP_REJECTED: Final[str] = "WP_REJECTED"
WP_CREATED: Final[str] = "WP_CREATED"

WP_TERMINAL_STAGES: frozenset[str] = frozenset(
    {WP_CREATED, WP_APPROVED, WP_REJECTED}
)

STAGE_LABELS: dict[str, str] = {
    "CREATE_WORK_PERMIT": "Create Work Permit",
    WP_DD_REVIEW: "DD Review",
    WP_FM_REVIEW: "FM Review",
    WP_MM_REVIEW: "Mall Manager Review",
    WP_MM_OPS_REVIEW: "Mall Manager / Operations Review",
    WP_CREATED: "Submitted",
    WP_APPROVED: "Approved",
    WP_REJECTED: "Rejected",
}

# Seed role_id → name (migration 1707406122 + handover OPERATIONS 81).
WP_ROLE_ID_TO_NAME: dict[str, str] = {
    "11": "MALL_MANAGER",
    "12": "DD_ENGINEER",
    "20": "FM_MANAGER",
    "81": "OPERATIONS",
}

_DD_ROLES: frozenset[str] = frozenset({"DD_ENGINEER", "12"})
_FM_ROLES: frozenset[str] = frozenset({"FM_MANAGER", "20"})
_COLD_L2_ROLES: frozenset[str] = frozenset(
    {"MALL_MANAGER", "OPERATION_EXECUTIVES", "OPERATIONS", "11", "81"}
)

_PLATFORM_APPROVED: frozenset[str] = frozenset(
    {"APPROVED", "COMPLETED", "DONE", "ACTIVE", "EXPIRED", "CLOSED", "EXTENDED"}
)
_PLATFORM_REJECTED: frozenset[str] = frozenset({"REJECTED"})

# Who may PATCH APPROVED at each chatbot review stage.
WP_REVIEW_ROLES: dict[str, frozenset[str]] = {
    WP_DD_REVIEW: frozenset({"DD_ENGINEER", "ADMIN"}),
    WP_FM_REVIEW: frozenset({"FM_MANAGER", "ADMIN"}),
    WP_MM_REVIEW: frozenset({"MALL_MANAGER", "ADMIN"}),
    WP_MM_OPS_REVIEW: frozenset(
        {"MALL_MANAGER", "OPERATIONS", "OPERATION_EXECUTIVES", "ADMIN"}
    ),
}


# ── Stage definition dataclass (mirrors handover_schema pattern) ──────────────


@dataclass(frozen=True)
class WorkPermitStageDefinition:
    """Immutable description of a work permit workflow stage."""

    stage: str
    role: str
    required_fields: tuple[str, ...]
    required_documents: tuple[str, ...] = dc_field(default_factory=tuple)


# ── Stage instances ───────────────────────────────────────────────────────────

_CREATE_REQUIRED: tuple[str, ...] = (
    "lease_code",
    "lease_id",
    "property_id",
    "unit_codes",
    "mall",
    "brand",
    "work_permit_type",
    "description",
    "start_date",
    "end_date",
    "contractor_name",
    "comments",
)

CREATE_WORK_PERMIT_STAGE = WorkPermitStageDefinition(
    stage="CREATE_WORK_PERMIT",
    role="MALL_MANAGER",
    required_fields=_CREATE_REQUIRED,
)

# Review hops: no extra DTO. Approver resubmits the GET payload.
WP_DD_REVIEW_STAGE = WorkPermitStageDefinition(
    stage=WP_DD_REVIEW, role="DD_ENGINEER", required_fields=()
)
WP_FM_REVIEW_STAGE = WorkPermitStageDefinition(
    stage=WP_FM_REVIEW, role="FM_MANAGER", required_fields=()
)
WP_MM_REVIEW_STAGE = WorkPermitStageDefinition(
    stage=WP_MM_REVIEW, role="MALL_MANAGER", required_fields=()
)
WP_MM_OPS_REVIEW_STAGE = WorkPermitStageDefinition(
    stage=WP_MM_OPS_REVIEW, role="MALL_MANAGER", required_fields=()
)

STAGE_REGISTRY: dict[str, WorkPermitStageDefinition] = {
    "CREATE_WORK_PERMIT": CREATE_WORK_PERMIT_STAGE,
    WP_DD_REVIEW: WP_DD_REVIEW_STAGE,
    WP_FM_REVIEW: WP_FM_REVIEW_STAGE,
    WP_MM_REVIEW: WP_MM_REVIEW_STAGE,
    WP_MM_OPS_REVIEW: WP_MM_OPS_REVIEW_STAGE,
}

# ── Field sets ────────────────────────────────────────────────────────────────

BACKEND_DERIVED_FIELDS: frozenset[str] = frozenset(
    {
        "lease_id",
        "property_id",
        "unit_codes",
        "mall",
        "brand",
        "tenant_profile_id",
        "city",
        "contracted_area",
    }
)

USER_SUPPLIED_FIELDS: frozenset[str] = frozenset(
    {
        "lease_code",
        "work_permit_type",
        "description",
        "start_date",
        "end_date",
        "contractor_name",
        "comments",
    }
)

EXTRACTABLE_FIELDS: frozenset[str] = frozenset(
    {
        "lease_code",
        "work_permit_type",
        "description",
        "start_date",
        "end_date",
        "contractor_name",
        "comments",
        "notes",
    }
)

BACKEND_ONLY_FIELDS: frozenset[str] = BACKEND_DERIVED_FIELDS

OPTIONAL_FIELDS: frozenset[str] = frozenset(
    {"contractor_name", "comments", "notes", "description"}
)

AUTO_GENERATED_FIELDS: frozenset[str] = frozenset()

# ── Helpers ───────────────────────────────────────────────────────────────────


def is_work_permit_category(category: str | None, sub_category: str | None) -> bool:
    """True when GET/list fields identify a Work Permit SR."""
    cat = (category or "").upper()
    sub = (sub_category or "").upper()
    if cat in {"WORK_PERMIT", "SR_WORK_PERMIT"}:
        return True
    return sub in VALID_WORK_PERMIT_TYPES


def normalize_wp_role(raw: Any) -> str:
    """Map platform assigned_role (name or seed id) to an uppercase role name."""
    if raw is None:
        return ""
    token = str(raw).strip().upper()
    return WP_ROLE_ID_TO_NAME.get(token, token)


def in_progress_roles(operations: list[dict[str, Any]] | None) -> set[str]:
    """Return normalized role names whose operation status is IN_PROGRESS."""
    roles: set[str] = set()
    for op in operations or []:
        if (op.get("status") or "").upper() != "IN_PROGRESS":
            continue
        name = normalize_wp_role(op.get("assigned_role") or op.get("role"))
        if name:
            roles.add(name)
    return roles


def map_wp_operations_to_stage(
    operations: list[dict[str, Any]] | None,
    *,
    sub_category: str | None = None,
    platform_status: str | None = None,
    current_stage: str | None = None,
) -> str | None:
    """Derive chatbot ``workflow_stage`` from V1 ``service_request_operations``.

    First IN_PROGRESS hop wins, in platform order: DD → FM → MM / Cold L2.
    """
    status = (platform_status or "").upper()
    if status in _PLATFORM_REJECTED:
        return WP_REJECTED
    if status in _PLATFORM_APPROVED:
        return WP_APPROVED

    roles = in_progress_roles(operations)
    if not roles:
        return current_stage

    if roles & {normalize_wp_role(r) for r in _DD_ROLES} or "DD_ENGINEER" in roles:
        return WP_DD_REVIEW
    if "FM_MANAGER" in roles:
        return WP_FM_REVIEW

    cold_l2 = roles & {"MALL_MANAGER", "OPERATION_EXECUTIVES", "OPERATIONS"}
    if cold_l2:
        sub = (sub_category or "").upper()
        if sub == "CONSTRUCTION_COLD" or len(cold_l2) > 1:
            return WP_MM_OPS_REVIEW
        return WP_MM_REVIEW

    return current_stage


def resolve_wp_sub_category(
    state_sub_category: str | None,
    collected: dict[str, Any] | None = None,
) -> str:
    """Prefer explicit subtype over the legacy ``WORK_PERMIT`` registry key."""
    for candidate in (
        state_sub_category,
        (collected or {}).get("work_permit_type"),
        (collected or {}).get("sub_category"),
    ):
        token = str(candidate or "").upper()
        if token in VALID_WORK_PERMIT_TYPES:
            return token
    return "WORK_PERMIT"


def iso_to_ddmmyyyy(value: Any) -> str:
    """Convert ``YYYY-MM-DD`` (or pass through ``DD/MM/YYYY``) for WP planned dates."""
    raw = str(value or "").strip()
    if not raw:
        return ""
    if "/" in raw[:10]:
        return raw[:10]
    date_part = raw[:10]
    parts = date_part.split("-")
    if len(parts) == 3 and len(parts[0]) == 4:
        return f"{parts[2]}/{parts[1]}/{parts[0]}"
    return raw


def ddmmyyyy_to_iso(value: Any) -> str:
    """Convert ``DD/MM/YYYY`` to ``YYYY-MM-DD`` when possible."""
    raw = str(value or "").strip()
    if not raw:
        return ""
    if "-" in raw[:10] and raw[4:5] == "-":
        return raw[:10]
    parts = raw[:10].split("/")
    if len(parts) == 3:
        day, month, year = parts
        return f"{year}-{month.zfill(2)}-{day.zfill(2)}"
    return raw


def get_required_fields(stage_name: str) -> tuple[str, ...]:
    """Return required fields for *stage_name*."""
    stage = STAGE_REGISTRY.get(stage_name)
    if not stage:
        raise KeyError(f"Unknown work permit stage '{stage_name}'")
    return stage.required_fields


def get_missing_fields(stage_name: str, collected: dict[str, Any]) -> list[str]:
    """Return required fields absent from *collected* for *stage_name*.

    Review stages have no required fields. Optional create fields treat only
    ``None`` as missing (empty string is an explicit skip).
    """

    def _is_missing(field_name: str, value: Any) -> bool:
        if field_name in OPTIONAL_FIELDS:
            return value is None
        return value is None or value == "" or value == []

    return [
        f
        for f in get_required_fields(stage_name)
        if _is_missing(f, collected.get(f))
    ]
