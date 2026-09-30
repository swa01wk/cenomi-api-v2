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

from app.agents.schemas.work_permit_form_fields import (
    FIELD_LABELS as WP_FORM_FIELD_LABELS,
    TENANT_FORM_BY_SUBTYPE,
    form_spec_for,
    is_tenant_field_missing,
)

# Re-export for confirmation / missing-field callers.
FIELD_LABELS = WP_FORM_FIELD_LABELS
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
# Sent back to the tenant (platform ACTION_REQUIRED): no review hop is open.
WP_ACTION_REQUIRED: Final[str] = "WP_ACTION_REQUIRED"

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
    WP_ACTION_REQUIRED: "Sent back to tenant",
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
_PLATFORM_ACTION_REQUIRED: frozenset[str] = frozenset({"ACTION_REQUIRED"})

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

# Base create requirements before subtype is known. Once work_permit_type is set,
# get_create_required_fields() unions the tenant form required set for that subtype.
# ``work_permit_type`` is first so create asks subtype before lease when empty
# (seeded Create-with-AI subtypes skip that turn).
_CREATE_REQUIRED_BASE: tuple[str, ...] = (
    "work_permit_type",
    "lease_code",
    "lease_id",
    "property_id",
    "unit_codes",
    "mall",
    "brand",
)

CREATE_WORK_PERMIT_STAGE = WorkPermitStageDefinition(
    stage="CREATE_WORK_PERMIT",
    role="MALL_MANAGER",
    required_fields=_CREATE_REQUIRED_BASE,
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
        "lease_brand_mall",
        "lease",
        "company_name",
        "country_code",
        "tenant_contact",
    }
)

_ALL_TENANT_USER_FIELDS: frozenset[str] = frozenset().union(
    *(frozenset(spec.field_types.keys()) for spec in TENANT_FORM_BY_SUBTYPE.values())
)

USER_SUPPLIED_FIELDS: frozenset[str] = frozenset(
    {"lease_code", "work_permit_type"}
) | _ALL_TENANT_USER_FIELDS

EXTRACTABLE_FIELDS: frozenset[str] = frozenset(
    {
        "lease_code",
        "work_permit_type",
        "department",
        "unit_type",
        "owner",
        "trade_name",
        "responsible_person",
        "phone",
        "type_work",
        "list_equipment",
        "work_info",
        "notes",
        "contractor_name",
        "work_type",
        "work_location",
        "demand_appendices",
        "special_conditions",
        "description",
        "title",
        "planneddate",
        "workers",
        "measureofsafety",
        "typeofwork",
        "effective_starting",
        "firstperiod",
        "secondperiod",
        "planned_second_period",
        "entry_gate",
        "permission_given_to",
    }
)

BACKEND_ONLY_FIELDS: frozenset[str] = BACKEND_DERIVED_FIELDS

OPTIONAL_FIELDS: frozenset[str] = frozenset().union(
    *(spec.optional_tenant_fields for spec in TENANT_FORM_BY_SUBTYPE.values())
) | frozenset({"comments", "description", "title", "notes"})

# No longer auto-filled ASAP — Handover-style optional ask / skip / short-flow.
AUTO_GENERATED_FIELDS: frozenset[str] = frozenset()

# Short-flow fills these instead of asking (same flag as Handover short flow).
WP_AI_FILLED_FIELDS: frozenset[str] = frozenset({"title", "description", "notes"})


def wp_short_flow_enabled() -> bool:
    """Reuse Handover short-flow switch for WP title/description autofill."""
    from app.core.config import get_settings

    return bool(get_settings().handover_short_flow_enabled)

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
    ACTION_REQUIRED (sent back to the tenant) wins over a stale review stage.
    """
    status = (platform_status or "").upper()
    if status in _PLATFORM_REJECTED:
        return WP_REJECTED
    if status in _PLATFORM_APPROVED:
        return WP_APPROVED
    if status in _PLATFORM_ACTION_REQUIRED:
        return WP_ACTION_REQUIRED

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


_WP_TITLE_MAX_LEN: Final[int] = 50  # Go WorkPermitOperations.Title max=50
_WP_DESCRIPTION_MAX_LEN: Final[int] = 200  # Go WorkPermitOperations.Description max=200
_WP_NOTES_MAX_LEN: Final[int] = 500  # Go WorkPermitOperations.Notes max=500

# Compact subtype labels for the 50-char title budget (brand must still fit).
_WP_TITLE_SUBTYPE_LABELS: Final[dict[str, str]] = {
    "CONSTRUCTION_HOT": "Construction Hot",
    "CONSTRUCTION_COLD": "Construction Cold",
    "CONSTRUCTION_ROOF_ACCESS": "Const Roof Access",
    "MAINTENANCE_HOT": "Maintenance Hot",
    "MAINTENANCE_COLD": "Maintenance Cold",
    "MAINTENANCE_ROOF_ACCESS": "Maint Roof Access",
    "OPERATIONS": "Operations",
}


def truncate_wp_title(title: str, *, max_len: int = _WP_TITLE_MAX_LEN) -> str:
    """Clamp title to the platform max (ellipsis when truncated)."""
    text = str(title or "").strip()
    if len(text) <= max_len:
        return text
    if max_len <= 1:
        return text[:max_len]
    return text[: max_len - 1].rstrip() + "…"


def truncate_wp_description(
    description: str, *, max_len: int = _WP_DESCRIPTION_MAX_LEN
) -> str:
    """Clamp description to the platform max (ellipsis when truncated)."""
    text = str(description or "").strip()
    if len(text) <= max_len:
        return text
    if max_len <= 1:
        return text[:max_len]
    return text[: max_len - 1].rstrip() + "…"


def truncate_wp_notes(notes: str, *, max_len: int = _WP_NOTES_MAX_LEN) -> str:
    """Clamp notes to the platform max (ellipsis when truncated)."""
    text = str(notes or "").strip()
    if len(text) <= max_len:
        return text
    if max_len <= 1:
        return text[:max_len]
    return text[: max_len - 1].rstrip() + "…"


def _clean_brand_token(brand: str) -> str:
    """Strip a leading ``Brand `` prefix from lease picker labels."""
    text = str(brand or "").strip()
    if text.lower().startswith("brand "):
        return text[6:].strip() or text
    return text


def wp_location_label(collected: dict[str, Any] | None) -> str:
    """Full lease / brand / mall string for descriptions."""
    data = collected or {}
    lease_brand_mall = str(data.get("lease_brand_mall") or "").strip()
    if lease_brand_mall:
        return lease_brand_mall
    brand = _clean_brand_token(str(data.get("brand") or ""))
    mall = str(data.get("mall") or "").strip()
    if brand and mall:
        return f"{brand} / {mall}"
    if brand or mall:
        return brand or mall
    return str(data.get("lease_code") or "").strip()


def wp_title_location_label(collected: dict[str, Any] | None) -> str:
    """Compact brand/mall (and lease) for the 50-char title budget.

    Prefers brand / mall over the long ``lease_brand_mall`` display string so
    the subtype label still fits. Lease code is appended by ``generate_wp_title``
    when there is room.
    """
    data = collected or {}
    brand = _clean_brand_token(str(data.get("brand") or ""))
    mall = str(data.get("mall") or "").strip()
    if brand and mall:
        return f"{brand} / {mall}"
    if brand:
        return brand
    if mall:
        return mall
    # Fall back to lease_brand_mall but strip a leading lease-code segment when
    # it duplicates lease_code (e.g. ``t0028922 - Brand Bershka - Mall``).
    lease_brand_mall = str(data.get("lease_brand_mall") or "").strip()
    lease_code = str(data.get("lease_code") or "").strip()
    if lease_brand_mall:
        cleaned = lease_brand_mall
        if lease_code and cleaned.lower().startswith(lease_code.lower()):
            rest = cleaned[len(lease_code) :].lstrip(" -/–—")
            # Drop a leading "Brand " token from picker strings.
            rest = _clean_brand_token(rest) if rest.lower().startswith("brand ") else rest
            if rest:
                return rest
        return cleaned
    return lease_code


def wp_subtype_label(collected: dict[str, Any] | None) -> str:
    """Human subtype label (e.g. ``Operations``, ``Construction – Hot Work``)."""
    subtype = resolve_wp_sub_category(None, collected)
    if subtype in WORK_PERMIT_TYPE_LABELS:
        return WORK_PERMIT_TYPE_LABELS[subtype]
    return subtype.replace("_", " ").title() if subtype else "Work Permit"


def wp_title_subtype_label(collected: dict[str, Any] | None) -> str:
    """Shorter subtype label for titles so brand still fits in 50 chars."""
    subtype = resolve_wp_sub_category(None, collected)
    if subtype in _WP_TITLE_SUBTYPE_LABELS:
        return _WP_TITLE_SUBTYPE_LABELS[subtype]
    return wp_subtype_label(collected)


def generate_wp_title(collected: dict[str, Any] | None) -> str:
    """Compact title: ``{short subtype} – {lease_code}`` within 50 chars."""
    data = collected or {}
    lease_code = str(data.get("lease_code") or "").strip()
    if not lease_code:
        return ""
    label = wp_title_subtype_label(data)
    return truncate_wp_title(f"{label} – {lease_code}")


def generate_wp_description(collected: dict[str, Any] | None) -> str:
    """Description: ``{subtype} work permit for {lease_code}.`` (max 200)."""
    data = collected or {}
    lease_code = str(data.get("lease_code") or "").strip()
    if not lease_code:
        return ""
    label = wp_subtype_label(collected)
    return truncate_wp_description(f"{label} work permit for {lease_code}.")


def generate_wp_notes(collected: dict[str, Any] | None) -> str:
    """Notes: ``{subtype} work permit for {lease_brand_mall or brand/mall}.`` (max 500)."""
    location = wp_location_label(collected)
    if not location:
        return ""
    label = wp_subtype_label(collected)
    return truncate_wp_notes(f"{label} work permit for {location}.")


def is_lease_code_only_wp_auto_text(
    value: Any,
    collected: dict[str, Any] | None,
    *,
    kind: str,
) -> bool:
    """Formerly refreshed lease-only auto text when brand/mall arrived.

    Title/description are lease-code-only by design now, so there is nothing
    to upgrade — always ``False`` (never clobber an existing value for this).
    """
    del value, collected, kind
    return False


def wp_text_should_autofill(
    value: Any,
    collected: dict[str, Any] | None,
    *,
    kind: str,
) -> bool:
    """True when short-flow should (re)write title/description.

    Includes ``None`` (never asked), empty skip, and lease-code-only upgrades.
    """
    if value is None:
        return True
    if isinstance(value, str) and value.strip() == "":
        return True
    return is_lease_code_only_wp_auto_text(value, collected, kind=kind)


def wp_text_should_skip_or_refresh(
    value: Any,
    collected: dict[str, Any] | None,
    *,
    kind: str,
) -> bool:
    """True for merge skip-fill: empty skip or lease-code-only upgrade.

    Deliberately excludes ``None`` so the optional ask turn still happens when
    short-flow is off (Handover-style).
    """
    if isinstance(value, str) and value.strip() == "":
        return True
    return is_lease_code_only_wp_auto_text(value, collected, kind=kind)


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


def get_create_required_fields(collected: dict[str, Any] | None = None) -> tuple[str, ...]:
    """Lease + type always; optional title/description after location; then form.

    After lease location is known, insert optional ``description`` / ``title``
    (Handover-style ask-or-skip) before subtype tenant-form fields. Empty string
    = skip → autofill. Short-flow omits them from ``get_missing_fields``.
    """
    collected = collected or {}
    subtype = resolve_wp_sub_category(None, collected)
    base = list(_CREATE_REQUIRED_BASE)
    seen: set[str] = set()
    ordered: list[str] = []
    for name in base:
        if name not in seen:
            seen.add(name)
            ordered.append(name)
    # Ask (or allow skip) once lease location + type are known — before form widgets.
    if wp_location_label(collected) and (
        collected.get("_wp_type_confirmed") or subtype in VALID_WORK_PERMIT_TYPES
    ):
        for name in ("description", "title"):
            if name not in seen:
                seen.add(name)
                ordered.append(name)
    spec = form_spec_for(subtype)
    if spec is not None:
        # Avoid duplicate lease_brand_mall vs lease_code — ask lease_code first;
        # lease_brand_mall is filled by lease lookup.
        for name in spec.required_tenant_fields:
            if name in {"lease_brand_mall"} or name in seen:
                continue
            seen.add(name)
            ordered.append(name)
            # Ask optional second period right after the required planneddate window.
            if (
                name == "planneddate"
                and subtype in {"CONSTRUCTION_COLD", "MAINTENANCE_COLD"}
                and "planned_second_period" not in seen
            ):
                seen.add("planned_second_period")
                ordered.append("planned_second_period")
        # Ops optional ask-or-skip (None = ask once; "" = explicit skip).
        if subtype == "OPERATIONS":
            for name in ("secondperiod", "entry_gate", "notes"):
                if name not in seen:
                    seen.add(name)
                    ordered.append(name)
        # Cold optional notes (second period already inserted after planneddate).
        elif subtype in {"CONSTRUCTION_COLD", "MAINTENANCE_COLD"}:
            if "notes" not in seen:
                seen.add("notes")
                ordered.append("notes")
    return tuple(ordered)


def get_required_fields(
    stage_name: str, collected: dict[str, Any] | None = None
) -> tuple[str, ...]:
    """Return required fields for *stage_name* (subtype-aware on create)."""
    if stage_name == "CREATE_SR":
        stage_name = "CREATE_WORK_PERMIT"
    if stage_name == "CREATE_WORK_PERMIT":
        return get_create_required_fields(collected)
    stage = STAGE_REGISTRY.get(stage_name)
    if not stage:
        raise KeyError(f"Unknown work permit stage '{stage_name}'")
    return stage.required_fields


def get_missing_fields(stage_name: str, collected: dict[str, Any]) -> list[str]:
    """Return required fields absent from *collected* for *stage_name*.

    Review stages have no required fields. Optional create fields treat only
    ``None`` as missing (empty string is an explicit skip). Nested form widgets
    (planneddate, workers, measureofsafety, …) use portal emptiness rules.

    Short-flow skips asking for ``WP_AI_FILLED_FIELDS`` (filled by autofill).
    """
    if stage_name == "CREATE_SR":
        stage_name = "CREATE_WORK_PERMIT"

    required = get_required_fields(stage_name, collected)
    subtype = resolve_wp_sub_category(None, collected)
    spec = form_spec_for(subtype)
    field_types = spec.field_types if spec else {}
    optional = OPTIONAL_FIELDS | (spec.optional_tenant_fields if spec else frozenset())
    unasked = WP_AI_FILLED_FIELDS if wp_short_flow_enabled() else frozenset()

    missing: list[str] = []
    for field_name in required:
        if field_name in unasked:
            continue
        value = collected.get(field_name)
        if is_tenant_field_missing(
            field_name,
            value,
            field_type=field_types.get(field_name),
            optional=field_name in optional,
        ):
            missing.append(field_name)
    return missing
